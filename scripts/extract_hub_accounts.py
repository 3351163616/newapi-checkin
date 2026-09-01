#!/usr/bin/env python3
"""从 All API Hub 浏览器插件的 LevelDB 存储提取站点与账号，生成 newapi-checkin 配置。

用法：python3 scripts/extract_hub_accounts.py
只输出统计与脱敏信息；用 --write 才会落盘生成配置文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXT_ID = "lapnciffpekdengooeolaienkeoilfeo"
EDGE_EXT_ID = "pcokpjaffghgipcgjhapgdpeddlhblaa"

# 数据源：Chrome 各 profile + Edge
SOURCES = [
    Path.home() / "AppData/Local/Google/Chrome/User Data" / p / "Local Extension Settings" / EXT_ID
    for p in ("Default", "Profile 1", "Profile 6", "Profile 8", "Profile 16")
] + [
    Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Local Extension Settings" / EDGE_EXT_ID
]


def extract_objects(data: str) -> list[dict]:
    # LevelDB value 是 JSON 字符串，内部 JSON 被转义一层：\" -> "
    unescaped = data.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
    pattern = re.compile(r'"id":"account-[0-9a-f-]{36}"')
    objs: list[dict] = []
    seen: set[int] = set()
    for m in pattern.finditer(unescaped):
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
    host = url.split("//")[-1].split("/")[0]
    host = host.split(":")[0]
    return host.replace(".", "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="落盘生成配置文件")
    args = ap.parse_args()

    objs = []
    for src in SOURCES:
        ldb = sorted(src.glob("*.log")) + sorted(src.glob("*.ldb"))
        if not ldb:
            print(f"[跳过] 无数据: {src}", file=sys.stderr)
            continue
        data = "".join(p.read_text(encoding="utf-8", errors="replace") for p in ldb)
        found = extract_objects(data)
        print(f"[读取] {src} -> {len(found)} 条记录")
        objs.extend(found)
    if not objs:
        print("所有数据源均无记录", file=sys.stderr)
        return 1

    # LevelDB 日志按写入顺序追加，快照历史重复出现 → 按 (site_url, account id) 去重，保留最后一次（最新）
    dedup: dict[tuple[str, str], dict] = {}
    for o in objs:
        ai = o.get("account_info") or {}
        key = (o["site_url"], str(ai.get("id")))
        dedup[key] = o

    sites: dict[str, list[dict]] = {}
    for o in dedup.values():
        sites.setdefault(o["site_url"], []).append(o)

    print(f"共 {len(sites)} 个站点 / {len(dedup)} 个账号（去重后）")
    for url, accs in sites.items():
        print(f"  {url} ({len(accs)})")
        for o in accs:
            ai = o.get("account_info") or {}
            tok = ai.get("access_token") or ""
            print(
                f"      {o.get('site_name')} | {ai.get('username')} | "
                f"id={ai.get('id')} | token={tok[:14]}..."
            )

    if not args.write:
        return 0

    root = Path.cwd()
    sites_json = []
    anyrouter_accounts = []
    for url, accs in sites.items():
        sid = site_id_from_url(url)
        label = accs[0].get("site_name") or sid
        site = {
            "id": sid,
            "label": label,
            "domain": url,
            "accounts_file": f"{sid}_accounts.json",
            "state_file": f"{sid}_checkin_state.json",
        }
        if sid != "anyrouter-top":
            sites_json.append(site)
        accounts = []
        name_counts: dict[str, int] = {}
        for o in accs:
            ai = o.get("account_info") or {}
            username = ai.get("username") or "acc"
            name_counts[username] = name_counts.get(username, 0) + 1
            name = username if name_counts[username] == 1 else f"{username}-{name_counts[username]}"
            account = {
                "name": name,
                "access_token": ai.get("access_token"),
                "user_id": ai.get("id"),
            }
            if sid == "anyrouter-top":
                # anyrouter.top 走项目专用逻辑（ANYROUTER_CONFIG），存 new_accounts_config.json
                anyrouter_accounts.append({**account, "provider": "anyrouter"})
            else:
                accounts.append(account)
        if accounts:
            (root / f"{sid}_accounts.json").write_text(
                json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"  写入 {sid}_accounts.json ({len(accounts)} 账号)")

    (root / "newapi_sites.json").write_text(
        json.dumps(sites_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  写入 newapi_sites.json ({len(sites_json)} 站点)")
    if anyrouter_accounts:
        (root / "new_accounts_config.json").write_text(
            json.dumps(anyrouter_accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  写入 new_accounts_config.json ({len(anyrouter_accounts)} anyrouter 账号)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
