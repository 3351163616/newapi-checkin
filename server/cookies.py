"""Cookie 域（saved_config.json 的 session 账号）：阿里云 WAF cookies 求解与缓存、
余额查询/签到（cookie 与 token 两种方式的公共实现）、session 续期、cookie 签到流程。

过渡期约定（拆分块D3，同 server/keys.py）：不做模块级 `import balance_server`，跨实体
引用（CONFIG_FILE、代理/UA、_get_cffi_session、可被测试 patch 的
_get_waf_cookies_if_needed / save state 助手等）在函数体内晚绑定 `bs.<名字>`。
waf_cache / _waf_lock 只做条目级变更，真实住所在本模块、bs 重导出同一对象；
anyrouter_checkin_state 被测试整体重绑，家在 balance_server，本模块一律经 bs. 读写。
"""

import asyncio
import base64
import json
import time
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from server.config import USER_AGENT

cookies_router = APIRouter()

waf_cache: dict = {}
_waf_lock = asyncio.Lock()

ANYROUTER_CONFIG = {
	'domain': 'https://anyrouter.top',
	'login_path': '/login',
	'user_info_path': '/api/user/self',
	'sign_in_path': '/api/user/sign_in',
	'api_user_key': 'new-api-user',
	'waf_cookie_names': ['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'],
}


class AccountItem(BaseModel):
	"""传统 session cookie 方式"""
	name: str
	cookies: dict
	api_user: str

class QueryRequest(BaseModel):
	accounts: list[AccountItem]


class TokenAccountItem(BaseModel):
	"""传统 access_token 方式（new_accounts_config.json）—— cookies 域内查询/签到用"""
	name: str
	access_token: str
	user_id: str


class TokenQueryRequest(BaseModel):
	accounts: list[TokenAccountItem]




def _api_url(path: str) -> str:
	"""返回 API 地址"""
	return ANYROUTER_CONFIG['domain'] + path


async def anyrouter_request(method: str, url: str, headers: dict, cookies: dict | None = None, json_body=None):
	"""向 anyrouter.top 发请求：curl_cffi 模拟 Chrome TLS 指纹 + 走代理（绕过 WAF/TLS 指纹检测）。

	curl_cffi 是同步库，放到专用线程池执行，并按线程复用 Session 以复用代理隧道与 TLS 握手。

	若响应是 WAF 挑战页，就用响应体里新的 arg1 就地重算 acw_sc__v2 再打一次（2026-08-09 实测有效）。
	这比重新走 get_waf_cookies() 少一个请求 —— 请求数直接决定会不会撞上 ESA 的 IP 限流。
	对 POST（签到）重试也是安全的：挑战页由阿里云边缘返回，请求没到过 new-api 源站，不会重复签到。
	返回 curl_cffi 的 Response 对象。
	"""
	import balance_server as bs

	proxies = {'https': bs._LOCAL_PROXY, 'http': bs._LOCAL_PROXY}
	send = dict(cookies or {})

	def _do(ck: dict):
		sess = bs._get_cffi_session('anyrouter', proxies)
		return sess.request(method.upper(), url, headers=headers, cookies=ck, json=json_body)

	loop = asyncio.get_running_loop()
	resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do, send)
	if resp.status_code != 200:
		return resp
	try:
		m = bs._WAF_CHALLENGE_RE.search(resp.text or '')
	except Exception:
		return resp
	if not m:
		return resp
	try:
		fresh = bs._solve_acw_sc_v2(m.group(1))
	except Exception:
		return resp

	# 挑战页可能顺带下发新的 acw_tc/cdn_sec_tc（Max-Age 只有 1 小时，缓存里的可能已过期），
	# 而 acw_sc__v2 是配着它们校验的，所以重试要用挑战页给的新值。只取 WAF 那几个名字，
	# 避免把响应里其它 cookie（如 session）混进来。
	try:
		issued = dict(resp.cookies)
	except Exception:
		issued = {}
	for name in ANYROUTER_CONFIG['waf_cookie_names']:
		if issued.get(name):
			send[name] = issued[name]
	send['acw_sc__v2'] = fresh
	cached = bs.waf_cache.get('anyrouter')
	if cached:
		# 让同一轮里后续账号直接用新值，别再各撞一次挑战页
		for name in ANYROUTER_CONFIG['waf_cookie_names']:
			if send.get(name):
				cached['cookies'][name] = send[name]
	return await loop.run_in_executor(bs._UPSTREAM_POOL, _do, send)


