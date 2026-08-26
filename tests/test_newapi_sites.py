"""通用 new-api 站点层的离线单测：站点注册表、按站点隔离、签到与 Turnstile 分支。

不发任何上游请求 —— 用假 Response 驱动。
    .venv/bin/python -m pytest test_newapi_sites.py -q
"""

import asyncio
import json

import pytest

import balance_server as bs


class FakeResponse:
	"""够用的 curl_cffi Response 替身。"""

	def __init__(self, status_code=200, payload=None, body=None, content_type='application/json; charset=utf-8'):
		self.status_code = status_code
		self.text = body if body is not None else json.dumps(payload or {})
		self.headers = {'content-type': content_type}
		self.cookies = {}

	def json(self):
		return json.loads(self.text)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
	"""把站点注册表、账号文件、状态文件全部指到 tmp_path，避免碰真实配置。"""
	sites_file = tmp_path / 'newapi_sites.json'
	monkeypatch.setattr(bs, 'NEWAPI_SITES_FILE', sites_file)
	monkeypatch.setattr(bs, 'NEWAPI_SEED_SITES', [])
	# NewapiSite.accounts_path()/state_path() 用 __file__ 所在目录，这里改成 tmp_path
	monkeypatch.setattr(bs.NewapiSite, 'accounts_path', lambda self: tmp_path / (self.accounts_file or f'{self.id}_accounts.json'))
	monkeypatch.setattr(bs.NewapiSite, 'state_path', lambda self: tmp_path / (self.state_file or f'{self.id}_checkin_state.json'))
	bs.newapi_checkin_states.clear()
	bs.waf_cache.clear()
	yield tmp_path
	bs.newapi_checkin_states.clear()
	bs.waf_cache.clear()


def write_sites(sandbox, sites):
	(sandbox / 'newapi_sites.json').write_text(json.dumps(sites, ensure_ascii=False), encoding='utf-8')


def site(id='tabitoken', label='TaBiAI', domain='https://tabitoken.com', **kw):
	return bs.NewapiSite(id=id, label=label, domain=domain, **kw)


# ===== 站点注册表 =====


def test_注册表不存在时用种子初始化(sandbox, monkeypatch):
	monkeypatch.setattr(bs, 'NEWAPI_SEED_SITES', [{'id': 'gorouter', 'label': 'GoRouter', 'domain': 'https://gorouter.app'}])
	sites = bs.load_newapi_sites()
	assert [s.id for s in sites] == ['gorouter']
	assert (sandbox / 'newapi_sites.json').exists(), '初始化后应落盘，否则每次启动都重建'


def test_站点默认路径与新版newapi一致(sandbox):
	s = site()
	assert s.sign_in_path == '/api/user/checkin', '签到走 checkin，不是旧的 sign_in'
	assert s.status_path == '/api/status'
	assert s.api_user_key == 'new-api-user'
	assert s.quota_per_unit == 500000


def test_历史文件名可显式指定(sandbox):
	s = site(id='gorouter', accounts_file='gorouter_accounts.json', state_file='gorouter_checkin_state.json')
	assert s.accounts_path().name == 'gorouter_accounts.json', '升级到通用实现后旧账号文件要原地可用'
	assert s.state_path().name == 'gorouter_checkin_state.json'
	# 未指定时按 id 生成
	assert site(id='foo').accounts_path().name == 'foo_accounts.json'


def test_按id查站点(sandbox):
	write_sites(sandbox, [{'id': 'a', 'label': 'A', 'domain': 'https://a.com'}])
	assert bs.get_newapi_site('a').label == 'A'
	assert bs.get_newapi_site('nope') is None


# ===== 账号按站点隔离 =====


