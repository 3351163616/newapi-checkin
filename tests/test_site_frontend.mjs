/**
 * templates/index.html 里通用站点逻辑的离线测试。
 *
 * 做法：从 HTML 里抓出最大的 <script> 块，用正则取出待测函数源码，在带最小 DOM 替身的
 * 沙箱里 eval 执行。不起浏览器、不发请求。
 *   node test_site_frontend.mjs
 */

import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const html = readFileSync(new URL("../templates/index.html", import.meta.url), "utf8");
const js = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).sort((a, b) => b.length - a.length)[0];

/** 抓出一个函数的完整源码。先跳过参数表再数花括号 —— 形如 `options = {}` 的默认值会骗过朴素计数。 */
function grab(name) {
  const start = js.search(new RegExp(`(async\\s+)?function\\s+${name}\\s*\\(`));
  assert.notEqual(start, -1, `找不到函数 ${name}`);
  let i = js.indexOf("(", start);
  let depth = 1;
  while (depth > 0) {
    i++;
    if (js[i] === "(") depth++;
    else if (js[i] === ")") depth--;
  }
  i = js.indexOf("{", i);
  depth = 1;
  let j = i;
  while (depth > 0) {
    j++;
    if (js[j] === "{") depth++;
    else if (js[j] === "}") depth--;
  }
  return js.slice(start, j + 1);
}

/** 取一个顶层 const/let 声明（用于 ACCENTS 之类的表）。 */
function grabDecl(name) {
  const re = new RegExp(`(const|let)\\s+${name}\\s*=`);
  const start = js.search(re);
  assert.notEqual(start, -1, `找不到声明 ${name}`);
  let i = js.indexOf("=", start);
  let depth = 0;
  let j = i;
  for (;;) {
    j++;
    const c = js[j];
    if (c === "{" || c === "[") depth++;
    else if (c === "}" || c === "]") depth--;
    else if (c === ";" && depth === 0) break;
    if (j > js.length) throw new Error("declaration not terminated");
  }
  return js.slice(start, j + 1);
}

// ===== DOM 替身 =====
const elements = new Map();
function el(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      value: "",
      textContent: "",
      innerHTML: "",
      className: "",
      href: "",
      style: {},
      disabled: false,
      _classes: new Set(),
      classList: {
        add(...c) {},
        remove(...c) {},
        toggle(c, on) {},
      },
      setAttribute() {},
      appendChild() {},
      querySelectorAll: () => [],
      focus() {},
      select() {},
      scrollIntoView() {},
    });
  }
  return elements.get(id);
}
const document = {
  getElementById: (id) => el(id),
  createElement: () => el("__tmp" + Math.random()),
};

// ===== 被测代码 =====
const src = [
  grabDecl("ACCENTS"),
  grab("accentOf"),
  grab("currentSite"),
  grab("isSiteProvider"),
  grab("autoCheckinKey"),
  grab("providerLabel"),
  grab("resolveRef"),
  grab("syncToJsonView"),
  grab("syncFromJsonView"),
  grab("buildSiteScript"),
  grab("filterPendingAccounts"),
  grab("providerAccounts"),
  grab("keyProviderLabel"),
  grab("buildKeyText"),
  grab("cssId"),
  grab("esc"),
  grab("tsDate"),
  grab("_acctKey"),
  grab("_acctTypeLabel"),
  grab("_acctId"),
].join("\n");

let newapiSites = [];
let siteAccounts = {};
let siteTurnstile = {};
let tokenAccounts = [];
let cookieAccounts = [];
let loginAccounts = [];
let currentProvider = "anyrouter";
const alerts = [];
const alert = (m) => alerts.push(m);
const saveTokenAccounts = () => {};
const saveConfig = () => {};
const saveLoginAccounts = () => {};
const saveSiteAccounts = () => {};