def anyrouter_block_reason(resp) -> tuple[str, str] | None:
	"""判断响应是否被拦截，返回 (kind, 人话原因)；正常返回 None。

	kind 有三种：
	  ratelimit — 阿里云 ESA 按出口 IP 限流：403 + 正文写着「Denied by http_ratelimit」、server: ESA。
	              实测与账号、路径、并发度都无关。2026-08-09 一次持续 28 分钟以上，2026-08-10 一次
	              在每 2.5 分钟探一次的情况下超过 30 分钟仍未解除 —— 探测本身也算请求，很可能在给
	              窗口续命。所以对策是**彻底停手等**，别写死"多久恢复"，也别循环重试。
	  challenge — 仍是 WAF 挑战页（正文带 arg1），说明 acw_sc__v2 没带上或算错了。
	  http      — 其它非 200。
	"""
	import balance_server as bs

	try:
		body = resp.text or ''
	except Exception:
		body = ''
	if resp.status_code != 200:
		m = bs._ESA_DENY_RE.search(body)
		if m:
			rule = m.group(1)
			if 'ratelimit' in rule.lower():
				return ('ratelimit', '站点限流：出口 IP 被 ESA 临时封禁，请过一段时间再试，期间不要反复重试')
			return ('http', f'被站点安全策略拦截（ESA {rule}）')
		return ('http', f'HTTP {resp.status_code}')
	if bs._WAF_CHALLENGE_RE.search(body):
		return ('challenge', 'WAF 挑战未通过：返回的是验证页而非数据')
	return None


async def _get_waf_cookies_if_needed() -> dict:
	"""获取 WAF cookies"""
	cookies = await get_waf_cookies()
	if cookies is None:
		return {}
	return cookies


@cookies_router.get('/api/waf/warmup')
async def waf_warmup():
	"""预热 WAF cookies 缓存，前端页面加载时调用"""
	cookies = await get_waf_cookies()
	if cookies:
		return {'success': True, 'message': 'WAF cookies 已就绪'}
	return {'success': False, 'message': 'WAF cookies 获取失败'}


async def get_waf_cookies() -> dict | None:
	"""获取 WAF cookies（curl_cffi 求解 acw_sc__v2 挑战），带缓存 + singleflight。

	阿里云 WAF 现已按 TLS 指纹（JA3）拦截无头 Chromium，导致 Playwright 直接握手失败
	（net::ERR_SSL_VERSION_OR_CIPHER_MISMATCH），拿不到 cookie。改用 curl_cffi 模拟
	Chrome 指纹访问登录页，提取 acw_tc/cdn_sec_tc 并解析 arg1 计算 acw_sc__v2 即可通过校验。

	加锁做 singleflight：缓存过期瞬间，warmup/查询/签到等并发调用只放一个去打挑战页，
	其余等结果 —— 挑战页请求是要省着用的配额。
	"""
	import balance_server as bs

	cached = waf_cache.get('anyrouter')
	if cached and cached['expires'] > time.time():
		return cached['cookies']

	async with _waf_lock:
		# 双检：排队等锁期间可能已有同伴刷新了缓存
		cached = waf_cache.get('anyrouter')
		if cached and cached['expires'] > time.time():
			return cached['cookies']

		config = ANYROUTER_CONFIG
		login_url = f'{config["domain"]}{config["login_path"]}'
		required = config['waf_cookie_names']

		def _do() -> dict:
			from curl_cffi import requests as cffi_requests

			# 这里刻意新建独立 Session（不复用 _get_cffi_session）：需要一个干净的 cookie jar
			# 来收集登录页下发的 Set-Cookie。每 5 分钟才走一次，握手开销可忽略。
			sess = cffi_requests.Session(
				impersonate='chrome131',
				proxies={'https': bs._LOCAL_PROXY, 'http': bs._LOCAL_PROXY},
				timeout=30,
			)
			resp = sess.get(login_url, headers={'User-Agent': bs.USER_AGENT})
			waf_cookies = {}
			for name in required:
				val = sess.cookies.get(name)
				if val:
					waf_cookies[name] = val
			m = bs._WAF_CHALLENGE_RE.search(resp.text)
			if m:
				waf_cookies['acw_sc__v2'] = bs._solve_acw_sc_v2(m.group(1))
			return waf_cookies

		try:
			loop = asyncio.get_running_loop()
			waf_cookies = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
			if waf_cookies:
				waf_cache['anyrouter'] = {
					'cookies': waf_cookies,
					'expires': time.time() + bs.WAF_CACHE_TTL,
				}
				return waf_cookies
			return None
		except Exception as e:
			print(f'[WAF] Error: {e}')
			return None


