"""通用站点防护层的离线单测：CF 质询识别、FlareSolverr 求解、阿里云 WAF 泛化、
newapi_request 撞防护自动过验重试。

不发任何上游请求 —— HTTP 全部用假 Session 替身驱动。
    .venv/bin/python -m pytest tests/test_protection.py -q
"""

import asyncio
import json
import time

import pytest

import balance_server as bs


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
	"""每个测试都隔离 CONFIG_FILE 与防护缓存，防跨测试污染。"""
	monkeypatch.setattr(bs, 'CONFIG_FILE', tmp_path / 'saved_config.json')
	bs.protection_cache.clear()
	bs._protection_locks.clear()
	yield
	bs.protection_cache.clear()
	bs._protection_locks.clear()


@pytest.fixture
def config_file(tmp_path, monkeypatch):
	"""供断言/预写配置用，路径与 _isolate 一致。"""
	return tmp_path / 'saved_config.json'


class FakeResp:
	"""够用的 curl_cffi Response 替身。"""

	def __init__(self, status_code=200, payload=None, body=None, headers=None):
		self.status_code = status_code
		self.text = body if body is not None else json.dumps(payload or {})
		self.headers = headers or {'content-type': 'application/json'}
		self.cookies = {}

	def json(self):
		return json.loads(self.text)


class FakeCookieJar:
	def __init__(self, d=None):
		self.d = dict(d or {})
		self.updates = []

	def get(self, name, default=None):
		return self.d.get(name, default)

	def update(self, mapping):
		self.d.update(dict(mapping))
		self.updates.append(dict(mapping))


class FakeSession:
	"""按序吐 FakeResp 的会话替身，记录请求与 cookie 注入。script 空了重复最后一个。"""

	def __init__(self, script):
		# 直接引用（不拷贝）：重试会新建 Session，pop 消耗要跨会话生效
		self.script = script
		self.requests = []
		self.cookies = FakeCookieJar()

	def request(self, method, url, headers=None, json=None):
		self.requests.append({'method': method, 'url': url, 'headers': dict(headers or {})})
		if len(self.script) > 1:
			return self.script.pop(0)
		return self.script[0]

	def post(self, url, json=None, timeout=None):
		return self.request('POST', url, headers=None, json=json)

	def get(self, url, headers=None):
		return self.request('GET', url, headers=headers)


@pytest.fixture
def env(tmp_path, monkeypatch, config_file):
	"""隔离 CONFIG_FILE / 防护缓存，提供按 key 分发的假 Session 工厂。"""
	monkeypatch.setattr(bs, 'NEWAPI_SITES_FILE', tmp_path / 'sites.json')
	monkeypatch.setattr(bs, 'NEWAPI_SEED_SITES', [])
	bs.protection_cache.clear()
	bs._protection_locks.clear()

	site_sessions = []
	flare_sessions = []
	site_script = []
	flare_script = []

	def factory(key, proxies=None):
		if key.startswith('flaresolverr:'):
			sess = FakeSession(flare_script)
			flare_sessions.append(sess)
			return sess
		sess = FakeSession(site_script)
		site_sessions.append(sess)
		return sess

	factory.site_sessions = site_sessions
	factory.flare_sessions = flare_sessions
	factory.site_script = site_script
	factory.flare_script = flare_script
	monkeypatch.setattr(bs, '_get_cffi_session', factory)
	return factory


def write_config(config_file, **solver):
	config_file.write_text(json.dumps({'turnstile_solver': solver}, ensure_ascii=False), encoding='utf-8')


def site(id='t', domain='https://t.com'):
	return bs.NewapiSite(id=id, label='T', domain=domain)


# ===== 防护识别 =====


def test_detect_cf质询_响应头优先():
	r = FakeResp(403, body='anything', headers={'cf-mitigated': 'challenge'})
	assert bs.detect_protection(r) == 'cf_challenge'


def test_detect_cf质询_正文兜底():
	r = FakeResp(503, body='Just a moment...', headers={})
	assert bs.detect_protection(r) == 'cf_challenge'


