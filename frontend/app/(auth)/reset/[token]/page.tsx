"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import type { FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";

export default function ResetPage() {
  const t = useTranslations("auth.reset");
  const tl = useTranslations("auth.login");
  const { token } = useParams<{ token: string }>();
  const reset = useMutation({ mutationFn: (password: string) => api.post("/auth/reset", { token, password }) });

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    reset.mutate(String(new FormData(e.currentTarget).get("password")));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          {reset.isSuccess ? (
            <Alert variant="success">{t("done")}</Alert>
          ) : (
            <Field label={t("password")} htmlFor="password" required>
              <Input id="password" name="password" type="password" autoComplete="new-password" minLength={8} required />
            </Field>
          )}
          {reset.isError && <Alert variant="destructive">{errorMessage(reset.error)}</Alert>}
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          {reset.isSuccess ? (
            <Button asChild className="w-full">
              <Link href="/login">{tl("submit")}</Link>
            </Button>
          ) : (
            <Button type="submit" className="w-full" disabled={reset.isPending}>
              {t("submit")}
            </Button>
          )}
        </CardFooter>
      </form>
    </Card>
  );
}
