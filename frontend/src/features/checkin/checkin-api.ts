/**
 * 签到功能的全部后端调用：四类账号的只读列表、自动签到开关、四套独立签到状态机
 * （AnyRouter cookie / AnyRouter token / AgentRouter / 各 new-api 站点）、Turnstile 探测、
 * Cookie 续期。只做路径 + 类型的薄封装，业务判断（今天是否已运行过、按站点分组、
 * 乐观更新等）留给调用方——具体见 checkin-format.ts 与各 panel 组件。
 *
 * `checkinKeys` 集中管理 react-query 缓存键：全站点总览（checkin-overview-section.tsx）
 * 与各 provider 面板会读取同一份账号/状态数据，键必须完全一致才能共享缓存、
 * 避免同一个接口被重复请求两次。
 */
import { apiGet, apiPost } from "@/shared/api/client";
import type {
  AccountsListPayload,
  CheckinFastResponse,
  CheckinResponse,
  CheckinResult,
  CheckinSettings,
  CheckinSettingsResponse,
  CheckinStartResponse,
  CheckinStatusResponse,
  CookieAccount,
  LoginAccount,
  RenewResponse,
  SavedConfigResponse,
  SiteAccount,
  SiteCheckinInfoResponse,
  SitesResponse,
  SiteSyncResponse,
  SiteTurnstileResponse,
  TokenAccount,
} from "@/types";

export const checkinKeys = {
  sites: ["checkin", "sites"] as const,
  cookieAccounts: ["checkin", "cookie-accounts"] as const,
  tokenAccounts: ["checkin", "token-accounts"] as const,
  loginAccounts: ["checkin", "login-accounts"] as const,
  siteAccounts: (siteId: string) => ["checkin", "site-accounts", siteId] as const,
  settings: ["checkin", "settings"] as const,
  anyrouterStatus: ["checkin", "anyrouter-status"] as const,
  loginStatus: ["checkin", "login-status"] as const,
  siteStatus: (siteId: string) => ["checkin", "site-status", siteId] as const,
  siteInfo: (siteId: string) => ["checkin", "site-info", siteId] as const,
  siteTurnstile: (siteId: string) => ["checkin", "site-turnstile", siteId] as const,
  /** 纯客户端存储，不对应任何接口：AnyRouter token 账号签到没有状态机也没有持久化
   * （后端 daily_checkin_scheduler 从不触碰它，见 balance_server.py:1425），
   * 这里借 react-query 缓存当一个「本次会话最近一次结果」的共享存储，
   * 供全站点总览（checkin-overview-section.tsx）与 token 面板共同订阅。*/
  tokenSessionResults: ["checkin", "token-session-results"] as const,
};

// ── 账号读取（只读；签到页不做账号增删改，那是 accounts 功能的职责）──────────

export function listSites(): Promise<SitesResponse> {
  return apiGet<SitesResponse>("/sites");
}

export async function listCookieAccounts(): Promise<CookieAccount[]> {
  const res = await apiGet<SavedConfigResponse>("/config");
  return res.data?.accounts ?? [];
}

export function listTokenAccounts(): Promise<AccountsListPayload<TokenAccount>> {
  return apiGet<AccountsListPayload<TokenAccount>>("/token/accounts");
}

export function listLoginAccounts(): Promise<AccountsListPayload<LoginAccount>> {
  return apiGet<AccountsListPayload<LoginAccount>>("/login-accounts/accounts");
}

export function listSiteAccounts(siteId: string): Promise<AccountsListPayload<SiteAccount>> {
  return apiGet<AccountsListPayload<SiteAccount>>(`/site/${encodeURIComponent(siteId)}/accounts`);
}

// ── 自动签到开关：<provider>_auto 布尔键 + AgentRouter 专属分钟间隔 ──────────

export function getCheckinSettings(): Promise<CheckinSettingsResponse> {
  return apiGet<CheckinSettingsResponse>("/checkin/settings");
}

/** 一次只改一部分也可以：布尔开关与分钟间隔可以分开传，未传的键后端保持原值 */
export function updateCheckinSettings(patch: Partial<CheckinSettings>): Promise<CheckinSettingsResponse> {
  return apiPost<CheckinSettingsResponse>("/checkin/settings", patch);
}

// ── AnyRouter · Cookie ────────────────────────────────────────────────────

