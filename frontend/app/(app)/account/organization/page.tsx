"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { FileUploadButton } from "@/components/file-upload-button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { API_PREFIX, api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { qk, useMe, useOrg, useOrgUsers } from "@/lib/hooks";
import type { Org, OrgSettings, OrgUser } from "@/lib/types";

export default function OrganizationPage() {
  const t = useTranslations("account.org");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const me = useMe();
  const canManage = me.data?.role === "manager" || me.data?.role === "admin";
  const org = useOrg();
  const [logoVersion, setLogoVersion] = useState(0);

  const onOrg = (data: Org) => queryClient.setQueryData(qk.org, data);
  const save = useMutation({ mutationFn: (body: object) => api.patch<Org>("/org", body), onSuccess: onOrg });
  const uploadLogo = useMutation({
    mutationFn: (file: File) => api.upload<Org>("/org/logo", file),
    onSuccess: (data) => {
      onOrg(data);
      setLogoVersion((v) => v + 1);
    },
  });
  const uploadDocx = useMutation({
    mutationFn: (file: File) => api.upload<Org>("/org/reference-docx", file),
    onSuccess: onOrg,
  });

  if (!org.data) return null;
  const o = org.data;

  function onSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    const settings: OrgSettings = {
      report_language: f.report_language as OrgSettings["report_language"],
      page_number_format: f.page_number_format as OrgSettings["page_number_format"],
      default_font: f.default_font,
      default_font_size: Number(f.default_font_size),
    };
    save.mutate({ name: f.name, address: f.address, phone: f.phone, email: f.email, settings });
  }

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <p className="text-sm text-muted-foreground">{t("hint")}</p>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSave}>
            <fieldset disabled={!canManage} className="grid gap-4 sm:grid-cols-2">
              <Field label={t("name")} htmlFor="name" required className="sm:col-span-2">
                <Input id="name" name="name" defaultValue={o.name} required />
              </Field>
              <Field label={t("bin")}>
                <Input value={o.bin} disabled />
              </Field>
              <Field label={t("phone")} htmlFor="phone">
                <Input id="phone" name="phone" defaultValue={o.phone} />
              </Field>
              <Field label={t("address")} htmlFor="address" className="sm:col-span-2">
                <Input id="address" name="address" defaultValue={o.address} />
              </Field>
              <Field label={t("email")} htmlFor="email">
                <Input id="email" name="email" type="email" defaultValue={o.email} />
              </Field>

              <h3 className="mt-3 text-sm font-semibold sm:col-span-2">{t("settings")}</h3>
              <Field label={t("reportLanguage")} htmlFor="report_language">
                <Select id="report_language" name="report_language" defaultValue={o.settings.report_language}>
                  <option value="ru">{t("lang.ru")}</option>
                  <option value="kk" disabled>
                    {t("lang.kk")}
                  </option>
                  <option value="en" disabled>
                    {t("lang.en")}
                  </option>
                </Select>
              </Field>
              <Field label={t("pageNumbers")} htmlFor="page_number_format">
                <Select id="page_number_format" name="page_number_format" defaultValue={o.settings.page_number_format}>
                  <option value="arabic">{t("pageFormat.arabic")}</option>
                  <option value="page_of_total">{t("pageFormat.page_of_total")}</option>
                </Select>
              </Field>
              <Field label={t("font")} htmlFor="default_font">
                <Select id="default_font" name="default_font" defaultValue={o.settings.default_font}>
                  {["Times New Roman", "Arial", "Calibri", "PT Serif", "PT Sans"].map((f) => (
                    <option key={f}>{f}</option>
                  ))}
                </Select>
              </Field>
              <Field label={t("fontSize")} htmlFor="default_font_size">
                <Input
                  id="default_font_size"
                  name="default_font_size"
                  type="number"
                  min={8}
                  max={16}
                  defaultValue={o.settings.default_font_size}
                />
              </Field>
            </fieldset>
            {canManage && (
              <div className="mt-5 flex items-center gap-3">
                <Button type="submit" disabled={save.isPending}>
                  {save.isPending ? tc("saving") : tc("save")}
                </Button>
                {save.isSuccess && <span className="text-sm text-emerald-700">{tc("saved")}</span>}
                {save.isError && <span className="text-sm text-destructive">{errorMessage(save.error)}</span>}
              </div>
            )}
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("branding")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex size-20 items-center justify-center overflow-hidden rounded-md border bg-muted">
              {o.has_logo ? (
                // eslint-disable-next-line @next/next/no-img-element -- логотип отдаётся API с авторизацией по cookie
                <img src={`${API_PREFIX}/org/logo?v=${logoVersion}`} alt={t("logo")} className="max-h-full max-w-full" />
              ) : (
                <span className="text-xs text-muted-foreground">{t("notUploaded")}</span>
              )}
            </div>
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium">{t("logo")}</p>
              <p className="text-xs text-muted-foreground">{t("logoHint")}</p>
              {canManage && (
                <FileUploadButton
                  accept=".png,.jpg,.jpeg,.svg"
                  label={o.has_logo ? tc("replace") : tc("upload")}
                  pending={uploadLogo.isPending}
                  onFile={(f) => uploadLogo.mutate(f)}
                />
              )}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <p className="flex items-center gap-2 text-sm font-medium">
              {t("referenceDocx")}
              <Badge variant={o.has_reference_docx ? "success" : "muted"}>
                {o.has_reference_docx ? t("uploaded") : t("notUploaded")}
              </Badge>
            </p>
            <p className="text-xs text-muted-foreground">{t("referenceDocxHint")}</p>
            <div className="flex gap-2">
              {canManage && (
                <FileUploadButton
                  accept=".docx"
                  label={o.has_reference_docx ? tc("replace") : tc("upload")}
                  pending={uploadDocx.isPending}
                  onFile={(f) => uploadDocx.mutate(f)}
                />
              )}
              {o.has_reference_docx && (
                <Button variant="ghost" size="sm" asChild>
                  <a href={`${API_PREFIX}/org/reference-docx`}>
                    <Download /> {tc("download")}
                  </a>
                </Button>
              )}
            </div>
          </div>
          {(uploadLogo.isError || uploadDocx.isError) && (
            <Alert variant="destructive">{errorMessage(uploadLogo.error ?? uploadDocx.error)}</Alert>
          )}
        </CardContent>
      </Card>

      {canManage && <UsersCard />}
    </div>
  );
}

