import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, ClipboardCopy, Globe, Loader2, Plus, Radar, ShieldCheck, Trash2, XCircle } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { EmptyState, ErrorState, LoadingState } from "@/shared/components/data-state";
import { PageHeader } from "@/shared/components/page-header";
import { apiGet, apiPost } from "@/shared/api/client";
import { getSiteTurnstile } from "@/features/checkin/checkin-api";
import { siteDotClass } from "@/shared/lib/site-color";
import type { NewapiSite, SiteProbeResponse, SitesResponse } from "@/types";

import { fetchSitesFull } from "@/features/accounts/accounts-api";
import { errorMessage } from "@/features/checkin/checkin-format";

/** 生成书签采集脚本：登录任意站点后点一下，自动读取 localStorage 的 token（及 user id）并上报回填 */
function buildBookmarklet(key: string, origin: string) {
  const k = JSON.stringify(key);
  const e = JSON.stringify(`${origin}/api/collect`);
  return `javascript:(function(){var k=${k},e=${e};var t="",u="";var ks=["access_token","accessToken","new_api_token","new-api-token","user_token","token"];for(var i=0;i<ks.length&&!t;i++){try{var v=localStorage.getItem(ks[i]);if(v&&v.length>8&&v.indexOf("{")<0)t=v}catch(x){}}if(!t){for(var j=0;j<localStorage.length&&!t;j++){var kk=localStorage.key(j),vv=localStorage.getItem(kk);if(vv&&vv.length<5000&&(vv.indexOf("access_token")>-1||vv.indexOf("accessToken")>-1)){try{var o=JSON.parse(vv);if(o.access_token)t=o.access_token;if(!u)u=o.user_id||o.id||""}catch(x){}}}}if(!u){var uk=["user","userInfo","user_info","new-api-user","userData"];for(var i=0;i<uk.length&&!u;i++){try{var uv=localStorage.getItem(uk[i]);if(uv){var uo=JSON.parse(uv);u=uo.id||uo.user_id||uo.user?.id||""}}catch(x){}}}if(!t){alert("未找到 token，请确认已登录");return}fetch(e,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({site_url:location.origin,access_token:t,user_id:u})}).then(function(r){return r.json()}).then(function(d){alert(d.success?"[OK] "+d.message:"[失败] "+(d.error||""))}).catch(function(){alert("连接采集服务失败")})})();`;
}

function StatusDot({ site }: { site: NewapiSite }) {
  const st = site.status?.status ?? "unknown";
  const err = site.status?.error;
  const common = "size-3.5 shrink-0";
  if (st === "ok") {
    return <CheckCircle2 className={`${common} text-checkin-done`} aria-label="账号可用" />;
  }
  if (st === "invalid") {
    return (
      <span className="shrink-0" title={err ? `最近检查失败：${err}` : "账号不可用"}>
        <XCircle className="size-3.5 text-destructive" aria-label="账号不可用" />
      </span>
    );
  }
  return (
    <span className="shrink-0" title="无账号或未检查">
      <Circle className="size-3.5 text-muted-foreground/40" aria-label="无账号" />
    </span>
  );
}