async def _query_balance_impl(name: str, headers: dict, cookies: dict) -> dict:
	"""余额查询的公共实现（cookie 与 access_token 两方式只差 headers/cookies 的构造）"""
	url = _api_url(ANYROUTER_CONFIG['user_info_path'])
	max_retries = 3

	for attempt in range(max_retries):
		try:
			resp = await anyrouter_request('GET', url, headers, cookies=cookies)
			blocked = anyrouter_block_reason(resp)
			if blocked:
				kind, why = blocked
				return {'name': name, 'success': False, 'error': why, 'blocked': kind}
			data = resp.json()
			if data.get('success'):
				user_data = data.get('data', {})
				return {
					'name': name,
					'success': True,
					'quota': round(user_data.get('quota', 0) / 500000, 2),
					'used': round(user_data.get('used_quota', 0) / 500000, 2),
					'username': user_data.get('username', ''),
				}
			return {
				'name': name,
				'success': False,
				'error': f'API 返回失败: {data.get("message", "Unknown")}',
			}
		except Exception as e:
			if attempt < max_retries - 1:
				await asyncio.sleep(1.5 * (attempt + 1))
				continue
			return {
				'name': name,
				'success': False,
				'error': f'{type(e).__name__}: {e}'[:150] or f'{type(e).__name__}',
			}


async def query_balance(account: AccountItem, waf_cookies: dict) -> dict:
	"""查询单个账号余额（cookie 方式，anyrouter.top）"""
	config = ANYROUTER_CONFIG
	all_cookies = {**waf_cookies, **account.cookies}

	headers = {
		'User-Agent': USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Referer': config['domain'],
		'Origin': config['domain'],
		config['api_user_key']: account.api_user,
	}
	return await _query_balance_impl(account.name, headers, all_cookies)


async def query_balance_with_token(account, waf_cookies: dict) -> dict:
	"""使用 access_token 查询单个账号余额（anyrouter.top）"""
	config = ANYROUTER_CONFIG

	headers = {
		'User-Agent': USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Referer': config['domain'],
		'Origin': config['domain'],
		'Authorization': f'Bearer {account.access_token}',
		config['api_user_key']: account.user_id,
	}
	return await _query_balance_impl(account.name, headers, waf_cookies)


async def _sign_in_impl(name: str, headers: dict, cookies: dict) -> dict:
	"""签到的公共实现（cookie 与 access_token 两方式只差 headers/cookies 的构造）"""
	url = _api_url(ANYROUTER_CONFIG['sign_in_path'])
	max_retries = 3

	for attempt in range(max_retries):
		try:
			resp = await anyrouter_request('POST', url, headers, cookies=cookies)
			blocked = anyrouter_block_reason(resp)
			if blocked:
				kind, why = blocked
				return {'name': name, 'success': False, 'message': why, 'blocked': kind}
			data = resp.json()
			if data.get('success'):
				msg = data.get('message', '')
				# 空消息表示签到成功，有消息可能是"今日已签到"等
				return {
					'name': name,
					'success': True,
					'message': msg if msg else '签到成功 +$25',
					'already_signed': bool(msg),
				}
			return {'name': name, 'success': False, 'message': data.get('message', '签到失败')}
		except Exception as e:
			if attempt < max_retries - 1:
				await asyncio.sleep(1.5 * (attempt + 1))
				continue
			return {'name': name, 'success': False, 'message': f'{type(e).__name__}: {e}'[:100]}


