"""通用 new-api 站点域：站点注册表、统一请求通道（撞防护自动过验）、余额查询、
签到（含打码预检）、签到状态机、站点健康巡检。

过渡期约定（拆分块C，同 server/keys.py）：不做模块级 `import balance_server`，跨实体
引用（可 patch 的查询/求解/巡检通道、NEWAPI_SEED_SITES、site_patrol_fails 等）在
函数体内晚绑定 `bs.<名字>` —— 测试对 bs 命名空间的 monkeypatch 原样生效。
newapi_checkin_states 只做条目级变更，真实住所在本模块；NEWAPI_DEFAULTS 随域迁移
（NewapiSite 字段默认值定义期求值）。站点三态状态的文件路径常量与读写助手留在
balance_server（测试会改写路径），本模块经 bs. 访问。NewapiSite 的
accounts_path/state_path 根目录经 `Path(bs.__file__).parent` 解析——迁移后
`__file__` 指向 server/，直接用会错位。
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from fastapi import APIRouter

sites_router = APIRouter()

NEWAPI_DEFAULTS = {
	'user_info_path': '/api/user/self',
	'sign_in_path': '/api/user/checkin',
	'status_path': '/api/status',
	'api_user_key': 'new-api-user',
	'quota_per_unit': 500000,
	'concurrency': 10,
	'accent': 'orange',
}


class NewapiAccountItem(BaseModel):
	"""通用 new-api 站点的 Access Token 账号（来自各站点自己的 accounts_file）"""

	name: str
	access_token: str
	user_id: str


class NewapiSite(BaseModel):
	"""一个 new-api 同构站点的配置。前端「站点管理」写入 newapi_sites.json，后端据此工作。

	`id` 决定接口路径（/api/site/{id}/...）与数据文件名，创建后不应再改；
	`accounts_file` / `state_file` 允许显式指定，用于兼容 gorouter 的历史文件名。
	"""

	id: str
	label: str
	domain: str
	accent: str = 'orange'
	user_info_path: str = NEWAPI_DEFAULTS['user_info_path']
	sign_in_path: str = NEWAPI_DEFAULTS['sign_in_path']
	status_path: str = NEWAPI_DEFAULTS['status_path']
	api_user_key: str = NEWAPI_DEFAULTS['api_user_key']
	quota_per_unit: int = NEWAPI_DEFAULTS['quota_per_unit']
	concurrency: int = NEWAPI_DEFAULTS['concurrency']
	auto_checkin: bool = True
	accounts_file: str = ''
	state_file: str = ''

	def accounts_path(self) -> Path:
		import balance_server as bs

		return Path(bs.__file__).parent / (self.accounts_file or f'{self.id}_accounts.json')

	def state_path(self) -> Path:
		import balance_server as bs

		return Path(bs.__file__).parent / (self.state_file or f'{self.id}_checkin_state.json')


# 通用 new-api 站点的签到状态：site_id -> 状态字典，结构与 agentrouter/cookie 一致。
# 各站点互不影响，可同时签到；持久化到各站点自己的 state_file。
newapi_checkin_states: dict[str, dict] = {}


def load_newapi_sites() -> list[NewapiSite]:
	import balance_server as bs

	if not bs.NEWAPI_SITES_FILE.exists():
		if bs.NEWAPI_SEED_SITES:
			bs._atomic_write_json(bs.NEWAPI_SITES_FILE, bs.NEWAPI_SEED_SITES, indent=2)
	return bs._read_json_models(bs.NEWAPI_SITES_FILE, NewapiSite, 'SITE')


def save_newapi_sites(sites: list[NewapiSite]):
	import balance_server as bs

	bs._atomic_write_json(bs.NEWAPI_SITES_FILE, [s.model_dump() for s in sites], indent=2)


def get_newapi_site(site_id: str) -> NewapiSite | None:
	"""按 id 取站点配置，找不到返回 None"""
	for s in load_newapi_sites():
		if s.id == site_id:
			return s
	return None


# ========== 站点健康自动巡检 ==========
# 站点三态状态平时只在查询/签到时更新，死了的站点会一直挂在注册表里每天空跑
# （实测出现过 521/522/404 的死站）。巡检调度器低频 GET /api/status（不挂中间件、
# 1 站点 1 请求），连续 SITE_PATROL_FAIL_LIMIT 次不可达就自动暂停该站点的每日签到
# 并推 webhook；恢复可达只更新状态，重新开启签到留给用户决定（站点可能换域名复活）。
SITE_PATROL_INTERVAL = 6 * 3600  # 巡检间隔 6 小时
SITE_PATROL_FIRST_DELAY = 300  # 启动 5 分钟后首巡（避开启动高峰）
SITE_PATROL_FAIL_LIMIT = 3


async def run_site_patrol() -> None:
	"""巡检一轮全部站点，更新三态状态并在持续失联时自动暂停签到"""
	import balance_server as bs

	sites = load_newapi_sites()
	if not sites:
		return
	for s in sites:
		try:
			resp = await bs.newapi_request(s, 'GET', s.status_path, {'User-Agent': bs.USER_AGENT})
			ok = resp.status_code == 200
			if ok:
				try:
					ok = bool((resp.json() or {}).get('data', {}).get('version'))
				except Exception:
					ok = False
		except Exception:
			ok = False

		if ok:
			if bs.site_patrol_fails.get(s.id, 0) >= SITE_PATROL_FAIL_LIMIT:
				bs.add_newapi_checkin_log(s, '巡检：站点恢复可达（每日签到仍为暂停，请手动重新开启）')
			bs.site_patrol_fails[s.id] = 0
			if bs._site_status.get(s.id, {}).get('status') == 'invalid':
				bs._set_site_status(s.id, 'unknown', '')
			continue

		n = bs.site_patrol_fails.get(s.id, 0) + 1
		bs.site_patrol_fails[s.id] = n
		if n >= SITE_PATROL_FAIL_LIMIT and s.auto_checkin:
			current = load_newapi_sites()
			for s2 in current:
				if s2.id == s.id:
					s2.auto_checkin = False
			save_newapi_sites(current)
			msg = f'巡检：连续 {n} 次不可达，已自动暂停每日签到'
			bs.add_newapi_checkin_log(s, msg)
			bs._set_site_status(s.id, 'invalid', f'巡检连续 {n} 次不可达')
			if bs.notify_configured():
				bs._spawn(bs.send_webhook_notify(
					f'⚠️ {s.label} 已失联',
					f'{s.domain} 连续 {n} 次巡检不可达，已自动暂停每日签到。\n站点恢复后请在站点管理重新开启自动签到。',
				))


async def site_patrol_scheduler():
	"""低频巡检调度器：先等首巡延迟，然后每 6 小时一轮"""
	await asyncio.sleep(SITE_PATROL_FIRST_DELAY)
	while True:
		try:
			await run_site_patrol()
		except Exception as e:
			print(f'[PATROL] 巡检异常: {e}')
		await asyncio.sleep(SITE_PATROL_INTERVAL)


def load_newapi_accounts(site: NewapiSite) -> list[NewapiAccountItem]:
	"""从站点自己的 accounts_file 加载账号列表"""
	import balance_server as bs

	return bs._read_json_models(site.accounts_path(), NewapiAccountItem, site.id.upper())


def save_newapi_accounts(site: NewapiSite, accounts: list[NewapiAccountItem]):
	import balance_server as bs

	bs._atomic_write_json(site.accounts_path(), [a.model_dump() for a in accounts], indent=2)


def _newapi_headers(site: NewapiSite, account: NewapiAccountItem) -> dict:
	"""new-api 请求头：Bearer token + New-Api-User，两者缺一即 401"""
	import balance_server as bs

	return {
		'User-Agent': bs.USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Referer': f'{site.domain}/console',
		'Origin': site.domain,
		'Authorization': f'Bearer {account.access_token}',
		site.api_user_key: account.user_id,
		'Cache-Control': 'no-store',
	}


async def newapi_request(site: NewapiSite, method: str, path: str, headers: dict, json_body=None, _auto_bypass: bool = True):
	"""向 new-api 站点发请求。这类站点在 Cloudflare 后，实测无需代理/WAF cookie，仍带 Chrome 指纹更稳。

	Session 按站点分开复用（key 用 site.id），避免不同域名共用连接池。

	撞上 CF 边缘质询或阿里云 WAF 挑战页时，解一次防护 cookies（按域名缓存 5 分钟）后
	原地重打一次；有缓存就直接带上。cf_clearance 绑 UA，重试时用求解方返回的同一个 UA。
	挑战页由防护层返回，请求没到过 new-api 源站，重试对签到是安全的（不会重复签）。
	"""
	import balance_server as bs

	url = site.domain + path
	prot = bs.protection_cache.get(site.domain.rstrip('/'))
	if prot and (prot.get('failed') or prot['expires'] <= time.time()):
		prot = None  # 负缓存/过期条目对请求方不可见；负缓存的拦截图在 ensure 里做
	send_headers = dict(headers)
	if prot and prot.get('user_agent'):
		send_headers['User-Agent'] = prot['user_agent']

	def _do():
		sess = bs._get_cffi_session(f'newapi:{site.id}')
		if prot:
			sess.cookies.update(prot['cookies'])
		return sess.request(method.upper(), url, headers=send_headers, json=json_body)

	loop = asyncio.get_running_loop()
	resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
	if not _auto_bypass:
		return resp
	kind = bs.detect_protection(resp)
	if kind is None:
		return resp
	if prot:
		# 带着缓存的 cookies 仍撞质询：这条缓存已坏（被吊销/过期），作废后强制重解，
		# 否则 ensure 会命中同一条「新鲜但无效」的缓存，拿同样的坏 cookies 白打一次
		bs.protection_cache.pop(site.domain.rstrip('/'), None)
	entry = await bs.ensure_protection_cookies(site, kind)
	if not entry:
		print(f'[{site.id.upper()}] 撞上 {kind} 防护但未能过验（CF 质询需在设置页配置 FlareSolverr）')
		return resp
	print(f'[{site.id.upper()}] 撞上 {kind} 防护，已过验并自动重试')
	return await newapi_request(site, method, path, headers, json_body, _auto_bypass=False)


async def _proxied_newapi_request(site: NewapiSite, method: str, path: str, headers: dict, json_body=None):
	"""经本地 mihomo 出口向站点发一次性请求（不复用 Session 池）。

	newapi_request 是直连的（这类站点平时不需要代理）；只有撞「按出口 IP 限流」的端点
	（取全量 key 的 batch/keys，20 次/20 分钟/IP）才借 mihomo 换出口。这里每次都新建连接，
	不存在 keep-alive 隧道钉死旧出口的问题（agentrouter 轮换踩过的坑）。
	"""
	import balance_server as bs

	from curl_cffi import requests as cffi_requests

	url = site.domain + path
	proxies = {'https': bs._LOCAL_PROXY, 'http': bs._LOCAL_PROXY}

	def _do():
		return cffi_requests.request(
			method.upper(), url, headers=headers, json=json_body,
			proxies=proxies, impersonate='chrome131', timeout=20,
		)

	loop = asyncio.get_running_loop()
	return await loop.run_in_executor(bs._UPSTREAM_POOL, _do)


async def query_balance_newapi(site: NewapiSite, account: NewapiAccountItem) -> dict:
	"""查询单个账号余额（access_token 方式）"""
	import balance_server as bs

	headers = _newapi_headers(site, account)
	unit = site.quota_per_unit or 500000
	max_retries = 3
	for attempt in range(max_retries):
		try:
			resp = await bs.newapi_request(site, 'GET', site.user_info_path, headers)
			if resp.status_code in (401, 403):
				return {'name': account.name, 'success': False, 'error': f'HTTP {resp.status_code}'}
			if resp.status_code != 200:
				if attempt < max_retries - 1:
					await asyncio.sleep(1.5 * (attempt + 1))
					continue
				return {'name': account.name, 'success': False, 'error': f'HTTP {resp.status_code}'}
			data = resp.json().get('data', {}) or {}
			used = round((data.get('used_quota') or 0) / unit, 2)
			quota = round((data.get('quota') or 0) / unit, 2)
			return {'name': account.name, 'success': True, 'used': used, 'quota': quota}
		except Exception as e:
			if attempt < max_retries - 1:
				await asyncio.sleep(1.5 * (attempt + 1))
				continue
			return {'name': account.name, 'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}


async def newapi_turnstile_status(site: NewapiSite) -> dict:
	"""探测站点当前是否开着 Turnstile 人机校验，带 5 分钟缓存。

	`GET /api/status` 会返回 `data.turnstile_check` 与 `data.turnstile_site_key`。
	站长若哪天关掉 Turnstile，这里会自动变成 enabled=False，服务器端签到随即恢复可用，
	前端也就不再需要走「浏览器脚本 + 同步」那条路 —— 无需改代码。

	sitekey 一律从这里读，不要硬编码：它有域名限制，各站点各不相同。
	探测失败时保守假定 enabled=True（宁可提示用户手动签，也别让自动签到静默失败）。
	"""
	import balance_server as bs

	cache_key = f'turnstile:{site.id}'
	cached = bs.waf_cache.get(cache_key)
	if cached and cached['expires'] > time.time():
		return cached['value']

	value = {'enabled': True, 'site_key': '', 'probed': False}
	try:
		resp = await bs.newapi_request(site, 'GET', site.status_path, {'User-Agent': bs.USER_AGENT})
		if resp.status_code == 200:
			data = (resp.json() or {}).get('data', {}) or {}
			value = {
				'enabled': bool(data.get('turnstile_check')),
				'site_key': data.get('turnstile_site_key') or '',
				'probed': True,
			}
	except Exception as e:
		print(f'[{site.id.upper()}] Turnstile 状态探测失败，保守假定已开启: {e}')

	bs.waf_cache[cache_key] = {'value': value, 'expires': time.time() + bs.WAF_CACHE_TTL}
	return value


async def sign_in_newapi(site: NewapiSite, account: NewapiAccountItem, turnstile_token: str | None = None) -> dict:
	"""为单个账号签到（POST /api/user/checkin，奖励区间由站点配置决定）。

	今日已签时接口返回 200 且 success=false、message='今日已签到'，据此区分 already。

	turnstile_token 传入时签到 URL 带上 ?turnstile=<token> —— new-api 的 TurnstileCheck
	中间件只认 query 参数，与前端浏览器脚本的传法一致。

	站点开着 Turnstile 且没传 token 时，接口会返回「Turnstile token 为空」——
	此时把 `turnstile_blocked` 标出来，让调用方知道这不是账号问题，而是需要先过人机校验。
	"""
	import balance_server as bs

	from urllib.parse import quote

	headers = _newapi_headers(site, account)
	path = site.sign_in_path + (f'?turnstile={quote(turnstile_token, safe="")}' if turnstile_token else '')
	max_retries = 3
	for attempt in range(max_retries):
		try:
			resp = await bs.newapi_request(site, 'POST', path, headers)
			if resp.status_code == 200:
				data = resp.json()
				msg = data.get('message', '')
				if data.get('success'):
					return {'name': account.name, 'success': True, 'message': msg or '签到成功', 'already_signed': False}
				if '已签' in msg:
					return {'name': account.name, 'success': True, 'message': msg, 'already_signed': True}
				if 'Turnstile' in msg:
					return {'name': account.name, 'success': False, 'message': msg, 'turnstile_blocked': True}
				return {'name': account.name, 'success': False, 'message': msg or '签到失败'}
			return {'name': account.name, 'success': False, 'message': f'HTTP {resp.status_code}'}
		except Exception as e:
			if attempt < max_retries - 1:
				await asyncio.sleep(1.5 * (attempt + 1))
				continue
			return {'name': account.name, 'success': False, 'message': f'{type(e).__name__}: {e}'[:100]}


async def newapi_checkin_info(site: NewapiSite, account: NewapiAccountItem) -> dict:
	"""读取单个账号的签到状态（GET /api/user/checkin），不触发签到"""
	import balance_server as bs

	unit = site.quota_per_unit or 500000
	try:
		resp = await bs.newapi_request(site, 'GET', site.sign_in_path, _newapi_headers(site, account))
		if resp.status_code != 200:
			return {'name': account.name, 'success': False, 'error': f'HTTP {resp.status_code}'}
		data = resp.json()
		if not data.get('success'):
			return {'name': account.name, 'success': False, 'error': data.get('message', 'Unknown')}
		d = data.get('data', {})
		stats = d.get('stats', {}) or {}
		return {
			'name': account.name,
			'success': True,
			'enabled': d.get('enabled'),
			'min_reward': round((d.get('min_quota') or 0) / unit, 2),
			'max_reward': round((d.get('max_quota') or 0) / unit, 2),
			'checked_in_today': stats.get('checked_in_today'),
			'total_checkins': stats.get('total_checkins'),
			'total_reward': round((stats.get('total_quota') or 0) / unit, 2),
		}
	except Exception as e:
		return {'name': account.name, 'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}


def newapi_state(site: NewapiSite) -> dict:
	"""取（或初始化）某站点的签到状态"""
	import balance_server as bs

	st = newapi_checkin_states.get(site.id)
	if st is None:
		st = bs._blank_checkin_state()
		newapi_checkin_states[site.id] = st
	return st


def add_newapi_checkin_log(site: NewapiSite, msg: str):
	import balance_server as bs

	bs._checkin_add_log(newapi_state(site), site.id.upper(), msg)


def save_newapi_checkin_state(site: NewapiSite):
	"""持久化签到状态"""
	import balance_server as bs

	bs._checkin_save(newapi_state(site), site.state_path(), site.id.upper())


def load_newapi_checkin_state(site: NewapiSite):
	"""服务启动时从文件恢复签到状态（仅用于前端展示历史进度）"""
	import balance_server as bs

	bs._checkin_load(newapi_state(site), site.state_path(), site.id.upper())


async def run_newapi_checkin(site: NewapiSite, trigger: str = 'manual'):
	"""执行某站点的签到：token 账号并发签到（Semaphore 取站点 concurrency），数秒内完成。

	签到成功后顺便查一次余额写入今日快照，省去额外的查询请求。

	站点开着 Turnstile 时分两种情况：配置了打码平台 → 先用免费的 GET 预检剔除今日已签
	账号（token 按次计费，别替已签的白解），再逐账号求解并签到（全自动）；没配置 → 先
	探测一次并直接结束，把原因写进日志。站长关掉 Turnstile 后无需改代码，此路径自动恢复。
	"""
	import balance_server as bs

	accounts = load_newapi_accounts(site)
	st = newapi_state(site)
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
	add_newapi_checkin_log(site, f'开始 {site.label} 签到（{trigger}），共 {len(accounts)} 个 token 账号')
	save_newapi_checkin_state(site)

	def _finish():
		st['signed'] = sum(1 for v in st['accounts'].values() if v['status'] == 'signed')
		st['already'] = sum(1 for v in st['accounts'].values() if v['status'] == 'already')
		st['failed'] = sum(1 for v in st['accounts'].values() if v['status'] == 'failed')
		st['running'] = False
		st['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		add_newapi_checkin_log(
			site, f'{site.label} 签到结束：成功 {st["signed"]} · 今日已签 {st["already"]} · 失败 {st["failed"]}'
		)
		save_newapi_checkin_state(site)
		# 失败推 webhook（默认关，设置页可开）——失败只在日志里等用户翻页发现不了
		if st['failed'] > 0 and bs.notify_configured() and bs.get_notify_config()['on_checkin_failed']:
			bad = [f'{name}：{v["message"][:60]}' for name, v in st['accounts'].items() if v['status'] == 'failed']

			async def _push_failure():
				r = await bs.send_webhook_notify(f'❌ {site.label} 签到失败 {st["failed"]} 个', '\n'.join(bad))
				if not r.get('sent'):
					add_newapi_checkin_log(site, f'失败通知推送未发送：{r.get("error", "")}')

			bs._spawn(_push_failure())

	if not accounts:
		add_newapi_checkin_log(site, f'没有 {site.label} 账号，签到结束')
		_finish()
		return

	ts_state = await bs.newapi_turnstile_status(site)
	ts_enabled = bool(ts_state['enabled'])
	ts_site_key = ts_state['site_key'] or ''
	if ts_enabled:
		solver_cfg = bs.get_turnstile_solver_config()
		if not solver_cfg['api_key']:
			ts_msg = '站点已开启 Turnstile 人机校验且未配置打码平台，服务器端无法签到；请在设置页配置打码平台，或用 Web UI 的浏览器脚本签到'
			now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
			for name in st['accounts']:
				st['accounts'][name] = {'status': 'failed', 'message': ts_msg, 'time': now_str}
			add_newapi_checkin_log(site, ts_msg)
			_finish()
			return
		if not ts_site_key:
			# 探测失败时 status 保守返回 enabled=True 但没有 sitekey，没东西可解，只能走浏览器
			ts_msg = '站点 Turnstile 状态探测失败拿不到 sitekey，无法服务器端求解；请稍后重试或用浏览器脚本签到'
			now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
			for name in st['accounts']:
				st['accounts'][name] = {'status': 'failed', 'message': ts_msg, 'time': now_str}
			add_newapi_checkin_log(site, ts_msg)
			_finish()
			return
		add_newapi_checkin_log(
			site, f"站点开启 Turnstile，改用打码平台（{solver_cfg['provider'] or '自定义网关'}）逐账号求解 token"
		)
		page_url = site.domain.rstrip('/') + '/login'

	sem = asyncio.Semaphore(site.concurrency or bs.NEWAPI_CONCURRENCY)
	if ts_enabled:
		# Turnstile token 按次计费：先用不挂中间件的 GET 状态把今日已签的剔掉，
		# 别替已签账号白解 token（与前端浏览器脚本的「先 sync 再生成」同一思路）。
		# 查询失败的账号按未签处理，宁可多花一次打码也不漏签。
		async def _preflight(acc: NewapiAccountItem):
			async with sem:
				return acc, await bs.newapi_checkin_info(site, acc)

		pending: list[NewapiAccountItem] = []
		for acc, info in await asyncio.gather(*[_preflight(a) for a in accounts]):
			if info.get('success') and info.get('checked_in_today'):
				ts_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
				st['accounts'][acc.name] = {'status': 'already', 'message': '今日已签（打码预检跳过）', 'time': ts_now}
			else:
				pending.append(acc)
		skipped = len(accounts) - len(pending)
		if skipped:
			add_newapi_checkin_log(site, f'打码预检：{skipped} 个账号今日已签，跳过求解')
		if not pending:
			_finish()
			return

	async def _one(acc: NewapiAccountItem):
		async with sem:
			if not st['running']:
				return
			ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
			token = None
			if ts_enabled:
				solved = await bs.solve_turnstile_token(ts_site_key, page_url)
				if not solved.get('success'):
					msg = f"过验失败: {solved.get('error', '未知错误')}"
					st['accounts'][acc.name] = {'status': 'failed', 'message': msg, 'time': ts}
					add_newapi_checkin_log(site, f'{acc.name}: {msg}')
					save_newapi_checkin_state(site)
					return
				token = solved['token']
			result = await bs.sign_in_newapi(site, acc, turnstile_token=token)
			if result.get('success'):
				status = 'already' if result.get('already_signed') else 'signed'
				st['accounts'][acc.name] = {'status': status, 'message': result.get('message', '签到成功'), 'time': ts}
				bal = await bs.query_balance_newapi(site, acc)
				if bal.get('success'):
					bs.record_account_usage(site.id, acc.name, bal['used'], bal['quota'])
			else:
				st['accounts'][acc.name] = {'status': 'failed', 'message': result.get('message', '签到失败'), 'time': ts}
			add_newapi_checkin_log(site, f'{acc.name}: {st["accounts"][acc.name]["message"]}')
			save_newapi_checkin_state(site)

	await asyncio.gather(*[_one(a) for a in (pending if ts_enabled else accounts)])
	_finish()


def start_newapi_checkin(site: NewapiSite, trigger: str = 'manual') -> bool:
	"""启动某站点的签到任务，若已在运行则返回 False"""
	st = newapi_state(site)
	if st['running']:
		return False
	import balance_server as bs

	st['task'] = bs.asyncio.create_task(run_newapi_checkin(site, trigger))
	return True


def _newapi_checkin_status_payload(site: NewapiSite) -> dict:
	"""组装签到状态返回体"""
	st = newapi_state(site)
	accounts = [
		{'name': name, 'status': info.get('status', 'pending'), 'message': info.get('message', ''), 'time': info.get('time')}
		for name, info in st['accounts'].items()
	]
	signed = sum(1 for a in accounts if a['status'] in ('signed', 'already'))
	failed = sum(1 for a in accounts if a['status'] == 'failed')
	return {
		'site_id': site.id,
		'running': st['running'],
		'date': st['date'],
		'trigger': st['trigger'],
		'started_at': st['started_at'],
		'finished_at': st['finished_at'],
		'total': st['total'],
		'done': signed + failed,
		'signed': signed,
		'failed': failed,
		'accounts': accounts,
		'logs': st['logs'][-30:],
	}


# ===== 端点（块E 自 balance_server.py 迁入，晚绑定 bs.<名字>）=====

def _site_or_error(site_id: str):
	import balance_server as bs
	"""取站点配置，找不到时返回统一的错误体"""
	site = bs.get_newapi_site(site_id)
	if site is None:
		return None, {'success': False, 'error': f'站点 {site_id} 不存在'}
	return site, None



@sites_router.post('/api/sites')
async def save_sites(req: dict):
	import balance_server as bs
	"""保存站点清单。前端「站点管理」用它增删改站点，后端无需改代码即可支持新站点。

	删除站点时保留其账号文件与签到状态文件，避免误删后账号丢失（重新添加同 id 即可恢复）。
	"""
	try:
		raw = req.get('sites', [])
		validated = [bs.NewapiSite(**s) for s in raw]
		ids = [s.id for s in validated]
		if len(set(ids)) != len(ids):
			return {'success': False, 'error': '站点 id 重复'}
		for s in validated:
			if not s.id.replace('_', '').replace('-', '').isalnum():
				return {'success': False, 'error': f'站点 id 只能用字母数字与 -_：{s.id}'}
			if not s.domain.startswith('http'):
				return {'success': False, 'error': f'域名要带 http(s)://：{s.domain}'}
			s.domain = s.domain.rstrip('/')
		bs.save_newapi_sites(validated)
		return {'success': True, 'sites': [s.model_dump() for s in validated]}
	except Exception as e:
		return {'success': False, 'error': str(e)}



@sites_router.post('/api/sites/probe')
async def probe_site(req: dict):
	import balance_server as bs
	"""探测一个域名是否是 new-api 站点，供前端在添加前确认域名填对了。

	读 `GET /api/status`：能返回 `data.version` 就是 new-api，顺带把站点名、签到是否开启、
	Turnstile 状态、quota 换算单位一并回给前端做默认值。此接口不写任何文件。
	"""
	domain = (req.get('domain') or '').strip().rstrip('/')
	if not domain.startswith('http'):
		return {'success': False, 'error': '域名要带 http(s)://'}
	probe = bs.NewapiSite(id='__probe__', label='probe', domain=domain)
	try:
		resp = await bs.newapi_request(probe, 'GET', probe.status_path, {'User-Agent': bs.USER_AGENT})
	except Exception as e:
		return {'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}
	if resp.status_code != 200:
		return {'success': False, 'error': f'HTTP {resp.status_code}'}
	try:
		data = (resp.json() or {}).get('data', {}) or {}
	except Exception:
		return {'success': False, 'error': '返回的不是 JSON，可能不是 new-api 站点'}
	if not data.get('version'):
		return {'success': False, 'error': '未识别为 new-api 站点（/api/status 里没有 version）'}
	page = await bs.probe_page_protection(domain)
	return {
		'success': True,
		'info': {
			'version': data.get('version'),
			'system_name': data.get('system_name') or '',
			'checkin_enabled': bool(data.get('checkin_enabled')),
			'turnstile_check': bool(data.get('turnstile_check')),
			'quota_per_unit': data.get('quota_per_unit') or bs.NEWAPI_DEFAULTS['quota_per_unit'],
			'protections': {
				'cf_challenge': bool(page.get('cf_challenge')),
				'aliyun_waf': bool(page.get('aliyun_waf')),
			},
		},
	}



@sites_router.get('/api/site/{site_id}/accounts')
async def get_site_accounts(site_id: str):
	import balance_server as bs
	"""获取某站点的账号列表"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	return {'success': True, 'accounts': [a.model_dump() for a in bs.load_newapi_accounts(site)]}



