"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, ShieldAlert, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";

import { DocumentsTab } from "@/components/documents/documents-tab";
import { IndependenceForm } from "@/components/project/independence-form";
import { TemplateTree, type TreeNode } from "@/components/template-tree";
import { PassportForm } from "@/components/project/passport-form";
import { TeamForm } from "@/components/project/team-form";
import { ProjectStatusBadge } from "@/components/project-status-badge";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { qk, useExperts, useMe, useProject, useTemplateSections } from "@/lib/hooks";
import type { Independence, MemberInput, PassportInput, Project } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "overview" | "independence" | "team" | "documents" | "sections";
const TABS: Tab[] = ["overview", "independence", "team", "documents", "sections"];
// Вкладки следующих фаз (TZ §4.5 А–Ж) — номер фазы из TZ §12.
const FUTURE_TABS: [string, number][] = [
  ["extracted", 4],
  ["assessment", 5],
  ["risks", 6],
  ["coverage", 7],
  ["result", 8],
];

function toPassport(p: Project): PassportInput {
  return {
    name: p.name,
    customer_name: p.customer_name,
    customer_bin: p.customer_bin,
    industry: p.industry,
    region: p.region,
    site: p.site,
    capacity_text: p.capacity_text,
    budget_amount: p.budget_amount,
    currency: p.currency,
    bank_name: p.bank_name,
  };
}