const ctx = {};
const fn = new Function(
  "document",
  "alert",
  "saveTokenAccounts",
  "saveConfig",
  "saveLoginAccounts",
  "saveSiteAccounts",
  "state",
  `
  let newapiSites = state.newapiSites, siteAccounts = state.siteAccounts, siteTurnstile = state.siteTurnstile;
  let tokenAccounts = state.tokenAccounts, cookieAccounts = state.cookieAccounts, loginAccounts = state.loginAccounts;
  let currentProvider = state.currentProvider;
  ${src}
  return {
    accentOf, currentSite, isSiteProvider, autoCheckinKey, providerLabel, resolveRef,
    syncToJsonView, syncFromJsonView, buildSiteScript, filterPendingAccounts,
    providerAccounts, keyProviderLabel, buildKeyText, cssId, esc, tsDate,
    _acctKey, _acctTypeLabel, _acctId,
    set(k, v) { if (k === 'newapiSites') newapiSites = v;
      else if (k === 'siteAccounts') siteAccounts = v;
      else if (k === 'siteTurnstile') siteTurnstile = v;
      else if (k === 'tokenAccounts') tokenAccounts = v;
      else if (k === 'cookieAccounts') cookieAccounts = v;
      else if (k === 'loginAccounts') loginAccounts = v;
      else if (k === 'currentProvider') currentProvider = v; },
    get(k) { return { newapiSites, siteAccounts, siteTurnstile, tokenAccounts, cookieAccounts, loginAccounts, currentProvider }[k]; },
  };
`,
)(document, alert, saveTokenAccounts, saveConfig, saveLoginAccounts, saveSiteAccounts, {
  newapiSites,
  siteAccounts,
  siteTurnstile,
  tokenAccounts,
  cookieAccounts,
  loginAccounts,
  currentProvider,
});

