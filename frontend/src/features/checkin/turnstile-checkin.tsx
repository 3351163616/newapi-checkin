/**
 * Web UI 内嵌 Turnstile 签到（Turnstile 站点专用，替代「去站点 Console 粘贴脚本」的原始流程）。
 *
 * 与 build-site-script.ts（浏览器脚本）同一套约束，差别只在验证组件渲染在哪：
 *
 * 1. widget 直接渲染在本页 —— token 由用户浏览器解决；提交也由**同一个浏览器**直连站点
 *    （fetch site.domain），solve 与 redeem 的出口 IP 天然一致。服务器端代签反而有风险：
 *    new-api 的 TurnstileCheck 会把 remoteip（站点看到的请求 IP）传给 siteverify，
 *    后端走代理时与解 token 的浏览器 IP 不一致可能被拒。
 * 2. 提交直连依赖 new-api 的 CORS 全开放（AllowAllOrigins，见 new-api middleware/cors.go）；
 *    个别魔改部署若关了 CORS 或被 Cloudflare 拦成 HTML，失败原因写进对应账号的 chip，
 *    一个都没签成时整轮终止并引导一键回退「浏览器脚本」老路（onFallback）。
 * 3. credentials:'omit' —— 与脚本版同理：new-api 的 authHelper 是 session 优先，带上浏览器
 *    登录态会让除当前登录者外的账号全部 401；omit 后各账号用自己的 access_token。
 * 4. sitekey 来自探测接口，绝不硬编码；站点若把 sitekey 限制在其自身域名，widget 会渲染失败
 *    （error-callback），同样走回退。
 * 5. widget 用 seq + settle 序号隔离 —— 防止上一账号的超时回调 resolve 到下一账号。
 *    reset 必须在 getToken 注册好等待者**之后**调用：invisible 挑战解得飞快，先 reset 后
 *    等待会把 token 白白丢掉，下一账号就永远等不到回调。
 * 6. token 一次性、有效期 5 分钟：解一个立刻提交一个，成功后再取下一个。
 */

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Terminal } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatusChip } from "@/features/checkin/checkin-card";
import { syncSiteCheckin } from "@/features/checkin/checkin-api";
import { siteDotClass } from "@/shared/lib/site-color";
import type { CheckinAccountRunStatus, NewapiSite, SiteAccount, TurnstileStatus } from "@/types";

interface TurnstileApi {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (widgetId?: string) => void;
  remove: (widgetId?: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let turnstileApiP: Promise<TurnstileApi> | null = null;

/** 页面级只加载一次 api.js；失败清空缓存让下次重试。render=explicit 走程序化渲染模式。 */
function loadTurnstileApi(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (!turnstileApiP) {
    turnstileApiP = new Promise<TurnstileApi>((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("Turnstile API 未就绪")));
      s.onerror = () => {
        turnstileApiP = null;
        reject(new Error("Turnstile 脚本加载失败（challenges.cloudflare.com 不可达）"));
      };
      document.head.appendChild(s);
    });
  }
  return turnstileApiP;
}

type Chip = { name: string; status: CheckinAccountRunStatus; message: string };

