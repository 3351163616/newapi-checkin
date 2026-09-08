/**
 * `_ref` 寻址协议：账号卡 / 密钥管理 / 编辑表单三处共享的账号定位方式，
 * 用「类型 + 类型内下标」而不是全局下标，避免删站点/删账号后引用错位。
 *
 * 格式（frontend-contract.md 「二、_ref 寻址协议」）：
 *   token:<i>            AnyRouter access_token 账号（对应 GET/POST /api/token/accounts）
 *   cookie:<i>           AnyRouter session cookie 账号（对应 GET/POST /api/config 的 accounts）
 *   login:<i>            AgentRouter 账号密码账号（对应 GET/POST /api/login-accounts/accounts）
 *   site:<site_id>:<i>   通用 new-api 站点账号（对应 GET/POST /api/site/{id}/accounts）
 *
 * 必须与后端 `resolve_key_ctx()`（balance_server.py:3408）严格一致——这是两处独立实现，
 * 全靠约定对齐，不共享代码。解析规则逐条对照该函数的 `_idx()` 与四个分支：
 * - 只看 `:` 分隔后的前若干段，下标必须是能被当整数解析的十进制字符串，多余的尾部分段忽略
 * - 这里只负责解析"形状"是否合法，不做下标越界检查——那需要当前账号数组的长度，
 *   由调用方（拿到 AccountRef 后去查对应数组）自行判断 `index` 是否在 `[0, array.length)` 内，
 *   和后端在 `_idx()` 之外另做 `idx < 0 or idx >= len(accounts)` 检查的分工完全一样
 */

export type AccountRef =
  | { type: "token"; index: number }
  | { type: "cookie"; index: number }
  | { type: "login"; index: number }
  | { type: "site"; siteId: string; index: number };

/**
 * 校验并解析一段下标：必须是可选正负号 + 至少一位数字，对应 Python `int()` 能接受的
 * 常规写法。这些 ref 字符串永远是本模块自己拼出来的（见下面四个构造函数），不是用户手打，
 * 所以不追求覆盖 Python `int()` 的全部怪癖（千位分隔符、首尾空白等），只保证格式不对时
 * 安全返回 `null` 而不是抛异常或悄悄产出 `NaN`。
 */
function parseIndexSegment(segment: string | undefined): number | null {
  if (segment === undefined || !/^[+-]?\d+$/.test(segment)) return null;
  return Number.parseInt(segment, 10);
}

/** 把 `_ref` 字符串解析成结构化形式；格式不合法（未知类型、下标缺失或非数字）返回 `null`。 */
export function parseAccountRef(ref: string): AccountRef | null {
  const parts = ref.split(":");
  const kind = parts[0] ?? "";

  if (kind === "token") {
    const index = parseIndexSegment(parts[1]);
    return index === null ? null : { type: "token", index };
  }
  if (kind === "cookie") {
    const index = parseIndexSegment(parts[1]);
    return index === null ? null : { type: "cookie", index };
  }
  if (kind === "login") {
    const index = parseIndexSegment(parts[1]);
    return index === null ? null : { type: "login", index };
  }
  if (kind === "site") {
    if (parts.length < 3) return null;
    const siteId = parts[1];
    const index = parseIndexSegment(parts[2]);
    return !siteId || index === null ? null : { type: "site", siteId, index };
  }
  return null;
}

export function tokenRef(index: number): string {
  return `token:${index}`;
}

export function cookieRef(index: number): string {
  return `cookie:${index}`;
}

export function loginRef(index: number): string {
  return `login:${index}`;
}

export function siteRef(siteId: string, index: number): string {
  return `site:${siteId}:${index}`;
}

/** `parseAccountRef` 的逆运算，把结构化形式还原成 `_ref` 字符串。 */
export function formatAccountRef(ref: AccountRef): string {
  switch (ref.type) {
    case "token":
      return tokenRef(ref.index);
    case "cookie":
      return cookieRef(ref.index);
    case "login":
      return loginRef(ref.index);
    case "site":
      return siteRef(ref.siteId, ref.index);
  }
}

/**
 * `_ref` → 合法 DOM id：把 `:` 换成 `_`。
 * `:` 本身是合法的 id 字符，但会被 `querySelector('#' + id)` 当成 CSS 伪类分隔符解析，
 * 不转义就会炸——账号卡 / 密钥面板按 `_ref` 生成元素 id 后经常要用选择器而不是
 * `getElementById` 去定位，所以统一转成 `_` 更省事，也是旧实现的做法（frontend-contract.md）。
 */
export function cssId(ref: string): string {
  return ref.replaceAll(":", "_");
}
