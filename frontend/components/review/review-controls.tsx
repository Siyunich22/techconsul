"use client";

import { Check, Undo2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ReviewStatus } from "@/lib/types";

/** Статус утверждения экспертом и кнопки «Утвердить» / «Вернуть на проверку». */
export function ReviewControls({
  status,
  canReview,
  onReview,
}: {
  status: ReviewStatus;
  canReview: boolean;
  onReview: (status: ReviewStatus) => void;
}) {
  const t = useTranslations("review");
  return (
    <span className="flex items-center gap-1">
      <Badge
        variant={
          status === "approved"
            ? "success"
            : status === "in_review"
              ? "warning"
              : "muted"
        }
      >
        {t(`status.${status}`)}
      </Badge>
      {canReview && status !== "approved" && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onReview("approved")}
          title={t("approve")}
        >
          <Check />
        </Button>
      )}
      {canReview && status === "approved" && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onReview("in_review")}
          title={t("reopen")}
        >
          <Undo2 />
        </Button>
      )}
    </span>
  );
}