export function getAnyrouterCheckinStatus(): Promise<CheckinStatusResponse> {
  return apiGet<CheckinStatusResponse>("/anyrouter/checkin/status");
}

/** 并发签到全部 cookie 账号（数秒完成），带进度状态机，与 AnyRouter 自动签到共用同一状态 */
export function startAnyrouterCheckin(): Promise<CheckinStartResponse> {
  return apiPost<CheckinStartResponse>("/anyrouter/checkin/start");
}

/**
 * 直接签到指定的 cookie 账号，不经过上面的进度状态机，几秒内同步返回结果。
 * `/api/anyrouter/checkin/start` 只会签到配置文件里的全部账号，选不出一个——
 * 单账号签到走这个独立、无状态的端点，传一个元素的数组即可。
 */
export function checkinCookieAccounts(accounts: CookieAccount[]): Promise<CheckinResponse> {
  return apiPost<CheckinResponse>("/checkin", { accounts });
}

export function renewCookieAccounts(names?: string[]): Promise<RenewResponse> {
  return apiPost<RenewResponse>("/anyrouter/renew", names ? { names } : {});
}

// ── AnyRouter · Token（★ 补缺口：旧前端从未给这个端点接过按钮）─────────────

/**
 * 签到 access_token 方式的 AnyRouter 账号。不传 `accounts` 则签到配置文件里的全部账号；
 * 传入一个元素即为单账号签到。这个端点没有进度状态机，也不在每日自动签到范围内
 * （daily_checkin_scheduler 只签 cookie / login 账号与各 new-api 站点），
 * 几秒内同步返回结果——结果只能靠这次调用的响应展示，刷新页面不会保留。
 */
export function checkinTokenAccounts(accounts?: TokenAccount[]): Promise<CheckinResponse> {
  return apiPost<CheckinResponse>("/token/checkin", accounts ? { accounts } : undefined);
}

export type { CheckinResult };

// ── AgentRouter（账号密码）────────────────────────────────────────────────

export function getLoginCheckinStatus(): Promise<CheckinStatusResponse> {
  return apiGet<CheckinStatusResponse>("/login-accounts/checkin/status");
}

/** 一键全签：轮换出口 IP 分批登录，约 1~2 分钟，今日已签到的账号自动跳过 */
export function startLoginCheckinFast(): Promise<CheckinFastResponse> {
  return apiPost<CheckinFastResponse>("/login-accounts/checkin/fast");
}

/** 缓慢签到：随机顺序逐个签到，账号间随机等待（间隔见 CheckinSettings.agentrouter_gap_*） */
export function startLoginCheckinSlow(): Promise<CheckinStartResponse> {
  return apiPost<CheckinStartResponse>("/login-accounts/checkin/start");
}

export function stopLoginCheckin(): Promise<{ message: string }> {
  return apiPost<{ message: string }>("/login-accounts/checkin/stop");
}

// ── 通用 new-api 站点 ─────────────────────────────────────────────────────

export function getSiteTurnstile(siteId: string): Promise<SiteTurnstileResponse> {
  return apiGet<SiteTurnstileResponse>(`/site/${encodeURIComponent(siteId)}/turnstile`);
}

export function getSiteCheckinStatus(siteId: string): Promise<CheckinStatusResponse> {
  return apiGet<CheckinStatusResponse>(`/site/${encodeURIComponent(siteId)}/checkin/status`);
}

/** 服务器端一键签到（仅 Turnstile 关闭时可用），并发数秒完成 */
export function startSiteCheckin(siteId: string): Promise<CheckinStartResponse> {
  return apiPost<CheckinStartResponse>(`/site/${encodeURIComponent(siteId)}/checkin/start`);
}

/** 浏览器脚本跑完后核对真实签到状态（GET，不挂 Turnstile），顺带刷新余额快照 */
export function syncSiteCheckin(siteId: string): Promise<SiteSyncResponse> {
  return apiPost<SiteSyncResponse>(`/site/${encodeURIComponent(siteId)}/checkin/sync`);
}

/** 只读签到状态与奖励区间，不触发签到——全站点总览的数据源，永远反映"此刻"的真实状态，
 * 不依赖任何人今天有没有点过签到按钮（不像下面的 status 状态机，可能还停在昨天）。*/
export function getSiteCheckinInfo(siteId: string): Promise<SiteCheckinInfoResponse> {
  return apiGet<SiteCheckinInfoResponse>(`/site/${encodeURIComponent(siteId)}/checkin/info`);
}
