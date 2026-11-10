"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, MessageSquare } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";

interface CommentItem {
  id: string;
  ref: string;
  author: string;
  text: string;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

/** Комментарии экспертов к пункту, риску или выводам: обсуждение и закрытие после правки. */
export function Comments({
  projectId,
  refId,
  canComment,
}: {
  projectId: string;
  refId: string;
  canComment: boolean;
}) {
  const t = useTranslations("comments");
  const queryClient = useQueryClient();
  const key = ["comments", projectId, refId];
  const comments = useQuery({
    queryKey: key,
    queryFn: () =>
      api.get<CommentItem[]>(
        `/projects/${projectId}/comments?ref=${encodeURIComponent(refId)}`,
      ),
  });
  const [text, setText] = useState("");
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: key });
    queryClient.invalidateQueries({ queryKey: ["coverage", projectId] });
  };
  const add = useMutation({
    mutationFn: () =>
      api.post(`/projects/${projectId}/comments`, { ref: refId, text }),
    onSuccess: () => {
      setText("");
      refresh();
    },
  });
  const resolve = useMutation({
    mutationFn: (id: string) =>
      api.post(`/projects/${projectId}/comments/${id}/resolve`),
    onSuccess: refresh,
  });
  const list = comments.data ?? [];

  return (
    <section
      className="flex flex-col gap-2 border-t pt-4"
      data-testid={`comments-${refId}`}
    >
      <h3 className="flex items-center gap-1 text-xs font-semibold text-muted-foreground uppercase">
        <MessageSquare className="size-3.5" />{" "}
        {t("title", { n: list.filter((c) => !c.resolved_at).length })}
      </h3>
      {list.map((c) => (
        <div
          key={c.id}
          className={c.resolved_at ? "text-muted-foreground" : ""}
        >
          <p className="text-xs">
            <span className="font-medium">{c.author}</span> ·{" "}
            {formatDate(c.created_at)}
            {c.resolved_at && (
              <> · {t("resolved", { name: c.resolved_by ?? "—" })}</>
            )}
          </p>
          <p className={c.resolved_at ? "line-through" : ""}>{c.text}</p>
          {!c.resolved_at && canComment && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2"
              onClick={() => resolve.mutate(c.id)}
            >
              <CheckCircle2 /> {t("resolve")}
            </Button>
          )}
        </div>
      ))}
      {canComment && (
        <div className="flex flex-col gap-2">
          <Textarea
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t("placeholder")}
            aria-label={t("placeholder")}
          />
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => add.mutate()}
            disabled={!text.trim() || add.isPending}
          >
            {t("add")}
          </Button>
        </div>
      )}
      {add.error ? (
        <Alert variant="destructive">{errorMessage(add.error)}</Alert>
      ) : null}
    </section>
  );
}