const SITES = [
  { id: "gorouter", label: "GoRouter", domain: "https://gorouter.app", accent: "orange", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" },
  { id: "tabitoken", label: "TaBiAI", domain: "https://tabitoken.com", accent: "sky", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" },
];

let passed = 0;
function test(name, body) {
  try {
    body();
    passed++;
    console.log("  ✓ " + name);
  } catch (e) {
    console.error("  ✗ " + name + "\n    " + e.message);
    process.exitCode = 1;
  }
}

console.log("站点识别与主题");
test("currentProvider 指向站点时能取到配置", () => {
  fn.set("newapiSites", SITES);
  fn.set("currentProvider", "tabitoken");
  assert.equal(fn.currentSite().label, "TaBiAI");
  assert.equal(fn.isSiteProvider(), true);
  fn.set("currentProvider", "anyrouter");
  assert.equal(fn.currentSite(), null);
  assert.equal(fn.isSiteProvider(), false);
});
test("站点被删后 currentSite 返回 null 而不是抛错", () => {
  fn.set("newapiSites", []);
  fn.set("currentProvider", "tabitoken");
  assert.equal(fn.currentSite(), null);
});
test("未知主题色回退到 orange，不产生 undefined 类名", () => {
  const a = fn.accentOf({ accent: "nonexistent" });
  assert.equal(a.bg, "bg-orange-600");
  assert.ok(!JSON.stringify(fn.accentOf(null)).includes("undefined"));
});
test("自动签到键名与站点 id 对应", () => {
  fn.set("newapiSites", SITES);
  fn.set("currentProvider", "tabitoken");
  assert.equal(fn.autoCheckinKey(), "tabitoken_auto");
  assert.equal(fn.providerLabel(), "TaBiAI");
  fn.set("currentProvider", "anyrouter");
  assert.equal(fn.autoCheckinKey(), "anyrouter_auto");
  assert.equal(fn.providerLabel(), "AnyRouter");
});

console.log("账号引用定位（_ref）");
test("各类型引用都能解析回正确的数组与下标", () => {
  fn.set("tokenAccounts", [{ name: "t0" }, { name: "t1" }]);
  fn.set("cookieAccounts", [{ name: "c0" }]);
  fn.set("loginAccounts", [{ name: "l0" }]);
  fn.set("siteAccounts", { gorouter: [{ name: "g0" }, { name: "g1" }], tabitoken: [{ name: "b0" }] });
  assert.equal(fn.resolveRef("token:1").arr[fn.resolveRef("token:1").idx].name, "t1");
  assert.equal(fn.resolveRef("cookie:0").arr[0].name, "c0");
  assert.equal(fn.resolveRef("login:0").arr[0].name, "l0");
  const r = fn.resolveRef("site:tabitoken:0");
  assert.equal(r.arr[r.idx].name, "b0");
  assert.equal(r.siteId, "tabitoken");
});
test("站点数量变化不会让引用错位（这是改用 _ref 的原因）", () => {
  fn.set("siteAccounts", { gorouter: [{ name: "g0" }], tabitoken: [{ name: "b0" }] });
  const before = fn.resolveRef("site:tabitoken:0");
  fn.set("newapiSites", [SITES[1]]); // 删掉 gorouter
  const after = fn.resolveRef("site:tabitoken:0");
  assert.equal(before.arr[before.idx].name, after.arr[after.idx].name);
});
test("未知引用返回 null", () => {
  assert.equal(fn.resolveRef("bogus:0"), null);
});

console.log("JSON 视图往返");
test("站点账号带 provider 标记，往返无损", () => {
  fn.set("newapiSites", SITES);
  fn.set("tokenAccounts", [{ name: "t", access_token: "x", user_id: "1", provider: "anyrouter" }]);
  fn.set("cookieAccounts", [{ name: "c", cookies: { session: "s" }, api_user: "2" }]);
  fn.set("loginAccounts", [{ name: "l", username: "u", password: "p" }]);
  fn.set("siteAccounts", { gorouter: [{ name: "g", access_token: "gt", user_id: "3" }], tabitoken: [{ name: "b", access_token: "bt", user_id: "4" }] });
  fn.syncToJsonView();
  const json = JSON.parse(el("accountInput").value);
  assert.equal(json.length, 5);
  assert.deepEqual(
    json.filter((a) => a.provider === "tabitoken").map((a) => a.name),
    ["b"],
  );
  assert.ok(fn.syncFromJsonView());
  assert.deepEqual(fn.get("siteAccounts").gorouter.map((a) => a.name), ["g"]);
  assert.deepEqual(fn.get("siteAccounts").tabitoken.map((a) => a.name), ["b"]);
  assert.deepEqual(fn.get("tokenAccounts").map((a) => a.name), ["t"]);
  assert.deepEqual(fn.get("cookieAccounts").map((a) => a.name), ["c"]);
  assert.deepEqual(fn.get("loginAccounts").map((a) => a.name), ["l"]);
});
test("provider 是未注册站点时不被吞掉，落回 AnyRouter token", () => {
  fn.set("newapiSites", SITES);
  el("accountInput").value = JSON.stringify([{ name: "x", access_token: "t", user_id: "9", provider: "removedsite" }]);
  assert.ok(fn.syncFromJsonView());
  assert.deepEqual(fn.get("tokenAccounts").map((a) => a.name), ["x"], "站点被删后账号不该凭空消失");
});
test("清空输入把所有桶清空", () => {
  fn.set("newapiSites", SITES);
  el("accountInput").value = "";
  assert.ok(fn.syncFromJsonView());
  assert.deepEqual(fn.get("siteAccounts"), { gorouter: [], tabitoken: [] });
});

console.log("去重分组键");
test("不同站点的同一 user_id 不算重复", () => {
  assert.notEqual(fn._acctKey({ user_id: "1" }, "site:gorouter"), fn._acctKey({ user_id: "1" }, "site:tabitoken"));
});
test("同站点 token 与 cookie 属同一 ID 命名空间", () => {
  assert.equal(fn._acctKey({ user_id: "5", provider: "anyrouter" }, "token"), fn._acctKey({ api_user: "5" }, "cookie"));
});
test("类型标签用站点显示名", () => {
  fn.set("newapiSites", SITES);
  assert.equal(fn._acctTypeLabel("site:tabitoken"), "TaBiAI");
  assert.equal(fn._acctTypeLabel("cookie"), "Cookie");
});
test("_acctId 按类型取对的字段", () => {
  assert.equal(fn._acctId({ type: "cookie", a: { api_user: "7" } }), "7");
  assert.equal(fn._acctId({ type: "login", a: { username: "u@x" } }), "u@x");
  assert.equal(fn._acctId({ type: "site:gorouter", a: { user_id: "9" } }), "9");
});

console.log("浏览器签到脚本生成");
test("脚本语法合法且用站点自己的路径与 header 键", () => {
  fn.set("siteAccounts", { tabitoken: [{ name: "b0", access_token: "TOK", user_id: "77" }] });
  fn.set("siteTurnstile", { tabitoken: { enabled: true, site_key: "0xSITEKEY" } });
  const s = fn.buildSiteScript({ id: "tabitoken", label: "TaBiAI", domain: "https://tabitoken.com", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" });
  new Function(s); // 语法检查
  assert.ok(s.includes("0xSITEKEY"), "sitekey 必须来自探测结果，不能硬编码");
  assert.ok(s.includes('"/api/user/checkin"'), "用相对路径，同域免 CORS");
  assert.ok(!s.includes("https://tabitoken.com/api/user/checkin"), "绝对路径会引入 CORS");
  assert.ok(s.includes('"new-api-user"'));
  assert.ok(s.includes("TOK") && s.includes("77"));
  assert.ok(!/innerHTML|insertAdjacentHTML|document\.write/.test(s), "只用 createElement，避免 Trusted Types 拦截");
  assert.ok(s.includes("settle(seq"), "序号隔离不能省：widget 回调长期注册，上一个账号的超时会打断下一个");
});
test("脚本不带 cookie，否则除登录账号外全部 401 不匹配", () => {
  fn.set("siteAccounts", { tabitoken: [{ name: "b0", access_token: "TOK", user_id: "77" }] });
  fn.set("siteTurnstile", { tabitoken: { enabled: true, site_key: "K" } });
  const s = fn.buildSiteScript({ id: "tabitoken", label: "T", domain: "https://t.com", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" });
  new Function(s);
  // new-api 的 authHelper 是 session 优先：有 session 就不看 Authorization 头，
  // 再拿 New-Api-User 与 session 里的 id 比对 → 其余账号全部 401「与登录用户不匹配」
  assert.ok(/credentials:\s*'omit'/.test(s), "fetch 默认 same-origin 会带上浏览器登录的 session cookie");
});
test("自定义了 sign_in_path 的站点也照用", () => {
  fn.set("siteAccounts", { odd: [{ name: "o", access_token: "T", user_id: "1" }] });
  fn.set("siteTurnstile", { odd: { enabled: true, site_key: "K" } });
  const s = fn.buildSiteScript({ id: "odd", label: "Odd", domain: "https://odd.io", sign_in_path: "/api/custom/checkin", api_user_key: "x-user" });
  new Function(s);
  assert.ok(s.includes('"/api/custom/checkin"'));
  assert.ok(s.includes('"x-user"'));
});
test("没有 sitekey 时脚本自我拒绝而不是拿空值去渲染", () => {
  fn.set("siteAccounts", { tabitoken: [{ name: "b", access_token: "T", user_id: "1" }] });
  fn.set("siteTurnstile", { tabitoken: { enabled: true, site_key: "" } });
  const s = fn.buildSiteScript({ id: "tabitoken", label: "T", domain: "https://t.com", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" });
  new Function(s);
  assert.ok(s.includes("if (!SITEKEY)"), "拿不到 sitekey 应提示重试，否则 widget 静默失败");
});
test("账号名里的引号不会破坏脚本", () => {
  fn.set("siteAccounts", { tabitoken: [{ name: 'a"b\'c</script>', access_token: "T", user_id: "1" }] });
  fn.set("siteTurnstile", { tabitoken: { enabled: true, site_key: "K" } });
  const s = fn.buildSiteScript({ id: "tabitoken", label: "T", domain: "https://t.com", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" });
  new Function(s);
});
test("复制脚本时按同步结果过滤：已签的剔除，查不到的保留", () => {
  const accs = [
    { name: "a", access_token: "T1", user_id: "1" },
    { name: "b", access_token: "T2", user_id: "2" },
    { name: "c", access_token: "T3", user_id: "3" },
    { name: "d", access_token: "T4", user_id: "4" },
  ];
  // /checkin/sync 的 results：success 即「今日已签到」。
  // a 已签；b 状态读取失败；c 确认未签；d 完全没有返回记录
  const results = [
    { name: "a", success: true, message: "今日已签到" },
    { name: "b", success: false, message: "HTTP 401" },
    { name: "c", success: false, message: "今日未签到" },
  ];
  const pending = fn.filterPendingAccounts(accs, results);
  assert.deepStrictEqual(pending.map((x) => x.name), ["b", "c", "d"],
    "已签的剔除；失败/缺失的保留（最多多跑一个 token，不丢签到机会）");
  assert.deepStrictEqual(fn.filterPendingAccounts(accs, null).map((x) => x.name), ["a", "b", "c", "d"],
    "拿不到清单时退回全量，别拦着签到");
});
test("buildSiteScript 用传入的过滤结果，只嵌待签账号的 token", () => {
  fn.set("siteAccounts", { tabitoken: [
    { name: "done", access_token: "TOK_DONE", user_id: "1" },
    { name: "todo", access_token: "TOK_TODO", user_id: "2" },
  ] });
  fn.set("siteTurnstile", { tabitoken: { enabled: true, site_key: "K" } });
  const s = fn.buildSiteScript({ id: "tabitoken", label: "T", domain: "https://t.com", sign_in_path: "/api/user/checkin", api_user_key: "new-api-user" },
    [{ name: "todo", access_token: "TOK_TODO", user_id: "2" }]);
  new Function(s);
  assert.ok(s.includes("TOK_TODO") && !s.includes("TOK_DONE"), "已签账号的 token 不该进脚本");
});

console.log("\nAPI 密钥管理");
test("providerAccounts 给出当前网站的账号与 _ref（与账号卡同一套寻址）", () => {
  fn.set("newapiSites", SITES);
  fn.set("tokenAccounts", [{ name: "t0" }, { name: "t1" }]);
  fn.set("cookieAccounts", [{ name: "c0" }]);
  fn.set("loginAccounts", [{ name: "L0" }]);
  fn.set("siteAccounts", { tabitoken: [{ name: "tb0" }, { name: "tb1" }] });

  fn.set("currentProvider", "anyrouter");
  assert.deepStrictEqual(fn.providerAccounts().map((a) => a._ref), ["token:0", "token:1", "cookie:0"],
    "AnyRouter 是 token + cookie 两类合起来");
  fn.set("currentProvider", "agentrouter");
  assert.deepStrictEqual(fn.providerAccounts().map((a) => a._ref), ["login:0"]);
  fn.set("currentProvider", "tabitoken");
  assert.deepStrictEqual(fn.providerAccounts().map((a) => a._ref), ["site:tabitoken:0", "site:tabitoken:1"]);
  fn.set("currentProvider", "gorouter");
  assert.deepStrictEqual(fn.providerAccounts(), [], "没账号的站点返回空数组而不是抛错");
});
test("providerAccounts 的 _ref 能被 resolveRef 解回同一个账号", () => {
  fn.set("newapiSites", SITES);
  fn.set("siteAccounts", { tabitoken: [{ name: "tb0" }, { name: "tb1" }] });
  fn.set("currentProvider", "tabitoken");
  const accs = fn.providerAccounts();
  const r = fn.resolveRef(accs[1]._ref);
  assert.equal(r.arr[r.idx].name, "tb1", "两套逻辑必须指向同一个账号，否则会给错账号建/删密钥");
});
test("keyProviderLabel 三类网站都给得出显示名", () => {
  fn.set("newapiSites", SITES);
  fn.set("currentProvider", "anyrouter");
  assert.equal(fn.keyProviderLabel(), "AnyRouter");
  fn.set("currentProvider", "agentrouter");
  assert.equal(fn.keyProviderLabel(), "AgentRouter");
  fn.set("currentProvider", "tabitoken");
  assert.equal(fn.keyProviderLabel(), "TaBiAI");
});
test("复制文本三种格式都带 sk- 前缀", () => {
  const accounts = [
    { ref: "site:tabitoken:0", name: "tb0", provider: "TaBiAI", success: true, keys: [{ id: 1, name: "cc", key: "AAA", masked: false }] },
    { ref: "site:tabitoken:1", name: "tb1", provider: "TaBiAI", success: true, keys: [{ id: 2, name: "dd", key: "BBB", masked: false }] },
  ];
  const plain = fn.buildKeyText(accounts, "key");
  assert.equal(plain.text, "sk-AAA\nsk-BBB");
  assert.equal(plain.count, 2);
  const named = fn.buildKeyText(accounts, "named");
  assert.equal(named.text, "tb0\tcc\tsk-AAA\ntb1\tdd\tsk-BBB");
  const json = JSON.parse(fn.buildKeyText(accounts, "json").text);
  assert.deepStrictEqual(json[0], { account: "tb0", provider: "TaBiAI", name: "cc", key: "sk-AAA" });
});
test("脱敏未取到的密钥不进复制内容，而是计入 skipped", () => {
  const accounts = [
    { ref: "a", name: "a", success: true, keys: [{ id: 1, name: "x", key: "AAA", masked: false }, { id: 2, name: "y", key: "BB**CC", masked: true }] },
    { ref: "b", name: "b", success: false, error: "用户已被封禁" },
  ];
  const r = fn.buildKeyText(accounts, "key");
  assert.equal(r.text, "sk-AAA", "半截的脱敏值复制出去是废的，必须排除");
  assert.equal(r.count, 1);
  assert.equal(r.skipped, 1);
});
test("没有任何密钥时 count 为 0（前端据此提示而不是复制空串）", () => {
  const r = fn.buildKeyText([{ ref: "a", name: "a", success: true, keys: [] }], "key");
  assert.equal(r.count, 0);
  assert.equal(r.text, "");
});
test("cssId 把 ref 变成合法的 DOM id", () => {
  assert.equal(fn.cssId("site:tabitoken:0"), "site_tabitoken_0");
  assert.equal(fn.cssId("token:3"), "token_3");
  assert.ok(!/[^a-zA-Z0-9_]/.test(fn.cssId("site:a-b.c:1")), "冒号/点/横杠都要清掉，否则 getElementById 取不到");
});
test("esc 挡住账号名与密钥名里的 HTML", () => {
  assert.equal(fn.esc('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(fn.esc(null), "");
  assert.equal(fn.esc(0), "0");
});
test("tsDate 把秒级时间戳格式化，永不过期给 -", () => {
  assert.equal(fn.tsDate(-1), "-");
  assert.equal(fn.tsDate(0), "-");
  assert.match(fn.tsDate(1786000000), /^\d{4}-\d{2}-\d{2}$/);
});

console.log(`\n${passed} 项通过${process.exitCode ? "，有失败" : ""}`);
