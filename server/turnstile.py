"""Turnstile 打码平台（服务器端过 CF 人机校验）：createTask 协议代解、按天用量计数、
平台余额查询、配置读写端点。

三家主流平台（2Captcha / YesCaptcha / CapSolver）都兼容 createTask + getTaskResult
协议，差异只有域名和 task.type；自定义网关按 2Captcha 协议猜。

过渡期约定（拆分块B，同 server/protection.py）：不做模块级 `import balance_server`，
跨实体引用（CONFIG_FILE、_get_cffi_session、可 patch 的 PRESETS/POLL_INTERVAL）在
函数体内晚绑定 `bs.<名字>`。_TURNSTILE_SOLVER_SEM 从不重绑，真实住所在本模块。
"""

import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

turnstile_router = APIRouter()

TURNSTILE_SOLVER_PRESETS = {
	'2captcha': {'base_url': 'https://api.2captcha.com', 'task_type': 'TurnstileTaskProxyless'},
	'yescaptcha': {'base_url': 'https://api.yescaptcha.com', 'task_type': 'TurnstileTaskProxyless'},
	'capsolver': {'base_url': 'https://api.capsolver.com', 'task_type': 'AntiTurnstileTaskProxyLess'},
}
# 打码按次计费且平台侧有限速，求解并发固定 3，不跟站点签到并发（10）走
_TURNSTILE_SOLVER_SEM = asyncio.Semaphore(3)
TURNSTILE_SOLVER_POLL_INTERVAL = 3  # 秒
TURNSTILE_SOLVER_TIMEOUT = 150  # 单个 token 求解上限


class TurnstileSolverRequest(BaseModel):
	"""打码平台 / FlareSolverr 配置。api_key 留空表示保留已保存的值（前端不回显密钥）。"""

	provider: str
	api_key: str = ''
	base_url: str = ''
	flaresolverr_url: str = ''


def get_turnstile_solver_config() -> dict:
	"""读打码平台配置（saved_config.json 的 turnstile_solver 段），缺省即未配置。"""
	import balance_server as bs

	try:
		cfg = bs._read_json_cached(bs.CONFIG_FILE)
	except Exception:
		cfg = {}
	s = cfg.get('turnstile_solver') if isinstance(cfg, dict) else None
	s = s if isinstance(s, dict) else {}
	provider = (s.get('provider') or '').strip().lower()
	api_key = (s.get('api_key') or '').strip()
	base_url = (s.get('base_url') or '').strip().rstrip('/')
	preset = TURNSTILE_SOLVER_PRESETS.get(provider)
	return {
		'provider': provider,
		'api_key': api_key,
		'base_url': base_url or (preset['base_url'] if preset else ''),
		# 自定义网关没指 task.type 时按 2Captcha 协议猜——绝大多数兼容网关都认这个名字
		'task_type': preset['task_type'] if preset else ('TurnstileTaskProxyless' if base_url else ''),
	}


async def solve_turnstile_token(site_key: str, page_url: str, timeout: int = TURNSTILE_SOLVER_TIMEOUT) -> dict:
	"""调打码平台解一个 Turnstile token，成功返回 {'success': True, 'token': ...}。

	对外入口：先做不触网的预检（未配置/缺 sitekey 不计费用统计），再交给 _raw 实际
	求解并按结果更新当日用量计数（solved / failed，落盘跨重启）。
	"""
	cfg = get_turnstile_solver_config()
	if not (cfg['api_key'] and cfg['base_url'] and cfg['task_type']):
		return {'success': False, 'error': '打码平台未配置（设置页选择平台并填 api_key）'}
	if not site_key:
		return {'success': False, 'error': '缺少 sitekey（站点状态探测失败）'}
	r = await _solve_turnstile_token_raw(cfg, site_key, page_url, timeout)
	_solver_stats_bump(bool(r.get('success')))
	return r


