import { useRouteError } from "react-router-dom";
import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * 路由级错误兜底：取代 react-router 默认的「💿 Hey developer」开发向报错页。
 * 最常见诱因是服务端刚部署过，旧标签页懒加载时请求到已不存在的旧 hash 产物，
 * 整页刷新拿到新 index.html 即可恢复。
 */
export function RouteError() {
  const error = useRouteError();
  const message = error instanceof Error ? error.message : "页面加载失败";

  return (
    <div className="grid min-h-screen place-items-center bg-background p-6">
      <div className="max-w-sm space-y-4 text-center">
        <h1 className="text-lg font-medium">页面出错了</h1>
        <p className="text-sm text-muted-foreground">
          {message}
          ——如果服务端刚更新过，刷新一下即可恢复。
        </p>
        <Button variant="outline" onClick={() => window.location.reload()}>
          <RotateCcw className="size-3.5" aria-hidden="true" />
          重新加载
        </Button>
      </div>
    </div>
  );
}
