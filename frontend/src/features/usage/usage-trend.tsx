import { useMemo, useState } from "react";
import { Area, Bar, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartLegend, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Spinner } from "@/components/ui/spinner";
import { DashboardPanel } from "@/features/dashboard/dashboard-panel";
import type { DayPoint } from "@/features/usage/usage-stats";
import { EmptyState } from "@/shared/components/data-state";
import { cn } from "@/shared/lib/cn";

/**
 * 三合一趋势图：余额（Area）+ 每日消耗+ 签到收益（Line 虚线）。
 * 结构照搬 grok2api 的 dashboard-trend.tsx（图例可点击隐藏系列、双 Y 轴随可见系列
 * 自动重排、自定义 tooltip），只换了数据源与系列含义。
 */
type TrendSeries = "balance" | "spend" | "gain";
type AxisSide = "left" | "right";

const TREND_SERIES: TrendSeries[] = ["balance", "spend", "gain"];

const money = (v: number) => `$${v.toFixed(2)}`;

export function UsageTrend({ days, loading, title = "余额与消耗趋势" }: { days: DayPoint[]; loading: boolean; title?: string }) {
  const [hiddenSeries, setHiddenSeries] = useState<Set<TrendSeries>>(() => new Set());

  const chartData = useMemo(
    () => days.map((d) => ({ ...d, label: d.date.slice(5).replace("-", "/") })),
    [days],
  );

  const chartConfig = useMemo<ChartConfig>(
    () => ({
      balance: { label: "总余额", theme: { light: "oklch(0.68 0.15 245)", dark: "oklch(0.74 0.13 245)" } },
      spend: { label: "每日消耗", theme: { light: "oklch(0.7 0.11 160)", dark: "oklch(0.73 0.1 160)" } },
      gain: { label: "签到收益", theme: { light: "oklch(0.76 0.12 80)", dark: "oklch(0.8 0.13 80)" } },
    }),
    [],
  );

  const hasData = days.some((d) => d.balance > 0 || d.spend > 0 || d.gain > 0);
  const axisSides = resolveAxes(hiddenSeries);

  function toggleSeries(series: TrendSeries): void {
    setHiddenSeries((current) => {
      const next = new Set(current);
      if (next.has(series)) next.delete(series);
      else next.add(series);
      return next;
    });
  }

  return (
    <DashboardPanel id="usage-trend-title" title={title} className="h-full min-h-[360px]">
      {!loading && !hasData ? (
        <div className="flex h-[280px] items-center justify-center">
          <EmptyState message="暂无用量数据" />
        </div>
      ) : (
        <div className="relative" aria-busy={loading}>
          <ChartContainer config={chartConfig} className={cn("h-[280px] w-full aspect-auto", loading && "opacity-40")}>
            <ComposedChart accessibilityLayer data={chartData} margin={{ left: 0, right: 4, top: 10, bottom: 0 }}>
              <defs>
                <linearGradient id="usage-balance-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-balance)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-balance)" stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={10} minTickGap={20} />
              <YAxis
                yAxisId="balance"
                hide={!axisSides.balance}
                orientation={axisSides.balance ?? "left"}
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                width={axisSides.balance ? 52 : 0}
                allowDecimals={false}
                tickFormatter={(value) => money(Number(value))}
              />
              <YAxis
                yAxisId="spend"
                hide={!axisSides.spend}
                orientation={axisSides.spend ?? "right"}
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                width={axisSides.spend ? 48 : 0}
                allowDecimals
                tickFormatter={(value) => money(Number(value))}
              />
              <YAxis
                yAxisId="gain"
                hide={!axisSides.gain}
                orientation={axisSides.gain ?? "right"}
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                width={axisSides.gain ? 48 : 0}
                allowDecimals
                tickFormatter={(value) => money(Number(value))}
              />
              <ChartTooltip
                cursor={false}
                content={(
                  <ChartTooltipContent
                    className="w-56 max-w-[calc(100vw-2rem)]"
                    indicator="dot"
                    labelFormatter={(_label, payload) => payload?.[0]?.payload?.date ?? ""}
                    formatter={(value, name, item) => (
                      <div className="flex w-full items-center justify-between gap-4">
                        <span className="flex min-w-0 items-center gap-2 text-xs font-normal text-muted-foreground">
                          <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: item.color || `var(--color-${String(name)})` }} />
                          <span className="truncate">{chartConfig[String(name)]?.label ?? String(name)}</span>
                        </span>
                        <span className="font-data shrink-0 text-xs font-normal tabular-nums text-muted-foreground">
                          {money(Number(value))}
                        </span>
                      </div>
                    )}
                  />
                )}
              />
              <Bar
                yAxisId="spend"
                dataKey="spend"
                fill="var(--color-spend)"
                fillOpacity={0.42}
                hide={hiddenSeries.has("spend")}
                maxBarSize={32}
                radius={[3, 3, 0, 0]}
                animationDuration={700}
                animationEasing="ease-out"
              />
              <Area
                yAxisId="balance"
                dataKey="balance"
                type="monotone"
                stroke="var(--color-balance)"
                strokeWidth={1.5}
                fill="url(#usage-balance-fill)"
                hide={hiddenSeries.has("balance")}
                dot={false}
                activeDot={{ r: 3, fill: "var(--color-balance)", stroke: "var(--color-background)", strokeWidth: 2 }}
                animationDuration={700}
                animationEasing="ease-out"
              />
              <Line
                yAxisId="gain"
                dataKey="gain"
                type="monotone"
                stroke="var(--color-gain)"
                strokeWidth={1.25}
                strokeDasharray="5 4"
                hide={hiddenSeries.has("gain")}
                dot={false}
                activeDot={{ r: 3, fill: "var(--color-gain)", stroke: "var(--color-background)", strokeWidth: 2 }}
                animationDuration={700}
                animationEasing="ease-out"
              />
              <ChartLegend content={<TrendLegend config={chartConfig} hiddenSeries={hiddenSeries} onToggle={toggleSeries} />} />
            </ComposedChart>
          </ChartContainer>
          {loading ? <div className="absolute inset-0 flex items-center justify-center"><Spinner className="size-5" /></div> : null}
        </div>
      )}
    </DashboardPanel>
  );
}

