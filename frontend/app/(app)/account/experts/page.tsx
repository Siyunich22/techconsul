"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, Trash2, UserPlus } from "lucide-react";
import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { FileUploadButton } from "@/components/file-upload-button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_PREFIX, api, errorMessage } from "@/lib/api";
import { useExperts, useMe } from "@/lib/hooks";
import type { Expert, ExpertInput } from "@/lib/types";

export default function ExpertsPage() {
  const t = useTranslations("account.experts");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const me = useMe();
  const canManage = me.data?.role === "manager" || me.data?.role === "admin";
  const [showInactive, setShowInactive] = useState(false);
  const experts = useExperts(showInactive);
  const [editing, setEditing] = useState<Expert | "new" | null>(null);
  const [message, setMessage] = useState<string>();

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["experts"] });
  const onError = (e: unknown) => setMessage(errorMessage(e));

  const remove = useMutation({ mutationFn: (id: string) => api.delete(`/org/experts/${id}`), onSuccess: refresh, onError });
  const toggle = useMutation({
    mutationFn: (e: Expert) => api.patch(`/org/experts/${e.id}`, { is_active: !e.is_active }),
    onSuccess: refresh,
    onError,
  });
  const uploadCv = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) => api.upload(`/org/experts/${id}/cv`, file),
    onSuccess: refresh,
    onError,
  });
  const invite = useMutation({
    mutationFn: (e: Expert) =>
      api.post<{ invite_link: string }>("/org/invitations", {
        email: e.email,
        full_name: e.full_name,
        role: "expert",
        expert_id: e.id,
      }),
    onSuccess: (data) => {
      setMessage(`${t("inviteToPlatform")}: ${data.invite_link}`);
      refresh();
    },
    onError,
  });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("hint")}</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            {t("showInactive")}
          </label>
          {canManage && (
            <Button onClick={() => setEditing("new")}>
              <Plus /> {t("add")}
            </Button>
          )}
        </div>
      </div>

      {message && (
        <Alert onClick={() => setMessage(undefined)} className="cursor-pointer break-all">
          {message}
        </Alert>
      )}

      {editing && (
        <ExpertForm
          expert={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}

      {experts.data?.length === 0 && !editing && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">{t("empty")}</CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {experts.data?.map((e) => (
          <Card key={e.id} className={e.is_active ? "" : "opacity-60"}>
            <CardContent className="flex flex-col gap-3 p-5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold">{e.full_name}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {e.specialization.map((s) => (
                      <Badge key={s} variant="secondary">
                        {s}
                      </Badge>
                    ))}
                    {e.user_id && <Badge variant="success">{t("hasAccount")}</Badge>}
                    {!e.is_active && <Badge variant="muted">{t("inactive")}</Badge>}
                  </div>
                </div>
                {canManage && (
                  <div className="flex">
                    <Button variant="ghost" size="icon" aria-label={tc("edit")} onClick={() => setEditing(e)}>
                      <Pencil />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={tc("delete")}
                      onClick={() => window.confirm(tc("confirmDelete")) && remove.mutate(e.id)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                )}
              </div>
              <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
                {e.education && (
                  <>
                    <dt className="text-muted-foreground">{t("education")}</dt>
                    <dd>{e.education}</dd>
                  </>
                )}
                {e.years !== null && (
                  <>
                    <dt className="text-muted-foreground">{t("years")}</dt>
                    <dd>{e.years}</dd>
                  </>
                )}
                {e.research_experience && (
                  <>
                    <dt className="text-muted-foreground">{t("research")}</dt>
                    <dd className="whitespace-pre-line">{e.research_experience}</dd>
                  </>
                )}
                {(e.email || e.phone) && (
                  <>
                    <dt className="text-muted-foreground">{t("email")}</dt>
                    <dd>{[e.email, e.phone].filter(Boolean).join(", ")}</dd>
                  </>
                )}
              </dl>
              <div className="flex flex-wrap items-center gap-2 border-t pt-3">
                {e.cv_filename ? (
                  <Button variant="ghost" size="sm" asChild>
                    <a href={`${API_PREFIX}/org/experts/${e.id}/cv`}>
                      <Download /> {e.cv_filename}
                    </a>
                  </Button>
                ) : (
                  <span className="text-xs text-muted-foreground">{t("noCv")}</span>
                )}
                {canManage && (
                  <FileUploadButton
                    accept=".pdf,.docx,.doc"
                    label={e.cv_filename ? tc("replace") : t("cv")}
                    pending={uploadCv.isPending && uploadCv.variables?.id === e.id}
                    onFile={(file) => uploadCv.mutate({ id: e.id, file })}
                  />
                )}
                {canManage && !e.user_id && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!e.email || invite.isPending}
                    title={e.email ? undefined : t("inviteNeedsEmail")}
                    onClick={() => invite.mutate(e)}
                  >
                    <UserPlus /> {t("inviteToPlatform")}
                  </Button>
                )}
                {canManage && (
                  <Button variant="ghost" size="sm" className="ml-auto" onClick={() => toggle.mutate(e)}>
                    {e.is_active ? t("deactivate") : t("activate")}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ExpertForm({ expert, onDone }: { expert: Expert | null; onDone: () => void }) {
  const t = useTranslations("account.experts");
  const tc = useTranslations("common");
  const save = useMutation({
    mutationFn: (body: ExpertInput) =>
      expert ? api.patch(`/org/experts/${expert.id}`, body) : api.post("/org/experts", body),
    onSuccess: onDone,
  });

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    save.mutate({
      full_name: f.full_name,
      specialization: f.specialization
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      education: f.education,
      research_experience: f.research_experience,
      years: f.years ? Number(f.years) : null,
      email: f.email,
      phone: f.phone,
      contacts: f.contacts,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{expert ? expert.full_name : t("add")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
          <Field label={t("fullName")} htmlFor="full_name" required>
            <Input id="full_name" name="full_name" defaultValue={expert?.full_name} required />
          </Field>
          <Field label={t("specialization")} htmlFor="specialization" hint={t("specializationHint")}>
            <Input id="specialization" name="specialization" defaultValue={expert?.specialization.join(", ")} />
          </Field>
          <Field label={t("education")} htmlFor="education" className="sm:col-span-2">
            <Textarea id="education" name="education" defaultValue={expert?.education} className="min-h-16" />
          </Field>
          <Field label={t("research")} htmlFor="research_experience" className="sm:col-span-2">
            <Textarea id="research_experience" name="research_experience" defaultValue={expert?.research_experience} />
          </Field>
          <Field label={t("years")} htmlFor="years">
            <Input id="years" name="years" type="number" min={0} max={80} defaultValue={expert?.years ?? ""} />
          </Field>
          <Field label={t("email")} htmlFor="email">
            <Input id="email" name="email" type="email" defaultValue={expert?.email} />
          </Field>
          <Field label={t("phone")} htmlFor="phone">
            <Input id="phone" name="phone" defaultValue={expert?.phone} />
          </Field>
          <Field label={t("contacts")} htmlFor="contacts">
            <Input id="contacts" name="contacts" defaultValue={expert?.contacts} />
          </Field>
          {save.isError && (
            <Alert variant="destructive" className="sm:col-span-2">
              {errorMessage(save.error)}
            </Alert>
          )}
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? tc("saving") : tc("save")}
            </Button>
            <Button type="button" variant="outline" onClick={onDone}>
              {tc("cancel")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
