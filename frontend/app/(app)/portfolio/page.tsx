"use client";

import { LayoutGrid, Plus, Rows3 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { ProjectStatusBadge } from "@/components/project-status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatMoney, formatPct } from "@/lib/format";
import { type ProjectFilters, useMe, useOrgUsers, useProjects, useReference } from "@/lib/hooks";
import { PROJECT_STATUSES, type ProjectListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

type View = "table" | "cards";
const VIEW_KEY = "portfolio.view";
const PAGE_SIZE = 25;

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "cards" ? "cards" : "table";
  } catch {
    return "table";
  }
}

export default function PortfolioPage() {
  const t = useTranslations("portfolio");
  const tc = useTranslations("common");
  const ts = useTranslations("status");
  const me = useMe();
  const canManage = me.data?.role === "manager" || me.data?.role === "admin";
  const reference = useReference();
  const users = useOrgUsers(canManage);

  const [view, setView] = useState<View>("table");
  const [filters, setFilters] = useState<ProjectFilters>({});
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => setView(readView()), []);
  useEffect(() => {
    const id = setTimeout(() => {
      setFilters((f) => ({ ...f, q: search || undefined }));
      setPage(1);
    }, 300);
    return () => clearTimeout(id);
  }, [search]);

  const projects = useProjects({ ...filters, page, page_size: PAGE_SIZE });
  const total = projects.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = Object.values(filters).some((v) => v !== undefined && v !== "" && v !== false);
  const industryLabel = (code: string) =>
    reference.data?.industries.find((i) => i.value === code)?.label.split(" — ")[1] ?? code;

  function setFilter(key: keyof ProjectFilters, value: string | boolean | undefined) {
    setFilters((f) => ({ ...f, [key]: value === "" ? undefined : value }));
    setPage(1);
  }

  function changeView(v: View) {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {}
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border bg-card p-0.5" role="group">
            <Button variant={view === "table" ? "secondary" : "ghost"} size="sm" onClick={() => changeView("table")}>
              <Rows3 /> {t("viewTable")}
            </Button>
            <Button variant={view === "cards" ? "secondary" : "ghost"} size="sm" onClick={() => changeView("cards")}>
              <LayoutGrid /> {t("viewCards")}
            </Button>
          </div>
          {canManage && (
            <Button asChild>
              <Link href="/projects/new">
                <Plus /> {t("newProject")}
              </Link>
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <Input
            className="sm:col-span-2 lg:col-span-2"
            placeholder={t("searchPlaceholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label={tc("search")}
          />
          <Select aria-label={t("filterStatus")} value={filters.status ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">{t("filterStatus")}: {tc("all")}</option>
            {PROJECT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {ts(s)}
              </option>
            ))}
          </Select>
          <Select aria-label={t("filterIndustry")} value={filters.industry ?? ""} onChange={(e) => setFilter("industry", e.target.value)}>
            <option value="">{t("filterIndustry")}: {tc("all")}</option>
            {reference.data?.industries.map((i) => (
              <option key={i.value} value={i.value}>
                {i.label}
              </option>
            ))}
          </Select>
          <Select aria-label={t("filterRegion")} value={filters.region ?? ""} onChange={(e) => setFilter("region", e.target.value)}>
            <option value="">{t("filterRegion")}: {tc("all")}</option>
            {reference.data?.regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
          {canManage && (
            <Select aria-label={t("filterOwner")} value={filters.owner_id ?? ""} onChange={(e) => setFilter("owner_id", e.target.value)}>
              <option value="">{t("filterOwner")}: {tc("all")}</option>
              {users.data
                ?.filter((u) => u.role === "manager" || u.role === "admin")
                .map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name}
                  </option>
                ))}
            </Select>
          )}
          <div className="flex items-center gap-2 sm:col-span-2 lg:col-span-2">
            <Input
              type="date"
              aria-label={t("filterFrom")}
              title={t("filterFrom")}
              value={filters.updated_from ?? ""}
              onChange={(e) => setFilter("updated_from", e.target.value)}
            />
            <span className="text-sm text-muted-foreground">{t("filterTo")}</span>
            <Input
              type="date"
              aria-label={t("filterTo")}
              value={filters.updated_to ?? ""}
              onChange={(e) => setFilter("updated_to", e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(filters.include_archived)}
              onChange={(e) => setFilter("include_archived", e.target.checked || undefined)}
            />
            {t("includeArchived")}
          </label>
          {hasFilters && (
            <Button
              variant="ghost"
              size="sm"
              className="justify-self-start"
              onClick={() => {
                setFilters({});
                setSearch("");
                setPage(1);
              }}
            >
              {tc("reset")}
            </Button>
          )}
        </CardContent>
      </Card>

      {projects.isPending ? (
        <p className="text-sm text-muted-foreground">{tc("loading")}</p>
      ) : total === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {hasFilters ? t("emptyFiltered") : t("empty")}
          </CardContent>
        </Card>
      ) : view === "table" ? (
        <ProjectTable items={projects.data!.items} industryLabel={industryLabel} />
      ) : (
        <ProjectCards items={projects.data!.items} industryLabel={industryLabel} />
      )}

      {total > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{t("total", { n: total })}</span>
          {pages > 1 && (
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                {tc("back")}
              </Button>
              <span>{t("pageOf", { page, pages })}</span>
              <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
                {tc("next")}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProjectTable({ items, industryLabel }: { items: ProjectListItem[]; industryLabel: (c: string) => string }) {
  const t = useTranslations("portfolio.col");
  const router = useRouter();
  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("name")}</TableHead>
            <TableHead>{t("customer")}</TableHead>
            <TableHead>{t("industry")}</TableHead>
            <TableHead>{t("region")}</TableHead>
            <TableHead className="text-right">{t("budget")}</TableHead>
            <TableHead>{t("status")}</TableHead>
            <TableHead className="text-right">{t("coverage")}</TableHead>
            <TableHead>{t("risk")}</TableHead>
            <TableHead>{t("owner")}</TableHead>
            <TableHead>{t("updated")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((p) => (
            <TableRow key={p.id} className="cursor-pointer" onClick={() => router.push(`/projects/${p.id}`)}>
              <TableCell className="max-w-64 font-medium">
                <Link href={`/projects/${p.id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                  {p.name}
                </Link>
              </TableCell>
              <TableCell className="max-w-48">{p.customer_name}</TableCell>
              <TableCell className="max-w-40 truncate" title={industryLabel(p.industry)}>
                {p.industry ? industryLabel(p.industry) : "—"}
              </TableCell>
              <TableCell>{p.region || "—"}</TableCell>
              <TableCell className="text-right whitespace-nowrap">{formatMoney(p.budget_amount, p.currency)}</TableCell>
              <TableCell>
                <ProjectStatusBadge status={p.status} />
              </TableCell>
              <TableCell className="text-right">{formatPct(p.coverage_pct)}</TableCell>
              <TableCell>{p.integral_risk ?? "—"}</TableCell>
              <TableCell className="whitespace-nowrap">{p.owner.full_name}</TableCell>
              <TableCell className="whitespace-nowrap">{formatDate(p.updated_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function ProjectCards({ items, industryLabel }: { items: ProjectListItem[]; industryLabel: (c: string) => string }) {
  const t = useTranslations("portfolio.col");
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((p) => (
        <Link key={p.id} href={`/projects/${p.id}`} className="group">
          <Card className={cn("h-full transition-shadow group-hover:shadow-md")}>
            <CardContent className="flex h-full flex-col gap-3 p-5">
              <div className="flex items-start justify-between gap-3">
                <h2 className="font-semibold leading-snug group-hover:underline">{p.name}</h2>
                <ProjectStatusBadge status={p.status} />
              </div>
              <p className="text-sm text-muted-foreground">{p.customer_name}</p>
              <dl className="mt-auto grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">{t("budget")}</dt>
                <dd className="text-right">{formatMoney(p.budget_amount, p.currency)}</dd>
                <dt className="text-muted-foreground">{t("industry")}</dt>
                <dd className="truncate text-right" title={industryLabel(p.industry)}>
                  {p.industry ? industryLabel(p.industry) : "—"}
                </dd>
                <dt className="text-muted-foreground">{t("region")}</dt>
                <dd className="text-right">{p.region || "—"}</dd>
                <dt className="text-muted-foreground">{t("coverage")}</dt>
                <dd className="text-right">{formatPct(p.coverage_pct)}</dd>
                <dt className="text-muted-foreground">{t("owner")}</dt>
                <dd className="text-right">{p.owner.full_name}</dd>
                <dt className="text-muted-foreground">{t("updated")}</dt>
                <dd className="text-right">{formatDate(p.updated_at)}</dd>
              </dl>
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}
