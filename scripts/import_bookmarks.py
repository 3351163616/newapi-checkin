#!/usr/bin/env python3
"""从浏览器书签提取 new-api 同构站点候选，配合服务器 /api/sites/probe 探测验证。

用法：
    python3 scripts/import_bookmarks.py            # 列出书签里的候选 URL
    python3 scripts/import_bookmarks.py --probe http://127.0.0.1:8003  # 调本地/远端探测
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BOOKMARK_PATHS = [
    Path.home() / "AppData/Local/Google/Chrome/User Data" / p / "Bookmarks"
    for p in ("Default", "Profile 1", "Profile 6", "Profile 8", "Profile 16")
] + [
    Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Bookmarks",
]


def walk(node, out):
    if isinstance(node, dict):
        if node.get("type") == "url" and node.get("url"):
            out.append({"name": node.get("name", ""), "url": node["url"]})
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


# new-api 系站点的控制台路径特征（书签大多存的是控制台/登录页入口）
CONSOLE_HINT = re.compile(
    r"/(console|dashboard|token|profile|personal|login|signin)(/|$)", re.I
)
# 明显无关的域名
IGNORE_HOST = re.compile(
    r"(github\.com|google|microsoft|weixin|qq\.com|qiniu|okx|claude\.ai|chatgpt\.com|all-api-hub|anthropic|baidu|bilibili|zhihu|aliyun|jd\.com|amazon|aws)",
    re.I,
)


def extract_candidates() -> list[dict]:
    seen: set[str] = set()
    out = []
    for path in BOOKMARK_PATHS:
        if not path.exists():
            continue
        try:
            bookmarks = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = []
        walk(bookmarks.get("roots", {}), items)
        for it in items:
            url = it["url"].strip()
            m = re.match(r"https?://([^/]+)(/.*)?$", url)
            if not m:
                continue
            host = m.group(1).lower()
            if IGNORE_HOST.search(host):
                continue
            path_part = m.group(2) or ""
            # 只保留看起来像站点入口的（有控制台路径特征，或裸域名）
            if not CONSOLE_HINT.search(path_part) and path_part not in ("", "/"):
                continue
            key = host
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": it["name"], "url": url, "host": host})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", metavar="BASE", help="探测服务地址，如 http://127.0.0.1:8003")
    args = ap.parse_args()

    cands = extract_candidates()
    if not cands:
        print("书签里没有找到候选站点")
        return 0
    print(f"书签候选站点 {len(cands)} 个：")
    for c in cands:
        print(f"  {c['host']:<38} {c['name'][:30]}")

    if not args.probe:
        return 0

    import httpx  # noqa: F401  # 不引入依赖，用 urllib

    from urllib import request

    for c in cands:
        try:
            req = request.Request(
                f"{args.probe}/api/sites/probe",
                data=json.dumps({"domain": c["host"]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with request.urlopen(req, timeout=15) as resp:
                r = json.loads(resp.read())
            if r.get("success"):
                info = r.get("info", {})
                print(
                    f"  ✅ {c['host']:<38} {info.get('system_name','?')} "
                    f"v{info.get('version','?')} 签到={'开' if info.get('checkin_enabled') else '关'}"
                )
            else:
                print(f"  ❌ {c['host']:<38} {r.get('error','探测失败')[:60]}")
        except Exception as e:
            print(f"  ❌ {c['host']:<38} {type(e).__name__}: {str(e)[:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
