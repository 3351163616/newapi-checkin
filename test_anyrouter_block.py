"""balance_server 的离线单测：拦截识别、session 身份判定、限流中止。

不发任何上游请求 —— 用假 Response 驱动，跑得快且不会撞限流。
    .venv/bin/python -m pytest test_anyrouter_block.py -q
"""

import asyncio
import base64

import pytest

import balance_server as bs

# ===== 测试替身 =====

CHALLENGE_BODY = "<html><script>var arg1='F1B3F9F67CB65755607C9638E1C6755187AB8CB0';</script></html>"
ESA_403_BODY = (
	'<!DOCTYPE html><html><head><title>403 Forbidden</title></head><body>'
	'<p>Sorry, you have been blocked</p><p>Denied by http_ratelimit</p>'
	'<p>RuleID: 21888958</p><p>Performance &amp; Security by ESA</p></body></html>'
)


class FakeResponse:
	"""够用的 curl_cffi Response 替身。"""

	def __init__(self, status_code=200, body='', content_type='application/json; charset=utf-8', cookies=None):
		self.status_code = status_code
		self.text = body
		self.headers = {'content-type': content_type}
		self.cookies = cookies or {}

	def json(self):
		import json

		return json.loads(self.text)


def make_session(payload_keys: dict, ts: int = 1786366280) -> str:
	"""造一个结构与 gorilla securecookie 一致的 session：base64(时间戳|base64(gob)|签名)。

	gob 里的字符串键用「长度字节 + 内容」编码，这正是 _session_is_authenticated 依赖的形式。
	"""
	gob = b'\r\x7f\x04\x01\x02\xff\x80\x00'
	for key, kind in payload_keys.items():
		gob += bytes([len(key)]) + key.encode() + (b'\x03int\x04\x02\x00\x02' if kind == 'int' else b'\x06string')
	inner = base64.urlsafe_b64encode(gob).rstrip(b'=')
	raw = str(ts).encode() + b'|' + inner + b'|' + b'FAKESIG'
	return base64.urlsafe_b64encode(raw).decode().rstrip('=')


# ===== anyrouter_block_reason =====


def test_esa_ratelimit_识别为限流并提示不要反复重试():
	kind, why = bs.anyrouter_block_reason(FakeResponse(403, ESA_403_BODY, 'text/html'))
	assert kind == 'ratelimit'
	assert '限流' in why
	assert '不要反复重试' in why


def test_esa_其它规则不误报成限流():
	body = ESA_403_BODY.replace('http_ratelimit', 'bot_manager')
	kind, why = bs.anyrouter_block_reason(FakeResponse(403, body, 'text/html'))
	assert kind == 'http'
	assert 'bot_manager' in why


def test_挑战页即使是200也算拦截():
	kind, why = bs.anyrouter_block_reason(FakeResponse(200, CHALLENGE_BODY, 'text/html'))
	assert kind == 'challenge'
	assert '验证页' in why


def test_普通http错误():
	kind, why = bs.anyrouter_block_reason(FakeResponse(502, 'bad gateway', 'text/html'))
	assert kind == 'http'
	assert '502' in why


def test_正常json不算拦截():
	assert bs.anyrouter_block_reason(FakeResponse(200, '{"success":true}')) is None


# ===== _session_is_authenticated =====


def test_登录session带id判为已登录():
	s = make_session({'oauth_state': 'string', 'id': 'int', 'username': 'string', 'role': 'int'})
	assert bs._session_is_authenticated(s) is True


def test_匿名session只有oauth_state判为未登录():
	assert bs._session_is_authenticated(make_session({'oauth_state': 'string'})) is False


def test_username里含id不会误判为已登录():
	# 关键：不能用 b'id' 裸匹配 —— "userid" 这种键名会撞上
	s = make_session({'oauth_state': 'string', 'userid_hint': 'string'})
	assert bs._session_is_authenticated(s) is False


def test_结构不认识时返回None而不是False():
	# 没有 oauth_state → 可能是新版结构，必须退回打接口核实，不能误报"已失效"
	assert bs._session_is_authenticated(make_session({'foo': 'string'})) is None
	assert bs._session_is_authenticated('这不是base64!!!') is None
	assert bs._session_is_authenticated('') is None


