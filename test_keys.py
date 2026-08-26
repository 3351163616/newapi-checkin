"""密钥（new-api 令牌）管理层的离线单测。

覆盖四类账号的 ref 解析、两种列表形态的兼容、脱敏 key 的取全量与缓存、
额度换算、创建/删除，以及取全量 key 的限流处理。

不发任何上游请求 —— 全部用假 Response + 假 request 闭包驱动。
    .venv/bin/python -m pytest test_keys.py -q
"""

import asyncio
import json
import time

import pytest

import balance_server as bs


class FakeResponse:
	def __init__(self, status_code=200, payload=None, body=None):
		self.status_code = status_code
		self.text = body if body is not None else json.dumps(payload or {})
		self.headers = {'content-type': 'application/json'}
		self.cookies = {}

	def json(self):
		return json.loads(self.text)


def ok(data):
	return FakeResponse(payload={'success': True, 'message': '', 'data': data})


def fail(message):
	return FakeResponse(payload={'success': False, 'message': message})


class RecordingCtx(bs.KeyCtx):
	"""按 (method, path) 给出预设响应，并记录调用序列"""

	def __init__(self, routes, ref='site:x:0', unit=500000):
		self.calls = []
		routes = dict(routes)

		async def _req(method, path, json_body=None):
			self.calls.append((method, path.split('?')[0], json_body))
			key = (method, path.split('?')[0])
			resp = routes.get(key)
			if resp is None:
				raise AssertionError(f'未预设的请求: {method} {path}')
			return resp(json_body) if callable(resp) else resp

		super().__init__(ref, 'acc', 'TestSite', _req, unit)


@pytest.fixture(autouse=True)
def _keys_cache_tmp(monkeypatch, tmp_path):
	"""列表缓存隔离：每个测试独立临时文件 + 清空内存缓存，别把假数据写进真实 keys_cache.json"""
	monkeypatch.setattr(bs, 'KEYS_CACHE_FILE', tmp_path / 'keys_cache.json')
	bs._keys_list_cache.clear()
	yield
	bs._keys_list_cache.clear()


def newapi_token(id=1, name='k', key='ABCD**********WXYZ', **kw):
	base = {
		'id': id,
		'user_id': 1,
		'key': key,
		'status': 1,
		'name': name,
		'created_time': 1786000000,
		'accessed_time': 1786100000,
		'expired_time': -1,
		'remain_quota': 500000,
		'unlimited_quota': False,
		'model_limits_enabled': False,
		'model_limits': '',
		'allow_ips': '',
		'used_quota': 250000,
		'group': 'default',
	}
	base.update(kw)
	return base


@pytest.fixture(autouse=True)
def clean_cache():
	bs._key_value_cache.clear()
	bs._agentrouter_key_sessions.clear()
	bs._keys_reveal_until.clear()
	yield
	bs._key_value_cache.clear()
	bs._agentrouter_key_sessions.clear()
	bs._keys_reveal_until.clear()


# ===== 列表形态兼容 =====


def test_新版分页形态与旧版数组形态都能解析():
	# gorouter / tabitoken / agentrouter 是 {page,total,items}
	assert bs._parse_token_items({'page': 1, 'total': 2, 'items': [{'id': 1}, {'id': 2}]}) == [{'id': 1}, {'id': 2}]
	# anyrouter（v0.0.0）直接是数组
	assert bs._parse_token_items([{'id': 3}]) == [{'id': 3}]
	# 空与异常都不该抛
	assert bs._parse_token_items(None) == []
	assert bs._parse_token_items({'items': None}) == []


def test_额度按站点单位换算成美元且识别脱敏():
	row = bs._token_row(newapi_token(remain_quota=1500000, used_quota=250000), 500000)
	assert row['remain_quota'] == 3.0 and row['used_quota'] == 0.5
	assert row['masked'] is True, 'key 里有 * 就是脱敏值，复制前必须换成全量'
	# 单位不同的站点换算也要跟着变
	assert bs._token_row(newapi_token(remain_quota=1500000), 1000000)['remain_quota'] == 1.5


