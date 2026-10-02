"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import { qk } from "@/lib/hooks";
import type { Me } from "@/lib/types";

export default function RegisterPage() {
  const t = useTranslations("auth.register");
  const router = useRouter();
  const queryClient = useQueryClient();

  const register = useMutation({
    mutationFn: (payload: unknown) => api.post<Me>("/auth/register", payload),
    onSuccess: (me) => {
      queryClient.setQueryData(qk.me, me);
      router.push("/portfolio");
    },
  });

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    register.mutate({
      organization: { name: f.orgName, bin: f.bin, address: f.address },
      user: { full_name: f.fullName, position: f.position, email: f.email, password: f.password },
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("orgSection")}</p>
          <Field label={t("orgName")} htmlFor="orgName" required>
            <Input id="orgName" name="orgName" required />
          </Field>
          <Field label={t("bin")} htmlFor="bin" hint={t("binHint")} required>
            <Input id="bin" name="bin" inputMode="numeric" pattern="\d{12}" maxLength={12} required />
          </Field>
          <Field label={t("address")} htmlFor="address">
            <Input id="address" name="address" />
          </Field>
          <p className="mt-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("userSection")}
          </p>
          <Field label={t("fullName")} htmlFor="fullName" required>
            <Input id="fullName" name="fullName" autoComplete="name" required />
          </Field>
          <Field label={t("position")} htmlFor="position">
            <Input id="position" name="position" />
          </Field>
          <Field label={t("email")} htmlFor="email" required>
            <Input id="email" name="email" type="email" autoComplete="email" required />
          </Field>
          <Field label={t("password")} htmlFor="password" hint={t("passwordHint")} required>
            <Input id="password" name="password" type="password" autoComplete="new-password" minLength={8} required />
          </Field>
          {register.isError && <Alert variant="destructive">{errorMessage(register.error)}</Alert>}
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={register.isPending}>
            {register.isPending ? t("submitting") : t("submit")}
          </Button>
          <p className="text-sm text-muted-foreground">
            {t("haveAccount")}{" "}
            <Link href="/login" className="text-primary hover:underline">
              {t("login")}
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