export function TurnstileCheckinDialog({
  site,
  accounts,
  ts,
  total,
  onFallback,
  onClose,
}: {
  site: NewapiSite;
  /** 待签账号（已剔除今日已签） */
  accounts: SiteAccount[];
  ts: TurnstileStatus;
  total: number;
  onFallback: () => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<"running" | "done" | "fatal">("running");
  const [chips, setChips] = useState<Chip[]>(() => accounts.map((a) => ({ name: a.name, status: "pending" as const, message: "等待验证" })));
  const [fatalMsg, setFatalMsg] = useState("");
  const [summary, setSummary] = useState("");
  const [syncing, setSyncing] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const widRef = useRef<string | null>(null);
  const userCancelRef = useRef(false);

  async function doSync() {
    setSyncing(true);
    try {
      await syncSiteCheckin(site.id);
      await queryClient.invalidateQueries({ queryKey: ["checkin"] });
      toast.success("已同步签到状态");
    } catch {
      toast.error("同步失败，稍后可再点「同步状态」");
    } finally {
      setSyncing(false);
    }
  }

  async function redeem(a: SiteAccount, token: string): Promise<{ ok: boolean; already: boolean; message: string }> {
    const r = await fetch(site.domain + (site.sign_in_path || "/api/user/checkin") + "?turnstile=" + encodeURIComponent(token), {
      method: "POST",
      credentials: "omit",
      headers: {
        Authorization: "Bearer " + a.access_token,
        [site.api_user_key || "new-api-user"]: String(a.user_id),
        Accept: "application/json",
      },
    });
    let d: { success?: boolean; message?: string };
    try {
      d = (await r.json()) as { success?: boolean; message?: string };
    } catch {
      return { ok: false, already: false, message: `HTTP ${r.status}，响应不是 JSON（可能被 Cloudflare 拦截）` };
    }
    if (d.success) return { ok: true, already: false, message: d.message || "签到成功" };
    const m = d.message || "";
    if (m.includes("已签")) return { ok: false, already: true, message: m };
    return { ok: false, already: false, message: m || "签到失败" };
  }

  async function run(stop: () => boolean): Promise<void> {
    let tsApi: TurnstileApi;
    try {
      tsApi = await loadTurnstileApi();
    } catch (e) {
      setFatalMsg(e instanceof Error ? e.message : String(e));
      setPhase("fatal");
      return;
    }
    if (stop()) return;
    const box = boxRef.current;
    if (!box) {
      setFatalMsg("验证容器未挂载");
      setPhase("fatal");
      return;
    }

    let seq = 0;
    let waiter: { resolve: (t: string) => void; reject: (e: Error) => void } | null = null;
    const settle = (s: number, err: Error | null, token?: string) => {
      if (s !== seq || !waiter) return;
      const p = waiter;
      waiter = null;
      if (err) p.reject(err);
      else p.resolve(token!);
    };
    let wid: string | null = null;
    const getToken = () => {
      const s = ++seq;
      return new Promise<string>((resolve, reject) => {
        waiter = { resolve, reject };
        if (wid === null) {
          wid = tsApi.render(box, {
            sitekey: ts.site_key,
            callback: (t: string) => settle(seq, null, t),
            "error-callback": (code: unknown) =>
              settle(seq, new Error(`Turnstile 组件错误 ${String(code)}（常见于站点把 sitekey 限制在其自身域名，可改用浏览器脚本）`)),
            "timeout-callback": () => settle(seq, new Error("Turnstile 验证超时，可重试或改用浏览器脚本")),
          });
          widRef.current = wid;
        } else {
          tsApi.reset(wid);
        }
        // 挂一个兜底看门狗；stale 定时器因序号不匹配自然失效
        setTimeout(() => settle(s, new Error("Turnstile 60 秒未完成（可能需要手动点一下验证框）")), 60000);
      });
    };

    let ok = 0;
    let already = 0;
    let bad = 0;
    for (let i = 0; i < accounts.length; i++) {
      if (stop()) return;
      const a = accounts[i];
      try {
        const token = await getToken();
        if (stop()) return;
        const r = await redeem(a, token);
        if (r.ok) ok++;
        else if (r.already) already++;
        else bad++;
        const status: CheckinAccountRunStatus = r.ok ? "signed" : r.already ? "already" : "failed";
        setChips((prev) => prev.map((c, idx) => (idx === i ? { name: a.name, status, message: r.message } : c)));
      } catch (e) {
        const msg =
          e instanceof TypeError
            ? "请求被浏览器拦截（CORS 或网络不通），建议改用浏览器脚本"
            : e instanceof Error
              ? e.message
              : String(e);
        bad++;
        setChips((prev) => prev.map((c, idx) => (idx === i ? { name: a.name, status: "failed" as const, message: msg } : c)));
        if (ok + already === 0) {
          // 一个都没签成就系统性失败（sitekey 域名限制 / CORS 关闭 / 组件加载不了），整轮没有意义
          setFatalMsg(msg);
          setPhase("fatal");
          return;
        }
      }
      // 账号之间稍作间隔，别像机器人连发
      await new Promise((r) => setTimeout(r, 400));
    }
    setSummary(`新签 ${ok} · 已签 ${already} · 失败 ${bad}`);
    setPhase("done");
    void doSync();
  }

  useEffect(() => {
    // cancelled 是本 effect 闭包自己的取消标记：StrictMode 下 effect 会挂载两次，
    // 第一轮 cleanup 后其 run 停在最近的检查点，第二轮从头跑，widget 只渲染一份
    let cancelled = false;
    const stop = () => cancelled || userCancelRef.current;
    void run(stop);
    return () => {
      cancelled = true;
      try {
        window.turnstile?.remove(widRef.current ?? undefined);
      } catch {
        /* 卸载时 widget 可能已不存在 */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-md animate-in fade-in zoom-in-95 space-y-3 rounded-lg border bg-card p-5 duration-300">
        <div className="flex items-center gap-2">
          <span className={siteDotClass(site.id)} aria-hidden="true" />
          <h2 className="truncate text-sm font-medium">{site.label} · 内嵌验证签到</h2>
          <Badge variant="secondary" className="text-[11px]">Turnstile</Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          验证框就嵌在本页：解一个 token 立刻提交一个账号（token 一次性），共 {accounts.length} 个待签 / {total} 个账号。
          弹出复选框时点一下即可。
        </p>

        <div className="flex min-h-20 items-center justify-center rounded-md border bg-muted/30 p-3">
          <div ref={boxRef} />
        </div>

        {phase === "fatal" ? (
          <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-600 dark:text-amber-400">
            验证没法在本页完成：{fatalMsg}
          </p>
        ) : null}
        {phase === "done" ? <p className="text-xs text-checkin-done">{summary}{syncing ? " · 正在同步…" : ""}</p> : null}

        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <StatusChip key={c.name} name={c.name} status={c.status} message={c.message} />
          ))}
        </div>

        <div className="flex flex-wrap justify-end gap-2">
          {phase === "running" ? (
            <Button variant="ghost" onClick={() => { userCancelRef.current = true; onClose(); }}>
              取消
            </Button>
          ) : (
            <>
              <Button variant="secondary" disabled={syncing} onClick={() => void doSync()}>
                <RefreshCw className="size-3.5" aria-hidden="true" />
                同步状态
              </Button>
              <Button variant="secondary" onClick={onFallback}>
                <Terminal className="size-3.5" aria-hidden="true" />
                改用浏览器脚本
              </Button>
              <Button variant="ghost" onClick={onClose}>关闭</Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
