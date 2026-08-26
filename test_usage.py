"""今日用量快照的离线单测：按站点隔离的 key、旧数据迁移、AgentRouter 余额来源。

不发任何上游请求 —— 用假 Session / 假 Response 驱动。
    .venv/bin/python -m pytest test_usage.py -q
"""

import asyncio
import json
from datetime import datetime

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


class FakeSession:
	"""按 (method, url 关键字) 返回预设响应，并记录调用"""

	def __init__(self, routes, cookies=None):
		self.routes = routes
		self.cookies = dict(cookies or {})
		self.calls = []

	def _pick(self, method, url):
		self.calls.append((method, url))
		for frag, resp in self.routes.items():
			if frag in url:
				return resp
		raise AssertionError(f'未预设的请求: {method} {url}')

	def post(self, url, **kw):
		return self._pick('POST', url)

	def get(self, url, **kw):
		return self._pick('GET', url)

	def request(self, method, url, **kw):
		return self._pick(method, url)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
	monkeypatch.setattr(bs, 'USAGE_FILE', tmp_path / 'daily_usage.json')
	return tmp_path / 'daily_usage.json'


# ===== key 按站点隔离 =====


def test_key_带站点前缀():
	assert bs.usage_key('gorouter', '5') == 'gorouter:5'
	assert bs.usage_key('agentrouter', '5') == 'agentrouter:5'


def test_同名账号在不同站点各记各的(usage_file):
	bs.record_account_usage('gorouter', '5', 171.58, 106.33)
	bs.record_account_usage('agentrouter', '5', 0.0, 2025.0)
	day = next(iter(bs.load_usage_data().values()))
	assert day['gorouter:5']['quota'] == 106.33
	assert day['agentrouter:5']['quota'] == 2025.0, '这就是之前 AgentRouter 显示 GoRouter 余额的原因'


def test_基线只认当天第一次写入(usage_file):
	bs.record_account_usage('gorouter', '9', 10.7, 127.17)
	bs.record_account_usage('gorouter', '9', 12.4, 125.47)  # 当天第二次（签到后再记一次）
	day = next(iter(bs.load_usage_data().values()))
	e = day['gorouter:9']
	assert e['used0'] == 10.7, '基线被覆盖的话今日用量永远算成 0'
	assert e['used'] == 12.4, 'used 要跟着更新，否则余额显示是旧的'


# ===== 旧数据迁移 =====


@pytest.fixture
def owners(monkeypatch):
	"""伪造账号归属：gorouter 有 5/6，agentrouter 有 5/tb，anyrouter 有 a1"""
	site = bs.NewapiSite(id='gorouter', label='GoRouter', domain='https://gorouter.app')
	monkeypatch.setattr(bs, 'load_newapi_sites', lambda: [site])
	monkeypatch.setattr(bs, 'load_newapi_accounts', lambda s: [
		bs.NewapiAccountItem(name='5', access_token='T', user_id='1'),
		bs.NewapiAccountItem(name='6', access_token='T', user_id='2'),
	])
	monkeypatch.setattr(bs, 'load_token_accounts', lambda: [])
	monkeypatch.setattr(bs, 'load_cookie_accounts', lambda: [bs.AccountItem(name='a1', cookies={}, api_user='9')])
	monkeypatch.setattr(bs, 'load_login_accounts', lambda: [
		bs.LoginAccountItem(name='5', username='u', password='p'),
		bs.LoginAccountItem(name='tb', username='u', password='p'),
	])


def test_归属唯一的账号直接改写(owners):
	data = {'2026-08-17': {'6': {'used': 1, 'quota': 2, 'used0': 1}, 'tb': {'used': 3, 'quota': 4, 'used0': 3}}}
	out, migrated, orphaned = bs.migrate_usage_keys(data)
	assert out['2026-08-17']['gorouter:6']['quota'] == 2
	assert out['2026-08-17']['agentrouter:tb']['quota'] == 4
	assert (migrated, orphaned) == (2, 0)