def test_真实线上session判为已登录():
	# 2026-08-09 从 anyrouter.top 实际抓到的一段（签名已截断，只测解码路径）
	real = (
		'MTc4NjI4Njc0OHxEWDhFQVFMX2dBQUJFQUVRQUFEXzVQLUFBQWNHYzNSeWFXNW5EQWNBQldkeWIzVndCbk4wY21sdVp3d0'
		'pBQWRrWldaaGRXeDBCbk4wY21sdVp3d0ZBQU5oWm1ZR2MzUnlhVzVuREFZQUJESXpNMWNHYzNSeWFXNW5EQTBBQzI5aGRY'
		'Um9YM04wWVhSbEJuTjBjbWx1Wnd3T0FBd3lUSFI2ZFdkNmJ6VnVOek1HYzNSeWFXNW5EQVFBQW1sa0EybHVkQVFGQVAwRX'
		'ExSUdjM1J5YVc1bkRBb0FDSFZ6WlhKdVlXMWxCbk4wY21sdVp3d1FBQTVzYVc1MWVHUnZYekUxTXpBd01RWnpkSEpwYm1j'
		'TUJnQUVjbTlzWlFOcGJuUUVBZ0FDQm5OMGNtbHVad3dJQUFaemRHRjBkWE1EYVc1MEJBSUFBZz09fM13V1pB5L3dWWdC7e'
		'_HsLuGyG5RLNLHLh0A3svJWqyt'
	)
	assert bs._session_is_authenticated(real) is True


# ===== renew_one_cookie：限流不该被误报成"请重新登录" =====


@pytest.fixture
def account():
	return bs.AccountItem(name='测试号', cookies={'session': 'old'}, api_user='153001')


def _patch_requests(monkeypatch, responses):
	"""按调用顺序返回预设响应，并记录实际发出的请求。"""
	calls = []

	async def fake(method, url, headers, cookies=None, json_body=None):
		calls.append((method, url))
		return responses[min(len(calls) - 1, len(responses) - 1)]

	monkeypatch.setattr(bs, 'anyrouter_request', fake)
	return calls


def test_续期时限流报限流而非接口变更(monkeypatch, account):
	_patch_requests(monkeypatch, [FakeResponse(403, ESA_403_BODY, 'text/html')])
	r = asyncio.run(bs.renew_one_cookie(account, {'acw_tc': 'x'}))
	assert r['success'] is False
	assert r['blocked'] == 'ratelimit'
	assert '接口可能已变更' not in r['message']
	assert '重新登录' not in r['message']


def test_校验请求被限流不提示重新登录(monkeypatch, account):
	# 第一个请求成功拿到新 cookie，但结构不认识 → 走接口核实，而核实请求撞上限流
	unknown = make_session({'foo': 'string'})
	calls = _patch_requests(
		monkeypatch,
		[
			FakeResponse(200, '{"success":true}', cookies={'session': unknown}),
			FakeResponse(403, ESA_403_BODY, 'text/html'),
		],
	)
	r = asyncio.run(bs.renew_one_cookie(account, {}))
	assert r['success'] is False
	assert r['blocked'] == 'ratelimit'
	assert '重新登录' not in r['message']  # 这才是原来把人带偏的那句
	assert '无法核实' in r['message']
	assert len(calls) == 2


def test_登录cookie本地判定就够不再打校验请求(monkeypatch, account):
	authed = make_session({'oauth_state': 'string', 'id': 'int', 'username': 'string'})
	calls = _patch_requests(monkeypatch, [FakeResponse(200, '{"success":true}', cookies={'session': authed})])
	r = asyncio.run(bs.renew_one_cookie(account, {}))
	assert r['success'] is True
	assert len(calls) == 1, '本地能判定时不该再打 /api/user/self'


def test_匿名cookie判为失效且不写回(monkeypatch, account):
	anon = make_session({'oauth_state': 'string'})
	calls = _patch_requests(
		monkeypatch,
		[
			FakeResponse(200, '{"success":true}', cookies={'session': anon}),
			FakeResponse(200, '{"success":false,"message":"未登录"}'),
		],
	)
	r = asyncio.run(bs.renew_one_cookie(account, {}))
	assert r['success'] is False
	assert '重新登录' in r['message']
	assert 'new_session' not in r
	assert len(calls) == 2, '匿名判定为 False 后仍要打接口确认，避免本地判错就丢账号'