def test_两个站点的账号互不干扰(sandbox):
	s1, s2 = site(id='gorouter'), site(id='tabitoken')
	bs.save_newapi_accounts(s1, [bs.NewapiAccountItem(name='g1', access_token='t1', user_id='1')])
	bs.save_newapi_accounts(s2, [bs.NewapiAccountItem(name='t1', access_token='t2', user_id='2')])
	assert [a.name for a in bs.load_newapi_accounts(s1)] == ['g1']
	assert [a.name for a in bs.load_newapi_accounts(s2)] == ['t1']


def test_账号文件缺失返回空列表(sandbox):
	assert bs.load_newapi_accounts(site(id='never-created')) == []


# ===== 请求构造 =====


def test_请求头带bearer与站点自定义的user键(sandbox):
	s = site(api_user_key='x-user')
	acc = bs.NewapiAccountItem(name='n', access_token='tok', user_id='42')
	h = bs._newapi_headers(s, acc)
	assert h['Authorization'] == 'Bearer tok'
	assert h['x-user'] == '42', 'api_user_key 可按站点覆盖'
	assert h['Referer'] == 'https://tabitoken.com/console'


def test_请求打到站点自己的域名(sandbox, monkeypatch):
	seen = {}

	def fake_exec(pool, fn):
		# run_in_executor 的替身：直接调用同步函数
		raise AssertionError('不该走到这里')

	async def fake_request(s, method, path, headers, json_body=None):
		seen['url'] = s.domain + path
		return FakeResponse(payload={'success': True, 'data': {'quota': 500000, 'used_quota': 0}})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	acc = bs.NewapiAccountItem(name='n', access_token='t', user_id='1')
	asyncio.run(bs.query_balance_newapi(site(), acc))
	assert seen['url'] == 'https://tabitoken.com/api/user/self'


def test_余额换算用站点自己的quota单位(sandbox, monkeypatch):
	async def fake_request(s, method, path, headers, json_body=None):
		return FakeResponse(payload={'success': True, 'data': {'quota': 5000000, 'used_quota': 500000, 'username': 'u'}})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	acc = bs.NewapiAccountItem(name='n', access_token='t', user_id='1')
	r = asyncio.run(bs.query_balance_newapi(site(), acc))
	assert (r['quota'], r['used']) == (10.0, 1.0)
	# 换个单位，同样的上游数据应换出不同美元数
	r2 = asyncio.run(bs.query_balance_newapi(site(quota_per_unit=1000000), acc))
	assert (r2['quota'], r2['used']) == (5.0, 0.5)


# ===== Turnstile 探测 =====


def test_turnstile状态从status接口读且不硬编码sitekey(sandbox, monkeypatch):
	async def fake_request(s, method, path, headers, json_body=None):
		assert path == '/api/status'
		return FakeResponse(payload={'data': {'turnstile_check': True, 'turnstile_site_key': '0xABC'}})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	v = asyncio.run(bs.newapi_turnstile_status(site()))
	assert v == {'enabled': True, 'site_key': '0xABC', 'probed': True}


def test_turnstile探测失败保守假定已开启(sandbox, monkeypatch):
	async def boom(*a, **kw):
		raise RuntimeError('网络炸了')

	monkeypatch.setattr(bs, 'newapi_request', boom)
	v = asyncio.run(bs.newapi_turnstile_status(site()))
	assert v['enabled'] is True and v['probed'] is False, '探不到时宁可提示手动签，也别让自动签到静默失败'


def test_turnstile缓存按站点分开(sandbox, monkeypatch):
	calls = []

	async def fake_request(s, method, path, headers, json_body=None):
		calls.append(s.id)
		return FakeResponse(payload={'data': {'turnstile_check': s.id == 'a', 'turnstile_site_key': 'k-' + s.id}})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	a, b = site(id='a'), site(id='b')
	va1 = asyncio.run(bs.newapi_turnstile_status(a))
	vb1 = asyncio.run(bs.newapi_turnstile_status(b))
	va2 = asyncio.run(bs.newapi_turnstile_status(a))  # 命中缓存
	assert va1['site_key'] == 'k-a' and vb1['site_key'] == 'k-b'
	assert va1['enabled'] is True and vb1['enabled'] is False
	assert va2 == va1
	assert calls == ['a', 'b'], '同一站点第二次应命中缓存，且缓存不能跨站点串'


