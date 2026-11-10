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
  web_search_enabled: boolean;
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

export type DocumentStatus = "uploaded" | "processing" | "recognized" | "indexed" | "extracted" | "error";

export interface ProjectDocument {
  id: string;
  parent_id: string | null;
  filename: string;
  relative_path: string;
  mime: string;
  size: number;
  kind: string;
  pages: number | null;
  category: string | null;
  category_source: "rules" | "llm" | "user" | null;
  category_confidence: number | null;
  status: DocumentStatus;
  error: string | null;
  ocr_pages: number;
  chunk_count: number;
  meta: Record<string, unknown>;
  uploaded_at: string;
  processed_at: string | null;
}

export interface DocumentPage {
  page_no: number;
  pages_total: number;
  sheet: string | null;
  text: string;
  tables: { title: string | null; cell_range: string | null; rows: string[][] }[];
  ocr: boolean;
  has_image: boolean;
}

export interface Completeness {
  documents_total: number;
  documents_processing: number;
  documents_failed: number;
  categories: { code: string; title: string; documents: number; required_by: string[] }[];
  items: { item_id: string; title: string; inputs: string[]; available: string[]; status: "full" | "partial" | "none" }[];
  missing: { code: string; title: string; partial_items: string[]; blocked_items: string[] }[];
  missing_references: { kind: string; items: string[] }[];
  items_full: number;
  items_partial: number;
  items_none: number;
}

export interface ReferenceDoc {
  id: string;
  title: string;
  kind: "ndt" | "law" | "norm" | "standard" | "other";
  filename: string;
  size: number;
  pages: number | null;
  valid_from: string | null;
  valid_to: string | null;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  created_at: string;
}

// --- паспорт проекта (фаза 4) ---

export interface PassportFact {
  value: string;
  unit: string;
  refs: string[];
  quote: string;
  confidence: number;
  manual: boolean;
}

/** Строка таблицы паспорта: колонки — строки (по макету), плюс источник на уровне строки. */
export interface PassportRow {
  refs: string[];
  quote: string;
  confidence: number;
  manual: boolean;
  [column: string]: string | string[] | number | boolean;
}

export type PassportSection = Record<string, PassportFact | PassportRow[] | null>;
export type PassportData = Record<string, PassportSection>;

export interface Discrepancy {
  field: string;
  title: string;
  description: string;
  severity: "high" | "medium" | "low";
  values: { value: string; refs: string[] }[];
  resolution: string;
}

export interface PassportSource {
  document_id: string;
  filename: string;
  category: string | null;
  page: number | null;
  sheet: string | null;
}

export type PassportStatus = "empty" | "extracting" | "draft" | "confirmed" | "failed";

export interface Passport {
  status: PassportStatus;
  data: PassportData;
  discrepancies: Discrepancy[];
  sources: Record<string, PassportSource>;
  progress: { stage: "map" | "reduce" | ""; done: number; total: number };
  warnings: string[];
  error: string | null;
  extracted_at: string | null;
  updated_at: string | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
}

export interface PassportLayoutSection {
  key: string;
  title: string;
  fields: { key: string; title: string }[];
  tables: { key: string; title: string; columns: { key: string; title: string }[] }[];
}

// --- оценка (фаза 5) ---

export type RunStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type ItemStatus = "pending" | "running" | "done" | "failed";
export type DisclosureStatus = "full" | "partial" | "not_disclosed" | "not_applicable";

export interface AssessmentRun {
  id: string;
  status: RunStatus;
  items_total: number;
  items_done: number;
  items_failed: number;
  cost_usd: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  web_search: boolean;
}

export interface AssessmentItem {
  item_id: string;
  title: string;
  section_id: string;
  section_title: string;
  kind: string | null;
  status: ItemStatus;
  version: number;
  disclosure_status: DisclosureStatus | null;
  summary: string | null;
  error: string | null;
  review_status: string;
  instruction: string;
  started_at: string | null;
  finished_at: string | null;
  reused: boolean;
  author: "ai" | "system" | "user";
}

export interface Assessment {
  run: AssessmentRun | null;
  items: AssessmentItem[];
}

export interface AssessmentEstimate {
  items_ai: number;
  items_info: number;
  items_reused: number;
  items_strong: number;
  budget_usd: string | null;
  resume_items: number;
  resume_cost_usd_min: string;
  resume_cost_usd_max: string;
  cost_usd_min: string;
  cost_usd_max: string;
  minutes: number;
  passport_confirmed: boolean;
  web_search: boolean;
  warnings: string[];
}

export interface SectionPayload {
  item_id: string;
  disclosure_status: DisclosureStatus;
  summary: string;
  analysis_md: string;
  tables: { title: string; columns: string[]; rows: string[][] }[];
  findings: { text: string; source_ids: string[] }[];
  remarks: string[];
  recommendations: string[];
  assumptions: string[];
  gap: { reason: string; impact_on_conclusions: string; assumptions: string; recommendation: string } | null;
  confidence: number;
}

export interface AssessmentSource {
  sid: string;
  type: "doc" | "reference" | "web" | "project";
  document_id: string | null;
  filename: string | null;
  page: number | null;
  sheet: string | null;
  cell_range: string | null;
  url: string | null;
  title: string;
  accessed_at: string | null;
}

export interface AssessmentItemDetail extends AssessmentItem {
  payload: SectionPayload | null;
  sources: AssessmentSource[];
  warnings: string[];
}

export interface ReportVersion {
  id: string;
  number: number;
  version: string;
  status: "draft" | "released";
  render_status: "queued" | "rendering" | "ready" | "failed";
  error: string | null;
  filename: string;
  has_docx: boolean;
  has_pdf: boolean;
  sha256: string | null;
  created_at: string;
  finished_at: string | null;
}

export type Zone = "red" | "yellow" | "green";
export type ReviewStatus = "ai_draft" | "in_review" | "approved";

export interface Risk {
  code: string;
  group: string;
  group_title: string;
  title: string;
  mandatory: boolean;
  status: string;
  version: number;
  probability: number | null;
  impact: number | null;
  zone: Zone | null;
  rationale: string | null;
  mitigation: string[];
  related_items: string[];
  review_status: ReviewStatus;
  error: string | null;
  warnings: string[];
}

export interface Risks {
  run_id: string | null;
  status: "queued" | "running" | "done" | "failed" | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  probability_scale: Record<string, string>;
  impact_scale: Record<string, string>;
  zone_matrix: Record<string, Zone>;
  risks: Risk[];
  integral: {
    level: Zone | null;
    red: number;
    yellow: number;
    green: number;
    scored: number;
    total: number;
    index: number;
  };
  conclusions: SectionPayload | null;
  conclusions_review: ReviewStatus;
  sources: AssessmentSource[];
  estimate_usd_min: string;
  estimate_usd_max: string;
}

export interface CoverageRow {
  ref: string;
  kind: "item" | "risk" | "conclusions";
  title: string;
  section: string;
  status: string;
  version: number;
  disclosure_status: DisclosureStatus | null;
  sources: number;
  experts: string[];
  review_status: ReviewStatus;
  comments: number;
  edited: boolean;
}

export interface Coverage {
  run_id: string | null;
  rows: CoverageRow[];
  issues: { level: "error" | "warning"; rule: string; message: string; ref: string | null }[];
  approved: number;
  total: number;
  can_release: boolean;
  llm_review_status: string | null;
  llm_review_at: string | null;
}
