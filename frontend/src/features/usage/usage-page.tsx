import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Coins, Flame, TrendingUp } from "lucide-react";
import { Cell, Pie, PieChart } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorState, LoadingState } from "@/shared/components/data-state";
import { KpiCard } from "@/shared/components/kpi-card";
import { PageHeader } from "@/shared/components/page-header";
import { apiGet } from "@/shared/api/client";
import { siteDotClass } from "@/shared/lib/site-color";
import { cn } from "@/shared/lib/cn";
import type { UsageHistory } from "@/types";

import { UsageTrend } from "@/features/usage/usage-trend";
import { computeUsageStats, healthLabel, healthTextClass, healthLevel, periodOverPeriod } from "@/features/usage/usage-stats";
import { errorMessage } from "@/features/checkin/checkin-format";

/** 站点色相与 index.css 的 --site-0..5 对齐（图表 fill 需要字面量颜色，吃不到 CSS class） */
const PROVIDER_PALETTE = [
  "oklch(0.68 0.14 235)",
  "oklch(0.68 0.16 285)",
  "oklch(0.68 0.16 320)",
  "oklch(0.7 0.12 195)",
  "oklch(0.68 0.15 265)",
  "oklch(0.7 0.17 350)",
];

const money = (v: number | null | undefined) => (v === null || v === undefined ? "--" : `$${v.toFixed(2)}`);

const PERIODS = [
  { value: "7", label: "7 天" },
  { value: "30", label: "30 天" },
  { value: "90", label: "90 天" },
] as const;

