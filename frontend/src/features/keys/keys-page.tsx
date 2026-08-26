import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, MoreHorizontal, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorState, LoadingState } from "@/shared/components/data-state";
import { PageHeader } from "@/shared/components/page-header";
import { apiPost } from "@/shared/api/client";
import { siteDotClass } from "@/shared/lib/site-color";
import { cn } from "@/shared/lib/cn";
import type { ApiKey, KeyAccountKeys, KeyMutationResponse, KeysListResponse, SiteAccount } from "@/types";

import { fetchLoginAccounts, fetchSavedConfig, fetchSiteAccounts, fetchSites, fetchTokenAccounts } from "@/features/accounts/accounts-api";
import { errorMessage } from "@/features/checkin/checkin-format";

type CopyFormat = "key" | "named" | "json";

const KEY_STATUS: Record<number, string> = { 1: "启用", 2: "已禁用", 3: "已过期", 4: "已耗尽" };

function tsDate(ts: number | null | undefined): string {
  if (!ts || ts < 0) return "-";
  return new Date(ts * 1000).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

interface AccountRefEntry {
  ref: string;
  providerId: string;
  name: string;
}

/** 三种复制格式；脱敏未取到的密钥不进复制内容，计入 skipped（tests/test_site_frontend.mjs 覆盖的行为） */
function buildKeyText(accounts: KeyAccountKeys[], format: CopyFormat): { text: string; count: number; skipped: number } {
  const lines: string[] = [];
  let count = 0;
  let skipped = 0;
  for (const acc of accounts) {
    if (!acc.success) continue;
    for (const key of acc.keys) {
      if (key.masked || !key.key) {
        skipped += 1;
        continue;
      }
      count += 1;
      if (format === "key") lines.push(key.key);
      else if (format === "named") lines.push(`${acc.name}｜${key.name}：${key.key}`);
      else lines.push(JSON.stringify({ account: acc.name, name: key.name, key: key.key }));
    }
  }
  return { text: lines.join(format === "json" ? ",\n" : "\n"), count, skipped };
}

export function KeysPage() {
  const queryClient = useQueryClient();
  const [format, setFormat] = useState<CopyFormat>("key");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newQuota, setNewQuota] = useState("");
  const [newDays, setNewDays] = useState("");

  const sitesQ = useQuery({ queryKey: ["accounts", "sites"], queryFn: fetchSites });
  const tokenQ = useQuery({ queryKey: ["accounts", "token"], queryFn: fetchTokenAccounts });
  const cookieQ = useQuery({ queryKey: ["accounts", "cookie"], queryFn: fetchSavedConfig });
  const loginQ = useQuery({ queryKey: ["accounts", "login"], queryFn: fetchLoginAccounts });
  // useMemo 稳定引用：避免兜底空数组每次渲染新建引用，扰动下游 useQuery 的依赖判断
  const sites = useMemo(() => sitesQ.data ?? [], [sitesQ.data]);
  const siteAccountsQ = useQuery({
    queryKey: ["accounts", "site-accounts", sites.map((s) => s.id)] as const,
    queryFn: async () => {
      const entries = await Promise.all(sites.map(async (s) => [s.id, await fetchSiteAccounts(s.id)] as const));
      return Object.fromEntries(entries) as Record<string, SiteAccount[]>;
    },
    enabled: sitesQ.isSuccess && sites.length > 0,
  });

  const refs: AccountRefEntry[] = useMemo(() => {
    const out: AccountRefEntry[] = [];
    (tokenQ.data ?? []).forEach((a, i) => out.push({ ref: `token:${i}`, providerId: "token", name: a.name }));
    ((cookieQ.data?.accounts ?? []).forEach((a, i) => out.push({ ref: `cookie:${i}`, providerId: "cookie", name: a.name })));
    (loginQ.data ?? []).forEach((a, i) => out.push({ ref: `login:${i}`, providerId: "agentrouter", name: a.name }));
    for (const s of sites) {
      (siteAccountsQ.data?.[s.id] ?? []).forEach((a, i) => out.push({ ref: `site:${s.id}:${i}`, providerId: s.id, name: a.name }));
    }
    return out;
  }, [tokenQ.data, cookieQ.data, loginQ.data, siteAccountsQ.data, sites]);

  const keysQ = useQuery({
    queryKey: ["keys", "list", refs.map((r) => r.ref)] as const,
    queryFn: () => apiPost<KeysListResponse>("/keys/list", { refs: refs.map((r) => r.ref) }),
    enabled: refs.length > 0,
  });

  if (sitesQ.isError) return <ErrorState message={errorMessage(sitesQ.error, "加载失败")} onRetry={() => void sitesQ.refetch()} />;

  const accounts = keysQ.data?.accounts ?? [];
  const byRef = new Map(accounts.map((a) => [a.ref, a]));

  function toggleRef(ref: string, next: boolean) {
    setSelected((current) => {
      const next2 = new Set(current);
      if (next) next2.add(ref);
      else next2.delete(ref);
      return next2;
    });
  }

  async function copyKeys(scope: "selected" | "all") {
    const targets = scope === "all" ? accounts : accounts.filter((a) => selected.has(a.ref));
    const { text, count, skipped } = buildKeyText(targets, format);
    if (count === 0) {
      toast.info(skipped > 0 ? `没有可复制的密钥（${skipped} 个仍为脱敏状态，先点刷新取全量）` : "没有可复制的密钥");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`已复制 ${count} 个密钥${skipped > 0 ? `，跳过 ${skipped} 个脱敏未取到的` : ""}`);
    } catch {
      toast.error("复制失败（需要 HTTPS 或 localhost 环境）");
    }
  }

  async function refreshAccount(ref: string) {
    try {
      await apiPost<KeysListResponse>("/keys/list", { refs: [ref], refresh: true });
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
      toast.success("已刷新");
    } catch (err) {
      toast.error(errorMessage(err, "刷新失败"));
    }
  }

  async function createKey() {
    if (!creating || !newName.trim()) return;
    try {
      const unlimited = newQuota.trim() === "";
      const quotaNum = unlimited ? undefined : Number(newQuota);
      if (!unlimited && (!Number.isFinite(quotaNum) || (quotaNum ?? 0) < 0)) {
        toast.error("额度必须是正数，留空表示不限额度");
        return;
      }
      const days = newDays.trim() === "" ? undefined : Number(newDays);
      if (days !== undefined && (!Number.isFinite(days) || days <= 0)) {
        toast.error("有效天数必须是正数，留空表示永不过期");
        return;
      }
      const expired = days === undefined ? -1 : Math.floor(Date.now() / 1000) + days * 86400;
      await apiPost<KeyMutationResponse>("/keys/create", {
        ref: creating,
        name: newName.trim(),
        unlimited_quota: unlimited,
        ...(unlimited ? {} : { remain_quota: quotaNum }),
        expired_time: expired,
      });
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
      toast.success(`已创建密钥 ${newName.trim()}`);
      setCreating(null);
      setNewName("");
      setNewQuota("");
      setNewDays("");
    } catch (err) {
      toast.error(errorMessage(err, "创建失败"));
    }
  }

  async function deleteKey(ref: string, key: ApiKey) {
    if (!window.confirm(`删除密钥「${key.name}」？此操作不可撤销。`)) return;
    try {
      await apiPost<KeyMutationResponse>("/keys/delete", { ref, id: key.id });
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
      toast.success(`已删除 ${key.name}`);
    } catch (err) {
      toast.error(errorMessage(err, "删除失败"));
    }
  }

  const totalKeys = accounts.filter((a) => a.success).reduce((n, a) => n + a.keys.length, 0);

  return (
    <div className="space-y-5">
      <PageHeader
        title="API 密钥"
        description={`${refs.length} 个账号 · ${totalKeys} 个密钥`}
        actions={
          <Button variant="secondary" size="sm" onClick={() => void queryClient.invalidateQueries({ queryKey: ["keys"] })}>
            <RefreshCw className="size-3.5" aria-hidden="true" />
            刷新
          </Button>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Checkbox
            checked={accounts.length > 0 && selected.size === accounts.length}
            onCheckedChange={(checked) => setSelected(checked === true ? new Set(accounts.map((a) => a.ref)) : new Set())}
            aria-label="全选"
          />
          全选（已选 {selected.size}）
        </label>
        <div className="flex items-center gap-1.5">
          <Select value={format} onValueChange={(v) => setFormat(v as CopyFormat)}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="key">纯密钥</SelectItem>
              <SelectItem value="named">带账号名</SelectItem>
              <SelectItem value="json">JSON</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="secondary" size="sm" onClick={() => void copyKeys("selected")} disabled={selected.size === 0}>
            <Copy className="size-3.5" aria-hidden="true" />
            复制选中
          </Button>
          <Button size="sm" onClick={() => void copyKeys("all")}>
            <Copy className="size-3.5" aria-hidden="true" />
            复制全部
          </Button>
        </div>
      </div>

      {keysQ.isLoading || tokenQ.isLoading || cookieQ.isLoading ? (
        <LoadingState />
      ) : refs.length === 0 ? (
        <EmptyState message="还没有账号——先去「账号管理」添加" />
      ) : (
        <div className="space-y-2">
          {refs.map((entry, i) => {
            const data = byRef.get(entry.ref);
            const failed = data && !data.success;
            return (
              <section
                key={entry.ref}
                className="animate-in fade-in slide-in-from-bottom-1 fill-mode-both overflow-hidden rounded-lg bg-card duration-300"
                style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
                  <label className="flex min-w-0 items-center gap-2">
                    <Checkbox checked={selected.has(entry.ref)} onCheckedChange={(c) => toggleRef(entry.ref, c === true)} aria-label={`选择 ${entry.name}`} />
                    <span className={siteDotClass(entry.providerId)} aria-hidden="true" />
                    <span className="truncate text-sm font-medium">{entry.name}</span>
                    <span className="font-data shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{entry.ref}</span>
                    {data?.success ? <Badge variant="secondary" className="text-[11px]">{data.keys.length} 个密钥{data.cached ? " · 缓存" : ""}</Badge> : null}
                    {failed ? <Badge variant="destructive" className="text-[11px]">加载失败</Badge> : null}
                  </label>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" onClick={() => void refreshAccount(entry.ref)}>
                      <RefreshCw className="size-3.5" aria-hidden="true" />
                      刷新
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setCreating(entry.ref)}>
                      <Plus className="size-3.5" aria-hidden="true" />
                      新建
                    </Button>
                  </div>
                </div>

                {failed ? (
                  <p className="px-4 py-3 text-xs text-destructive">{(data as { error: string }).error}</p>
                ) : data?.success && data.keys.length > 0 ? (
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead>名称</TableHead>
                        <TableHead>状态</TableHead>
                        <TableHead className="text-right">剩余额度</TableHead>
                        <TableHead className="text-right">已用</TableHead>
                        <TableHead>到期</TableHead>
                        <TableHead>最近使用</TableHead>
                        <TableHead className="w-20 text-right">操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.keys.map((key) => (
                        <TableRow key={key.id}>
                          <TableCell>
                            <div className="flex items-center gap-1.5 text-xs">
                              <KeyRound className="size-3 text-muted-foreground" aria-hidden="true" />
                              <span className="font-medium">{key.name}</span>
                              {key.masked ? <Badge variant="outline" className="text-[10px] text-muted-foreground">脱敏</Badge> : null}
                            </div>
                            <div className="font-data mt-0.5 text-[10px] text-muted-foreground">{key.masked ? key.key : `${key.key.slice(0, 10)}…`}</div>
                          </TableCell>
                          <TableCell>
                            <span className={cn("text-xs", key.status === 1 ? "text-checkin-done" : key.status === 3 || key.status === 4 ? "text-checkin-failed" : "text-muted-foreground")}>
                              {KEY_STATUS[key.status] ?? `状态 ${key.status}`}
                            </span>
                          </TableCell>
                          <TableCell className="text-right">
                            {key.unlimited_quota ? (
                              <span className="font-data text-xs text-muted-foreground">无限额度</span>
                            ) : (
                              <div className="flex items-center justify-end gap-2">
                                <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                                  <div
                                    className={cn("h-full rounded-full transition-all duration-500", key.remain_quota / Math.max(0.01, key.used_quota + key.remain_quota) < 0.15 ? "bg-checkin-failed" : "bg-foreground/60")}
                                    style={{ width: `${Math.max(2, (key.remain_quota / Math.max(0.01, key.used_quota + key.remain_quota)) * 100)}%` }}
                                  />
                                </div>
                                <span className="font-data text-xs tabular-nums">${key.remain_quota.toFixed(2)}</span>
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="font-data text-right text-xs">${key.used_quota.toFixed(2)}</TableCell>
                          <TableCell className="font-data text-xs">{tsDate(key.expired_time)}</TableCell>
                          <TableCell className="font-data text-xs">{tsDate(key.accessed_time)}</TableCell>
                          <TableCell className="text-right">
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon" className="size-7" aria-label={`${key.name} 操作`}>
                                  <MoreHorizontal className="size-4" aria-hidden="true" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                {!key.masked ? (
                                  <DropdownMenuItem
                                    onClick={() => {
                                      void navigator.clipboard?.writeText(key.key).then(() => toast.success("已复制"), () => toast.error("复制失败"));
                                    }}
                                  >
                                    <Copy className="size-3.5" aria-hidden="true" />
                                    复制密钥
                                  </DropdownMenuItem>
                                ) : (
                                  <DropdownMenuItem disabled>密钥已脱敏，先点「刷新」取全量</DropdownMenuItem>
                                )}
                                <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={() => void deleteKey(entry.ref, key)}>
                                  <Trash2 className="size-3.5" aria-hidden="true" />
                                  删除
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <p className="px-4 py-3 text-xs text-muted-foreground">暂无密钥</p>
                )}

                {creating === entry.ref ? (
                  <div className="space-y-3 border-t bg-muted/20 px-4 py-3">
                    <div className="grid gap-3 sm:grid-cols-4">
                      <div className="space-y-1">
                        <Label className="text-xs">名称</Label>
                        <Input value={newName} onChange={(e) => setNewName(e.target.value)} className="h-8 text-xs" placeholder="my-key" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">额度（$，留空=无限）</Label>
                        <Input value={newQuota} onChange={(e) => setNewQuota(e.target.value)} className="h-8 text-xs" placeholder="无限" inputMode="decimal" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">有效天数（留空=永不）</Label>
                        <Input value={newDays} onChange={(e) => setNewDays(e.target.value)} className="h-8 text-xs" placeholder="永不" inputMode="numeric" />
                      </div>
                      <div className="flex items-end gap-2">
                        <Button size="sm" onClick={() => void createKey()}>
                          创建
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setCreating(null)}>取消</Button>
                      </div>
                    </div>
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
