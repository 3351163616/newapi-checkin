import { AlertCircle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { TableCell, TableRow } from "@/components/ui/table";
import { cn } from "@/shared/lib/cn";

export function LoadingState({ className }: { className?: string }) {
  return (
    <div className={cn("flex min-h-44 items-center justify-center", className)}>
      <Spinner className="size-5" />
    </div>
  );
}

export function TableLoadingRow({ colSpan }: { colSpan: number }) {
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell colSpan={colSpan} className="p-0">
        <LoadingState className="min-h-40" />
      </TableCell>
    </TableRow>
  );
}

export function EmptyState({ message, action }: { message?: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-44 flex-col items-center justify-center gap-2.5 text-center text-muted-foreground">
      <Inbox className="size-7 stroke-1" />
      <p className="text-sm">{message ?? "暂无数据"}</p>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex min-h-44 flex-col items-center justify-center gap-3 text-center">
      <AlertCircle className="size-7 text-destructive" />
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      <Button type="button" variant="secondary" size="sm" onClick={onRetry}>重试</Button>
    </div>
  );
}
