"use client";

import { AlertTriangle, CheckCircle2, Library } from "lucide-react";
import { useTranslations } from "next-intl";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Completeness } from "@/lib/types";
import { cn } from "@/lib/utils";

export function CompletenessPanel({ data }: { data: Completeness | undefined }) {
  const t = useTranslations("documents");
  if (!data) return null;
  const total = data.items_full + data.items_partial + data.items_none;
  const pct = (n: number) => `${(n / Math.max(1, total)) * 100}%`;

  return (
    <Card className="h-fit xl:sticky xl:top-20" data-testid="completeness">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{t("completeness.title")}</CardTitle>
        <div className="flex h-2 overflow-hidden rounded bg-muted" aria-hidden>
          <div className="bg-emerald-500" style={{ width: pct(data.items_full) }} />
          <div className="bg-amber-400" style={{ width: pct(data.items_partial) }} />
          <div className="bg-red-400" style={{ width: pct(data.items_none) }} />
        </div>
        <p className="text-xs text-muted-foreground">
          {t("completeness.summary", { full: data.items_full, partial: data.items_partial, none: data.items_none })}
        </p>
        {(data.documents_processing > 0 || data.documents_failed > 0) && (
          <p className="text-xs">
            {data.documents_processing > 0 && <span className="mr-3">{t("processing", { n: data.documents_processing })}</span>}
            {data.documents_failed > 0 && <span className="text-destructive">{t("failed", { n: data.documents_failed })}</span>}
          </p>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {data.missing.length === 0 ? (
          <p className="flex items-start gap-2 text-emerald-700">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> {t("completeness.allPresent")}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("completeness.missingTitle")}
            </p>
            {data.missing.map((m) => (
              <div key={m.code} className="flex flex-col gap-1" data-testid="missing-category">
                {m.blocked_items.length > 0 && (
                  <p className="flex items-start gap-2 text-red-700">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                    {t("completeness.missingBlocked", { title: m.title, items: m.blocked_items.join(", ") })}
                  </p>
                )}
                {m.partial_items.length > 0 && (
                  <p className="flex items-start gap-2 text-amber-800">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                    {t("completeness.missingPartial", { title: m.title, items: m.partial_items.join(", ") })}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        {data.missing_references.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("completeness.referencesTitle")}
            </p>
            {data.missing_references.map((r) => (
              <p key={r.kind} className="flex items-start gap-2 text-amber-800">
                <Library className="mt-0.5 size-4 shrink-0" />
                {t("completeness.missingReference", { kind: t(`refKind.${r.kind}`), items: r.items.join(", ") })}
              </p>
            ))}
          </div>
        )}

        <div className="flex flex-col gap-1.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("completeness.categories")}
          </p>
          {data.categories
            .filter((c) => c.required_by.length > 0 || c.documents > 0)
            .map((c) => (
              <div key={c.code} className="flex items-center justify-between gap-2">
                <span className={cn("truncate", c.documents === 0 && "text-muted-foreground")} title={c.title}>
                  {c.title}
                </span>
                <span
                  className={cn(
                    "min-w-6 rounded px-1.5 text-center text-xs font-medium",
                    c.documents ? "bg-emerald-100 text-emerald-800" : "bg-muted text-muted-foreground",
                  )}
                >
                  {c.documents}
                </span>
              </div>
            ))}
        </div>
      </CardContent>
    </Card>
  );
}
