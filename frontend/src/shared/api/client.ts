/**
 * 极薄的 fetch 封装：统一注入鉴权、剥离后端的 `{success:...}` 信封、把失败转换成带 message 的 Error。
 *
 * 基址固定为相对路径 `/api`：开发期由 Vite dev server 代理到 127.0.0.1:8003（见 vite.config.ts），
 * 生产期由 balance_server.py 同源托管静态产物，两种场景都不需要绝对地址。调用方传入的 path
 * 不带 `/api` 前缀，例如 `apiGet("/check-auth")`、`apiPost("/site/" + id + "/query")`。
 */

const API_BASE = "/api";

/** 与后端约定的 localStorage 键名，不能改。 */
export const AUTH_TOKEN_STORAGE_KEY = "auth_token";

export function getAuthToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}

const unauthorizedListeners = new Set<() => void>();

/**
 * 订阅"请求收到 401（未登录/登录已过期）"事件，返回取消订阅函数。
 *
 * 特意不从这里 import shared/auth 的任何东西——client 不知道 React/Context 的存在，
 * 是 auth-context 反过来订阅它以同步登录状态，避免两个模块互相 import 形成循环依赖。
 */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function notifyUnauthorized(): void {
  clearAuthToken();
  unauthorizedListeners.forEach((listener) => listener());
}

export class ApiError extends Error {
  /** HTTP 状态码；网络层面的失败（请求根本没发出去）用 0 表示 */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * 从后端各种错误形状里抠出一句人话：
 * - `{error}`：认证中间件（401）与多数业务失败（如「监控已在运行中」）
 * - `{message}`：登录失败等少数端点
 * - `{detail}`：FastAPI 自动生成的错误（404 路由不存在、422 校验失败、500 未捕获异常）
 */
function extractErrorMessage(payload: unknown, status: number): string {
  if (isPlainObject(payload)) {
    if (typeof payload.error === "string" && payload.error) return payload.error;
    if (typeof payload.message === "string" && payload.message) return payload.message;
    const detail = payload.detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first: unknown = detail[0];
      if (isPlainObject(first) && typeof first.msg === "string") return first.msg;
    }
  }
  return `请求失败（HTTP ${status}）`;
}

/**
 * 剥离 `{success, ...fields}` 信封，只把业务字段交给调用方。
 *
 * 用 `payload.success === false` 而不是 `!payload.success` 判定失败，是因为
 * GET /api/monitor/status（以及鉴权白名单内的 /api/check-auth）不带 success 字段——
 * 这种情况下 `success` 是 `undefined`，若用 `!success` 判断会把正常响应误判成失败。
 * 没有 success 字段时直接原样返回整个 payload，这正是 monitor/status 需要的行为，
 * 不需要调用方额外传参区分。
 */
function unwrapEnvelope<T>(payload: unknown, status: number): T {
  if (isPlainObject(payload) && "success" in payload) {
    if (payload.success === false) {
      throw new ApiError(status, extractErrorMessage(payload, status));
    }
    // _success 是刻意丢弃的信封字段（下划线前缀供 eslint 的 varsIgnorePattern 识别）
    const { success: _success, ...rest } = payload;
    return rest as T;
  }
  return payload as T;
}

export interface ApiRequestOptions {
  /** 外部中止信号（如 React Query queryFn 收到的 signal），切走页面时取消在途请求 */
  signal?: AbortSignal;
  /**
   * 超时毫秒数；不传则不限时 —— 本项目有多个分钟级的慢端点（余额查询 / 一键全签），
   * 一刀切的默认超时会把它们误杀，所以由调用方按端点快慢自行指定。
   */
  timeoutMs?: number;
}

async function request<T>(path: string, init: RequestInit, options: ApiRequestOptions): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  // 外部 signal 与超时 signal 合并：任一触发都中止请求
  const signals: AbortSignal[] = [];
  if (options.signal) signals.push(options.signal);
  if (options.timeoutMs !== undefined) signals.push(AbortSignal.timeout(options.timeoutMs));
  const signal = signals.length > 0 ? AbortSignal.any(signals) : undefined;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers, signal });
  } catch (error) {
    if (options.signal?.aborted) {
      // 调用方主动取消：保持 AbortError 语义，让 React Query 正确识别「已取消」而非失败
      throw error instanceof Error ? error : new DOMException("Aborted", "AbortError");
    }
    if (signal?.aborted) {
      throw new ApiError(0, "请求超时，请重试");
    }
    throw new ApiError(0, "网络错误，请检查连接后重试");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.status === 401) {
    notifyUnauthorized();
  }
  if (!response.ok) {
    throw new ApiError(response.status, extractErrorMessage(payload, response.status));
  }
  return unwrapEnvelope<T>(payload, response.status);
}

export function apiGet<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  return request<T>(path, { method: "GET" }, options);
}

export function apiPost<T>(path: string, body?: unknown, options: ApiRequestOptions = {}): Promise<T> {
  return request<T>(
    path,
    {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    options
  );
}
