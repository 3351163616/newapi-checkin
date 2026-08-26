import { cn } from "@/shared/lib/cn";

/**
 * 骨架屏：数据加载期间用脉冲灰块占位，数据到达后平滑替换，
 * 避免「spinner → 内容突然跳出来」的生硬感（grok2api 的加载过渡做法）。
 */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-muted/60", className)} />;
}

/** KPI 卡的骨架形态：与 KpiCard 同尺寸同布局，加载中先占位 */
export function KpiSkeleton() {
  return (
    <div className="rounded-lg bg-card p-4">
      <Skeleton className="h-3 w-16" />
      <Skeleton className="mt-2 h-6 w-24" />
      <Skeleton className="mt-1.5 h-3 w-20" />
    </div>
  );
}

/** 区块（图表/表格）的骨架形态 */
export function BlockSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      <Skeleton className="h-3 w-28" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}