def test_接口不下发cookie时才提示接口变更(monkeypatch, account):
	_patch_requests(monkeypatch, [FakeResponse(200, '{"success":true}', cookies={})])
	r = asyncio.run(bs.renew_one_cookie(account, {}))
	assert r['success'] is False
	assert '接口可能已变更' in r['message']


# ===== anyrouter_request：挑战页就地重解重试 =====


class FakeCffiSession:
	"""按顺序返回预设响应，并记下每次实际发出的 cookie。"""

	def __init__(self, responses):
		self._responses = list(responses)
		self.sent_cookies = []

	def request(self, method, url, headers=None, cookies=None, json=None):
		self.sent_cookies.append(dict(cookies or {}))
		return self._responses[min(len(self.sent_cookies) - 1, len(self._responses) - 1)]


def _patch_cffi(monkeypatch, responses):
	fake = FakeCffiSession(responses)
	monkeypatch.setattr(bs, '_get_cffi_session', lambda key, proxies=None: fake)
	return fake


# 挑战页里的 arg1 对应的正解，由 _solve_acw_sc_v2 算出（算法本身另有实测覆盖）
EXPECTED_SOLUTION = bs._solve_acw_sc_v2('F1B3F9F67CB65755607C9638E1C6755187AB8CB0')


def test_遇挑战页自动重算cookie并重试(monkeypatch):
	fake = _patch_cffi(
		monkeypatch,
		[
			FakeResponse(200, CHALLENGE_BODY, 'text/html'),
			FakeResponse(200, '{"success":true}'),
		],
	)
	bs.waf_cache.pop('anyrouter', None)
	resp = asyncio.run(bs.anyrouter_request('GET', 'https://anyrouter.top/api/status', {}, cookies={'acw_tc': 'x'}))

	assert bs.anyrouter_block_reason(resp) is None, '调用方应当拿到重试后的正常响应'
	assert len(fake.sent_cookies) == 2
	assert 'acw_sc__v2' not in fake.sent_cookies[0]
	assert fake.sent_cookies[1]['acw_sc__v2'] == EXPECTED_SOLUTION
	assert fake.sent_cookies[1]['acw_tc'] == 'x', '重试要保留原有 cookie'


def test_重试成功后回写缓存供后续请求复用(monkeypatch):
	_patch_cffi(monkeypatch, [FakeResponse(200, CHALLENGE_BODY, 'text/html'), FakeResponse(200, '{}')])
	bs.waf_cache['anyrouter'] = {'cookies': {'acw_tc': 'x', 'acw_sc__v2': 'stale'}, 'expires': 9e18}
	asyncio.run(bs.anyrouter_request('GET', 'https://anyrouter.top/api/status', {}, cookies={'acw_tc': 'x'}))

	# 不回写的话，同一轮里剩下几十个账号会各自再撞一次挑战页，请求量翻倍 → 更容易触发 IP 限流
	assert bs.waf_cache['anyrouter']['cookies']['acw_sc__v2'] == EXPECTED_SOLUTION
	bs.waf_cache.pop('anyrouter', None)


def test_重试沿用挑战页下发的新acw_tc(monkeypatch):
	# 挑战页的 Max-Age 只有 1 小时，它会顺带补发新的 acw_tc；acw_sc__v2 配着它校验，
	# 重试必须用新的那个，否则用旧 acw_tc + 新解出的 acw_sc__v2，照样过不了
	fake = _patch_cffi(
		monkeypatch,
		[
			FakeResponse(200, CHALLENGE_BODY, 'text/html', cookies={'acw_tc': 'NEW', 'session': '不该带上'}),
			FakeResponse(200, '{}'),
		],
	)
	bs.waf_cache.pop('anyrouter', None)
	asyncio.run(bs.anyrouter_request('GET', 'https://anyrouter.top/api/status', {}, cookies={'acw_tc': 'OLD'}))

	assert fake.sent_cookies[1]['acw_tc'] == 'NEW'
	assert fake.sent_cookies[1]['acw_sc__v2'] == EXPECTED_SOLUTION
	assert 'session' not in fake.sent_cookies[1], '只该沿用 WAF cookie，别把响应里其它 cookie 混进来'


