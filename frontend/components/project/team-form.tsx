"use client";

import { Trash2 } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import type { AssignableSection, Expert, MemberInput } from "@/lib/types";
import { cn } from "@/lib/utils";

export function TeamForm({
  value,
  onChange,
  experts,
  sections,
  enabledOptional,
}: {
  value: MemberInput[];
  onChange: (v: MemberInput[]) => void;
  experts: Expert[];
  sections: AssignableSection[];
  enabledOptional: string[];
}) {
  const t = useTranslations("project.teamTab");
  const available = sections.filter((s) => !s.optional || enabledOptional.includes(s.id));
  const free = experts.filter((e) => !value.some((m) => m.expert_id === e.id));
  const assigned = new Set(value.flatMap((m) => m.assigned_items));
  const unassigned = available.filter((s) => !assigned.has(s.id)).map((s) => s.id);

  const update = (i: number, patch: Partial<MemberInput>) =>
    onChange(value.map((m, j) => (j === i ? { ...m, ...patch } : m)));

  if (experts.length === 0) {
    return (
      <Alert>
        {t("noExperts")}{" "}
        <Link href="/account/experts" className="text-primary underline">
          {t("goToExperts")}
        </Link>
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">{t("intro")}</p>
      {value.map((m, i) => {
        const expert = experts.find((e) => e.id === m.expert_id);
        return (
          <Card key={m.expert_id}>
            <CardContent className="flex flex-col gap-3 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium">{expert?.full_name ?? m.expert_id}</p>
                  <p className="text-xs text-muted-foreground">{expert?.specialization.join(", ")}</p>
                </div>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={m.role === "lead"}
                      onChange={(e) => update(i, { role: e.target.checked ? "lead" : "expert" })}
                    />
                    {t("lead")}
                  </label>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Исключить"
                    onClick={() => onChange(value.filter((_, j) => j !== i))}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
              <div>
                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("sections")}</p>
                <div className="flex flex-wrap gap-2">
                  {available.map((s) => {
                    const on = m.assigned_items.includes(s.id);
                    return (
                      <button
                        key={s.id}
                        type="button"
                        aria-pressed={on}
                        title={s.title}
                        onClick={() =>
                          update(i, {
                            assigned_items: on
                              ? m.assigned_items.filter((x) => x !== s.id)
                              : [...m.assigned_items, s.id],
                          })
                        }
                        className={cn(
                          "rounded-md border px-2.5 py-1 text-left text-xs transition-colors",
                          on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent",
                        )}
                      >
                        <span className="font-semibold">{s.id}</span> {s.title}
                        {s.extension && (
                          <Badge variant="muted" className="ml-1.5">
                            {t("extension")}
                          </Badge>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            </CardContent>
          </Card>
        );
      })}

      {free.length > 0 && (
        <Select
          aria-label={t("addExpert")}
          value=""
          onChange={(e) =>
            e.target.value && onChange([...value, { expert_id: e.target.value, role: "expert", assigned_items: [] }])
          }
          className="max-w-sm"
        >
          <option value="">+ {t("addExpert")}</option>
          {free.map((e) => (
            <option key={e.id} value={e.id}>
              {e.full_name}
              {e.specialization.length ? ` — ${e.specialization.join(", ")}` : ""}
            </option>
          ))}
        </Select>
      )}

      {value.length > 0 && unassigned.length > 0 && (
        <p className="text-xs text-amber-700">{t("unassigned", { list: unassigned.join(", ") })}</p>
      )}
    </div>
  );
}
