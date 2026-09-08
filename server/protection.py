"""通用站点防护层：CF 边缘质询 / 阿里云 WAF 的检测、求解与 cookies 缓存。

过渡期约定（拆分块B，同 server/keys.py）：本模块不做模块级 `import balance_server`，
跨实体引用（CONFIG_FILE、USER_AGENT、_get_cffi_session 等）在函数体内晚绑定
`bs.<名字>` —— 测试对 bs 命名空间的 monkeypatch 原样生效。protection_cache /
_protection_locks 只做条目级变更（从不整体重绑），真实住所在本模块、bs 重导出同一对象。
"""

import asyncio
import re
import time

from fastapi import APIRouter

protection_router = APIRouter()

# 阿里云挑战页会把待求解的参数写成 var arg1='...'；ESA 拦截页会写明命中的规则名
_WAF_CHALLENGE_RE = re.compile(r"arg1='([0-9A-Fa-f]+)'")
_ESA_DENY_RE = re.compile(r'Denied by (\w+)')

_CF_CHALLENGE_BODY_RE = re.compile(r'Just a moment|challenge-platform|_cf_chl|cf-chl|Checking your browser', re.I)
_ALIYUN_WAF_COOKIE_NAMES = ('acw_tc', 'cdn_sec_tc', 'acw_sc__v2')

# 防护 cookies 缓存：{domain: {'cookies': dict, 'user_agent': str|None, 'expires': float}}，TTL 沿用 WAF_CACHE_TTL
protection_cache: dict = {}
_protection_locks: dict[str, asyncio.Lock] = {}

# 阿里云 WAF acw_sc__v2 挑战求解常量（从挑战页混淆脚本反混淆得到，长期稳定）
_WAF_POS = [
	0xF, 0x23, 0x1D, 0x18, 0x21, 0x10, 0x1, 0x26, 0xA, 0x9, 0x13, 0x1F, 0x28, 0x1B, 0x16, 0x17, 0x19, 0xD,
	0x6, 0xB, 0x27, 0x12, 0x14, 0x8, 0xE, 0x15, 0x20, 0x1A, 0x2, 0x1E, 0x7, 0x4, 0x11, 0x5, 0x3, 0x1C, 0x22,
	0x25, 0xC, 0x24,
]
_WAF_MASK = '3000176000856006061501533003690027800375'


def _solve_acw_sc_v2(arg1: str) -> str:
	"""根据挑战页的 arg1 计算阿里云 WAF 的 acw_sc__v2 cookie。

	等价于挑战页混淆脚本：先按 q[i]=arg1[pos[i]-1] 重排，再与 mask 逐字节十六进制异或。
	"""
	q = ''.join(arg1[_WAF_POS[i] - 1] for i in range(len(_WAF_POS)))
	v = ''
	for i in range(0, min(len(q), len(_WAF_MASK)), 2):
		v += format(int(q[i : i + 2], 16) ^ int(_WAF_MASK[i : i + 2], 16), '02x')
	return v


def get_flaresolverr_url() -> str:
	"""FlareSolverr 地址（saved_config.json 的 turnstile_solver.flaresolverr_url），未配置返回空串。"""
	import balance_server as bs

	try:
		cfg = bs._read_json_cached(bs.CONFIG_FILE)
	except Exception:
		cfg = {}
	s = cfg.get('turnstile_solver') if isinstance(cfg, dict) else None
	return ((s or {}).get('flaresolverr_url') or '').strip().rstrip('/')


def detect_protection(resp) -> str | None:
	"""识别响应背后的防护层：'cf_challenge' / 'aliyun_waf'；正常返回 None。

	CF 以官方 cf-mitigated 响应头为准，正文特征兜底（质询页变体多，头最可靠）；
	阿里云 WAF 挑战页在 200 里也可能出现（校验中转页），认 arg1 特征。
	"""
	try:
		headers = resp.headers or {}
		body = resp.text or ''
	except Exception:
		return None
	if str(headers.get('cf-mitigated', '')).lower() == 'challenge':
		return 'cf_challenge'
	if resp.status_code in (403, 503) and _CF_CHALLENGE_BODY_RE.search(body):
		return 'cf_challenge'
	if _WAF_CHALLENGE_RE.search(body):
		return 'aliyun_waf'
	return None


async def solve_aliyun_waf(domain: str) -> dict | None:
	"""过任意域名的阿里云 WAF 挑战：GET 首页 → 提 arg1 → 算 acw_sc__v2。失败返回 None。

	独立 Session 收挑战页下发的 acw_tc/cdn_sec_tc（acw_sc__v2 与它们配对校验），
	直连不走代理——这类站点平时不需要代理。
	"""
	import balance_server as bs

	def _do():
		from curl_cffi import requests as cffi_requests

		sess = cffi_requests.Session(impersonate='chrome131', timeout=30)
		resp = sess.get(domain + '/', headers={'User-Agent': bs.USER_AGENT})
		m = _WAF_CHALLENGE_RE.search(resp.text or '')
		if not m:
			return None
		cookies = {}
		for name in _ALIYUN_WAF_COOKIE_NAMES:
			val = sess.cookies.get(name)
			if val:
				cookies[name] = val
		cookies['acw_sc__v2'] = _solve_acw_sc_v2(m.group(1))
		return {'cookies': cookies, 'user_agent': None}

	loop = asyncio.get_running_loop()
	try:
		return await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
	except Exception as e:
		print(f'[PROTECT] {domain} 阿里云 WAF 求解失败: {e}')
		return None


