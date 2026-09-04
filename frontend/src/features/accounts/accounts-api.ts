/**
 * 账号管理页的网络层：只做请求/响应形状转换，不含分组、去重、展示逻辑
 * （那些在 account-ref.ts / account-rows.ts）。行号引用见 docs/frontend-contract.md 「一、后端 API 全清单」。
 */

import { apiGet, apiPost } from "@/shared/api/client";
import type {
  AccountsListPayload,
  CheckinState,
  CheckinStatusResponse,
  CookieAccount,
  LoginAccount,
  NewapiSite,
  QueryResponse,
  QueryResult,
  SavedConfig,
  SavedConfigResponse,
  SiteAccount,
  SitesResponse,
  TokenAccount,
  UsageBaseline,
} from "@/types";

// ─────────────────────────────────────────────────────────────────────────
// 读取
// ─────────────────────────────────────────────────────────────────────────

/** GET /api/sites —— 站点筛选下拉、JSON 分流、_ref 站点标签都要用到 */
export function fetchSites(): Promise<NewapiSite[]> {
  return apiGet<SitesResponse>("/sites").then((r) => r.sites);
}

/** GET /api/sites?with_counts=1（带 collect_key_ready 和各站点账号数）——站点管理页用 */
export function fetchSitesFull(): Promise<SitesResponse> {
  return apiGet<SitesResponse>("/sites?with_counts=1");
}

/** GET /api/config —— accounts 字段是 cookie 账号；email/monitor 由设置页拥有，这里只读不动 */
export function fetchSavedConfig(): Promise<SavedConfig | null> {
  return apiGet<SavedConfigResponse>("/config").then((r) => r.data);
}

/** GET /api/token/accounts */
export function fetchTokenAccounts(): Promise<TokenAccount[]> {
  return apiGet<AccountsListPayload<TokenAccount>>("/token/accounts").then((r) => r.accounts);
}

/** GET /api/login-accounts/accounts */
export function fetchLoginAccounts(): Promise<LoginAccount[]> {
  return apiGet<AccountsListPayload<LoginAccount>>("/login-accounts/accounts").then((r) => r.accounts);
}

/** GET /api/site/{id}/accounts */
export function fetchSiteAccounts(siteId: string): Promise<SiteAccount[]> {
  return apiGet<AccountsListPayload<SiteAccount>>(`/site/${encodeURIComponent(siteId)}/accounts`).then((r) => r.accounts);
}

/** GET /api/usage/today —— 今日用量基线，key 是 `provider:账号名`（provider 见各 query* 函数注释） */
export function fetchUsageBaseline(): Promise<UsageBaseline> {
  return apiGet<UsageBaseline>("/usage/today");
}

/** GET /api/anyrouter/checkin/status —— 只覆盖 cookie 账号（AnyRouter 签到只处理 cookie 类型） */
export function fetchAnyrouterCheckinStatus(): Promise<CheckinState> {
  return apiGet<CheckinStatusResponse>("/anyrouter/checkin/status").then((r) => r.status);
}

/** GET /api/login-accounts/checkin/status */
export function fetchLoginCheckinStatus(): Promise<CheckinState> {
  return apiGet<CheckinStatusResponse>("/login-accounts/checkin/status").then((r) => r.status);
}

/** GET /api/site/{id}/checkin/status */
export function fetchSiteCheckinStatus(siteId: string): Promise<CheckinState> {
  return apiGet<CheckinStatusResponse>(`/site/${encodeURIComponent(siteId)}/checkin/status`).then((r) => r.status);
}

// ─────────────────────────────────────────────────────────────────────────
// 写入
// ─────────────────────────────────────────────────────────────────────────

/** POST /api/token/accounts */
export function saveTokenAccounts(accounts: TokenAccount[]): Promise<void> {
  return apiPost<unknown>("/token/accounts", { accounts }).then(() => undefined);
}

/** POST /api/login-accounts/accounts */
export function saveLoginAccounts(accounts: LoginAccount[]): Promise<void> {
  return apiPost<unknown>("/login-accounts/accounts", { accounts }).then(() => undefined);
}

/** POST /api/site/{id}/accounts */
export function saveSiteAccounts(siteId: string, accounts: SiteAccount[]): Promise<void> {
  return apiPost<unknown>(`/site/${encodeURIComponent(siteId)}/accounts`, { accounts }).then(() => undefined);
}

/**
 * 保存 cookie 账号——`/api/config` 是 accounts / email / monitor 共用的端点，设置页拥有
 * email/monitor 字段。这里必须先整份读回当前配置、只替换 accounts，再整体写回，
 * 否则会把设置页刚保存的 SMTP/监控配置清空。
 */
export async function saveCookieAccounts(accounts: CookieAccount[]): Promise<void> {
  const current = await fetchSavedConfig();
  const merged: SavedConfig = {
    accounts,
    email: current?.email ?? { smtp_server: "", smtp_port: 465, email_user: "", email_to: "" },
    monitor: current?.monitor ?? { interval: 6, threshold: 10 },
  };
  await apiPost<unknown>("/config", merged);
}

// ─────────────────────────────────────────────────────────────────────────
// 余额查询——四个来源各自独立，provider 标签决定 usage baseline 的 key 前缀
// （balance_server.py 的 record_account_usage 调用点已核实：token/cookie 用 "anyrouter"，
// login 用 "agentrouter"，站点账号用 site.id）
// ─────────────────────────────────────────────────────────────────────────

/** POST /api/token/query */
export function queryTokenBalances(accounts: TokenAccount[]): Promise<QueryResult[]> {
  return apiPost<QueryResponse>("/token/query", { accounts }).then((r) => r.results);
}

/** POST /api/query（cookie 方式） */
export function queryCookieBalances(accounts: CookieAccount[]): Promise<QueryResult[]> {
  return apiPost<QueryResponse>("/query", { accounts }).then((r) => r.results);
}

/**
 * GET /api/login-accounts/balances —— 会按出口 IP 分批查询避开 WAF，一轮约 1~2 分钟；
 * 这个请求本身会把结果写入今日用量快照（record_account_usage），所以调用方必须等它
 * resolve 之后再取 usage/today，顺序反了会拿到查询前的旧基线。
 */
export function queryLoginBalances(): Promise<QueryResult[]> {
  return apiGet<QueryResponse>("/login-accounts/balances").then((r) => r.results);
}

/** POST /api/site/{id}/query */
export function querySiteBalances(siteId: string): Promise<QueryResult[]> {
  return apiPost<QueryResponse>(`/site/${encodeURIComponent(siteId)}/query`).then((r) => r.results);
}
