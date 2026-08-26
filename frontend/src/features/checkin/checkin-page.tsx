import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Loader2, Play, RefreshCw, Square, Terminal } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorState, LoadingState } from "@/shared/components/data-state";
import { PageHeader } from "@/shared/components/page-header";
import { siteDotClass } from "@/shared/lib/site-color";
import type { CheckinAccountStatus, CheckinResult, NewapiSite, SiteAccount, SiteSyncResult } from "@/types";

import { buildSiteScript, filterPendingAccounts } from "@/features/checkin/build-site-script";
import {
  checkinKeys,
  checkinTokenAccounts,
  getCheckinSettings,
  getAnyrouterCheckinStatus,
  getLoginCheckinStatus,
  getSiteCheckinStatus,
  getSiteTurnstile,
  listLoginAccounts,
  listSiteAccounts,
  listSites,
  listTokenAccounts,
  renewCookieAccounts,
  startAnyrouterCheckin,
  startLoginCheckinFast,
  startLoginCheckinSlow,
  startSiteCheckin,
  stopLoginCheckin,
  syncSiteCheckin,
  updateCheckinSettings,
} from "@/features/checkin/checkin-api";
import { AutoCheckinRow, CheckinCard, StatusChip } from "@/features/checkin/checkin-card";
import { errorMessage, finishedSummary, hasRunHistory, isCheckedStatus, runningSummary } from "@/features/checkin/checkin-format";

type Filter = "all" | "signed" | "unsigned";
type Chip = { name: string; status: CheckinAccountStatus["status"]; message: string };

function visibleChips(items: Chip[], filter: Filter): Chip[] {
  if (filter === "signed") return items.filter((i) => isCheckedStatus(i.status));
  if (filter === "unsigned") return items.filter((i) => !isCheckedStatus(i.status));
  return items;
}

