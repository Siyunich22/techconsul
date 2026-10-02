"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const ITEMS: { href: string; key: string; phase?: number }[] = [
  { href: "/account/profile", key: "profile" },
  { href: "/account/organization", key: "organization" },
  { href: "/account/experts", key: "experts" },
  { href: "#tasks", key: "tasks", phase: 7 },
  { href: "#usage", key: "usage", phase: 9 },
];

export default function AccountLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("nav");
  const tc = useTranslations("common");
  const pathname = usePathname();
  return (
    <div className="flex flex-col gap-6 md:flex-row">
      <nav className="flex shrink-0 gap-1 overflow-x-auto md:w-52 md:flex-col">
        {ITEMS.map((item) =>
          item.phase ? (
            <span
              key={item.key}
              title={tc("phase", { n: item.phase })}
              className="cursor-not-allowed rounded-md px-3 py-2 text-sm whitespace-nowrap text-muted-foreground/60"
            >
              {t(item.key)}
            </span>
          ) : (
            <Link
              key={item.key}
              href={item.href}
              className={cn(
                "rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap text-muted-foreground hover:bg-accent hover:text-foreground",
                pathname === item.href && "bg-accent text-foreground",
              )}
            >
              {t(item.key)}
            </Link>
          ),
        )}
      </nav>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
