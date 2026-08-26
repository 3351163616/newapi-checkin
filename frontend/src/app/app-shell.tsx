import { BarChart3, CalendarCheck, Globe, KeyRound, LayoutDashboard, LogOut, Menu, Moon, Settings, Sun, Users } from "lucide-react";
import { useTheme } from "next-themes";
import { useState, type ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";

function ThemeToggleButton({ theme, setTheme }: { theme: string | undefined; setTheme: (t: string) => void }) {
  const isDark = theme === "dark" || (theme === "system" && typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-8 rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "切换到浅色主题" : "切换到深色主题"}
      title={isDark ? "切换到浅色" : "切换到深色"}
    >
      {isDark ? <Sun className="size-4" strokeWidth={1.8} /> : <Moon className="size-4" strokeWidth={1.8} />}
    </Button>
  );
}

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useAuth } from "@/shared/auth/use-auth";
import { cn } from "@/shared/lib/cn";

const navigation = [
  { href: "/dashboard", label: "总览", icon: LayoutDashboard },
  { href: "/accounts", label: "账号", icon: Users },
  { href: "/checkin", label: "签到", icon: CalendarCheck },
  { href: "/keys", label: "密钥", icon: KeyRound },
  { href: "/usage", label: "用量", icon: BarChart3 },
  { href: "/sites", label: "站点", icon: Globe },
  { href: "/settings", label: "设置", icon: Settings },
] as const;

export function AppShell() {
  const { logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);

  function navigationLinks(): ReactNode {
    return navigation.map(({ href, label, icon: Icon }) => (
      <NavLink
        key={href}
        to={href}
        onClick={() => setMobileOpen(false)}
        className={({ isActive }) => cn(
          "group flex h-10 items-center gap-3 rounded-md px-3 text-[15px] font-normal text-muted-foreground transition-colors hover:bg-secondary/55 hover:text-foreground",
          isActive && "bg-secondary/60 text-foreground",
        )}
      >
        {({ isActive }) => (
          <>
            <span className="flex size-6 shrink-0 items-center justify-center">
              <Icon className={cn("size-[18px] text-muted-foreground", isActive && "text-foreground")} strokeWidth={1.8} />
            </span>
            {label}
          </>
        )}
      </NavLink>
    ));
  }

  const navigationContent = (
    <nav className="mt-8 min-h-0 flex-1 overflow-y-auto overscroll-contain pr-2 pb-2" aria-label="主导航">
      <div className="space-y-1">{navigationLinks()}</div>
    </nav>
  );

  const footerControl = (
    <div className="flex h-9 items-center justify-between gap-1 px-2.5">
      <Button
        variant="ghost"
        size="sm"
        className="h-8 gap-1.5 px-2.5 text-xs font-normal text-muted-foreground hover:text-foreground"
        onClick={() => void logout()}
      >
        <LogOut className="size-4" strokeWidth={1.8} />
        退出登录
      </Button>
    </div>
  );

  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 z-30 hidden h-screen w-[280px] flex-col overflow-hidden bg-sidebar px-4 py-5 lg:flex">
        <div className="flex h-7 shrink-0 items-center px-2.5">
          <Link to="/dashboard" className="truncate text-xl font-semibold text-foreground">
            New API Balance Manager
          </Link>
        </div>
        {navigationContent}
        <div className="relative z-10 mt-4 shrink-0 border-t border-sidebar-border/60 bg-sidebar pt-4">{footerControl}</div>
      </aside>

      <div className="flex min-h-screen flex-col lg:pl-[280px]">
        {/* 桌面顶栏：右侧圆形主题切换 + 登出 */}
        <header className="sticky top-0 z-40 hidden h-12 items-center justify-end gap-1.5 border-b bg-background/80 px-5 backdrop-blur-sm lg:flex">
          <ThemeToggleButton theme={theme} setTheme={setTheme} />
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 px-2.5 text-xs font-normal text-muted-foreground hover:text-foreground"
            onClick={() => void logout()}
          >
            <LogOut className="size-4" strokeWidth={1.8} />
            退出登录
          </Button>
        </header>

        <header className="sticky top-0 z-40 flex h-12 items-center gap-3 border-b bg-background px-4 lg:hidden">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="size-8 shrink-0" aria-label="打开导航">
                <Menu className="size-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="flex h-dvh max-h-dvh w-72 flex-col gap-0 overflow-hidden bg-sidebar px-3 py-4 [&>button]:right-2 [&>button]:top-3.5 [&>button]:flex [&>button]:size-7 [&>button]:items-center [&>button]:justify-center">
              <SheetHeader className="h-7 shrink-0 px-2.5 text-left">
                <SheetTitle className="flex h-7 items-center text-lg">New API Balance Manager</SheetTitle>
                <SheetDescription className="sr-only">主导航</SheetDescription>
              </SheetHeader>
              <nav className="mt-5 min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1 pb-2" aria-label="主导航">
                <div className="space-y-1">{navigationLinks()}</div>
              </nav>
              <div className="relative z-10 mt-3 shrink-0 border-t border-sidebar-border/60 bg-sidebar pt-3">{footerControl}</div>
            </SheetContent>
          </Sheet>
          <span className="truncate text-sm font-semibold text-foreground">New API Balance Manager</span>
          <div className="ml-auto flex items-center gap-1">
            <ThemeToggleButton theme={theme} setTheme={setTheme} />
          </div>
        </header>

        <main className="w-full flex-1 px-4 py-5 sm:px-6 lg:py-6">
          <Outlet />
        </main>
        <footer className="border-t px-4 py-3 sm:px-6">
          <p className="text-[11px] text-muted-foreground">
            New API Balance Manager · Powered by curl_cffi + FastAPI · 数据仅存于本地
          </p>
        </footer>
      </div>
    </div>
  );
}