@sites_router.post('/api/site/{site_id}/accounts')
async def post_site_accounts(site_id: str, req: dict):
	import balance_server as bs
	"""保存某站点的账号列表"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	try:
		validated = [bs.NewapiAccountItem(**acc) for acc in req.get('accounts', [])]
		bs.save_newapi_accounts(site, validated)
		return {'success': True}
	except Exception as e:
		return {'success': False, 'error': str(e)}



@sites_router.post('/api/site/{site_id}/query')
async def query_site(site_id: str):
	import balance_server as bs
	"""批量查询某站点账号余额（并发，无 WAF/代理依赖）"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	accounts = bs.load_newapi_accounts(site)
	if not accounts:
		return {'success': False, 'error': f'没有 {site.label} 账号'}

	sem = asyncio.Semaphore(site.concurrency or NEWAPI_CONCURRENCY)

	async def _limited(a):
		async with sem:
			return await bs.query_balance_newapi(site, a)

	results = await asyncio.gather(*[_limited(a) for a in accounts])
	ok = [r for r in results if r.get('success')]
	if ok:
		bs._set_site_status(site.id, 'ok', '')
	else:
		errors = '; '.join(str(r.get('error', ''))[:60] for r in results if not r.get('success'))
		bs._set_site_status(site.id, 'invalid', errors[:200])
	return {
		'success': True,
		'results': results,
		'summary': {
			'total_quota': round(sum(r['quota'] for r in ok), 2),
			'total_used': round(sum(r['used'] for r in ok), 2),
			'account_count': len(results),
			'success_count': len(ok),
		},
	}