def test_明文key不标记脱敏():
	row = bs._token_row(newapi_token(key='NUOQi1' + 'x' * 42), 500000)
	assert row['masked'] is False


def test_列表带出全部可展示字段():
	row = bs._token_row(newapi_token(group='vip', model_limits_enabled=True, model_limits='gpt-4', allow_ips='1.2.3.4'), 500000)
	for f in (
		'id', 'name', 'key', 'masked', 'status', 'unlimited_quota', 'remain_quota', 'used_quota',
		'expired_time', 'created_time', 'accessed_time', 'group', 'model_limits_enabled',
		'model_limits', 'allow_ips',
	):
		assert f in row, f'{f} 没带出来，前端就展示不了'
	assert row['group'] == 'vip' and row['model_limits'] == 'gpt-4' and row['allow_ips'] == '1.2.3.4'


# ===== 取全量 key =====


def test_默认就取全量key():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': [newapi_token(id=1)], 'total': 1}),
		('POST', '/api/token/batch/keys'): ok({'keys': {'1': 'FULLKEY1'}}),
	})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['keys'][0]['key'] == 'FULLKEY1' and r['keys'][0]['masked'] is False
	assert [c[0] for c in ctx.calls] == ['GET', 'POST'], '前端要直接展示完整密钥，列表自带脱敏值就必须顺手取全量'


def test_批量接口一次取完():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': [newapi_token(id=1), newapi_token(id=2)], 'total': 2}),
		('POST', '/api/token/batch/keys'): ok({'keys': {'1': 'FULLKEY1', '2': 'FULLKEY2'}}),
	})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert [k['key'] for k in r['keys']] == ['FULLKEY1', 'FULLKEY2']
	assert all(not k['masked'] for k in r['keys'])
	batch = [c for c in ctx.calls if c[1] == '/api/token/batch/keys']
	assert len(batch) == 1, '必须一次批量取完 —— 逐个取会瞬间打满 20 次/20 分钟的配额'
	assert batch[0][2] == {'ids': [1, 2]}


def test_取到的key进缓存后不再打上游():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': [newapi_token(id=7)], 'total': 1}),
		('POST', '/api/token/batch/keys'): ok({'keys': {'7': 'FULLKEY7'}}),
	})
	asyncio.run(bs.list_account_keys(ctx))
	before = len(ctx.calls)
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['keys'][0]['key'] == 'FULLKEY7'
	assert ctx.calls[before:] == [], '全量列表落缓存后，第二次读取零上游请求'


def test_限流时给出人话提示而不是静默失败():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': [newapi_token()], 'total': 1}),
		('POST', '/api/token/batch/keys'): FakeResponse(status_code=429),
	})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['success'] and '限流' in r['warning']
	assert r['keys'][0]['masked'] is True, '拿不到就保持脱敏，不能显示成半截值'


def test_没有批量接口时退回逐个取():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': [newapi_token(id=3)], 'total': 1}),
		('POST', '/api/token/batch/keys'): FakeResponse(status_code=404),
		('POST', '/api/token/3/key'): ok({'key': 'FULLKEY3'}),
	})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['keys'][0]['key'] == 'FULLKEY3' and r['warning'] is None


def test_没有批量接口且密钥过多时拒绝逐个取():
	items = [newapi_token(id=i) for i in range(1, 8)]
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': items, 'total': len(items)}),
		('POST', '/api/token/batch/keys'): FakeResponse(status_code=404),
	})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert '逐个查看' in r['warning']
	assert not any(c[1].endswith('/key') for c in ctx.calls), '7 个密钥逐个取会吃掉三分之一的配额'


def test_明文站点不触发取密钥请求():
	ctx = RecordingCtx({('GET', '/api/token/'): ok({'items': [newapi_token(key='PLAINKEY')], 'total': 1})})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['keys'][0]['key'] == 'PLAINKEY' and r['warning'] is None
	assert [c[0] for c in ctx.calls] == ['GET'], 'agentrouter 这类直接给明文的站点不该多打一次'


