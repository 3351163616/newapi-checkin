/**
 * 生成「在目标站点浏览器 Console 里执行」的自包含签到脚本（Turnstile 站点专用）。
 * 从 templates/index.html 的 buildSiteScript(:2606) 逐行移植，五条硬约束全部保留：
 *
 * 1. credentials:'omit' —— 脚本跑在站点自己的页面上，fetch 默认 same-origin 会带上
 *    浏览器登录态的 session cookie；而 new-api 的 authHelper 是「session 优先，有 session
 *    就完全不看 Authorization 头」，随后拿 New-Api-User 跟 session 里的 id 比对，
 *    于是除了当前登录的那个账号，其余全部 401。omit 之后没有 session 才走 access_token 分支。
 *    附带好处：响应里的 Set-Cookie 也被忽略，不会动到用户的登录态。
 * 2. 请求地址用站点自己配置的相对 sign_in_path —— 绝对路径会引入 CORS。
 * 3. sitekey 绝不硬编码，来自 Turnstile 探测结果；拿不到就让脚本自我拒绝，
 *    不能拿空值渲染（tests/test_site_frontend.mjs 有断言）。
 * 4. 只用 createElement，不用 innerHTML —— 规避 Trusted Types。
 * 5. Turnstile widget 用 seq + settle() 序号隔离 —— 防止上一账号的 30s 超时回调
 *    错误地 resolve 下一账号的等待 Promise。
 *
 * 另：账号名含引号或 </script> 不会破坏脚本——所有动态值都经 JSON.stringify 注入。
 */

import type { NewapiSite, SiteAccount, SiteSyncResult, TurnstileStatus } from "@/types";

/**
 * 按 /checkin/sync 的核对结果剔除今日已签到的账号 —— sync 的 `success` 字段即「今日已签到」。
 * 查不到结果的账号保守保留 —— 最多让脚本收到一句「今日已签到」，不丢签到机会。
 */
export function filterPendingAccounts(accs: SiteAccount[], results: SiteSyncResult[]): SiteAccount[] {
  const byName = new Map<string, SiteSyncResult>();
  for (const r of results) {
    if (r && r.name) byName.set(r.name, r);
  }
  return accs.filter((a) => {
    const r = byName.get(a.name);
    return !r || r.success !== true;
  });
}

export function buildSiteScript(site: NewapiSite, accs: SiteAccount[], ts: TurnstileStatus): string {
  const payload = accs.map((a) => ({ n: a.name, t: a.access_token, u: String(a.user_id) }));
  return [
    "(async () => {",
    "  const A = " + JSON.stringify(payload) + ";",
    "  const SITEKEY = " + JSON.stringify(ts.site_key || "") + ";",
    "  if (!SITEKEY) { console.error('未拿到 sitekey，请回 Web UI 重新点一次签到'); return; }",
    "  if (!window.turnstile) {",
    "    await new Promise((res, rej) => { const s = document.createElement('script');",
    "      s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js'; s.onload = res; s.onerror = rej;",
    "      document.head.appendChild(s); });",
    "    await new Promise(r => setTimeout(r, 1500));",
    "  }",
    "  let box = document.getElementById('__ts_box');",
    "  if (!box) { box = document.createElement('div'); box.id = '__ts_box';",
    "    box.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:99999;background:#fff;padding:8px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.3)';",
    "    document.body.appendChild(box); }",
    "  let wid = null, seq = 0, pend = null;",
    "  const settle = (s, err, tok) => { if (s !== seq || !pend) return; const p = pend; pend = null;",
    "    err ? p.rej(new Error(err)) : p.res(tok); };",
    "  const getToken = () => { const s = ++seq; return new Promise((res, rej) => { pend = { res, rej };",
    "    if (wid === null) { wid = turnstile.render(box, { sitekey: SITEKEY,",
    "      callback: t => settle(seq, null, t),",
    "      'error-callback': e => settle(seq, 'Turnstile 错误 ' + e),",
    "      'timeout-callback': () => settle(seq, 'Turnstile 超时') }); }",
    "    else { turnstile.reset(wid); }",
    "    setTimeout(() => settle(s, 'Turnstile 30s 超时'), 30000); }); };",
    "  let ok = 0, already = 0, bad = 0;",
    "  console.log('%c开始签到 ' + A.length + ' 个账号', 'color:#f59e0b;font-weight:bold');",
    "  for (let i = 0; i < A.length; i++) {",
    "    const a = A[i];",
    "    try {",
    "      const tok = await getToken();",
    "      const r = await fetch(" + JSON.stringify(site.sign_in_path || "/api/user/checkin") + " + '?turnstile=' + encodeURIComponent(tok), { method: 'POST',",
    "        credentials: 'omit',",
    "        headers: { Authorization: 'Bearer ' + a.t, " + JSON.stringify(site.api_user_key || "new-api-user") + ": a.u, Accept: 'application/json' } });",
    "      const d = await r.json();",
    "      if (d.success) { ok++; console.log('[' + (i+1) + '/' + A.length + '] ✓ ' + a.n + ' 签到成功'); }",
    "      else if ((d.message || '').includes('已签')) { already++; console.log('[' + (i+1) + '/' + A.length + '] = ' + a.n + ' 今日已签到'); }",
    "      else { bad++; console.warn('[' + (i+1) + '/' + A.length + '] ✗ ' + a.n + ' ' + d.message); }",
    "    } catch (e) { bad++; console.error('[' + (i+1) + '/' + A.length + '] ✗ ' + a.n + ' ' + e.message); }",
    "  }",
    "  try { turnstile.remove(wid); } catch (e) {}",
    "  box.remove();",
    "  console.log('%c完成：新签 ' + ok + ' · 已签 ' + already + ' · 失败 ' + bad + '，回 Web UI 点「同步状态」',",
    "    'color:#10b981;font-weight:bold');",
    "})();",
  ].join("\n");
}
