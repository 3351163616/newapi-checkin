/**
 * 全项目类型的单一事实来源，逐项对照 balance_server.py 的真实响应整理，
 * 不是从 OpenAPI/pydantic 自动生成——后端多数端点直接返回字面量 dict，没有 schema。
 *
 * 字段名与后端 JSON 保持一致的 snake_case（不做驼峰转换），因为 shared/api/client.ts
 * 是不做字段映射的薄封装：TS 类型描述的就是网络上跑的原始形状。
 *
 * 所有类型都是「剥离 `{success:...}` 信封之后」的形状，即 apiGet/apiPost 的返回值类型。
 * 例外是 MonitorStatus——它对应的端点本来就不带 success 信封。
 */

// ─────────────────────────────────────────────────────────────────────────
// 账号：四种子类型，与 `_ref` 协议的 token/cookie/login/site 一一对应
// （寻址协议本身见 shared/lib/account-ref.ts）
// ─────────────────────────────────────────────────────────────────────────

/** AnyRouter session cookie 账号（GET/POST /api/config 的 accounts 字段） */
export interface CookieAccount {
  name: string;
  /** 实际只读写 `session` 一个键，但源结构是任意字典，原样保留 */
  cookies: Record<string, string>;
  api_user: string;
}

/** AnyRouter access_token 账号（GET/POST /api/token/accounts） */
export interface TokenAccount {
  name: string;
  access_token: string;
  user_id: string;
  /**
   * 默认 'anyrouter'。可视化↔JSON 双模式里，若这个值命中某个已注册站点 id，
   * 该条目在 JSON 视图会被前端归入那个站点而非 AnyRouter 桶（见 frontend-contract.md 「关键交互行为」）。
   */
  provider: string;
}

/** AgentRouter 账号密码账号（GET/POST /api/login-accounts/accounts） */
export interface LoginAccount {
  name: string;
  username: string;
  password: string;
}

/** 通用 new-api 站点账号（GET/POST /api/site/{id}/accounts） */
export interface SiteAccount {
  name: string;
  access_token: string;
  user_id: string;
}

export type Account = CookieAccount | TokenAccount | LoginAccount | SiteAccount;

/** GET 账号列表类端点与其对应 POST 保存端点共享的 `{accounts:[...]}` 信封内容 */
export interface AccountsListPayload<T> {
  accounts: T[];
}

// ─────────────────────────────────────────────────────────────────────────
// 站点注册表：GET/POST /api/sites、POST /api/sites/probe
// ─────────────────────────────────────────────────────────────────────────

/** 一个 new-api 同构站点的完整配置（model_dump() 总是补全全部字段） */
export interface NewapiSite {
  /** 决定接口路径 /api/site/{id}/... 与数据文件名，创建后不应再改 */
  id: string;
  label: string;
  domain: string;
  /**
   * 遗留字段：旧前端用它取 Tailwind 主题色。本次重构改用哈希得来的
   * --site-N token（见 shared/lib/site-color.ts），仅为与后端往返一致而保留。
   */
  accent: string;
  user_info_path: string;
  sign_in_path: string;
  status_path: string;
  api_user_key: string;
  quota_per_unit: number;
  concurrency: number;
  auto_checkin: boolean;
  accounts_file: string;
  state_file: string;
  /** 三态健康状态：ok（有可用账号）/ invalid（全部失败）/ unknown（无账号或未检查） */
  status?: { status: "ok" | "invalid" | "unknown"; error?: string; checked_at?: number };
}

/** POST /api/sites 的写入体：只有 id/label/domain 是 pydantic 必填，其余字段有默认值 */
export type NewapiSiteInput = Pick<NewapiSite, "id" | "label" | "domain"> & Partial<Omit<NewapiSite, "id" | "label" | "domain">>;

export interface SitesResponse {
  sites: NewapiSite[];
  collect_key_ready?: boolean;
  /** 仅 GET /api/sites?with_counts=1 返回：站点 id → 账号数 */
  counts?: Record<string, number>;
}

/** POST /api/sites/probe 探测结果 */
export interface SiteProbeInfo {
  version: string;
  system_name: string;
  checkin_enabled: boolean;
  turnstile_check: boolean;
  quota_per_unit: number;
  /** 站点首页的网络层防护（裸探测，保守分类；运行期撞到会自动过验） */
  protections?: {
    cf_challenge: boolean;
    aliyun_waf: boolean;
  };
}

export interface SiteProbeResponse {
  info: SiteProbeInfo;
}

/** GET /api/site/{id}/turnstile */
export interface TurnstileStatus {
  enabled: boolean;
  site_key: string;
  probed: boolean;
}

export interface SiteTurnstileResponse {
  turnstile: TurnstileStatus;
}