# ===== 签到 =====


def _stub_signin(monkeypatch, responses):
	"""按账号名给出不同的签到响应。"""

	async def fake_request(s, method, path, headers, json_body=None):
		if method == 'GET' and path == s.status_path:
			return FakeResponse(payload={'data': {'turnstile_check': False}})
		if method == 'GET' and path == s.user_info_path:
			return FakeResponse(payload={'success': True, 'data': {'quota': 500000, 'used_quota': 0}})
		name = headers[s.api_user_key]
		return responses[name]

	monkeypatch.setattr(bs, 'newapi_request', fake_request)


def test_签到成功与今日已签区分(sandbox, monkeypatch):
	_stub_signin(
		monkeypatch,
		{
			'1': FakeResponse(payload={'success': True, 'message': '签到成功，获得 $7.5'}),
			'2': FakeResponse(payload={'success': False, 'message': '今日已签到'}),
		},
	)
	s = site()
	r1 = asyncio.run(bs.sign_in_newapi(s, bs.NewapiAccountItem(name='n1', access_token='t', user_id='1')))
	r2 = asyncio.run(bs.sign_in_newapi(s, bs.NewapiAccountItem(name='n2', access_token='t', user_id='2')))
	assert r1['success'] and r1['already_signed'] is False
	assert r2['success'] and r2['already_signed'] is True, '已签到算成功，不该记为失败'


def test_turnstile拦截被标出来而不是当成账号问题(sandbox, monkeypatch):
	_stub_signin(monkeypatch, {'1': FakeResponse(payload={'success': False, 'message': 'Turnstile token 为空'})})
	r = asyncio.run(bs.sign_in_newapi(site(), bs.NewapiAccountItem(name='n', access_token='t', user_id='1')))
	assert r['success'] is False and r['turnstile_blocked'] is True


def test_开着turnstile时整轮快速失败不打上游(sandbox, monkeypatch):
	s = site()
	bs.save_newapi_accounts(s, [bs.NewapiAccountItem(name=f'n{i}', access_token='t', user_id=str(i)) for i in range(14)])
	signin_calls = []

	async def fake_request(s_, method, path, headers, json_body=None):
		if method == 'GET' and path == s_.status_path:
			return FakeResponse(payload={'data': {'turnstile_check': True, 'turnstile_site_key': '0xK'}})
		signin_calls.append(path)
		return FakeResponse(payload={'success': False, 'message': 'Turnstile token 为空'})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	monkeypatch.setattr(bs, 'record_account_usage', lambda *a: None)
	asyncio.run(bs.run_newapi_checkin(s, trigger='auto'))
	st = bs.newapi_state(s)
	assert st['failed'] == 14 and st['signed'] == 0
	assert signin_calls == [], '开着 Turnstile 时不该让 14 个账号各打一次上游拿同一句报错'
	assert 'Turnstile' in st['accounts']['n0']['message']


def test_签到状态按站点隔离且各自落盘(sandbox, monkeypatch):
	a, b = site(id='a', label='A'), site(id='b', label='B')
	bs.save_newapi_accounts(a, [bs.NewapiAccountItem(name='a1', access_token='t', user_id='1')])
	bs.save_newapi_accounts(b, [bs.NewapiAccountItem(name='b1', access_token='t', user_id='1')])

	async def fake_request(s_, method, path, headers, json_body=None):
		if method == 'GET' and path == s_.status_path:
			return FakeResponse(payload={'data': {'turnstile_check': False}})
		if method == 'GET' and path == s_.user_info_path:
			return FakeResponse(payload={'success': True, 'data': {'quota': 500000, 'used_quota': 0}})
		# a 站签到成功，b 站失败
		if s_.id == 'a':
			return FakeResponse(payload={'success': True, 'message': '签到成功'})
		return FakeResponse(payload={'success': False, 'message': '账号异常'})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	monkeypatch.setattr(bs, 'record_account_usage', lambda *a_: None)
	asyncio.run(bs.run_newapi_checkin(a))
	asyncio.run(bs.run_newapi_checkin(b))
	assert bs.newapi_state(a)['signed'] == 1 and bs.newapi_state(a)['failed'] == 0
	assert bs.newapi_state(b)['signed'] == 0 and bs.newapi_state(b)['failed'] == 1
	assert json.loads((sandbox / 'a_checkin_state.json').read_text())['signed'] == 1
	assert json.loads((sandbox / 'b_checkin_state.json').read_text())['failed'] == 1


