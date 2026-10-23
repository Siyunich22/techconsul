"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileText, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { usePassport, usePassportLayout } from "@/lib/hooks";
import type {
  Discrepancy,
  Passport,
  PassportData,
  PassportFact,
  PassportLayoutSection,
  PassportRow,
  PassportSource,
  PassportStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export type OpenSource = (docId: string, page: number, quote?: string) => void;

const STATUS_VARIANT: Record<PassportStatus, "muted" | "default" | "warning" | "success" | "destructive"> = {
  empty: "muted",
  extracting: "default",
  draft: "warning",
  confirmed: "success",
  failed: "destructive",
};

const SEVERITY_VARIANT = { high: "destructive", medium: "warning", low: "muted" } as const;
const LOW_CONFIDENCE = 0.6;

interface Draft {
  data: PassportData;
  discrepancies: Discrepancy[];
}

function isFact(v: unknown): v is PassportFact {
  return Boolean(v) && typeof v === "object" && !Array.isArray(v) && "value" in (v as object);
}

/** Перед сохранением: пустые значения → null, пустые строки таблиц — удаляются. */
function normalize(draft: Draft, layout: PassportLayoutSection[]): Draft {
  const data: PassportData = structuredClone(draft.data);
  for (const section of layout) {
    const s = data[section.key];
    for (const f of section.fields) {
      const fact = s[f.key];
      if (isFact(fact) && !fact.value.trim()) s[f.key] = null;
    }
    for (const t of section.tables) {
      const rows = (s[t.key] as PassportRow[] | null) ?? [];
      s[t.key] = rows.filter((r) => t.columns.some((c) => String(r[c.key] ?? "").trim()));
    }
  }
  return { data, discrepancies: draft.discrepancies };
}

export function PassportTab({
  projectId,
  canEdit,
  onOpenSource,
}: {
  projectId: string;
  canEdit: boolean;
  onOpenSource: OpenSource;
}) {
  const t = useTranslations("passport");
  const queryClient = useQueryClient();
  const passport = usePassport(projectId);
  const layout = usePassportLayout();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showEmpty, setShowEmpty] = useState(false);

  const setData = (p: Passport) => queryClient.setQueryData(["passport", projectId], p);
  const extract = useMutation({
    mutationFn: () => api.post<Passport>(`/projects/${projectId}/passport/extract`),
    onSuccess: (p) => {
      setDraft(null);
      setData(p);
    },
  });
  const save = useMutation({
    mutationFn: (body: Draft) => api.put<Passport>(`/projects/${projectId}/passport`, body),
    onSuccess: (p) => {
      setDraft(null);
      setData(p);
    },
  });
  const confirm = useMutation({
    mutationFn: () => api.put<Passport>(`/projects/${projectId}/passport/confirm`, {}),
    onSuccess: setData,
  });

  if (!passport.data || !layout.data) return null;
  const p = passport.data;
  const editing = draft !== null;
  const view: Draft = draft ?? { data: p.data, discrepancies: p.discrepancies };
  const hasData = p.status === "draft" || p.status === "confirmed";
  const error = extract.error ?? save.error ?? confirm.error;

  const startExtract = () => {
    if (hasData && !window.confirm(t("reextractConfirm"))) return;
    extract.mutate();
  };

  return (
    <div className="flex flex-col gap-4" data-testid="passport">
      <Card>
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">{t("title")}</h2>
                <Badge variant={STATUS_VARIANT[p.status]} data-testid="passport-status">
                  {t(`status.${p.status}`)}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                {p.extracted_at && t("extractedAt", { date: formatDate(p.extracted_at) })}
                {p.confirmed_at && ` · ${t("confirmed", { name: p.confirmed_by ?? "—", date: formatDate(p.confirmed_at) })}`}
              </p>
            </div>
            {canEdit && (
              <div className="flex flex-wrap gap-2">
                {editing ? (
                  <>
                    <Button variant="outline" onClick={() => setDraft(null)} disabled={save.isPending}>
                      {t("cancel")}
                    </Button>
                    <Button onClick={() => save.mutate(normalize(draft, layout.data))} disabled={save.isPending}>
                      {t("save")}
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      variant={hasData ? "outline" : "default"}
                      onClick={startExtract}
                      disabled={p.status === "extracting" || extract.isPending}
                    >
                      <Sparkles /> {hasData ? t("reextract") : t("extract")}
                    </Button>
                    {hasData && (
                      <Button
                        variant="outline"
                        onClick={() => setDraft(structuredClone({ data: p.data, discrepancies: p.discrepancies }))}
                      >
                        <Pencil /> {t("edit")}
                      </Button>
                    )}
                    {p.status === "draft" && (
                      <Button onClick={() => confirm.mutate()} disabled={confirm.isPending}>
                        <CheckCircle2 /> {t("confirm")}
                      </Button>
                    )}
                  </>
                )}
              </div>
            )}
          </div>

          {p.status === "empty" && <p className="text-sm text-muted-foreground">{t("intro")}</p>}
          {p.status === "extracting" && <Progress passport={p} />}
          {p.status === "failed" && (
            <Alert variant="destructive">
              {t("failed")}: {p.error}
            </Alert>
          )}
          {p.status === "draft" && !editing && <Alert variant="warning">{t("draftHint")}</Alert>}
          {p.warnings.length > 0 && (
            <Alert variant="warning">
              <p className="font-medium">{t("warnings")}</p>
              <ul className="list-disc pl-5 text-sm">
                {p.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </Alert>
          )}
          {error ? <Alert variant="destructive">{errorMessage(error)}</Alert> : null}
        </CardContent>
      </Card>

      {hasData && (
        <>
          {view.discrepancies.length > 0 && (
            <Discrepancies
              items={view.discrepancies}
              sources={p.sources}
              editing={editing}
              onOpenSource={onOpenSource}
              onChange={(discrepancies) => setDraft({ ...view, discrepancies })}
            />
          )}
          <label className="flex items-center gap-2 self-end text-sm text-muted-foreground">
            <input type="checkbox" checked={showEmpty} onChange={(e) => setShowEmpty(e.target.checked)} />
            {t("showEmpty")}
          </label>
          {layout.data.map((section) => (
            <SectionCard
              key={section.key}
              section={section}
              value={view.data[section.key] ?? {}}
              sources={p.sources}
              editing={editing}
              showEmpty={showEmpty || editing}
              onOpenSource={onOpenSource}
              onChange={(value) => setDraft({ ...view, data: { ...view.data, [section.key]: value } })}
            />
          ))}
        </>
      )}
    </div>
  );
}

function Progress({ passport }: { passport: Passport }) {
  const t = useTranslations("passport");
  const { stage, done, total } = passport.progress;
  const pct = stage === "reduce" ? 95 : total ? Math.round((done / total) * 90) : 2;
  return (
    <div className="flex flex-col gap-2" data-testid="passport-progress">
      <div className="h-2 overflow-hidden rounded bg-muted">
        <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-sm text-muted-foreground">
        {stage === "map" ? t("progressMap", { done, total }) : stage === "reduce" ? t("progressReduce") : t("progressStart")}
      </p>
    </div>
  );
}

function SourceLinks({
  refs,
  quote,
  sources,
  onOpenSource,
}: {
  refs: string[];
  quote?: string;
  sources: Record<string, PassportSource>;
  onOpenSource: OpenSource;
}) {
  const t = useTranslations("passport");
  const label = (s: PassportSource) =>
    `${s.filename}, ${s.sheet ? t("sheet", { name: s.sheet }) : t("page", { n: s.page ?? 1 })}`;
  return (
    <div className="flex flex-col items-start gap-0.5">
      {refs
        .filter((r) => sources[r])
        .map((r) => (
          <button
            key={r}
            type="button"
            title={quote ? `${t("sourceHint")}: «${quote}»` : t("sourceHint")}
            onClick={() => onOpenSource(sources[r].document_id, sources[r].page ?? 1, quote || undefined)}
            className="inline-flex max-w-72 items-center gap-1 text-left text-xs text-primary hover:underline"
          >
            <FileText className="size-3 shrink-0" />
            <span className="truncate">{label(sources[r])}</span>
          </button>
        ))}
    </div>
  );
}

function Confidence({ value, manual }: { value: number; manual: boolean }) {
  const t = useTranslations("passport");
  if (manual) return <Badge variant="secondary">{t("manual")}</Badge>;
  const low = value < LOW_CONFIDENCE;
  return (
    <Badge variant={low ? "warning" : "muted"} title={low ? t("lowConfidence") : undefined}>
      {low && <AlertTriangle className="size-3" />}
      {Math.round(value * 100)}%
    </Badge>
  );
}

function SectionCard({
  section,
  value,
  sources,
  editing,
  showEmpty,
  onOpenSource,
  onChange,
}: {
  section: PassportLayoutSection;
  value: Record<string, PassportFact | PassportRow[] | null>;
  sources: Record<string, PassportSource>;
  editing: boolean;
  showEmpty: boolean;
  onOpenSource: OpenSource;
  onChange: (v: Record<string, PassportFact | PassportRow[] | null>) => void;
}) {
  const t = useTranslations("passport");
  const fields = section.fields.filter((f) => showEmpty || isFact(value[f.key]));
  const tables = section.tables.filter((tb) => showEmpty || ((value[tb.key] as PassportRow[] | null) ?? []).length > 0);
  const filled = fields.length + tables.length;

  const setFact = (key: string, patch: Partial<PassportFact>) => {
    const prev = isFact(value[key]) ? (value[key] as PassportFact) : null;
    const next: PassportFact = {
      value: "",
      unit: "",
      refs: [],
      quote: "",
      confidence: 1,
      ...prev,
      ...patch,
      manual: true,
    };
    onChange({ ...value, [key]: next });
  };

  return (
    <Card data-testid={`passport-section-${section.key}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{section.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {filled === 0 && <p className="text-sm text-muted-foreground">{t("notFound")}</p>}
        {fields.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-56">{t("param")}</TableHead>
                <TableHead>{t("value")}</TableHead>
                <TableHead className="w-64">{t("source")}</TableHead>
                <TableHead className="w-24">{t("confidence")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {fields.map((f) => {
                const fact = isFact(value[f.key]) ? (value[f.key] as PassportFact) : null;
                return (
                  <TableRow key={f.key}>
                    <TableCell className="align-top font-medium">{f.title}</TableCell>
                    <TableCell className="align-top">
                      {editing ? (
                        <div className="flex gap-2">
                          <Input
                            value={fact?.value ?? ""}
                            aria-label={f.title}
                            onChange={(e) => setFact(f.key, { value: e.target.value })}
                          />
                          <Input
                            className="w-28"
                            value={fact?.unit ?? ""}
                            placeholder={t("unit")}
                            onChange={(e) => setFact(f.key, { unit: e.target.value })}
                          />
                        </div>
                      ) : fact ? (
                        <span>
                          {fact.value} {fact.unit}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      {fact && <SourceLinks refs={fact.refs} quote={fact.quote} sources={sources} onOpenSource={onOpenSource} />}
                    </TableCell>
                    <TableCell className="align-top">{fact && <Confidence value={fact.confidence} manual={fact.manual} />}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        {tables.map((tb) => (
          <RowsTable
            key={tb.key}
            title={tb.title}
            columns={tb.columns}
            rows={(value[tb.key] as PassportRow[] | null) ?? []}
            sources={sources}
            editing={editing}
            onOpenSource={onOpenSource}
            onChange={(rows) => onChange({ ...value, [tb.key]: rows })}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function RowsTable({
  title,
  columns,
  rows,
  sources,
  editing,
  onOpenSource,
  onChange,
}: {
  title: string;
  columns: { key: string; title: string }[];
  rows: PassportRow[];
  sources: Record<string, PassportSource>;
  editing: boolean;
  onOpenSource: OpenSource;
  onChange: (rows: PassportRow[]) => void;
}) {
  const t = useTranslations("passport");
  const setCell = (i: number, key: string, v: string) =>
    onChange(rows.map((r, n) => (n === i ? { ...r, [key]: v, manual: true } : r)));
  const addRow = () =>
    onChange([...rows, { refs: [], quote: "", confidence: 1, manual: true, ...Object.fromEntries(columns.map((c) => [c.key, ""])) }]);

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {rows.length === 0 && !editing ? (
        <p className="text-sm text-muted-foreground">{t("noRows")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((c) => (
                <TableHead key={c.key}>{c.title}</TableHead>
              ))}
              <TableHead className="w-56">{t("source")}</TableHead>
              <TableHead className="w-20">{t("confidence")}</TableHead>
              {editing && <TableHead className="w-10" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, i) => (
              <TableRow key={i}>
                {columns.map((c) => (
                  <TableCell key={c.key} className="align-top">
                    {editing ? (
                      <Input
                        className="min-w-24"
                        value={String(row[c.key] ?? "")}
                        aria-label={c.title}
                        onChange={(e) => setCell(i, c.key, e.target.value)}
                      />
                    ) : (
                      String(row[c.key] ?? "")
                    )}
                  </TableCell>
                ))}
                <TableCell className="align-top">
                  <SourceLinks refs={row.refs} quote={row.quote} sources={sources} onOpenSource={onOpenSource} />
                </TableCell>
                <TableCell className="align-top">
                  <Confidence value={row.confidence} manual={row.manual} />
                </TableCell>
                {editing && (
                  <TableCell className="align-top">
                    <Button
                      variant="ghost"
                      size="icon"
                      title={t("removeRow")}
                      onClick={() => onChange(rows.filter((_, n) => n !== i))}
                    >
                      <Trash2 />
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editing && (
        <Button variant="outline" size="sm" className="self-start" onClick={addRow}>
          <Plus /> {t("addRow")}
        </Button>
      )}
    </div>
  );
}

function Discrepancies({
  items,
  sources,
  editing,
  onOpenSource,
  onChange,
}: {
  items: Discrepancy[];
  sources: Record<string, PassportSource>;
  editing: boolean;
  onOpenSource: OpenSource;
  onChange: (items: Discrepancy[]) => void;
}) {
  const t = useTranslations("passport");
  return (
    <Card className="border-amber-300" data-testid="passport-discrepancies">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangle className="size-4 text-amber-600" /> {t("discrepancies")}
        </CardTitle>
        <p className="text-xs text-muted-foreground">{t("discrepanciesHint")}</p>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {items.map((d, i) => (
          <div key={`${d.field}-${i}`} className={cn("flex flex-col gap-2", i > 0 && "border-t pt-4")}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={SEVERITY_VARIANT[d.severity]}>{t(`severity.${d.severity}`)}</Badge>
              <span className="font-medium">{d.title}</span>
            </div>
            <p className="text-sm">{d.description}</p>
            <ul className="flex flex-col gap-1 text-sm">
              {d.values.map((v, n) => (
                <li key={n} className="flex flex-wrap items-start gap-3">
                  <span className="min-w-32 font-medium">{v.value}</span>
                  <SourceLinks refs={v.refs} sources={sources} onOpenSource={onOpenSource} />
                </li>
              ))}
            </ul>
            {editing ? (
              <Textarea
                rows={2}
                value={d.resolution}
                placeholder={t("resolutionPlaceholder")}
                aria-label={t("resolution")}
                onChange={(e) => onChange(items.map((x, n) => (n === i ? { ...x, resolution: e.target.value } : x)))}
              />
            ) : (
              d.resolution && (
                <p className="text-sm">
                  <span className="text-muted-foreground">{t("resolution")}:</span> {d.resolution}
                </p>
              )
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
