import { Navigate, createBrowserRouter } from "react-router-dom";

import { AnonymousBoundary, AuthBoundary } from "@/app/auth-boundary";
import { DeferredAccountsPage, DeferredAppShell, DeferredCheckinPage, DeferredDashboardPage, DeferredKeysPage, DeferredSettingsPage, DeferredSitesPage, DeferredUsagePage } from "@/app/deferred-pages";
import { RouteError } from "@/app/route-error";
import { LoginPage } from "@/features/auth/login-page";

export const router = createBrowserRouter([
  {
    errorElement: <RouteError />,
    element: <AnonymousBoundary />,
    children: [{ path: "/login", element: <LoginPage /> }],
  },
  {
    errorElement: <RouteError />,
    element: <AuthBoundary />,
    children: [
      {
        element: <DeferredAppShell />,
        children: [
          { index: true, element: <Navigate to="/dashboard" replace /> },
          { path: "/dashboard", element: <DeferredDashboardPage /> },
          { path: "/accounts", element: <DeferredAccountsPage /> },
          { path: "/checkin", element: <DeferredCheckinPage /> },
          { path: "/keys", element: <DeferredKeysPage /> },
          { path: "/usage", element: <DeferredUsagePage /> },
          { path: "/sites", element: <DeferredSitesPage /> },
          { path: "/settings", element: <DeferredSettingsPage /> },
        ],
      },
    ],
  },
  { path: "*", element: <Navigate to="/dashboard" replace /> },
], {
  // 与 `vite build --base` 保持一致；根路径部署时 import.meta.env.BASE_URL 为 "/"，
  // basename 传 undefined 等价于默认行为。
  basename: import.meta.env.BASE_URL.replace(/\/$/, "") || undefined,
});
