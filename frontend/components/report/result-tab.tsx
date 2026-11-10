"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileDown, FileText, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_PREFIX, api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { ReportVersion } from "@/lib/types";

const BUSY = ["queued", "rendering"];

/** Результат (TZ §4.5 Ж): черновики отчёта DOCX/PDF по последнему прогону оценки. */
export function ResultTab({
  projectId,
  canGenerate,
}: {
  projectId: string;
  canGenerate: boolean;
}) {
  const t = useTranslations("report");
  const queryClient = useQueryClient();
  const reports = useQuery({
    queryKey: ["reports", projectId],
    queryFn: () => api.get<ReportVersion[]>(`/projects/${projectId}/reports`),
    refetchInterval: (q) =>
      q.state.data?.some((r) => BUSY.includes(r.render_status)) ? 3000 : false,
  });
  const create = useMutation({
    mutationFn: () => api.post<ReportVersion>(`/projects/${projectId}/reports`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["reports", projectId] }),
  });
  const busy =
    reports.data?.some((r) => BUSY.includes(r.render_status)) ?? false;
  const href = (r: ReportVersion, fmt: "docx" | "pdf") =>
    `${API_PREFIX}/projects/${projectId}/reports/${r.id}/download?fmt=${fmt}`;

  return (
    <Card data-testid="result">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle>{t("title")}</CardTitle>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("hint")}</p>
        </div>
        {canGenerate && (
          <Button
            onClick={() => create.mutate()}
            disabled={create.isPending || busy}
          >
            {create.isPending || busy ? (
              <Loader2 className="animate-spin" />
            ) : (
              <FileText />
            )}
            {t("generate")}
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {create.error && (
          <Alert variant="destructive">{errorMessage(create.error)}</Alert>
        )}
        {reports.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        )}
        {reports.data?.map((r) => (
          <div
            key={r.id}
            className="flex flex-wrap items-center gap-3 rounded-md border p-3"
            data-testid="report-version"
          >
            <span className="font-medium">v{r.version}</span>
            <Badge variant={r.status === "released" ? "success" : "muted"}>
              {t(`status.${r.status}`)}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {formatDate(r.created_at)}
            </span>
            {BUSY.includes(r.render_status) && (
              <span className="flex items-center gap-1 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {t("rendering")}
              </span>
            )}
            {r.render_status === "failed" && (
              <span className="text-sm text-destructive">
                {t("failed", { error: r.error ?? "" })}
              </span>
            )}
            {r.render_status === "ready" && (
              <div className="ml-auto flex gap-2">
                {r.has_docx && (
                  <Button asChild variant="outline" size="sm">
                    <a href={href(r, "docx")} download>
                      <FileDown /> DOCX
                    </a>
                  </Button>
                )}
                {r.has_pdf && (
                  <Button asChild variant="outline" size="sm">
                    <a href={href(r, "pdf")} download>
                      <FileDown /> PDF
                    </a>
                  </Button>
                )}
              </div>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
