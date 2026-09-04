/**
 * ID 去重——`_ref` 本身的解析/构造已经收敛到 `@/shared/lib/account-ref`（账号表格 / 密钥管理
 * / 编辑表单三处共享），这里只放去重专属的逻辑：分组键、展示用的类型名/标识、去重方案计算。
 *
 * 对照 docs/frontend-contract.md 「三、ID 去重」与 templates/index.html 的
 * _acctKey/_acctTypeLabel/_acctId(:1079/:1085/:1095)、dedupeAccounts(:1100) 移植。
 */

import type { AccountRef } from "@/shared/lib/account-ref";
import { formatAccountRef } from "@/shared/lib/account-ref";
import type { Account, CookieAccount, LoginAccount, NewapiSite, SiteAccount, TokenAccount } from "@/types";

import { sessionDaysLeft } from "@/features/accounts/account-format";

export function accountTypeLabel(type: AccountRef["type"], siteId: string | undefined, sites: readonly NewapiSite[]): string {
  if (type === "token") return "Token";
  if (type === "cookie") return "Cookie";
  if (type === "login") return "Login";
  return sites.find((site) => site.id === siteId)?.label ?? siteId ?? "站点";
}

/**
 * 去重分组键：同站点同 user_id 判重；token/cookie 共享 `anyrouter:` 命名空间
 * （二者指向同一个上游账号体系）；login 按 username（大小写不敏感）；
 * 站点账号按 `site_id:user_id`（不同站点 id 天然不冲突）。
 */
export function dedupeKey(type: AccountRef["type"], account: Account, siteId?: string): string {
  if (type === "token") return `${(account as TokenAccount).provider || "anyrouter"}:${(account as TokenAccount).user_id}`;
  if (type === "cookie") return `anyrouter:${(account as CookieAccount).api_user}`;
  if (type === "login") return `agentrouter:${(account as LoginAccount).username.toLowerCase()}`;
  return `${siteId}:${(account as SiteAccount).user_id}`;
}

/** 去重结果列表里展示给用户看的「ID」：cookie 用 api_user，login 用 username，其余用 user_id。 */
export function dedupeIdentifier(type: AccountRef["type"], account: Account): string {
  if (type === "cookie") return (account as CookieAccount).api_user;
  if (type === "login") return (account as LoginAccount).username;
  return (account as TokenAccount | SiteAccount).user_id;
}

interface DedupeEntry {
  ref: AccountRef;
  account: Account;
  key: string;
}

export interface DedupeRemoval {
  ref: AccountRef;
  name: string;
  identifier: string;
}

export interface DedupeBuckets {
  token: TokenAccount[];
  cookie: CookieAccount[];
  login: LoginAccount[];
  /** site id -> 该站点的账号列表 */
  site: Record<string, SiteAccount[]>;
}

export interface DedupePlan {
  removals: DedupeRemoval[];
  /** 去重后各桶的新数组；调用方只需要对「长度变化了」的桶落盘，其余不动 */
  next: DedupeBuckets;
}

/**
 * 计算去重方案（纯函数，不落盘）。重复时优先保留有效期最长的 cookie，
 * 否则保留分组内第一个——与旧前端 dedupeAccounts() 的策略一致。
 */
export function planDedupe(buckets: DedupeBuckets, sites: readonly NewapiSite[]): DedupePlan {
  const entries: DedupeEntry[] = [];
  buckets.token.forEach((account, index) => entries.push({ ref: { type: "token", index }, account, key: dedupeKey("token", account) }));
  buckets.cookie.forEach((account, index) => entries.push({ ref: { type: "cookie", index }, account, key: dedupeKey("cookie", account) }));
  buckets.login.forEach((account, index) => entries.push({ ref: { type: "login", index }, account, key: dedupeKey("login", account) }));
  for (const site of sites) {
    (buckets.site[site.id] ?? []).forEach((account, index) =>
      entries.push({ ref: { type: "site", siteId: site.id, index }, account, key: dedupeKey("site", account, site.id) }),
    );
  }

  const groups = new Map<string, DedupeEntry[]>();
  for (const entry of entries) {
    const existing = groups.get(entry.key);
    if (existing) existing.push(entry);
    else groups.set(entry.key, [entry]);
  }

  const removeRefs = new Set<string>();
  const removals: DedupeRemoval[] = [];
  for (const group of groups.values()) {
    if (group.length <= 1) continue;
    const cookies = group.filter((entry) => entry.ref.type === "cookie");
    const keep =
      cookies.length > 0
        ? cookies.reduce((best, entry) => {
            const bestDays = sessionDaysLeft((best.account as CookieAccount).cookies?.session) ?? -1;
            const entryDays = sessionDaysLeft((entry.account as CookieAccount).cookies?.session) ?? -1;
            return entryDays > bestDays ? entry : best;
          })
        : group[0];
    for (const entry of group) {
      if (entry === keep) continue;
      removeRefs.add(formatAccountRef(entry.ref));
      removals.push({ ref: entry.ref, name: entry.account.name, identifier: dedupeIdentifier(entry.ref.type, entry.account) });
    }
  }

  const kept = (ref: AccountRef) => !removeRefs.has(formatAccountRef(ref));
  const nextSite: Record<string, SiteAccount[]> = {};
  for (const site of sites) {
    nextSite[site.id] = (buckets.site[site.id] ?? []).filter((_, index) => kept({ type: "site", siteId: site.id, index }));
  }

  return {
    removals,
    next: {
      token: buckets.token.filter((_, index) => kept({ type: "token", index })),
      cookie: buckets.cookie.filter((_, index) => kept({ type: "cookie", index })),
      login: buckets.login.filter((_, index) => kept({ type: "login", index })),
      site: nextSite,
    },
  };
}
