"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Download, Eye, FileText, FolderUp, RotateCw, Trash2, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useMemo, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";

import { CompletenessPanel } from "@/components/documents/completeness-panel";
import { DocumentPreview } from "@/components/documents/document-preview";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { API_PREFIX, api, errorMessage } from "@/lib/api";
import { useCompleteness, useDocuments } from "@/lib/hooks";
import type { DocumentStatus, ProjectDocument } from "@/lib/types";
import { relativeDir, uploadFile } from "@/lib/upload";
import { cn } from "@/lib/utils";

interface QueueItem {
  key: string;
  name: string;
  loaded: number;
  total: number;
  error?: string;
  done?: boolean;
}

const STATUS_VARIANT: Record<DocumentStatus, "muted" | "default" | "secondary" | "success" | "destructive"> = {
  uploaded: "muted",
  processing: "default",
  recognized: "secondary",
  indexed: "success",
  extracted: "secondary",
  error: "destructive",
};

const PARALLEL_FILES = 3;

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} КБ`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
  return `${(bytes / 1024 ** 3).toFixed(2)} ГБ`;
}

export function DocumentsTab({
  projectId,
  canDelete,
  initialPreview,
}: {
  projectId: string;
  canDelete: boolean;
  initialPreview?: { docId: string; page: number; q?: string };
}) {
  const t = useTranslations("documents");
  const queryClient = useQueryClient();
  const docs = useDocuments(projectId);
  const docsVersion = useMemo(
    () => (docs.data ?? []).map((d) => `${d.id}:${d.status}:${d.category}`).join("|"),
    [docs.data],
  );
  const completeness = useCompleteness(projectId, docsVersion);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [preview, setPreview] = useState(initialPreview ?? null);
  const [error, setError] = useState<string>();
  const folderInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
    queryClient.invalidateQueries({ queryKey: ["project", projectId] });
  }, [queryClient, projectId]);

  const startUpload = useCallback(
    async (files: File[]) => {
      const items = files.map((f, i) => ({ key: `${Date.now()}-${i}-${f.name}`, name: f.name, loaded: 0, total: f.size }));
      setQueue((q) => [...q.filter((x) => !x.done), ...items]);
      const update = (key: string, patch: Partial<QueueItem>) =>
        setQueue((q) => q.map((x) => (x.key === key ? { ...x, ...patch } : x)));
      let next = 0;
      const worker = async () => {
        while (next < files.length) {
          const i = next++;
          const { key } = items[i];
          try {
            await uploadFile(projectId, files[i], relativeDir(files[i]), (p) => update(key, { loaded: p.loaded }));
            update(key, { done: true, loaded: files[i].size });
            refresh();
          } catch (e) {
            update(key, { error: errorMessage(e) });
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(PARALLEL_FILES, files.length) }, worker));
    },
    [projectId, refresh],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop: (accepted) => accepted.length && startUpload(accepted),
    noClick: true,
  });

  const setCategory = useMutation({
    mutationFn: ({ id, category }: { id: string; category: string }) =>
      api.patch(`/projects/${projectId}/documents/${id}`, { category }),
    onSuccess: refresh,
    onError: (e) => setError(errorMessage(e)),
  });
  const reprocess = useMutation({
    mutationFn: (id: string) => api.post(`/projects/${projectId}/documents/${id}/reprocess`),
    onSuccess: refresh,
    onError: (e) => setError(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/projects/${projectId}/documents/${id}`),
    onSuccess: refresh,
    onError: (e) => setError(errorMessage(e)),
  });

  const categories = completeness.data?.categories ?? [];
  const categoryTitle = (code: string | null) => categories.find((c) => c.code === code)?.title ?? code ?? "—";
  const activeQueue = queue.filter((q) => !q.done || q.error);
  const list = docs.data ?? [];

  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_22rem]">
      <div className="flex min-w-0 flex-col gap-4">
        <div
          {...getRootProps()}
          className={cn(
            "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed bg-card px-6 py-8 text-center transition-colors",
            isDragActive && "border-primary bg-primary/5",
          )}
        >
          <input {...getInputProps()} data-testid="file-input" />
          <Upload className="size-8 text-muted-foreground" />
          <p className="font-medium">{isDragActive ? t("dropActive") : t("dropHere")}</p>
          <p className="max-w-xl text-xs text-muted-foreground">{t("formats")}</p>
          <div className="flex gap-2">
            <Button type="button" onClick={open}>
              <Upload /> {t("chooseFiles")}
            </Button>
            <Button type="button" variant="outline" onClick={() => folderInput.current?.click()}>
              <FolderUp /> {t("chooseFolder")}
            </Button>
            <input
              ref={folderInput}
              type="file"
              className="hidden"
              multiple
              {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []);
                e.target.value = "";
                if (files.length) startUpload(files);
              }}
            />
          </div>
        </div>

        {activeQueue.length > 0 && (
          <Card>
            <CardContent className="flex flex-col gap-2 p-4">
              <p className="text-sm font-medium">{t("queue")}</p>
              {activeQueue.map((q) => (
                <div key={q.key} className="flex items-center gap-3 text-sm">
                  <span className="w-56 truncate" title={q.name}>
                    {q.name}
                  </span>
                  {q.error ? (
                    <span className="text-destructive">
                      {t("uploadFailed")}: {q.error}
                    </span>
                  ) : (
                    <>
                      <div className="h-2 flex-1 overflow-hidden rounded bg-muted">
                        <div className="h-full bg-primary transition-all" style={{ width: `${(q.loaded / Math.max(1, q.total)) * 100}%` }} />
                      </div>
                      <span className="w-20 text-right text-xs text-muted-foreground">{formatSize(q.loaded)}</span>
                    </>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {error && (
          <Alert variant="destructive" onClick={() => setError(undefined)} className="cursor-pointer">
            {error}
          </Alert>
        )}

        <Card>
          {list.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted-foreground">{t("empty")}</CardContent>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("col.file")}</TableHead>
                  <TableHead>{t("col.category")}</TableHead>
                  <TableHead>{t("col.status")}</TableHead>
                  <TableHead className="text-right">{t("col.pages")}</TableHead>
                  <TableHead className="text-right">{t("col.size")}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <DocumentRow
                    key={d.id}
                    doc={d}
                    projectId={projectId}
                    categories={categories}
                    categoryTitle={categoryTitle}
                    canDelete={canDelete}
                    onPreview={() => setPreview({ docId: d.id, page: 1 })}
                    onCategory={(category) => setCategory.mutate({ id: d.id, category })}
                    onReprocess={() => reprocess.mutate(d.id)}
                    onDelete={() => window.confirm(t("confirmDelete")) && remove.mutate(d.id)}
                  />
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </div>

      <CompletenessPanel data={completeness.data} />

      {preview && (
        <DocumentPreview
          projectId={projectId}
          doc={list.find((d) => d.id === preview.docId)}
          initialPage={preview.page}
          initialQuery={preview.q}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

function DocumentRow({
  doc,
  projectId,
  categories,
  categoryTitle,
  canDelete,
  onPreview,
  onCategory,
  onReprocess,
  onDelete,
}: {
  doc: ProjectDocument;
  projectId: string;
  categories: { code: string; title: string }[];
  categoryTitle: (code: string | null) => string;
  canDelete: boolean;
  onPreview: () => void;
  onCategory: (code: string) => void;
  onReprocess: () => void;
  onDelete: () => void;
}) {
  const t = useTranslations("documents");
  const isContainer = doc.status === "extracted";
  const ready = doc.status === "recognized" || doc.status === "indexed";
  const folder = doc.relative_path && doc.relative_path !== doc.filename ? doc.relative_path.replace(/\/[^/]*$/, "") : "";
  return (
    <TableRow className={cn(doc.parent_id && "bg-muted/30")}>
      <TableCell className="max-w-80">
        <div className="flex items-start gap-2">
          {isContainer ? (
            <Archive className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          ) : (
            <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0">
            <p className="truncate font-medium" title={doc.filename}>
              {doc.filename}
            </p>
            {(folder || doc.parent_id) && (
              <p className="truncate text-xs text-muted-foreground" title={doc.relative_path}>
                {doc.parent_id ? `${t("fromArchive")}: ` : ""}
                {folder}
              </p>
            )}
            {doc.error && <p className="text-xs text-destructive">{doc.error}</p>}
            {typeof doc.meta?.warning === "string" && <p className="text-xs text-amber-700">{doc.meta.warning}</p>}
          </div>
        </div>
      </TableCell>
      <TableCell className="min-w-52">
        {isContainer ? (
          <span className="text-xs text-muted-foreground">—</span>
        ) : (
          <div className="flex flex-col gap-1">
            <Select
              aria-label={t("col.category")}
              value={doc.category ?? ""}
              disabled={!ready}
              onChange={(e) => onCategory(e.target.value)}
              className="h-8 text-xs"
            >
              {!doc.category && <option value="">—</option>}
              {categories.map((c) => (
                <option key={c.code} value={c.code} title={c.title}>
                  {c.title}
                </option>
              ))}
              {doc.category && !categories.some((c) => c.code === doc.category) && (
                <option value={doc.category}>{categoryTitle(doc.category)}</option>
              )}
            </Select>
            {doc.category_source && (
              <span className="text-[11px] text-muted-foreground">
                {t(`source.${doc.category_source}`)}
                {doc.category_source !== "user" && doc.category_confidence !== null
                  ? ` · ${Math.round(doc.category_confidence * 100)}%`
                  : ""}
              </span>
            )}
          </div>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-1">
          <Badge variant={STATUS_VARIANT[doc.status]} data-testid="doc-status">
            {t(`status.${doc.status}`)}
          </Badge>
          {doc.ocr_pages > 0 && <span className="text-[11px] text-muted-foreground">{t("ocr", { n: doc.ocr_pages })}</span>}
        </div>
      </TableCell>
      <TableCell className="text-right">{doc.pages ?? "—"}</TableCell>
      <TableCell className="text-right whitespace-nowrap">{formatSize(doc.size)}</TableCell>
      <TableCell>
        <div className="flex justify-end">
          {ready && (
            <Button variant="ghost" size="icon" title={t("preview")} aria-label={t("preview")} onClick={onPreview}>
              <Eye />
            </Button>
          )}
          <Button variant="ghost" size="icon" asChild title={t("download")}>
            <a href={`${API_PREFIX}/projects/${projectId}/documents/${doc.id}/file`} aria-label={t("download")}>
              <Download />
            </a>
          </Button>
          {doc.status === "error" && (
            <Button variant="ghost" size="icon" title={t("reprocess")} aria-label={t("reprocess")} onClick={onReprocess}>
              <RotateCw />
            </Button>
          )}
          {canDelete && (
            <Button variant="ghost" size="icon" title={t("delete")} aria-label={t("delete")} onClick={onDelete}>
              <Trash2 />
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}
