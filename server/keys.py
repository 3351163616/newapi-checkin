"""密钥管理域（new-api 的「令牌」/api/token/）。

四类账号（token/site/cookie/login）跑的都是 new-api，密钥接口同名，差异收敛进 KeyCtx；
跨账号取全量 key 的限流协调与出口轮换也在这里。

过渡期约定（拆分第一块，见 balance_server 对应节的注释）：
- 本模块**不做**模块级 `import balance_server` —— 由 balance_server 在自身定义序列中
  导入并重导出全部公开名，避免循环导入；
- 函数体内对账号加载器、请求通道、文件路径、可变缓存等一切跨实体引用，一律晚绑定
  `bs.<名字>` —— 测试对 bs 命名空间的 monkeypatch 因此原样生效（零测试改动）；
- 后续块迁移时逐步把这里的 bs.* 换成真正的模块内依赖， tests 的 patch 目标随之迁移。
"""

import asyncio
import json
import time

from fastapi import APIRouter

keys_router = APIRouter()

# 可变状态的真实住所；balance_server 重导出同一批对象，测试照旧 `bs._xxx.clear()`。
# 常量（KEYS_CACHE_FILE / AGENTROUTER_SESSION_FILE / TOKEN_LIST_PATH 等）仍留在
# balance_server —— 它们会被测试改写，函数体一律经 bs. 读取。
_key_value_cache: dict[str, str] = {}
_keys_list_cache: dict = {}
_agentrouter_key_sessions: dict[str, dict] = {}
_agentrouter_login_lock = asyncio.Lock()
_keys_reveal_until: dict[str, float] = {}
_keys_reveal_lock = asyncio.Lock()

# 取全量 key 的跨账号协调参数（限流是 20 次/20 分钟/出口 IP，全端点共享）
KEYS_REVEAL_CONCURRENCY = 4  # 列表接口可以猛并发，取全量是限流资源，小批推进
KEYS_REVEAL_MAX_SWITCHES = 6  # 最多换 6 个出口（每个出口 20 次/20 分钟，29 个账号最多用 2 个）
KEYS_REVEAL_LIMIT_WINDOW = 20 * 60


def load_keys_list_cache():
	"""启动时恢复密钥列表缓存（与其它数据文件一样，读写都容错）"""
	import balance_server as bs

	try:
		data = json.loads(bs.KEYS_CACHE_FILE.read_text(encoding='utf-8'))
		if isinstance(data, dict):
			bs._keys_list_cache = data
	except Exception:
		bs._keys_list_cache = {}


def save_keys_list_cache():
	import balance_server as bs

	try:
		now = time.time()
		for k in [k for k, v in bs._keys_list_cache.items() if now - v.get('ts', 0) > bs.KEYS_CACHE_MAX_AGE]:
			bs._keys_list_cache.pop(k, None)
		bs._atomic_write_json(bs.KEYS_CACHE_FILE, bs._keys_list_cache)
	except Exception as e:
		print(f'[KEYS] 列表缓存写盘失败: {e}')


def load_agentrouter_sessions():
	"""启动时恢复缓存的 agentrouter session，避免重启后的第一次查询打满登录限流"""
	import balance_server as bs

	if not bs.AGENTROUTER_SESSION_FILE.exists():
		return
	try:
		data = json.loads(bs.AGENTROUTER_SESSION_FILE.read_text(encoding='utf-8'))
	except Exception as e:
		print(f'[AGENTROUTER] 读取 session 缓存失败: {e}')
		return
	now = time.time()
	kept = {k: v for k, v in data.items() if isinstance(v, dict) and v.get('expires', 0) > now}
	bs._agentrouter_key_sessions.update(kept)
	if kept:
		print(f'[AGENTROUTER] 恢复了 {len(kept)} 个 session 缓存')


