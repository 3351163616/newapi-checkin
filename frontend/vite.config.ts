import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 后端 balance_server.py 的 46 个路由全部挂在 /api 前缀下，只有 `/` 是页面路由，
// 所以开发期只需把 /api 代理过去，其余交给 Vite 的 SPA 处理。
const API_TARGET = process.env.VITE_DEV_API_TARGET ?? "http://127.0.0.1:8003";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": API_TARGET,
    },
  },
  build: {
    // 产物直接交给 balance_server.py 托管
    outDir: "dist",
    sourcemap: false,
  },
});