def test_重名的按优先级归给站点(owners):
	data = {'2026-08-17': {'5': {'used': 9, 'quota': 8, 'used0': 9}}}
	out, _, _ = bs.migrate_usage_keys(data)
	assert 'gorouter:5' in out['2026-08-17'], '站点的值由 0 点快照每天写入，比 agentrouter 的可信'
	assert 'agentrouter:5' not in out['2026-08-17'], 'AgentRouter 的历史从迁移当天重新开始'
	assert '5' not in out['2026-08-17'], '裸 key 必须清掉，否则两边还会都读到它'


def test_认不出归属的条目原样保留(owners):
	data = {'2026-08-17': {'早就删掉的账号': {'used': 1, 'quota': 2, 'used0': 1}}}
	out, migrated, orphaned = bs.migrate_usage_keys(data)
	assert out['2026-08-17']['早就删掉的账号']['quota'] == 2, '认不出来就别乱认，也别丢'
	assert (migrated, orphaned) == (0, 1)


def test_已迁移过的数据不再改动(owners):
	data = {'2026-08-17': {'gorouter:6': {'used': 1, 'quota': 2, 'used0': 1}}}
	out, migrated, _ = bs.migrate_usage_keys(data)
	assert out == data and migrated == 0, '迁移必须幂等，否则每次启动都重写一遍'


def test_新旧key同时存在时不覆盖新的(owners):
	data = {'2026-08-17': {'gorouter:6': {'used': 100, 'quota': 1, 'used0': 100}, '6': {'used': 1, 'quota': 2, 'used0': 1}}}
	out, _, _ = bs.migrate_usage_keys(data)
	assert out['2026-08-17']['gorouter:6']['used'] == 100, '新格式的数据更可信，不该被旧裸 key 盖掉'


def test_迁移不动其它日期的结构(owners):
	data = {'2026-08-16': {'6': {'used': 1, 'quota': 2, 'used0': 1}}, '2026-08-17': {}}
	out, _, _ = bs.migrate_usage_keys(data)
	assert set(out.keys()) == {'2026-08-16', '2026-08-17'}
	assert out['2026-08-17'] == {}


# ===== 基线接口 =====


def test_今日基线按新key返回(usage_file, monkeypatch):
	bs.record_account_usage('gorouter', '5', 170.18, 106.33)
	bs.record_account_usage('agentrouter', '5', 0.0, 2025.0)
	r = asyncio.run(bs.get_today_usage())
	assert r['baseline']['gorouter:5'] == 170.18
	assert r['baseline']['agentrouter:5'] == 0.0


# ===== AgentRouter 余额来源 =====


def _patch_session(monkeypatch, session):
	monkeypatch.setattr(bs, '_get_cffi_session', lambda key, proxies=None: session)


LOGIN_OK = FakeResponse(payload={
	'success': True,
	'message': '',
	# 登录响应里 quota/used_quota 存在但恒为 0 —— 实测如此，不能拿来记账
	'data': {'id': 105950, 'username': 'linuxdo_105950', 'quota': 0, 'used_quota': 0, 'checked_in': False},
})
SELF_OK = FakeResponse(payload={
	'success': True,
	'data': {'id': 105950, 'username': 'linuxdo_105950', 'quota': 1012500000, 'used_quota': 5000000},
})


def test_余额取自user_self而不是登录响应(monkeypatch):
	sess = FakeSession({'/api/user/login': LOGIN_OK, '/api/user/self': SELF_OK}, cookies={'session': 'S'})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.query_balance_login(bs.LoginAccountItem(name='6', username='u', password='p')))
	assert r['success'] is True
	assert r['quota'] == 2025.0, '登录响应里是 0，取它就会把余额记成 0'
	assert r['used'] == 10.0
	assert any('/api/user/self' in c[1] for c in sess.calls), '必须额外打一次 /api/user/self'


def test_取余额失败时报错而不是记成0(monkeypatch):
	sess = FakeSession({'/api/user/login': LOGIN_OK, '/api/user/self': FakeResponse(status_code=500)})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.query_balance_login(bs.LoginAccountItem(name='6', username='u', password='p')))
	assert r['success'] is False and '余额' in r['error']
	assert 'HTTP 500' in r['error'], '要说清是哪一步失败，否则下次排查会误判成代码坏了'


