/**
 * 纯格式化 / 解码函数，不含网络请求也不依赖 React —— 对照旧前端逐项移植，
 * 语义必须保持一致（尤其 cookie 剩余天数的解码算法，四个分级颜色的阈值）。
 *
 * 来源：templates/index.html 的 maskToken(:1236)、sessionDaysLeft/cookieExpiryBadge(:2454/:2466)、
 * todayUsedOf(:1861)。
 */

import type { QueryResult } from "@/types";

/** token/密钥掩码：前 6…后 4，短值直接打码。用于账号名旁的辅助信息，不用于校验。 */
export function maskToken(token: string | null | undefined): string {
  if (!token) return "";
  if (token.length <= 8) return "****";
  return `${token.slice(0, 6)}...${token.slice(-4)}`;
}

/**
 * 解码 gorilla session cookie，算出剩余有效天数（首段为签名 unix 秒，有效期固定 30 天）。
 * 解不出来（格式不对/不是这种 cookie）返回 null，调用方应展示为「--」而不是 0。
 */
export function sessionDaysLeft(session: string | null | undefined): number | null {
  try {
    let b64 = (session ?? "").replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    const raw = atob(b64);
    const ts = Number.parseInt(raw.split("|")[0] ?? "", 10);
    if (!ts || ts < 1_000_000_000) return null;
    return ((ts + 2_592_000) * 1000 - Date.now()) / 86_400_000;
  } catch {
    return null;
  }
}

export type CookieExpiryLevel = "expired" | "critical" | "warning" | "ok";

export interface CookieExpiryInfo {
  level: CookieExpiryLevel;
  days: number;
  label: string;
}

/** cookie 到期徽标：≤0 天已过期，≤3 危急，≤7 警告，其余正常。阈值与旧前端一致。 */
export function cookieExpiryInfo(session: string | null | undefined): CookieExpiryInfo | null {
  const raw = sessionDaysLeft(session);
  if (raw === null) return null;
  if (raw <= 0) return { level: "expired", days: 0, label: "已过期" };
  const days = Math.floor(raw);
  const level: CookieExpiryLevel = days <= 3 ? "critical" : days <= 7 ? "warning" : "ok";
  return { level, days, label: `剩 ${days} 天` };
}

/**
 * 今日用量 = 当前已用 − 今日基线已用；没有基线（当天无快照或账号是今天新加的）返回 null，
 * 调用方展示为「--」。`used0` 由调用方按 `provider:账号名` 从 usage baseline 里查出来再传入，
 * 这里不关心 key 的拼法（token/cookie 用 "anyrouter"，login 用 "agentrouter"，站点用 site.id，
 * 已由后端 record_account_usage() 的调用约定固定）。
 */
export function computeTodayUsed(result: QueryResult | undefined, used0: number | undefined): number | null {
  if (!result || !result.success || used0 === undefined) return null;
  const diff = result.used - used0;
  return diff > 0 ? Math.round(diff * 100) / 100 : 0;
}

/** 金额展示：null/undefined 一律 "--"，避免把「没查询」和「查到 0」混为一谈。 */
export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `$${value.toFixed(2)}`;
}