# ===== 列表错误处理 =====


def test_上游报错原样透出():
	ctx = RecordingCtx({('GET', '/api/token/'): fail('用户已被封禁')})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['success'] is False and r['error'] == '用户已被封禁'


def test_非JSON响应不抛异常():
	ctx = RecordingCtx({('GET', '/api/token/'): FakeResponse(body='<html>challenge</html>')})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['success'] is False and 'JSON' in r['error']


def test_超过一页时标记截断():
	ctx = RecordingCtx({('GET', '/api/token/'): ok({'items': [newapi_token(key='PLAIN')], 'total': 300})})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['truncated'] is True and r['total'] == 300


# ===== 创建 / 删除 =====


def use_ctx(monkeypatch, ctx):
	"""让 keys_create / keys_delete 解析到指定的假 ctx"""

	async def _resolve(ref):
		return ctx, None

	monkeypatch.setattr(bs, 'resolve_key_ctx', _resolve)
	return ctx


def test_创建密钥把美元额度换算成原始额度(monkeypatch):
	captured = {}

	def _add(body):
		captured.update(body)
		return ok(None)

	ctx = use_ctx(monkeypatch, RecordingCtx({
		('POST', '/api/token/'): _add,
		('GET', '/api/token/'): ok({'items': [newapi_token(key='NEWPLAIN')], 'total': 1}),
	}))
	r = asyncio.run(bs.keys_create({'ref': ctx.ref, 'name': 'k1', 'unlimited_quota': False, 'remain_quota': 5}))
	assert r['success'] is True
	assert captured['remain_quota'] == 5 * 500000, '上游要的是原始额度，前端传的是美元'
	assert captured['unlimited_quota'] is False and captured['expired_time'] == -1


def test_创建无限额度密钥不写额度(monkeypatch):
	captured = {}
	ctx = use_ctx(monkeypatch, RecordingCtx({
		('POST', '/api/token/'): lambda b: (captured.update(b), ok(None))[1],
		('GET', '/api/token/'): ok({'items': [], 'total': 0}),
	}))
	asyncio.run(bs.keys_create({'ref': ctx.ref, 'name': 'k'}))
	assert captured['unlimited_quota'] is True and captured['remain_quota'] == 0


def test_创建后重新列出以便拿到新key的完整值(monkeypatch):
	ctx = use_ctx(monkeypatch, RecordingCtx({
		('POST', '/api/token/'): ok(None),
		('GET', '/api/token/'): ok({'items': [newapi_token(id=9)], 'total': 1}),
		('POST', '/api/token/batch/keys'): ok({'keys': {'9': 'BRANDNEW'}}),
	}))
	r = asyncio.run(bs.keys_create({'ref': ctx.ref, 'name': 'k'}))
	assert r['account']['keys'][0]['key'] == 'BRANDNEW', '上游 AddToken 不回 key，必须重列 + 取全量'


def test_创建时名称为空或过长被拦下(monkeypatch):
	ctx = use_ctx(monkeypatch, RecordingCtx({('GET', '/api/token/'): ok({'items': [], 'total': 0})}))
	assert asyncio.run(bs.keys_create({'ref': ctx.ref, 'name': '  '}))['success'] is False
	assert asyncio.run(bs.keys_create({'ref': ctx.ref, 'name': 'x' * 51}))['success'] is False
	assert ctx.calls == [], '本地能判的错不该打上游'


def test_删除后清掉该key的缓存(monkeypatch):
	bs._key_value_cache['site:x:0:5'] = 'STALE'
	use_ctx(monkeypatch, RecordingCtx({
		('DELETE', '/api/token/5'): ok(None),
		('GET', '/api/token/'): ok({'items': [], 'total': 0}),
	}))
	r = asyncio.run(bs.keys_delete({'ref': 'site:x:0', 'id': 5}))
	assert r['success'] is True
	assert 'site:x:0:5' not in bs._key_value_cache, '缓存不清掉，同 id 复用时会复制到已删的旧 key'