function toMembers(p: Project): MemberInput[] {
  return p.members.map((m) => ({ expert_id: m.expert.id, role: m.role, assigned_items: m.assigned_items }));
}

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const t = useTranslations("project");
  const tc = useTranslations("common");
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useMe();
  const canManage = me.data?.role === "manager" || me.data?.role === "admin";
  const project = useProject(id);
  const [tab, setTab] = useState<Tab>("overview");
  const [deepLink, setDeepLink] = useState<{ docId: string; page: number; q?: string }>();

  // ?tab=documents&doc=<id>&page=<n>&q=<фрагмент> — переход по ссылке-источнику
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("tab") as Tab | null;
    if (requested && TABS.includes(requested)) setTab(requested);
    const doc = params.get("doc");
    if (doc) setDeepLink({ docId: doc, page: Number(params.get("page") ?? 1), q: params.get("q") ?? undefined });
  }, []);

  const invalidate = (p: Project) => {
    queryClient.setQueryData(qk.project(id), p);
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  };
  const archive = useMutation({
    mutationFn: (action: "archive" | "unarchive") => api.post<Project>(`/projects/${id}/${action}`),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/projects/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      router.push("/portfolio");
    },
  });

  if (project.isPending) return <p className="text-sm text-muted-foreground">{tc("loading")}</p>;
  if (project.isError) return <Alert variant="destructive">{t("notFound")}</Alert>;
  const p = project.data;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link href="/portfolio" className="text-sm text-muted-foreground hover:underline">
            ← {tc("back")}
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{p.name}</h1>
            <ProjectStatusBadge status={p.status} />
            {p.independence.is_complete ? (
              <Badge variant="success">
                <ShieldCheck className="size-3.5" /> {t("independence")}
              </Badge>
            ) : (
              <Badge variant="destructive" title={t("indep.incomplete")}>
                <ShieldAlert className="size-3.5" /> {t("independence")}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {p.customer_name} · {formatMoney(p.budget_amount, p.currency)} · {t("createdBy")}: {p.owner.full_name} ·{" "}
            {t("template")}: {p.template_version.code}
          </p>
        </div>
        {canManage && (
          <div className="flex gap-2">
            {p.status === "archived" ? (
              <Button variant="outline" onClick={() => archive.mutate("unarchive")}>
                <ArchiveRestore /> {t("unarchive")}
              </Button>
            ) : (
              <Button variant="outline" onClick={() => archive.mutate("archive")}>
                <Archive /> {t("archive")}
              </Button>
            )}
            {p.status === "draft" && (
              <Button
                variant="outline"
                className="text-destructive"
                onClick={() => window.confirm(tc("confirmDelete")) && remove.mutate()}
              >
                <Trash2 /> {t("deleteDraft")}
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="flex gap-1 overflow-x-auto border-b">
        {TABS.map((key) => (
          <TabButton key={key} active={tab === key} onClick={() => setTab(key)}>
            {key === "overview" || key === "documents" || key === "sections" ? t(`tabs.${key}`) : t(key)}
          </TabButton>
        ))}
        {FUTURE_TABS.map(([key, phase]) => (
          <TabButton key={key} disabled title={tc("phase", { n: phase })}>
            {t(`tabs.${key}`)}
          </TabButton>
        ))}
      </div>

      {tab === "overview" && <OverviewTab project={p} canEdit={canManage} onSaved={invalidate} />}
      {tab === "independence" && <IndependenceTab project={p} canEdit={canManage} onSaved={invalidate} />}
      {tab === "team" && <TeamTab project={p} canEdit={canManage} onSaved={invalidate} />}
      {tab === "documents" && <DocumentsTab projectId={p.id} canDelete={canManage} initialPreview={deepLink} />}
      {tab === "sections" && <SectionsTab projectId={p.id} templateId={p.template_version.id} />}
    </div>
  );
}

function TabButton({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={cn(
        "-mb-px border-b-2 border-transparent px-3 py-2 text-sm font-medium whitespace-nowrap text-muted-foreground",
        active && "border-primary text-foreground",
        !disabled && !active && "hover:text-foreground",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      {children}
    </button>
  );
}

function SaveBar({ pending, error, saved, onSave }: { pending: boolean; error: unknown; saved: boolean; onSave: () => void }) {
  const tc = useTranslations("common");
  return (
    <div className="mt-5 flex items-center gap-3">
      <Button onClick={onSave} disabled={pending}>
        {pending ? tc("saving") : tc("save")}
      </Button>
      {saved && <span className="text-sm text-emerald-700">{tc("saved")}</span>}
      {error ? <span className="text-sm text-destructive">{errorMessage(error)}</span> : null}
    </div>
  );
}

function useSave<T>(id: string, onSaved: (p: Project) => void, fn: (body: T) => Promise<Project>) {
  const [saved, setSaved] = useState(false);
  const m = useMutation({
    mutationFn: fn,
    onSuccess: (p) => {
      onSaved(p);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });
  return { ...m, saved };
}

type TabProps = { project: Project; canEdit: boolean; onSaved: (p: Project) => void };

function OverviewTab({ project, canEdit, onSaved }: TabProps) {
  const [value, setValue] = useState(toPassport(project));
  const sections = useTemplateSections(project.template_version.id);
  const [enabledOptional, setEnabledOptional] = useState(project.enabled_optional_items);
  useEffect(() => setValue(toPassport(project)), [project]);
  const save = useSave(project.id, onSaved, (body: object) => api.patch<Project>(`/projects/${project.id}`, body));

  return (
    <Card>
      <CardContent className="p-6">
        <fieldset disabled={!canEdit}>
          <PassportForm
            value={value}
            onChange={setValue}
            optionalSections={sections.data?.filter((s) => s.optional)}
            enabledOptional={enabledOptional}
            onEnabledOptionalChange={setEnabledOptional}
          />
        </fieldset>
        {canEdit && (
          <SaveBar
            pending={save.isPending}
            error={save.error}
            saved={save.saved}
            onSave={() =>
              save.mutate({ ...value, budget_amount: value.budget_amount || null, enabled_optional_items: enabledOptional })
            }
          />
        )}
      </CardContent>
    </Card>
  );
}

function IndependenceTab({ project, canEdit, onSaved }: TabProps) {
  const t = useTranslations("project.indep");
  const [value, setValue] = useState<Independence>(project.independence);
  const save = useSave(project.id, onSaved, (body: Independence) =>
    api.patch<Project>(`/projects/${project.id}`, { independence: body }),
  );
  return (
    <Card>
      <CardContent className="p-6">
        <Alert variant={project.independence.is_complete ? "success" : "warning"} className="mb-5">
          {project.independence.is_complete ? t("complete") : t("incomplete")}
        </Alert>
        <fieldset disabled={!canEdit}>
          <IndependenceForm value={value} onChange={setValue} />
        </fieldset>
        {canEdit && <SaveBar pending={save.isPending} error={save.error} saved={save.saved} onSave={() => save.mutate(value)} />}
      </CardContent>
    </Card>
  );
}

function TeamTab({ project, canEdit, onSaved }: TabProps) {
  const [value, setValue] = useState(toMembers(project));
  const experts = useExperts();
  const sections = useTemplateSections(project.template_version.id);
  const queryClient = useQueryClient();
  const save = useSave(project.id, onSaved, async (body: MemberInput[]) => {
    await api.put(`/projects/${project.id}/members`, body);
    return queryClient.fetchQuery({
      queryKey: qk.project(project.id),
      queryFn: () => api.get<Project>(`/projects/${project.id}`),
      staleTime: 0,
    });
  });
  return (
    <Card>
      <CardContent className="p-6">
        <fieldset disabled={!canEdit}>
          <TeamForm
            value={value}
            onChange={setValue}
            experts={experts.data ?? []}
            sections={sections.data ?? []}
            enabledOptional={project.enabled_optional_items}
          />
        </fieldset>
        {canEdit && <SaveBar pending={save.isPending} error={save.error} saved={save.saved} onSave={() => save.mutate(value)} />}
      </CardContent>
    </Card>
  );
}

function SectionsTab({ projectId, templateId }: { projectId: string; templateId: string }) {
  const items = useQuery({
    queryKey: ["project-items", projectId],
    queryFn: () => api.get<TreeNode[]>(`/projects/${projectId}/items`),
  });
  const template = useQuery({
    queryKey: ["template", templateId],
    queryFn: () => api.get<{ document_categories: Record<string, string> }>(`/templates/${templateId}`),
  });
  return (
    <Card>
      <CardContent className="p-6">
        {items.data && <TemplateTree nodes={items.data} categories={template.data?.document_categories} />}
      </CardContent>
    </Card>
  );
}
