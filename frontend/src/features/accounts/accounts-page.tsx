import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Coins, Filter, Globe2, Layers, Loader2, Plus, Search, Sparkles, Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState, ErrorState, LoadingState } from "@/shared/components/data-state";
import { KpiCard } from "@/shared/components/kpi-card";
import { PageHeader } from "@/shared/components/page-header";
import { SortableTableHead } from "@/shared/components/sortable-table-head";
import { useDebouncedValue } from "@/shared/hooks/use-debounced-value";
import { siteDotClass } from "@/shared/lib/site-color";
import { cn } from "@/shared/lib/cn";
import type { AccountRef } from "@/shared/lib/account-ref";
import type { CookieAccount, LoginAccount, QueryResult, QueryResultSuccess, SiteAccount, TokenAccount } from "@/types";

import {
  fetchLoginAccounts,
  fetchSavedConfig,
  fetchSiteAccounts,
  fetchSites,
  fetchTokenAccounts,
  fetchUsageBaseline,
  queryCookieBalances,
  queryLoginBalances,
  querySiteBalances,
  queryTokenBalances,
  saveCookieAccounts,
  saveLoginAccounts,
  saveSiteAccounts,
  saveTokenAccounts,
} from "@/features/accounts/accounts-api";
import { classifyJsonAccounts, exampleAccountsJson, serializeAccountsToJson } from "@/features/accounts/account-json";
import { planDedupe, type DedupeBuckets } from "@/features/accounts/account-dedupe";
import {
  computeTodayUsed,
  cookieExpiryInfo,
  formatMoney,
  maskToken,
  type CookieExpiryLevel,
} from "@/features/accounts/account-format";
import { errorMessage } from "@/features/checkin/checkin-format";

const ALL_SITES = "__all__";

interface AccountRow {
  ref: string;
  /** 用量基线/余额结果共用的 key 前缀：token/cookie → "provider"，login → "agentrouter"，站点 → site.id */
  keyPrefix: string;
  providerId: string;
  providerLabel: string;
  kindLabel: string;
  name: string;
  identifier: string;
  tokenMask?: string;
  cookieExpiry?: { level: CookieExpiryLevel; label: string } | null;
}

const expiryTextClass: Record<CookieExpiryLevel, string> = {
  expired: "text-checkin-failed",
  critical: "text-checkin-failed",
  warning: "text-checkin-pending",
  ok: "text-checkin-done",
};

type Kind = "token" | "cookie" | "login" | "site";

interface FormState {
  kind: Kind;
  siteId: string;
  name: string;
  accessToken: string;
  userId: string;
  session: string;
  apiUser: string;
  username: string;
  password: string;
}

const emptyForm: FormState = {
  kind: "token",
  siteId: "",
  name: "",
  accessToken: "",
  userId: "",
  session: "",
  apiUser: "",
  username: "",
  password: "",
};