def save_agentrouter_sessions():
	import balance_server as bs

	try:
		bs._atomic_write_json(bs.AGENTROUTER_SESSION_FILE, bs._agentrouter_key_sessions, indent=2)
	except Exception as e:
		print(f'[AGENTROUTER] 保存 session 缓存失败: {e}')


class KeyCtx:
	"""一个账号的密钥操作上下文：request 闭包封装了该账号怎么发请求，上层不用关心类型"""

	def __init__(self, ref: str, name: str, provider: str, request, quota_per_unit: int = 500000, proxied_request=None):
		self.ref = ref
		self.name = name
		self.provider = provider
		self.request = request
		self.quota_per_unit = quota_per_unit or 500000
		# 走 mihomo 出口的备用请求（签名同 request）。取全量 key 撞「按出口 IP 限流」时
		# 靠它换出口重试；None 表示该类账号没有可轮换的通道。
		self.proxied_request = proxied_request


async def _agentrouter_session(account, force: bool = False) -> tuple[dict, str]:
	"""登录 agentrouter 换 session cookie，带缓存。返回 (cookies, user_id)

	缓存的意义不只是快：登录接口按 IP 限流，而 agentrouter「登录即签到」——
	复用 session 既少打限流，也避免查余额时顺带触发签到。
	"""
	import balance_server as bs

	cached = bs._agentrouter_key_sessions.get(account.name)
	if not force and cached and cached['expires'] > time.time():
		return cached['cookies'], cached['user_id']

	config = bs.AGENTROUTER_ORG_CONFIG
	proxies = {'https': bs._AGENTROUTER_PROXY}

	def _do():
		sess = bs._get_cffi_session(bs._ar_session_key('agentrouter-keys'), proxies)
		resp = sess.post(
			f'{config["domain"]}{config["login_path"]}',
			json={'username': account.username, 'password': account.password},
			timeout=15,
		)
		return resp, dict(sess.cookies)

	loop = asyncio.get_running_loop()
	# 登录接口按 IP 限流，串行 + 间隔，别让批量操作把窗口打满
	async with bs._agentrouter_login_lock:
		resp, jar = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
		await asyncio.sleep(1.5)
	# 429 的响应体是空的，直接 resp.json() 会抛 JSONDecodeError，
	# 报出来是「Expecting value: line 1 column 1」这种看不懂的错 —— 必须先认出限流。
	if resp.status_code == 429:
		raise RuntimeError('登录被站点限流（429），请等几分钟再试')
	if resp.status_code != 200:
		raise RuntimeError(f'登录失败: HTTP {resp.status_code}')
	try:
		data = resp.json()
	except Exception:
		raise RuntimeError(f'登录响应不是 JSON（HTTP {resp.status_code}）') from None
	if not data.get('success'):
		raise RuntimeError(f'登录失败: {data.get("message", "Unknown")}')
	user_id = str((data.get('data') or {}).get('id') or '')
	if not user_id:
		raise RuntimeError('登录成功但没拿到 user id')
	bs._agentrouter_key_sessions[account.name] = {
		'cookies': jar,
		'user_id': user_id,
		'expires': time.time() + bs.AGENTROUTER_SESSION_TTL,
	}
	save_agentrouter_sessions()
	return jar, user_id


