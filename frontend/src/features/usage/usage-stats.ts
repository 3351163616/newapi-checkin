/**
 * 用量统计的纯计算层：全部是无副作用的纯函数，输入是 GET /api/usage/history 的
 * { "YYYY-MM-DD": { "provider:账号名": {used, quota, used0} } }，输出各种视图形状。
 * 与展示组件严格分离——方便单测，也方便灌 mock 数据验证视觉效果。
 *
 * 关键口径（后端 query_* 存的原始字段，见 balance_server.py query_balance_newapi 等）：
 * - quota = 剩余余额（new-api user.self 的 quota 字段，已按站点汇率折成美元）；used = 累计消耗
 * - 余额 = quota。不要减 used——那是把累计消耗重复扣一遍（agentrouter 账号会算成负数）
 * - 每日消耗 = used - used0（当天基线）
 * - 签到/充值入账（精确）：消耗会同时压低余额、推高 used，所以真实入账 =
 *   Δquota + Δused（相邻两天都有快照才计，断档视为新基线）。只把正数计入收益；
 *   「签到了但被当天消耗吃掉」时入账≈0、余额变动为负，签到本身仍是成功的——
 *   结合 signedDays（有入账的天）与 lastDelta（余额变动）即可区分这两种情况。
 */

import type { UsageHistory, UsageDayMap } from "@/types";

export interface DayPoint {
  date: string;
  /** 全账号余额合计 */
  balance: number;
  /** 全账号当日消耗合计 */
  spend: number;
  /** 全账号签到/充值入账合计（Δquota + Δused） */
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
  /** 签到/充值入账合计（Δquota + Δused） */
  gain: number;
  /** 最新一日余额变动（quota 相对前一日；首日或断档时为 null） */
  lastDelta: number | null;
  /** 按近 7 日速率推算的剩余天数；消耗为 0 或无数据时为 null */
  daysLeft: number | null;
  /** 热力图：每一天是否有入账（签到奖励或充值，按 Δquota + Δused > 0 判定） */
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
      balance += entry.quota;
      spend += Math.max(0, entry.used - entry.used0);
    }
    // gain 先置 0：逐日入账在下面用 computeDailyGains 回填
    // （按天聚合时拿不到前一日的快照，在这里算不出增量）
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
    let prevUsed: number | null = null;
    let lastDelta: number | null = null;
    const signedDays = new Set<string>();
    const spends: number[] = [];

    for (const date of dates) {
      const entry = history.history[date]![key];
      if (!entry) {
        // 断档：无「昨日」可比，余额变动与入账都以本日为新基线
        prevQuota = null;
        prevUsed = null;
        lastDelta = null;
        continue;
      }
      if (prevQuota !== null && prevUsed !== null) {
        // 真实入账 = 余额增量 + 消耗回补（两者同源，见文件头口径说明）
        const credit = entry.quota - prevQuota + (entry.used - prevUsed);
        if (credit > 0.005) {
          gain += credit;
          signedDays.add(date);
        }
        lastDelta = round2(entry.quota - prevQuota);
      }
      prevQuota = entry.quota;
      prevUsed = entry.used;
      const daySpend = Math.max(0, entry.used - entry.used0);
      spend += daySpend;
      spends.push(daySpend);
    }

    const last = lastDay[key]!;
    const balance = round2(last.quota);
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
      lastDelta,
      daysLeft: burn7 > 0.005 ? round2(balance / burn7) : null,
      signedDays,
    });

    providerTotals.set(provider, (providerTotals.get(provider) ?? 0) + balance);
  }

  // 逐日入账回填到天数序列（按天聚合时拿不到前一日快照，这里单独算）
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

/** 逐日入账（精确值）：各账号 quota+used 相对前一记录日（仅当日历相邻、断档不计）的真实增量合计 */
export function computeDailyGains(history: Record<string, UsageDayMap>, dates: string[]): Map<string, number> {
  const gains = new Map<string, number>();
  const prevByKey = new Map<string, { quota: number; used: number; date: string }>();
  for (let i = 0; i < dates.length; i++) {
    const date = dates[i]!;
    const prevDate = i > 0 ? dates[i - 1]! : null;
    let dayGain = 0;
    for (const [key, entry] of Object.entries(history[date]!)) {
      const prev = prevByKey.get(key);
      // 只有上一记录日恰好是前一天才算增量，否则是跨断档比较，会把多天的变化记到一天
      if (prev && prevDate !== null && prev.date === prevDate) {
        const credit = entry.quota - prev.quota + (entry.used - prev.used);
        if (credit > 0.005) dayGain += credit;
      }
      prevByKey.set(key, { quota: entry.quota, used: entry.used, date });
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