def test_删除失败时不清缓存(monkeypatch):
	bs._key_value_cache['site:x:0:5'] = 'KEEP'
	use_ctx(monkeypatch, RecordingCtx({('DELETE', '/api/token/5'): fail('无权操作')}))
	r = asyncio.run(bs.keys_delete({'ref': 'site:x:0', 'id': 5}))
	assert r['success'] is False
	assert bs._key_value_cache['site:x:0:5'] == 'KEEP'


def test_删除必须带id():
	assert asyncio.run(bs.keys_delete({'ref': 'site:x:0'}))['success'] is False


# ===== ref 解析 =====


def test_未知或越界的ref给出明确错误(monkeypatch):
	monkeypatch.setattr(bs, 'load_token_accounts', lambda: [])
	assert asyncio.run(bs.resolve_key_ctx('wat'))[1].startswith('未知的账号类型')
	assert '无效' in asyncio.run(bs.resolve_key_ctx('token:5'))[1]
	assert '无效' in asyncio.run(bs.resolve_key_ctx('token:abc'))[1]
	assert '不存在' in asyncio.run(bs.resolve_key_ctx('site:nope:0'))[1]
	assert '无效' in asyncio.run(bs.resolve_key_ctx('site:nope'))[1]


def test_站点账号的ref解析出该站点的单位与显示名(monkeypatch):
	s = bs.NewapiSite(id='demo', label='Demo', domain='https://demo.io', quota_per_unit=1000000)
	monkeypatch.setattr(bs, 'get_newapi_site', lambda sid: s if sid == 'demo' else None)
	monkeypatch.setattr(bs, 'load_newapi_accounts', lambda site: [bs.NewapiAccountItem(name='a0', access_token='T', user_id='1')])
	ctx, err = asyncio.run(bs.resolve_key_ctx('site:demo:0'))
	assert err is None and ctx.provider == 'Demo' and ctx.quota_per_unit == 1000000 and ctx.name == 'a0'


def test_anyrouter两类账号都能解析且cookie账号不带Bearer(monkeypatch):
	monkeypatch.setattr(bs, 'load_token_accounts', lambda: [bs.TokenAccountItem(name='t0', access_token='TOK', user_id='11')])
	monkeypatch.setattr(bs, 'load_cookie_accounts', lambda: [bs.AccountItem(name='c0', cookies={'session': 'S'}, api_user='22')])

	async def _no_waf():
		return {'acw_tc': 'x'}

	monkeypatch.setattr(bs, '_get_waf_cookies_if_needed', _no_waf)

	sent = {}

	async def fake_request(method, url, headers, cookies=None, json_body=None):
		sent['headers'], sent['cookies'] = headers, cookies
		return ok({'items': [], 'total': 0})

	monkeypatch.setattr(bs, 'anyrouter_request', fake_request)
	monkeypatch.setattr(bs, 'anyrouter_block_reason', lambda resp: None)

	ctx, err = asyncio.run(bs.resolve_key_ctx('token:0'))
	assert err is None and ctx.provider == 'AnyRouter'
	asyncio.run(ctx.request('GET', '/api/token/'))
	assert sent['headers']['Authorization'] == 'Bearer TOK'
	assert sent['headers']['new-api-user'] == '11'

	ctx, err = asyncio.run(bs.resolve_key_ctx('cookie:0'))
	assert err is None
	asyncio.run(ctx.request('GET', '/api/token/'))
	assert 'Authorization' not in sent['headers'], 'cookie 账号靠 session 认证，带 Bearer 反而多余'
	assert sent['cookies']['session'] == 'S' and sent['cookies']['acw_tc'] == 'x'