async def resolve_key_ctx(ref: str) -> tuple[KeyCtx | None, str | None]:
	"""把前端的账号引用（token:3 / cookie:0 / login:2 / site:tabitoken:0）解析成 KeyCtx。

	格式与前端账号卡的 `_ref` 完全一致，这样前端不用再维护第二套寻址方式。
	"""
	import balance_server as bs

	parts = str(ref).split(':')
	kind = parts[0] if parts else ''

	def _idx(pos: int) -> int | None:
		try:
			return int(parts[pos])
		except (IndexError, ValueError):
			return None

	if kind in ('token', 'cookie'):
		idx = _idx(1)
		accounts = bs.load_token_accounts() if kind == 'token' else bs.load_cookie_accounts()
		if idx is None or idx < 0 or idx >= len(accounts):
			return None, f'账号引用 {ref} 无效'
		account = accounts[idx]
		config = bs.ANYROUTER_CONFIG
		waf = await bs._get_waf_cookies_if_needed()
		headers = {
			'User-Agent': bs.USER_AGENT,
			'Accept': 'application/json, text/plain, */*',
			'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
			'Referer': config['domain'],
			'Origin': config['domain'],
		}
		if kind == 'token':
			headers['Authorization'] = f'Bearer {account.access_token}'
			headers[config['api_user_key']] = account.user_id
			cookies = dict(waf)
		else:
			headers[config['api_user_key']] = account.api_user
			cookies = {**waf, **account.cookies}

		async def _req(method, path, json_body=None, _h=headers, _c=cookies):
			resp = await bs.anyrouter_request(method, bs._api_url(path), _h, cookies=_c, json_body=json_body)
			blocked = bs.anyrouter_block_reason(resp)
			if blocked:
				raise RuntimeError(blocked[1])
			return resp

		return KeyCtx(ref, account.name, 'AnyRouter', _req), None

	if kind == 'login':
		idx = _idx(1)
		accounts = bs.load_login_accounts()
		if idx is None or idx < 0 or idx >= len(accounts):
			return None, f'账号引用 {ref} 无效'
		account = accounts[idx]
		config = bs.AGENTROUTER_ORG_CONFIG
		try:
			cookies, user_id = await bs._agentrouter_session(account)
		except Exception as e:
			return None, f'{account.name}: {e}'
		proxies = {'https': bs._AGENTROUTER_PROXY}
		headers = {
			'User-Agent': bs.USER_AGENT,
			'Accept': 'application/json, text/plain, */*',
			'Referer': config['domain'],
			'new-api-user': user_id,
		}

		async def _req(method, path, json_body=None, _h=headers, _c=cookies):
			def _do():
				sess = bs._get_cffi_session('agentrouter-keys-req', proxies)
				return sess.request(
					method.upper(), f'{config["domain"]}{path}', headers=_h, cookies=_c, json=json_body, timeout=20
				)

			loop = asyncio.get_running_loop()
			return await loop.run_in_executor(bs._UPSTREAM_POOL, _do)

		return KeyCtx(ref, account.name, 'AgentRouter', _req), None

	if kind == 'site':
		if len(parts) < 3:
			return None, f'账号引用 {ref} 无效'
		site = bs.get_newapi_site(parts[1])
		if site is None:
			return None, f'站点 {parts[1]} 不存在'
		accounts = bs.load_newapi_accounts(site)
		idx = _idx(2)
		if idx is None or idx < 0 or idx >= len(accounts):
			return None, f'账号引用 {ref} 无效'
		account = accounts[idx]
		headers = bs._newapi_headers(site, account)

		async def _req(method, path, json_body=None, _s=site, _h=headers):
			return await bs.newapi_request(_s, method, path, _h, json_body=json_body)

		async def _proxied(method, path, json_body=None, _s=site, _h=headers):
			return await bs._proxied_newapi_request(_s, method, path, _h, json_body=json_body)

		return KeyCtx(ref, account.name, site.label, _req, site.quota_per_unit, proxied_request=_proxied), None

	return None, f'未知的账号类型：{ref}'


def _parse_token_items(data) -> list[dict]:
	"""列表响应有两种形态：旧版 data 直接是数组，新版是 {page,page_size,total,items}"""
	if isinstance(data, dict):
		return data.get('items') or []
	if isinstance(data, list):
		return data
	return []