async def sign_in(account: AccountItem, waf_cookies: dict) -> dict:
	"""为单个账号执行签到（cookie 方式，anyrouter.top）"""
	config = ANYROUTER_CONFIG
	all_cookies = {**waf_cookies, **account.cookies}

	headers = {
		'User-Agent': USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Referer': f'{config["domain"]}/console',
		'Origin': config['domain'],
		config['api_user_key']: account.api_user,
		'Cache-Control': 'no-store',
	}
	return await _sign_in_impl(account.name, headers, all_cookies)


async def sign_in_with_token(account, waf_cookies: dict) -> dict:
	"""使用 access_token 为单个账号执行签到（anyrouter.top）"""
	config = ANYROUTER_CONFIG

	headers = {
		'User-Agent': USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Referer': f'{config["domain"]}/console',
		'Origin': config['domain'],
		'Authorization': f'Bearer {account.access_token}',
		config['api_user_key']: account.user_id,
		'Cache-Control': 'no-store',
	}
	return await _sign_in_impl(account.name, headers, waf_cookies)


def load_cookie_accounts() -> list[AccountItem]:
	"""从 saved_config.json 加载 cookie/session 方式账号列表"""
	import balance_server as bs

	if not bs.CONFIG_FILE.exists():
		return []
	try:
		data = bs._read_json_cached(bs.CONFIG_FILE)
		accounts = data.get('accounts', []) if isinstance(data, dict) else data
		return [AccountItem(**a) for a in accounts]
	except Exception as e:
		print(f'[ANYROUTER] 加载 cookie 账号失败: {e}')
		return []


def _session_is_authenticated(session: str) -> bool | None:
	"""从 session cookie 本地判断它是否代表已登录身份。True/False 为确定结论，None 表示判不出来。

	gorilla securecookie 的载荷是 base64(时间戳|gob|HMAC)，gob 里是 gin session 的键值对明文
	（只签名不加密）。2026-08-09 实测两种 cookie 的差别很干净：

	  匿名（未登录时调 /api/oauth/state 得到）：176 字节，只有 oauth_state
	  已登录：496 字节，含 id / username / role / status / group / aff

	所以有 `id` 键即已登录。这样就不必为每个账号多打一次 /api/user/self —— 请求数直接决定
	会不会撞上 ESA 的 IP 限流，27 个账号能从 54 个请求降到 27 个。

	判不出来时返回 None 而不是 False：new-api 换了 session 结构的话，宁可退回打接口核实，
	也不要把好账号误报成"已失效，请重新登录"。
	"""
	try:
		raw = base64.urlsafe_b64decode(session + '=' * (-len(session) % 4))
		parts = raw.split(b'|')
		if len(parts) < 2:
			return None
		gob = base64.urlsafe_b64decode(parts[1] + b'=' * (-len(parts[1]) % 4))
	except Exception:
		return None
	if b'oauth_state' not in gob:
		return None  # 连预期的键都没有，说明结构变了，交给接口核实
	# gob 的字符串键以「长度字节 + 内容」编码，用 \x02id 精确匹配，避免撞上 username 里的 "id"
	return b'\x02id' in gob


def save_renewed_sessions(updates: dict):
	"""把续期得到的新 session 批量写回 saved_config.json（按账号名匹配）"""
	import balance_server as bs

	if not updates or not bs.CONFIG_FILE.exists():
		return
	try:
		data = json.loads(bs.CONFIG_FILE.read_text(encoding='utf-8'))
		accounts = data.get('accounts', []) if isinstance(data, dict) else data
		for a in accounts:
			if a.get('name') in updates:
				a.setdefault('cookies', {})['session'] = updates[a['name']]
		bs._atomic_write_json(bs.CONFIG_FILE, data, indent=2)
	except Exception as e:
		print(f'[ANYROUTER] 写回续期 session 失败: {e}')