def test_限流页不触发重试(monkeypatch):
	# 403 拦截页里没有 arg1，重试也没用，只会加重封禁
	fake = _patch_cffi(monkeypatch, [FakeResponse(403, ESA_403_BODY, 'text/html')])
	resp = asyncio.run(bs.anyrouter_request('GET', 'https://anyrouter.top/api/status', {}, cookies={}))
	assert len(fake.sent_cookies) == 1
	assert bs.anyrouter_block_reason(resp)[0] == 'ratelimit'


def test_正常响应只发一次请求(monkeypatch):
	fake = _patch_cffi(monkeypatch, [FakeResponse(200, '{"success":true}')])
	asyncio.run(bs.anyrouter_request('GET', 'https://anyrouter.top/api/status', {}, cookies={}))
	assert len(fake.sent_cookies) == 1


def test_连续两次挑战页不无限重试(monkeypatch):
	fake = _patch_cffi(monkeypatch, [FakeResponse(200, CHALLENGE_BODY, 'text/html')])
	resp = asyncio.run(bs.anyrouter_request('GET', 'https://anyrouter.top/api/status', {}, cookies={}))
	assert len(fake.sent_cookies) == 2, '最多重试一次'
	assert bs.anyrouter_block_reason(resp)[0] == 'challenge'


# ===== /api/anyrouter/renew：限流后中止剩余账号 =====


def test_限流后中止剩余账号不再打上游(monkeypatch, tmp_path):
	accounts = [bs.AccountItem(name=f'号{i}', cookies={'session': 'old'}, api_user=str(i)) for i in range(20)]
	monkeypatch.setattr(bs, 'load_cookie_accounts', lambda: accounts)
	monkeypatch.setattr(bs, '_get_waf_cookies_if_needed', _async_return({'acw_tc': 'x'}))
	monkeypatch.setattr(bs, 'save_renewed_sessions', lambda updates: None)

	attempted = []

	async def fake_renew(account, waf):
		attempted.append(account.name)
		if len(attempted) == 1:
			return {'name': account.name, 'success': False, 'message': '限流', 'blocked': 'ratelimit'}
		return {'name': account.name, 'success': True, 'message': 'ok', 'new_session': 'new'}

	monkeypatch.setattr(bs, 'renew_one_cookie', fake_renew)
	monkeypatch.setattr(bs, 'ANYROUTER_CONCURRENCY', 1)  # 串行才能确定地观察到中止

	data = asyncio.run(bs.anyrouter_renew({}))

	assert len(attempted) == 1, f'限流后不该继续打上游，实际打了 {len(attempted)} 个'
	assert data['summary']['skipped'] == 19
	assert '限流' in data['notice'] and '反复重试' in data['notice']


def test_未限流时所有账号都会尝试(monkeypatch):
	accounts = [bs.AccountItem(name=f'号{i}', cookies={'session': 'old'}, api_user=str(i)) for i in range(5)]
	monkeypatch.setattr(bs, 'load_cookie_accounts', lambda: accounts)
	monkeypatch.setattr(bs, '_get_waf_cookies_if_needed', _async_return({'acw_tc': 'x'}))
	saved = {}
	monkeypatch.setattr(bs, 'save_renewed_sessions', lambda updates: saved.update(updates))

	async def fake_renew(account, waf):
		return {'name': account.name, 'success': True, 'message': 'ok', 'new_session': 'new-' + account.name}

	monkeypatch.setattr(bs, 'renew_one_cookie', fake_renew)
	data = asyncio.run(bs.anyrouter_renew({}))

	assert data['summary'] == {'total': 5, 'renewed': 5, 'failed': 0, 'skipped': 0}
	assert 'notice' not in data
	assert len(saved) == 5
	assert all('new_session' not in r for r in data['results']), 'new_session 不能回传给前端'


def _async_return(value):
	async def _f(*args, **kwargs):
		return value

	return _f
