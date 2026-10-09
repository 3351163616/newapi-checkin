"""会话缓存的上界与淘汰：fd 耗尽事故的回归测试。

2026-10-09 线上事故：_get_cffi_session 按 (线程, key) 无界缓存 Session，而每个活着的
会话占 1 个 eventfd（+ keep-alive 时 1 个 socket）——24 站点 × 32 上游线程顶满 1024 的
fd 软限，accept() 开始 EMFILE、静态文件 500。此处锁住「超上限即淘汰最久未用并 close」。

不发任何请求，也不碰真 curl_cffi —— Session 用替身注入。

    .venv/bin/python -m pytest tests/test_session_cache.py -q
"""

import sys
import types

import pytest

from server import common


class FakeCookies:
	"""只记 clear 次数：复用会话时必须清空 cookie，否则上个账号的 session 会串台。"""

	def __init__(self):
		self.cleared = 0

	def clear(self):
		self.cleared += 1


class FakeSession:
	instances: list = []

	def __init__(self, **kwargs):
		self.kwargs = kwargs
		self.closed = False
		self.cookies = FakeCookies()
		FakeSession.instances.append(self)

	def close(self):
		self.closed = True


@pytest.fixture(autouse=True)
def fake_curl_cffi(monkeypatch):
	"""注入 curl_cffi 替身，并清空线程本地缓存（否则用例之间互相看到对方的会话）。"""
	req = types.ModuleType('curl_cffi.requests')
	req.Session = FakeSession
	mod = types.ModuleType('curl_cffi')
	mod.requests = req
	monkeypatch.setitem(sys.modules, 'curl_cffi', mod)
	monkeypatch.setitem(sys.modules, 'curl_cffi.requests', req)
	monkeypatch.setattr(common._thread_local, 'sessions', {}, raising=False)
	FakeSession.instances = []


# ===== 复用与串台防护 =====


def test_同key复用同一个会话():
	first = common._get_cffi_session('newapi:gorouter')
	again = common._get_cffi_session('newapi:gorouter')
	assert first is again
	assert len(FakeSession.instances) == 1, '同 key 不该重复建会话（要复用的就是握手与隧道）'


def test_不同key各自建会话():
	a = common._get_cffi_session('newapi:gorouter')
	b = common._get_cffi_session('newapi:tabitoken')
	assert a is not b
	assert len(FakeSession.instances) == 2


def test_每次取用都清空cookie():
	sess = common._get_cffi_session('anyrouter')
	assert sess.cookies.cleared == 1
	common._get_cffi_session('anyrouter')
	assert sess.cookies.cleared == 2, '复用旧会话也要清 cookie，否则账号之间会串台'


# ===== 上限与淘汰 =====


def test_超出上限淘汰最久未用并关闭():
	maxn = common._SESSION_CACHE_MAX
	sessions = [common._get_cffi_session(f'newapi:site{i}') for i in range(maxn)]
	assert not any(s.closed for s in sessions)
	extra = common._get_cffi_session('newapi:extra')
	assert sessions[0].closed, '被淘汰的会话必须 close()，只从 dict 里删掉不会还 fd'
	assert not sessions[1].closed
	assert not extra.closed
	assert len(common._thread_local.sessions) == maxn


def test_刚用过的会话不会被优先淘汰():
	maxn = common._SESSION_CACHE_MAX
	sessions = [common._get_cffi_session(f'newapi:site{i}') for i in range(maxn)]
	common._get_cffi_session('newapi:site0')  # 重新使用，应移到最末位
	common._get_cffi_session('newapi:newcomer')
	assert not sessions[0].closed, 'LRU 而非 FIFO：刚用过的会话不该被淘汰'
	assert sessions[1].closed


def test_淘汰关闭失败不影响取用(monkeypatch):
	def boom(self):
		raise RuntimeError('设备忙')

	monkeypatch.setattr(FakeSession, 'close', boom)
	for i in range(common._SESSION_CACHE_MAX + 2):
		common._get_cffi_session(f'newapi:site{i}')  # 不抛异常即通过


def test_缓存上限留在fd预算内():
	# 32 个上游线程 × 上限 × 2 个 fd（eventfd + keep-alive socket）要显著低于线上 1024
	# 的 fd 软限：上限 8 时最坏 512 个 —— 调大这个常量前先确认 fd 上限也跟着调了
	assert 0 < common._SESSION_CACHE_MAX <= 8
