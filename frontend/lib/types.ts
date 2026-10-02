// Типы ответов API (зеркало Pydantic-схем backend/app/schemas).

export type UserRole = "admin" | "manager" | "expert" | "observer";

export type ProjectStatus =
  | "draft"
  | "documents_uploaded"
  | "analysis"
  | "review"
  | "approved"
  | "released"
  | "archived";

export const PROJECT_STATUSES: ProjectStatus[] = [
  "draft",
  "documents_uploaded",
  "analysis",
  "review",
  "approved",
  "released",
  "archived",
];

export interface Me {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  position: string;
  phone: string;
  organization: { id: string; name: string; bin: string };
  last_login_at: string | null;
}

export interface OrgSettings {
  report_language: "ru" | "kk" | "en";
  page_number_format: "arabic" | "page_of_total";
  default_font: string;
  default_font_size: number;
}

export interface Org {
  id: string;
  name: string;
  bin: string;
  address: string;
  phone: string;
  email: string;
  token_limit_month: number | null;
  has_logo: boolean;
  has_reference_docx: boolean;
  settings: OrgSettings;
}

export interface OrgUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  position: string;
  is_active: boolean;
  last_login_at: string | null;
  invitation_pending: boolean;
}

export interface Expert {
  id: string;
  user_id: string | null;
  full_name: string;
  specialization: string[];
  education: string;
  research_experience: string;
  years: number | null;
  email: string;
  phone: string;
  contacts: string;
  cv_filename: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type ExpertInput = Omit<Expert, "id" | "user_id" | "cv_filename" | "is_active" | "created_at" | "updated_at">;

export interface Independence {
  affiliated: boolean | null;
  participated_in_docs: boolean | null;
  is_supplier: boolean | null;
  justification: string;
  joint_experience: string;
  has_conflict?: boolean;
  is_complete?: boolean;
}

export type MemberRole = "lead" | "expert";

export interface MemberInput {
  expert_id: string;
  role: MemberRole;
  assigned_items: string[];
}

export interface Member {
  id: string;
  role: MemberRole;
  assigned_items: string[];
  expert: { id: string; full_name: string; specialization: string[]; user_id: string | null };
}

export interface ProjectListItem {
  id: string;
  name: string;
  customer_name: string;
  industry: string;
  region: string;
  budget_amount: string | null;
  currency: string;
  status: ProjectStatus;
  coverage_pct: string | null;
  integral_risk: string | null;
  owner: { id: string; full_name: string };
  created_at: string;
  updated_at: string;
}

export interface Project extends ProjectListItem {
  customer_bin: string;
  site: string;
  capacity_text: string;
  bank_name: string;
  template_version: { id: string; code: string; title: string };
  enabled_optional_items: string[];
  independence: Independence;
  members: Member[];
}

export interface PassportInput {
  name: string;
  customer_name: string;
  customer_bin: string;
  industry: string;
  region: string;
  site: string;
  capacity_text: string;
  budget_amount: string | null;
  currency: string;
  bank_name: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Reference {
  industries: { value: string; label: string }[];
  currencies: string[];
  regions: string[];
  statuses: ProjectStatus[];
}

export interface TemplateInfo {
  id: string;
  code: string;
  title: string;
  is_default: boolean;
}

export interface AssignableSection {
  id: string;
  title: string;
  expert_role: string | null;
  optional: boolean;
  extension: boolean;
}