async def renew_one_cookie(account: AccountItem, waf_cookies: dict) -> dict:
	"""续期单个 cookie 账号的 session（+30 天）。

	调用 GET /api/oauth/state 触发服务端 session.Save() 重发 cookie。拿到新 cookie 后必须确认它
	仍代表已登录身份 —— 已过期的 session 打这个接口同样会 200 + 下发一个**匿名** cookie，写回去
	就把登录态弄丢了。优先用本地解码判断（零请求），只在解码判不出来时才打 /api/user/self 核实。
	"""
	import balance_server as bs

	base = ANYROUTER_CONFIG['domain']
	headers = {
		'User-Agent': bs.USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Referer': f'{base}/console',
		'Origin': base,
		ANYROUTER_CONFIG['api_user_key']: account.api_user,
		'Cache-Control': 'no-store',
	}
	try:
		all_cookies = {**waf_cookies, **account.cookies}
		resp = await bs.anyrouter_request('GET', base + '/api/oauth/state', headers, cookies=all_cookies)
		blocked = bs.anyrouter_block_reason(resp)
		if blocked:
			kind, why = blocked
			return {'name': account.name, 'success': False, 'message': f'续期失败 · {why}', 'blocked': kind}
		try:
			new_session = resp.cookies.get('session')
		except Exception:
			new_session = None
		if not new_session:
			return {'name': account.name, 'success': False, 'message': '续期接口未下发新 cookie（接口可能已变更）'}

		# 本地解码判身份：匿名 cookie 的 gob 里只有 oauth_state，登录 cookie 才带 id/username（已实测）
		authed = bs._session_is_authenticated(new_session)
		if authed is not True:
			# 解码判不出来（新版 new-api 可能换了 session 结构），退回打一次接口核实，别误判成失效
			check = await bs.anyrouter_request(
				'GET', base + '/api/user/self', headers, cookies={**waf_cookies, 'session': new_session}
			)
			blocked = bs.anyrouter_block_reason(check)
			if blocked:
				kind, why = blocked
				# 这里是核实请求被拦，不代表 cookie 有问题，别提示"重新登录"把人带偏
				return {
					'name': account.name,
					'success': False,
					'message': f'新 cookie 无法核实 · {why}',
					'blocked': kind,
				}
			ok = False
			try:
				cd = check.json()
				ok = bool(cd.get('success')) and cd.get('data', {}).get('id') is not None
			except Exception:
				ok = False
			if not ok:
				return {'name': account.name, 'success': False, 'message': 'cookie 已失效，无法续期，请重新登录'}
		info = bs._session_expiry_info(new_session) or {}
		return {
			'name': account.name,
			'success': True,
			'message': '续期成功',
			'new_session': new_session,
			'expires_at': info.get('expires_at'),
			'days_left': info.get('days_left'),
		}
	except Exception as e:
		return {'name': account.name, 'success': False, 'message': f'{type(e).__name__}: {e}'[:100]}


def add_anyrouter_checkin_log(msg: str):
	import balance_server as bs

	bs._checkin_add_log(bs.anyrouter_checkin_state, 'ANYROUTER', msg)


def save_anyrouter_checkin_state():
	"""持久化 AnyRouter 签到状态"""
	import balance_server as bs

	bs._checkin_save(bs.anyrouter_checkin_state, bs.ANYROUTER_CHECKIN_STATE_FILE, 'ANYROUTER')


def load_anyrouter_checkin_state():
	"""服务启动时恢复 AnyRouter 签到状态（仅用于前端展示历史进度）"""
	import balance_server as bs

	bs._checkin_load(bs.anyrouter_checkin_state, bs.ANYROUTER_CHECKIN_STATE_FILE, 'ANYROUTER')