def test_签到取余额失败时quota留空以跳过记账(monkeypatch):
	sess = FakeSession({'/api/user/login': LOGIN_OK, '/api/user/self': FakeResponse(status_code=500)})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.sign_in_login(bs.LoginAccountItem(name='6', username='u', password='p')))
	assert r['success'] is True, '签到本身成功了，不该因为读余额失败就判为签到失败'
	assert r['quota'] is None, 'quota 为 None 时调用方会跳过记账，避免 0 污染当天基线'


def test_签到成功时带上真实余额(monkeypatch):
	sess = FakeSession({'/api/user/login': LOGIN_OK, '/api/user/self': SELF_OK})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.sign_in_login(bs.LoginAccountItem(name='6', username='u', password='p')))
	assert r['already_signed'] is False and r['message'] == '签到成功'
	assert r['quota'] == 2025.0 and r['used'] == 10.0


def test_已签到状态仍取自登录响应(monkeypatch):
	login = FakeResponse(payload={
		'success': True,
		'data': {'id': 1, 'username': 'x', 'quota': 0, 'used_quota': 0, 'checked_in': True},
	})
	sess = FakeSession({'/api/user/login': login, '/api/user/self': SELF_OK})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.sign_in_login(bs.LoginAccountItem(name='6', username='u', password='p')))
	assert r['already_signed'] is True and r['message'] == '今日已签到'


def test_读余额时带上new_api_user头(monkeypatch):
	captured = {}

	class Sess(FakeSession):
		def get(self, url, **kw):
			captured.update(kw.get('headers') or {})
			return super().get(url, **kw)

	sess = Sess({'/api/user/self': SELF_OK}, cookies={'session': 'S'})
	_patch_session(monkeypatch, sess)
	r, why = asyncio.run(bs.agentrouter_real_balance({'session': 'S'}, '105950'))
	assert why is None and r['quota'] == 2025.0
	assert captured.get('new-api-user') == '105950', '没有这个头 new-api 一律 401'


def test_没有user_id时不发请求(monkeypatch):
	sess = FakeSession({})
	_patch_session(monkeypatch, sess)
	assert asyncio.run(bs.agentrouter_real_balance({'session': 'S'}, ''))[0] is None
	assert sess.calls == []


# ===== 余额端点 =====


@pytest.fixture
def agent_accounts(monkeypatch):
	accs = [bs.LoginAccountItem(name=n, username='u' + n, password='p') for n in ('a', 'b')]
	monkeypatch.setattr(bs, 'load_login_accounts', lambda: accs)
	bs._agentrouter_key_sessions.clear()
	yield accs
	bs._agentrouter_key_sessions.clear()


class _NoRotate:
	"""ExitRotator 替身：不轮换（start() 返回 False）。测试绝不能碰真 mihomo —— 那会真切用户的节点。"""

	def __init__(self):
		self.switched = 0

	async def start(self):
		return False

	async def next_ip(self):
		self.switched += 1

	async def restore(self):
		pass

	@property
	def ip_count(self):
		return 0

	@property
	def current(self):
		return 0


@pytest.fixture
def no_rotator(monkeypatch):
	monkeypatch.setattr(bs, 'ExitRotator', _NoRotate)


@pytest.fixture
def fast_gaps(monkeypatch):
	"""轮与轮之间的等待清零，测试才不会真睡"""
	monkeypatch.setattr(bs, 'WAF_ROUND_GAP', 0)
	monkeypatch.setattr(bs, 'WAF_DEGRADED_GAP', 0)
	monkeypatch.setattr(bs, 'WAF_COOLDOWN', 0)


def test_余额端点返回实时值并写入基线(usage_file, agent_accounts, no_rotator, fast_gaps, monkeypatch):
	async def fake_session(account, force=False):
		return {'session': 'S'}, '1'

	async def fake_balance(cookies, uid):
		return {'quota': 2025.0, 'used': 10.0, 'username': 'x'}, None

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	monkeypatch.setattr(bs, 'agentrouter_real_balance', fake_balance)
	r = asyncio.run(bs.login_accounts_balances())
	assert r['summary']['total_quota'] == 4050.0
	assert all(not x.get('as_of') for x in r['results']), '实时值不该带 as_of —— 带了前端就把今日用量显示成 "--"'
	day = next(iter(bs.load_usage_data().values()))
	assert day['agentrouter:a']['quota'] == 2025.0, '查询要顺带写基线，否则今日用量永远算不出来'


