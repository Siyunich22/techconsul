"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import { qk } from "@/lib/hooks";
import type { Me, UserRole } from "@/lib/types";

interface InvitationInfo {
  email: string;
  full_name: string;
  organization_name: string;
  role: UserRole;
}

export default function InvitePage() {
  const t = useTranslations("auth.invite");
  const tr = useTranslations("roles");
  const tc = useTranslations("common");
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();

  const info = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => api.get<InvitationInfo>(`/auth/invitations/${token}`),
    retry: false,
  });
  const accept = useMutation({
    mutationFn: (body: { password: string; full_name: string }) =>
      api.post<Me>("/auth/invitations/accept", { token, ...body }),
    onSuccess: (me) => {
      queryClient.setQueryData(qk.me, me);
      router.push("/portfolio");
    },
  });

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    accept.mutate({ password: String(f.get("password")), full_name: String(f.get("fullName")) });
  }

  if (info.isPending) return <p className="text-center text-sm text-muted-foreground">{tc("loading")}</p>;
  if (info.isError) return <Alert variant="destructive">{t("invalid")}</Alert>;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>
          {t("description", { org: info.data.organization_name })}
          <br />
          {t("role", { role: tr(info.data.role) })}
        </CardDescription>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          <Field label="Email">
            <Input value={info.data.email} disabled />
          </Field>
          <Field label={t("fullName")} htmlFor="fullName" required>
            <Input id="fullName" name="fullName" defaultValue={info.data.full_name} required />
          </Field>
          <Field label={t("password")} htmlFor="password" required>
            <Input id="password" name="password" type="password" autoComplete="new-password" minLength={8} required />
          </Field>
          {accept.isError && <Alert variant="destructive">{errorMessage(accept.error)}</Alert>}
        </CardContent>
        <CardFooter>
          <Button type="submit" className="w-full" disabled={accept.isPending}>
            {t("submit")}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