def test_anyrouter被限流时报错而不是当成空列表(monkeypatch):
	monkeypatch.setattr(bs, 'load_token_accounts', lambda: [bs.TokenAccountItem(name='t0', access_token='TOK', user_id='11')])

	async def _no_waf():
		return {}

	monkeypatch.setattr(bs, '_get_waf_cookies_if_needed', _no_waf)

	async def fake_request(method, url, headers, cookies=None, json_body=None):
		return FakeResponse(status_code=403, body='Denied by http_ratelimit')

	monkeypatch.setattr(bs, 'anyrouter_request', fake_request)
	monkeypatch.setattr(bs, 'anyrouter_block_reason', lambda resp: ('ratelimit', '出口 IP 被限流'))

	ctx, err = asyncio.run(bs.resolve_key_ctx('token:0'))
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['success'] is False and '限流' in r['error']


def test_agentrouter复用缓存的session不重复登录(monkeypatch):
	acc = bs.LoginAccountItem(name='a', username='u', password='p')
	logins = []

	def fake_session_call(account):
		logins.append(account.name)
		return {'session': 'S'}, '105944'

	async def fake_session(account):
		return fake_session_call(account)

	monkeypatch.setattr(bs, 'load_login_accounts', lambda: [acc])
	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	ctx1, e1 = asyncio.run(bs.resolve_key_ctx('login:0'))
	ctx2, e2 = asyncio.run(bs.resolve_key_ctx('login:0'))
	assert e1 is None and e2 is None
	assert ctx1.provider == 'AgentRouter' and ctx1.name == 'a'
	assert len(logins) == 2, '每次解析都会问一次 session（缓存在 _agentrouter_session 内部）'


def test_agentrouter登录失败时不抛异常而是返回错误(monkeypatch):
	monkeypatch.setattr(bs, 'load_login_accounts', lambda: [bs.LoginAccountItem(name='a', username='u', password='p')])

	async def boom(account):
		raise RuntimeError('登录失败: 密码错误')

	monkeypatch.setattr(bs, '_agentrouter_session', boom)
	ctx, err = asyncio.run(bs.resolve_key_ctx('login:0'))
	assert ctx is None and '密码错误' in err


# ===== 批量端点 =====


def test_批量列出时坏账号不影响好账号(monkeypatch):
	async def fake_resolve(ref):
		if ref == 'bad':
			return None, '账号引用 bad 无效'
		return RecordingCtx({('GET', '/api/token/'): ok({'items': [newapi_token(key='PLAIN')], 'total': 1})}, ref=ref), None

	monkeypatch.setattr(bs, 'resolve_key_ctx', fake_resolve)
	r = asyncio.run(bs.keys_list({'refs': ['site:a:0', 'bad', 'site:a:1']}))
	assert r['success'] is True
	assert [a['success'] for a in r['accounts']] == [True, False, True]
	assert r['accounts'][1]['error'] == '账号引用 bad 无效'


def test_批量列出保持传入顺序(monkeypatch):
	async def fake_resolve(ref):
		return RecordingCtx({('GET', '/api/token/'): ok({'items': [newapi_token(key='PLAIN')], 'total': 0})}, ref=ref), None

	monkeypatch.setattr(bs, 'resolve_key_ctx', fake_resolve)
	refs = [f'site:a:{i}' for i in range(8)]
	r = asyncio.run(bs.keys_list({'refs': refs}))
	assert [a['ref'] for a in r['accounts']] == refs, '顺序乱了前端就对不上账号'


# ===== 取全量的跨账号协调：撞限流换出口 / 熔断 =====


class _FakeRotator:
	"""替身：不碰真 mihomo，记录切换与恢复次数"""

	def __init__(self, ok=True):
		self.switched = 0
		self.restored = 0
		self.ok = ok

	async def prepare(self):
		return True

	async def next_exit(self):
		self.switched += 1
		return self.ok

	async def restore(self):
		self.restored += 1


def _reveal_ctx(rotator):
	"""直连 batch 必 429、proxied 一切正常的站点账号 ctx"""
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok({'items': [newapi_token(id=1)], 'total': 1}),
		('POST', '/api/token/batch/keys'): FakeResponse(status_code=429),
	})

	async def _proxied(method, path, json_body=None):
		return ok({'keys': {'1': 'FULL1'}})

	ctx.proxied_request = _proxied
	return ctx


