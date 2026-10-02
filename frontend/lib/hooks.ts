"use client";

import { useQuery } from "@tanstack/react-query";

import { api, queryString } from "@/lib/api";
import type {
  AssignableSection,
  Completeness,
  DocumentPage,
  ProjectDocument,
  Expert,
  Me,
  Org,
  OrgUser,
  Page,
  Project,
  ProjectListItem,
  Reference,
  TemplateInfo,
} from "@/lib/types";

export const qk = {
  me: ["me"] as const,
  org: ["org"] as const,
  orgUsers: ["org", "users"] as const,
  experts: (includeInactive = false) => ["experts", { includeInactive }] as const,
  projects: (params: object) => ["projects", params] as const,
  project: (id: string) => ["project", id] as const,
  reference: ["reference"] as const,
  templates: ["templates"] as const,
  sections: (templateId: string) => ["templates", templateId, "sections"] as const,
};

export const useMe = () => useQuery({ queryKey: qk.me, queryFn: () => api.get<Me>("/me"), retry: false });

export const useOrg = () => useQuery({ queryKey: qk.org, queryFn: () => api.get<Org>("/org") });

export const useOrgUsers = (enabled = true) =>
  useQuery({ queryKey: qk.orgUsers, queryFn: () => api.get<OrgUser[]>("/org/users"), enabled });

export const useExperts = (includeInactive = false) =>
  useQuery({
    queryKey: qk.experts(includeInactive),
    queryFn: () => api.get<Expert[]>(`/org/experts${queryString({ include_inactive: includeInactive })}`),
  });

export interface ProjectFilters {
  q?: string;
  status?: string;
  industry?: string;
  region?: string;
  owner_id?: string;
  updated_from?: string;
  updated_to?: string;
  include_archived?: boolean;
  sort?: string;
  desc?: boolean;
  page?: number;
  page_size?: number;
}

export const useProjects = (filters: ProjectFilters) =>
  useQuery({
    queryKey: qk.projects(filters),
    queryFn: () => api.get<Page<ProjectListItem>>(`/projects${queryString({ ...filters })}`),
    placeholderData: (prev) => prev,
  });

export const useProject = (id: string) =>
  useQuery({ queryKey: qk.project(id), queryFn: () => api.get<Project>(`/projects/${id}`) });

export const useReference = () =>
  useQuery({ queryKey: qk.reference, queryFn: () => api.get<Reference>("/reference"), staleTime: Infinity });

export const useTemplates = () =>
  useQuery({ queryKey: qk.templates, queryFn: () => api.get<TemplateInfo[]>("/templates"), staleTime: Infinity });

export const useTemplateSections = (templateId: string | undefined) =>
  useQuery({
    queryKey: qk.sections(templateId ?? ""),
    queryFn: () => api.get<AssignableSection[]>(`/templates/${templateId}/sections`),
    enabled: Boolean(templateId),
    staleTime: Infinity,
  });

const ACTIVE: string[] = ["uploaded", "processing", "recognized"];

export const useDocuments = (projectId: string) =>
  useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.get<ProjectDocument[]>(`/projects/${projectId}/documents`),
    // пока есть файлы в обработке — опрашиваем статусы (SSE для прогона оценки — фаза 5)
    refetchInterval: (q) => (q.state.data?.some((d) => ACTIVE.includes(d.status)) ? 2000 : false),
  });

export const useCompleteness = (projectId: string, docsVersion: string) =>
  useQuery({
    queryKey: ["completeness", projectId, docsVersion],
    queryFn: () => api.get<Completeness>(`/projects/${projectId}/completeness`),
    placeholderData: (prev) => prev,
  });

export const useDocumentPage = (projectId: string, docId: string | null, page: number) =>
  useQuery({
    queryKey: ["document-page", projectId, docId, page],
    queryFn: () => api.get<DocumentPage>(`/projects/${projectId}/documents/${docId}/pages/${page}`),
    enabled: Boolean(docId),
  });