def test_余额端点session失效时重登一次(usage_file, agent_accounts, no_rotator, fast_gaps, monkeypatch):
	# 按账号计数，别用全局计数 —— 两个账号是并发跑的，全局计数的先后不确定
	sessions = {}
	balances = {}

	async def fake_session(account, force=False):
		sessions[account.name] = sessions.get(account.name, 0) + 1
		return {'session': 'S-' + account.name}, account.name

	async def fake_balance(cookies, uid):
		balances[uid] = balances.get(uid, 0) + 1
		# 每个账号第一次失败（模拟 session 过期），重登后成功
		if balances[uid] == 1:
			return None, 'HTTP 401'
		return {'quota': 1.0, 'used': 0.0, 'username': 'x'}, None

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	monkeypatch.setattr(bs, 'agentrouter_real_balance', fake_balance)
	r = asyncio.run(bs.login_accounts_balances())
	assert sessions == {'a': 2, 'b': 2}, '每个账号都该在余额读不到时重登一次'
	assert r['summary']['success_count'] == 2


def test_余额端点支持按名字过滤(usage_file, agent_accounts, no_rotator, fast_gaps, monkeypatch):
	# WAF 按出口 IP 限速，整查会在第 5~7 个触发拦截；names 用于分小批查询
	calls = []

	async def fake_session(account, force=False):
		calls.append(account.name)
		return {'session': 'S'}, '1'

	async def fake_balance(cookies, uid):
		return {'quota': 1.0, 'used': 0.0, 'username': 'x'}, None

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	monkeypatch.setattr(bs, 'agentrouter_real_balance', fake_balance)
	r = asyncio.run(bs.login_accounts_balances(names=' b , 不存在的 '))
	assert calls == ['b'], 'names 过滤生效，未列出的账号不该被查询（省 WAF 配额）'
	assert r['summary']['success_count'] == 1


def test_登录限流报成人话而不是JSON解析错(monkeypatch, tmp_path):
	# 429 的响应体是空的，直接 .json() 会抛 JSONDecodeError，报出来完全看不出是限流
	monkeypatch.setattr(bs, 'AGENTROUTER_SESSION_FILE', tmp_path / 's.json')
	sess = FakeSession({'/api/user/login': FakeResponse(status_code=429, body='')})
	_patch_session(monkeypatch, sess)
	bs._agentrouter_key_sessions.clear()
	acc = bs.LoginAccountItem(name='a', username='u', password='p')
	with pytest.raises(RuntimeError, match='限流'):
		asyncio.run(bs._agentrouter_session(acc))


def test_查余额遇到登录限流时给出明确原因(monkeypatch):
	sess = FakeSession({'/api/user/login': FakeResponse(status_code=429, body='')})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.query_balance_login(bs.LoginAccountItem(name='a', username='u', password='p')))
	assert r['success'] is False and '限流' in r['error']


def test_签到遇到登录限流时给出明确原因(monkeypatch):
	sess = FakeSession({'/api/user/login': FakeResponse(status_code=429, body='')})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.sign_in_login(bs.LoginAccountItem(name='a', username='u', password='p')))
	assert r['success'] is False and '限流' in r['message']


def test_session缓存落盘并能恢复(monkeypatch, tmp_path):
	f = tmp_path / 'agentrouter_sessions.json'
	monkeypatch.setattr(bs, 'AGENTROUTER_SESSION_FILE', f)
	sess = FakeSession({'/api/user/login': LOGIN_OK}, cookies={'session': 'S'})
	_patch_session(monkeypatch, sess)
	bs._agentrouter_key_sessions.clear()
	acc = bs.LoginAccountItem(name='a', username='u', password='p')
	asyncio.run(bs._agentrouter_session(acc))
	assert f.exists(), '不落盘的话每次重启后的第一次查询就是 N 次登录，稳定把自己打进 429'

	bs._agentrouter_key_sessions.clear()
	bs.load_agentrouter_sessions()
	assert bs._agentrouter_key_sessions['a']['user_id'] == '105950'
	# 恢复之后再取不该再登录
	before = len(sess.calls)
	cookies, uid = asyncio.run(bs._agentrouter_session(acc))
	assert len(sess.calls) == before and uid == '105950'
	bs._agentrouter_key_sessions.clear()


