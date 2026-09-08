/**
 * 签到状态的展示口径：颜色语义、文案、日期比较、cookie 剩余天数解码。
 * 全部是纯函数，不依赖 React，方便在多个 panel 与总览之间复用同一套判断。
 */
import type { CheckinAccountRunStatus, CheckinState, CheckinTrigger } from "@/types";

/** 今天的日期字符串（本地时区，YYYY-MM-DD），与后端 `datetime.now().strftime('%Y-%m-%d')` 同格式 */
export function todayStr(): string {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${mm}-${dd}`;
}

/** signed/already 视为"已签到"，pending/failed 视为"未签到"——对应总览筛选框的两个桶 */
export function isCheckedStatus(status: CheckinAccountRunStatus): boolean {
  return status === "signed" || status === "already";
}

export function statusToneClass(status: CheckinAccountRunStatus): string {
  if (isCheckedStatus(status)) return "text-checkin-done";
  if (status === "failed") return "text-checkin-failed";
  return "text-checkin-pending";
}

export function statusDotClass(status: CheckinAccountRunStatus): string {
  if (isCheckedStatus(status)) return "bg-checkin-done";
  if (status === "failed") return "bg-checkin-failed";
  return "bg-checkin-pending";
}

export function statusLabel(status: CheckinAccountRunStatus): string {
  if (status === "signed") return "签到成功";
  if (status === "already") return "今日已签";
  if (status === "failed") return "签到失败";
  return "未签到";
}

export function triggerLabel(trigger: CheckinTrigger | undefined): string {
  if (trigger === "auto") return "自动触发";
  if (trigger === "fast") return "一键全签";
  if (trigger === "browser") return "浏览器脚本";
  if (trigger === "manual") return "手动触发";
  return "触发";
}

/** 运行中时的一句话状态：优先显示当前账号，其次下一次签到时间，否则给个通用提示 */
export function runningSummary(state: CheckinState): string {
  if (state.current) return `正在签到：${state.current}`;
  if (state.next_at) return `下一个账号将在 ${state.next_at} 签到`;
  return "签到进行中…";
}

export function finishedSummary(state: CheckinState): string {
  if (state.finished_at) return `完成于 ${state.finished_at}（${triggerLabel(state.trigger)}）`;
  return "尚未运行";
}

/** 一个状态机是否"看起来有内容可展示"——没跑过就别渲染空的进度条 */
export function hasRunHistory(state: CheckinState | null | undefined): state is CheckinState {
  return Boolean(state && (state.total > 0 || state.started_at));
}

/**
 * 解码 gorilla session cookie（base64url(时间戳|gob|HMAC)），算出剩余有效天数（30 天有效期）。
 * 与后端 `_session_expiry_info()`（balance_server.py:1469）互为独立实现，解码逻辑必须一致。
 * 解不出来（格式变了/空值）返回 null，调用方应展示为"未知"而不是当作已过期。
 */
export function cookieDaysLeft(session: string | undefined): number | null {
  if (!session) return null;
  try {
    let b64 = session.replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    const raw = atob(b64);
    const ts = Number.parseInt(raw.split("|")[0] ?? "", 10);
    if (!ts || ts < 1_000_000_000) return null;
    return ((ts + 2_592_000) * 1000 - Date.now()) / 86_400_000;
  } catch {
    return null;
  }
}

export type ExpiryTone = "done" | "pending" | "failed";

/** 剩余天数 → 健康度色调：>7 天正常，1~7 天预警，≤0 已过期 */
export function cookieExpiryTone(daysLeft: number): ExpiryTone {
  if (daysLeft <= 0) return "failed";
  if (daysLeft <= 7) return "pending";
  return "done";
}

export function toneTextClass(tone: ExpiryTone): string {
  return tone === "done" ? "text-checkin-done" : tone === "pending" ? "text-checkin-pending" : "text-checkin-failed";
}

/** 从任意 catch 到的错误里抠一句人话；ApiError/Error 都直接用 message，其余用兜底文案 */
export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
