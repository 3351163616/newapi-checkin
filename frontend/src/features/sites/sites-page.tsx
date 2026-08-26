import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Loader2, Plus, Radar, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState, ErrorState, LoadingState } from "@/shared/components/data-state";
import { PageHeader } from "@/shared/components/page-header";
import { apiPost } from "@/shared/api/client";
import { getSiteTurnstile } from "@/features/checkin/checkin-api";
import { siteDotClass } from "@/shared/lib/site-color";
import type { NewapiSite, SiteProbeResponse, SitesResponse } from "@/types";

import { fetchSiteAccounts, fetchSites } from "@/features/accounts/accounts-api";
import { errorMessage } from "@/features/checkin/checkin-format";

export function SitesPage() {
  const queryClient = useQueryClient();
  const sitesQ = useQuery({ queryKey: ["accounts", "sites"], queryFn: fetchSites });
  const sites = sitesQ.data ?? [];

  const countsQ = useQuery({
    queryKey: ["accounts", "site-accounts", sites.map((s) => s.id)] as const,
    queryFn: async () => {
      const entries = await Promise.all(sites.map(async (s) => [s.id, (await fetchSiteAccounts(s.id)).length] as const));
      return Object.fromEntries(entries) as Record<string, number>;
    },
    enabled: sitesQ.isSuccess && sites.length > 0,
  });

  const [newId, setNewId] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newDomain, setNewDomain] = useState("");
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<{ domain: string; ok: boolean; system_name: string; version: string; checkin_enabled: boolean; turnstile_check: boolean; quota_per_unit: number } | null>(null);
  const [adding, setAdding] = useState(false);

  if (sitesQ.isError) return <ErrorState message={errorMessage(sitesQ.error, "站点列表加载失败")} onRetry={() => void sitesQ.refetch()} />;

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
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(`已移除 ${site.label}（账号数据已保留）`);
    } catch (err) {
      toast.error(errorMessage(err, "删除失败"));
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="站点管理" description="接入任意 new-api 同构站点" />

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
            <SiteCard key={site.id} site={site} count={countsQ.data?.[site.id] ?? 0} index={i} onDelete={() => void onDelete(site)} />
          ))}
        </div>
      )}
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