export function CheckinPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [tokenResults, setTokenResults] = useState<CheckinResult[] | null>(null);
  const [scriptDialog, setScriptDialog] = useState<{ site: NewapiSite; script: string; pending: number; total: number } | null>(null);
  const [renewNotice, setRenewNotice] = useState<string | null>(null);

  const sitesQ = useQuery({ queryKey: checkinKeys.sites, queryFn: listSites });
  const settingsQ = useQuery({ queryKey: checkinKeys.settings, queryFn: getCheckinSettings });
  const cookieStatusQ = useQuery({
    queryKey: ["checkin", "cookie-status"],
    queryFn: getAnyrouterCheckinStatus,
    refetchInterval: (query) => (query.state.data?.status.running ? 3000 : false),
  });
  const loginStatusQ = useQuery({
    queryKey: checkinKeys.loginStatus,
    queryFn: getLoginCheckinStatus,
    refetchInterval: (query) => (query.state.data?.status.running ? 5000 : false),
  });
  const tokenAccountsQ = useQuery({ queryKey: checkinKeys.tokenAccounts, queryFn: listTokenAccounts });
  const loginAccountsQ = useQuery({ queryKey: checkinKeys.loginAccounts, queryFn: listLoginAccounts });

  const sites = sitesQ.data?.sites ?? [];
  const siteAccountsQ = useQuery({
    queryKey: ["checkin", "site-accounts-map", sites.map((s) => s.id)] as const,
    queryFn: async () => {
      const entries = await Promise.all(sites.map(async (s) => [s.id, (await listSiteAccounts(s.id)).accounts] as const));
      return Object.fromEntries(entries) as Record<string, SiteAccount[]>;
    },
    enabled: sitesQ.isSuccess,
  });
  const siteStatusesQ = useQuery({
    queryKey: ["checkin", "site-statuses", sites.map((s) => s.id)] as const,
    queryFn: async () => {
      const entries = await Promise.all(sites.map(async (s) => [s.id, (await getSiteCheckinStatus(s.id)).status] as const));
      return Object.fromEntries(entries);
    },
    enabled: sitesQ.isSuccess && sites.length > 0,
    refetchInterval: (query) => (sites.some((s) => (query.state.data as Record<string, { running: boolean }> | undefined)?.[s.id]?.running) ? 5000 : false),
  });

  const settings = settingsQ.data?.settings;
  const tokenAccounts = tokenAccountsQ.data?.accounts ?? [];
  const loginAccounts = loginAccountsQ.data?.accounts ?? [];
  const cookieState = cookieStatusQ.data?.status;
  const loginState = loginStatusQ.data?.status;

  if (sitesQ.isError) return <ErrorState message={errorMessage(sitesQ.error, "站点加载失败")} onRetry={() => void sitesQ.refetch()} />;

  async function toggleSetting(patch: Parameters<typeof updateCheckinSettings>[0], successMsg: string) {
    try {
      await updateCheckinSettings(patch);
      await queryClient.invalidateQueries({ queryKey: checkinKeys.settings });
      toast.success(successMsg);
    } catch {
      toast.error("保存失败，界面已回滚");
    }
  }

  async function guard(name: string, fn: () => Promise<unknown>, doneMsg?: (r: unknown) => string) {
    if (busy) return;
    setBusy(name);
    try {
      const r = await fn();
      if (doneMsg) toast.success(doneMsg(r));
      await queryClient.invalidateQueries({ queryKey: ["checkin"] });
    } catch (err) {
      toast.error(errorMessage(err, "操作失败"));
    } finally {
      setBusy(null);
    }
  }

  async function onTokenCheckin() {
    if (busy) return;
    setBusy("token");
    try {
      const res = await checkinTokenAccounts();
      setTokenResults(res.results);
      toast.success(`签到完成：新签 ${res.summary.new_signed} · 成功 ${res.summary.success} / ${res.summary.total}`);
    } catch (err) {
      toast.error(errorMessage(err, "签到失败"));
    } finally {
      setBusy(null);
    }
  }

  async function onSiteCheckin(site: NewapiSite) {
    if (busy) return;
    setBusy(`site:${site.id}`);
    try {
      const ts = (await getSiteTurnstile(site.id)).turnstile;
      const accs = siteAccountsQ.data?.[site.id] ?? [];
      if (accs.length === 0) {
        toast.info(`没有 ${site.label} 账号`);
        return;
      }
      if (!ts.enabled) {
        if (window.confirm(`在 ${site.label} 执行服务器端签到？`)) {
          await startSiteCheckin(site.id);
          toast.success(`${site.label} 签到已启动`);
        }
        return;
      }
      // Turnstile 站点：生成浏览器脚本。先跑 sync 剔除今日已签——token 一次性，替已签的取 token 是纯浪费
      let pending = accs;
      try {
        const sync = await syncSiteCheckin(site.id);
        const results: SiteSyncResult[] = sync.results;
        pending = filterPendingAccounts(accs, results);
      } catch {
        /* 同步失败就全量生成，别拦着用户签到 */
      }
      if (pending.length === 0) {
        toast.success(`${accs.length} 个账号今日都已签到，不用跑脚本 ✓`);
        await queryClient.invalidateQueries({ queryKey: ["checkin"] });
        return;
      }
      setScriptDialog({ site, script: buildSiteScript(site, pending, ts), pending: pending.length, total: accs.length });
    } catch (err) {
      toast.error(errorMessage(err, "签到失败"));
    } finally {
      setBusy(null);
      await queryClient.invalidateQueries({ queryKey: ["checkin"] });
    }
  }

  function progressBar(state: { running: boolean; total: number; done: number } | undefined) {
    if (!state?.running) return null;
    return (
      <div className="space-y-1.5">
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-checkin-pending transition-all duration-500" style={{ width: `${state.total > 0 ? (state.done / state.total) * 100 : 0}%` }} />
        </div>
        <p className="text-xs text-muted-foreground">{state.done} / {state.total}</p>
      </div>
    );
  }

  function logBlock(entries: { time: string; message: string }[] | undefined) {
    if (!entries || entries.length === 0) return null;
    return (
      <div className="max-h-28 space-y-0.5 overflow-y-auto rounded-md bg-muted/40 p-2">
        {entries.slice(-8).reverse().map((l, i) => (
          <p key={`${l.time}-${i}`} className="animate-in fade-in font-data text-[11px] text-muted-foreground duration-200" style={{ animationDelay: `${i * 30}ms` }}>
            <span className="mr-1.5 opacity-60">{l.time}</span>
            {l.message}
          </p>
        ))}
      </div>
    );
  }

  function chipList(accounts: Chip[]) {
    const shown = visibleChips(accounts, filter);
    if (shown.length === 0) return <EmptyState message={filter === "all" ? "还没有账号" : "没有匹配的账号"} />;
    return (
      <div className="flex flex-wrap gap-1.5">
        {shown.map((a, i) => (
          <span key={`${a.name}-${a.status}`} className="animate-in fade-in zoom-in-95 duration-300 fill-mode-both" style={{ animationDelay: `${i * 35}ms` }}>
            <StatusChip name={a.name} status={a.status} message={a.message} />
          </span>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="签到中心"
        description="全站点的签到状态与操作"
        actions={
          <Select value={filter} onValueChange={(v) => setFilter(v as Filter)}>
            <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="signed">已签到</SelectItem>
              <SelectItem value="unsigned">未签到</SelectItem>
            </SelectContent>
          </Select>
        }
      />

      {renewNotice ? <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-600 dark:text-amber-400">{renewNotice}</p> : null}
      {busy ? <LoadingState className="min-h-16" /> : null}

      <div className="space-y-2">
        <CheckinCard
          dotClassName={siteDotClass("cookie")}
          title="Handle · Cookie 账号"
          subtitle={hasRunHistory(cookieState) ? finishedSummary(cookieState) : undefined}
          actions={
            <>
              <Button size="sm" disabled={!!busy} onClick={() => void guard("cookie", startAnyrouterCheckin, () => "Cookie 签到已启动")}>
                {busy === "cookie" ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />}
                签到
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!!busy}
                onClick={() =>
                  void guard("renew", () => renewCookieAccounts(), (r) => {
                    const rr = r as { summary: { renewed: number; skipped: number; failed: number }; notice?: string };
                    setRenewNotice(rr.notice ?? null);
                    return `续期完成：成功 ${rr.summary.renewed} · 跳过 ${rr.summary.skipped} · 失败 ${rr.summary.failed}`;
                  })
                }
              >
                <RefreshCw className="size-3.5" aria-hidden="true" />
                Cookie 续期
              </Button>
            </>
          }
        >
          {settings ? (
            <AutoCheckinRow
              checked={Boolean(settings.checkin_auto_enabled)}
              onCheckedChange={(next) => void toggleSetting({ checkin_auto_enabled: next }, next ? "已开启自动签到" : "已关闭自动签到")}
              hint="每天 0 点并发签到全部 cookie 账号"
            />
          ) : null}
          {progressBar(cookieState)}
          {chipList((cookieState?.accounts ?? []).map((a) => ({ name: a.name, status: a.status, message: a.message })))}
          {logBlock(cookieState?.logs)}
        </CheckinCard>

        <CheckinCard
          dotClassName={siteDotClass("token")}
          title="Handle · Token 账号"
          subtitle={`${tokenAccounts.length} 个账号 · 不在每日自动签到范围内，需手动签`}
          actions={
            <Button size="sm" disabled={!!busy} onClick={() => void onTokenCheckin()}>
              {busy === "token" ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />}
              签到
            </Button>
          }
        >
          {tokenResults ? (
            <div className="flex flex-wrap gap-1.5">
              {tokenResults.map((r, i) => (
                <span key={`${r.name}-token-${i}`} className="animate-in fade-in zoom-in-95 duration-300 fill-mode-both" style={{ animationDelay: `${i * 35}ms` }}>
                  <StatusChip name={r.name} status={r.success ? (r.already_signed ? "already" : "signed") : "failed"} message={r.message} />
                </span>
              ))}
            </div>
          ) : (
            <EmptyState message="点「签到」执行一次；结果只在本次会话内展示" />
          )}
        </CheckinCard>

        <CheckinCard
          dotClassName={siteDotClass("agentrouter")}
          title="AgentRouter · 账号密码"
          subtitle={hasRunHistory(loginState) ? (loginState.running ? runningSummary(loginState) : finishedSummary(loginState)) : `${loginAccounts.length} 个账号`}
          actions={
            <>
              <Button size="sm" disabled={!!busy || loginState?.running} onClick={() => void guard("fast", startLoginCheckinFast, () => "一键全签已启动（约 1~2 分钟）")}>
                {busy === "fast" ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />}
                一键全签
              </Button>
              <Button variant="secondary" size="sm" disabled={!!busy || loginState?.running} onClick={() => void guard("slow", startLoginCheckinSlow, () => "缓慢签到已启动，可关闭页面")}>
                缓慢签到
              </Button>
              {loginState?.running ? (
                <Button variant="secondary" size="sm" onClick={() => void guard("stop", stopLoginCheckin, () => "已停止")}>
                  <Square className="size-3.5" aria-hidden="true" />
                  停止
                </Button>
              ) : null}
            </>
          }
        >
          {loginState?.running ? <p className="rounded-md bg-amber-500/10 px-2 py-1 text-xs text-amber-600 dark:text-amber-400">签到进行中，一键全签与缓慢模式互斥</p> : null}
          {settings ? (
            <>
              <AutoCheckinRow
                checked={Boolean(settings.login_accounts_auto_enabled)}
                onCheckedChange={(next) => void toggleSetting({ login_accounts_auto_enabled: next }, next ? "已开启自动签到" : "已关闭自动签到")}
                hint="每天 0 点按缓慢模式自动签到"
              />
              <div className="flex flex-wrap items-center gap-2 rounded-md border p-3">
                <span className="text-xs text-muted-foreground">缓慢模式账号间隔（分钟，1~1440）</span>
                <Input
                  type="number"
                  min={1}
                  max={1440}
                  defaultValue={String(settings.login_accounts_gap_min)}
                  className="h-7 w-20 text-xs"
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (!Number.isFinite(v) || v < 1 || v > 1440) {
                      toast.error("间隔必须在 1~1440 分钟之间");
                      e.target.value = String(settings.login_accounts_gap_min);
                      return;
                    }
                    void toggleSetting({ login_accounts_gap_min: v }, `间隔下限已保存为 ${v} 分钟`);
                  }}
                />
                <span className="text-xs text-muted-foreground">~</span>
                <Input
                  type="number"
                  min={1}
                  max={1440}
                  defaultValue={String(settings.login_accounts_gap_max)}
                  className="h-7 w-20 text-xs"
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (!Number.isFinite(v) || v < 1 || v > 1440) {
                      toast.error("间隔必须在 1~1440 分钟之间");
                      e.target.value = String(settings.login_accounts_gap_max);
                      return;
                    }
                    void toggleSetting({ login_accounts_gap_max: v }, `间隔上限已保存为 ${v} 分钟`);
                  }}
                />
              </div>
            </>
          ) : null}
          {progressBar(loginState)}
          {chipList((loginState?.accounts ?? []).map((a) => ({ name: a.name, status: a.status, message: a.message })))}
          {logBlock(loginState?.logs)}
        </CheckinCard>

        {sites.map((site) => {
          const state = siteStatusesQ.data?.[site.id];
          const count = siteAccountsQ.data?.[site.id]?.length ?? 0;
          return (
            <CheckinCard
              key={site.id}
              dotClassName={siteDotClass(site.id)}
              title={site.label}
              subtitle={hasRunHistory(state) ? (state.running ? runningSummary(state) : finishedSummary(state)) : `${count} 个账号`}
              actions={
                <Button size="sm" disabled={!!busy} onClick={() => void onSiteCheckin(site)}>
                  {busy === `site:${site.id}` ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />}
                  签到
                </Button>
              }
            >
              {settings ? (
                <AutoCheckinRow
                  checked={Boolean(settings[`${site.id}_auto`])}
                  onCheckedChange={(next) => void toggleSetting({ [`${site.id}_auto`]: next }, next ? `已开启 ${site.label} 自动签到` : `已关闭 ${site.label} 自动签到`)}
                  hint="每天 0 点服务器端签到；开启 Turnstile 的站点需手动浏览器签到"
                />
              ) : null}
              {progressBar(state)}
              {state?.accounts ? chipList(state.accounts.map((a) => ({ name: a.name, status: a.status, message: a.message }))) : <EmptyState message="尚未签到过" />}
              {logBlock(state?.logs)}
            </CheckinCard>
          );
        })}
      </div>

      {scriptDialog ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-xl animate-in fade-in zoom-in-95 space-y-3 rounded-lg border bg-card p-5 duration-300">
            <div className="flex items-center gap-2">
              <Terminal className="size-4" aria-hidden="true" />
              <h2 className="text-sm font-medium">{scriptDialog.site.label} · 浏览器签到脚本</h2>
              <Badge variant="secondary" className="text-[11px]">Turnstile</Badge>
            </div>
            <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
              <li>在浏览器新标签页打开 <span className="font-data text-foreground">{scriptDialog.site.domain}</span> 并登录任意一个账号</li>
              <li>按 F12 打开控制台（Console），粘贴下面的脚本并回车</li>
              <li>右下角出现 Turnstile 滑块，等它自动完成；{scriptDialog.pending} 个账号会逐个签到</li>
              <li>跑完回这里点「同步状态」核对结果</li>
            </ol>
            <p className="text-[11px] text-muted-foreground">
              脚本不带 cookie（credentials:omit），用的是各账号自己的 access_token；今日已签的 {scriptDialog.total - scriptDialog.pending} 个账号已剔除
            </p>
            <textarea
              readOnly
              value={scriptDialog.script}
              rows={10}
              className="font-data w-full rounded-md border bg-muted/40 p-2 text-[11px]"
              onFocus={(e) => e.currentTarget.select()}
            />
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => void guard("sync", () => syncSiteCheckin(scriptDialog.site.id), () => "同步完成")}>
                <RefreshCw className="size-3.5" aria-hidden="true" />
                同步状态
              </Button>
              <Button
                onClick={() => {
                  void navigator.clipboard?.writeText(scriptDialog.script).then(
                    () => toast.success("脚本已复制，去站点控制台粘贴执行"),
                    () => toast.error("复制失败，请手动全选复制（点击文本框会自动全选）"),
                  );
                }}
              >
                <Copy className="size-3.5" aria-hidden="true" />
                复制脚本
              </Button>
              <Button variant="ghost" onClick={() => setScriptDialog(null)}>关闭</Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