def test_过期的session缓存不会被恢复(monkeypatch, tmp_path):
	f = tmp_path / 'agentrouter_sessions.json'
	f.write_text(json.dumps({'a': {'cookies': {}, 'user_id': '1', 'expires': 0}}), encoding='utf-8')
	monkeypatch.setattr(bs, 'AGENTROUTER_SESSION_FILE', f)
	bs._agentrouter_key_sessions.clear()
	bs.load_agentrouter_sessions()
	assert bs._agentrouter_key_sessions == {}


WAF_SLIDE = FakeResponse(body=(
	'<!doctype html><meta name="aliyun_waf_aa" content="ff92"><script>'
	'var captcha = new SlideCaptcha();</script>'
))


def test_认出阿里云WAF滑块拦截页():
	# 拦截页 HTTP 是 200，直接 .json() 会抛 JSONDecodeError，看起来像代码坏了
	why = bs.agentrouter_block_reason(WAF_SLIDE)
	assert why and 'WAF' in why and '滑块' in why
	assert bs.agentrouter_block_reason(FakeResponse(status_code=429, body='')).startswith('被站点限流')
	assert bs.agentrouter_block_reason(SELF_OK) is None


def test_被WAF拦时报出真实原因而不是笼统失败(monkeypatch):
	sess = FakeSession({'/api/user/login': LOGIN_OK, '/api/user/self': WAF_SLIDE})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.query_balance_login(bs.LoginAccountItem(name='6', username='u', password='p')))
	assert r['success'] is False and 'WAF' in r['error']


def test_被WAF拦时不再重登(usage_file, agent_accounts, no_rotator, fast_gaps, monkeypatch):
	# 没轮换出新的出口 IP 时，被 WAF 拦也不该重登重试 ——
	# cookie 被滑块标记后，同一个 IP 上换新 cookie 也过不去（实测）
	sessions = []

	async def fake_session(account, force=False):
		sessions.append(account.name)
		return {'session': 'S'}, '1'

	async def fake_balance(cookies, uid):
		return None, '被阿里云 WAF 拦截（滑块验证），出口 IP 请求过多，需等一段时间自行恢复'

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	monkeypatch.setattr(bs, 'agentrouter_real_balance', fake_balance)
	r = asyncio.run(bs.login_accounts_balances())
	assert sessions == ['a', 'b'], '每个账号只该登录一次，WAF 拦截时重登纯属浪费'
	assert 'WAF' in r['results'][0]['error']
	assert bs.load_usage_data() == {}


def test_余额端点登录失败不写基线(usage_file, agent_accounts, no_rotator, fast_gaps, monkeypatch):
	async def fake_session(account, force=False):
		raise RuntimeError('登录失败: 用户已被封禁')

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	r = asyncio.run(bs.login_accounts_balances())
	assert r['summary']['success_count'] == 0
	assert '封禁' in r['results'][0]['error']
	assert bs.load_usage_data() == {}, '失败的账号不能写进快照，否则基线被污染'


def test_轮换可用时被拦的账号换IP重登重试(usage_file, agent_accounts, fast_gaps, monkeypatch):
	# WAF 滑块标记的是 session cookie：换 IP + 重登（force=True）就能过 —— 这是整查能查全的关键
	class _Rotate(_NoRotate):
		async def start(self):
			return True

	monkeypatch.setattr(bs, 'ExitRotator', _Rotate)
	sessions = []
	balance_calls = []

	async def fake_session(account, force=False):
		sessions.append((account.name, force))
		return {'session': 'S'}, '1'

	async def fake_balance(cookies, uid):
		balance_calls.append(uid)
		if len(balance_calls) == 1:
			return None, '被阿里云 WAF 拦截（滑块验证），出口 IP 请求过多'
		return {'quota': 2.0, 'used': 0.0, 'username': 'x'}, None

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	monkeypatch.setattr(bs, 'agentrouter_real_balance', fake_balance)
	r = asyncio.run(bs.login_accounts_balances())
	assert r['summary']['success_count'] == 2, '被拦一次 + 换 IP 重登后应全部成功'
	by_acc = {}
	for name, force in sessions:
		by_acc.setdefault(name, []).append(force)
	retried = [f for f in by_acc.values() if False in f and True in f]
	assert retried, f'被 WAF 拦的账号必须以 force=True 重登重试，实际调用: {sessions}'


