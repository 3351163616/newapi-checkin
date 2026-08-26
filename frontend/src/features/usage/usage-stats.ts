/**
 * 用量统计的纯计算层：全部是无副作用的纯函数，输入是 GET /api/usage/history 的
 * { "YYYY-MM-DD": { "provider:账号名": {used, quota, used0} } }，输出各种视图形状。
 * 与展示组件严格分离——方便单测，也方便灌 mock 数据验证视觉效果。
 *
 * 关键口径（与后端 balance_server.py:4066-4092 对齐）：
 * - 余额 = quota - used（美元）
 * - 每日消耗 = used - used0（当天基线）
 * - 签到收益：后端没有持久化签到历史（checkin_state.date 每轮覆盖），从 quota 的
 *   日增量反推——某天 quota 比前一天高 = 那天签到成功。这是近似值，UI 必须标注。
 */

import type { UsageHistory, UsageDayMap } from "@/types";

export interface DayPoint {
  date: string;
  /** 全账号余额合计 */
  balance: number;
  /** 全账号当日消耗合计 */
  spend: number;
  /** 全账号签到收益合计（quota 日增量） */
  gain: number;
}

export interface AccountStat {
  /** usage key：provider:账号名 */
  key: string;
  provider: string;
  name: string;
  balance: number;
  /** 窗口期内总消耗 */
  spend: number;
  /** 近 7 日日均消耗 */
  burnRate7: number;
  /** 近 30 日日均消耗 */
  burnRate30: number;
  /** 签到收益合计（quota 日增量） */
  gain: number;
  /** 按近 7 日速率推算的剩余天数；消耗为 0 或无数据时为 null */
  daysLeft: number | null;
  /** 热力图：每一天是否签到成功（按 quota 增量反推） */
  signedDays: Set<string>;
}

export interface UsageStats {
  days: DayPoint[];
  accounts: AccountStat[];
  providers: { provider: string; balance: number }[];
  dateRange: [string, string] | null;
}

const round2 = (n: number) => Math.round(n * 100) / 100;

export function computeUsageStats(history: UsageHistory, lowBalanceThreshold = 10): UsageStats {
  const dates = Object.keys(history.history).sort();
  if (dates.length === 0) {
    return { days: [], accounts: [], providers: [], dateRange: null };
  }

  // ── 按天聚合 ──
  const days: DayPoint[] = dates.map((date) => {
    const day = history.history[date]!;
    let balance = 0;
    let spend = 0;
    for (const entry of Object.values(day)) {
      balance += entry.quota - entry.used;
      spend += Math.max(0, entry.used - entry.used0);
    }
    // gain 先置 0：精确的逐日签到收益在下面用 computeDailyGains 回填
    // （按天聚合时拿不到前一日的 quota，在这里算不出增量）
    return { date, balance: round2(balance), spend: round2(spend), gain: 0 };
  });

  // ── 按账号聚合 ──
  const lastDay = history.history[dates[dates.length - 1]!]!;
  const accountKeys = Object.keys(lastDay);
  const accountStats: AccountStat[] = [];
  const providerTotals = new Map<string, number>();

  for (const key of accountKeys) {
    const [provider, ...rest] = key.split(":");
    const name = rest.join(":");
    let spend = 0;
    let gain = 0;
    let prevQuota: number | null = null;
    const signedDays = new Set<string>();
    const spends: number[] = [];

    for (const date of dates) {
      const entry = history.history[date]![key];
      if (!entry) {
        prevQuota = null;
        continue;
      }
      if (prevQuota !== null && entry.quota > prevQuota) {
        gain += entry.quota - prevQuota;
        signedDays.add(date);
      }
      prevQuota = entry.quota;
      const daySpend = Math.max(0, entry.used - entry.used0);
      spend += daySpend;
      spends.push(daySpend);
    }

    const last = lastDay[key]!;
    const balance = round2(last.quota - last.used);
    const last7 = spends.slice(-7);
    const last30 = spends.slice(-30);
    const burn7 = last7.length > 0 ? last7.reduce((a, b) => a + b, 0) / last7.length : 0;
    const burn30 = last30.length > 0 ? last30.reduce((a, b) => a + b, 0) / last30.length : 0;

    accountStats.push({
      key,
      provider,
      name,
      balance,
      spend: round2(spend),
      burnRate7: round2(burn7),
      burnRate30: round2(burn30),
      gain: round2(gain),
      daysLeft: burn7 > 0.005 ? round2(balance / burn7) : null,
      signedDays,
    });

    providerTotals.set(provider, (providerTotals.get(provider) ?? 0) + balance);
  }

  // 签到收益回填到天数序列（按天聚合时拿不到前一日的 quota，这里单独算精确日增量）
  const gainsByDay = computeDailyGains(history.history, dates);
  for (const point of days) point.gain = gainsByDay.get(point.date) ?? 0;

  void lowBalanceThreshold;
  return {
    days,
    accounts: accountStats.sort((a, b) => b.spend - a.spend),
    providers: [...providerTotals.entries()]
      .map(([provider, balance]) => ({ provider, balance: round2(balance) }))
      .sort((a, b) => b.balance - a.balance),
    dateRange: [dates[0]!, dates[dates.length - 1]!],
  };
}

/** 逐日签到收益（精确值）：当天各账号 quota 相对前一日（断档则视为新基线，不计收益）的增量合计 */
export function computeDailyGains(history: Record<string, UsageDayMap>, dates: string[]): Map<string, number> {
  const gains = new Map<string, number>();
  const prevQuotaByKey = new Map<string, number>();
  for (const date of dates) {
    let dayGain = 0;
    for (const [key, entry] of Object.entries(history[date]!)) {
      const prev = prevQuotaByKey.get(key);
      if (prev !== undefined && entry.quota > prev) dayGain += entry.quota - prev;
      prevQuotaByKey.set(key, entry.quota);
    }
    gains.set(date, round2(dayGain));
  }
  return gains;
}

/** 环比：后 N 天均值 vs 前 N 天均值的百分比变化；基数不足或为 0 时返回 null */
export function periodOverPeriod(days: DayPoint[], windowDays: number): number | null {
  if (days.length < windowDays * 2) return null;
  const recent = days.slice(-windowDays);
  const before = days.slice(-windowDays * 2, -windowDays);
  const avg = (arr: DayPoint[]) => arr.reduce((s, d) => s + d.spend, 0) / arr.length;
  const a = avg(recent);
  const b = avg(before);
  if (b <= 0) return null;
  return round2(((a - b) / b) * 100);
}

/** 账号健康度分级 */
export type HealthLevel = "exhausted" | "low" | "idle" | "ok";

export function healthLevel(stat: AccountStat, lowBalanceThreshold = 10): HealthLevel {
  if (stat.balance <= 0.01) return "exhausted";
  if (stat.balance < lowBalanceThreshold) return "low";
  if (stat.burnRate30 < 0.01) return "idle";
  return "ok";
}

export const healthLabel: Record<HealthLevel, string> = {
  exhausted: "已耗尽",
  low: "余额告警",
  idle: "长期闲置",
  ok: "正常",
};

export const healthTextClass: Record<HealthLevel, string> = {
  exhausted: "text-checkin-failed",
  low: "text-balance-low",
  idle: "text-muted-foreground",
  ok: "text-checkin-done",
};
