"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  FileText,
  Loader2,
  Play,
  RotateCw,
  XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { Fragment, type ReactNode, useEffect, useMemo, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { API_PREFIX, api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  useAssessment,
  useAssessmentEstimate,
  useAssessmentItem,
} from "@/lib/hooks";
import type {
  Assessment,
  AssessmentItem,
  AssessmentRun,
  AssessmentSource,
  DisclosureStatus,
  ItemStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export type OpenSource = (docId: string, page: number, quote?: string) => void;

const DISCLOSURE_VARIANT: Record<
  DisclosureStatus,
  "success" | "warning" | "destructive" | "muted"
> = {
  full: "success",
  partial: "warning",
  not_disclosed: "destructive",
  not_applicable: "muted",
};

const ACTIVE = ["queued", "running"];

/** Прогресс прогона: поток событий SSE обновляет кэш запроса; при обрыве работает запасной опрос. */
function useRunEvents(
  projectId: string,
  run: AssessmentRun | null | undefined,
) {
  const queryClient = useQueryClient();
  const runId = run && ACTIVE.includes(run.status) ? run.id : null;
  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") return;
    const source = new EventSource(
      `${API_PREFIX}/projects/${projectId}/runs/${runId}/events`,
    );
    source.addEventListener("progress", (e) => {
      queryClient.setQueryData(
        ["assessment", projectId],
        JSON.parse((e as MessageEvent).data) as Assessment,
      );
    });
    source.addEventListener("end", () => {
      source.close();
      queryClient.invalidateQueries({ queryKey: ["assessment", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    });
    source.onerror = () => source.close(); // запасной опрос в useAssessment
    return () => source.close();
  }, [projectId, runId, queryClient]);
}

export function AssessmentTab({
  projectId,
  canRun,
  canRerun,
  onOpenSource,
}: {
  projectId: string;
  canRun: boolean;
  canRerun: boolean;
  onOpenSource: OpenSource;
}) {
  const t = useTranslations("assessment");
  const queryClient = useQueryClient();
  const assessment = useAssessment(projectId);
  const [confirming, setConfirming] = useState(false);
  const estimate = useAssessmentEstimate(projectId, confirming);
  const [selected, setSelected] = useState<string | null>(null);
  useRunEvents(projectId, assessment.data?.run);

  const start = useMutation({
    mutationFn: () => api.post<AssessmentRun>(`/projects/${projectId}/runs`),
    onSuccess: () => {
      setConfirming(false);
      queryClient.invalidateQueries({ queryKey: ["assessment", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const groups = useMemo(() => {
    const out: { id: string; title: string; items: AssessmentItem[] }[] = [];
    for (const item of assessment.data?.items ?? []) {
      const last = out[out.length - 1];
      if (last && last.id === item.section_id) last.items.push(item);
      else
        out.push({
          id: item.section_id,
          title: item.section_title,
          items: [item],
        });
    }
    return out;
  }, [assessment.data]);

  if (!assessment.data) return null;
  const { run, items } = assessment.data;
  const active = run !== null && ACTIVE.includes(run.status);
  const current = items.find((i) => i.item_id === selected) ?? null;

  return (
    <div className="flex flex-col gap-4" data-testid="assessment">
      <Card>
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">{t("title")}</h2>
                {run && (
                  <Badge
                    variant={
                      run.status === "done"
                        ? "success"
                        : run.status === "failed"
                          ? "destructive"
                          : "default"
                    }
                    data-testid="run-status"
                  >
                    {t(`runStatus.${run.status}`)}
                  </Badge>
                )}
              </div>
              {run && (
                <p className="text-xs text-muted-foreground">
                  {formatDate(run.started_at)} ·{" "}
                  {t("cost", { cost: Number(run.cost_usd).toFixed(2) })}
                </p>
              )}
            </div>
            {canRun && !active && !confirming && (
              <Button
                variant={run ? "outline" : "default"}
                onClick={() => setConfirming(true)}
              >
                <Play /> {run ? t("restart") : t("start")}
              </Button>
            )}
          </div>
          {!run && !confirming && (
            <p className="text-sm text-muted-foreground">{t("intro")}</p>
          )}
          {confirming && (
            <ConfirmRun
              estimate={estimate.data}
              restart={Boolean(run)}
              pending={start.isPending}
              error={start.error}
              onConfirm={() => start.mutate()}
              onCancel={() => setConfirming(false)}
            />
          )}
          {run && <RunProgress run={run} />}
          {run?.error && <Alert variant="destructive">{run.error}</Alert>}
        </CardContent>
      </Card>

      {items.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-[minmax(320px,420px)_1fr]">
          <Card className="h-fit">
            <CardContent className="flex flex-col gap-3 p-3">
              {groups.map((g) => (
                <div key={g.id} className="flex flex-col">
                  <p className="px-2 py-1 text-xs font-semibold text-muted-foreground uppercase">
                    {g.title}
                  </p>
                  {g.items.map((item) => (
                    <button
                      key={item.item_id}
                      type="button"
                      onClick={() => setSelected(item.item_id)}
                      data-testid={`item-${item.item_id}`}
                      className={cn(
                        "flex items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                        selected === item.item_id && "bg-muted",
                      )}
                    >
                      <StatusIcon status={item.status} />
                      <span className="flex-1">
                        <span className="font-medium">{item.item_id}</span>{" "}
                        {item.title}
                      </span>
                      {item.disclosure_status && (
                        <Badge
                          variant={DISCLOSURE_VARIANT[item.disclosure_status]}
                          className="shrink-0"
                        >
                          {t(`disclosure.${item.disclosure_status}`)}
                        </Badge>
                      )}
                    </button>
                  ))}
                </div>
              ))}
            </CardContent>
          </Card>
          {current ? (
            <ItemPanel
              projectId={projectId}
              item={current}
              canRerun={
                canRerun &&
                !(current.status === "pending" || current.status === "running")
              }
              onOpenSource={onOpenSource}
            />
          ) : (
            <Card className="h-fit">
              <CardContent className="p-6 text-sm text-muted-foreground">
                {t("selectItem")}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ItemStatus }) {
  const t = useTranslations("assessment");
  const props = {
    className: "mt-0.5 size-4 shrink-0",
    "aria-label": t(`itemStatus.${status}`),
  };
  if (status === "done")
    return (
      <CheckCircle2
        {...props}
        className={cn(props.className, "text-emerald-600")}
      />
    );
  if (status === "failed")
    return (
      <XCircle {...props} className={cn(props.className, "text-red-600")} />
    );
  if (status === "running")
    return (
      <Loader2
        {...props}
        className={cn(props.className, "animate-spin text-primary")}
      />
    );
  return (
    <CircleDashed
      {...props}
      className={cn(props.className, "text-muted-foreground")}
    />
  );
}

function ConfirmRun({
  estimate,
  restart,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  estimate: import("@/lib/types").AssessmentEstimate | undefined;
  restart: boolean;
  pending: boolean;
  error: unknown;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("assessment");
  if (!estimate) return <p className="text-sm text-muted-foreground">…</p>;
  return (
    <div
      className="flex flex-col gap-3 rounded-md border p-4"
      data-testid="run-confirm"
    >
      <p className="font-medium">{t("confirmTitle")}</p>
      <ul className="text-sm">
        <li>
          {t("confirmItems", {
            ai: estimate.items_ai,
            info: estimate.items_info,
          })}
        </li>
        <li>
          {t("confirmCost", {
            min: Number(estimate.cost_usd_min).toFixed(0),
            max: Number(estimate.cost_usd_max).toFixed(0),
          })}
        </li>
        <li>{t("confirmTime", { minutes: estimate.minutes })}</li>
        <li>
          {t("confirmWeb", { state: estimate.web_search ? t("on") : t("off") })}
        </li>
      </ul>
      {estimate.warnings.length > 0 && (
        <Alert variant="warning">
          <ul className="list-disc pl-5 text-sm">
            {estimate.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Alert>
      )}
      {restart && (
        <p className="text-xs text-muted-foreground">{t("confirmRestart")}</p>
      )}
      {error ? (
        <Alert variant="destructive">{errorMessage(error)}</Alert>
      ) : null}
      <div className="flex gap-2">
        <Button
          onClick={onConfirm}
          disabled={pending || !estimate.passport_confirmed}
        >
          <Play /> {t("confirmGo")}
        </Button>
        <Button variant="outline" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>
      {!estimate.passport_confirmed && (
        <p className="text-sm text-destructive">{t("needPassport")}</p>
      )}
    </div>
  );
}

function RunProgress({ run }: { run: AssessmentRun }) {
  const t = useTranslations("assessment");
  const finished = run.items_done + run.items_failed;
  const pct = run.items_total
    ? Math.round((finished / run.items_total) * 100)
    : 0;
  return (
    <div className="flex flex-col gap-1" data-testid="run-progress">
      <div className="flex h-2 overflow-hidden rounded bg-muted">
        <div
          className="bg-emerald-500 transition-all"
          style={{
            width: `${(run.items_done / Math.max(1, run.items_total)) * 100}%`,
          }}
        />
        <div
          className="bg-red-400 transition-all"
          style={{
            width: `${(run.items_failed / Math.max(1, run.items_total)) * 100}%`,
          }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {t("progress", { done: finished, total: run.items_total })} ({pct}%)
        {run.items_failed > 0 &&
          ` · ${t("failedCount", { n: run.items_failed })}`}
      </p>
    </div>
  );
}

function sourceLabel(
  s: AssessmentSource,
  t: ReturnType<typeof useTranslations>,
): string {
  if (s.type === "doc" && s.filename) {
    return `${s.filename}, ${s.sheet ? t("sheet", { name: s.sheet }) : t("page", { n: s.page ?? 1 })}`;
  }
  if (s.type === "web")
    return `${s.title || s.url}${s.accessed_at ? `, ${t("accessed", { date: formatDate(s.accessed_at) })}` : ""}`;
  return s.title;
}

function ItemPanel({
  projectId,
  item,
  canRerun,
  onOpenSource,
}: {
  projectId: string;
  item: AssessmentItem;
  canRerun: boolean;
  onOpenSource: OpenSource;
}) {
  const t = useTranslations("assessment");
  const queryClient = useQueryClient();
  const detail = useAssessmentItem(
    projectId,
    item.item_id,
    item.version * 10 +
      ["pending", "running", "done", "failed"].indexOf(item.status),
  );
  const [instruction, setInstruction] = useState("");
  const rerun = useMutation({
    mutationFn: () =>
      api.post(
        `/projects/${projectId}/assessment/items/${item.item_id}/rerun`,
        { instruction },
      ),
    onSuccess: () => {
      setInstruction("");
      queryClient.invalidateQueries({ queryKey: ["assessment", projectId] });
    },
  });
  const sources = useMemo(
    () => new Map((detail.data?.sources ?? []).map((s) => [s.sid, s])),
    [detail.data],
  );

  const openSource = (sid: string) => {
    const s = sources.get(sid);
    if (!s) return;
    if (s.type === "doc" && s.document_id)
      onOpenSource(s.document_id, s.page ?? 1);
    else if (s.url) window.open(s.url, "_blank", "noopener,noreferrer");
  };
  const cite = (text: string) => (
    <Cited text={text} sources={sources} onOpen={openSource} />
  );
  const p = detail.data?.payload;

  return (
    <Card className="h-fit" data-testid="item-panel">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {item.item_id} {item.title}
          {item.disclosure_status && (
            <Badge variant={DISCLOSURE_VARIANT[item.disclosure_status]}>
              {t(`disclosure.${item.disclosure_status}`)}
            </Badge>
          )}
          <Badge variant="muted">
            {item.kind === "info" ? t("fromProject") : t("aiDraft")}
          </Badge>
          {item.version > 1 && (
            <Badge variant="outline">{t("version", { n: item.version })}</Badge>
          )}
        </CardTitle>
        {item.instruction && (
          <p className="text-xs text-muted-foreground">
            {t("instruction", { text: item.instruction })}
          </p>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {item.status === "failed" && (
          <Alert variant="destructive">
            {t("error")}: {item.error}
          </Alert>
        )}
        {p && (
          <>
            <Block title={t("summary")}>{cite(p.summary)}</Block>
            {p.analysis_md && (
              <Block title={t("analysis")}>
                <Markdown text={p.analysis_md} render={cite} />
              </Block>
            )}
            {p.tables.map((tbl, n) => (
              <Block key={n} title={tbl.title}>
                <Table>
                  <TableHeader>
                    <TableRow>
                      {tbl.columns.map((c, i) => (
                        <TableHead key={i}>{c}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tbl.rows.map((row, i) => (
                      <TableRow key={i}>
                        {row.map((cell, j) => (
                          <TableCell key={j} className="align-top">
                            {cite(cell)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Block>
            ))}
            <ListBlock
              title={t("findings")}
              items={p.findings.map(
                (f) =>
                  `${f.text} ${f.source_ids.map((s) => `[${s}]`).join(" ")}`,
              )}
              render={cite}
            />
            <ListBlock title={t("remarks")} items={p.remarks} render={cite} />
            <ListBlock
              title={t("recommendations")}
              items={p.recommendations}
              render={cite}
            />
            <ListBlock
              title={t("assumptions")}
              items={p.assumptions}
              render={cite}
            />
            {p.gap && (
              <Alert variant="warning">
                <p className="font-medium">{t("gap")}</p>
                <dl className="mt-1 grid gap-1">
                  {(
                    [
                      "reason",
                      "impact_on_conclusions",
                      "assumptions",
                      "recommendation",
                    ] as const
                  ).map((k) => (
                    <div key={k}>
                      <dt className="text-xs text-muted-foreground">
                        {t(
                          k === "reason"
                            ? "gapReason"
                            : k === "impact_on_conclusions"
                              ? "gapImpact"
                              : k === "assumptions"
                                ? "gapAssumptions"
                                : "gapRecommendation",
                        )}
                      </dt>
                      <dd>{p.gap![k]}</dd>
                    </div>
                  ))}
                </dl>
              </Alert>
            )}
            {detail.data && detail.data.sources.length > 0 && (
              <Block title={t("sources")}>
                <ul className="flex flex-col gap-1" data-testid="item-sources">
                  {detail.data.sources.map((s) => (
                    <li key={s.sid} className="flex items-start gap-2">
                      <span className="font-mono text-xs text-muted-foreground">
                        [{s.sid}]
                      </span>
                      <button
                        type="button"
                        onClick={() => openSource(s.sid)}
                        className="inline-flex items-center gap-1 text-left text-primary hover:underline"
                        disabled={s.type === "project"}
                      >
                        {s.type === "web" ? (
                          <ExternalLink className="size-3" />
                        ) : (
                          <FileText className="size-3" />
                        )}
                        {sourceLabel(s, t)}
                      </button>
                    </li>
                  ))}
                </ul>
              </Block>
            )}
            {detail.data && detail.data.warnings.length > 0 && (
              <Alert variant="warning">
                <p className="flex items-center gap-1 font-medium">
                  <AlertTriangle className="size-4" /> {t("warnings")}
                </p>
                <ul className="list-disc pl-5">
                  {detail.data.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </Alert>
            )}
            <p className="text-xs text-muted-foreground">
              {t("confidence", { pct: Math.round(p.confidence * 100) })}
            </p>
          </>
        )}
        {canRerun && item.kind !== "info" && (
          <div className="flex flex-col gap-2 border-t pt-4">
            <Textarea
              rows={2}
              value={instruction}
              placeholder={t("rerunPlaceholder")}
              onChange={(e) => setInstruction(e.target.value)}
              aria-label={t("rerun")}
            />
            <Button
              variant="outline"
              className="self-start"
              onClick={() => rerun.mutate()}
              disabled={rerun.isPending}
            >
              <RotateCw /> {t("rerunGo")}
            </Button>
            {rerun.error ? (
              <Alert variant="destructive">{errorMessage(rerun.error)}</Alert>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase">
        {title}
      </h3>
      <div>{children}</div>
    </section>
  );
}

function ListBlock({
  title,
  items,
  render,
}: {
  title: string;
  items: string[];
  render: (s: string) => ReactNode;
}) {
  if (items.length === 0) return null;
  return (
    <Block title={title}>
      <ul className="list-disc pl-5">
        {items.map((x, i) => (
          <li key={i}>{render(x)}</li>
        ))}
      </ul>
    </Block>
  );
}

/** Текст со ссылками [S12] → кнопки-источники; **жирный** — жирным. Без HTML-вставок. */
function Cited({
  text,
  sources,
  onOpen,
}: {
  text: string;
  sources: Map<string, AssessmentSource>;
  onOpen: (sid: string) => void;
}) {
  const parts = text.split(/(\[S\d+\]|\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) => {
        const ref = /^\[(S\d+)\]$/.exec(part);
        if (ref) {
          const s = sources.get(ref[1]);
          return (
            <button
              key={i}
              type="button"
              onClick={() => onOpen(ref[1])}
              title={s ? s.title : undefined}
              className="mx-0.5 rounded bg-primary/10 px-1 font-mono text-xs text-primary hover:bg-primary/20"
            >
              {ref[1]}
            </button>
          );
        }
        if (part.startsWith("**") && part.endsWith("**"))
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

/** Упрощённый Markdown: заголовки ###, списки - / 1., абзацы. */
function Markdown({
  text,
  render,
}: {
  text: string;
  render: (s: string) => ReactNode;
}) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="flex flex-col gap-2">
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length === 0) return null;
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
          return (
            <ul key={i} className="list-disc pl-5">
              {lines.map((l, j) => (
                <li key={j}>{render(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>
              ))}
            </ul>
          );
        }
        const heading = /^#{1,4}\s+(.*)$/.exec(lines[0]);
        if (heading) {
          return (
            <Fragment key={i}>
              <p className="font-semibold">{render(heading[1])}</p>
              {lines.length > 1 && (
                <p className="whitespace-pre-line">
                  {render(lines.slice(1).join("\n"))}
                </p>
              )}
            </Fragment>
          );
        }
        return (
          <p key={i} className="whitespace-pre-line">
            {render(lines.join("\n"))}
          </p>
        );
      })}
    </div>
  );
}