def test_连续被拦零成功时提前中止不再硬磨(fast_gaps, usage_file, monkeypatch):
	# 出口 IP 都在惩罚期时继续打只会给窗口续命 —— 连续 8 个账号被拦就该收手
	class _Rotate(_NoRotate):
		async def start(self):
			return True

	monkeypatch.setattr(bs, 'ExitRotator', _Rotate)
	accs = [bs.LoginAccountItem(name=f'n{i}', username='u', password='p') for i in range(10)]
	monkeypatch.setattr(bs, 'load_login_accounts', lambda: accs)
	sessions = []

	async def fake_session(account, force=False):
		sessions.append(account.name)
		return {'session': 'S'}, '1'

	async def fake_balance(cookies, uid):
		return None, '被阿里云 WAF 拦截（滑块验证）'

	monkeypatch.setattr(bs, '_agentrouter_session', fake_session)
	monkeypatch.setattr(bs, 'agentrouter_real_balance', fake_balance)
	r = asyncio.run(bs.login_accounts_balances())
	assert r['summary']['success_count'] == 0
	attempted = set(sessions)
	assert len(attempted) < len(accs), f'连续被拦就该提前中止，不该把 10 个都试一遍（实际试了 {len(attempted)} 个）'
	assert any('惩罚期' in (x.get('error') or '') for x in r['results']), '未查到的账号要说明是提前中止，别让人以为代码坏了'


def test_查询进行中时重复请求返回提示(usage_file, agent_accounts, monkeypatch):
	# 轮换出口是全局动作，两轮并发查询会互相切对方的节点
	async def slow_query(accounts, live):
		await asyncio.sleep(0.15)
		return [{'name': a.name, 'success': True, 'quota': 1.0, 'used': 0.0, 'username': 'x'} for a in accounts]

	monkeypatch.setattr(bs, '_query_login_balances', slow_query)

	async def run_two():
		t1 = asyncio.create_task(bs.login_accounts_balances())
		await asyncio.sleep(0.05)
		t2 = asyncio.create_task(bs.login_accounts_balances())
		return await asyncio.gather(t1, t2)

	first, second = asyncio.run(run_two())
	assert first['success'] is True
	assert second['success'] is False and '进行中' in second['error']


def test_从mihomo配置读controller地址(monkeypatch, tmp_path):
	cfg = tmp_path / 'config.yaml'
	cfg.write_text('mixed-port: 7890\nexternal-controller: 0.0.0.0:9090\nmode: rule\nsecret: "s3cr"\n', encoding='utf-8')
	monkeypatch.setattr(bs, 'MIHOMO_CONFIG_FILE', cfg)
	assert bs._mihomo_controller() == ('http://127.0.0.1:9090', 's3cr'), '0.0.0.0 要换成本机回环地址'


def test_读不到mihomo配置时返回None(monkeypatch, tmp_path):
	monkeypatch.setattr(bs, 'MIHOMO_CONFIG_FILE', tmp_path / '不存在.yaml')
	assert bs._mihomo_controller() is None


def test_切换出口后连接池代数更新(monkeypatch):
	# mihomo 切节点不杀旧 keep-alive 隧道，复用 Session 会继续走旧出口（实测）——
	# 每次切换必须让连接池 key 变化，逼请求重新建连，否则轮换形同虚设
	monkeypatch.setattr(bs, '_mihomo_call', lambda method, url, secret, body=None: FakeResponse(status_code=204))
	rot = bs.ExitRotator()
	rot._nodes = ['A', 'B']
	rot._ips = ['1.1.1.1', '2.2.2.2']
	g0 = bs._exit_generation
	asyncio.run(rot.next_ip())
	assert bs._exit_generation == g0 + 1
	assert bs._ar_session_key('agentrouter-self') == f'agentrouter-self:g{g0 + 1}'


