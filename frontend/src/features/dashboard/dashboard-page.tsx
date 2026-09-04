import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Cell, Pie, PieChart } from "recharts";
import { Link } from "react-router-dom";
import { CalendarCheck, Coins, Flame, Wallet } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { ErrorState, LoadingState } from "@/shared/components/data-state";
import { KpiCard } from "@/shared/components/kpi-card";
import { KpiSkeleton } from "@/shared/components/skeleton";
import { PageHeader } from "@/shared/components/page-header";
import { siteDotClass } from "@/shared/lib/site-color";
import { cn } from "@/shared/lib/cn";
import { apiGet } from "@/shared/api/client";
import type { CheckinStatusResponse, UsageHistory } from "@/types";

import { UsageTrend } from "@/features/usage/usage-trend";
import { computeUsageStats, healthLevel, healthLabel, healthTextClass } from "@/features/usage/usage-stats";
import { getAnyrouterCheckinStatus } from "@/features/checkin/checkin-api";
import { StatusChip } from "@/features/checkin/checkin-card";
import { isCheckedStatus, errorMessage } from "@/features/checkin/checkin-format";

const PROVIDER_PALETTE = [
  "oklch(0.68 0.14 235)",
  "oklch(0.68 0.16 285)",
  "oklch(0.68 0.16 320)",
  "oklch(0.7 0.12 195)",
  "oklch(0.68 0.15 265)",
  "oklch(0.7 0.17 350)",
];

const money = (v: number | null | undefined) => (v === null || v === undefined ? "--" : `$${v.toFixed(2)}`);