def test_撞限流自动换出口重试并回写缓存(monkeypatch):
	rotator = _FakeRotator(ok=True)
	monkeypatch.setattr(bs, '_KeysExitRotator', lambda: rotator)
	ctx = _reveal_ctx(rotator)
	use_ctx(monkeypatch, ctx)

	r = asyncio.run(bs.keys_list({'refs': [ctx.ref]}))
	acc = r['accounts'][0]
	assert acc['keys'][0]['key'] == 'FULL1' and acc['keys'][0]['masked'] is False, '换出口后应拿到全量'
	assert rotator.switched == 1 and rotator.restored == 1, '切了出口且结束恢复了原节点'
	assert [c for c in ctx.calls if c[1] == '/api/token/batch/keys' and c[0] == 'POST'], '直连先撞了一次限流'
	assert bs._keys_list_cache['site:x:0|acc']['result']['keys'][0]['key'] == 'FULL1', '拿到全量要回写缓存'


def test_撞限流出口用尽则熔断且期间零上游(monkeypatch):
	rotator = _FakeRotator(ok=False)
	monkeypatch.setattr(bs, '_KeysExitRotator', lambda: rotator)
	ctx = _reveal_ctx(rotator)
	use_ctx(monkeypatch, ctx)

	r = asyncio.run(bs.keys_list({'refs': [ctx.ref]}))
	assert '出口已用尽' in r['accounts'][0]['warning']
	assert bs._keys_reveal_until.get('site:x') > 0, '出口用尽要熔断 20 分钟'

	batch_calls = len([c for c in ctx.calls if c[1] == '/api/token/batch/keys'])
	r2 = asyncio.run(bs.keys_list({'refs': [ctx.ref]}))
	assert '自动恢复' in r2['accounts'][0]['warning'], '熔断期内给出恢复时间而不是硬报错'
	assert len([c for c in ctx.calls if c[1] == '/api/token/batch/keys']) == batch_calls, '熔断期内不该再打上游'


def test_agentrouter轮换进行时不切节点(monkeypatch):
	rotator = _FakeRotator(ok=True)
	monkeypatch.setattr(bs, '_KeysExitRotator', lambda: rotator)
	ctx = _reveal_ctx(rotator)
	use_ctx(monkeypatch, ctx)

	async def scenario():
		await bs._balances_query_lock.acquire()
		try:
			return await bs.keys_list({'refs': [ctx.ref]})
		finally:
			bs._balances_query_lock.release()

	r = asyncio.run(scenario())
	assert '限流' in r['accounts'][0]['warning'], '不轮换时直连 429 就只能标注'
	assert rotator.switched == 0, 'agentrouter 的轮换在跑时不能动 mihomo 节点'
	assert r['accounts'][0]['keys'][0]['masked'] is True, '没轮换就拿不到全量，保持脱敏'


def test_没有refs时直接报错():
	assert asyncio.run(bs.keys_list({'refs': []}))['success'] is False
	assert asyncio.run(bs.keys_list({}))['success'] is False


# ===== 列表缓存（密钥很少变，默认读缓存零上游请求） =====


def test_列表结果落缓存_第二次零上游():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok([newapi_token(id=1)]),
		('POST', '/api/token/batch/keys'): ok({'keys': {'1': 'FULL1'}}),
	})
	r1 = asyncio.run(bs.list_account_keys(ctx))
	assert len(ctx.calls) == 2
	r2 = asyncio.run(bs.list_account_keys(ctx))
	assert len(ctx.calls) == 2, '密钥很少变，第二次读取应直接回缓存，不打上游'
	assert r2['cached'] is True and r2['cached_at']
	assert r2['keys'] == r1['keys'] and r2['success'] is True