def _token_row(t: dict, unit: int) -> dict:
	"""把上游的令牌对象整理成前端要展示的字段。额度按站点的 quota_per_unit 换算成美元。"""
	key = t.get('key') or ''
	return {
		'id': t.get('id'),
		'name': t.get('name') or '',
		'key': key,
		'masked': '*' in key,
		'status': t.get('status'),
		'unlimited_quota': bool(t.get('unlimited_quota')),
		'remain_quota': round((t.get('remain_quota') or 0) / unit, 2),
		'used_quota': round((t.get('used_quota') or 0) / unit, 2),
		'expired_time': t.get('expired_time'),
		'created_time': t.get('created_time'),
		'accessed_time': t.get('accessed_time'),
		'group': t.get('group') or '',
		'model_limits_enabled': bool(t.get('model_limits_enabled')),
		'model_limits': t.get('model_limits') or '',
		'allow_ips': t.get('allow_ips') or '',
	}


async def reveal_key_values(ctx: KeyCtx, rows: list[dict], request=None) -> str | None:
	"""把脱敏的 key 换成全量值（就地改 rows）。返回警告文案，None 表示全部拿到。

	默认走 ctx.request（与该账号平时一致的通道）；`request=` 可注入别的出口 ——
	撞限流轮换时传 ctx.proxied_request，让这次请求经 mihomo 的新出口出去。
	优先用 `POST /api/token/batch/keys` —— 一次请求拿一个账号的全部 key，
	而它和逐个取的 `POST /api/token/{id}/key` 共享同一个 20 次/20 分钟/IP 的配额，
	所以账号多的时候「批量」是唯一可行的方式。
	"""
	import balance_server as bs

	req = request or ctx.request
	need = []
	for r in rows:
		if not r['masked']:
			continue
		cached = bs._key_value_cache.get(f'{ctx.ref}:{r["id"]}')
		if cached:
			r['key'], r['masked'] = cached, False
		else:
			need.append(r)
	if not need:
		return None

	ids = [r['id'] for r in need if r['id'] is not None]
	try:
		resp = await req('POST', '/api/token/batch/keys', {'ids': ids})
	except Exception as e:
		return f'取完整密钥失败：{e}'
	if resp.status_code == 429:
		return '站点限流（取密钥 20 次/20 分钟），请稍后再试'
	if resp.status_code in (401, 403):
		# 认证问题重试也没用，别掉进下面「没有 batch 端点」的逐个取兜底（tb0 实测：
		# access_token 失效时 batch 与逐个全 401，最后报成「N 个密钥未取到完整值」，看不出根因）
		try:
			msg = resp.json().get('message') or f'HTTP {resp.status_code}'
		except Exception:
			msg = f'HTTP {resp.status_code}'
		return f'取完整密钥失败：{msg}（该账号的 access_token 可能已失效，请更新后重试）'
	if resp.status_code == 200:
		try:
			data = resp.json()
		except Exception:
			data = {}
		if data.get('success'):
			keys = (data.get('data') or {}).get('keys') or {}
			missing = 0
			for r in need:
				full = keys.get(str(r['id'])) or keys.get(r['id'])
				if full:
					r['key'], r['masked'] = full, False
					bs._key_value_cache[f'{ctx.ref}:{r["id"]}'] = full
				else:
					missing += 1
			return f'{missing} 个密钥未取到完整值' if missing else None
		return f'取完整密钥失败：{data.get("message") or "Unknown"}'

	# 旧版本可能没有 batch 端点，退回逐个取。同样吃 20 次/20 分钟的配额，
	# 所以只在密钥不多时才走，免得一个账号就把配额耗光。
	if len(need) > 5:
		return f'该站点不支持批量取密钥，且待取 {len(need)} 个超过单账号上限，请逐个查看'
	failed = 0
	for r in need:
		try:
			one = await req('POST', f'/api/token/{r["id"]}/key')
			full = ((one.json() or {}).get('data') or {}).get('key') if one.status_code == 200 else None
		except Exception:
			full = None
		if full:
			r['key'], r['masked'] = full, False
			bs._key_value_cache[f'{ctx.ref}:{r["id"]}'] = full
		else:
			failed += 1
	return f'{failed} 个密钥未取到完整值' if failed else None


