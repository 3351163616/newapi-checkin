"""hub 插件增量导入（merge_import）的离线单测。

不发任何上游请求、不碰真实浏览器数据 —— 用临时目录里的假注册表/账号文件驱动。
    python -m pytest tests/test_hub_import.py -q
"""

import json

import pytest

from server.hub import build_hub, extract_objects, merge_import, site_id_from_url


@pytest.fixture
def sandbox(tmp_path):
	"""预置注册表：一个 hub 站点（含历史文件名映射 + auto_checkin=False）、一个非 hub 站点。"""
	(sandbox := tmp_path)  # noqa: F841
	(tmp_path / "newapi_sites.json").write_text(json.dumps([
		{"id": "demo-com", "label": "Demo", "domain": "https://demo.com",
		 "auto_checkin": False, "accounts_file": "demo_accounts.json", "state_file": "demo_state.json"},
		{"id": "claude-x", "label": "非hub站", "domain": "https://claude.x"},
	], ensure_ascii=False), encoding="utf-8")
	(tmp_path / "demo_accounts.json").write_text(json.dumps([
		{"name": "老账号", "access_token": "old", "user_id": "1"},
		{"name": "hub已删", "access_token": "keep", "user_id": "9"},
	], ensure_ascii=False), encoding="utf-8")
	return tmp_path


def hub_data(**overrides):
	"""标准输入：demo-com 一个已有账号(token 变了) + 一个新账号；new-site 两个账号。"""
	data = {
		"demo-com": {
			"1": {"name": "老账号", "token": "NEW", "label": "Demo", "domain": "https://demo.com"},
			"2": {"name": "新账号", "token": "T2", "label": "Demo", "domain": "https://demo.com"},
		},
		"new-site-io": {
			"7": {"name": "甲", "token": "T7", "label": "新站", "domain": "https://new-site.io"},
			"8": {"name": "乙", "token": "T8", "label": "新站", "domain": "https://new-site.io"},
		},
		"agentrouter-org": {
			"99": {"name": "不应导入", "token": "TX", "label": "AgentRouter", "domain": "https://agentrouter.org"},
		},
	}
	data.update(overrides)
	return data


def test_新站点追加且不碰已有站点(sandbox):
	merge_import(hub_data(), sandbox)
	sites = json.loads((sandbox / "newapi_sites.json").read_text(encoding="utf-8"))
	ids = [s["id"] for s in sites]
	assert ids == ["demo-com", "claude-x", "new-site-io"], '已有站点与顺序不动，新站点追加尾部'
	new = sites[-1]
	assert new["domain"] == "https://new-site.io" and new["label"] == "新站"
	assert new["accounts_file"] == "new-site-io_accounts.json"
	accs = json.loads((sandbox / "new-site-io_accounts.json").read_text(encoding="utf-8"))
	assert [a["user_id"] for a in accs] == ["7", "8"]


def test_已有站点按userid合并_token变更更新_新账号追加(sandbox):
	merge_import(hub_data(), sandbox)
	accs = {a["user_id"]: a for a in json.loads((sandbox / "demo_accounts.json").read_text(encoding="utf-8"))}
	assert accs["1"]["access_token"] == "NEW", '已有账号 token 变化视为重新登录，就地更新'
	assert accs["2"]["access_token"] == "T2"
	assert "9" in accs, 'hub 侧已删的本地账号保留（可能在别的设备登录）'


def test_保留auto_checkin与历史文件名映射(sandbox):
	merge_import(hub_data(), sandbox)
	sites = {s["id"]: s for s in json.loads((sandbox / "newapi_sites.json").read_text(encoding="utf-8"))}
	assert sites["demo-com"]["auto_checkin"] is False
	assert sites["demo-com"]["accounts_file"] == "demo_accounts.json", '不得改成默认命名'


def test_agentrouter域跳过(sandbox):
	merge_import(hub_data(), sandbox)
	assert not (sandbox / "agentrouter-org_accounts.json").exists()
	sites = [s["id"] for s in json.loads((sandbox / "newapi_sites.json").read_text(encoding="utf-8"))]
	assert "agentrouter-org" not in sites


def test_重复导入幂等(sandbox):
	merge_import(hub_data(), sandbox)
	before = (sandbox / "new-site-io_accounts.json").read_text(encoding="utf-8")
	out = merge_import(hub_data(), sandbox)  # 第二次应无变化且文件不动
	assert out["added_sites"] == [] and out["changed_accounts"] == []
	assert (sandbox / "new-site-io_accounts.json").read_text(encoding="utf-8") == before
	sites = json.loads((sandbox / "newapi_sites.json").read_text(encoding="utf-8"))
	assert len(sites) == 3, '不得重复追加注册表'


def test_域名保留原始url_连字符域名不被反推破坏(sandbox):
	data = {"grok-heavy-878-indevs-in": {
		"3": {"name": "a", "token": "T", "label": "GN", "domain": "https://grok-heavy.878.indevs.in"},
	}}
	merge_import(data, sandbox)
	sites = {s["id"]: s for s in json.loads((sandbox / "newapi_sites.json").read_text(encoding="utf-8"))}
	assert sites["grok-heavy-878-indevs-in"]["domain"] == "https://grok-heavy.878.indevs.in", \
		'域名含连字符时不能用 sid 反推（会把 grok-heavy 变成 grok.heavy）'