async def _solve_turnstile_token_raw(cfg: dict, site_key: str, page_url: str, timeout: int) -> dict:
	"""实际调打码平台。同一 token 只能用一次（new-api 转发给 CF siteverify 后即作废），
	每个账号签到前各解一个。CapSolver 的 createTask 可能直接带 solution.token，
	与轮询 getTaskResult 两种返回方式一并兼容。
	"""
	import balance_server as bs

	def _post(path: str, payload: dict):
		sess = bs._get_cffi_session(f'turnstile-solver:{cfg["base_url"]}')
		return sess.post(cfg['base_url'] + path, json=payload, timeout=30)

	loop = asyncio.get_running_loop()

	async with _TURNSTILE_SOLVER_SEM:
		try:
			resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _post, '/createTask', {
				'clientKey': cfg['api_key'],
				'task': {'type': cfg['task_type'], 'websiteURL': page_url, 'websiteKey': site_key},
			})
			data = resp.json()
			if data.get('errorId'):
				return {'success': False, 'error': f"createTask 失败: {data.get('errorCode') or data.get('errorDescription')}"}
			token = (data.get('solution') or {}).get('token')
			if token:
				return {'success': True, 'token': token}
			task_id = data.get('taskId')
			if not task_id:
				return {'success': False, 'error': f'createTask 响应异常: {str(data)[:150]}'}

			deadline = time.monotonic() + timeout
			while time.monotonic() < deadline:
				await asyncio.sleep(bs.TURNSTILE_SOLVER_POLL_INTERVAL)
				resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _post, '/getTaskResult', {
					'clientKey': cfg['api_key'], 'taskId': task_id,
				})
				data = resp.json()
				if data.get('errorId'):
					return {'success': False, 'error': f"求解失败: {data.get('errorCode') or data.get('errorDescription')}"}
				if data.get('status') == 'ready':
					token = (data.get('solution') or {}).get('token')
					if token:
						return {'success': True, 'token': token}
					return {'success': False, 'error': '平台返回 ready 但没有 token'}
			return {'success': False, 'error': f'求解超时（{timeout}s 未就绪）'}
		except Exception as e:
			return {'success': False, 'error': f'{type(e).__name__}: {e}'[:150]}


# ── 打码用量统计：按天计数（次数 = 费用），跨重启持久化到 solver_stats.json ──
# 路径常量 SOLVER_STATS_FILE 过渡期留在 balance_server（块E 随 config 一并收口）


def _solver_stats() -> dict:
	import balance_server as bs

	today = datetime.now().strftime('%Y-%m-%d')
	try:
		data = json.loads(bs.SOLVER_STATS_FILE.read_text(encoding='utf-8'))
	except Exception:
		data = {}
	if not isinstance(data, dict) or data.get('date') != today:
		data = {'date': today, 'solved': 0, 'failed': 0}
	return data


def _solver_stats_bump(ok: bool) -> None:
	import balance_server as bs

	try:
		data = _solver_stats()
		data['solved' if ok else 'failed'] = data.get('solved' if ok else 'failed', 0) + 1
		bs._atomic_write_json(bs.SOLVER_STATS_FILE, data)
	except Exception as e:
		print(f'[SOLVER] 用量统计写入失败: {e}')


@turnstile_router.get('/api/turnstile/solver/stats')
async def solver_stats():
	"""今日打码用量（成功/失败次数，失败也消耗平台侧部分配额）"""
	return {'success': True, 'stats': _solver_stats()}


@turnstile_router.post('/api/turnstile/solver/balance')
async def solver_balance():
	"""查询打码平台账户余额（按次查询，前端按钮触发，不自动轮询）。

	2Captcha 用 res.php?action=getbalance，YesCaptcha/CapSolver/自定义网关走
	createTask 同族的 POST /getBalance {clientKey}。
	"""
	import balance_server as bs

	cfg = get_turnstile_solver_config()
	if not cfg['api_key']:
		return {'success': False, 'error': '打码平台未配置'}

	def _do_2captcha():
		sess = bs._get_cffi_session(f'turnstile-solver:{cfg["base_url"]}')
		return sess.get(f'{cfg["base_url"]}/res.php', params={'key': cfg['api_key'], 'action': 'getbalance', 'json': 1}, timeout=15)

	def _do_getbalance():
		sess = bs._get_cffi_session(f'turnstile-solver:{cfg["base_url"]}')
		return sess.post(cfg['base_url'] + '/getBalance', json={'clientKey': cfg['api_key']}, timeout=15)

	loop = asyncio.get_running_loop()
	try:
		if cfg['provider'] == '2captcha':
			data = (await loop.run_in_executor(bs._UPSTREAM_POOL, _do_2captcha)).json()
			# {"status":1,"request":"9.12"} 或 {"status":0,"request":"ERROR_..."}
			if str(data.get('status')) == '1':
				return {'success': True, 'balance': float(data.get('request', 0))}
			return {'success': False, 'error': str(data.get('request'))[:100]}
		resp = await loop.run_in_executor(bs._UPSTREAM_POOL, _do_getbalance)
		data = resp.json()
		if data.get('errorId'):
			return {'success': False, 'error': str(data.get('errorCode') or data.get('errorDescription'))[:100]}
		if data.get('balance') is not None:
			return {'success': True, 'balance': float(data['balance'])}
		return {'success': False, 'error': f'响应异常: {str(data)[:100]}'}
	except Exception as e:
		return {'success': False, 'error': f'{type(e).__name__}: {e}'[:120]}


