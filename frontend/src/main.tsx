import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { AppProviders } from "@/app/providers";
import { router } from "@/app/router";
import "./index.css";

// 服务端重新部署后，旧标签页懒加载新路由可能请求到已被替换的旧 hash 产物而失败。
// Vite 会在动态 import 失败时派发 vite:preloadError，整页刷新拿到新 index.html
// 即可自愈；冷却时间防止服务端真故障时陷入刷新死循环。
const CHUNK_RELOAD_GUARD = "chunk-reload-at";
window.addEventListener("vite:preloadError", () => {
  const last = Number(sessionStorage.getItem(CHUNK_RELOAD_GUARD) ?? 0);
  if (Date.now() - last < 10_000) return;
  sessionStorage.setItem(CHUNK_RELOAD_GUARD, String(Date.now()));
  window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);
