"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { EMPTY_INDEPENDENCE, IndependenceForm, independenceComplete } from "@/components/project/independence-form";
import { EMPTY_PASSPORT, PassportForm } from "@/components/project/passport-form";
import { TeamForm } from "@/components/project/team-form";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import { useExperts, useTemplateSections, useTemplates } from "@/lib/hooks";
import type { Independence, MemberInput, PassportInput, Project } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function NewProjectPage() {
  const t = useTranslations("project");
  const tc = useTranslations("common");
  const router = useRouter();
  const queryClient = useQueryClient();
  const formRef = useRef<HTMLFormElement>(null);

  const [step, setStep] = useState(1);
  const [passport, setPassport] = useState<PassportInput>(EMPTY_PASSPORT);
  const [independence, setIndependence] = useState<Independence>(EMPTY_INDEPENDENCE);
  const [members, setMembers] = useState<MemberInput[]>([]);
  const [templateId, setTemplateId] = useState<string>();
  const [enabledOptional, setEnabledOptional] = useState<string[]>([]);

  const templates = useTemplates();
  const sections = useTemplateSections(templateId);
  const experts = useExperts();

  useEffect(() => {
    if (!templateId && templates.data?.length) {
      setTemplateId((templates.data.find((tpl) => tpl.is_default) ?? templates.data[0]).id);
    }
  }, [templates.data, templateId]);

  const create = useMutation({
    mutationFn: () =>
      api.post<Project>("/projects", {
        ...passport,
        template_version_id: templateId,
        enabled_optional_items: enabledOptional,
        independence,
        members,
      }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/projects/${project.id}`);
    },
  });

  const answered =
    independence.affiliated !== null && independence.participated_in_docs !== null && independence.is_supplier !== null;
  const steps = [t("passport"), t("independence"), t("team")];

  function next() {
    if (step === 1 && !formRef.current?.reportValidity()) return;
    if (step === 2 && !answered) return;
    setStep(step + 1);
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <h1 className="text-2xl font-semibold tracking-tight">{t("wizard.title")}</h1>

      <ol className="flex gap-2">
        {steps.map((label, i) => {
          const n = i + 1;
          return (
            <li
              key={label}
              className={cn(
                "flex flex-1 items-center gap-2 rounded-md border px-3 py-2 text-sm",
                n === step && "border-primary bg-primary/5 font-medium",
                n < step && "text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "flex size-6 items-center justify-center rounded-full border text-xs",
                  n < step && "border-primary bg-primary text-primary-foreground",
                )}
              >
                {n < step ? <Check className="size-3.5" /> : n}
              </span>
              {label}
            </li>
          );
        })}
      </ol>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {t("wizard.step", { n: step })} · {steps[step - 1]}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form ref={formRef} onSubmit={(e) => e.preventDefault()}>
            {step === 1 && (
              <PassportForm
                value={passport}
                onChange={setPassport}
                templates={templates.data}
                templateId={templateId}
                onTemplateChange={(id) => {
                  setTemplateId(id);
                  setEnabledOptional([]);
                  setMembers([]);
                }}
                optionalSections={sections.data?.filter((s) => s.optional)}
                enabledOptional={enabledOptional}
                onEnabledOptionalChange={setEnabledOptional}
              />
            )}
            {step === 2 && (
              <>
                <IndependenceForm value={independence} onChange={setIndependence} />
                {answered && !independenceComplete(independence) && (
                  <Alert variant="warning" className="mt-4">
                    {t("indep.incomplete")}
                  </Alert>
                )}
              </>
            )}
            {step === 3 && (
              <TeamForm
                value={members}
                onChange={setMembers}
                experts={experts.data ?? []}
                sections={sections.data ?? []}
                enabledOptional={enabledOptional}
              />
            )}
          </form>
          {create.isError && (
            <Alert variant="destructive" className="mt-4">
              {errorMessage(create.error)}
            </Alert>
          )}
        </CardContent>
      </Card>

      <div className="flex justify-between">
        <Button variant="outline" onClick={() => (step === 1 ? router.back() : setStep(step - 1))}>
          {step === 1 ? tc("cancel") : tc("back")}
        </Button>
        {step < 3 ? (
          <Button onClick={next} disabled={step === 2 && !answered}>
            {tc("next")}
          </Button>
        ) : (
          <Button onClick={() => create.mutate()} disabled={create.isPending}>
            {create.isPending ? t("wizard.creating") : t("wizard.create")}
          </Button>
        )}
      </div>
    </div>
  );
}
