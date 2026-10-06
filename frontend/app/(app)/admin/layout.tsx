"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/admin/templates", key: "templates" },
  { href: "/admin/reference-docs", key: "references" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("admin");
  const pathname = usePathname();
  return (
    <div className="flex flex-col gap-5">
      <nav className="flex gap-1 border-b">
        {ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "-mb-px border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground",
              pathname.startsWith(item.href) && "border-primary text-foreground",
            )}
          >
            {t(item.key)}
          </Link>
        ))}
      </nav>
      {children}
    </div>
  );
}