async def run_anyrouter_checkin(trigger: str = 'manual'):
	"""执行签到：cookie 账号并发签到（Semaphore 取 ANYROUTER_CONCURRENCY），数秒内完成。"""
	import balance_server as bs

	accounts = load_cookie_accounts()
	st = bs.anyrouter_checkin_state
	today = datetime.now().strftime('%Y-%m-%d')
	st['running'] = True
	st['date'] = today
	st['trigger'] = trigger
	st['started_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	st['finished_at'] = None
	st['total'] = len(accounts)
	st['signed'] = 0
	st['already'] = 0
	st['failed'] = 0
	st['accounts'] = {a.name: {'status': 'pending', 'message': '等待签到', 'time': None} for a in accounts}
	st['logs'] = []
	add_anyrouter_checkin_log(f'开始签到（{trigger}），共 {len(accounts)} 个 cookie 账号')
	save_anyrouter_checkin_state()

	def _finish():
		st['signed'] = sum(1 for v in st['accounts'].values() if v['status'] == 'signed')
		st['already'] = sum(1 for v in st['accounts'].values() if v['status'] == 'already')
		st['failed'] = sum(1 for v in st['accounts'].values() if v['status'] == 'failed')
		st['running'] = False
		st['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		add_anyrouter_checkin_log(
			f'签到结束：成功 {st["signed"]} · 今日已签 {st["already"]} · 失败 {st["failed"]}'
		)
		save_anyrouter_checkin_state()

	if not accounts:
		add_anyrouter_checkin_log('没有 cookie 账号，签到结束')
		_finish()
		return

	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies:
		ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		for name in st['accounts']:
			st['accounts'][name] = {'status': 'failed', 'message': 'WAF cookies 获取失败', 'time': ts}
		add_anyrouter_checkin_log('WAF cookies 获取失败，签到中止')
		_finish()
		return

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)

	async def _one(acc: AccountItem):
		async with sem:
			if not st['running']:
				return
			result = await bs.sign_in(acc, waf_cookies)
			ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
			if result.get('success'):
				status = 'already' if result.get('already_signed') else 'signed'
				st['accounts'][acc.name] = {'status': status, 'message': result.get('message', '签到成功'), 'time': ts}
			else:
				st['accounts'][acc.name] = {'status': 'failed', 'message': result.get('message', '签到失败'), 'time': ts}
			add_anyrouter_checkin_log(f'{acc.name}: {st["accounts"][acc.name]["message"]}')
			save_anyrouter_checkin_state()

	await asyncio.gather(*[_one(a) for a in accounts])
	_finish()


def start_anyrouter_checkin(trigger: str = 'manual') -> bool:
	"""启动签到任务，若已在运行则返回 False"""
	import balance_server as bs

	if bs.anyrouter_checkin_state['running']:
		return False
	bs.anyrouter_checkin_state['task'] = asyncio.create_task(run_anyrouter_checkin(trigger))
	return True


# ===== 端点（块E 自 balance_server.py 迁入，晚绑定 bs.<名字>）=====

@cookies_router.post('/api/query')
async def query(req: QueryRequest):
	import balance_server as bs
	"""批量查询账号余额"""
	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies :
		return {'success': False, 'error': 'WAF cookies 获取失败，请稍后重试'}

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)

	async def limited_query(acc):
		async with sem:
			return await bs.query_balance(acc, waf_cookies)

	tasks = [limited_query(acc) for acc in req.accounts]
	results = await asyncio.gather(*tasks)

	total_quota = sum(r.get('quota', 0) for r in results if r.get('success'))
	total_used = sum(r.get('used', 0) for r in results if r.get('success'))

	return {
		'success': True,
		'results': results,
		'summary': {
			'total_quota': round(total_quota, 2),
			'total_used': round(total_used, 2),
			'account_count': len(results),
			'success_count': sum(1 for r in results if r.get('success')),
		},
	}



@cookies_router.post('/api/checkin')
async def checkin(req: QueryRequest):
	import balance_server as bs
	"""批量签到"""
	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies :
		return {'success': False, 'error': 'WAF cookies 获取失败，请稍后重试'}

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)

	async def limited_sign_in(acc):
		async with sem:
			return await bs.sign_in(acc, waf_cookies)

	tasks = [limited_sign_in(acc) for acc in req.accounts]
	results = await asyncio.gather(*tasks)

	success_count = sum(1 for r in results if r.get('success'))
	new_sign_count = sum(1 for r in results if r.get('success') and not r.get('already_signed'))

	return {
		'success': True,
		'results': results,
		'summary': {
			'total': len(results),
			'success': success_count,
			'new_signed': new_sign_count,
		},
	}