// ─────────────────────────────────────────────────────────────────────────
// Turnstile 打码平台：GET/POST /api/turnstile/solver、POST /api/turnstile/solver/test
// ─────────────────────────────────────────────────────────────────────────

/** GET /api/turnstile/solver —— api_key 只回是否已配置，后端绝不回显 */
export interface TurnstileSolverStatus {
  provider: string;
  base_url: string;
  configured: boolean;
  /** FlareSolverr 地址（过 Cloudflare 边缘质询用），可为空 */
  flaresolverr_url: string;
  /** 各预设平台的默认 API 域名（custom 平台不在此列） */
  presets: Record<string, string>;
}

export interface TurnstileSolverSaveResponse {
  success: boolean;
  message?: string;
  error?: string;
}

export interface TurnstileSolverTestResponse {
  success: boolean;
  /** false 表示没真正消耗打码（如站点未开 Turnstile，无需测） */
  tested: boolean;
  site?: string;
  /** 求解耗时（秒） */
  elapsed?: number;
  token_preview?: string;
  message?: string;
  error?: string;
}

/** POST /api/protection/test —— 站点防护层探测；solved 里 null = 撞到了但没配求解器 */
export interface ProtectionTestResponse {
  success: boolean;
  site: string;
  domain: string;
  page_http_status: number | null;
  page_error?: string;
  protections: { cf_challenge: boolean; aliyun_waf: boolean; turnstile: boolean };
  solved: { aliyun_waf?: boolean; cf_challenge?: boolean | null };
  flaresolverr_configured: boolean;
  error?: string;
}

// ─────────────────────────────────────────────────────────────────────────
// 保存的配置：GET/POST /api/config —— 后端不做 schema 校验，形状由前端约定
// ─────────────────────────────────────────────────────────────────────────

/** 不含密码，可安全落盘；SMTP 密码只在 /api/monitor/start 请求体里临时携带 */
export interface SavedEmailConfig {
  smtp_server: string;
  smtp_port: number;
  email_user: string;
  email_to: string;
}

export interface SavedMonitorPreferences {
  interval: number;
  threshold: number;
}

/** 前端固定读写这个形状；后端 POST /api/config 接受任意 dict，不做校验 */
export interface SavedConfig {
  accounts: CookieAccount[];
  email: SavedEmailConfig;
  monitor: SavedMonitorPreferences;
}

export interface SavedConfigResponse {
  data: SavedConfig | null;
}

// ─────────────────────────────────────────────────────────────────────────
// 余额查询：POST /api/query、/api/token/query、/api/site/{id}/query、
// GET /api/login-accounts/balances —— 四个端点响应形状完全一致
// ─────────────────────────────────────────────────────────────────────────

/** 上游请求被拦截的三种归类，见 anyrouter_block_reason() / agentrouter_block_reason() */
export type BlockedReason = "ratelimit" | "http" | "challenge";

export interface QueryResultSuccess {
  name: string;
  success: true;
  quota: number;
  used: number;
  username: string;
}

export interface QueryResultFailure {
  name: string;
  success: false;
  error: string;
  blocked?: BlockedReason;
}

export type QueryResult = QueryResultSuccess | QueryResultFailure;

export interface QuerySummary {
  total_quota: number;
  total_used: number;
  account_count: number;
  success_count: number;
}

export interface QueryResponse {
  results: QueryResult[];
  summary: QuerySummary;
}

// ─────────────────────────────────────────────────────────────────────────
// 签到：批量签到结果（/api/checkin、/api/token/checkin）
// ─────────────────────────────────────────────────────────────────────────

export interface CheckinResultSuccess {
  name: string;
  success: true;
  message: string;
  already_signed: boolean;
}

export interface CheckinResultFailure {
  name: string;
  success: false;
  message: string;
  blocked?: BlockedReason;
}

export type CheckinResult = CheckinResultSuccess | CheckinResultFailure;

export interface CheckinSummary {
  total: number;
  success: number;
  new_signed: number;
}

export interface CheckinResponse {
  results: CheckinResult[];
  summary: CheckinSummary;
}

// ─────────────────────────────────────────────────────────────────────────
// 签到调度状态：三条独立状态机（AnyRouter cookie / AgentRouter / 各 new-api 站点）
// 共用同一套 _checkin_status_payload 形状，字段并集见下，用可选字段区分差异来源
// ─────────────────────────────────────────────────────────────────────────

export type CheckinAccountRunStatus = "pending" | "signed" | "already" | "failed";

export interface CheckinAccountStatus {
  name: string;
  status: CheckinAccountRunStatus;
  message: string;
  time: string | null;
  /** 仅 AgentRouter（login-accounts）的状态携带，登录响应顺带取到的余额 */
  quota?: number | null;
  used?: number | null;
}

export interface CheckinLogEntry {
  time: string;
  message: string;
}

export type CheckinTrigger = "manual" | "auto" | "fast" | "browser" | null;