def test_ExitRotator按实际出口IP去重并恢复原节点(monkeypatch):
	# 节点名不同 ≠ 出口 IP 不同（原生 03/04 同 IP），必须按实测 IP 去重；探针不过的节点不要
	selected = {'node': '原节点'}
	probes = []

	def fake_call(method, url, secret, body=None):
		if method == 'GET':
			return FakeResponse(payload={'now': '原节点', 'all': ['原节点', '美国A', '美国B同IP', '美国B2', '香港X', '剩余流量：x']})
		selected['node'] = body['name']
		return FakeResponse(status_code=204)

	monkeypatch.setattr(bs, '_mihomo_call', fake_call)
	monkeypatch.setattr(bs, '_mihomo_controller', lambda: ('http://127.0.0.1:9090', 's'))

	ips = {'原节点': '1.1.1.1', '美国A': '2.2.2.2', '美国B同IP': '2.2.2.2', '美国B2': '3.3.3.3', '香港X': '4.4.4.4'}

	async def fake_egress():
		return ips[selected['node']]

	async def fake_probe():
		probes.append(selected['node'])
		return selected['node'] != '香港X'  # 香港X 模拟被 WAF 拦的节点

	monkeypatch.setattr(bs, '_query_egress_ip', fake_egress)
	monkeypatch.setattr(bs, '_probe_exit_passes_waf', fake_probe)
	bs._waf_pass_cache.update(ts=0.0, nodes=[], ips=[])

	async def run():
		rot = bs.ExitRotator()
		assert await rot.start() is True
		assert rot.ip_count == 2, '同 IP 的（美国B同IP）、原节点、探针不过的（香港X）都该被剔除'
		await rot.next_ip()
		assert selected['node'] == '美国A'
		await rot.next_ip()
		assert selected['node'] == '美国B2'
		await rot.restore()
		assert selected['node'] == '原节点', '查询结束必须把用户原来的节点切回去'
		# 探测结果有缓存：TTL 内再来一次不重新探测
		n_probes = len(probes)
		rot2 = bs.ExitRotator()
		assert await rot2.start() is True and rot2.ip_count == 2
		assert len(probes) == n_probes, 'TTL 内应走缓存，别每次查询都把节点探测一遍（探测也耗 WAF 配额）'

	asyncio.run(run())
	bs._waf_pass_cache.update(ts=0.0, nodes=[], ips=[])


# ===== 缓慢签到间隔设置 =====


def test_缓慢签到间隔取自设置并兜底(monkeypatch):
	monkeypatch.setitem(bs.checkin_settings, 'agentrouter_gap_min', 2)
	monkeypatch.setitem(bs.checkin_settings, 'agentrouter_gap_max', 3)
	for _ in range(20):
		assert 120 <= bs.checkin_gap_seconds() <= 180
	# min 比 max 大时夹到 min，不能炸
	monkeypatch.setitem(bs.checkin_settings, 'agentrouter_gap_min', 10)
	monkeypatch.setitem(bs.checkin_settings, 'agentrouter_gap_max', 5)
	assert 600 <= bs.checkin_gap_seconds() <= 600
	# 设置坏了退回默认 30~60 分钟
	monkeypatch.setitem(bs.checkin_settings, 'agentrouter_gap_min', 'x')
	monkeypatch.setitem(bs.checkin_settings, 'agentrouter_gap_max', None)
	assert 1800 <= bs.checkin_gap_seconds() <= 3600


def test_间隔设置接口接受分钟数并自动对调(monkeypatch, tmp_path):
	monkeypatch.setattr(bs, 'CHECKIN_SETTINGS_FILE', tmp_path / 'checkin_settings.json')
	asyncio.run(bs.update_checkin_settings({'agentrouter_gap_min': 90, 'agentrouter_gap_max': 30}))
	assert (bs.checkin_settings['agentrouter_gap_min'], bs.checkin_settings['agentrouter_gap_max']) == (30, 90), '填反了要自动对调，别让 randint 炸掉'
	# 非法类型直接忽略，不动现有值
	asyncio.run(bs.update_checkin_settings({'agentrouter_gap_min': 'abc', 'agentrouter_gap_max': True}))
	assert bs.checkin_settings['agentrouter_gap_min'] == 30
	# 越界夹到 1~1440
	asyncio.run(bs.update_checkin_settings({'agentrouter_gap_min': 0, 'agentrouter_gap_max': 999999}))
	assert (bs.checkin_settings['agentrouter_gap_min'], bs.checkin_settings['agentrouter_gap_max']) == (1, 1440)


