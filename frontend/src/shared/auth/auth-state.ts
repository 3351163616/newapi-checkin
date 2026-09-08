/**
 * Auth 状态与 Context 的类型定义。从 auth-context.tsx 里拆出来，好让 use-auth.ts
 * 只依赖类型 + Context 对象本身，不必连带整个 Provider 实现（避免不必要的重新打包边界）。
 *
 * 四态语义，与 AuthBoundary / AnonymousBoundary（app/auth-boundary.tsx）的路由决策一一对应：
 * - restoring：启动时正在用本地 auth_token 换取登录态（GET /api/check-auth 请求中）
 * - authenticated：token 校验通过
 * - anonymous：本地没有 token，或 token 已被确认失效
 *   （check-auth 返回 `{authenticated:false}`，或极端情况下返回 401）
 * - unavailable：check-auth 请求本身失败（网络问题 / 服务端挂了）——token 未必失效，
 *   不能清掉，只能提示"无法连接服务器"并提供重试，这也是它要和 anonymous 严格区分的原因
 */
import { createContext } from "react";

export type AuthStatus = "restoring" | "authenticated" | "anonymous" | "unavailable";

export interface AuthContextValue {
  status: AuthStatus;
  /** 重新走一遍启动时的校验流程；SessionUnavailableScreen 的"重试"按钮用它 */
  retryRestore: () => Promise<void>;
  /** 无论 POST /api/logout 成败都会清本地 token 并把状态置为 anonymous */
  logout: () => Promise<void>;
  /** 成功后落 token 并置为 authenticated；失败抛出带 message 的 Error，交给登录页展示 */
  login: (username: string, password: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
