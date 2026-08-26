import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";

import { Spinner } from "@/components/ui/spinner";

const AppShell = lazyNamed(() => import("@/app/app-shell"), "AppShell");
const DashboardPage = lazyNamed(() => import("@/features/dashboard/dashboard-page"), "DashboardPage");
const AccountsPage = lazyNamed(() => import("@/features/accounts/accounts-page"), "AccountsPage");
const CheckinPage = lazyNamed(() => import("@/features/checkin/checkin-page"), "CheckinPage");
const KeysPage = lazyNamed(() => import("@/features/keys/keys-page"), "KeysPage");
const UsagePage = lazyNamed(() => import("@/features/usage/usage-page"), "UsagePage");
const SitesPage = lazyNamed(() => import("@/features/sites/sites-page"), "SitesPage");
const SettingsPage = lazyNamed(() => import("@/features/settings/settings-page"), "SettingsPage");

function lazyNamed<T extends Record<K, ComponentType>, K extends keyof T>(loader: () => Promise<T>, exportName: K): LazyExoticComponent<T[K]> {
  return lazy(async () => ({ default: (await loader())[exportName] }));
}

function DeferredPage({ page: Page }: { page: ComponentType }) {
  return <Suspense fallback={<PageLoadingFallback />}><Page /></Suspense>;
}

export function DeferredAppShell() {
  return <Suspense fallback={<PageLoadingFallback fullScreen />}><AppShell /></Suspense>;
}

export function DeferredDashboardPage() {
  return <DeferredPage page={DashboardPage} />;
}

export function DeferredAccountsPage() {
  return <DeferredPage page={AccountsPage} />;
}

export function DeferredCheckinPage() {
  return <DeferredPage page={CheckinPage} />;
}

export function DeferredKeysPage() {
  return <DeferredPage page={KeysPage} />;
}

export function DeferredUsagePage() {
  return <DeferredPage page={UsagePage} />;
}

export function DeferredSitesPage() {
  return <DeferredPage page={SitesPage} />;
}

export function DeferredSettingsPage() {
  return <DeferredPage page={SettingsPage} />;
}

function PageLoadingFallback({ fullScreen = false }: { fullScreen?: boolean }) {
  return (
    <div className={fullScreen ? "flex min-h-screen items-center justify-center bg-background" : "flex min-h-[calc(100vh-7rem)] items-center justify-center lg:min-h-[calc(100vh-10rem)]"}>
      <Spinner className="size-5" />
    </div>
  );
}
