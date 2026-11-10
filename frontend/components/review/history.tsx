"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { SectionPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ItemVersion {
  version: number;
  author: "ai" | "system" | "user";
  editor: string | null;
  instruction: string;
  status: string;
  review_status: string;
  created_at: string;
  payload: SectionPayload | null;
}

type Op = { kind: "same" | "add" | "del"; text: string };

/** Пословное сравнение (наибольшая общая подпоследовательность), с разделителями и пробелами. */
export function wordDiff(a: string, b: string): Op[] {
  const x = a.split(/(\s+)/).filter(Boolean);
  const y = b.split(/(\s+)/).filter(Boolean);
  if (x.length * y.length > 4_000_000) {
    return [
      { kind: "del", text: a },
      { kind: "add", text: b },
    ];
  }
  const m = x.length;
  const n = y.length;
  const lcs = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      lcs[i][j] =
        x[i] === y[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const ops: Op[] = [];
  const push = (kind: Op["kind"], text: string) => {
    const last = ops[ops.length - 1];
    if (last && last.kind === kind) last.text += text;
    else ops.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (x[i] === y[j]) {
      push("same", x[i]);
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) push("del", x[i++]);
    else push("add", y[j++]);
  }
  while (i < m) push("del", x[i++]);
  while (j < n) push("add", y[j++]);
  return ops;
}

function Diff({ before, after }: { before: string; after: string }) {
  return (
    <p className="whitespace-pre-wrap text-sm">
      {wordDiff(before, after).map((op, k) => (
        <span
          key={k}
          className={cn(
            op.kind === "add" && "bg-emerald-100 text-emerald-900",
            op.kind === "del" && "bg-red-100 text-red-900 line-through",
          )}
        >
          {op.text}
        </span>
      ))}
    </p>
  );
}

const asText = (p: SectionPayload | null) =>
  p
    ? [
        p.summary,
        p.analysis_md,
        ...p.remarks.map((r) => `• ${r}`),
        ...p.recommendations.map((r) => `→ ${r}`),
        p.gap ? Object.values(p.gap).join("\n") : "",
      ]
        .filter(Boolean)
        .join("\n\n")
    : "";

/** История версий пункта: кто и когда изменил; сравнение выбранной версии с текущей. */
export function VersionHistory({
  projectId,
  itemId,
  current,
}: {
  projectId: string;
  itemId: string;
  current: number;
}) {
  const t = useTranslations("history");
  const versions = useQuery({
    queryKey: ["item-versions", projectId, itemId, current],
    queryFn: () =>
      api.get<ItemVersion[]>(
        `/projects/${projectId}/assessment/items/${itemId}/versions`,
      ),
  });
  const list = versions.data ?? [];
  const [base, setBase] = useState<number | null>(null);
  if (list.length < 2) return null;
  const latest = list[0];
  const compare =
    list.find((v) => v.version === (base ?? list[1].version)) ?? list[1];

  return (
    <section
      className="flex flex-col gap-2 border-t pt-4"
      data-testid="history"
    >
      <h3 className="text-xs font-semibold text-muted-foreground uppercase">
        {t("title")}
      </h3>
      <ul className="flex flex-col gap-1 text-xs">
        {list.map((v) => (
          <li key={v.version}>
            <span className="font-medium">v{v.version}</span> ·{" "}
            {v.author === "user"
              ? t("expert", { name: v.editor ?? "—" })
              : t(v.author)}{" "}
            · {formatDate(v.created_at)}
            {v.instruction && (
              <span className="text-muted-foreground"> — {v.instruction}</span>
            )}
          </li>
        ))}
      </ul>
      <label className="flex items-center gap-2 text-xs">
        {t("compare", { v: latest.version })}
        <Select
          value={compare.version}
          onChange={(e) => setBase(Number(e.target.value))}
          className="h-8 w-24"
          aria-label={t("compareWith")}
        >
          {list.slice(1).map((v) => (
            <option key={v.version} value={v.version}>
              v{v.version}
            </option>
          ))}
        </Select>
      </label>
      <div className="rounded-md border p-3" data-testid="diff">
        <Diff before={asText(compare.payload)} after={asText(latest.payload)} />
      </div>
    </section>
  );
}