def test_refresh参数绕过缓存():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok([newapi_token(id=1)]),
		('POST', '/api/token/batch/keys'): ok({'keys': {'1': 'FULL1'}}),
	})
	asyncio.run(bs.list_account_keys(ctx))
	r2 = asyncio.run(bs.list_account_keys(ctx, refresh=True))
	# 列表重查了，但全量值命中 _key_value_cache（同一个 id 的 key 不会变），不再花限流配额
	assert [c[0] for c in ctx.calls] == ['GET', 'POST', 'GET'], '强制重查必须重查列表'
	assert not r2.get('cached')


def test_缓存里存全量列表_第二次零上游直接可见():
	# 前端直接展示完整密钥：缓存必须存成品，否则每次打开都要重打限流接口
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok([newapi_token(id=7, key='ABCD**********WXYZ')]),
		('POST', '/api/token/batch/keys'): ok({'keys': {'7': 'ABCD1234EFGH5678WXYZ'}}),
	})
	r1 = asyncio.run(bs.list_account_keys(ctx))
	assert r1['keys'][0]['key'] == 'ABCD1234EFGH5678WXYZ'
	calls = len(ctx.calls)
	r2 = asyncio.run(bs.list_account_keys(ctx))
	assert r2['cached'] and r2['keys'][0]['key'] == 'ABCD1234EFGH5678WXYZ' and r2['keys'][0]['masked'] is False
	assert len(ctx.calls) == calls, '第二次命中缓存不该再打上游'


def test_旧缓存里的脱敏条目读取时自动补全并回写():
	# 兼容只存过脱敏列表的旧 keys_cache.json：命中后发现还有脱敏值就补取一次全量并回写
	row = bs._token_row(newapi_token(id=7), 500000)
	bs._keys_list_cache['site:x:0|acc'] = {
		'ts': time.time(),
		'result': {'ref': 'site:x:0', 'name': 'acc', 'provider': 'TestSite', 'success': True, 'keys': [row], 'total': 1, 'truncated': False, 'warning': None},
	}
	ctx = RecordingCtx({('POST', '/api/token/batch/keys'): ok({'keys': {'7': 'FULL7'}})})
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['cached'] is True and r['keys'][0]['key'] == 'FULL7' and r['keys'][0]['masked'] is False
	assert [c[0] for c in ctx.calls] == ['POST'], '命中缓存不该重打列表接口'
	assert 'FULL7' in json.dumps(bs._keys_list_cache['site:x:0|acc']['result']), '补全后要回写，下次连补取都省了'


def test_失败的结果不进缓存():
	ctx = RecordingCtx({('GET', '/api/token/'): fail('boom')})
	asyncio.run(bs.list_account_keys(ctx))
	asyncio.run(bs.list_account_keys(ctx))
	assert len(ctx.calls) == 2, '失败不该被缓存，否则错误会一直顶着'


def test_缓存按ref加账号名隔离():
	# ref 是位置索引，账号增删后同一个 ref 可能指向别的账号 —— 名字对不上就当未命中
	async def req(method, path, json_body=None):
		return ok([newapi_token(id=1, key='PLAIN')])

	asyncio.run(bs.list_account_keys(bs.KeyCtx('site:x:0', 'acc', 'TestSite', req)))
	asyncio.run(bs.list_account_keys(bs.KeyCtx('site:x:0', '别的账号', 'TestSite', req)))
	assert set(bs._keys_list_cache.keys()) == {'site:x:0|acc', 'site:x:0|别的账号'}


def test_缓存落盘重启后仍可用():
	ctx = RecordingCtx({
		('GET', '/api/token/'): ok([newapi_token(id=1)]),
		('POST', '/api/token/batch/keys'): ok({'keys': {'1': 'FULL1'}}),
	})
	asyncio.run(bs.list_account_keys(ctx))
	assert bs.KEYS_CACHE_FILE.exists(), '不落盘的话重启后第一次打开弹窗又要全量重查'
	bs._keys_list_cache.clear()
	bs.load_keys_list_cache()
	r = asyncio.run(bs.list_account_keys(ctx))
	assert r['cached'] is True and len(ctx.calls) == 2
	assert r['keys'][0]['key'] == 'FULL1', '落盘的是全量成品，重启后打开直接可见'
