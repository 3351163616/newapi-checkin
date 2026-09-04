"""Turnstile 打码平台的离线单测：配置读写、token 求解、带 token 签到与调度分支。

不发任何上游请求 —— 打码平台的 HTTP 用假 Session 替身驱动。
    .venv/bin/python -m pytest tests/test_turnstile_solver.py -q
"""

import asyncio
import json

import pytest

import balance_server as bs


class FakeResponse:
	"""够用的 curl_cffi Response 替身。"""

	def __init__(self, payload):
		self.status_code = 200
		self.text = json.dumps(payload)
		self.headers = {'content-type': 'application/json; charset=utf-8'}
		self.cookies = {}

	def json(self):
		return json.loads(self.text)


class FakeSolverSession:
	"""按 path 依次吐预设响应的 Session 替身，记录调用供断言。"""

	def __init__(self, responses):
		# 直接引用（不拷贝）：轮询每次都会新建 Session，得让 pop 消耗跨 Session 生效
		self.responses = responses
		self.calls = []

	def post(self, path, json=None, timeout=None):
		# 真实调用传的是完整 URL（base_url + path），按预设 key 做后缀匹配
		key = next((k for k in self.responses if path.endswith(k)), path)
		self.calls.append((key, json))
		seq = self.responses.setdefault(key, [{}])
		payload = seq.pop(0) if len(seq) > 1 else seq[0]
		return FakeResponse(payload)


class SolverEnv:
	"""假 Session 工厂：测试往 responses 里塞平台响应，sessions 攒下全部请求记录。"""

	def __init__(self):
		self.responses = {}
		self.sessions = []

	def session_factory(self, key, proxies=None):
		sess = FakeSolverSession(self.responses)
		self.sessions.append(sess)
		return sess

	def all_calls(self):
		return [call for s in self.sessions for call in s.calls]


@pytest.fixture
def config_file(tmp_path, monkeypatch):
	"""把 saved_config.json 指到 tmp_path，隔离真实配置。"""
	cfg = tmp_path / 'saved_config.json'
	monkeypatch.setattr(bs, 'CONFIG_FILE', cfg)
	return cfg


@pytest.fixture
def solver_env(config_file, monkeypatch):
	"""预置 YesCaptcha 配置与假 Session 工厂，返回 SolverEnv。"""
	monkeypatch.setattr(bs, 'TURNSTILE_SOLVER_PRESETS', {
		'yescaptcha': {'base_url': 'https://api.yescaptcha.com', 'task_type': 'TurnstileTaskProxyless'},
	})
	env = SolverEnv()
	monkeypatch.setattr(bs, '_get_cffi_session', env.session_factory)
	return env


def write_solver_config(config_file, **solver):
	config_file.write_text(json.dumps({'turnstile_solver': solver}, ensure_ascii=False), encoding='utf-8')


# ===== 配置读取 =====


def test_未配置时返回空且不炸(config_file):
	cfg = bs.get_turnstile_solver_config()
	assert cfg['api_key'] == '' and cfg['provider'] == ''
	assert not cfg['base_url'] and not cfg['task_type']


def test_配置损坏时视为未配置(config_file):
	config_file.write_text('{oops', encoding='utf-8')
	assert bs.get_turnstile_solver_config()['api_key'] == ''


def test_已知平台补全默认域名与任务类型(config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key=' k1 ')
	cfg = bs.get_turnstile_solver_config()
	assert cfg['api_key'] == 'k1'
	assert cfg['base_url'] == 'https://api.yescaptcha.com'
	assert cfg['task_type'] == 'TurnstileTaskProxyless'


def test_自定义网关只给域名时按2captcha协议猜任务类型(config_file):
	write_solver_config(config_file, provider='custom', api_key='k', base_url='https://gw.example.com/')
	cfg = bs.get_turnstile_solver_config()
	assert cfg['base_url'] == 'https://gw.example.com'
	assert cfg['task_type'] == 'TurnstileTaskProxyless'