function TrendLegend({ config, hiddenSeries, onToggle }: { config: ChartConfig; hiddenSeries: Set<TrendSeries>; onToggle: (series: TrendSeries) => void }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-2 pt-3 text-xs text-muted-foreground">
      {TREND_SERIES.map((series) => {
        const hidden = hiddenSeries.has(series);
        const label = config[series]?.label ?? series;
        return (
          <button
            key={series}
            type="button"
            className={cn("flex items-center gap-1.5 rounded-md px-2 py-1 transition-[background-color,color,opacity] hover:bg-accent hover:opacity-100", hidden && "opacity-35")}
            onClick={() => onToggle(series)}
            aria-pressed={!hidden}
            aria-label={`${hidden ? "显示" : "隐藏"} ${String(label)}`}
          >
            {series === "spend" ? (
              <span className="size-2 shrink-0 rounded-[2px]" style={{ backgroundColor: "var(--color-spend)" }} />
            ) : (
              <span
                className={cn("w-3 shrink-0 border-t", series === "gain" && "border-dashed")}
                style={{ borderColor: `var(--color-${series})` }}
              />
            )}
            <span>{label}</span>
          </button>
        );
      })}
    </div>
  );
}

function resolveAxes(hiddenSeries: ReadonlySet<TrendSeries>): Partial<Record<TrendSeries, AxisSide>> {
  const visible = TREND_SERIES.filter((series) => !hiddenSeries.has(series));
  if (visible.length === 3) return { balance: "left", spend: "right" };
  if (visible.length === 2) {
    if (visible.includes("balance")) {
      return { balance: "left", [visible.includes("spend") ? "spend" : "gain"]: "right" };
    }
    return { gain: "left", spend: "right" };
  }
  return visible.length === 1 ? { [visible[0]!]: "left" } : {};
}
