"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BadgeCheck, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ReviewControls } from "@/components/risks/risks-tab";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import type {
  Coverage,
  CoverageRow,
  ReportVersion,
  ReviewStatus,
} from "@/lib/types";

/** Матрица покрытия (TZ §4.5 Е): пункт/риск/выводы → статус → источники → эксперт → утверждён; валидатор. */
export function CoverageTab({
  projectId,
  canReview,
  canRelease,
  onReleased,
}: {
  projectId: string;
  canReview: boolean;
  canRelease: boolean;
  onReleased: () => void;
}) {
  const t = useTranslations("coverage");
  const tr = useTranslations("assessment");
  const queryClient = useQueryClient();
  const [onlyOpen, setOnlyOpen] = useState(false);
  const coverage = useQuery({
    queryKey: ["coverage", projectId],
    queryFn: () => api.get<Coverage>(`/projects/${projectId}/coverage`),
  });
  const review = useMutation({
    mutationFn: ({
      row,
      status,
    }: {
      row: CoverageRow;
      status: ReviewStatus;
    }) => {
      const url =
        row.kind === "item"
          ? `assessment/items/${row.ref}/review`
          : row.kind === "risk"
            ? `risks/${row.ref}/review`
            : "conclusions/review";
      return api.put(`/projects/${projectId}/${url}`, { status });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["coverage", projectId] });
      queryClient.invalidateQueries({ queryKey: ["risks", projectId] });
      queryClient.invalidateQueries({ queryKey: ["assessment", projectId] });
    },
  });
  const release = useMutation({
    mutationFn: () =>
      api.post<ReportVersion>(`/projects/${projectId}/reports`, {
        release: true,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reports", projectId] });
      onReleased();
    },
  });

  if (!coverage.data) return null;
  const c = coverage.data;
  const errors = c.issues.filter((i) => i.level === "error");
  const warnings = c.issues.filter((i) => i.level === "warning");
  const rows = onlyOpen
    ? c.rows.filter((r) => r.review_status !== "approved")
    : c.rows;

  return (
    <div className="flex flex-col gap-4" data-testid="coverage">
      <Card>
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <h2 className="text-lg font-semibold">{t("title")}</h2>
              <p
                className="text-sm text-muted-foreground"
                data-testid="approved-count"
              >
                {t("approved", { n: c.approved, total: c.total })}
              </p>
            </div>
            {canRelease && (
              <Button
                onClick={() => release.mutate()}
                disabled={!c.can_release || release.isPending}
              >
                <BadgeCheck /> {t("release")}
              </Button>
            )}
          </div>
          {release.error && (
            <Alert variant="destructive">{errorMessage(release.error)}</Alert>
          )}
          {c.can_release ? (
            <Alert variant="success">{t("ready")}</Alert>
          ) : (
            <div className="flex flex-col gap-1" data-testid="issues">
              <p className="text-sm font-medium">
                {t("errors", { n: errors.length })}
              </p>
              <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto text-sm">
                {errors.map((i, n) => (
                  <li key={n} className="flex gap-2">
                    <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />{" "}
                    {i.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {warnings.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted-foreground">
                {t("warnings", { n: warnings.length })}
              </summary>
              <ul className="mt-1 flex flex-col gap-1">
                {warnings.map((i, n) => (
                  <li key={n} className="flex gap-2">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />{" "}
                    {i.message}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {review.error && (
            <Alert variant="destructive">{errorMessage(review.error)}</Alert>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">{t("matrix")}</CardTitle>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={onlyOpen}
              onChange={(e) => setOnlyOpen(e.target.checked)}
            />
            {t("onlyOpen")}
          </label>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">{t("col.ref")}</th>
                <th className="pr-2">{t("col.title")}</th>
                <th className="pr-2">{t("col.status")}</th>
                <th className="pr-2">{t("col.sources")}</th>
                <th className="pr-2">{t("col.experts")}</th>
                <th>{t("col.review")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={`${r.kind}-${r.ref}`}
                  className="border-t align-top"
                  data-testid={`row-${r.ref}`}
                >
                  <td className="py-2 pr-2 font-mono text-xs">{r.ref}</td>
                  <td className="pr-2">{r.title}</td>
                  <td className="pr-2">
                    {r.disclosure_status ? (
                      <Badge variant="muted">
                        {tr(`disclosure.${r.disclosure_status}`)}
                      </Badge>
                    ) : (
                      <Badge
                        variant={r.status === "done" ? "muted" : "destructive"}
                      >
                        {t(`state.${r.status}`)}
                      </Badge>
                    )}
                  </td>
                  <td className="pr-2">
                    {r.kind === "risk" ? "—" : r.sources}
                  </td>
                  <td className="pr-2 text-xs">
                    {r.experts.join(", ") || "—"}
                  </td>
                  <td>
                    <ReviewControls
                      status={r.review_status}
                      canReview={canReview && r.status === "done"}
                      onReview={(status) => review.mutate({ row: r, status })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
