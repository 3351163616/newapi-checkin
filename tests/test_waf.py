import asyncio

import pytest

try:
    from playwright.async_api import async_playwright
except ImportError:
    # playwright 只在手动验证 WAF 时才装（见 CLAUDE.md），平时不进依赖，收集时直接跳过
    pytest.skip('playwright 未安装（test_waf.py 仅用于手动验证 WAF 绕过）', allow_module_level=True)


async def test():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            print("[1] 浏览器已启动，正在访问登录页...")
            await page.goto("https://anyrouter.top/login", wait_until="networkidle")
            print("[2] 页面加载完成")
            cookies = await page.context.cookies()
            print(f"[3] 获取到 {len(cookies)} 个 cookies:")
            for c in cookies:
                print(f"    {c['name']} = {c['value'][:30]}...")
            await browser.close()
            print("[4] 测试完成")
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")

asyncio.run(test())