export function SitesPage() {
  const queryClient = useQueryClient();
  // 注意：queryKey 不能与 fetchSites（返回数组）共用 ["accounts","sites"]，
  // 否则会命中其他页面缓存的旧数组，data.sites 恒为 undefined
  const sitesQ = useQuery({ queryKey: ["sites", "manage"], queryFn: fetchSitesFull });
  const sites = sitesQ.data?.sites ?? [];
  // 账号数由后端 with_counts=1 顺带返回（读盘有 mtime 缓存），不再逐站点发请求
  const counts = sitesQ.data?.counts;

  const [newId, setNewId] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newDomain, setNewDomain] = useState("");
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<{ domain: string; ok: boolean; system_name: string; version: string; checkin_enabled: boolean; turnstile_check: boolean; quota_per_unit: number } | null>(null);
  const [adding, setAdding] = useState(false);
  const [collectOpen, setCollectOpen] = useState(false);
  const collectKeyQ = useQuery({
    queryKey: ["sites", "collect-key"],
    queryFn: async () => (await apiGet<{ success: boolean; key: string }>("/collect/key")).key,
    enabled: collectOpen,
  });

  if (sitesQ.isError) return <ErrorState message={errorMessage(sitesQ.error, "站点列表加载失败")} onRetry={() => void sitesQ.refetch()} />;

  const bookmarklet = collectKeyQ.data ? buildBookmarklet(collectKeyQ.data, window.location.origin) : "";

  async function copyBookmarklet() {
    try {
      await navigator.clipboard.writeText(bookmarklet);
      toast.success("已复制采集脚本——在浏览器书签栏新建书签，地址栏粘贴即可");
    } catch {
      toast.error("复制失败，请手动选中下方脚本复制");
    }
  }

  async function onProbe() {
    if (!newDomain.trim()) {
      toast.error("先填域名，例如 api.example.com");
      return;
    }
    setProbing(true);
    setProbeResult(null);
    try {
      const res = await apiPost<SiteProbeResponse>("/sites/probe", { domain: newDomain.trim() });
      setProbeResult({ domain: newDomain.trim(), ok: true, ...res.info });
      toast.success(`探测成功：${res.info.system_name}`);
      if (!newId) setNewId(newDomain.trim().replace(/^https?:\/\//, "").split(".")[0]?.replace(/[^a-z0-9-]/gi, "") ?? "");
      if (!newLabel) setNewLabel(res.info.system_name);
    } catch (err) {
      toast.error(errorMessage(err, "探测失败：不是 new-api 站点或无法访问"));
    } finally {
      setProbing(false);
    }
  }

  async function onAdd(event: FormEvent) {
    event.preventDefault();
    if (!newId.trim() || !newLabel.trim() || !newDomain.trim()) {
      toast.error("站点 ID、显示名称、域名都要填");
      return;
    }
    if (!/^[a-z0-9][a-z0-9-]*$/i.test(newId.trim())) {
      toast.error("站点 ID 只能含字母、数字和连字符");
      return;
    }
    if (sites.some((s) => s.id === newId.trim())) {
      toast.error(`站点 ID「${newId.trim()}」已存在`);
      return;
    }
    setAdding(true);
    try {
      const input = { id: newId.trim(), label: newLabel.trim(), domain: newDomain.trim().replace(/^https?:\/\//, "") };
      await apiPost<SitesResponse>("/sites", { sites: [...sites, input] });
      // 本页缓存键是 ["sites", ...]，其他页面的站点列表在 ["accounts","sites"]，两处都要失效
      await queryClient.invalidateQueries({ queryKey: ["sites"] });
      await queryClient.invalidateQueries({ queryKey: ["accounts", "sites"] });
      toast.success(`已接入 ${input.label}，去「账号管理」添加它的账号`);
      setNewId("");
      setNewLabel("");
      setNewDomain("");
      setProbeResult(null);
    } catch (err) {
      toast.error(errorMessage(err, "添加失败"));
    } finally {
      setAdding(false);
    }
  }

  async function onDelete(site: NewapiSite) {
    if (!window.confirm(`删除站点 ${site.label}？\n\n只从站点清单移除，账号数据与签到状态会保留在服务器上——重新添加同 ID 站点即可恢复。`)) return;
    try {
      await apiPost<SitesResponse>("/sites", { sites: sites.filter((s) => s.id !== site.id) });
      await queryClient.invalidateQueries({ queryKey: ["sites"] });
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(`已移除 ${site.label}（账号数据已保留）`);
    } catch (err) {
      toast.error(errorMessage(err, "删除失败"));
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="站点管理"
        description="接入任意 new-api 同构站点"
        actions={
          <Button variant="outline" onClick={() => setCollectOpen(true)}>
            <ClipboardCopy className="size-3.5" aria-hidden="true" />
            采集 Token
          </Button>
        }
      />

      <section className="rounded-lg bg-card p-4 sm:p-5">
        <h2 className="text-sm font-medium">接入新站点</h2>
        <p className="mt-1 text-xs text-muted-foreground">填个域名即可接入，后端零改动；建议先「探测一下」确认是 new-api 站点</p>
        <form className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_1.4fr_auto]" onSubmit={onAdd}>
          <div className="space-y-1">
            <Label htmlFor="site-id" className="text-xs">站点 ID（唯一，创建后勿改）</Label>
            <Input id="site-id" value={newId} onChange={(e) => setNewId(e.target.value)} className="h-8 font-data text-xs" placeholder="gorouter" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="site-label" className="text-xs">显示名称</Label>
            <Input id="site-label" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} className="h-8 text-xs" placeholder="GoRouter" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="site-domain" className="text-xs">域名</Label>
            <Input id="site-domain" value={newDomain} onChange={(e) => setNewDomain(e.target.value)} className="h-8 font-data text-xs" placeholder="https://gorouter.app" />
          </div>
          <div className="flex items-end gap-2">
            <Button type="button" variant="secondary" onClick={() => void onProbe()} disabled={probing}>
              {probing ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Radar className="size-3.5" aria-hidden="true" />}
              探测
            </Button>
            <Button type="submit" disabled={adding}>
              {adding ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Plus className="size-3.5" aria-hidden="true" />}
              添加
            </Button>
          </div>
        </form>
        {probeResult ? (
          <div className="animate-in fade-in slide-in-from-top-1 mt-3 rounded-md border p-3 text-xs duration-300">
            <p className="font-medium">
              <Globe className="mr-1 inline size-3.5" aria-hidden="true" />
              {probeResult.system_name} <span className="text-muted-foreground">v{probeResult.version}</span>
            </p>
            <p className="mt-1 text-muted-foreground">
              签到功能：{probeResult.checkin_enabled ? <span className="text-checkin-done">已开启</span> : "未开启"}
              {" · "}
              Turnstile 校验：{probeResult.turnstile_check ? <span className="text-checkin-pending">需要（走浏览器脚本签到）</span> : "不需要（可服务器端签到）"}
              {` · 1 美元 = ${probeResult.quota_per_unit} 配额`}
            </p>
          </div>
        ) : null}
      </section>

      {sitesQ.isLoading ? <LoadingState /> : null}

      {sites.length === 0 && !sitesQ.isLoading ? (
        <EmptyState message="还没有接入任何 new-api 站点——用上面的表单加一个" />
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {sites.map((site, i) => (
            <SiteCard key={site.id} site={site} count={counts?.[site.id] ?? 0} index={i} onDelete={() => void onDelete(site)} />
          ))}
        </div>
      )}

      <Sheet open={collectOpen} onOpenChange={setCollectOpen}>
        <SheetContent className="w-full sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>采集 Token</SheetTitle>
            <SheetDescription>
              登录站点后一键回填 access_token：登录 → 点书签 → 自动验证并写入配置。
            </SheetDescription>
          </SheetHeader>
          <div className="mt-4 space-y-4 text-sm">
            {sitesQ.data?.collect_key_ready === false ? (
              <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
                服务器未启用采集功能（缺少 COLLECT_KEY 环境变量）——在服务器 .env 里加一行 COLLECT_KEY=任意随机字符串后重启服务。
              </p>
            ) : null}
            <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
              <li>把下面的脚本存为浏览器书签（书签栏右键 → 添加网页 → 名称随意，地址粘贴脚本）</li>
              <li>打开目标站点控制台并登录（页面需已登录，token 存在浏览器本地）</li>
              <li>在站点页面上点击这个书签，脚本自动读取 token 并回填到本系统</li>
              <li>回到本页面查看状态点：<CheckCircle2 className="inline size-3 text-checkin-done" aria-hidden="true" /> 绿 = 可用，<XCircle className="inline size-3 text-destructive" aria-hidden="true" /> 红 = 失败，<Circle className="inline size-3 text-muted-foreground/40" aria-hidden="true" /> 灰 = 无账号</li>
            </ol>
            {collectKeyQ.isLoading ? (
              <p className="text-xs text-muted-foreground">加载中…</p>
            ) : collectKeyQ.data ? (
              <div className="space-y-2">
                <textarea
                  readOnly
                  value={bookmarklet}
                  rows={7}
                  className="w-full resize-none rounded-md border bg-muted/40 p-2 font-data text-[11px] leading-relaxed"
                  onFocus={(e) => e.currentTarget.select()}
                />
                <Button size="sm" onClick={() => void copyBookmarklet()}>
                  <ClipboardCopy className="size-3.5" aria-hidden="true" />
                  复制脚本
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">采集密钥加载失败，请刷新重试。</p>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function SiteCard({ site, count, index, onDelete }: { site: NewapiSite; count: number; index: number; onDelete: () => void }) {
  const turnstileQ = useQuery({
    queryKey: ["checkin", "site-turnstile", site.id],
    queryFn: () => getSiteTurnstile(site.id),
    retry: false,
  });
  const turnstile = turnstileQ.data?.turnstile;

  return (
    <section
      className="animate-in fade-in slide-in-from-bottom-2 fill-mode-both rounded-lg bg-card p-4 duration-300"
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={siteDotClass(site.id)} aria-hidden="true" />
            <StatusDot site={site} />
            <h3 className="truncate text-sm font-medium">{site.label}</h3>
            <span className="font-data shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{site.id}</span>
          </div>
          <a href={site.domain.startsWith("http") ? site.domain : `https://${site.domain}`} target="_blank" rel="noreferrer" className="font-data mt-1 block truncate text-xs text-muted-foreground hover:text-foreground hover:underline">
            {site.domain}
          </a>
        </div>
        <Button variant="ghost" size="icon" className="size-7 text-destructive hover:text-destructive" onClick={onDelete} aria-label={`删除 ${site.label}`}>
          <Trash2 className="size-3.5" aria-hidden="true" />
        </Button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary" className="text-[11px]">{count} 个账号</Badge>
        {site.status?.status === "invalid" && site.status.error ? (
          <Badge variant="outline" className="max-w-full truncate border-destructive/40 text-[11px] text-destructive" title={site.status.error}>
            {site.status.error}
          </Badge>
        ) : null}
        {turnstile ? (
          turnstile.enabled ? (
            <Badge variant="secondary" className="text-[11px] text-checkin-pending">Turnstile · 浏览器脚本</Badge>
          ) : (
            <Badge variant="secondary" className="text-[11px] text-checkin-done">
              <ShieldCheck className="mr-1 size-3" aria-hidden="true" />
              可服务器端签到
            </Badge>
          )
        ) : null}
        <Badge variant="outline" className="text-[11px] text-muted-foreground">${site.quota_per_unit}/配额单位</Badge>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">签到路径 <span className="font-data">{site.sign_in_path || "/api/user/checkin"}</span></p>
    </section>
  );
}