def test_签到状态恢复后running被清掉(sandbox):
	s = site(id='a')
	(sandbox / 'a_checkin_state.json').write_text(
		json.dumps({'running': True, 'date': '2026-08-11', 'total': 3, 'accounts': {}}), encoding='utf-8'
	)
	bs.load_newapi_checkin_state(s)
	st = bs.newapi_state(s)
	assert st['running'] is False, '进程重启后不该以为还在跑'
	assert st['date'] == '2026-08-11' and st['total'] == 3


# ===== 自动签到开关 =====


def test_站点开关合并成扁平设置(sandbox):
	write_sites(
		sandbox,
		[
			{'id': 'gorouter', 'label': 'G', 'domain': 'https://g.com', 'auto_checkin': True},
			{'id': 'tabitoken', 'label': 'T', 'domain': 'https://t.com', 'auto_checkin': False},
		],
	)
	merged = bs._all_checkin_settings()
	assert merged['gorouter_auto'] is True
	assert merged['tabitoken_auto'] is False
	assert 'anyrouter_auto' in merged and 'agentrouter_auto' in merged


def test_更新站点开关写回站点文件(sandbox):
	write_sites(sandbox, [{'id': 'tabitoken', 'label': 'T', 'domain': 'https://t.com', 'auto_checkin': True}])
	out = asyncio.run(bs.update_checkin_settings({'tabitoken_auto': False}))
	assert out['settings']['tabitoken_auto'] is False
	assert json.loads((sandbox / 'newapi_sites.json').read_text())[0]['auto_checkin'] is False


def test_旧的gorouter_auto开关迁移到站点(sandbox, monkeypatch, tmp_path):
	"""通用化之前 gorouter 的开关存在 checkin_settings.json，迁移时不能把用户关掉的开关悄悄打开。"""
	settings_file = tmp_path / 'checkin_settings.json'
	settings_file.write_text(json.dumps({'anyrouter_auto': False, 'gorouter_auto': False}), encoding='utf-8')
	monkeypatch.setattr(bs, 'CHECKIN_SETTINGS_FILE', settings_file)
	write_sites(sandbox, [{'id': 'gorouter', 'label': 'G', 'domain': 'https://g.com', 'auto_checkin': True}])
	monkeypatch.setitem(bs.checkin_settings, 'anyrouter_auto', True)

	bs.load_checkin_settings()

	assert bs.checkin_settings['anyrouter_auto'] is False
	assert json.loads((sandbox / 'newapi_sites.json').read_text())[0]['auto_checkin'] is False
	assert 'gorouter_auto' not in json.loads(settings_file.read_text()), '迁移完要去掉旧键，否则每次启动都覆盖站点里的新值'


# ===== 接口层 =====


def test_未知站点返回明确错误(sandbox):
	write_sites(sandbox, [])
	for coro in (
		bs.get_site_accounts('nope'),
		bs.query_site('nope'),
		bs.site_checkin_start('nope'),
		bs.site_turnstile('nope'),
		bs.site_checkin_status('nope'),
		bs.site_checkin_sync('nope'),
	):
		out = asyncio.run(coro)
		assert out['success'] is False and 'nope' in out['error']