@turnstile_router.get('/api/turnstile/solver')
async def turnstile_solver_status():
	"""打码平台 / FlareSolverr 配置状态。api_key 只回是否已配置，绝不回显。"""
	import balance_server as bs

	cfg = get_turnstile_solver_config()
	return {
		'success': True,
		'solver': {
			'provider': cfg['provider'],
			'base_url': cfg['base_url'],
			'configured': bool(cfg['api_key'] and cfg['base_url'] and cfg['task_type']),
			'flaresolverr_url': bs.get_flaresolverr_url(),
			'presets': {name: p['base_url'] for name, p in TURNSTILE_SOLVER_PRESETS.items()},
			'stats': _solver_stats(),
		},
	}


@turnstile_router.post('/api/turnstile/solver')
async def save_turnstile_solver(req: TurnstileSolverRequest):
	"""保存打码平台 / FlareSolverr 配置（合并写 saved_config.json，不动其他段）"""
	import balance_server as bs

	provider = req.provider.strip().lower()
	if provider != 'custom' and provider not in TURNSTILE_SOLVER_PRESETS:
		return {'success': False, 'error': f'未知平台: {req.provider}（可选 2captcha / yescaptcha / capsolver / custom）'}
	try:
		data = dict(bs._read_json_cached(bs.CONFIG_FILE) or {}) if bs.CONFIG_FILE.exists() else {}
	except Exception:
		data = {}
	saved = data.get('turnstile_solver') or {}
	data['turnstile_solver'] = {
		'provider': provider,
		'api_key': req.api_key.strip() or saved.get('api_key') or '',
		'base_url': req.base_url.strip(),
		'flaresolverr_url': req.flaresolverr_url.strip(),
	}
	bs._atomic_write_json(bs.CONFIG_FILE, data, indent=2)
	return {'success': True, 'message': '打码平台配置已保存'}


@turnstile_router.post('/api/turnstile/solver/test')
async def test_turnstile_solver(site_id: str = ''):
	"""实解一个 token 验证打码配置（会消耗一次打码费用，约 $0.001~0.002）。

	不指定站点时自动挑第一个开着 Turnstile 且有 sitekey 的站点。只求解不消费：
	token 不会拿去签到，纯验证 api_key、余额与网络链路是否可用。
	"""
	import balance_server as bs

	cfg = get_turnstile_solver_config()
	if not cfg['api_key']:
		return {'success': False, 'error': '请先保存 api_key'}
	sites = bs.load_newapi_sites()
	site = None
	site_key = ''
	if site_id:
		site = next((s for s in sites if s.id == site_id), None)
		if site is None:
			return {'success': False, 'error': f'未知站点: {site_id}'}
		ts = await bs.newapi_turnstile_status(site)
		if not ts['enabled']:
			return {'success': True, 'tested': False, 'message': f'{site.label} 未开启 Turnstile，无需打码'}
		if not ts['site_key']:
			return {'success': False, 'error': f'{site.label} 状态探测失败拿不到 sitekey'}
		site_key = ts['site_key']
	else:
		for s in sites:
			ts = await bs.newapi_turnstile_status(s)
			if ts['enabled'] and ts['site_key']:
				site, site_key = s, ts['site_key']
				break
		if site is None:
			return {'success': False, 'error': '没有开着 Turnstile 的站点，没有可用于测试的 sitekey'}

	page_url = site.domain.rstrip('/') + '/login'
	started = time.monotonic()
	r = await solve_turnstile_token(site_key, page_url)
	elapsed = round(time.monotonic() - started, 1)
	if r.get('success'):
		return {'success': True, 'tested': True, 'site': site.label, 'elapsed': elapsed, 'token_preview': (r['token'] or '')[:24] + '…'}
	return {'success': False, 'tested': True, 'site': site.label, 'elapsed': elapsed, 'error': r.get('error')}
