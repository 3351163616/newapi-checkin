import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "@/shared/lib/cn";

/**
 * KPI 统计卡：右上角语义色图标块 + 大数字 + 副行说明。
 * 对照 grok2api 的统计卡样式：数字用 tabular-nums 对齐、stagger 入场、
 * 可整体包成 Link 跳转相关页面。
 */
export function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
  iconClass = "text-muted-foreground",
  href,
  delay = 0,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: LucideIcon;
  /** 图标块的语义色 class，如 text-checkin-done / text-balance-low */
  iconClass?: string;
  href?: string;
  /** stagger 入场的 animation-delay（ms） */
  delay?: number;
}) {
  const inner = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 text-xs text-muted-foreground">{label}</p>
        {Icon ? (
          <span className={cn("shrink-0 rounded-md p-1.5", iconClass, "bg-current/10")}>
            <Icon className="size-4" aria-hidden="true" />
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 truncate font-data text-xl font-medium tabular-nums">{value}</p>
      {sub ? <p className="mt-1 truncate text-[11px] text-muted-foreground">{sub}</p> : null}
    </>
  );

  const cls = cn(
    "block animate-in fade-in slide-in-from-bottom-2 fill-mode-both rounded-lg bg-card p-4 duration-300",
    href && "transition-colors hover:bg-accent/50",
  );

  return href ? (
    <Link to={href} className={cls} style={{ animationDelay: `${delay}ms` }}>
      {inner}
    </Link>
  ) : (
    <div className={cls} style={{ animationDelay: `${delay}ms` }}>
      {inner}
    </div>
  );
}