export interface CheckinState {
  /** 仅通用 new-api 站点的状态携带 */
  site_id?: string;
  running: boolean;
  date: string | null;
  trigger: CheckinTrigger;
  /** 仅 AgentRouter（login-accounts）携带：一键全签 fast / 缓慢模式 slow，默认 slow */
  mode?: "fast" | "slow";
  started_at: string | null;
  finished_at: string | null;
  total: number;
  done: number;
  signed: number;
  failed: number;
  /** 仅 AgentRouter 缓慢模式携带：当前正在签到的账号名 */
  current?: string | null;
  /** 仅 AgentRouter 缓慢模式携带：下一个账号预计签到时间 */
  next_at?: string | null;
  accounts: CheckinAccountStatus[];
  logs: CheckinLogEntry[];
}

export interface CheckinStatusResponse {
  status: CheckinState;
}

export interface CheckinStartResponse {
  message: string;
  status: CheckinState;
}

/** POST /api/login-accounts/checkin/fast */
export interface CheckinFastSummary {
  total: number;
  new_signed: number;
  already: number;
  failed: number;
  aborted: boolean;
}

export interface CheckinFastResponse {
  summary: CheckinFastSummary;
  status: CheckinState;
}

/** GET /api/site/{id}/checkin/info —— 只读签到状态与奖励区间，不触发签到 */
export interface CheckinInfoAccountSuccess {
  name: string;
  success: true;
  enabled: boolean | null;
  min_reward: number;
  max_reward: number;
  checked_in_today: boolean | null;
  total_checkins: number | null;
  total_reward: number;
}

export interface CheckinInfoAccountFailure {
  name: string;
  success: false;
  error: string;
}

export type CheckinInfoAccount = CheckinInfoAccountSuccess | CheckinInfoAccountFailure;

export interface SiteCheckinInfoResponse {
  accounts: CheckinInfoAccount[];
}

/** POST /api/site/{id}/checkin/sync —— 浏览器脚本跑完后核对真实签到状态 */
export interface SiteSyncResult {
  name: string;
  /**
   * true = 核对到「今日已签到」。false 有两种可能：今日确实未签到，或状态查询本身失败——
   * 用 `total_checkins` 是否存在来区分（查询失败时不会有这个字段）。
   */
  success: boolean;
  message: string;
  already_signed?: boolean;
  total_checkins?: number;
  quota?: number | null;
  used?: number | null;
}

export interface SiteSyncResponse {
  results: SiteSyncResult[];
  checked_in: number;
  total: number;
  status: CheckinState;
}

// ─────────────────────────────────────────────────────────────────────────
// 自动签到开关：GET/POST /api/checkin/settings
// ─────────────────────────────────────────────────────────────────────────

export interface CheckinSettings {
  anyrouter_auto: boolean;
  agentrouter_auto: boolean;
  /** 缓慢签到模式：账号间隔下限/上限（分钟） */
  agentrouter_gap_min: number;
  agentrouter_gap_max: number;
  /** 每个 new-api 站点会合成一个 `<site_id>_auto` 开关，键名动态 */
  [siteAutoKey: string]: boolean | number;
}

export interface CheckinSettingsResponse {
  settings: CheckinSettings;
}

// ─────────────────────────────────────────────────────────────────────────
// AnyRouter cookie 续期与状态：POST /api/anyrouter/renew、GET .../cookie-status
// ─────────────────────────────────────────────────────────────────────────

export interface RenewResultSuccess {
  name: string;
  success: true;
  message: string;
  expires_at: string | null;
  days_left: number | null;
}

export interface RenewResultFailure {
  name: string;
  success: false;
  message: string;
  blocked?: BlockedReason;
  /** 站点限流触发后，本轮中止时批量跳过的账号会带这个标记 */
  skipped?: true;
}

export type RenewResult = RenewResultSuccess | RenewResultFailure;

export interface RenewSummary {
  total: number;
  renewed: number;
  failed: number;
  skipped: number;
}

export interface RenewResponse {
  results: RenewResult[];
  summary: RenewSummary;
  /** 仅撞上站点限流中止时出现 */
  notice?: string;
}

/** GET /api/anyrouter/cookie-status（⛔ 现状缺口：前端目前改本地解码算剩余天数，未接这个端点） */
export interface CookieStatusAccount {
  name: string;
  api_user: string;
  expires_at: string | null;
  days_left: number | null;
}

export interface CookieStatusResponse {
  accounts: CookieStatusAccount[];
}

// ─────────────────────────────────────────────────────────────────────────
// 密钥管理：POST /api/keys/list、/api/keys/create、/api/keys/delete
// ─────────────────────────────────────────────────────────────────────────