# ===== 一键全签（轮换出口） =====


@pytest.fixture
def fast_checkin_env(usage_file, no_rotator, fast_gaps, monkeypatch):
	"""重置签到状态并隔离落盘，测完恢复"""
	orig = json.loads(json.dumps(bs.checkin_state))
	monkeypatch.setattr(bs, 'save_checkin_state', lambda: None)
	bs.checkin_state.update(running=False, date=None, accounts={}, logs=[], total=0, done=0, order=[], mode=None)
	yield
	bs.checkin_state.clear()
	bs.checkin_state.update(orig)


def test_一键全签跳过今日已签账号(fast_checkin_env, agent_accounts, monkeypatch):
	today = datetime.now().strftime('%Y-%m-%d')
	bs.checkin_state['date'] = today
	bs.checkin_state['accounts'] = {'a': {'status': 'signed', 'message': '签到成功', 'time': 'x'}}
	calls = []

	async def fake_sign_in(account):
		calls.append(account.name)
		return {'name': account.name, 'success': True, 'message': '签到成功', 'already_signed': False, 'quota': 10.0, 'used': 1.0}

	monkeypatch.setattr(bs, 'sign_in_login', fake_sign_in)
	r = asyncio.run(bs.login_checkin_fast())
	assert calls == ['b'], '今日已签到的账号不该再登录（登录即签到，白耗 WAF 配额）'
	assert r['summary']['new_signed'] == 1 and r['summary']['already'] == 1
	st = bs._checkin_status_payload()
	assert not st['running'] and st['mode'] == 'fast'
	by_name = {a['name']: a for a in st['accounts']}
	assert by_name['a']['status'] == 'signed' and by_name['b']['status'] == 'signed'
	day = next(iter(bs.load_usage_data().values()))
	assert day['agentrouter:b']['quota'] == 10.0, '签到顺带拿到的余额要写入今日快照'


def test_一键全签时缓慢流程在跑则拒绝(fast_checkin_env, agent_accounts):
	bs.checkin_state['running'] = True
	r = asyncio.run(bs.login_checkin_fast())
	assert r['success'] is False and '运行' in r['error']


def test_sign_in_one把封禁类失败标记为fatal(monkeypatch):
	async def fake_sign_in(account):
		return {'name': account.name, 'success': False, 'message': '登录失败: 用户名或密码错误，或用户已被封禁'}

	monkeypatch.setattr(bs, 'sign_in_login', fake_sign_in)
	r = asyncio.run(bs._sign_in_one(bs.LoginAccountItem(name='x', username='u', password='p'), False))
	assert r['fatal'] is True, '密码错/封禁换 IP 也没用，调度器不该重试'


def test_sign_in_one的WAF拦截不标记fatal(monkeypatch):
	async def fake_sign_in(account):
		return {'name': account.name, 'success': False, 'message': '登录被拦: 被阿里云 WAF 拦截（滑块验证）'}

	monkeypatch.setattr(bs, 'sign_in_login', fake_sign_in)
	r = asyncio.run(bs._sign_in_one(bs.LoginAccountItem(name='x', username='u', password='p'), False))
	assert r['fatal'] is False, 'WAF 拦的是 cookie/IP，换 IP 重登就能过，必须允许重试'


def test_签到登录被WAF滑块拦时报人话(monkeypatch):
	# 滑块页是 200 + HTML，不先认出来 resp.json() 会抛 JSONDecodeError，完全看不出是被拦
	sess = FakeSession({'/api/user/login': WAF_SLIDE})
	_patch_session(monkeypatch, sess)
	r = asyncio.run(bs.sign_in_login(bs.LoginAccountItem(name='a', username='u', password='p')))
	assert r['success'] is False and 'WAF' in r['message']