export function AccountsPage() {
  const queryClient = useQueryClient();
  const [siteFilter, setSiteFilter] = useState(ALL_SITES);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);
  const [mode, setMode] = useState<"visual" | "json">("visual");
  const [jsonText, setJsonText] = useState("");
  const [applyingJson, setApplyingJson] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [balances, setBalances] = useState<Record<string, QueryResult>>({});
  const [form, setForm] = useState<FormState | null>(null);
  const [editingRef, setEditingRef] = useState<AccountRef | null>(null);

  const sitesQ = useQuery({ queryKey: ["accounts", "sites"], queryFn: fetchSites });
  const cookieQ = useQuery({ queryKey: ["accounts", "cookie"], queryFn: fetchSavedConfig });
  const tokenQ = useQuery({ queryKey: ["accounts", "token"], queryFn: fetchTokenAccounts });
  const loginQ = useQuery({ queryKey: ["accounts", "login"], queryFn: fetchLoginAccounts });
  const sites = sitesQ.data ?? [];
  const siteAccountsQ = useQuery({
    queryKey: ["accounts", "site-accounts", sites.map((s) => s.id)] as const,
    queryFn: async () => {
      const entries = await Promise.all(sites.map(async (s) => [s.id, await fetchSiteAccounts(s.id)] as const));
      return Object.fromEntries(entries) as Record<string, SiteAccount[]>;
    },
    enabled: sitesQ.isSuccess && sites.length > 0,
  });
  const baselineQ = useQuery({ queryKey: ["accounts", "baseline"], queryFn: fetchUsageBaseline });

  const buckets: DedupeBuckets = useMemo(
    () => ({
      token: tokenQ.data ?? [],
      cookie: cookieQ.data?.accounts ?? [],
      login: loginQ.data ?? [],
      site: Object.fromEntries(sites.map((s) => [s.id, siteAccountsQ.data?.[s.id] ?? []])),
    }),
    [tokenQ.data, cookieQ.data, loginQ.data, siteAccountsQ.data, sites],
  );

  const rows: AccountRow[] = useMemo(() => {
    const out: AccountRow[] = [];
    buckets.cookie.forEach((a, i) =>
      out.push({
        ref: `cookie:${i}`,
        keyPrefix: "provider",
        providerId: "cookie",
        providerLabel: "Handle Cookie 账号",
        kindLabel: "Cookie",
        name: a.name,
        identifier: a.api_user,
        cookieExpiry: cookieExpiryInfo(a.cookies?.session),
      }),
    );
    buckets.token.forEach((a, i) =>
      out.push({
        ref: `token:${i}`,
        keyPrefix: "provider",
        providerId: "token",
        providerLabel: "Handle Token 账号",
        kindLabel: "Token",
        name: a.name,
        identifier: a.user_id,
        tokenMask: maskToken(a.access_token),
      }),
    );
    buckets.login.forEach((a, i) =>
      out.push({
        ref: `login:${i}`,
        keyPrefix: "agentrouter",
        providerId: "agentrouter",
        providerLabel: "AgentRouter",
        kindLabel: "Login",
        name: a.name,
        identifier: a.username,
      }),
    );
    for (const s of sites) {
      for (const [i, a] of (buckets.site[s.id] ?? []).entries()) {
        out.push({
          ref: `site:${s.id}:${i}`,
          keyPrefix: s.id,
          providerId: s.id,
          providerLabel: s.label,
          kindLabel: s.label,
          name: a.name,
          identifier: a.user_id,
          tokenMask: maskToken(a.access_token),
        });
      }
    }
    return out;
  }, [buckets, sites]);

  const filtered = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    const list = rows.filter((r) => {
      if (siteFilter !== ALL_SITES && r.providerId !== siteFilter) return false;
      if (typeFilter !== "all" && r.kindLabel !== typeFilter && !(typeFilter === "site" && r.providerId !== "cookie" && r.providerId !== "token" && r.providerId !== "agentrouter")) return false;
      if (!q) return true;
      return r.name.toLowerCase().includes(q) || r.identifier.toLowerCase().includes(q);
    });
    if (!sortBy) return list;
    const bal = (r: AccountRow) => balances[`${r.keyPrefix}:${r.name}`];
    const val = (r: AccountRow) => {
      if (sortBy === "quota") {
        const b = bal(r);
        return b?.success ? (b as QueryResultSuccess).quota - (b as QueryResultSuccess).used : -1;
      }
      if (sortBy === "used") {
        const b = bal(r);
        return b?.success ? (b as QueryResultSuccess).used : -1;
      }
      if (sortBy === "today") {
        const t = computeTodayUsed(bal(r)?.success ? bal(r) : undefined, baselineQ.data?.baseline[`${r.keyPrefix}:${r.name}`]);
        return t ?? -1;
      }
      return r.name.toLowerCase();
    };
    const sorted = [...list].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      const c = typeof av === "string" || typeof bv === "string" ? String(av).localeCompare(String(bv)) : (av as number) - (bv as number);
      return sortOrder === "asc" ? c : -c;
    });
    return sorted;
  }, [rows, siteFilter, typeFilter, debouncedSearch, sortBy, sortOrder, balances, baselineQ.data]);

  const filterOptions = useMemo(
    () => [
      { id: "cookie", label: "Handle Cookie" },
      { id: "token", label: "Handle Token" },
      { id: "agentrouter", label: "AgentRouter" },
      ...sites.map((s) => ({ id: s.id, label: s.label })),
    ],
    [sites],
  );

  function onSort(field: string, initialOrder: "asc" | "desc") {
    if (sortBy === field) setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    else {
      setSortBy(field);
      setSortOrder(initialOrder);
    }
  }

  function onBatchDelete() {
    if (selected.size === 0) return;
    const names = rows.filter((r) => selected.has(r.ref)).map((r) => r.name);
    if (!window.confirm(`删除选中的 ${names.length} 个账号？\n\n${names.slice(0, 8).join("、")}${names.length > 8 ? ` 等 ${names.length} 个` : ""}\n\n此操作不可撤销。`)) return;
    const next: DedupeBuckets = {
      token: buckets.token.filter((_, i) => !selected.has(`token:${i}`)),
      cookie: buckets.cookie.filter((_, i) => !selected.has(`cookie:${i}`)),
      login: buckets.login.filter((_, i) => !selected.has(`login:${i}`)),
      site: copySiteBuckets(),
    };
    for (const s of sites) next.site[s.id] = (buckets.site[s.id] ?? []).filter((_, i) => !selected.has(`site:${s.id}:${i}`));
    const kinds: Kind[] = [];
    if (selected.size > 0) {
      if (["token"].some((t) => selected.has(`${t}:0`) || buckets.token.some((_, i) => selected.has(`${t}:${i}`)))) kinds.push("token");
      if (buckets.cookie.some((_, i) => selected.has(`cookie:${i}`))) kinds.push("cookie");
      if (buckets.login.some((_, i) => selected.has(`login:${i}`))) kinds.push("login");
      if (sites.some((s) => (buckets.site[s.id] ?? []).some((_, i) => selected.has(`site:${s.id}:${i}`)))) kinds.push("site");
    }
    void persist(next, kinds).then(
      () => {
        toast.success(`已删除 ${names.length} 个账号`);
        setSelected(new Set());
      },
      (err) => toast.error(errorMessage(err, "删除失败")),
    );
  }

  function invalidateAll() {
    void queryClient.invalidateQueries({ queryKey: ["accounts"] });
  }

  function copySiteBuckets(): Record<string, SiteAccount[]> {
    const copy: Record<string, SiteAccount[]> = {};
    for (const s of sites) copy[s.id] = [...(buckets.site[s.id] ?? [])];
    return copy;
  }

  async function persist(next: DedupeBuckets, kinds: Kind[]) {
    const jobs: Promise<unknown>[] = [];
    if (kinds.includes("token")) jobs.push(saveTokenAccounts(next.token));
    if (kinds.includes("cookie")) jobs.push(saveCookieAccounts(next.cookie));
    if (kinds.includes("login")) jobs.push(saveLoginAccounts(next.login));
    if (kinds.includes("site")) {
      for (const s of sites) jobs.push(saveSiteAccounts(s.id, next.site[s.id] ?? []));
    }
    await Promise.all(jobs);
    invalidateAll();
  }

  async function onQueryBalances() {
    if (querying) return;
    setQuerying(true);
    const merged: Record<string, QueryResult>[] = [];
    const tasks: Promise<void>[] = [
      queryTokenBalances(buckets.token)
        .then((rs) => rs.forEach((r) => merged.push({ [`provider:${r.name}`]: r })))
        .catch(() => undefined),
      queryCookieBalances(buckets.cookie)
        .then((rs) => rs.forEach((r) => merged.push({ [`provider:${r.name}`]: r })))
        .catch(() => undefined),
      queryLoginBalances()
        .then((rs) => rs.forEach((r) => merged.push({ [`agentrouter:${r.name}`]: r })))
        .catch(() => undefined),
      ...sites.map((s) =>
        querySiteBalances(s.id)
          .then((rs) => rs.forEach((r) => merged.push({ [`${s.id}:${r.name}`]: r })))
          .catch(() => undefined),
      ),
    ];
    await Promise.all(tasks);
    setBalances((prev) => Object.assign({}, prev, ...merged));
    // login-accounts/balances 会顺手写今日用量快照，查询后基线要重新取
    void queryClient.invalidateQueries({ queryKey: ["accounts", "baseline"] });
    setQuerying(false);
    toast.success("余额查询完成");
  }

  function onOpenDedupe() {
    const plan = planDedupe(buckets, sites);
    if (plan.removals.length === 0) {
      toast.info("没有发现重复账号");
      return;
    }
    const detail = plan.removals.map((r) => `· ${r.name}（${r.identifier}）`).join("\n");
    if (!window.confirm(`将删除 ${plan.removals.length} 个重复账号：\n\n${detail}\n\n保留策略：优先保留有效期最长的 cookie。确认删除？`)) return;
    void persist(plan.next, ["token", "cookie", "login", "site"]).then(
      () => toast.success(`已去重：删除 ${plan.removals.length} 个重复账号`),
      (err) => toast.error(errorMessage(err, "去重保存失败")),
    );
  }

  function onSwitchMode(next: string) {
    if (next === "json") setJsonText(serializeAccountsToJson(buckets, sites));
    setMode(next as "visual" | "json");
  }

  async function onApplyJson() {
    if (applyingJson) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(jsonText);
    } catch (err) {
      toast.error(`JSON 解析失败：${errorMessage(err, "格式错误")}`);
      return;
    }
    if (!Array.isArray(parsed)) {
      toast.error("JSON 顶层必须是数组");
      return;
    }
    const { buckets: next, skipped } = classifyJsonAccounts(parsed, sites);
    setApplyingJson(true);
    try {
      await persist(next, ["token", "cookie", "login", "site"]);
      setMode("visual");
      toast.success(`已应用：token ${next.token.length} · cookie ${next.cookie.length} · login ${next.login.length} · 站点 ${Object.values(next.site).reduce((n, a) => n + a.length, 0)}${skipped ? `，忽略无法识别 ${skipped} 条` : ""}`);
    } catch (err) {
      toast.error(errorMessage(err, "应用失败"));
    } finally {
      setApplyingJson(false);
    }
  }

  function onSubmitForm(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    const next: DedupeBuckets = {
      token: [...buckets.token],
      cookie: [...buckets.cookie],
      login: [...buckets.login],
      site: copySiteBuckets(),
    };
    const make: Record<Kind, () => void> = {
      token: () => next.token.push({ name: form.name, access_token: form.accessToken, user_id: form.userId, provider: "provider" }),
      cookie: () => next.cookie.push({ name: form.name, cookies: { session: form.session }, api_user: form.apiUser }),
      login: () => next.login.push({ name: form.name, username: form.username, password: form.password }),
      site: () => (next.site[form.siteId] ??= []).push({ name: form.name, access_token: form.accessToken, user_id: form.userId }),
    };
    make[form.kind]();
    void persist(next, [form.kind]).then(
      () => {
        toast.success(`已${editingRef ? "更新" : "添加"}账号 ${form.name}`);
        setForm(null);
        setEditingRef(null);
      },
      (err) => toast.error(errorMessage(err, "保存失败")),
    );
  }

  function onDeleteRow(row: AccountRow) {
    if (!window.confirm(`删除账号 ${row.name}？此操作不可撤销。`)) return;
    const next: DedupeBuckets = {
      token: [...buckets.token],
      cookie: [...buckets.cookie],
      login: [...buckets.login],
      site: copySiteBuckets(),
    };
    if (row.providerId === "cookie") next.cookie = next.cookie.filter((a) => a.name !== row.name || a.api_user !== row.identifier);
    else if (row.providerId === "token") next.token = next.token.filter((a) => a.name !== row.name || a.user_id !== row.identifier);
    else if (row.providerId === "agentrouter") next.login = next.login.filter((a) => a.name !== row.name);
    else next.site[row.providerId] = (next.site[row.providerId] ?? []).filter((a) => a.name !== row.name);
    const kind: Kind = row.providerId === "cookie" ? "cookie" : row.providerId === "token" ? "token" : row.providerId === "agentrouter" ? "login" : "site";
    void persist(next, [kind]).then(
      () => toast.success(`已删除 ${row.name}`),
      (err) => toast.error(errorMessage(err, "删除失败")),
    );
  }

  if (sitesQ.isError) return <ErrorState message={errorMessage(sitesQ.error, "站点列表加载失败")} onRetry={() => void sitesQ.refetch()} />;

  const queried = Object.values(balances);
  const totalBalance = queried
    .map((r) => (r.success ? r.quota - r.used : 0))
    .reduce((a, b) => a + b, 0);
  const totalToday = rows
    .map((row) => computeTodayUsed(balances[`${row.keyPrefix}:${row.name}`]?.success ? balances[`${row.keyPrefix}:${row.name}`] : undefined, baselineQ.data?.baseline[`${row.keyPrefix}:${row.name}`]))
    .reduce<number>((sum, v) => sum + (v ?? 0), 0);

  return (
    <div className="space-y-5">
      <PageHeader
        title="账号管理"
        description="全部站点账号的聚合视图"
        actions={
          <>
            <Button onClick={() => void onQueryBalances()} disabled={querying}>
              {querying ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
              {querying ? "查询中…" : "查询余额"}
            </Button>
            <Button variant="secondary" onClick={onOpenDedupe}>
              ID 去重
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setForm({ ...emptyForm, siteId: sites[0]?.id ?? "" });
                setEditingRef(null);
              }}
            >
              <Plus className="size-4" aria-hidden="true" />
              添加账号
            </Button>
          </>
        }
      />

      <section className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="总余额" value={formatMoney(totalBalance)} sub={queried.length > 0 ? `已查询 ${queried.filter((r) => r.success).length} 个账号` : "点「查询余额」后显示"} icon={Coins} iconClass="text-site-0" delay={0} />
        <KpiCard label="今日用量" value={formatMoney(totalToday)} sub="相对当日首次快照的增量" icon={Layers} iconClass="text-site-1" delay={60} />
        <KpiCard label="账号总数" value={rows.length} sub={`Token ${buckets.token.length} · Cookie ${buckets.cookie.length} · Login ${buckets.login.length}`} icon={Users} iconClass="text-site-2" delay={120} />
        <KpiCard label="站点数" value={sites.length} sub={sites.length > 0 ? sites.map((s) => s.label).join(" · ") : "未接入站点"} icon={Globe2} iconClass="text-site-3" delay={180} />
      </section>

      {selected.size > 0 ? (
        <div className="flex animate-in fade-in slide-in-from-top-1 items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2 duration-200">
          <p className="text-xs text-muted-foreground">
            已选 <span className="font-medium text-foreground">{selected.size}</span> 个账号
          </p>
          <div className="flex items-center gap-1.5">
            <Button variant="secondary" size="sm" disabled={querying} onClick={() => void onQueryBalances()}>
              {querying ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
              查询选中余额
            </Button>
            <Button size="sm" className="bg-destructive/10 text-destructive hover:bg-destructive/15 hover:text-destructive" onClick={onBatchDelete}>
              <Trash2 className="size-3.5" aria-hidden="true" />
              批量删除
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>取消</Button>
          </div>
        </div>
      ) : null}

      <Tabs value={mode} onValueChange={onSwitchMode}>
        <div className="flex flex-wrap items-center gap-2">
          <TabsList>
            <TabsTrigger value="visual">可视化</TabsTrigger>
            <TabsTrigger value="json">JSON</TabsTrigger>
          </TabsList>
          {mode === "visual" ? (
            <>
              <Select value={siteFilter} onValueChange={setSiteFilter}>
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_SITES}>全部站点（{rows.length}）</SelectItem>
                  {filterOptions.map((o) => (
                    <SelectItem key={o.id} value={o.id}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索账号名 / ID" className="h-8 w-52 pl-8 text-xs" />
              </div>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="secondary" size="sm" className="h-8 gap-1.5 text-xs" aria-label="筛选">
                    <Filter className="size-3.5" aria-hidden="true" />
                    筛选
                    {typeFilter !== "all" ? <span className="rounded-full bg-foreground/15 px-1.5 text-[10px]">{typeFilter}</span> : null}
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-44 p-1.5">
                  <p className="px-2 py-1 text-[11px] font-medium text-muted-foreground">账号类型</p>
                  {[
                    { id: "all", label: "全部" },
                    { id: "Cookie", label: "Cookie" },
                    { id: "Token", label: "Token" },
                    { id: "Login", label: "Login" },
                    { id: "site", label: "站点账号" },
                  ].map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setTypeFilter(t.id)}
                      className={cn(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-accent",
                        typeFilter === t.id && "bg-accent text-foreground",
                      )}
                    >
                      {t.label}
                      {typeFilter === t.id ? <span className="size-1.5 rounded-full bg-foreground" aria-hidden="true" /> : null}
                    </button>
                  ))}
                </PopoverContent>
              </Popover>
            </>
          ) : null}
        </div>

        <TabsContent value="visual" className="mt-3">
          <div className="overflow-hidden rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-10">
                    <Checkbox
                      checked={filtered.length > 0 && filtered.every((r) => selected.has(r.ref))}
                      onCheckedChange={(checked) => {
                        if (checked === true) setSelected(new Set(filtered.map((r) => r.ref)));
                        else setSelected(new Set());
                      }}
                      aria-label="全选本页"
                    />
                  </TableHead>
                  <TableHead>站点</TableHead>
                  <TableHead>账号</TableHead>
                  <TableHead>类型</TableHead>
                  <SortableTableHead field="quota" sortBy={sortBy ?? ""} sortOrder={sortOrder} align="right" onSort={onSort}>余额</SortableTableHead>
                  <SortableTableHead field="used" sortBy={sortBy ?? ""} sortOrder={sortOrder} align="right" onSort={onSort}>已用</SortableTableHead>
                  <SortableTableHead field="today" sortBy={sortBy ?? ""} sortOrder={sortOrder} align="right" onSort={onSort}>今日</SortableTableHead>
                  <TableHead>Cookie 有效期</TableHead>
                  <TableHead className="w-20 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((row, i) => {
                  const result = balances[`${row.keyPrefix}:${row.name}`];
                  const today = computeTodayUsed(result?.success ? result : undefined, baselineQ.data?.baseline[`${row.keyPrefix}:${row.name}`]);
                  return (
                    <TableRow
                      key={row.ref}
                      className={cn("animate-in fade-in slide-in-from-bottom-1 fill-mode-both duration-300", selected.has(row.ref) && "bg-accent/40")}
                      style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}
                    >
                      <TableCell className="w-10">
                        <Checkbox
                          checked={selected.has(row.ref)}
                          onCheckedChange={(c) => {
                            const next = new Set(selected);
                            if (c === true) next.add(row.ref);
                            else next.delete(row.ref);
                            setSelected(next);
                          }}
                          aria-label={`选择 ${row.name}`}
                        />
                      </TableCell>
                      <TableCell>
                        <span className="flex items-center gap-1.5 text-xs">
                          <span className={siteDotClass(row.providerId)} aria-hidden="true" />
                          {row.providerLabel}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="text-xs font-medium">{row.name}</div>
                        <div className="font-data text-[11px] text-muted-foreground">{row.identifier}</div>
                        {row.tokenMask ? <div className="font-data text-[11px] text-muted-foreground">{row.tokenMask}</div> : null}
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="text-[11px]">
                          {row.kindLabel}
                        </Badge>
                      </TableCell>
                      <TableCell className={cn("text-right font-data text-xs", result?.success === false && "text-checkin-failed")}>
                        {result ? (result.success ? formatMoney(result.quota - result.used) : "失败") : "--"}
                      </TableCell>
                      <TableCell className="text-right font-data text-xs">{result?.success ? formatMoney(result.used) : "--"}</TableCell>
                      <TableCell className="text-right font-data text-xs">{today === null ? "--" : formatMoney(today)}</TableCell>
                      <TableCell className="text-xs">
                        {row.cookieExpiry ? <span className={expiryTextClass[row.cookieExpiry.level]}>{row.cookieExpiry.label}</span> : "--"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon" className="size-7" onClick={() => onDeleteRow(row)} aria-label={`删除 ${row.name}`}>
                          <Trash2 className="size-3.5" aria-hidden="true" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            {tokenQ.isLoading || cookieQ.isLoading || loginQ.isLoading || siteAccountsQ.isLoading ? (
              <LoadingState className="min-h-32" />
            ) : filtered.length === 0 ? (
              <EmptyState
                message={rows.length === 0 ? "还没有账号" : "没有匹配的账号"}
                action={
                  rows.length === 0 ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        setForm({ ...emptyForm, siteId: sites[0]?.id ?? "" });
                        setEditingRef(null);
                      }}
                    >
                      <Plus className="size-3.5" aria-hidden="true" />
                      添加账号
                    </Button>
                  ) : undefined
                }
              />
            ) : null}
          </div>
        </TabsContent>

        <TabsContent value="json" className="mt-3 space-y-2">
          <p className="text-xs text-muted-foreground">
            按字段特征分流：有 access_token 且 provider 命中站点 → 该站点；有 access_token → Token；有 username+password → Login；其余 → Cookie。站点被删后其账号落回 Token 桶，不会丢失。
          </p>
          <Textarea value={jsonText} onChange={(e) => setJsonText(e.target.value)} rows={16} className="font-data text-xs" spellCheck={false} />
          <div className="flex items-center gap-2">
            <Button onClick={() => void onApplyJson()} disabled={applyingJson}>
              {applyingJson ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Sparkles className="size-4" aria-hidden="true" />}
              应用 JSON
            </Button>
            <Button variant="secondary" onClick={() => setJsonText(exampleAccountsJson)}>
              加载示例
            </Button>
          </div>
        </TabsContent>
      </Tabs>

      {form ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true">
          <form onSubmit={onSubmitForm} className="w-full max-w-md animate-in fade-in zoom-in-95 space-y-4 rounded-lg border bg-card p-5">
            <h2 className="text-sm font-medium">{editingRef ? "编辑账号" : "添加账号"}</h2>
            <Tabs
              value={form.kind}
              onValueChange={(v) => {
                if (!form) return;
                setForm({ ...form, kind: v as Kind, siteId: v === "site" ? (form.siteId || sites[0]?.id || "") : form.siteId });
              }}
            >
              <TabsList className="w-full">
                <TabsTrigger value="token" className="flex-1">Token</TabsTrigger>
                <TabsTrigger value="cookie" className="flex-1">Cookie</TabsTrigger>
                <TabsTrigger value="login" className="flex-1">Login</TabsTrigger>
                <TabsTrigger value="site" className="flex-1" disabled={sites.length === 0}>站点</TabsTrigger>
              </TabsList>
            </Tabs>

            {form.kind === "site" ? (
              <div className="space-y-1.5">
                <Label>站点</Label>
                <Select value={form.siteId} onValueChange={(v) => setForm({ ...form, siteId: v })}>
                  <SelectTrigger className="w-full"><SelectValue placeholder="选择站点" /></SelectTrigger>
                  <SelectContent>
                    {sites.map((s) => (
                      <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}

            <div className="space-y-1.5">
              <Label htmlFor="acc-name">账号名称</Label>
              <Input id="acc-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="text-xs" required />
            </div>

            {form.kind === "token" || form.kind === "site" ? (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="acc-token">access_token</Label>
                  <Input id="acc-token" value={form.accessToken} onChange={(e) => setForm({ ...form, accessToken: e.target.value })} className="font-data text-xs" required />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="acc-uid">user_id</Label>
                  <Input id="acc-uid" value={form.userId} onChange={(e) => setForm({ ...form, userId: e.target.value })} className="font-data text-xs" required />
                </div>
              </>
            ) : null}
            {form.kind === "cookie" ? (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="acc-session">session cookie</Label>
                  <Textarea id="acc-session" value={form.session} onChange={(e) => setForm({ ...form, session: e.target.value })} rows={3} className="font-data text-xs" required />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="acc-apiuser">api_user</Label>
                  <Input id="acc-apiuser" value={form.apiUser} onChange={(e) => setForm({ ...form, apiUser: e.target.value })} className="font-data text-xs" required />
                </div>
              </>
            ) : null}
            {form.kind === "login" ? (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="acc-username">用户名</Label>
                  <Input id="acc-username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} className="text-xs" required />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="acc-password">密码</Label>
                  <Input id="acc-password" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} className="text-xs" required />
                </div>
              </>
            ) : null}

            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="ghost" onClick={() => setForm(null)}>取消</Button>
              <Button type="submit">保存</Button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

export type { CookieAccount, LoginAccount, TokenAccount };