async def solve_cf_challenge(domain: str) -> dict | None:
	"""用 FlareSolverr 过 Cloudflare 边缘质询，返回 {cookies, user_agent}。

	未配置 FlareSolverr 时直接返回 None——保持旧行为：撞质询就让调用方拿到 403/503。
	"""
	import balance_server as bs

	base = get_flaresolverr_url()
	if not base:
		return None

	def _do():
		sess = bs._get_cffi_session(f'flaresolverr:{base}')
		return sess.post(base + '/v1', json={'cmd': 'request.get', 'url': domain + '/', 'maxTimeout': 60000}, timeout=70)

	loop = asyncio.get_running_loop()
	try:
		resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
		data = resp.json()
		if data.get('status') != 'ok':
			print(f'[PROTECT] {domain} FlareSolverr 求解失败: {str(data.get("message"))[:120]}')
			return None
		solution = data.get('solution') or {}
		cookies = {c.get('name'): c.get('value') for c in solution.get('cookies', []) if c.get('name')}
		if not cookies:
			return None
		return {'cookies': cookies, 'user_agent': solution.get('userAgent') or None}
	except Exception as e:
		print(f'[PROTECT] {domain} FlareSolverr 调用失败: {e}')
		return None


async def ensure_protection_cookies(site, kind: str) -> dict | None:
	"""确保某站点的防护 cookies 就绪（成功缓存 5 分钟，失败负缓存 60 秒），失败返回 None。

	singleflight 很重要：签到是 10 并发，缓存过期瞬间不能让每个账号各打一次
	FlareSolverr/WAF 挑战页（前者按次耗时几十秒，后者可能撞 IP 限流）。
	失败也记一段短负缓存：FlareSolverr 挂掉时一批 10 个账号不至各自等满 70 秒超时，
	60 秒后自动允许重试，不影响「服务恢复即恢复签到」。
	"""
	import balance_server as bs

	domain = site.domain.rstrip('/')
	cached = protection_cache.get(domain)
	if cached and cached.get('failed'):
		# 负缓存：近期刚失败过，别再去撞求解器
		if cached['expires'] > time.time():
			return None
		protection_cache.pop(domain, None)
		cached = None
	if cached and cached['expires'] > time.time():
		return cached
	lock = _protection_locks.setdefault(domain, asyncio.Lock())
	async with lock:
		cached = protection_cache.get(domain)
		if cached and cached.get('failed') and cached['expires'] > time.time():
			return None
		if cached and not cached.get('failed') and cached['expires'] > time.time():
			return cached
		if kind == 'aliyun_waf':
			solved = await bs.solve_aliyun_waf(domain)
		elif kind == 'cf_challenge':
			solved = await bs.solve_cf_challenge(domain)
		else:
			solved = None
		if not solved:
			protection_cache[domain] = {'failed': True, 'expires': time.time() + 60}
			return None
		entry = {**solved, 'expires': time.time() + bs.WAF_CACHE_TTL}
		protection_cache[domain] = entry
		return entry


async def probe_page_protection(domain: str) -> dict:
	"""裸探测站点首页的防护层（不走 newapi_request 的自动过验），供添加站点时分类展示。"""
	import balance_server as bs

	def _do():
		from curl_cffi import requests as cffi_requests

		sess = cffi_requests.Session(impersonate='chrome131', timeout=30)
		return sess.get(domain + '/', headers={'User-Agent': bs.USER_AGENT})

	loop = asyncio.get_running_loop()
	try:
		resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do)
		kind = detect_protection(resp)
		return {
			'http_status': resp.status_code,
			'cf_challenge': kind == 'cf_challenge',
			'aliyun_waf': kind == 'aliyun_waf',
		}
	except Exception as e:
		return {'http_status': None, 'cf_challenge': False, 'aliyun_waf': False, 'error': str(e)[:100]}


@protection_router.post('/api/protection/test')
async def protection_test(site_id: str = ''):
	"""探测某站点挂了哪些防护层，并现场验证突破手段是否可用。

	首页裸探测只反映「当前这次响应」的防护（部分站点仅对 API 或按负载触发质询），
	结果是最保守的分类；运行期 newapi_request 撞到防护都会自动过验重试。
	solved 里 None 表示撞到了但没配求解器（CF 质询需 FlareSolverr）。
	"""
	import balance_server as bs

	sites = bs.load_newapi_sites()
	if site_id:
		site = next((s for s in sites if s.id == site_id), None)
		if site is None:
			return {'success': False, 'error': f'未知站点: {site_id}'}
	else:
		if not sites:
			return {'success': False, 'error': '没有站点可探测'}
		site = sites[0]
	domain = site.domain.rstrip('/')

	page = await bs.probe_page_protection(domain)
	ts = await bs.newapi_turnstile_status(site)
	protections = {
		'cf_challenge': bool(page.get('cf_challenge')),
		'aliyun_waf': bool(page.get('aliyun_waf')),
		'turnstile': bool(ts['enabled']),
	}
	solved: dict = {}
	if protections['aliyun_waf']:
		solved['aliyun_waf'] = bool(await bs.solve_aliyun_waf(domain))
	if protections['cf_challenge']:
		if bs.get_flaresolverr_url():
			solved['cf_challenge'] = bool(await bs.solve_cf_challenge(domain))
		else:
			solved['cf_challenge'] = None
	return {
		'success': True,
		'site': site.label,
		'domain': site.domain,
		'page_http_status': page.get('http_status'),
		'page_error': page.get('error'),
		'protections': protections,
		'solved': solved,
		'flaresolverr_configured': bool(get_flaresolverr_url()),
	}