async def list_account_keys(ctx: KeyCtx, refresh: bool = False, reveal: bool = True) -> dict:
	"""列出一个账号的密钥；reveal=True（默认）顺手把脱敏的 key 换成全量（前端要直接展示完整密钥）。

	取全量走 `reveal_key_values`（值另进 `_key_value_cache`），拿到后把**全量成品**写进列表缓存，
	之后打开弹窗、复制都零上游请求；限流拿不到时保持脱敏并带 `warning`，
	下次命中缓存还会自动补取一次再回写（旧版缓存文件里只有脱敏列表，靠这步无痛升级）。
	keys_list 端点传 reveal=False —— 取全量是 20 次/20 分钟/IP 的限流资源且**跨账号共享**，
	由端点层的 `_reveal_accounts` 统一协调（小批推进 + 撞限流自动换出口）；
	单账号调用方（建/删后重列）保持默认 True。
	refresh=False 且有缓存时直接回缓存，结果带 `cached_at` 供前端标注时间。
	"""
	import balance_server as bs

	base = {'ref': ctx.ref, 'name': ctx.name, 'provider': ctx.provider}
	ckey = f'{ctx.ref}|{ctx.name}'
	if not refresh:
		hit = bs._keys_list_cache.get(ckey)
		if hit:
			out = json.loads(json.dumps(hit['result']))
			if reveal and any(k.get('masked') for k in out.get('keys', [])):
				out['warning'] = await reveal_key_values(ctx, out['keys'])
				hit['result'] = json.loads(json.dumps(out))
				save_keys_list_cache()
			out['cached'] = True
			out['cached_at'] = hit.get('ts')
			return out
	try:
		resp = await ctx.request('GET', f'{bs.TOKEN_LIST_PATH}?p=1&page_size={bs.TOKEN_PAGE_SIZE}')
	except Exception as e:
		return {**base, 'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}
	if resp.status_code != 200:
		return {**base, 'success': False, 'error': f'HTTP {resp.status_code}'}
	try:
		data = resp.json()
	except Exception:
		return {**base, 'success': False, 'error': '响应不是 JSON（可能被拦截）'}
	if not data.get('success'):
		return {**base, 'success': False, 'error': data.get('message') or 'Unknown'}

	payload = data.get('data')
	items = _parse_token_items(payload)
	total = payload.get('total') if isinstance(payload, dict) else len(items)
	rows = [_token_row(t, ctx.quota_per_unit) for t in items]
	result = {
		**base,
		'success': True,
		'keys': rows,
		'total': total if isinstance(total, int) else len(rows),
		'truncated': isinstance(total, int) and total > len(rows),
		'warning': await reveal_key_values(ctx, rows) if reveal else None,
	}
	stored = json.loads(json.dumps(result))  # 深拷贝：调用方会就地改 rows，不能穿透进缓存
	stored.pop('warning', None)  # warning 是瞬时状态（限流提示），别带进缓存
	bs._keys_list_cache[ckey] = {'ts': time.time(), 'result': stored}
	save_keys_list_cache()
	return result


def _reveal_scope(ctx: KeyCtx) -> str:
	"""限流熔断的维度：站点账号按站点，其余按账号类型（不同站点的限流互相独立）。"""
	parts = ctx.ref.split(':')
	return ':'.join(parts[:2]) if parts[0] == 'site' else parts[0]


def _keys_cache_store_if_complete(ctx: KeyCtx, acc: dict) -> None:
	"""账号的 key 全部拿到全量后回写列表缓存（warning/cached 等瞬态字段不落盘，深拷贝防穿透）。"""
	import balance_server as bs

	if not acc.get('success') or any(k.get('masked') for k in acc.get('keys', [])):
		return
	stored = json.loads(json.dumps({k: v for k, v in acc.items() if k not in ('warning', 'cached', 'cached_at')}))
	bs._keys_list_cache[f'{ctx.ref}|{ctx.name}'] = {'ts': time.time(), 'result': stored}
	save_keys_list_cache()


async def _reveal_accounts(pairs: list[tuple[KeyCtx | None, dict]]) -> None:
	"""把各账号列表里的脱敏 key 统一取成全量（跨账号协调 + 撞限流自动换出口）。

	限流跨账号共享：各账号并发猛打必然后面全 429。这里小批推进，撞 429 先切 mihomo 出口
	重试（经一次性新连接，必然走新出口），出口用尽才熔断 KEYS_REVEAL_LIMIT_WINDOW（期间不再打上游）。
	成功一个账号就回写列表缓存，之后打开零上游。
	"""
	import balance_server as bs

	pending = [
		(ctx, acc) for ctx, acc in pairs
		if ctx is not None and acc.get('success') and any(k.get('masked') for k in acc.get('keys', []))
	]
	if not pending:
		return
	scope = _reveal_scope(pending[0][0])
	until = bs._keys_reveal_until.get(scope, 0)
	if time.time() < until:
		mins = max(1, int((until - time.time()) // 60) + 1)
		for _, acc in pending:
			acc['warning'] = f'站点限流中（取密钥 20 次/20 分钟/IP），约 {mins} 分钟后自动恢复'
		return
	if bs._balances_query_lock.locked():
		rotator = None  # agentrouter 的出口轮换正在跑，切节点会互相踩 —— 退化为直连硬扛
	else:
		rotator = bs._KeysExitRotator()
		if not await rotator.prepare():
			rotator = None
	switches = 0
	via_proxy = False
	async with bs._keys_reveal_lock:
		try:
			i = 0
			while i < len(pending):
				batch = pending[i:i + bs.KEYS_REVEAL_CONCURRENCY]

				async def _one(c: KeyCtx, a: dict):
					return await reveal_key_values(c, a['keys'], request=c.proxied_request if via_proxy else None)

				warnings = await asyncio.gather(*[_one(c, a) for c, a in batch])
				for (_, acc), w in zip(batch, warnings):
					acc['warning'] = w
				i += len(batch)
				for c, a in batch:
					_keys_cache_store_if_complete(c, a)
				for (c, a), w in zip(batch, warnings):
					if w and '限流' in w and not c.proxied_request:
						a['warning'] = '站点限流（取密钥 20 次/20 分钟/IP），该类账号没有可换的代理出口，请稍后再试'
				retry = [(c, a) for (c, a), w in zip(batch, warnings) if w and '限流' in w and c.proxied_request]
				while retry:
					if rotator is None or switches >= bs.KEYS_REVEAL_MAX_SWITCHES or not await rotator.next_exit():
						bs._keys_reveal_until[scope] = time.time() + bs.KEYS_REVEAL_LIMIT_WINDOW
						for _, a in retry + pending[i:]:
							a['warning'] = '站点限流（取密钥 20 次/20 分钟/IP）且可用出口已用尽，请 20 分钟后再试'
						return
					switches += 1
					via_proxy = True
					warnings = await asyncio.gather(*[
						reveal_key_values(c, a['keys'], request=c.proxied_request) for c, a in retry
					])
					for (_, acc), w in zip(retry, warnings):
						acc['warning'] = w
					for c, a in retry:
						_keys_cache_store_if_complete(c, a)
					retry = [(c, a) for (c, a), w in zip(retry, warnings) if w and '限流' in w]
		finally:
			if rotator is not None:
				await rotator.restore()


@keys_router.post('/api/keys/list')
async def keys_list(req: dict):
	"""列出若干账号的密钥（脱敏的取成全量后落缓存，之后打开/复制都零上游请求）。

	refs 用前端账号卡的 `_ref` 格式，与账号类型无关；
	refresh=True 绕过列表缓存强制重查（密钥很少变，默认走缓存，见 list_account_keys）。
	取全量由 `_reveal_accounts` 跨账号协调 —— 限流 20 次/20 分钟/IP 是全端点共享的，
	账号多的站点（tabitoken 29 个）必须撞限流换出口才能一轮拿完。
	"""
	import balance_server as bs

	refs = req.get('refs') or []
	if not isinstance(refs, list) or not refs:
		return {'success': False, 'error': '没有指定账号'}
	refresh = bool(req.get('refresh'))

	sem = asyncio.Semaphore(6)

	async def _one(ref):
		ctx, err = await bs.resolve_key_ctx(ref)
		if err:
			return None, {'ref': ref, 'name': str(ref), 'provider': '', 'success': False, 'error': err}
		async with sem:
			return ctx, await bs.list_account_keys(ctx, refresh, reveal=False)

	pairs = list(await asyncio.gather(*[_one(r) for r in refs]))
	await _reveal_accounts(pairs)
	return {'success': True, 'accounts': [acc for _, acc in pairs]}


@keys_router.post('/api/keys/create')
async def keys_create(req: dict):
	"""给某个账号新建一个密钥。上游 AddToken 只回 success 不回 key，所以创建后重新列一次。"""
	import balance_server as bs

	ref = req.get('ref') or ''
	ctx, err = await bs.resolve_key_ctx(ref)
	if err:
		return {'success': False, 'error': err}

	name = (req.get('name') or '').strip()
	if not name:
		return {'success': False, 'error': '密钥名称不能为空'}
	if len(name) > 50:
		return {'success': False, 'error': '密钥名称不能超过 50 个字符'}

	unlimited = req.get('unlimited_quota', True)
	# 前端传的是美元，上游要的是原始额度
	quota = req.get('remain_quota') or 0
	body = {
		'name': name,
		'remain_quota': 0 if unlimited else int(float(quota) * ctx.quota_per_unit),
		'expired_time': int(req.get('expired_time') or -1),
		'unlimited_quota': bool(unlimited),
		'model_limits_enabled': False,
		'model_limits': '',
		'allow_ips': '',
		'group': req.get('group') or '',
	}
	try:
		resp = await ctx.request('POST', bs.TOKEN_LIST_PATH, body)
	except Exception as e:
		return {'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}
	if resp.status_code != 200:
		return {'success': False, 'error': f'HTTP {resp.status_code}'}
	try:
		data = resp.json()
	except Exception:
		return {'success': False, 'error': '响应不是 JSON（可能被拦截）'}
	if not data.get('success'):
		return {'success': False, 'error': data.get('message') or 'Unknown'}

	return {'success': True, 'account': await bs.list_account_keys(ctx, refresh=True)}


@keys_router.post('/api/keys/delete')
async def keys_delete(req: dict):
	"""删除某个账号下的一个密钥"""
	import balance_server as bs

	ref = req.get('ref') or ''
	key_id = req.get('id')
	if key_id is None:
		return {'success': False, 'error': '没有指定密钥 id'}
	ctx, err = await bs.resolve_key_ctx(ref)
	if err:
		return {'success': False, 'error': err}
	try:
		resp = await ctx.request('DELETE', f'{bs.TOKEN_LIST_PATH}{key_id}')
	except Exception as e:
		return {'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}
	if resp.status_code != 200:
		return {'success': False, 'error': f'HTTP {resp.status_code}'}
	try:
		data = resp.json()
	except Exception:
		return {'success': False, 'error': '响应不是 JSON（可能被拦截）'}
	if not data.get('success'):
		return {'success': False, 'error': data.get('message') or 'Unknown'}
	bs._key_value_cache.pop(f'{ref}:{key_id}', None)
	bs._keys_list_cache.pop(f'{ref}|{ctx.name}', None)
	return {'success': True, 'account': await bs.list_account_keys(ctx, refresh=True)}