@sites_router.post('/api/site/{site_id}/checkin/start')
async def site_checkin_start(site_id: str):
	import balance_server as bs
	"""启动某站点的账号签到（并发，数秒完成）"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	st = bs.newapi_state(site)
	if st['running']:
		return {'success': False, 'error': f'{site.label} 签到已在运行中', 'status': bs._newapi_checkin_status_payload(site)}
	accounts = bs.load_newapi_accounts(site)
	if not accounts:
		return {'success': False, 'error': f'没有 {site.label} 账号可签到'}
	bs.start_newapi_checkin(site, trigger='manual')
	await asyncio.sleep(0.2)
	return {
		'success': True,
		'message': f'{site.label} 签到已启动，共 {len(accounts)} 个账号',
		'status': bs._newapi_checkin_status_payload(site),
	}



@sites_router.get('/api/site/{site_id}/turnstile')
async def site_turnstile(site_id: str):
	import balance_server as bs
	"""返回某站点当前的 Turnstile 状态，供前端决定签到走哪条路。

	enabled=True  → 服务器端要带 token 才签得了：配置了打码平台就自动求解，
	                没配置则前端展示浏览器脚本 + 同步流程
	enabled=False → 站长关掉了校验，前端直接走 /checkin/start 一键签到
	"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	return {'success': True, 'turnstile': await bs.newapi_turnstile_status(site)}



