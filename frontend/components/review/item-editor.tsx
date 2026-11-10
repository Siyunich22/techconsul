"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { MarkdownEditor } from "@/components/review/markdown-editor";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api";
import type { DisclosureStatus, SectionPayload } from "@/lib/types";

export interface ItemEditBody {
  disclosure_status: DisclosureStatus;
  summary: string;
  analysis_md: string;
  remarks: string[];
  recommendations: string[];
  assumptions: string[];
  gap: {
    reason: string;
    impact_on_conclusions: string;
    assumptions: string;
    recommendation: string;
  } | null;
  note: string;
}

const lines = (s: string) =>
  s
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

/** Правка пункта (или выводов) экспертом: сохраняется новой версией, ИИ-версия остаётся в истории. */
export function ItemEditor({
  payload,
  sources,
  pending,
  error,
  onSave,
  onCancel,
}: {
  payload: SectionPayload | null;
  sources: string[];
  pending: boolean;
  error: unknown;
  onSave: (body: ItemEditBody) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("editor");
  const ta = useTranslations("assessment");
  const [status, setStatus] = useState<DisclosureStatus>(
    payload?.disclosure_status ?? "not_disclosed",
  );
  const [summary, setSummary] = useState(payload?.summary ?? "");
  const [analysis, setAnalysis] = useState(payload?.analysis_md ?? "");
  const [remarks, setRemarks] = useState((payload?.remarks ?? []).join("\n"));
  const [recs, setRecs] = useState((payload?.recommendations ?? []).join("\n"));
  const [assumptions, setAssumptions] = useState(
    (payload?.assumptions ?? []).join("\n"),
  );
  const [gap, setGap] = useState({
    reason: payload?.gap?.reason ?? "",
    impact_on_conclusions: payload?.gap?.impact_on_conclusions ?? "",
    assumptions: payload?.gap?.assumptions ?? "",
    recommendation: payload?.gap?.recommendation ?? "",
  });
  const [note, setNote] = useState("");

  const save = () =>
    onSave({
      disclosure_status: status,
      summary,
      analysis_md: analysis,
      remarks: lines(remarks),
      recommendations: lines(recs),
      assumptions: lines(assumptions),
      gap: status === "not_disclosed" ? gap : null,
      note,
    });

  return (
    <div className="flex flex-col gap-3" data-testid="item-editor">
      <label className="flex flex-col gap-1 text-xs font-medium">
        {t("status")}
        <Select
          value={status}
          onChange={(e) => setStatus(e.target.value as DisclosureStatus)}
        >
          {(
            ["full", "partial", "not_disclosed", "not_applicable"] as const
          ).map((s) => (
            <option key={s} value={s}>
              {ta(`disclosure.${s}`)}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex flex-col gap-1 text-xs font-medium">
        {t("summary")}
        <Textarea
          rows={3}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </label>
      <div className="flex flex-col gap-1 text-xs font-medium">
        {t("analysis")}
        <MarkdownEditor
          value={analysis}
          onChange={setAnalysis}
          label={t("analysis")}
        />
      </div>
      {status === "not_disclosed" && (
        <fieldset className="grid gap-2 rounded-md border p-3">
          <legend className="px-1 text-xs font-medium">{ta("gap")}</legend>
          {(
            [
              ["reason", "gapReason"],
              ["impact_on_conclusions", "gapImpact"],
              ["assumptions", "gapAssumptions"],
              ["recommendation", "gapRecommendation"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="flex flex-col gap-1 text-xs">
              {ta(label)}
              <Textarea
                rows={2}
                value={gap[k]}
                onChange={(e) => setGap({ ...gap, [k]: e.target.value })}
              />
            </label>
          ))}
        </fieldset>
      )}
      {(
        [
          ["remarks", remarks, setRemarks],
          ["recommendations", recs, setRecs],
          ["assumptions", assumptions, setAssumptions],
        ] as const
      ).map(([key, value, set]) => (
        <label key={key} className="flex flex-col gap-1 text-xs font-medium">
          {t(key)}
          <Textarea
            rows={3}
            value={value}
            onChange={(e) => set(e.target.value)}
            placeholder={t("onePerLine")}
          />
        </label>
      ))}
      <p className="text-xs text-muted-foreground">
        {t("refsHint", { sources: sources.slice(0, 12).join(", ") || "—" })}
      </p>
      <label className="flex flex-col gap-1 text-xs font-medium">
        {t("note")}
        <Input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("notePlaceholder")}
        />
      </label>
      {error ? (
        <Alert variant="destructive">{errorMessage(error)}</Alert>
      ) : null}
      <div className="flex gap-2">
        <Button onClick={save} disabled={pending}>
          {t("save")}
        </Button>
        <Button variant="outline" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>
    </div>
  );
}