def test_detect_阿里云waf_arg1特征():
	r = FakeResp(200, body="var arg1='AABBCCDD';", headers={})
	assert bs.detect_protection(r) == 'aliyun_waf'


def test_detect_正常JSON返回None():
	r = FakeResp(200, payload={'success': True, 'data': {}})
	assert bs.detect_protection(r) is None


def test_acw算法可逆向量():
	# 构造 arg1 使重排结果恰好等于 mask：异或后应得全 0
	arg1 = [''] * 40
	for i, pos in enumerate(bs._WAF_POS):
		arg1[pos - 1] = bs._WAF_MASK[i]
	assert bs._solve_acw_sc_v2(''.join(arg1)) == '0' * 40


# ===== 阿里云 WAF 泛化求解 =====


def test_solve_aliyun_waf_提取挑战并算cookie(monkeypatch):
	class FakeWafSession:
		def __init__(self, **kw):
			self.cookies = FakeCookieJar({'acw_tc': 'tc-1', 'cdn_sec_tc': 'cdn-1'})

		def get(self, url, headers=None):
			return FakeResp(200, body="<script>var arg1='0123456789ABCDEF0123456789ABCDEF0123456789AB';</script>")

	monkeypatch.setattr('curl_cffi.requests.Session', FakeWafSession)
	r = asyncio.run(bs.solve_aliyun_waf('https://w.com'))
	assert r['cookies']['acw_tc'] == 'tc-1'
	assert r['cookies']['acw_sc__v2'] == bs._solve_acw_sc_v2('0123456789ABCDEF0123456789ABCDEF0123456789AB')


def test_solve_aliyun_waf_无挑战返回None(monkeypatch):
	class FakeWafSession:
		def __init__(self, **kw):
			self.cookies = FakeCookieJar()

		def get(self, url, headers=None):
			return FakeResp(200, payload={'ok': True})

	monkeypatch.setattr('curl_cffi.requests.Session', FakeWafSession)
	assert asyncio.run(bs.solve_aliyun_waf('https://w.com')) is None


# ===== FlareSolverr =====


def test_solve_cf_未配置直接None不发请求(env, config_file):
	env.flare_script.append(FakeResp(200, payload={'status': 'ok'}))
	assert asyncio.run(bs.solve_cf_challenge('https://t.com')) is None
	assert env.flare_sessions == [], '没配 FlareSolverr 就不该发请求'


def test_solve_cf_成功返回cookies和UA(env, config_file):
	write_config(config_file, flaresolverr_url='http://127.0.0.1:8191')
	env.flare_script.append(FakeResp(200, payload={
		'status': 'ok',
		'solution': {
			'cookies': [{'name': 'cf_clearance', 'value': 'cb-1'}, {'name': '__cf_bm', 'value': 'bm-1'}],
			'userAgent': 'fl-ua/1.0',
		},
	}))
	r = asyncio.run(bs.solve_cf_challenge('https://t.com'))
	assert r['cookies'] == {'cf_clearance': 'cb-1', '__cf_bm': 'bm-1'}
	assert r['user_agent'] == 'fl-ua/1.0'
	assert env.flare_sessions[0].requests[0]['url'] == 'http://127.0.0.1:8191/v1'


def test_solve_cf_失败返回None(env, config_file):
	write_config(config_file, flaresolverr_url='http://127.0.0.1:8191')
	env.flare_script.append(FakeResp(200, payload={'status': 'error', 'message': 'Challenges failed'}))
	assert asyncio.run(bs.solve_cf_challenge('https://t.com')) is None


# ===== 缓存 + singleflight =====


def test_ensure_并发只解一次且结果进缓存(monkeypatch):
	calls = {'n': 0}

	async def slow_solve(domain):
		calls['n'] += 1
		await asyncio.sleep(0.01)
		return {'cookies': {'cf_clearance': 'cb'}, 'user_agent': 'ua'}

	monkeypatch.setattr(bs, 'solve_cf_challenge', slow_solve)
	s = site()

	async def run():
		return await asyncio.gather(
			bs.ensure_protection_cookies(s, 'cf_challenge'),
			bs.ensure_protection_cookies(s, 'cf_challenge'),
		)

	a, b = asyncio.run(run())
	assert calls['n'] == 1, 'singleflight：并发只放一个去解'
	assert a and b and a['cookies'] == b['cookies']
	assert bs.protection_cache['https://t.com']['cookies'] == {'cf_clearance': 'cb'}