@cookies_router.get('/api/token/accounts')
async def get_token_accounts():
	import balance_server as bs
	"""获取 new_accounts_config.json 中的账号列表（含完整信息，用于管理）"""
	accounts = bs.load_token_accounts()
	return {
		'success': True,
		'accounts': [acc.model_dump() for acc in accounts],
	}



@cookies_router.post('/api/token/accounts')
async def save_token_accounts(req: dict):
	import balance_server as bs
	"""保存 token 账号列表到 new_accounts_config.json"""
	try:
		raw_accounts = req.get('accounts', [])
		validated = [bs.TokenAccountItem(**acc) for acc in raw_accounts]
		bs._atomic_write_json(NEW_ACCOUNTS_FILE, [acc.model_dump() for acc in validated], indent=2)
		return {'success': True}
	except Exception as e:
		return {'success': False, 'error': str(e)}



@cookies_router.post('/api/token/query')
async def query_with_token(req: TokenQueryRequest | None = None):
	import balance_server as bs
	"""使用 access_token 批量查询账号余额
	如果不传 accounts，则从 new_accounts_config.json 读取
	"""
	if req and req.accounts:
		accounts = req.accounts
	else:
		accounts = bs.load_token_accounts()
		if not accounts:
			return {'success': False, 'error': 'new_accounts_config.json 不存在或为空'}

	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies :
		return {'success': False, 'error': 'WAF cookies 获取失败，请稍后重试'}

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)

	async def limited_query(acc):
		async with sem:
			return await bs.query_balance_with_token(acc, waf_cookies)

	tasks = [limited_query(acc) for acc in accounts]
	results = await asyncio.gather(*tasks)

	total_quota = sum(r.get('quota', 0) for r in results if r.get('success'))
	total_used = sum(r.get('used', 0) for r in results if r.get('success'))

	return {
		'success': True,
		'results': results,
		'summary': {
			'total_quota': round(total_quota, 2),
			'total_used': round(total_used, 2),
			'account_count': len(results),
			'success_count': sum(1 for r in results if r.get('success')),
		},
	}



@cookies_router.post('/api/token/checkin')
async def checkin_with_token(req: TokenQueryRequest | None = None):
	import balance_server as bs
	"""使用 access_token 批量签到
	如果不传 accounts，则从 new_accounts_config.json 读取
	"""
	if req and req.accounts:
		accounts = req.accounts
	else:
		accounts = bs.load_token_accounts()
		if not accounts:
			return {'success': False, 'error': 'new_accounts_config.json 不存在或为空'}

	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies :
		return {'success': False, 'error': 'WAF cookies 获取失败，请稍后重试'}

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)

	async def limited_sign_in(acc):
		async with sem:
			return await sign_in_with_token(acc, waf_cookies)

	tasks = [limited_sign_in(acc) for acc in accounts]
	results = await asyncio.gather(*tasks)

	success_count = sum(1 for r in results if r.get('success'))
	new_sign_count = sum(1 for r in results if r.get('success') and not r.get('already_signed'))

	return {
		'success': True,
		'results': results,
		'summary': {
			'total': len(results),
			'success': success_count,
			'new_signed': new_sign_count,
		},
	}



@cookies_router.post('/api/anyrouter/checkin/start')
async def anyrouter_checkin_start():
	import balance_server as bs
	"""启动 AnyRouter cookie 账号签到（并发，数秒完成）"""
	if bs.anyrouter_checkin_state['running']:
		return {'success': False, 'error': 'AnyRouter 签到已在运行中', 'status': _anyrouter_checkin_status_payload()}
	accounts = bs.load_cookie_accounts()
	if not accounts:
		return {'success': False, 'error': '没有 cookie 账号可签到'}
	bs.start_anyrouter_checkin(trigger='manual')
	await asyncio.sleep(0.2)
	return {
		'success': True,
		'message': f'AnyRouter 签到已启动，共 {len(accounts)} 个账号',
		'status': _anyrouter_checkin_status_payload(),
	}



