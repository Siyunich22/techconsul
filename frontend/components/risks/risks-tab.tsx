"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Play } from "lucide-react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";

import { Cited, Markdown } from "@/components/assessment/assessment-tab";
import { Comments } from "@/components/review/comments";
import { ItemEditor, type ItemEditBody } from "@/components/review/item-editor";
import { ReviewControls } from "@/components/review/review-controls";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ReviewStatus, Risk, Risks, Zone } from "@/lib/types";
import { cn } from "@/lib/utils";

const BUSY = ["queued", "running"];
export const ZONE_CLASS: Record<Zone, string> = {
  red: "bg-red-200 text-red-950",
  yellow: "bg-amber-100 text-amber-950",
  green: "bg-emerald-100 text-emerald-950",
};
const ZONE_BADGE: Record<Zone, "destructive" | "warning" | "success"> = {
  red: "destructive",
  yellow: "warning",
  green: "success",
};

type OpenSource = (docId: string, page: number, q?: string) => void;

interface RiskEditBody {
  probability: number;
  impact: number;
  rationale: string;
  mitigation: string[];
  note: string;
}

/** Риски (TZ §4.5 Д): реестр 15 обязательных + дополнительных рисков, тепловая карта, выводы раздела 5. */
export function RisksTab({
  projectId,
  canRun,
  canReview,
  onOpenSource,
}: {
  projectId: string;
  canRun: boolean;
  canReview: boolean;
  onOpenSource: OpenSource;
}) {
  const t = useTranslations("risks");
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [editingConclusions, setEditingConclusions] = useState(false);
  const key = ["risks", projectId];
  const risks = useQuery({
    queryKey: key,
    queryFn: () => api.get<Risks>(`/projects/${projectId}/risks`),
    refetchInterval: (q) =>
      q.state.data?.status && BUSY.includes(q.state.data.status) ? 4000 : false,
  });
  const onData = (data: Risks) => {
    queryClient.setQueryData(key, data);
    queryClient.invalidateQueries({ queryKey: ["coverage", projectId] });
  };
  const run = useMutation({
    mutationFn: () => api.post<Risks>(`/projects/${projectId}/risks/run`),
    onSuccess: (data) => {
      setConfirming(false);
      onData(data);
    },
  });
  const review = useMutation({
    mutationFn: ({
      url,
      status,
      version,
    }: {
      url: string;
      status: ReviewStatus;
      version?: number;
    }) => api.put<Risks>(`/projects/${projectId}/${url}`, { status, version }),
    onSuccess: onData,
  });
  const editRisk = useMutation({
    mutationFn: ({ code, body }: { code: string; body: RiskEditBody }) =>
      api.put<Risks>(`/projects/${projectId}/risks/${code}/edit`, body),
    onSuccess: onData,
  });
  const editConclusions = useMutation({
    mutationFn: (body: ItemEditBody) =>
      api.put<Risks>(`/projects/${projectId}/conclusions/edit`, body),
    onSuccess: (data) => {
      setEditingConclusions(false);
      onData(data);
    },
  });
  const sources = useMemo(
    () => new Map((risks.data?.sources ?? []).map((s) => [s.sid, s])),
    [risks.data],
  );

  if (!risks.data) return null;
  const d = risks.data;
  const busy = d.status !== null && BUSY.includes(d.status);
  const openSource = (sid: string) => {
    const s = sources.get(sid);
    if (s?.type === "doc" && s.document_id)
      onOpenSource(s.document_id, s.page ?? 1);
    else if (s?.url && /^https?:\/\//i.test(s.url))
      window.open(s.url, "_blank", "noopener,noreferrer");
  };
  const cite = (text: string) => (
    <Cited text={text} sources={sources} onOpen={openSource} />
  );
  const groups = Array.from(new Set(d.risks.map((r) => r.group))).map((g) => ({
    id: g,
    title: d.risks.find((r) => r.group === g)?.group_title ?? g,
    risks: d.risks.filter((r) => r.group === g),
  }));

  return (
    <div className="flex flex-col gap-4" data-testid="risks">
      <Card>
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">{t("title")}</h2>
                {d.integral.level && (
                  <Badge
                    variant={ZONE_BADGE[d.integral.level]}
                    data-testid="integral"
                  >
                    {t("integral", { level: t(`zone.${d.integral.level}`) })}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground">
                {d.integral.scored > 0
                  ? t("counts", {
                      scored: d.integral.scored,
                      total: d.integral.total,
                      red: d.integral.red,
                      yellow: d.integral.yellow,
                      green: d.integral.green,
                    })
                  : t("intro")}
              </p>
              {d.finished_at && !busy && (
                <p className="text-xs text-muted-foreground">
                  {formatDate(d.finished_at)}
                </p>
              )}
            </div>
            {canRun && !busy && !confirming && (
              <Button
                variant={d.status ? "outline" : "default"}
                onClick={() => setConfirming(true)}
              >
                <Play /> {d.status ? t("rerun") : t("run")}
              </Button>
            )}
            {busy && (
              <span className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {t("running")}
              </span>
            )}
          </div>
          {confirming && (
            <div
              className="flex flex-col gap-3 rounded-md border p-4"
              data-testid="risks-confirm"
            >
              <p className="text-sm">
                {t("confirm", {
                  min: Number(d.estimate_usd_min).toFixed(1),
                  max: Number(d.estimate_usd_max).toFixed(1),
                })}
              </p>
              {run.error && (
                <Alert variant="destructive">{errorMessage(run.error)}</Alert>
              )}
              <div className="flex gap-2">
                <Button onClick={() => run.mutate()} disabled={run.isPending}>
                  {t("start")}
                </Button>
                <Button variant="outline" onClick={() => setConfirming(false)}>
                  {t("cancel")}
                </Button>
              </div>
            </div>
          )}
          {d.error && (
            <Alert variant={d.status === "failed" ? "destructive" : "warning"}>
              {d.error}
            </Alert>
          )}
          {review.error ? (
            <Alert variant="destructive">{errorMessage(review.error)}</Alert>
          ) : null}
          {editRisk.error ? (
            <Alert variant="destructive">{errorMessage(editRisk.error)}</Alert>
          ) : null}
        </CardContent>
      </Card>

      {d.integral.scored > 0 && <Heatmap data={d} />}

      {groups.map((g) => (
        <Card key={g.id}>
          <CardHeader>
            <CardTitle className="text-base">
              {g.id} {g.title}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col divide-y">
            {g.risks.map((r) => (
              <RiskRow
                key={`${r.code}-${r.version}`}
                risk={r}
                data={d}
                cite={cite}
                canReview={canReview}
                projectId={projectId}
                onReview={(status) =>
                  review.mutate({
                    url: `risks/${r.code}/review`,
                    status,
                    version: r.version,
                  })
                }
                onEdit={(body) => editRisk.mutateAsync({ code: r.code, body })}
              />
            ))}
          </CardContent>
        </Card>
      ))}

      {d.conclusions && (
        <Card data-testid="conclusions">
          <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
            <CardTitle className="text-base">{t("conclusions")}</CardTitle>
            <span className="flex items-center gap-2">
              <ReviewControls
                status={d.conclusions_review}
                canReview={canReview}
                onReview={(status) =>
                  review.mutate({ url: "conclusions/review", status })
                }
              />
              {canReview && !editingConclusions && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditingConclusions(true)}
                >
                  <Pencil /> {t("edit")}
                </Button>
              )}
            </span>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            {editingConclusions ? (
              <ItemEditor
                payload={d.conclusions}
                sources={d.sources.map((x) => x.sid)}
                pending={editConclusions.isPending}
                error={editConclusions.error}
                onSave={(body) => editConclusions.mutate(body)}
                onCancel={() => setEditingConclusions(false)}
              />
            ) : (
              <>
                <p className="font-medium">{cite(d.conclusions.summary)}</p>
                <Markdown text={d.conclusions.analysis_md} render={cite} />
                {d.conclusions.tables.map((tbl, i) => (
                  <div key={i} className="overflow-x-auto">
                    <p className="mb-1 text-xs font-semibold text-muted-foreground">
                      {tbl.title}
                    </p>
                    <table className="w-full border text-xs">
                      <thead>
                        <tr>
                          {tbl.columns.map((c) => (
                            <th key={c} className="border px-2 py-1 text-left">
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {tbl.rows.map((row, j) => (
                          <tr key={j}>
                            {row.map((cell, k) => (
                              <td
                                key={k}
                                className="border px-2 py-1 align-top"
                              >
                                {cite(String(cell))}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </>
            )}
            <Comments projectId={projectId} refId="5" canComment={canReview} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Heatmap({ data }: { data: Risks }) {
  const t = useTranslations("risks");
  const ps = Object.keys(data.probability_scale)
    .map(Number)
    .sort((a, b) => b - a);
  const is = Object.keys(data.impact_scale)
    .map(Number)
    .sort((a, b) => a - b);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("heatmap")}</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="text-xs" data-testid="heatmap">
          <tbody>
            {ps.map((p) => (
              <tr key={p}>
                <th className="pr-2 text-right font-normal text-muted-foreground">
                  {data.probability_scale[p]}
                </th>
                {is.map((i) => {
                  const codes = data.risks
                    .filter((r) => r.probability === p && r.impact === i)
                    .map((r) => r.code);
                  return (
                    <td
                      key={i}
                      className={cn(
                        "h-12 w-36 border border-white text-center font-medium",
                        ZONE_CLASS[data.zone_matrix[`${p},${i}`]],
                      )}
                    >
                      {codes.join(", ")}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td />
              {is.map((i) => (
                <th key={i} className="pt-1 font-normal text-muted-foreground">
                  {data.impact_scale[i]}
                </th>
              ))}
            </tr>
          </tbody>
        </table>
        <p className="mt-2 text-xs text-muted-foreground">{t("axes")}</p>
      </CardContent>
    </Card>
  );
}

function RiskRow({
  risk,
  data,
  cite,
  canReview,
  projectId,
  onReview,
  onEdit,
}: {
  risk: Risk;
  data: Risks;
  cite: (s: string) => ReactNode;
  canReview: boolean;
  projectId: string;
  onReview: (status: ReviewStatus) => void;
  onEdit: (body: RiskEditBody) => Promise<unknown>;
}) {
  const t = useTranslations("risks");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    probability: risk.probability ?? 3,
    impact: risk.impact ?? 2,
    rationale: risk.rationale ?? "",
    mitigation: risk.mitigation.join("\n"),
  });
  const save = () =>
    onEdit({
      probability: form.probability,
      impact: form.impact,
      rationale: form.rationale,
      mitigation: form.mitigation
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
      note: "",
    }).then(() => setEditing(false));

  return (
    <div
      className="flex flex-col gap-2 py-3 text-sm"
      data-testid={`risk-${risk.code}`}
    >
      <div className="flex flex-wrap items-start gap-2">
        <span className="font-mono text-xs font-semibold">{risk.code}</span>
        <span className="flex-1 font-medium">
          {risk.title}
          {!risk.mandatory && (
            <span className="text-muted-foreground"> {t("extra")}</span>
          )}
        </span>
        {risk.zone && (
          <Badge variant={ZONE_BADGE[risk.zone]}>
            {t(`zone.${risk.zone}`)}
          </Badge>
        )}
        <ReviewControls
          status={risk.review_status}
          canReview={canReview && risk.status === "done"}
          onReview={onReview}
        />
        {canReview && risk.status === "done" && !editing && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setEditing(true)}
            title={t("edit")}
          >
            <Pencil />
          </Button>
        )}
      </div>
      {editing ? (
        <div
          className="flex flex-col gap-2 rounded-md border p-3"
          data-testid={`risk-editor-${risk.code}`}
        >
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-col gap-1 text-xs">
              {t("probability")}
              <Select
                value={form.probability}
                onChange={(e) =>
                  setForm({ ...form, probability: Number(e.target.value) })
                }
              >
                {Object.entries(data.probability_scale).map(([k, v]) => (
                  <option key={k} value={k}>
                    {k} — {v}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              {t("impact")}
              <Select
                value={form.impact}
                onChange={(e) =>
                  setForm({ ...form, impact: Number(e.target.value) })
                }
              >
                {Object.entries(data.impact_scale).map(([k, v]) => (
                  <option key={k} value={k}>
                    {k} — {v}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          <label className="flex flex-col gap-1 text-xs">
            {t("rationale")}
            <Textarea
              rows={4}
              value={form.rationale}
              onChange={(e) => setForm({ ...form, rationale: e.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            {t("mitigation")}
            <Textarea
              rows={3}
              value={form.mitigation}
              onChange={(e) => setForm({ ...form, mitigation: e.target.value })}
            />
          </label>
          <div className="flex gap-2">
            <Button size="sm" onClick={save}>
              {t("save")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(false)}
            >
              {t("cancel")}
            </Button>
          </div>
        </div>
      ) : risk.probability !== null && risk.impact !== null ? (
        <>
          <p className="text-xs text-muted-foreground">
            {t("scores", {
              p: data.probability_scale[risk.probability],
              i: data.impact_scale[risk.impact],
            })}
          </p>
          {risk.rationale && <p>{cite(risk.rationale)}</p>}
          {risk.mitigation.length > 0 && (
            <ul className="list-disc pl-5">
              {risk.mitigation.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {risk.error ?? t("notScored")}
        </p>
      )}
      {risk.status === "done" && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {t("discuss")}
          </summary>
          <Comments
            projectId={projectId}
            refId={risk.code}
            canComment={canReview}
          />
        </details>
      )}
    </div>
  );
}
