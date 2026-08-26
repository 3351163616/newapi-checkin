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
    rollupOptions: {
      output: {
        /**
         * vendor 分包：react/react-dom/react-router/radix 这些几乎不变的运行时单独成块，
         * 业务代码迭代发版时它们的 hash 保持稳定，浏览器缓存不至于整包失效。
         * 页面级拆分已由 deferred-pages.tsx 的路由懒加载完成（recharts 等重依赖只在
         * dashboard/usage 的异步 chunk 里），这里只补「框架层」这一刀。
         */
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("lucide-react")) return "vendor-icons";
          if (id.includes("react") || id.includes("radix-ui") || id.includes("sonner")) return "vendor-react";
          if (id.includes("recharts") || id.includes("victory-vendor") || id.includes("d3-")) return "vendor-charts";
          return "vendor";
        },
      },
    },
  },
});
