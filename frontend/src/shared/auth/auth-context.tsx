/**
 * 全局登录态管理。后端（balance_server.py:75-110）只有单一管理员账号、一枚 30 天有效期
 * 的 Bearer token，没有 access/refresh 双 token 机制，所以恢复流程比 grok2api 的版本简单：
 * 直接拿本地 token 问一次 GET /api/check-auth，不需要刷新令牌那一套。
 *
 * 状态机与消费方的关系：
 * - AuthBoundary（受保护路由，app/auth-boundary.tsx）：
 *   restoring 显示占位、anonymous 跳转 /login、unavailable 显示"无法连接服务器"+重试、
 *   authenticated 放行
 * - AnonymousBoundary（/login）：authenticated 时跳回 /dashboard，其余放行
 * - AppShell 的"退出登录"按钮调 logout()
 * - LoginPage 调 login(username, password)
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, apiGet, apiPost, clearAuthToken, getAuthToken, onUnauthorized, setAuthToken } from "@/shared/api/client";
import { AuthContext, type AuthContextValue, type AuthStatus } from "@/shared/auth/auth-state";

interface CheckAuthResponse {
  authenticated: boolean;
}

interface LoginResponse {
  token: string;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() => (getAuthToken() ? "restoring" : "anonymous"));

  // 给每次 restoreSession 调用编号，只采信"最后发起的那一次"的结果。
  // retryRestore 是暴露给"无法连接服务器"重试按钮的，用户可能连点，或者
  // React StrictMode 下 effect 会二次挂载触发两次调用——不加这层守卫，
  // 旧请求晚于新请求返回时会把状态带回过时的结果。
  const restoreRequestSeq = useRef(0);

  const restoreSession = useCallback(async (): Promise<void> => {
    const token = getAuthToken();
    if (!token) {
      setStatus("anonymous");
      return;
    }
    setStatus("restoring");
    const seq = ++restoreRequestSeq.current;
    try {
      const result = await apiGet<CheckAuthResponse>("/check-auth");
      if (seq !== restoreRequestSeq.current) return;
      if (result.authenticated) {
        setStatus("authenticated");
      } else {
        clearAuthToken();
        setStatus("anonymous");
      }
    } catch (error) {
      if (seq !== restoreRequestSeq.current) return;
      if (error instanceof ApiError && error.status === 401) {
        // client.ts 在 401 时已经清过 token 并广播过 onUnauthorized，这里只是同步状态
        setStatus("anonymous");
      } else {
        // 网络错误或服务端异常：token 未必失效，别清掉，留给用户重试
        setStatus("unavailable");
      }
    }
  }, []);

  useEffect(() => {
    void restoreSession();
    return onUnauthorized(() => setStatus("anonymous"));
  }, [restoreSession]);

  const login = useCallback(async (username: string, password: string): Promise<void> => {
    const result = await apiPost<LoginResponse>("/login", { username, password });
    setAuthToken(result.token);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    try {
      await apiPost("/logout");
    } catch {
      // 登出端点失败也无所谓——本地状态照样清，不能让用户卡在"已登出但 UI 还显示登录"的状态。
      // 旧前端根本没有登出功能，这里是补的缺口（frontend-contract.md 缺口 #1）。
    } finally {
      clearAuthToken();
      setStatus("anonymous");
    }
  }, []);

  const value: AuthContextValue = { status, retryRestore: restoreSession, logout, login };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
