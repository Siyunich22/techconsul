"use client";

import { useTranslations } from "next-intl";

import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useReference } from "@/lib/hooks";
import type { AssignableSection, PassportInput, TemplateInfo } from "@/lib/types";

export const EMPTY_PASSPORT: PassportInput = {
  name: "",
  customer_name: "",
  customer_bin: "",
  industry: "",
  region: "",
  site: "",
  capacity_text: "",
  budget_amount: null,
  currency: "KZT",
  bank_name: "",
};

export function PassportForm({
  value,
  onChange,
  templates,
  templateId,
  onTemplateChange,
  optionalSections = [],
  enabledOptional = [],
  onEnabledOptionalChange,
}: {
  value: PassportInput;
  onChange: (v: PassportInput) => void;
  templates?: TemplateInfo[];
  templateId?: string;
  onTemplateChange?: (id: string) => void;
  optionalSections?: AssignableSection[];
  enabledOptional?: string[];
  onEnabledOptionalChange?: (ids: string[]) => void;
}) {
  const t = useTranslations("project.fields");
  const tc = useTranslations("common");
  const reference = useReference();
  const set = (key: keyof PassportInput) => (e: { target: { value: string } }) =>
    onChange({ ...value, [key]: key === "budget_amount" ? e.target.value || null : e.target.value });

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Field label={t("name")} htmlFor="name" required className="md:col-span-2">
        <Input id="name" value={value.name} onChange={set("name")} required />
      </Field>
      <Field label={t("customerName")} htmlFor="customer_name" hint={t("customerNameHint")} required>
        <Input id="customer_name" value={value.customer_name} onChange={set("customer_name")} required />
      </Field>
      <Field label={t("customerBin")} htmlFor="customer_bin">
        <Input
          id="customer_bin"
          value={value.customer_bin}
          onChange={set("customer_bin")}
          inputMode="numeric"
          pattern="\d{12}"
          maxLength={12}
        />
      </Field>
      <Field label={t("industry")} htmlFor="industry">
        <Select id="industry" value={value.industry} onChange={set("industry")}>
          <option value="">{tc("notSpecified")}</option>
          {reference.data?.industries.map((i) => (
            <option key={i.value} value={i.value}>
              {i.label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("region")} htmlFor="region">
        <Select id="region" value={value.region} onChange={set("region")}>
          <option value="">{tc("notSpecified")}</option>
          {reference.data?.regions.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("site")} htmlFor="site" hint={t("siteHint")}>
        <Input id="site" value={value.site} onChange={set("site")} />
      </Field>
      <Field label={t("capacity")} htmlFor="capacity_text" hint={t("capacityHint")}>
        <Input id="capacity_text" value={value.capacity_text} onChange={set("capacity_text")} />
      </Field>
      <div className="grid grid-cols-[1fr_7rem] gap-3">
        <Field label={t("budget")} htmlFor="budget_amount">
          <Input
            id="budget_amount"
            type="number"
            min={0}
            step="0.01"
            value={value.budget_amount ?? ""}
            onChange={set("budget_amount")}
          />
        </Field>
        <Field label={t("currency")} htmlFor="currency">
          <Select id="currency" value={value.currency} onChange={set("currency")}>
            {(reference.data?.currencies ?? ["KZT"]).map((c) => (
              <option key={c}>{c}</option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label={t("bank")} htmlFor="bank_name">
        <Input id="bank_name" value={value.bank_name} onChange={set("bank_name")} />
      </Field>
      {templates && onTemplateChange && (
        <Field label={t("template")} htmlFor="template">
          <Select id="template" value={templateId} onChange={(e) => onTemplateChange(e.target.value)}>
            {templates.map((tpl) => (
              <option key={tpl.id} value={tpl.id}>
                {tpl.title} ({tpl.code})
              </option>
            ))}
          </Select>
        </Field>
      )}
      {optionalSections.length > 0 && onEnabledOptionalChange && (
        <Field label={t("optionalItems")} hint={t("optionalItemsHint")} className="md:col-span-2">
          <div className="flex flex-col gap-2">
            {optionalSections.map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={enabledOptional.includes(s.id)}
                  onChange={(e) =>
                    onEnabledOptionalChange(
                      e.target.checked ? [...enabledOptional, s.id] : enabledOptional.filter((id) => id !== s.id),
                    )
                  }
                />
                {s.id}. {s.title}
              </label>
            ))}
          </div>
        </Field>
      )}
    </div>
  );
}
