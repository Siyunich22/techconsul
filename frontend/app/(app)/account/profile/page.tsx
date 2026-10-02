"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import { qk, useMe } from "@/lib/hooks";
import type { Me } from "@/lib/types";

export default function ProfilePage() {
  const t = useTranslations("account.profile");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const me = useMe();

  const save = useMutation({
    mutationFn: (body: Partial<Me>) => api.patch<Me>("/me", body),
    onSuccess: (data) => queryClient.setQueryData(qk.me, data),
  });
  const password = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) => api.post("/me/password", body),
  });

  if (!me.data) return null;

  function onSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    save.mutate({ full_name: f.full_name, position: f.position, phone: f.phone });
  }

  function onPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = Object.fromEntries(new FormData(form)) as Record<string, string>;
    password.mutate(
      { current_password: f.current_password, new_password: f.new_password },
      { onSuccess: () => form.reset() },
    );
  }

  return (
    <div className="flex max-w-2xl flex-col gap-5">
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSave} className="grid gap-4 sm:grid-cols-2">
            <Field label={t("fullName")} htmlFor="full_name" required className="sm:col-span-2">
              <Input id="full_name" name="full_name" defaultValue={me.data.full_name} required />
            </Field>
            <Field label={t("position")} htmlFor="position">
              <Input id="position" name="position" defaultValue={me.data.position} />
            </Field>
            <Field label={t("phone")} htmlFor="phone">
              <Input id="phone" name="phone" defaultValue={me.data.phone} />
            </Field>
            <Field label={t("email")} className="sm:col-span-2">
              <Input value={me.data.email} disabled />
            </Field>
            <div className="flex items-center gap-3 sm:col-span-2">
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? tc("saving") : tc("save")}
              </Button>
              {save.isSuccess && <span className="text-sm text-emerald-700">{tc("saved")}</span>}
              {save.isError && <span className="text-sm text-destructive">{errorMessage(save.error)}</span>}
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("passwordTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onPassword} className="grid gap-4 sm:grid-cols-2">
            <Field label={t("currentPassword")} htmlFor="current_password" required>
              <Input id="current_password" name="current_password" type="password" autoComplete="current-password" required />
            </Field>
            <Field label={t("newPassword")} htmlFor="new_password" required>
              <Input id="new_password" name="new_password" type="password" autoComplete="new-password" minLength={8} required />
            </Field>
            <div className="flex items-center gap-3 sm:col-span-2">
              <Button type="submit" variant="outline" disabled={password.isPending}>
                {t("changePassword")}
              </Button>
              {password.isSuccess && <span className="text-sm text-emerald-700">{t("passwordChanged")}</span>}
            </div>
            {password.isError && (
              <Alert variant="destructive" className="sm:col-span-2">
                {errorMessage(password.error)}
              </Alert>
            )}
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