def test_保存站点拒绝重复id(sandbox):
	out = asyncio.run(
		bs.save_sites({'sites': [{'id': 'a', 'label': 'A', 'domain': 'https://a.com'}, {'id': 'a', 'label': 'B', 'domain': 'https://b.com'}]})
	)
	assert out['success'] is False and '重复' in out['error']


def test_保存站点拒绝非法id与裸域名(sandbox):
	bad_id = asyncio.run(bs.save_sites({'sites': [{'id': 'a b/c', 'label': 'A', 'domain': 'https://a.com'}]}))
	assert bad_id['success'] is False
	bad_domain = asyncio.run(bs.save_sites({'sites': [{'id': 'a', 'label': 'A', 'domain': 'a.com'}]}))
	assert bad_domain['success'] is False and 'http' in bad_domain['error']


def test_保存站点会去掉域名末尾斜杠(sandbox):
	out = asyncio.run(bs.save_sites({'sites': [{'id': 'a', 'label': 'A', 'domain': 'https://a.com/'}]}))
	assert out['sites'][0]['domain'] == 'https://a.com', '末尾斜杠会让拼出的路径变成 //api/...'


def test_同步状态用未挂turnstile的get核对(sandbox, monkeypatch):
	"""浏览器脚本签完后，后端必须靠 GET 读真实状态，而不是相信脚本的自报。"""
	s = site()
	bs.save_newapi_accounts(
		s,
		[
			bs.NewapiAccountItem(name='n1', access_token='t', user_id='1'),
			bs.NewapiAccountItem(name='n2', access_token='t', user_id='2'),
		],
	)
	write_sites(sandbox, [s.model_dump()])
	methods = []

	async def fake_request(s_, method, path, headers, json_body=None):
		methods.append((method, path))
		if path == s_.sign_in_path:
			checked = headers[s_.api_user_key] == '1'
			return FakeResponse(
				payload={'success': True, 'data': {'stats': {'checked_in_today': checked, 'total_checkins': 3, 'total_quota': 0}}}
			)
		return FakeResponse(payload={'success': True, 'data': {'quota': 500000, 'used_quota': 0}})

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	monkeypatch.setattr(bs, 'record_account_usage', lambda *a: None)
	out = asyncio.run(bs.site_checkin_sync('tabitoken'))

	assert out['checked_in'] == 1 and out['total'] == 2
	assert all(m == 'GET' for m, _ in methods), '同步只读状态，绝不能顺手 POST 触发签到'
	assert bs.newapi_state(s)['trigger'] == 'browser'
	assert bs.newapi_state(s)['accounts']['n2']['status'] == 'failed'


def test_探测非newapi站点给出可读原因(sandbox, monkeypatch):
	async def fake_request(s_, method, path, headers, json_body=None):
		return FakeResponse(body='<html>hi</html>', content_type='text/html')

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	out = asyncio.run(bs.probe_site({'domain': 'https://example.com'}))
	assert out['success'] is False and 'JSON' in out['error']


def test_探测newapi站点回传版本与turnstile(sandbox, monkeypatch):
	async def fake_request(s_, method, path, headers, json_body=None):
		return FakeResponse(
			payload={
				'data': {
					'version': 'v1.0.0-rc.23',
					'system_name': 'TaBiAI',
					'checkin_enabled': True,
					'turnstile_check': True,
					'quota_per_unit': 500000,
				}
			}
		)

	monkeypatch.setattr(bs, 'newapi_request', fake_request)
	out = asyncio.run(bs.probe_site({'domain': 'https://tabitoken.com/'}))
	assert out['success'] is True
	assert out['info']['system_name'] == 'TaBiAI' and out['info']['turnstile_check'] is True


def test_探测拒绝裸域名(sandbox):
	out = asyncio.run(bs.probe_site({'domain': 'tabitoken.com'}))
	assert out['success'] is False and 'http' in out['error']
