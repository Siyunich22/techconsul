"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Star, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { TemplateTree, type TreeNode } from "@/components/template-tree";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { API_PREFIX, ApiError, api, apiFetch, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useMe } from "@/lib/hooks";

interface Issue {
  level: "error" | "warning";
  path: string;
  message: string;
}

interface TemplateRow {
  id: string;
  code: string;
  title: string;
  is_default: boolean;
  created_at: string;
  projects: number;
  warnings: Issue[];
  summary: { items_total: number; items_optional: number; categories: number; mandatory_risks: number };
}

interface TemplateDetail {
  tree: TreeNode[];
  document_categories: Record<string, string>;
}

function Issues({ issues, testId }: { issues: Issue[]; testId?: string }) {
  const t = useTranslations("admin");
  return (
    <ul className="flex max-h-72 flex-col gap-1 overflow-auto text-xs" data-testid={testId}>
      {issues.map((i, n) => (
        <li key={n} className="flex gap-2">
          <Badge variant={i.level === "error" ? "destructive" : "warning"} className="shrink-0">
            {t(i.level)}
          </Badge>
          <code className="shrink-0 text-muted-foreground">{i.path}</code>
          <span>{i.message}</span>
        </li>
      ))}
    </ul>
  );
}

export default function TemplatesPage() {
  const t = useTranslations("admin");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const me = useMe();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [makeDefault, setMakeDefault] = useState(false);
  const [check, setCheck] = useState<{ valid: boolean; code: string | null; issues: Issue[] }>();
  const [openId, setOpenId] = useState<string>();

  const rows = useQuery({
    queryKey: ["admin-templates"],
    queryFn: () => api.get<TemplateRow[]>("/admin/templates"),
    enabled: me.data?.role === "admin",
  });
  const detail = useQuery({
    queryKey: ["template", openId],
    queryFn: () => api.get<TemplateDetail>(`/templates/${openId}`),
    enabled: Boolean(openId),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin-templates"] });

  const form = (extra: Record<string, string> = {}) => {
    const f = new FormData();
    if (file) f.append("file", file);
    for (const [k, v] of Object.entries(extra)) f.append(k, v);
    return f;
  };
  const validate = useMutation({
    mutationFn: () => apiFetch<typeof check>("/admin/templates/validate", { method: "POST", body: form() }),
    onSuccess: (data) => setCheck(data),
  });
  const upload = useMutation({
    mutationFn: () =>
      apiFetch<TemplateRow>("/admin/templates", { method: "POST", body: form({ make_default: String(makeDefault) }) }),
    onSuccess: () => {
      setFile(undefined);
      setCheck(undefined);
      if (fileRef.current) fileRef.current.value = "";
      refresh();
    },
  });
  const setDefault = useMutation({ mutationFn: (id: string) => api.post(`/admin/templates/${id}/default`), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => api.delete(`/admin/templates/${id}`), onSuccess: refresh });

  if (me.data && me.data.role !== "admin") return <Alert variant="destructive">403</Alert>;
  const uploadIssues =
    upload.error instanceof ApiError && typeof upload.error.detail === "object" && upload.error.detail
      ? ((upload.error.detail as { issues?: Issue[] }).issues ?? [])
      : [];

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("templates")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("templatesHint")}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("uploadTemplate")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <Field label={t("chooseYaml")} htmlFor="tpl-file" className="min-w-72 flex-1">
              <Input
                id="tpl-file"
                ref={fileRef}
                type="file"
                accept=".yaml,.yml"
                onChange={(e) => {
                  setFile(e.target.files?.[0]);
                  setCheck(undefined);
                }}
              />
            </Field>
            <Button variant="outline" disabled={!file || validate.isPending} onClick={() => validate.mutate()}>
              {t("check")}
            </Button>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} />
              {t("makeDefault")}
            </label>
            <Button disabled={!file || !check?.valid || upload.isPending} onClick={() => upload.mutate()}>
              {t("save")}
            </Button>
          </div>
          {check && (
            <Alert variant={check.valid ? "success" : "destructive"}>
              {check.valid ? t("valid", { code: check.code ?? "" }) : t("invalid")}
            </Alert>
          )}
          {check && check.issues.length > 0 && <Issues issues={check.issues} testId="check-issues" />}
          {upload.isError && <Alert variant="destructive">{uploadIssues.length ? t("invalid") : errorMessage(upload.error)}</Alert>}
          {uploadIssues.length > 0 && <Issues issues={uploadIssues} />}
        </CardContent>
      </Card>

      {rows.data?.map((r) => (
        <Card key={r.id}>
          <CardContent className="flex flex-col gap-3 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="flex items-center gap-2 font-semibold">
                  {r.title}
                  {r.is_default && <Badge variant="success">{t("default")}</Badge>}
                </p>
                <p className="text-xs text-muted-foreground">
                  <code>{r.code}</code> · {formatDate(r.created_at)} · {t("projects", { n: r.projects })}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("items", { n: r.summary.items_total })} · {t("optionalItems", { n: r.summary.items_optional })} ·{" "}
                  {t("risks", { n: r.summary.mandatory_risks })} · {t("categoriesCount", { n: r.summary.categories })}
                </p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setOpenId(openId === r.id ? undefined : r.id)}>
                  {t("structure")}
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <a href={`${API_PREFIX}/templates/${r.id}/yaml`}>
                    <Download /> {t("downloadYaml")}
                  </a>
                </Button>
                {!r.is_default && (
                  <Button variant="ghost" size="sm" onClick={() => setDefault.mutate(r.id)}>
                    <Star /> {t("setDefault")}
                  </Button>
                )}
                {!r.is_default && r.projects === 0 && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={tc("delete")}
                    onClick={() => window.confirm(tc("confirmDelete")) && remove.mutate(r.id)}
                  >
                    <Trash2 />
                  </Button>
                )}
              </div>
            </div>
            {r.warnings.length > 0 && (
              <details className="text-sm">
                <summary className="cursor-pointer text-amber-800">{t("warnings", { n: r.warnings.length })}</summary>
                <div className="mt-2">
                  <Issues issues={r.warnings} />
                </div>
              </details>
            )}
            {openId === r.id && detail.data && (
              <TemplateTree nodes={detail.data.tree} categories={detail.data.document_categories} />
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
