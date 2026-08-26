/**
 * 可视化 ↔ JSON 双模式的纯转换逻辑（不含 React、不含网络请求）。
 *
 * 对照 docs/frontend-contract.md 「关键交互行为 · 可视化 ↔ JSON 双模式」与
 * templates/index.html 的 syncToJsonView(:1161) / syncFromJsonView(:1170) 移植：
 *
 * JSON 数组按字段特征分流（顺序不可颠倒——一个条目可能同时满足多个粗略条件）：
 *   1. 有 access_token 且 provider 命中已注册站点 id → 该站点账号
 *   2. 有 access_token（其它）→ token 账号
 *   3. 有 username + password → login 账号
 *   4. 其余 → cookie 账号
 *
 * 站点被删除后，指向它的记录不会丢——第 1 条不命中时会顺势落进第 2 条的 token 桶。
 */

import type { CookieAccount, LoginAccount, NewapiSite, SiteAccount, TokenAccount } from "@/types";

import type { DedupeBuckets } from "@/features/accounts/account-dedupe";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

export interface JsonClassifyResult {
  buckets: DedupeBuckets;
  /** 数组里形状完全无法识别（非对象）的条目数，供界面提示「已忽略 N 条」 */
  skipped: number;
}

/** 把 JSON.parse 后的数组分流成四类账号桶。调用方负责保证传入的确实是数组。 */
export function classifyJsonAccounts(parsed: unknown[], sites: readonly NewapiSite[]): JsonClassifyResult {
  const siteIds = new Set(sites.map((site) => site.id));
  const token: TokenAccount[] = [];
  const cookie: CookieAccount[] = [];
  const login: LoginAccount[] = [];
  const site: Record<string, SiteAccount[]> = {};
  for (const s of sites) site[s.id] = [];

  let skipped = 0;
  for (const raw of parsed) {
    if (!isPlainObject(raw)) {
      skipped += 1;
      continue;
    }
    const name = str(raw.name);
    const accessToken = typeof raw.access_token === "string" ? raw.access_token : "";
    const provider = typeof raw.provider === "string" ? raw.provider : "";

    if (accessToken && siteIds.has(provider)) {
      site[provider].push({ name, access_token: accessToken, user_id: str(raw.user_id) });
    } else if (accessToken) {
      token.push({ name, access_token: accessToken, user_id: str(raw.user_id), provider: provider || "anyrouter" });
    } else if (typeof raw.username === "string" && raw.username && typeof raw.password === "string" && raw.password) {
      login.push({ name, username: raw.username, password: raw.password });
    } else {
      const rawCookies = isPlainObject(raw.cookies) ? raw.cookies : {};
      const cookies: Record<string, string> = {};
      for (const [key, value] of Object.entries(rawCookies)) cookies[key] = str(value);
      cookie.push({ name, cookies, api_user: str(raw.api_user) });
    }
  }
  return { buckets: { token, cookie, login, site }, skipped };
}

/** 四类账号桶合并序列化为一份 JSON 文本；站点账号靠 provider:"<site_id>" 标记，保证往返无损。 */
export function serializeAccountsToJson(buckets: DedupeBuckets, sites: readonly NewapiSite[]): string {
  const merged: unknown[] = [...buckets.token, ...buckets.cookie, ...buckets.login];
  for (const site of sites) {
    for (const account of buckets.site[site.id] ?? []) {
      merged.push({ ...account, provider: site.id });
    }
  }
  return JSON.stringify(merged, null, 2);
}

/** JSON 面板的「加载示例」——三种格式各给一条，字段名与真实账号完全一致。 */
export const exampleAccountsJson = JSON.stringify(
  [
    { name: "示例账号1（Token 方式）", access_token: "your_access_token_here", user_id: "12345", provider: "anyrouter" },
    { name: "示例账号2（Cookie 方式）", cookies: { session: "your_session_cookie" }, api_user: "67890" },
    { name: "示例账号3（new-api 站点，provider 填站点 ID）", access_token: "your_access_token_here", user_id: "11962", provider: "gorouter" },
  ],
  null,
  2,
);