def test_ensure_求解失败不进缓存(monkeypatch):
	async def fail_solve(domain):
		return None

	monkeypatch.setattr(bs, 'solve_aliyun_waf', fail_solve)
	s = site()
	assert asyncio.run(bs.ensure_protection_cookies(s, 'aliyun_waf')) is None
	assert bs.protection_cache == {}


# ===== newapi_request 自动过验 =====


def test_请求_撞质询自动过验并重试(env, config_file):
	write_config(config_file, flaresolverr_url='http://127.0.0.1:8191')
	env.site_script.extend([
		FakeResp(403, body='Just a moment...', headers={}),
		FakeResp(200, payload={'success': True, 'data': {}}),
	])
	env.flare_script.append(FakeResp(200, payload={
		'status': 'ok',
		'solution': {'cookies': [{'name': 'cf_clearance', 'value': 'cb-9'}], 'userAgent': 'fl-ua'},
	}))
	r = asyncio.run(bs.newapi_request(site(), 'GET', '/api/status', {'User-Agent': 'orig'}))
	assert r.status_code == 200
	assert len(env.site_sessions) == 2, '质询 + 重试共两次请求'
	second = env.site_sessions[1].requests[0]
	assert second['headers']['User-Agent'] == 'fl-ua', 'cf_clearance 绑 UA，重试必须带求解方的 UA'
	assert env.site_sessions[1].cookies.d.get('cf_clearance') == 'cb-9'


def test_请求_有缓存时直接带cookies一次成功(env, config_file):
	bs.protection_cache['https://t.com'] = {
		'cookies': {'cf_clearance': 'cached'}, 'user_agent': 'cached-ua', 'expires': time.time() + 60,
	}
	env.site_script.append(FakeResp(200, payload={'success': True}))
	r = asyncio.run(bs.newapi_request(site(), 'GET', '/api/status', {'User-Agent': 'orig'}))
	assert r.status_code == 200
	assert len(env.site_sessions) == 1
	sess = env.site_sessions[0]
	assert sess.requests[0]['headers']['User-Agent'] == 'cached-ua'
	assert sess.cookies.d.get('cf_clearance') == 'cached'
	assert env.flare_sessions == [], '缓存命中不该碰 FlareSolverr'


def test_请求_缓存cookies失效时作废并重解(env, config_file):
	# 带着缓存 cookies 仍撞质询 → 缓存已坏，必须作废重解，不能拿同一份坏 cookies 白打一次
	write_config(config_file, flaresolverr_url='http://127.0.0.1:8191')
	bs.protection_cache['https://t.com'] = {
		'cookies': {'cf_clearance': 'stale'}, 'user_agent': 'stale-ua', 'expires': time.time() + 300,
	}
	env.site_script.extend([
		FakeResp(403, body='Just a moment...', headers={}),
		FakeResp(200, payload={'success': True}),
	])
	env.flare_script.append(FakeResp(200, payload={
		'status': 'ok',
		'solution': {'cookies': [{'name': 'cf_clearance', 'value': 'fresh'}], 'userAgent': 'fresh-ua'},
	}))

	r = asyncio.run(bs.newapi_request(site(), 'GET', '/api/status', {'User-Agent': 'orig'}))
	assert r.status_code == 200
	assert env.flare_sessions, '坏缓存要触发重解'
	assert env.site_sessions[0].cookies.d.get('cf_clearance') == 'stale', '第一次请求带的是旧缓存'
	assert env.site_sessions[1].cookies.d.get('cf_clearance') == 'fresh', '重试必须带新解的 cookies'
	assert env.site_sessions[1].requests[0]['headers']['User-Agent'] == 'fresh-ua'
	assert bs.protection_cache['https://t.com']['cookies'] == {'cf_clearance': 'fresh'}


