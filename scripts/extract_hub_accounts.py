#!/usr/bin/env python3
"""从 All API Hub 浏览器插件的 LevelDB 存储提取站点与账号，生成 newapi-checkin 配置。

用法：
    python3 scripts/extract_hub_accounts.py            # 只输出统计与脱敏信息（dry-run）
    python3 scripts/extract_hub_accounts.py --merge    # 增量导入（推荐）：新站点追加、
                                                       # 已有站点按 user_id upsert，保留开关
    python3 scripts/extract_hub_accounts.py --write    # 全量重写（覆盖注册表与账号文件，慎用）

解析与合并逻辑在 server/hub.py（与 Web 端「从 All API Hub 导入」共用），本脚本是
读本机 Chrome/Edge LevelDB 的 CLI 薄壳。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.hub import EXT_IDS, build_hub, extract_objects, merge_import  # noqa: E402

EXT_ID, EDGE_EXT_ID = EXT_IDS

# 数据源：Chrome 各 profile + Edge
SOURCES = [
    Path.home() / "AppData/Local/Google/Chrome/User Data" / p / "Local Extension Settings" / EXT_ID
    for p in ("Default", "Profile 1", "Profile 6", "Profile 8", "Profile 16")
] + [
    Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Local Extension Settings" / EDGE_EXT_ID
]


def collect() -> list[dict]:
	objs: list[dict] = []
	for src in SOURCES:
		ldb = sorted(src.glob("*.log")) + sorted(src.glob("*.ldb"))
		if not ldb:
			print(f"[跳过] 无数据: {src}", file=sys.stderr)
			continue
		data = "".join(p.read_text("utf-8", errors="replace") for p in ldb)
		found = extract_objects(data)
		print(f"[读取] {src} -> {len(found)} 条记录")
		objs.extend(found)
	return objs


def main() -> int:
	ap = argparse.ArgumentParser()
	ap.add_argument("--write", action="store_true", help="全量重写配置文件（覆盖注册表与账号文件，慎用）")
	ap.add_argument("--merge", action="store_true", help="增量导入：新站点追加、已有站点按 user_id upsert（保留开关/非 hub 站点/历史文件名）")
	args = ap.parse_args()

	objs = collect()
	if not objs:
		print("所有数据源均无记录", file=sys.stderr)
		return 1

	hub, skipped = build_hub(objs)
	total = sum(len(a) for a in hub.values())
	print(f"共 {len(hub)} 个站点 / {total} 个有效账号（去重后）")
	for sid, accs in sorted(hub.items()):
		print(f"  {next(iter(accs.values()))['domain']} ({len(accs)})")
		for a in accs.values():
			print(f"      {a['label']} | {a['name']} | token={a['token'][:14]}...")
	if skipped:
		print(f"[跳过] {skipped} 个空 token 账号（未登录/会话已失效）")

	if not args.write and not args.merge:
		return 0

	if args.merge:
		result = merge_import(hub, Path.cwd())
		for entry in result["added_sites"]:
			print(f"  [新站点] {entry['id']} ({entry['label']})")
		for c in result["changed_accounts"]:
			print(f"  [{c['action']}] {c['site']}:{c['name']}")
		if not result["added_sites"] and not result["changed_accounts"]:
			print("无变化")
		return 0

	# --write：全量重写（危险，保留仅为兼容）
	import json
	root = Path.cwd()
	sites_json = []
	for sid, accs in hub.items():
		first = next(iter(accs.values()))
		if sid != "gorouter-app":  # gorouter 沿用历史文件名，见仓库 NewapiSite 注释
			sites_json.append({
				"id": sid, "label": first["label"], "domain": first["domain"],
				"accounts_file": f"{sid}_accounts.json", "state_file": f"{sid}_checkin_state.json",
			})
		accounts = [{"name": a["name"], "access_token": a["token"], "user_id": uid} for uid, a in accs.items()]
		(root / f"{sid}_accounts.json").write_text(
			json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
		print(f"  写入 {sid}_accounts.json ({len(accounts)} 账号)")
	(root / "newapi_sites.json").write_text(
		json.dumps(sites_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
	print(f"  写入 newapi_sites.json ({len(sites_json)} 站点)")
	return 0


if __name__ == "__main__":
	sys.exit(main())
