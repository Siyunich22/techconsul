"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_PREFIX, queryString } from "@/lib/api";
import { useDocumentPage } from "@/lib/hooks";
import type { ProjectDocument } from "@/lib/types";

/** Подсветка вхождений фрагмента в тексте страницы (для форматов без растрового предпросмотра). */
function highlight(text: string, q: string): ReactNode[] {
  const needle = q.trim();
  if (!needle) return [text];
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  const n = needle.toLowerCase();
  let i = 0;
  for (let at = lower.indexOf(n); at !== -1; at = lower.indexOf(n, i)) {
    parts.push(text.slice(i, at), <mark key={at}>{text.slice(at, at + needle.length)}</mark>);
    i = at + needle.length;
  }
  parts.push(text.slice(i));
  return parts;
}

export function DocumentPreview({
  projectId,
  doc,
  initialPage = 1,
  initialQuery = "",
  onClose,
}: {
  projectId: string;
  doc: ProjectDocument | undefined;
  initialPage?: number;
  initialQuery?: string;
  onClose: () => void;
}) {
  const t = useTranslations("documents");
  const [page, setPage] = useState(initialPage);
  const [q, setQ] = useState(initialQuery);
  const [appliedQ, setAppliedQ] = useState(initialQuery);
  const data = useDocumentPage(projectId, doc?.id ?? null, page);
  const total = doc?.pages ?? data.data?.pages_total ?? 1;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!doc) return null;
  const imageUrl = `${API_PREFIX}/projects/${projectId}/documents/${doc.id}/pages/${page}/image${queryString({ q: appliedQ || undefined })}`;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose} role="dialog" aria-label={doc.filename}>
      <div className="flex h-full w-full max-w-4xl flex-col bg-background shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <p className="min-w-0 flex-1 truncate font-medium" title={doc.filename}>
            {doc.filename}
          </p>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Закрыть">
            <X />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          <Button variant="outline" size="icon" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Назад">
            <ChevronLeft />
          </Button>
          <span className="text-sm" data-testid="preview-page">
            {t("page", { page, total })}
          </span>
          <Button variant="outline" size="icon" disabled={page >= total} onClick={() => setPage(page + 1)} aria-label="Вперёд">
            <ChevronRight />
          </Button>
          {data.data?.sheet && <span className="text-sm text-muted-foreground">{t("sheet", { name: data.data.sheet })}</span>}
          <form
            className="ml-auto flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setAppliedQ(q);
            }}
          >
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("highlight")} className="h-8 w-56" />
            <Button type="submit" size="sm" variant="secondary">
              {t("highlight")}
            </Button>
          </form>
        </div>
        <div className="flex-1 overflow-auto bg-muted/40 p-4">
          {data.data?.has_image ? (
            // eslint-disable-next-line @next/next/no-img-element -- рендер страницы отдаёт API (cookie-авторизация)
            <img src={imageUrl} alt={t("page", { page, total })} className="mx-auto max-w-full bg-white shadow" />
          ) : (
            <div className="mx-auto max-w-3xl rounded bg-card p-6 shadow-sm">
              <p className="mb-3 text-xs text-muted-foreground">{t("textOnly")}</p>
              <pre className="font-sans text-sm whitespace-pre-wrap">{highlight(data.data?.text ?? "", appliedQ)}</pre>
            </div>
          )}
          {data.data && data.data.tables.length > 0 && data.data.has_image && (
            <details className="mx-auto mt-4 max-w-3xl rounded bg-card p-4 text-sm shadow-sm">
              <summary className="cursor-pointer font-medium">
                {t("tables")} ({data.data.tables.length})
              </summary>
              {data.data.tables.map((tb, i) => (
                <table key={i} className="mt-3 w-full border-collapse text-xs">
                  <tbody>
                    {tb.rows.slice(0, 50).map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) => (
                          <td key={c} className="border px-2 py-1">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ))}
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
