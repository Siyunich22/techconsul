"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import type { ProjectStatus } from "@/lib/types";

const VARIANT: Record<ProjectStatus, "muted" | "default" | "warning" | "success" | "secondary"> = {
  draft: "muted",
  documents_uploaded: "secondary",
  analysis: "default",
  review: "warning",
  approved: "success",
  released: "success",
  archived: "muted",
};

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  const t = useTranslations("status");
  return <Badge variant={VARIANT[status]}>{t(status)}</Badge>;
}
