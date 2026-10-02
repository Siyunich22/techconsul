"use client";

import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Alert } from "@/components/ui/alert";
import { Field } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import type { Independence } from "@/lib/types";
import { cn } from "@/lib/utils";

export const EMPTY_INDEPENDENCE: Independence = {
  affiliated: null,
  participated_in_docs: null,
  is_supplier: null,
  justification: "",
  joint_experience: "",
};

type Question = "affiliated" | "participated_in_docs" | "is_supplier";

export function hasConflict(v: Independence): boolean {
  return Boolean(v.affiliated || v.participated_in_docs || v.is_supplier);
}

/** Те же правила, что Independence.is_complete на backend. */
export function independenceComplete(v: Independence): boolean {
  const answered = v.affiliated !== null && v.participated_in_docs !== null && v.is_supplier !== null;
  return answered && (!hasConflict(v) || v.justification.trim().length > 0);
}

export function IndependenceForm({ value, onChange }: { value: Independence; onChange: (v: Independence) => void }) {
  const t = useTranslations("project.indep");
  const tc = useTranslations("common");
  const conflict = hasConflict(value);
  const questions: [Question, string][] = [
    ["affiliated", t("affiliated")],
    ["participated_in_docs", t("participated")],
    ["is_supplier", t("supplier")],
  ];

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-muted-foreground">{t("intro")}</p>
      {questions.map(([key, label]) => (
        <fieldset key={key} className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <legend className="sr-only">{label}</legend>
          <span className="text-sm font-medium">{label}</span>
          <div className="flex gap-2" role="radiogroup" aria-label={label}>
            {[true, false].map((answer) => (
              <label
                key={String(answer)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm",
                  value[key] === answer && (answer ? "border-red-400 bg-red-50" : "border-primary bg-primary/5"),
                )}
              >
                <input
                  type="radio"
                  name={key}
                  checked={value[key] === answer}
                  onChange={() => onChange({ ...value, [key]: answer })}
                />
                {answer ? tc("yes") : tc("no")}
              </label>
            ))}
          </div>
        </fieldset>
      ))}

      {conflict && (
        <>
          <Alert variant="destructive" className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            {t("conflictWarning")}
          </Alert>
          <Field label={t("justification")} htmlFor="justification" hint={t("justificationHint")} required>
            <Textarea
              id="justification"
              value={value.justification}
              onChange={(e) => onChange({ ...value, justification: e.target.value })}
              className={cn(!value.justification.trim() && "border-red-400")}
            />
          </Field>
        </>
      )}

      <Field label={t("jointExperience")} htmlFor="joint_experience">
        <Textarea
          id="joint_experience"
          value={value.joint_experience}
          onChange={(e) => onChange({ ...value, joint_experience: e.target.value })}
        />
      </Field>
    </div>
  );
}
