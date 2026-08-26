import { useEffect, useRef, useState, type ReactNode } from "react";

import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { statusDotClass, statusToneClass } from "@/features/checkin/checkin-format";
import { cn } from "@/shared/lib/cn";
import type { CheckinAccountRunStatus } from "@/types";

/** 每个 provider 分组共用的卡片外壳：色点 + 标题 + 副标题 + 右上角动作区 + 内容 */
export function CheckinCard({
  dotClassName,
  title,
  subtitle,
  actions,
  children,
}: {
  dotClassName: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg bg-card">
      <div className="flex flex-col gap-3 border-b px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn("size-2 shrink-0 rounded-full", dotClassName)} aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="truncate text-sm font-medium">{title}</h2>
            {subtitle ? <p className="mt-0.5 truncate text-xs text-muted-foreground">{subtitle}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-1.5">{actions}</div> : null}
      </div>
      <div className="space-y-3 px-4 py-4 sm:px-5">{children}</div>
    </section>
  );
}

/** 每日 0 点自动签到开关行；`warning` 传入时整行变琥珀色（用于 Turnstile 开启的站点） */
export function AutoCheckinRow({
  checked,
  onCheckedChange,
  disabled,
  warning,
  hint,
}: {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  warning?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border p-3">
      <div className="min-w-0 space-y-1">
        <Label className="text-foreground">每日 0 点自动签到</Label>
        <p className={cn("text-xs", warning ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
          {warning ?? hint ?? "关闭后仅停止自动触发，手动签到不受影响"}
        </p>
      </div>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onCheckedChange} />
    </div>
  );
}

/**
 * 账号状态徽标：状态变化时用醒目的入场动画重播一次，制造"跃迁感"。
 * 调用方必须把 `key` 设成随状态变化的值（如 `${name}-${status}`）——
 * React 只在元素被判定为"新元素"时才会重新触发一次性入场动画，仅换 className 不会重放。
 */
export function StatusChip({
  name,
  status,
  message,
  className,
}: {
  name: string;
  status: CheckinAccountRunStatus;
  message?: string | null;
  className?: string;
}) {
  return (
    <span
      title={message || undefined}
      className={cn(
        "inline-flex animate-in items-center gap-1 rounded-full bg-secondary/70 px-2 py-0.5 text-[11px] duration-300 fade-in zoom-in-95",
        statusToneClass(status),
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", statusDotClass(status))} aria-hidden="true" />
      {name}
    </span>
  );
}

/** 数字滚动计数：目标值变化时用 rAF 缓动过渡，而不是瞬间跳变（"编排感"的一部分） */
export function AnimatedNumber({ value, durationMs = 500, className }: { value: number; durationMs?: number; className?: string }) {
  const [display, setDisplay] = useState(value);
  const displayRef = useRef(value);

  useEffect(() => {
    displayRef.current = display;
  }, [display]);

  useEffect(() => {
    const from = displayRef.current;
    if (from === value) return;
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - (1 - t) * (1 - t);
      setDisplay(Math.round(from + (value - from) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, durationMs]);

  return <span className={cn("tabular-nums", className)}>{display}</span>;
}
