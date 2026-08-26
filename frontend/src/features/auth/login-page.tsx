import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/shared/auth/use-auth";
import { errorMessage } from "@/features/checkin/checkin-format";

/**
 * 登录页。旧前端的密码框不在 <form> 里，浏览器一直报
 * 「Password field is not contained in a form」——回车不能提交、密码管理器不识别。
 * 这里必须是真 <form onSubmit>，并带上 autocomplete 属性。
 */
export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setError(null);
    if (!username.trim() || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setPending(true);
    try {
      await login(username.trim(), password);
      // 登录成功后由 AuthBoundary 接管跳转，这里不用做任何事
    } catch (err) {
      setError(errorMessage(err, "登录失败，请重试"));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm animate-in fade-in zoom-in-95 slide-in-from-bottom-2 duration-300">
        <section className="rounded-lg border bg-card p-6 shadow-sm">
          <h1 className="text-xl font-medium tracking-tight">New API Balance Manager</h1>
          <p className="mt-1 text-xs text-muted-foreground">批量管理 new-api 系站点：余额 · 签到 · 用量 · 密钥</p>

          <form className="mt-6 space-y-4" onSubmit={onSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="login-username">用户名</Label>
              <Input
                id="login-username"
                autoComplete="username"
                placeholder="admin"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={pending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="login-password">密码</Label>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={pending}
              />
            </div>

            {error ? (
              <p className="animate-in fade-in slide-in-from-top-1 text-xs text-destructive" role="alert">
                {error}
              </p>
            ) : null}

            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  登录中…
                </>
              ) : (
                "登录"
              )}
            </Button>
          </form>
        </section>
        <p className="mt-4 text-center text-xs text-muted-foreground">
          凭据来自服务端 .env（AUTH_USERNAME / AUTH_PASSWORD），首次启动会自动生成随机密码
        </p>
      </div>
    </main>
  );
}
