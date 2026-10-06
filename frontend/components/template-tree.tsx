"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface TreeNode {
  id: string;
  title: string;
  type: "section" | "subsection" | "item";
  kind: string | null;
  requirement: string | null;
  expert_role: string | null;
  inputs: string[];
  tools: string[];
  depends_on: string[];
  requires_reference: string[];
  output_hints: { tables?: string[]; min_alternatives?: number };
  conditional: string | null;
  extension: boolean;
  optional: boolean;
  enabled: boolean;
  children: TreeNode[];
}

/** Дерево пунктов шаблона ТЗ — только чтение (редактор разделов — фаза 7). */
export function TemplateTree({ nodes, categories }: { nodes: TreeNode[]; categories?: Record<string, string> }) {
  const t = useTranslations("tree");
  const [open, setOpen] = useState<Set<string>>(() => new Set(nodes.map((n) => n.id)));
  const all = (list: TreeNode[]): string[] => list.flatMap((n) => [n.id, ...all(n.children)]);
  const toggle = (id: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-2" data-testid="template-tree">
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={() => setOpen(new Set(all(nodes)))}>
          {t("expandAll")}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen(new Set())}>
          {t("collapseAll")}
        </Button>
      </div>
      <ul className="flex flex-col gap-0.5">
        {nodes.map((n) => (
          <Node key={n.id} node={n} depth={0} open={open} toggle={toggle} categories={categories} />
        ))}
      </ul>
    </div>
  );
}

function Node({
  node,
  depth,
  open,
  toggle,
  categories,
}: {
  node: TreeNode;
  depth: number;
  open: Set<string>;
  toggle: (id: string) => void;
  categories?: Record<string, string>;
}) {
  const t = useTranslations("tree");
  const expandable = node.children.length > 0 || node.type === "item" || Boolean(node.requirement);
  const isOpen = open.has(node.id);
  return (
    <li className={cn(!node.enabled && "opacity-55")}>
      <button
        type="button"
        onClick={() => expandable && toggle(node.id)}
        className="flex w-full items-start gap-1.5 rounded px-1 py-1 text-left text-sm hover:bg-accent"
        style={{ paddingLeft: depth * 18 + 4 }}
        aria-expanded={expandable ? isOpen : undefined}
      >
        <span className="mt-0.5 size-4 shrink-0 text-muted-foreground">
          {expandable && (isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
        </span>
        <span className={cn("shrink-0 font-semibold tabular-nums", node.type === "section" && "text-primary")}>
          {node.id}
        </span>
        <span className={cn(node.type !== "item" && "font-medium")}>{node.title}</span>
        <span className="ml-auto flex shrink-0 gap-1 pl-2">
          {node.kind && node.type === "item" && <Badge variant="outline">{t(`kind.${node.kind}`)}</Badge>}
          {node.kind === "risks" && <Badge variant="outline">{t("kind.risks")}</Badge>}
          {node.extension && <Badge variant="secondary">{t("extension")}</Badge>}
          {node.optional && <Badge variant="warning">{t("optional")}</Badge>}
        </span>
      </button>
      {isOpen && node.children.length === 0 && (
        <div className="mb-2 ml-6 flex flex-col gap-1 rounded border-l-2 bg-muted/40 px-3 py-2 text-xs" style={{ marginLeft: depth * 18 + 26 }}>
          {node.requirement && <p className="text-sm">{node.requirement}</p>}
          {node.inputs.length > 0 && (
            <p>
              <span className="text-muted-foreground">{t("inputs")}: </span>
              {node.inputs.map((c) => categories?.[c] ?? c).join(", ")}
            </p>
          )}
          {node.tools.length > 0 && (
            <p>
              <span className="text-muted-foreground">{t("tools")}: </span>
              {node.tools.join(", ")}
            </p>
          )}
          {node.depends_on.length > 0 && (
            <p>
              <span className="text-muted-foreground">{t("dependsOn")}: </span>
              {node.depends_on.join(", ")}
            </p>
          )}
          {node.requires_reference.length > 0 && (
            <p>
              <span className="text-muted-foreground">{t("requiresReference")}: </span>
              {node.requires_reference.join(", ")}
            </p>
          )}
          {node.conditional && (
            <p>
              <span className="text-muted-foreground">{t("conditional")}: </span>
              {node.conditional}
            </p>
          )}
          {node.output_hints.tables?.map((tb) => (
            <p key={tb}>
              <span className="text-muted-foreground">{t("tables")}: </span>
              {tb}
            </p>
          ))}
        </div>
      )}
      {isOpen && node.children.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {node.children.map((c) => (
            <Node key={c.id} node={c} depth={depth + 1} open={open} toggle={toggle} categories={categories} />
          ))}
        </ul>
      )}
    </li>
  );
}