@cookies_router.get('/api/anyrouter/checkin/status')
async def anyrouter_checkin_status():
	import balance_server as bs
	"""获取 AnyRouter 签到进度状态"""
	return {'success': True, 'status': _anyrouter_checkin_status_payload()}



@cookies_router.get('/api/anyrouter/cookie-status')
async def anyrouter_cookie_status():
	import balance_server as bs
	"""返回每个 cookie 账号 session 的过期时间与剩余天数"""
	accounts = bs.load_cookie_accounts()
	items = []
	for a in accounts:
		info = bs._session_expiry_info(a.cookies.get('session', '')) or {}
		items.append({
			'name': a.name,
			'api_user': a.api_user,
			'expires_at': info.get('expires_at'),
			'days_left': info.get('days_left'),
		})
	return {'success': True, 'accounts': items}



@cookies_router.post('/api/anyrouter/renew')
async def anyrouter_renew(req: dict | None = None):
	import balance_server as bs
	"""续期 AnyRouter cookie 账号的 session（+30 天）。

	可传 {"names": [...]} 指定账号，不传则续期全部 cookie 账号。
	续期成功的新 session 会写回 saved_config.json。

	一旦某个账号撞上 ESA 的 IP 限流就中止后续账号：限的是出口 IP，剩下的账号必然同样失败，
	白打几十个请求还可能把限流窗口续上（计数器是否随新请求延长未实测，但没有理由去试）。
	实测触发后至少半小时内所有 anyrouter 功能全废。
	"""
	accounts = bs.load_cookie_accounts()
	if not accounts:
		return {'success': False, 'error': '没有 cookie 账号'}
	if req and req.get('names'):
		wanted = set(req['names'])
		accounts = [a for a in accounts if a.name in wanted]
		if not accounts:
			return {'success': False, 'error': '指定的账号不存在'}

	waf_cookies = await bs._get_waf_cookies_if_needed()
	if not waf_cookies:
		return {'success': False, 'error': 'WAF cookies 获取失败，请稍后重试'}

	sem = asyncio.Semaphore(bs.ANYROUTER_CONCURRENCY)
	ratelimited = asyncio.Event()

	def _skipped(name: str) -> dict:
		return {'name': name, 'success': False, 'message': '已跳过：站点正在限流，本轮提前中止', 'skipped': True}

	async def _limited(a):
		if ratelimited.is_set():
			return _skipped(a.name)
		async with sem:
			# 再判一次：排队等信号量的这段时间里，前面的账号可能已经撞上限流
			if ratelimited.is_set():
				return _skipped(a.name)
			r = await bs.renew_one_cookie(a, waf_cookies)
		if r.get('blocked') == 'ratelimit':
			ratelimited.set()
		return r

	results = await asyncio.gather(*[_limited(a) for a in accounts])
	# 写回续期成功的新 session
	updates = {r['name']: r['new_session'] for r in results if r.get('success') and r.get('new_session')}
	bs.save_renewed_sessions(updates)
	# 返回时剔除敏感的 new_session 字段
	clean = [{k: v for k, v in r.items() if k != 'new_session'} for r in results]
	skipped = sum(1 for r in results if r.get('skipped'))
	payload = {
		'success': True,
		'results': clean,
		'summary': {
			'total': len(results),
			'renewed': sum(1 for r in results if r.get('success')),
			'failed': sum(1 for r in results if not r.get('success')),
			'skipped': skipped,
		},
	}
	if ratelimited.is_set():
		payload['notice'] = (
			f'站点限流已触发，本轮中止（跳过 {skipped} 个账号）。这是按出口 IP 的临时封禁，'
			'期间余额查询与签到也会失败。请隔一段时间再点续期 —— 反复重试会让封禁持续更久。'
		)
	return payload


