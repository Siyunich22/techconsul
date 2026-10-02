"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, apiFetch, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import type { ReferenceDoc } from "@/lib/types";

const KINDS = ["ndt", "law", "norm", "standard", "other"] as const;

export default function ReferenceDocsPage() {
  const t = useTranslations("admin");
  const td = useTranslations("documents");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const me = useMe();
  const [formKey, setFormKey] = useState(0);
  const refs = useQuery({
    queryKey: ["reference-docs"],
    queryFn: () => api.get<ReferenceDoc[]>("/admin/reference-docs"),
    enabled: me.data?.role === "admin",
    refetchInterval: (q) => (q.state.data?.some((r) => ["uploaded", "processing"].includes(r.status)) ? 3000 : false),
  });
  const upload = useMutation({
    mutationFn: (form: FormData) => apiFetch<ReferenceDoc>("/admin/reference-docs", { method: "POST", body: form }),
    onSuccess: () => {
      setFormKey((k) => k + 1);
      queryClient.invalidateQueries({ queryKey: ["reference-docs"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/reference-docs/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reference-docs"] }),
  });

  if (me.data && me.data.role !== "admin") return <Alert variant="destructive">403</Alert>;

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    for (const k of ["valid_from", "valid_to"]) if (!form.get(k)) form.delete(k);
    upload.mutate(form);
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("references")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("referencesHint")}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("upload")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form key={formKey} onSubmit={onSubmit} className="grid gap-3 md:grid-cols-[2fr_1fr_1fr_1fr] md:items-end">
            <Field label={t("title")} htmlFor="title" required>
              <Input id="title" name="title" required />
            </Field>
            <Field label={t("kind")} htmlFor="kind">
              <Select id="kind" name="kind" defaultValue="ndt">
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {td(`refKind.${k}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("validFrom")} htmlFor="valid_from">
              <Input id="valid_from" name="valid_from" type="date" />
            </Field>
            <Field label={t("validTo")} htmlFor="valid_to">
              <Input id="valid_to" name="valid_to" type="date" />
            </Field>
            <Field label={t("file")} htmlFor="file" required className="md:col-span-3">
              <Input id="file" name="file" type="file" required accept=".pdf,.docx,.doc,.txt,.xlsx" />
            </Field>
            <Button type="submit" disabled={upload.isPending}>
              {upload.isPending ? tc("saving") : tc("upload")}
            </Button>
          </form>
          {upload.isError && (
            <Alert variant="destructive" className="mt-3">
              {errorMessage(upload.error)}
            </Alert>
          )}
        </CardContent>
      </Card>
      <Card>
        {refs.data?.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted-foreground">{t("empty")}</CardContent>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("title")}</TableHead>
                <TableHead>{t("kind")}</TableHead>
                <TableHead>{t("validFrom")}</TableHead>
                <TableHead>{td("col.status")}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {refs.data?.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <p className="font-medium">{r.title}</p>
                    <p className="text-xs text-muted-foreground">{r.filename}</p>
                    {r.error && <p className="text-xs text-destructive">{r.error}</p>}
                  </TableCell>
                  <TableCell>{td(`refKind.${r.kind}`)}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {formatDate(r.valid_from)} — {formatDate(r.valid_to)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={r.status === "indexed" ? "success" : r.status === "error" ? "destructive" : "default"}>
                      {td(`status.${r.status}`)}
                    </Badge>
                    {r.status === "indexed" && (
                      <p className="mt-1 text-[11px] text-muted-foreground">{t("chunks", { n: r.chunk_count })}</p>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={tc("delete")}
                      onClick={() => window.confirm(tc("confirmDelete")) && remove.mutate(r.id)}
                    >
                      <Trash2 />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