@sites_router.get('/api/site/{site_id}/checkin/status')
async def site_checkin_status(site_id: str):
	import balance_server as bs
	"""获取某站点的签到进度状态"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	return {'success': True, 'status': bs._newapi_checkin_status_payload(site)}



@sites_router.post('/api/site/{site_id}/checkin/sync')
async def site_checkin_sync(site_id: str):
	import balance_server as bs
	"""在浏览器脚本签完之后，从站点核对每个账号的真实签到状态。

	`GET /api/user/checkin` 没有挂 Turnstile 中间件（只有 POST 挂了，见 new-api 的
	router/api-router.go），所以服务器随时能读到 `stats.checked_in_today`。用它核对，
	就不需要浏览器脚本把结果回传 —— 脚本跑在站点的 HTTPS 页面上，受混合内容策略
	限制本来也无法 fetch 回 HTTP 的本服务。

	顺便查一次余额写入今日快照，与 bs.run_newapi_checkin 的口径保持一致。
	"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	accounts = bs.load_newapi_accounts(site)
	if not accounts:
		return {'success': False, 'error': f'没有 {site.label} 账号'}

	sem = asyncio.Semaphore(site.concurrency or NEWAPI_CONCURRENCY)
	results: dict = {}

	async def _one(acc: bs.NewapiAccountItem):
		async with sem:
			info = await bs.newapi_checkin_info(site, acc)
			bal = await bs.query_balance_newapi(site, acc)
		if bal.get('success'):
			bs.record_account_usage(site.id, acc.name, bal['used'], bal['quota'])
		if not info.get('success'):
			results[acc.name] = {'name': acc.name, 'success': False, 'message': info.get('error', '状态查询失败')}
			return
		checked = bool(info.get('checked_in_today'))
		results[acc.name] = {
			'name': acc.name,
			'success': checked,
			'message': '今日已签到' if checked else '今日未签到',
			'already_signed': checked,
			'total_checkins': info.get('total_checkins'),
			'quota': bal.get('quota') if bal.get('success') else None,
			'used': bal.get('used') if bal.get('success') else None,
		}

	await asyncio.gather(*[_one(a) for a in accounts])

	ordered = [results[a.name] for a in accounts if a.name in results]
	now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	st = bs.newapi_state(site)
	st['running'] = False
	st['date'] = datetime.now().strftime('%Y-%m-%d')
	st['trigger'] = 'browser'
	st['started_at'] = st.get('started_at') or now_str
	st['finished_at'] = now_str
	st['total'] = len(ordered)
	st['accounts'] = {}
	st['logs'] = []
	for r in ordered:
		st['accounts'][r['name']] = {
			'status': 'already' if r['success'] else 'failed',
			'message': r['message'],
			'time': now_str,
		}
		add_newapi_checkin_log(site, f'{r["name"]}: {r["message"]}')
	signed = sum(1 for r in ordered if r['success'])
	st['signed'] = 0
	st['already'] = signed
	st['failed'] = len(ordered) - signed
	add_newapi_checkin_log(site, f'状态同步完成：已签到 {signed} / {len(ordered)}')
	save_newapi_checkin_state(site)

	return {
		'success': True,
		'results': ordered,
		'checked_in': signed,
		'total': len(ordered),
		'status': bs._newapi_checkin_status_payload(site),
	}



@sites_router.get('/api/site/{site_id}/checkin/info')
async def site_checkin_info_all(site_id: str):
	import balance_server as bs
	"""读取某站点各账号的签到状态与奖励区间（不触发签到）"""
	site, err = bs._site_or_error(site_id)
	if err:
		return err
	accounts = bs.load_newapi_accounts(site)
	if not accounts:
		return {'success': False, 'error': f'没有 {site.label} 账号'}

	sem = asyncio.Semaphore(site.concurrency or NEWAPI_CONCURRENCY)

	async def _limited(a):
		async with sem:
			return await bs.newapi_checkin_info(site, a)

	results = await asyncio.gather(*[_limited(a) for a in accounts])
	return {'success': True, 'accounts': results}