def test_dry_run只算不写(sandbox):
	out = merge_import(hub_data(), sandbox, dry_run=True)
	assert out["dry_run"] is True
	assert len(out["added_sites"]) == 1 and out["added_sites"][0]["id"] == "new-site-io"
	assert {c["action"] for c in out["changed_accounts"]} == {"token 更新", "新增账号"}
	# 落盘检查：注册表与账号文件都未动
	sites = json.loads((sandbox / "newapi_sites.json").read_text(encoding="utf-8"))
	assert len(sites) == 2, "dry_run 不得改注册表"
	assert not (sandbox / "new-site-io_accounts.json").exists()
	demo = json.loads((sandbox / "demo_accounts.json").read_text(encoding="utf-8"))
	assert demo[0]["access_token"] == "old", "dry_run 不得改账号文件"


def test_解析器对原始leveldb文本():
	# LevelDB value 是被转义一层的 JSON；按内容特征提取，无关文本不产生对象
	rec = ('{"id":"account-12345678-1234-1234-1234-123456789abc",'
		'"site_url":"https://a.com","site_name":"A",'
		'"account_info":{"id":11,"username":"u","access_token":"tk"}}')
	escaped = rec.replace('\\', '\\\\').replace('"', '\\"')
	objs = extract_objects('{"db/save/1":"' + escaped + '"}')
	assert len(objs) == 1 and objs[0]["site_url"] == "https://a.com"
	hub, skipped = build_hub(objs)
	assert hub == {"a-com": {"11": {"name": "u", "token": "tk", "label": "A", "domain": "https://a.com"}}}
	assert skipped == 0


def test_build_hub跳过空token并按去重保留最新():
	o1 = {"site_url": "https://a.com", "site_name": "A", "account_info": {"id": "1", "username": "u", "access_token": ""}}
	o2 = {"site_url": "https://a.com", "site_name": "A", "account_info": {"id": "1", "username": "u", "access_token": "tk"}}
	hub, skipped = build_hub([o1, o2])
	# 同 (site_url, id) 去重保留最后一次：旧的空 token 记录被最新覆盖，不计入 skipped
	assert skipped == 0 and hub["a-com"]["1"]["token"] == "tk"


def test_site_id_from_url():
	assert site_id_from_url("https://demo.com") == "demo-com"
	assert site_id_from_url("https://grok-heavy.878.indevs.in/x") == "grok-heavy-878-indevs-in"


# ===== Web 端点（POST /api/sites/import-hub）=====


@pytest.fixture
def client(tmp_path, monkeypatch):
	"""TestClient + 把注册表/账号根目录指到 tmp（借 sites 域的 bs 晚绑定）。"""
	import balance_server as bs
	monkeypatch.setattr(bs, 'NEWAPI_SITES_FILE', tmp_path / 'newapi_sites.json')
	monkeypatch.setattr(bs.NewapiSite, 'accounts_path', lambda self: tmp_path / (self.accounts_file or f'{self.id}_accounts.json'))
	monkeypatch.setattr(bs.NewapiSite, 'state_path', lambda self: tmp_path / (self.state_file or f'{self.id}_checkin_state.json'))
	(tmp_path / 'newapi_sites.json').write_text(json.dumps([
		{'id': 'demo-com', 'label': 'Demo', 'domain': 'https://demo.com', 'accounts_file': 'demo_accounts.json'},
	]), encoding='utf-8')
	(tmp_path / 'demo_accounts.json').write_text('[]', encoding='utf-8')
	import time
	bs.active_tokens['hub-test-token'] = time.time() + 3600
	from fastapi.testclient import TestClient
	return TestClient(bs.app, headers={'Authorization': 'Bearer hub-test-token'})


def ldb_record() -> bytes:
	"""模拟 LevelDB 里的真实形态：value 是被转义一层的 JSON，含 account-uuid 外层 id。"""
	rec = ('{"id":"account-12345678-1234-1234-1234-123456789abc",'
		'"site_url":"https://demo.com","site_name":"Demo",'
		'"account_info":{"id":42,"username":"u1","access_token":"TK"}}')
	escaped = rec.replace('\\', '\\\\').replace('"', '\\"')
	return ('{"db/save/1":"' + escaped + '"}').encode()


def test_导入端点_预览不落盘(client, tmp_path):
	r = client.post('/api/sites/import-hub?apply=false', files=[('files', ('000003.log', ldb_record(), 'application/octet-stream'))])
	d = r.json()
	assert d['success'] and d['applied'] is False
	assert d['accounts_in_upload'] == 1 and d['changed_accounts'][0]['action'] == '新增账号'
	assert not (tmp_path / 'demo_accounts.json').read_text(encoding='utf-8').strip() != '[]' or True
	assert json.loads((tmp_path / 'demo_accounts.json').read_text(encoding='utf-8')) == [], '预览不得落盘'


def test_导入端点_apply落盘并幂等(client, tmp_path):
	f = [('files', ('000003.log', ldb_record(), 'application/octet-stream'))]
	r1 = client.post('/api/sites/import-hub?apply=true', files=f)
	assert r1.json()['applied'] is True
	accs = json.loads((tmp_path / 'demo_accounts.json').read_text(encoding='utf-8'))
	assert accs[0]['user_id'] == '42' and accs[0]['access_token'] == 'TK'
	r2 = client.post('/api/sites/import-hub?apply=true', files=f)
	assert r2.json()['changed_accounts'] == [], '二次导入幂等'


def test_导入端点_非leveldb文件给出人话(client):
	r = client.post('/api/sites/import-hub', files=[('files', ('readme.txt', b'hello', 'text/plain'))])
	assert r.json()['success'] is False and '.log/.ldb' in r.json()['error']


def test_导入端点_无账号数据报错(client):
	r = client.post('/api/sites/import-hub', files=[('files', ('000003.log', b'{"unrelated":1}', 'application/octet-stream'))])
	assert r.json()['success'] is False and '识别' in r.json()['error']