function UsersCard() {
  const t = useTranslations("account.org");
  const tr = useTranslations("roles");
  const queryClient = useQueryClient();
  const users = useOrgUsers();
  const [link, setLink] = useState<string>();
  const invite = useMutation({
    mutationFn: (body: object) => api.post<{ user: OrgUser; invite_link: string }>("/org/invitations", body),
    onSuccess: (data) => {
      setLink(data.invite_link);
      queryClient.invalidateQueries({ queryKey: qk.orgUsers });
    },
  });

  function onInvite(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = Object.fromEntries(new FormData(form)) as Record<string, string>;
    invite.mutate(f, { onSuccess: () => form.reset() });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("users")}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ФИО</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>{t("inviteRole")}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.data?.map((u) => (
              <TableRow key={u.id}>
                <TableCell>{u.full_name}</TableCell>
                <TableCell>{u.email}</TableCell>
                <TableCell>{tr(u.role)}</TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {u.invitation_pending ? <Badge variant="warning">{t("pending")}</Badge> : formatDate(u.last_login_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <form onSubmit={onInvite} className="grid gap-3 sm:grid-cols-[1fr_1fr_10rem_auto] sm:items-end">
          <Field label={t("inviteEmail")} htmlFor="inv_email" required>
            <Input id="inv_email" name="email" type="email" required />
          </Field>
          <Field label={t("inviteName")} htmlFor="inv_name" required>
            <Input id="inv_name" name="full_name" required />
          </Field>
          <Field label={t("inviteRole")} htmlFor="inv_role">
            <Select id="inv_role" name="role" defaultValue="expert">
              <option value="expert">{tr("expert")}</option>
              <option value="manager">{tr("manager")}</option>
            </Select>
          </Field>
          <Button type="submit" disabled={invite.isPending}>
            {t("sendInvite")}
          </Button>
        </form>
        {link && (
          <Alert variant="success">
            {t("inviteSent")} <code className="break-all">{link}</code>
          </Alert>
        )}
        {invite.isError && <Alert variant="destructive">{errorMessage(invite.error)}</Alert>}
      </CardContent>
    </Card>
  );
}
