import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, Loader2, Radio, RefreshCw, Send } from "lucide-react";
import { toast } from "sonner";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState, LoadingState } from "@/shared/components/data-state";
import { PageHeader } from "@/shared/components/page-header";
import { apiGet, apiPost } from "@/shared/api/client";
import type { CookieAccount, MonitorStatus, SavedConfig } from "@/types";

import { errorMessage } from "@/features/checkin/checkin-format";

/** GET /api/monitor/status 是全项目唯一不带 {success:...} 信封的端点——apiGet 原样透传整个 payload */
interface ProxyInfo {
  proxy: { url: string; source: string; has_credentials: boolean; reachable: boolean | null };
  mihomo: { group: string; rotation_enabled: boolean; config_path: string; config_exists: boolean };
}

const emailSchema = z.object({
  smtp_server: z.string().min(1, "SMTP 服务器必填"),
  smtp_port: z.coerce.number().int().min(1).max(65535),
  email_user: z.string().min(1, "发件邮箱必填"),
  email_pass: z.string().min(1, "密码 / 授权码必填"),
  email_to: z.string().email("收件邮箱格式不对"),
});

export function SettingsPage() {
  const queryClient = useQueryClient();

  const configQ = useQuery({ queryKey: ["accounts", "cookie"], queryFn: async (): Promise<SavedConfig | null> => (await apiGet<{ data: SavedConfig | null }>("/config")).data });
  const monitorQ = useQuery({
    queryKey: ["monitor", "status"],
    queryFn: () => apiGet<MonitorStatus>("/monitor/status"),
    refetchInterval: (query) => (query.state.data?.running ? 5000 : false),
  });
  const proxyQ = useQuery({ queryKey: ["system", "proxy-info"], queryFn: () => apiGet<ProxyInfo>("/system/proxy-info"), retry: false });
  const [probingProxy, setProbingProxy] = useState(false);

  const [smtpServer, setSmtpServer] = useState("");
  const [smtpPort, setSmtpPort] = useState("465");
  const [emailUser, setEmailUser] = useState("");
  const [emailPass, setEmailPass] = useState("");
  const [emailTo, setEmailTo] = useState("");
  const [interval, setIntervalHours] = useState("6");
  const [threshold, setThreshold] = useState("10");
  const [showPass, setShowPass] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  if (configQ.data && !hydrated) {
    const cfg = configQ.data;
    setSmtpServer(cfg.email?.smtp_server ?? "");
    setSmtpPort(String(cfg.email?.smtp_port ?? 465));
    setEmailUser(cfg.email?.email_user ?? "");
    setEmailTo(cfg.email?.email_to ?? "");
    setIntervalHours(String(cfg.monitor?.interval ?? 6));
    setThreshold(String(cfg.monitor?.threshold ?? 10));
    setHydrated(true);
  }

  const monitor = monitorQ.data;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const parsed = emailSchema.safeParse({ smtp_server: smtpServer, smtp_port: smtpPort, email_user: emailUser, email_pass: emailPass, email_to: emailTo });
    if (!parsed.success) {
      toast.error(parsed.error.issues[0]?.message ?? "表单有误");
      return;
    }
    const accounts: CookieAccount[] = configQ.data?.accounts ?? [];
    if (accounts.length === 0) {
      toast.error("没有 cookie 账号可监控——先去「账号管理」添加");
      return;
    }
    setSubmitting(true);
    try {
      await apiPost("/monitor/start", {
        accounts,
        email: parsed.data,
        interval_hours: Number(interval) || 6,
        threshold: Number(threshold) || 10,
      });
      await queryClient.invalidateQueries({ queryKey: ["monitor"] });
      toast.success("监控已启动");
    } catch (err) {
      toast.error(errorMessage(err, "启动失败"));
    } finally {
      setSubmitting(false);
    }
  }

  async function stopMonitor() {
    try {
      await apiPost("/monitor/stop");
      await queryClient.invalidateQueries({ queryKey: ["monitor"] });
      toast.success("监控已停止");
    } catch (err) {
      toast.error(errorMessage(err, "停止失败"));
    }
  }

  async function probeProxy() {
    setProbingProxy(true);
    try {
      await queryClient.invalidateQueries({ queryKey: ["system", "proxy-info"] });
      await apiGet<ProxyInfo>("/system/proxy-info?probe=true");
      await queryClient.invalidateQueries({ queryKey: ["system", "proxy-info"] });
      toast.success("连通性测试完成");
    } catch (err) {
      toast.error(errorMessage(err, "探测失败"));
    } finally {
      setProbingProxy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="设置" description="监控告警与运行环境" />

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <div className="flex min-h-8 items-center justify-between gap-3">
          <h2 className="text-sm font-medium">余额监控告警</h2>
          <div className="flex items-center gap-2">
            {monitorQ.isLoading ? null : monitor?.running ? (
              <Badge variant="secondary" className="gap-1 text-[11px] text-checkin-done">
                <span className="relative flex size-1.5">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-checkin-done opacity-75" />
                  <span className="relative inline-flex size-1.5 rounded-full bg-checkin-done" />
                </span>
                运行中
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-[11px] text-muted-foreground">未启动</Badge>
            )}
            {monitor?.running ? (
              <Button variant="secondary" size="sm" onClick={() => void stopMonitor()}>停止</Button>
            ) : null}
          </div>
        </div>
        {monitor?.running && monitor.config ? (
          <p className="mt-1 text-xs text-muted-foreground">
            每 {monitor.config.interval_hours} 小时检查一次 · 阈值 ${monitor.config.threshold} · 监控 {monitor.config.account_count} 个账号 · 上次 {monitor.last_check ?? "--"} · 下次 {monitor.next_check ?? "--"}
          </p>
        ) : null}

        <form className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={onSubmit}>
          <div className="space-y-1">
            <Label htmlFor="smtp-server" className="text-xs">SMTP 服务器</Label>
            <Input id="smtp-server" value={smtpServer} onChange={(e) => setSmtpServer(e.target.value)} className="h-8 text-xs" placeholder="smtp.example.com" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="smtp-port" className="text-xs">端口</Label>
            <Input id="smtp-port" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} className="h-8 font-data text-xs" inputMode="numeric" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="email-user" className="text-xs">发件邮箱</Label>
            <Input id="email-user" value={emailUser} onChange={(e) => setEmailUser(e.target.value)} className="h-8 text-xs" placeholder="bot@example.com" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="email-pass" className="text-xs">密码 / 授权码</Label>
            <div className="relative">
              <Input
                id="email-pass"
                type={showPass ? "text" : "password"}
                autoComplete="new-password"
                value={emailPass}
                onChange={(e) => setEmailPass(e.target.value)}
                className="h-8 pr-8 text-xs"
                placeholder="••••••••"
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowPass((v) => !v)}
                aria-label={showPass ? "隐藏密码" : "显示密码"}
              >
                {showPass ? <EyeOff className="size-3.5" aria-hidden="true" /> : <Eye className="size-3.5" aria-hidden="true" />}
              </button>
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="email-to" className="text-xs">收件邮箱</Label>
            <Input id="email-to" value={emailTo} onChange={(e) => setEmailTo(e.target.value)} className="h-8 text-xs" placeholder="you@example.com" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="monitor-interval" className="text-xs">检查间隔（小时）</Label>
            <Input id="monitor-interval" value={interval} onChange={(e) => setIntervalHours(e.target.value)} className="h-8 font-data text-xs" inputMode="numeric" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="monitor-threshold" className="text-xs">告警阈值（$）</Label>
            <Input id="monitor-threshold" value={threshold} onChange={(e) => setThreshold(e.target.value)} className="h-8 font-data text-xs" inputMode="decimal" />
          </div>
          <div className="flex items-end">
            <Button type="submit" className="w-full sm:w-auto" disabled={submitting || monitor?.running}>
              {submitting ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Send className="size-3.5" aria-hidden="true" />}
              启动监控
            </Button>
          </div>
        </form>

        {monitor?.logs && monitor.logs.length > 0 ? (
          <div className="mt-4 max-h-32 space-y-0.5 overflow-y-auto rounded-md bg-muted/40 p-2">
            {monitor.logs.slice(-10).reverse().map((l, i) => (
              <p key={`${l.time}-${i}`} className="font-data text-[11px] text-muted-foreground">
                <span className="mr-1.5 opacity-60">{l.time}</span>
                {l.message}
              </p>
            ))}
          </div>
        ) : null}
      </section>

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <div className="flex min-h-8 items-center justify-between gap-3">
          <h2 className="text-sm font-medium">代理与出口</h2>
          <Button variant="secondary" size="sm" onClick={() => void probeProxy()} disabled={probingProxy}>
            {probingProxy ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Radio className="size-3.5" aria-hidden="true" />}
            测试连通性
          </Button>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">访问 Handle / AgentRouter 必须经本地代理。以下配置来自服务端环境变量 / .env，只读——修改后需重启服务。</p>
        {proxyQ.isLoading ? (
          <LoadingState className="min-h-16" />
        ) : proxyQ.isError ? (
          <ErrorState message={errorMessage(proxyQ.error, "代理信息加载失败")} onRetry={() => void proxyQ.refetch()} />
        ) : proxyQ.data ? (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="rounded-md border p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium">上游代理</span>
                {proxyQ.data.proxy.reachable === false ? (
                  <Badge variant="secondary" className="text-[11px] text-checkin-failed">✗ 不可达</Badge>
                ) : proxyQ.data.proxy.reachable === true ? (
                  <Badge variant="secondary" className="text-[11px] text-checkin-done">✓ 可达</Badge>
                ) : null}
              </div>
              <p className="font-data mt-1.5 text-xs">{proxyQ.data.proxy.url}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                来源：{proxyQ.data.proxy.source}
                {proxyQ.data.proxy.has_credentials ? " · 凭据已隐藏" : ""}
                {proxyQ.data.proxy.reachable === false ? " —— 代理未运行时余额查询会静默失败，请先启动 mihomo" : ""}
              </p>
            </div>
            <div className="rounded-md border p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium">mihomo 出口轮换</span>
                <Badge variant="secondary" className="text-[11px]">
                  {proxyQ.data.mihomo.rotation_enabled ? "已启用" : "未配置"}
                </Badge>
              </div>
              <p className="font-data mt-1.5 text-xs">{proxyQ.data.mihomo.group || "--"}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {proxyQ.data.mihomo.rotation_enabled
                  ? "AgentRouter 查询期间轮换出口 IP，避开阿里云 WAF 的按 IP 限流"
                  : "未配置 MIHOMO_GROUP —— 以单一出口访问（安全降级，非故障）"}
                {" · "}
                {proxyQ.data.mihomo.config_exists ? "已找到配置" : "未找到 mihomo 配置文件"}
              </p>
            </div>
          </div>
        ) : null}
        <Button variant="ghost" size="sm" className="mt-2 text-muted-foreground" onClick={() => void proxyQ.refetch()}>
          <RefreshCw className="size-3.5" aria-hidden="true" />
          刷新
        </Button>
      </section>
    </div>
  );
}