export function UsagePage() {
  const [period, setPeriod] = useState<"7" | "30" | "90">("30");
  const historyQ = useQuery({
    queryKey: ["usage", "history"],
    queryFn: () => apiGet<UsageHistory>("/usage/history"),
  });

  const stats = useMemo(() => (historyQ.data ? computeUsageStats(historyQ.data) : null), [historyQ.data]);
  const windowDays = Number(period);

  const visibleDays = useMemo(() => (stats ? stats.days.slice(-windowDays) : []), [stats, windowDays]);
  const pop7 = useMemo(() => (stats ? periodOverPeriod(stats.days, 7) : null), [stats]);
  const pop30 = useMemo(() => (stats ? periodOverPeriod(stats.days, 30) : null), [stats]);

  const pieConfig = useMemo<ChartConfig>(() => {
    const config: ChartConfig = {};
    stats?.providers.forEach((p, i) => {
      config[p.provider] = {
        label: p.provider,
        theme: { light: PROVIDER_PALETTE[i % PROVIDER_PALETTE.length]!, dark: PROVIDER_PALETTE[i % PROVIDER_PALETTE.length]! },
      };
    });
    return config;
  }, [stats]);

  const burn7 = visibleDays.length >= 7 ? visibleDays.slice(-7).reduce((s, d) => s + d.spend, 0) / 7 : null;
  const burn30 = visibleDays.length >= 30 ? visibleDays.slice(-30).reduce((s, d) => s + d.spend, 0) / 30 : null;
  const totalGain = visibleDays.reduce((s, d) => s + d.gain, 0);
  const totalBalance = stats ? stats.days[stats.days.length - 1]?.balance ?? 0 : 0;

  if (historyQ.isError) return <ErrorState message={errorMessage(historyQ.error, "用量历史加载失败")} onRetry={() => void historyQ.refetch()} />;

  return (
    <div className="space-y-5">
      <PageHeader
        title="用量分析"
        description="余额、消耗、签到收益与账号健康度"
        actions={
          <Tabs value={period} onValueChange={(v) => setPeriod(v as "7" | "30" | "90")}>
            <TabsList>
              {PERIODS.map((p) => (
                <TabsTrigger key={p.value} value={p.value}>
                  {p.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        }
      />

      <section className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="总余额" value={money(totalBalance)} sub={stats?.dateRange ? `截至 ${stats.dateRange[1]}` : undefined} icon={Coins} iconClass="text-site-0" delay={0} />
        <KpiCard
          label="近 7 日日均消耗"
          value={money(burn7)}
          sub={pop7 === null ? undefined : `环比 ${pop7 >= 0 ? "+" : ""}${pop7.toFixed(0)}%`}
          icon={Flame}
          iconClass={pop7 !== null && pop7 > 5 ? "text-checkin-failed" : pop7 !== null && pop7 < -5 ? "text-checkin-done" : "text-site-1"}
          delay={60}
        />
        <KpiCard
          label="近 30 日日均消耗"
          value={money(burn30)}
          sub={pop30 === null ? undefined : `环比 ${pop30 >= 0 ? "+" : ""}${pop30.toFixed(0)}%`}
          icon={Activity}
          iconClass={pop30 !== null && pop30 > 5 ? "text-checkin-failed" : pop30 !== null && pop30 < -5 ? "text-checkin-done" : "text-site-2"}
          delay={120}
        />
        <KpiCard label={`签到收益（${period} 天）`} value={money(totalGain)} sub="按 quota 日增量统计" icon={TrendingUp} iconClass="text-checkin-done" delay={180} />
      </section>

      <div className="grid items-stretch gap-2 xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
        <UsageTrend days={visibleDays} loading={historyQ.isLoading} />
        <section className="rounded-lg bg-card p-4 sm:p-5">
          <h2 className="text-sm font-medium">站点余额占比</h2>
          <div className="mt-4">
            {stats && stats.providers.length > 0 ? (
              <ChartContainer config={pieConfig} className="mx-auto aspect-square max-h-[220px]">
                <PieChart>
                  <ChartTooltip cursor={false} content={<ChartTooltipContent className="w-40" nameKey="name" />} />
                  <Pie data={stats.providers} dataKey="balance" nameKey="provider" innerRadius={54} outerRadius={82} paddingAngle={2} strokeWidth={2}>
                    {stats.providers.map((p) => (
                      <Cell key={p.provider} fill={`var(--color-${p.provider})`} />
                    ))}
                  </Pie>
                </PieChart>
              </ChartContainer>
            ) : (
              <EmptyState message="暂无站点数据" />
            )}
            <div className="mt-3 space-y-1.5">
              {stats?.providers.map((p) => (
                <div key={p.provider} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5">
                    <span className={siteDotClass(p.provider)} aria-hidden="true" />
                    {p.provider}
                  </span>
                  <span className="font-data tabular-nums">{money(p.balance)}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      <div className="grid items-start gap-2 xl:grid-cols-2">
        <section className="rounded-lg bg-card p-4 sm:p-5">
          <h2 className="text-sm font-medium">余额耗尽预测</h2>
          <p className="mt-1 text-xs text-muted-foreground">按近 7 日日均消耗推算；余额低于 $10 或已耗尽的账号需要处理</p>
          <div className="mt-3">
            {stats && stats.accounts.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>账号</TableHead>
                    <TableHead className="text-right">余额</TableHead>
                    <TableHead className="text-right">日均</TableHead>
                    <TableHead className="text-right">预计剩余</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.accounts.slice(0, 8).map((a) => (
                    <TableRow key={a.key}>
                      <TableCell>
                        <span className="flex items-center gap-1.5 text-xs">
                          <span className={siteDotClass(a.provider)} aria-hidden="true" />
                          {a.name}
                        </span>
                      </TableCell>
                      <TableCell className={cn("text-right font-data text-xs", a.balance < 10 && "text-balance-low")}>{money(a.balance)}</TableCell>
                      <TableCell className="text-right font-data text-xs">{money(a.burnRate7)}</TableCell>
                      <TableCell className={cn("text-right font-data text-xs", a.daysLeft !== null && a.daysLeft < 7 && "text-balance-low")}>
                        {a.daysLeft === null ? "∞" : `${Math.max(0, Math.floor(a.daysLeft))} 天`}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : historyQ.isLoading ? (
              <LoadingState className="min-h-32" />
            ) : (
              <EmptyState message="暂无数据" />
            )}
          </div>
        </section>

        <section className="rounded-lg bg-card p-4 sm:p-5">
          <h2 className="text-sm font-medium">Top 消耗账号</h2>
          <p className="mt-1 text-xs text-muted-foreground">窗口期（{period} 天）内累计消耗排行</p>
          <div className="mt-3 space-y-2">
            {stats?.accounts.slice(0, 6).map((a, i) => {
              const max = stats.accounts[0]?.spend || 1;
              return (
                <div key={a.key} className="animate-in fade-in slide-in-from-left-1 fill-mode-both duration-300" style={{ animationDelay: `${i * 50}ms` }}>
                  <div className="flex items-center justify-between text-xs">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className={siteDotClass(a.provider)} aria-hidden="true" />
                      <span className="truncate">{a.name}</span>
                    </span>
                    <span className="font-data shrink-0 tabular-nums">{money(a.spend)}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-foreground/70 transition-all duration-500"
                      style={{ width: `${Math.max(4, (a.spend / max) * 100)}%` }}
                    />
                  </div>
                </div>
              );
            })}
            {stats && stats.accounts.length === 0 && !historyQ.isLoading ? <EmptyState message="暂无数据" /> : null}
          </div>
        </section>
      </div>

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <h2 className="text-sm font-medium">签到热力图</h2>
        <p className="mt-1 text-xs text-muted-foreground">根据额度变化推算（后端未持久化签到历史，quota 较前一日增加即视为当日签到成功）</p>
        <div className="mt-3 overflow-x-auto">
          {stats && stats.accounts.length > 0 ? (
            <table className="border-separate border-spacing-[2px]">
              <tbody>
                {stats.accounts.map((a) => (
                  <tr key={a.key}>
                    <td className="w-40 truncate pr-2 text-right text-[11px] text-muted-foreground">
                      <span className="flex items-center justify-end gap-1.5">
                        <span className={siteDotClass(a.provider)} aria-hidden="true" />
                        {a.name}
                      </span>
                    </td>
                    {visibleDays.map((d) => (
                      <td key={d.date}>
                        <span
                          title={`${a.name} · ${d.date}：${a.signedDays.has(d.date) ? "已签到" : "未签到 / 无数据"}`}
                          className={cn("block size-3 rounded-[3px]", a.signedDays.has(d.date) ? "bg-checkin-done/80" : "bg-muted")}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : historyQ.isLoading ? (
            <LoadingState className="min-h-32" />
          ) : (
            <EmptyState message="暂无数据" />
          )}
        </div>
      </section>

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <h2 className="text-sm font-medium">账号健康度</h2>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {stats?.accounts.map((a, i) => {
            const level = healthLevel(a);
            return (
              <span
                key={a.key}
                title={`${a.name} · 余额 ${money(a.balance)} · 日均 ${money(a.burnRate7)}`}
                className={cn(
                  "inline-flex animate-in items-center gap-1 rounded-full bg-secondary/70 px-2 py-0.5 text-[11px] fade-in zoom-in-95 duration-300 fill-mode-both",
                  healthTextClass[level],
                )}
                style={{ animationDelay: `${i * 35}ms` }}
              >
                <span className={siteDotClass(a.provider)} aria-hidden="true" />
                {a.name} · {healthLabel[level]}
              </span>
            );
          })}
          {stats && stats.accounts.length === 0 && !historyQ.isLoading ? <EmptyState message="暂无数据" /> : null}
        </div>
      </section>
    </div>
  );
}