/** 一个 new-api token 的展示字段（额度已按站点 quota_per_unit 换算成美元） */
export interface ApiKey {
  id: number;
  name: string;
  key: string;
  /** true 表示 key 仍是脱敏值（形如 `sk-xxx***xxx`），需要看 warning 了解取全量失败的原因 */
  masked: boolean;
  /** new-api 的 token 状态码（1 启用等，具体枚举以站点自身前端为准，这里不强行编码） */
  status: number;
  unlimited_quota: boolean;
  remain_quota: number;
  used_quota: number;
  expired_time: number | null;
  created_time: number | null;
  accessed_time: number | null;
  group: string;
  model_limits_enabled: boolean;
  model_limits: string;
  allow_ips: string;
}

interface KeyAccountKeysBase {
  ref: string;
  name: string;
  /** 展示用标签：'AnyRouter' / 'AgentRouter' / 站点的 label，不是机器可比较的 id */
  provider: string;
}

export interface KeyAccountKeysSuccess extends KeyAccountKeysBase {
  success: true;
  keys: ApiKey[];
  total: number;
  truncated: boolean;
  /** 取全量密钥时限流等原因导致部分仍脱敏，会带这条提示；键可能整个不存在（见类型注释） */
  warning?: string | null;
  cached?: boolean;
  /** 命中缓存时的写入时刻（`time.time()` 秒级时间戳） */
  cached_at?: number;
}

export interface KeyAccountKeysFailure extends KeyAccountKeysBase {
  success: false;
  error: string;
}

export type KeyAccountKeys = KeyAccountKeysSuccess | KeyAccountKeysFailure;

export interface KeysListRequest {
  /** `_ref` 协议字符串数组，见 shared/lib/account-ref.ts */
  refs: string[];
  /** 绕过列表缓存强制重查；密钥很少变，默认走缓存 */
  refresh?: boolean;
}

export interface KeysListResponse {
  accounts: KeyAccountKeys[];
}

export interface CreateKeyRequest {
  ref: string;
  name: string;
  /** 默认 true（不限额度）；为 false 时 remain_quota 才生效 */
  unlimited_quota?: boolean;
  /** 美元；后端会按账号所属站点的 quota_per_unit 换算成上游原始额度 */
  remain_quota?: number;
  /** unix 秒；-1 表示永不过期 */
  expired_time?: number;
  group?: string;
}

export interface DeleteKeyRequest {
  ref: string;
  id: number;
}

/** /api/keys/create、/api/keys/delete 成功后都会返回该账号刷新后的完整密钥列表 */
export interface KeyMutationResponse {
  account: KeyAccountKeys;
}

// ─────────────────────────────────────────────────────────────────────────
// 监控：POST /api/monitor/start、/stop，GET /api/monitor/status
// ⚠️ MonitorStatus 对应的端点是全项目唯一不带 `{success:...}` 外层包装的响应
// ─────────────────────────────────────────────────────────────────────────

export interface MonitorEmailConfig extends SavedEmailConfig {
  /** SMTP 密码，只在这个请求体里临时携带，不会被 /api/config 持久化 */
  email_pass: string;
}

export interface MonitorStartRequest {
  accounts: CookieAccount[];
  email: MonitorEmailConfig;
  /** 默认 6 */
  interval_hours?: number;
  /** 默认 10（美元） */
  threshold?: number;
}

export interface MonitorConfig {
  interval_hours: number;
  threshold: number;
  account_count: number;
  email_to: string;
}

export interface MonitorLogEntry {
  time: string;
  message: string;
}

/** GET /api/monitor/status 的原始响应——没有 success 信封，apiGet 会原样透传整个 payload */
export interface MonitorStatus {
  running: boolean;
  config: MonitorConfig | null;
  last_check: string | null;
  next_check: string | null;
  alerted_accounts: string[];
  logs: MonitorLogEntry[];
}

// ─────────────────────────────────────────────────────────────────────────
// 用量统计：GET /api/usage/today、/api/usage/history
// ─────────────────────────────────────────────────────────────────────────

export interface UsageEntry {
  /** 当前已用量（美元） */
  used: number;
  /** 当前总额度（美元） */
  quota: number;
  /** 当天第一次记录到的已用量，即今日用量的基线，写入后当天不再变动 */
  used0: number;
}

/** 一天的用量快照：key 是 `usage_key(provider, name)` 拼出的 "provider:账号名" */
export type UsageDayMap = Record<string, UsageEntry>;

/** GET /api/usage/today —— 今日用量 = 前端拿到的实时 quota/used 减去这里的基线 */
export interface UsageBaseline {
  date: string;
  /** key 同 UsageDayMap，value 是当天基线 used0 */
  baseline: Record<string, number>;
}

/** GET /api/usage/history —— 最近 30 天，key 是 YYYY-MM-DD */
export interface UsageHistory {
  history: Record<string, UsageDayMap>;
}