def test_请求_过验失败原样返回质询响应(env, config_file):
	# 未配置 FlareSolverr：ensure 返回 None，调用方拿到的就是质询页本身
	env.site_script.append(FakeResp(403, body='Just a moment...', headers={}))
	r = asyncio.run(bs.newapi_request(site(), 'GET', '/api/status', {'User-Agent': 'orig'}))
	assert r.status_code == 403 and 'Just a moment' in r.text
	assert len(env.site_sessions) == 1


def test_请求_禁用自动过验时撞质询不重试(env, config_file):
	write_config(config_file, flaresolverr_url='http://127.0.0.1:8191')
	env.site_script.append(FakeResp(403, body='Just a moment...', headers={}))
	env.flare_script.append(FakeResp(200, payload={'status': 'ok', 'solution': {'cookies': [], 'userAgent': ''}}))
	r = asyncio.run(bs.newapi_request(site(), 'GET', '/', {'User-Agent': 'u'}, _auto_bypass=False))
	assert r.status_code == 403
	assert len(env.site_sessions) == 1


def test_请求_正常响应零开销不触发求解(env):
	env.site_script.append(FakeResp(200, payload={'success': True}))
	r = asyncio.run(bs.newapi_request(site(), 'GET', '/api/status', {}))
	assert r.status_code == 200 and len(env.site_sessions) == 1


# ===== 配置与检测端点 =====


def test_配置_保存flaresolverr并读回(config_file):
	r = asyncio.run(bs.save_turnstile_solver(bs.TurnstileSolverRequest(
		provider='yescaptcha', api_key='k', flaresolverr_url='http://127.0.0.1:8191/')))
	assert r['success']
	assert bs.get_flaresolverr_url() == 'http://127.0.0.1:8191'
	status = asyncio.run(bs.turnstile_solver_status())
	assert status['solver']['flaresolverr_url'] == 'http://127.0.0.1:8191'


def test_配置_flaresolverr清空生效(config_file):
	write_config(config_file, flaresolverr_url='http://old:8191')
	asyncio.run(bs.save_turnstile_solver(bs.TurnstileSolverRequest(provider='yescaptcha', api_key='k', flaresolverr_url='')))
	assert bs.get_flaresolverr_url() == ''


def test_protection_test_聚合探测与求解(env, config_file, monkeypatch):
	write_config(config_file, flaresolverr_url='http://127.0.0.1:8191')

	async def fake_probe(domain):
		return {'http_status': 403, 'cf_challenge': True, 'aliyun_waf': False}

	async def fake_ts(s):
		return {'enabled': True, 'site_key': '0xK', 'probed': True}

	async def fake_solve_cf(domain):
		return {'cookies': {'cf_clearance': 'cb'}, 'user_agent': 'ua'}

	monkeypatch.setattr(bs, 'probe_page_protection', fake_probe)
	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'solve_cf_challenge', fake_solve_cf)
	monkeypatch.setattr(bs, 'load_newapi_sites', lambda: [site()])

	r = asyncio.run(bs.protection_test())
	assert r['success'] and r['site'] == 'T'
	assert r['protections'] == {'cf_challenge': True, 'aliyun_waf': False, 'turnstile': True}
	assert r['solved'] == {'cf_challenge': True}


def test_protection_test_撞质询未配flaresolverr时报None(env, config_file, monkeypatch):
	async def fake_probe(domain):
		return {'http_status': 403, 'cf_challenge': True, 'aliyun_waf': False}

	async def fake_ts(s):
		return {'enabled': False, 'site_key': '', 'probed': True}

	monkeypatch.setattr(bs, 'probe_page_protection', fake_probe)
	monkeypatch.setattr(bs, 'newapi_turnstile_status', fake_ts)
	monkeypatch.setattr(bs, 'load_newapi_sites', lambda: [site()])

	r = asyncio.run(bs.protection_test())
	assert r['solved'] == {'cf_challenge': None}, 'None = 撞到了但没配求解器'
