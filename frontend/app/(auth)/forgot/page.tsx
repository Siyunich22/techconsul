"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";

export default function ForgotPage() {
  const t = useTranslations("auth.forgot");
  const forgot = useMutation({ mutationFn: (email: string) => api.post("/auth/forgot", { email }) });

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    forgot.mutate(String(new FormData(e.currentTarget).get("email")));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          {forgot.isSuccess ? (
            <Alert variant="success">{t("sent")}</Alert>
          ) : (
            <Field label="Email" htmlFor="email" required>
              <Input id="email" name="email" type="email" autoComplete="email" required />
            </Field>
          )}
          {forgot.isError && <Alert variant="destructive">{errorMessage(forgot.error)}</Alert>}
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          {!forgot.isSuccess && (
            <Button type="submit" className="w-full" disabled={forgot.isPending}>
              {t("submit")}
            </Button>
          )}
          <Link href="/login" className="text-sm text-primary hover:underline">
            {t("back")}
          </Link>
        </CardFooter>
      </form>
    </Card>
  );
}