export function DashboardPage() {
  const historyQ = useQuery({ queryKey: ["usage", "history"], queryFn: () => apiGet<UsageHistory>("/usage/history") });
  const loginStatusQ = useQuery({ queryKey: ["checkin", "login-status"], queryFn: () => apiGet<CheckinStatusResponse>("/login-accounts/checkin/status"), retry: false });
  const cookieStatusQ = useQuery({ queryKey: ["checkin", "cookie-status-machine"], queryFn: getAnyrouterCheckinStatus, retry: false });

  const stats = useMemo(() => (historyQ.data ? computeUsageStats(historyQ.data) : null), [historyQ.data]);

  const chipAccounts = useMemo(() => {
    const out: { provider: string; name: string; status: Parameters<typeof isCheckedStatus>[0]; message: string }[] = [];
    for (const [provider, resp] of [["provider", cookieStatusQ.data], ["agentrouter", loginStatusQ.data]] as const) {
      for (const a of resp?.status.accounts ?? []) out.push({ provider, name: a.name, status: a.status, message: a.message });
    }
    return out;
  }, [cookieStatusQ.data, loginStatusQ.data]);

  const signedCount = chipAccounts.filter((a) => isCheckedStatus(a.status)).length;
  const today = stats?.days[stats.days.length - 1];
  const totalBalance = today?.balance ?? 0;
  const todaySpend = today?.spend ?? 0;

  const pieConfig = useMemo<ChartConfig>(() => {
    const config: ChartConfig = {};
    stats?.providers.forEach((p, i) => {
      const color = PROVIDER_PALETTE[i % PROVIDER_PALETTE.length]!;
      config[p.provider] = { label: p.provider, theme: { light: color, dark: color } };
    });
    return config;
  }, [stats]);

  const attention = useMemo(() => (stats ? stats.accounts.filter((a) => healthLevel(a) !== "ok" && healthLevel(a) !== "idle") : []), [stats]);

  if (historyQ.isError) return <ErrorState message={errorMessage(historyQ.error, "用量数据加载失败")} onRetry={() => void historyQ.refetch()} />;

  return (
    <div className="space-y-5">
      <PageHeader title="总览" description="跨站点的余额、消耗与签到状态聚合" />

      <section className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {historyQ.isLoading ? (
          <>
            <KpiSkeleton /><KpiSkeleton /><KpiSkeleton /><KpiSkeleton />
          </>
        ) : (
          <>
        <KpiCard label="总余额" value={money(totalBalance)} sub="全部站点合计" icon={Coins} iconClass="text-site-0" href="/accounts" delay={0} />
        <KpiCard label="今日消耗" value={money(todaySpend)} sub="相对当日首次快照" icon={Flame} iconClass="text-site-1" href="/usage" delay={60} />
        <KpiCard label="今日签到" value={chipAccounts.length > 0 ? `${signedCount} / ${chipAccounts.length}` : "--"} sub="已签到 / 总数" icon={CalendarCheck} iconClass="text-checkin-done" href="/checkin" delay={120} />
        <KpiCard label="账号 / 站点" value={`${stats?.accounts.length ?? 0} / ${stats?.providers.length ?? 0}`} sub="覆盖的站点与账号" icon={Wallet} iconClass="text-site-3" href="/sites" delay={180} />
          </>
        )}
      </section>

      <div className="grid items-stretch gap-2 xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
        <UsageTrend days={stats ? stats.days.slice(-30) : []} loading={historyQ.isLoading} title="近 30 日趋势" />
        <section className="rounded-lg bg-card p-4 sm:p-5">
          <h2 className="text-sm font-medium">站点余额分布</h2>
          <div className="mt-4">
            {stats && stats.providers.length > 0 ? (
              <>
                <ChartContainer config={pieConfig} className="mx-auto aspect-square max-h-[200px]">
                  <PieChart>
                    <ChartTooltip cursor={false} content={<ChartTooltipContent className="w-40" nameKey="name" />} />
                    <Pie data={stats.providers} dataKey="balance" nameKey="provider" innerRadius={50} outerRadius={76} paddingAngle={2} strokeWidth={2}>
                      {stats.providers.map((p) => (
                        <Cell key={p.provider} fill={`var(--color-${p.provider})`} />
                      ))}
                    </Pie>
                  </PieChart>
                </ChartContainer>
                <div className="mt-3 space-y-1.5">
                  {stats.providers.map((p) => (
                    <div key={p.provider} className="flex items-center justify-between text-xs">
                      <span className="flex items-center gap-1.5">
                        <span className={siteDotClass(p.provider)} aria-hidden="true" />
                        {p.provider}
                      </span>
                      <span className="font-data tabular-nums">{money(p.balance)}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : historyQ.isLoading ? (
              <LoadingState className="min-h-32" />
            ) : null}
          </div>
        </section>
      </div>

      <div className="grid items-start gap-2 xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
        <section className="rounded-lg bg-card p-4 sm:p-5">
          <div className="flex min-h-8 items-center justify-between gap-3">
            <h2 className="text-sm font-medium">签到状态速览</h2>
            <Button variant="secondary" size="sm" asChild>
              <Link to="/checkin">签到中心</Link>
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {chipAccounts.length > 0 ? (
              chipAccounts.map((a, i) => (
                <span key={`${a.provider}-${a.name}-${a.status}`} className="animate-in fade-in zoom-in-95 duration-300 fill-mode-both" style={{ animationDelay: `${i * 35}ms` }}>
                  <StatusChip name={a.name} status={a.status} message={a.message} />
                </span>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">暂无签到记录——去签到中心跑一次就有了</p>
            )}
          </div>
        </section>

        <section className="rounded-lg bg-card p-4 sm:p-5">
          <div className="flex min-h-8 items-center justify-between gap-3">
            <h2 className="text-sm font-medium">余额告警</h2>
            <Button variant="secondary" size="sm" asChild>
              <Link to="/usage">用量分析</Link>
            </Button>
          </div>
          <div className="mt-3 flex flex-col gap-1.5">
            {attention.length > 0 ? (
              attention.map((a) => {
                const level = healthLevel(a);
                return (
                  <div key={a.key} className="flex items-center justify-between text-xs">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className={siteDotClass(a.provider)} aria-hidden="true" />
                      <span className="truncate">{a.name}</span>
                    </span>
                    <span className={cn("shrink-0 font-data tabular-nums", healthTextClass[level])}>
                      {money(a.balance)} · {healthLabel[level]}
                    </span>
                  </div>
                );
              })
            ) : (
              <p className="text-xs text-checkin-done">全部账号余额健康 ✓</p>
            )}
          </div>
        </section>
      </div>

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <div className="flex min-h-8 items-center justify-between gap-3">
          <h2 className="text-sm font-medium">账号余额明细</h2>
          <Button variant="secondary" size="sm" asChild>
            <Link to="/accounts">账号管理</Link>
          </Button>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">来自每日快照的最新数据，无需手动查询</p>
        <div className="mt-3">
          {stats && stats.accounts.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>站点</TableHead>
                  <TableHead>账号</TableHead>
                  <TableHead className="text-right">余额</TableHead>
                  <TableHead className="text-right">今日用量</TableHead>
                  <TableHead className="text-right">日均消耗</TableHead>
                  <TableHead className="text-right">预计剩余</TableHead>
                  <TableHead>状态</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stats.accounts.map((a, i) => {
                  const level = healthLevel(a);
                  const lastDay = stats.days[stats.days.length - 1];
                  const dayEntry = lastDay ? historyQ.data?.history[lastDay.date]?.[a.key] : undefined;
                  const todayUsed = dayEntry ? dayEntry.used - dayEntry.used0 : null;
                  return (
                    <TableRow key={a.key} className="animate-in fade-in slide-in-from-bottom-1 fill-mode-both duration-300" style={{ animationDelay: `${Math.min(i, 10) * 30}ms` }}>
                      <TableCell>
                        <span className="flex items-center gap-1.5 text-xs">
                          <span className={siteDotClass(a.provider)} aria-hidden="true" />
                          {a.provider}
                        </span>
                      </TableCell>
                      <TableCell className="text-xs font-medium">{a.name}</TableCell>
                      <TableCell className={cn("text-right font-data text-xs tabular-nums", a.balance < 10 && "text-balance-low")}>{money(a.balance)}</TableCell>
                      <TableCell className="text-right font-data text-xs tabular-nums">{todayUsed === null ? "--" : money(Math.max(0, todayUsed))}</TableCell>
                      <TableCell className="text-right font-data text-xs tabular-nums">{money(a.burnRate7)}</TableCell>
                      <TableCell className="text-right font-data text-xs tabular-nums">{a.daysLeft === null ? "∞" : `${Math.max(0, Math.floor(a.daysLeft))} 天`}</TableCell>
                      <TableCell>
                        <span className={cn("text-xs", healthTextClass[level])}>{healthLabel[level]}</span>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : historyQ.isLoading ? (
            <LoadingState className="min-h-24" />
          ) : (
            <p className="text-xs text-muted-foreground">暂无用量数据——先跑一次「查询余额」生成快照</p>
          )}
        </div>
      </section>

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <div className="flex min-h-8 items-center justify-between gap-3">
          <h2 className="text-sm font-medium">最近签到日志</h2>
          <Button variant="secondary" size="sm" asChild>
            <Link to="/checkin">签到中心</Link>
          </Button>
        </div>
        <div className="mt-3 grid gap-2 xl:grid-cols-2">
          {[
            { label: "AgentRouter", logs: loginStatusQ.data?.status.logs ?? [], tone: "text-site-1" },
            { label: "Cookie 账号", logs: cookieStatusQ.data?.status.logs ?? [], tone: "text-site-0" },
          ].map((src) => (
            <div key={src.label} className="max-h-44 space-y-0.5 overflow-y-auto rounded-md bg-muted/40 p-2">
              <p className={cn("sticky top-0 bg-muted/80 px-1 py-0.5 text-[11px] font-medium", src.tone)}>{src.label}</p>
              {src.logs.length > 0 ? (
                src.logs.slice(-8).reverse().map((l, i) => (
                  <p key={`${src.label}-${l.time}-${i}`} className="animate-in fade-in px-1 font-data text-[11px] text-muted-foreground duration-200" style={{ animationDelay: `${i * 30}ms` }}>
                    <span className="mr-1.5 opacity-60">{l.time}</span>
                    {l.message}
                  </p>
                ))
              ) : (
                <p className="px-1 text-[11px] text-muted-foreground">暂无日志</p>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