# ===== token 求解 =====


def test_solve_直接返回token时不再轮询(solver_env, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	solver_env.responses = {'/createTask': [{'errorId': 0, 'solution': {'token': 'tok-1'}}]}
	r = asyncio.run(bs.solve_turnstile_token('0xAAA', 'https://x.com/login'))
	assert r['success'] and r['token'] == 'tok-1'
	assert [(p, _) for p, _ in solver_env.all_calls() if p == '/getTaskResult'] == [], '同步出 token 就不该轮询'


def test_solve_轮询到ready(solver_env, config_file, monkeypatch):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	monkeypatch.setattr(bs, 'TURNSTILE_SOLVER_POLL_INTERVAL', 0)
	solver_env.responses = {
		'/createTask': [{'errorId': 0, 'taskId': 't1'}],
		'/getTaskResult': [{'errorId': 0, 'status': 'processing'}, {'errorId': 0, 'status': 'ready', 'solution': {'token': 'tok-2'}}],
	}
	r = asyncio.run(bs.solve_turnstile_token('0xAAA', 'https://x.com/login'))
	assert r['success'] and r['token'] == 'tok-2'
	assert len(solver_env.all_calls()) == 3


def test_solve_平台报错原样透出(solver_env, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	solver_env.responses = {'/createTask': [{'errorId': 1, 'errorCode': 'ERROR_KEY_DOES_NOT_EXIST'}]}
	r = asyncio.run(bs.solve_turnstile_token('0xAAA', 'https://x.com/login'))
	assert not r['success'] and 'ERROR_KEY_DOES_NOT_EXIST' in r['error']


def test_solve_超时放弃(solver_env, config_file, monkeypatch):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	monkeypatch.setattr(bs, 'TURNSTILE_SOLVER_POLL_INTERVAL', 0)
	solver_env.responses = {'/createTask': [{'errorId': 0, 'taskId': 't1'}]}
	r = asyncio.run(bs.solve_turnstile_token('0xAAA', 'https://x.com/login', timeout=0))
	assert not r['success'] and '超时' in r['error']


def test_solve_未配置直接拒绝(solver_env, config_file):
	r = asyncio.run(bs.solve_turnstile_token('0xAAA', 'https://x.com/login'))
	assert not r['success'] and '未配置' in r['error']
	assert solver_env.sessions == [], '未配置就不该发任何请求'


def test_solve_缺sitekey直接拒绝(solver_env, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	r = asyncio.run(bs.solve_turnstile_token('', 'https://x.com/login'))
	assert not r['success'] and 'sitekey' in r['error']
	assert solver_env.sessions == []


def test_solve_请求体带平台与页面信息(solver_env, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='kk')
	solver_env.responses = {'/createTask': [{'errorId': 0, 'solution': {'token': 't'}}]}
	asyncio.run(bs.solve_turnstile_token('0xSITE', 'https://x.com/login'))
	_, body = solver_env.all_calls()[0]
	assert body['clientKey'] == 'kk'
	assert body['task'] == {'type': 'TurnstileTaskProxyless', 'websiteURL': 'https://x.com/login', 'websiteKey': '0xSITE'}


# ===== 签到链路 =====


def _ok(path_box=None):
	"""返回一个记下 path 的 fake newapi_request。"""
	async def fake_request(s, method, path, headers, json_body=None):
		if path_box is not None:
			path_box.append(path)
		return FakeResponse({'success': True, 'message': '签到成功', 'data': {}})
	return fake_request


def test_sign_in_带token时拼进query(monkeypatch):
	site = bs.NewapiSite(id='t', label='T', domain='https://t.com')
	acc = bs.NewapiAccountItem(name='a', access_token='at', user_id='1')
	paths = []
	monkeypatch.setattr(bs, 'newapi_request', _ok(paths))
	r = asyncio.run(bs.sign_in_newapi(site, acc, turnstile_token='XX/YY+ZZ'))
	assert r['success']
	assert paths == ['/api/user/checkin?turnstile=XX%2FYY%2BZZ'], 'token 需 urlencode 后放 query'


def test_sign_in_不带token时路径干净(monkeypatch):
	site = bs.NewapiSite(id='t', label='T', domain='https://t.com')
	acc = bs.NewapiAccountItem(name='a', access_token='at', user_id='1')
	paths = []
	monkeypatch.setattr(bs, 'newapi_request', _ok(paths))
	asyncio.run(bs.sign_in_newapi(site, acc))
	assert paths == ['/api/user/checkin']


@pytest.fixture
def sandbox(tmp_path, monkeypatch, config_file):
	"""站点注册表/账号/状态全部隔离到 tmp_path。"""
	monkeypatch.setattr(bs, 'NEWAPI_SITES_FILE', tmp_path / 'newapi_sites.json')
	monkeypatch.setattr(bs, 'NEWAPI_SEED_SITES', [])
	monkeypatch.setattr(bs.NewapiSite, 'accounts_path', lambda self: tmp_path / f'{self.id}_accounts.json')
	monkeypatch.setattr(bs.NewapiSite, 'state_path', lambda self: tmp_path / f'{self.id}_state.json')
	bs.newapi_checkin_states.clear()
	bs.waf_cache.clear()
	yield tmp_path
	bs.newapi_checkin_states.clear()
	bs.waf_cache.clear()


def _prep_site(sandbox, accounts=('a1', 'a2')):
	site = bs.NewapiSite(id='ts', label='TSite', domain='https://ts.com')
	(sandbox / 'ts_accounts.json').write_text(json.dumps([
		{'name': n, 'access_token': f'at-{n}', 'user_id': str(i)} for i, n in enumerate(accounts)
	]), encoding='utf-8')
	return site


def test_调度_turnstile开启且有打码时逐账号求解并签到(sandbox, monkeypatch, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	site = _prep_site(sandbox)

	async def fake_ts(s):
		return {'enabled': True, 'site_key': '0xK', 'probed': True}

	async def fake_solve(site_key, page_url, timeout=0):
		return {'success': True, 'token': f'tok-{page_url}-{site_key}'}

	async def fake_info(s, acc):
		return {'success': True, 'checked_in_today': False}

	paths = []

	async def fake_bal(*a, **kw):
		return {'success': False}

	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'newapi_checkin_info', fake_info)
	monkeypatch.setattr(bs, 'solve_turnstile_token', fake_solve)
	monkeypatch.setattr(bs, 'newapi_request', _ok(paths))
	monkeypatch.setattr(bs, 'query_balance_newapi', fake_bal)

	asyncio.run(bs.run_newapi_checkin(site, trigger='manual'))
	st = bs.newapi_state(site)
	assert st['signed'] == 2 and st['failed'] == 0
	assert sorted(p.split('?')[1] for p in paths) == [
		'turnstile=tok-https%3A%2F%2Fts.com%2Flogin-0xK',
		'turnstile=tok-https%3A%2F%2Fts.com%2Flogin-0xK',
	], '每个账号各带一个求解出的 token'


def test_调度_turnstile开启但未配置打码时整体放弃(sandbox, monkeypatch, config_file):
	site = _prep_site(sandbox)

	async def fake_ts(s):
		return {'enabled': True, 'site_key': '0xK', 'probed': True}

	async def fail_solve(*a, **kw):
		raise AssertionError('未配置打码平台就不该尝试求解')

	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'solve_turnstile_token', fail_solve)

	asyncio.run(bs.run_newapi_checkin(site, trigger='manual'))
	st = bs.newapi_state(site)
	assert st['failed'] == 2 and st['signed'] == 0
	assert all('打码平台' in v['message'] for v in st['accounts'].values())


def test_调度_求解失败只标记该账号其余照签(sandbox, monkeypatch, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	site = _prep_site(sandbox, accounts=('bad', 'good'))

	async def fake_ts(s):
		return {'enabled': True, 'site_key': '0xK', 'probed': True}

	# bad 先被 gather 调度，第一次求解失败，第二次成功
	solve_seq = {'n': 0}

	async def fake_solve(site_key, page_url, timeout=0):
		solve_seq['n'] += 1
		if solve_seq['n'] == 1:
			return {'success': False, 'error': '余额不足'}
		return {'success': True, 'token': f'tok-{solve_seq["n"]}'}

	async def fake_info(s, acc):
		return {'success': True, 'checked_in_today': False}

	signed_users = []

	async def fake_request(s, method, path, headers, json_body=None):
		signed_users.append(headers.get('new-api-user'))
		return FakeResponse({'success': True, 'message': '签到成功', 'data': {}})

	async def fake_bal(*a, **kw):
		return {'success': False}

	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'newapi_checkin_info', fake_info)
	monkeypatch.setattr(bs, 'solve_turnstile_token', fake_solve)
	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	monkeypatch.setattr(bs, 'query_balance_newapi', fake_bal)

	asyncio.run(bs.run_newapi_checkin(site, trigger='manual'))
	st = bs.newapi_state(site)
	assert st['failed'] == 1 and st['signed'] == 1
	assert '过验失败' in st['accounts']['bad']['message'] and '余额不足' in st['accounts']['bad']['message']
	assert '0' not in signed_users and '1' in signed_users, 'bad(user_id=0) 没签到，good(user_id=1) 正常签'


def test_调度_打码预检剔除已签账号不白解token(sandbox, monkeypatch, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	site = _prep_site(sandbox, accounts=('done', 'todo'))

	async def fake_ts(s):
		return {'enabled': True, 'site_key': '0xK', 'probed': True}

	async def fake_info(s, acc):
		if acc.name == 'done':
			return {'success': True, 'checked_in_today': True}
		return {'success': True, 'checked_in_today': False}

	solve_calls = {'n': 0}

	async def fake_solve(site_key, page_url, timeout=0):
		solve_calls['n'] += 1
		return {'success': True, 'token': f'tok-{solve_calls["n"]}'}

	paths = []

	async def fake_bal(*a, **kw):
		return {'success': False}

	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'newapi_checkin_info', fake_info)
	monkeypatch.setattr(bs, 'solve_turnstile_token', fake_solve)
	monkeypatch.setattr(bs, 'newapi_request', _ok(paths))
	monkeypatch.setattr(bs, 'query_balance_newapi', fake_bal)

	asyncio.run(bs.run_newapi_checkin(site, trigger='manual'))
	st = bs.newapi_state(site)
	assert solve_calls['n'] == 1, '已签账号不该消耗打码 token'
	assert st['signed'] == 1 and st['already'] == 1 and st['failed'] == 0
	assert '预检' in st['accounts']['done']['message']
	assert len(paths) == 1, '已签账号连签到 POST 也不该发'


def test_调度_全部已签时直接结束不碰打码(sandbox, monkeypatch, config_file):
	write_solver_config(config_file, provider='yescaptcha', api_key='k')
	site = _prep_site(sandbox, accounts=('d1', 'd2'))

	async def fake_ts(s):
		return {'enabled': True, 'site_key': '0xK', 'probed': True}

	async def fake_info(s, acc):
		return {'success': True, 'checked_in_today': True}

	async def fail_solve(*a, **kw):
		raise AssertionError('全部已签就不该尝试求解')

	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'newapi_checkin_info', fake_info)
	monkeypatch.setattr(bs, 'solve_turnstile_token', fail_solve)

	asyncio.run(bs.run_newapi_checkin(site, trigger='manual'))
	st = bs.newapi_state(site)
	assert st['already'] == 2 and st['signed'] == 0 and st['failed'] == 0
