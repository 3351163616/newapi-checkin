"""All API Hub 浏览器插件数据导入域：LevelDB 文本解析、hub 数据整理、增量合并。

数据源两种入口共用本模块：
- CLI：scripts/extract_hub_accounts.py --merge（读本机 Chrome/Edge 的 LevelDB 文件）
- Web：POST /api/sites/import-hub（前端 webkitdirectory 选目录上传 .log/.ldb 文件）

解析按内容特征（"id":"account-<uuid>"）提取，不依赖文件路径 —— 用户选错目录时
无关文件解析不出账号，天然安全。merge_import 与 CLI 语义完全一致（保留开关、
非 hub 站点、历史文件名映射、本地独有账号；agentrouter.org 跳过）。
"""

import json
import re
from pathlib import Path

# 插件商店 ID：Chrome 与 Edge 各一个（后端只按内容特征解析，不依赖 ID；
# 这两个常量供 CLI 定位本机数据源使用）
EXT_IDS = ("lapnciffpekdengooeolaienkeoilfeo", "pcokpjaffghgipcgjhapgdpeddlhblaa")

_ACCOUNT_RE = re.compile(r'"id":"account-[0-9a-f-]{36}"')


def extract_objects(data: str) -> list[dict]:
	"""从 LevelDB 原始文本提取账号对象（value 是 JSON 字符串，内部 JSON 被转义一层）。"""
	unescaped = data.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
	objs: list[dict] = []
	seen: set[int] = set()
	for m in _ACCOUNT_RE.finditer(unescaped):
		start = m.start() - 1
		if start in seen:
			continue
		seen.add(start)
		try:
			obj = json.JSONDecoder().raw_decode(unescaped, start)[0]
		except Exception:
			continue
		if isinstance(obj, dict) and obj.get("site_url"):
			objs.append(obj)
	return objs


def site_id_from_url(url: str) -> str:
	"""hub 站点 URL → 注册表站点 id（host 的点变横线；id 决定路径与数据文件名）"""
	host = url.split("//")[-1].split("/")[0]
	host = host.split(":")[0]
	return host.replace(".", "-")


def build_hub(objs: list[dict]) -> tuple[dict[str, dict[str, dict]], int]:
	"""原始对象列表 → ({sid: {user_id: {name, token, label, domain}}}, 跳过的空 token 数)。

	LevelDB 按写入顺序追加、快照历史会重复出现 → 按 (site_url, account id) 去重
	保留最后一次（最新）；空 token 条目（换设备未重登）跳过——导入后也无法查询/签到。
	"""
	dedup: dict[tuple[str, str], dict] = {}
	for o in objs:
		ai = o.get("account_info") or {}
		dedup[(o["site_url"], str(ai.get("id")))] = o

	hub: dict[str, dict[str, dict]] = {}
	skipped = 0
	for (url, _aid), o in dedup.items():
		sid = site_id_from_url(url)
		ai = o.get("account_info") or {}
		token = ai.get("access_token") or ""
		if not token:
			skipped += 1
			continue
		bucket = hub.setdefault(sid, {})
		base = ai.get("username") or "acc"
		# 同站点同名账号展示名加序号（与历史导入口径一致）
		dup = sum(1 for a in bucket.values() if a["name"] == base or a["name"].startswith(base + "-"))
		name = base if dup == 0 else f"{base}-{dup + 1}"
		bucket[str(ai.get("id"))] = {
			"name": name,
			"token": token,
			"label": o.get("site_name") or sid,
			"domain": url,
		}
	return hub, skipped


def merge_import(hub: dict[str, dict[str, dict]], root: Path, dry_run: bool = False) -> dict:
	"""增量导入：新站点追加注册表尾部、已有站点按 user_id upsert（不删本地账号）。

	dry_run=True 时只计算变更计划不落盘。返回结构化结果：
	{added_sites: [{id,label,domain,accounts}], changed_accounts: [{site,name,action}],
	 unchanged_sites: n, skipped_empty: 0}（skipped_empty 由 build_hub 返回，调用方合并）。
	"""
	sites_path = root / "newapi_sites.json"
	sites = json.loads(sites_path.read_text(encoding="utf-8"))
	known = {s["id"]: s for s in sites}
	added_sites, changes = [], []
	unchanged = 0

	for sid, accs in hub.items():
		if sid == "agentrouter-org":  # 登录式专用域，token 导入无意义
			continue
		acc_path = root / (known[sid].get("accounts_file") if sid in known else f"{sid}_accounts.json")
		existing = json.loads(acc_path.read_text(encoding="utf-8")) if acc_path.exists() else []
		by_uid = {str(a["user_id"]): a for a in existing}
		before = len(changes)
		for uid, a in accs.items():
			if uid in by_uid:
				if by_uid[uid]["access_token"] != a["token"]:
					by_uid[uid]["access_token"] = a["token"]
					changes.append({"site": sid, "name": a["name"], "action": "token 更新"})
				continue
			by_uid[uid] = {"name": a["name"], "access_token": a["token"], "user_id": uid}
			changes.append({"site": sid, "name": a["name"], "action": "新增账号"})
		merged = list(by_uid.values())
		if len(changes) == before:
			unchanged += 1
			continue
		if not dry_run and merged != existing:
			acc_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
		if sid not in known and merged:
			entry = {
				"id": sid,
				"label": next(iter(accs.values()))["label"],
				"domain": next(iter(accs.values()))["domain"],
				"accounts_file": acc_path.name,
				"state_file": f"{sid}_checkin_state.json",
			}
			sites.append(entry)
			added_sites.append(entry)

	if added_sites and not dry_run:
		sites_path.write_text(json.dumps(sites, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
	return {
		"added_sites": added_sites,
		"changed_accounts": changes,
		"unchanged_sites": unchanged,
		"dry_run": dry_run,
	}
