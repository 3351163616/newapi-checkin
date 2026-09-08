import { useContext } from "react";

import { AuthContext, type AuthContextValue } from "@/shared/auth/auth-state";

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth 必须在 AuthProvider 内部使用");
  }
  return value;
}
