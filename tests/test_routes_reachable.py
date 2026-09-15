"""全量端点路由可达性回归：带 token 用正确方法逐端点请求，断言不被 405/catch-all 吞掉。

背景：块E 曾漏挂载 sites/agentrouter/mihomo 三个 router，长期未被发现——
鉴权冒烟只测 401（中间件先拦，从未穿越到路由层），单元测试直接调函数不走 HTTP，
而 catch-all 把未挂载路径伪装成 404/405。本测试从 app 路由表枚举全部 /api 端点，
逐一真实请求（不发上游：用不存在的资源 id，业务层应返回 4xx JSON 而非 405/HTML）。
    python -m pytest tests/test_routes_reachable.py -q
"""

import time

import pytest
from fastapi.testclient import TestClient

import balance_server as bs


def _collect_endpoints():
	"""从全部 router（含惰性挂载的子 router）收集 (method, path) 清单。"""
	endpoints = []

	def walk(routes):
		for r in routes:
			inner = getattr(r, 'routes', None)  # 惰性挂载节点（_IncludedRouter）
			original = getattr(r, 'original_router', None)
			if original is not None:
				walk(original.routes)
				continue
			methods = getattr(r, 'methods', None)
			path = getattr(r, 'path', None)
			if methods and path and path.startswith('/api'):
				for m in sorted(methods - {'HEAD', 'OPTIONS'}):
					endpoints.append((m, path))

	walk(bs.app.routes)
	return endpoints


@pytest.fixture(scope='module')
def client():
	bs.active_tokens['routes-test'] = time.time() + 3600
	return TestClient(bs.app, headers={'Authorization': 'Bearer routes-test'})


def test_路由表覆盖了所有业务域():
	eps = _collect_endpoints()
	paths = {p for _, p in eps}
	for domain_prefix in ('/api/sites', '/api/site/{site_id}', '/api/login-accounts',
	                      '/api/token', '/api/monitor', '/api/keys', '/api/usage',
	                      '/api/turnstile', '/api/notify', '/api/waf', '/api/system/proxy-info'):
		assert any(p.startswith(domain_prefix) for p in paths), f'{domain_prefix} 域端点缺失'
	# 块E 曾漏挂载的三个 router 的代表端点必须在表内
	assert ('POST', '/api/sites') in eps or ('GET', '/api/sites') in eps
	assert any(p == '/api/login-accounts/balances' for _, p in eps)
	assert any(p == '/api/system/proxy-info' for _, p in eps)
	assert any(p == '/api/sites/import-hub' for _, p in eps), 'hub 导入端点缺失'


def test_逐端点真实请求不被405或catchall吞掉(client):
	skip_bodies = {'/api/login', '/api/logout'}  # 会改会话状态的除外
	checked = 0
	for m, path in _collect_endpoints():
		if path in skip_bodies:
			continue
		p = path.replace('{site_id}', '___nonexistent___')
		r = client.request(m, p, json={} if m in ('POST', 'PUT', 'DELETE') else None)
		assert r.status_code != 405, f'{m} {path} -> 405（router 未挂载或方法注册丢失）'
		if p.startswith('/api'):
			body = r.text[:200]
			assert '未知接口' not in body, f'{m} {path} 被 catch-all 吞掉（router 未挂载）'
		checked += 1
	assert checked >= 40, f'端点覆盖数异常：{checked}'
