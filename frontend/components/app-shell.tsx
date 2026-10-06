"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations();
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const me = useMe();

  useEffect(() => {
    if (me.isError) router.replace("/login");
  }, [me.isError, router]);

  const logout = useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSettled: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });

  if (!me.data) {
    return <div className="p-8 text-sm text-muted-foreground">{t("common.loading")}</div>;
  }

  const nav = [
    { href: "/portfolio", label: t("nav.portfolio"), active: pathname.startsWith("/portfolio") || pathname.startsWith("/projects") },
    { href: "/account/profile", label: t("nav.account"), active: pathname.startsWith("/account") },
    ...(me.data.role === "admin"
      ? [{ href: "/admin/templates", label: t("admin.nav"), active: pathname.startsWith("/admin") }]
      : []),
  ];

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b bg-card/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
          <Link href="/portfolio" className="text-lg font-semibold tracking-tight text-primary">
            {t("app.name")}
          </Link>
          <nav className="flex gap-1">
            {nav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground",
                  item.active && "bg-accent text-foreground",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium leading-tight">{me.data.full_name}</p>
              <p className="text-xs text-muted-foreground">
                {me.data.organization.name} · {t(`roles.${me.data.role}`)}
              </p>
            </div>
            <Button variant="ghost" size="icon" onClick={() => logout.mutate()} title={t("nav.logout")} aria-label={t("nav.logout")}>
              <LogOut />
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </div>
  );
}
