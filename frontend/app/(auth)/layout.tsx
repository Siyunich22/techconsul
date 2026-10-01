import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

export default async function AuthLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations("app");
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-8 px-4 py-12">
      <div className="text-center">
        <p className="text-2xl font-semibold tracking-tight text-primary">{t("name")}</p>
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{t("tagline")}</p>
      </div>
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
