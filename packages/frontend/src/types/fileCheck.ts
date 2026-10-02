// Mirrors the pydantic models of packages/backend/app/routers/file_check.py
// (GET /projects/{project_id}/checklists, POST /projects/{project_id}/file-check),
// which return the deterministic engine's output
// (packages/lambda/file-check-mcp/engine.py). The UI only renders these values:
// the verdict is decided by the engine.
//
// GET uses response_model_exclude_none (unset fields are absent); POST
// serializes unset optional fields as null. Hence `?: T | null`.

/**
 * The backend currently returns READY or NOT READY; NEEDS REVIEW is rendered
 * (amber) if the API ever returns it.
 */
export type FileCheckVerdict = 'READY' | 'NOT READY' | 'NEEDS REVIEW';

// ------------------------------------------------------------------ checklists

export interface ChecklistRule {
  kind: 'present' | 'monthly' | 'period' | 'manual';
  months?: number | null;
  field?: string | null;
  from_field?: string | null;
  to_field?: string | null;
  min_count?: number | null;
}

export interface ChecklistItem {
  id: string;
  label: string;
  required: boolean;
  doc_types: string[];
  rule: ChecklistRule;
}

export interface Checklist {
  id: string;
  name: string;
  product?: string | null;
  applicant_type?: string | null;
  description?: string | null;
  items: ChecklistItem[];
  consistency_checks: string[];
  /** The checklist's indicative FOIR policy, if any. */
  foir?: ChecklistFoirPolicy | null;
}

/** A published FOIR value the checklist's limit can be compared with. */
export interface FoirLimitAlternative {
  value?: number | null;
  source?: string | null;
  url?: string | null;
}

/**
 * The checklist's `foir` block (packages/lambda/file-check-mcp/checklists.json;
 * router type: dict[str, Any]). `value` is a fraction (0.7 = 70%).
 */
export interface ChecklistFoirPolicy {
  value?: number | null;
  /** true: a FOIR above the limit is a MISMATCH (NOT READY). No bundled checklist sets it. */
  hard_limit?: boolean | null;
  /** PUBLISHED or DEMO-POLICY */
  basis?: string | null;
  /** e.g. "Varunika Loan Partners Calculator, eligibility tab: 'FOIR 70%'" */
  source?: string | null;
  url?: string | null;
  note?: string | null;
  alternatives?: FoirLimitAlternative[] | null;
  [key: string]: unknown;
}

export interface ChecklistCatalog {
  default_checklist: string;
  checklists: Checklist[];
}

/** What the checklist dropdown needs from a Checklist. */
export type FileCheckChecklistSummary = Pick<
  Checklist,
  'id' | 'name' | 'product' | 'applicant_type' | 'description' | 'foir'
>;

// ------------------------------------------------------------------ file check

export interface FileCheckRequest {
  /** ^[a-z0-9_]+$, max 64; default: the catalog's default_checklist. */
  checklist_id?: string;
  /** Full name or PAN, 1..200 characters; default: every applicant. */
  applicant?: string;
  /** YYYY-MM the "last N months" rules end at. */
  reference_month?: string;
}

export interface ChecklistRef {
  id: string;
  name?: string | null;
}

/**
 * Tokens and cost of one document's facts extraction (the file-check step's
 * one model call; the other analysis steps are not included), as recorded by
 * the backend. null: not recorded for this document.
 */
export interface DocumentUsage {
  model_id?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

/** The applicant's documents' facts-extraction usage, summed by the backend. */
export interface ApplicantUsageTotal {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  documents_with_usage: number;
  documents_total: number;
}

export interface ApplicantDocument {
  document_id?: string | null;
  document_name: string;
  /**
   * loan_application, identity_details, salary_slip, bank_statement,
   * form16_itr, credit_report, rent_agreement, pension_slip or other
   */
  doc_type: string;
  grounded?: boolean | null;
  grounding_notes: string[];
  unverified_fields: string[];
  /** Absent from backends without per-document usage. */
  usage?: DocumentUsage | null;
}

export interface ChecklistItemResult {
  item_id: string;
  item: string;
  required: boolean;
  status: 'PRESENT' | 'MISSING' | 'REVIEW';
  /** null for REVIEW (manual) items */
  ok: boolean | null;
  /** Human-readable finding citing document names and months. */
  detail: string;
  documents: string[];
  /** monthly / period items: YYYY-MM needed */
  required_months?: string[] | null;
  /** monthly / period items: YYYY-MM missing */
  missing_months?: string[] | null;
}

export interface ConsistencyResult {
  check_id: string;
  check: string;
  /**
   * MISMATCH makes the verdict NOT READY; REVIEW is a finding a person must
   * look at (listed in needs_review) that does not change the verdict.
   */
  status: 'OK' | 'MISMATCH' | 'REVIEW' | 'N/A' | 'INFO';
  detail: string;
  documents: string[];
}

export interface BankCredit {
  document_name: string;
  date?: string | null;
  amount: number;
}

export interface IncomeSummary {
  declared_net?: number | null;
  slip_net?: number | null;
  slip_gross?: number | null;
  bank_salary_credit?: number | null;
  form16_gross?: number | null;
  slip_gross_x12?: number | null;
  bank_credits: BankCredit[];
}

export interface ApplicantResult {
  applicant: string;
  pan?: string | null;
  verdict: FileCheckVerdict;
  reference_month?: string | null;
  reference_month_label?: string | null;
  documents: ApplicantDocument[];
  checklist: ChecklistItemResult[];
  consistency: ConsistencyResult[];
  income: IncomeSummary;
  /** Every reason for NOT READY, in order (MISSING / MISMATCH / REVIEW / PENDING). */
  reasons: string[];
  missing_items: string[];
  mismatches: string[];
  /**
   * Consistency findings with status REVIEW ('check: detail'); reported, the
   * verdict is unchanged. Absent from backends older than the obligations checks.
   */
  needs_review?: string[];
  /** Items a person must verify (not decided by the rules). */
  manual_review: string[];
  /**
   * Existing obligations from the bank statement and the application. Absent
   * from backends older than the obligations checks; null when not computed.
   */
  obligations?: FileCheckObligations | null;
  /** Indicative FOIR, if the checklist enables it; the lender's policy decides. */
  foir?: FileCheckFoir | null;
  /** Facts extraction cost of this applicant's documents. Absent from older backends. */
  usage_total?: ApplicantUsageTotal | null;
}

// ------------------------------------------------------------------ obligations
// The engine's obligations block (engine._obligations; router type:
// dict[str, Any]). Every field is optional and checked before use: the UI
// shows these values and never recomputes them.

export type DebitCategory =
  | 'loan_emi'
  | 'rent'
  | 'investment'
  | 'utility'
  | 'credit_card'
  | 'insurance'
  | 'other';

export type DebitChannel = 'ACH' | 'NACH' | 'ECS' | 'SI' | 'UPI' | 'other';

/** One bank debit row behind a grouped obligation. */
export interface DebitEvidence {
  document_name?: string | null;
  /** YYYY-MM-DD */
  date?: string | null;
  /** YYYY-MM */
  month?: string | null;
  amount?: number | null;
  narration?: string | null;
}

/** Debits to one payee, grouped across the statement months. */
export interface FileCheckDebit {
  payee?: string | null;
  narration?: string | null;
  category?: DebitCategory | string | null;
  channel?: DebitChannel | string | null;
  /** The median when fixed, the average when variable. */
  amount?: number | null;
  min_amount?: number | null;
  max_amount?: number | null;
  fixed?: boolean | null;
  day_of_month?: number | null;
  day_consistent?: boolean | null;
  /** YYYY-MM */
  months?: string[] | null;
  months_seen?: number | null;
  months_total?: number | null;
  count?: number | null;
  /** An amount was not found in the document text. */
  unverified?: boolean | null;
  documents?: string[] | null;
  evidence?: DebitEvidence[] | null;
  /** Matched to an EMI declared on the loan application. */
  declared?: boolean | null;
  declared_lender?: string | null;
  declared_amount?: number | null;
  /** undeclared_loan_debits only */
  kind?: 'loan_emi' | 'possible_emi' | string | null;
  [key: string]: unknown;
}

export type DeclaredEmiStatus =
  | 'matched'
  | 'partial'
  | 'amount_differs'
  | 'not_found'
  | 'not_checked';

/** An existing EMI declared on the loan application, matched to the bank. */
export interface FileCheckDeclaredEmi {
  lender?: string | null;
  loan_type?: string | null;
  amount?: number | null;
  document_name?: string | null;
  unverified?: boolean | null;
  status?: DeclaredEmiStatus | string | null;
  matched_payee?: string | null;
  bank_amount?: number | null;
  months_matched?: number | null;
  months_total?: number | null;
  matched_months?: string[] | null;
  day_of_month?: number | null;
  [key: string]: unknown;
}

export interface ObligationTotals {
  loan_emis?: number | null;
  other_fixed?: number | null;
  fixed_monthly?: number | null;
  variable_monthly_average?: number | null;
}

export interface FileCheckObligations {
  /** Bank debit details were extracted. */
  available?: boolean | null;
  declared_available?: boolean | null;
  unavailable_reasons?: string[] | null;
  /** YYYY-MM */
  statement_months?: string[] | null;
  documents?: string[] | null;
  fixed_loan_emis?: FileCheckDebit[] | null;
  other_fixed_debits?: FileCheckDebit[] | null;
  variable_debits?: FileCheckDebit[] | null;
  one_off_debits?: FileCheckDebit[] | null;
  declared_emis?: FileCheckDeclaredEmi[] | null;
  undeclared_loan_debits?: FileCheckDebit[] | null;
  totals?: ObligationTotals | null;
  /** The EMIs FOIR counts (undeclared bank EMIs and unmatched declared EMIs included). */
  foir_emi_total?: number | null;
  foir_emi_basis?: string | null;
  notes?: string[] | null;
  [key: string]: unknown;
}

/**
 * The engine's indicative FOIR block (router type: dict[str, Any]). Every
 * field is checked before use; the UI never recomputes the numbers.
 */
export interface FileCheckFoir {
  /** "indicative — the lender's policy decides" */
  label?: string | null;
  indicative?: boolean | null;
  /** fraction, e.g. 0.7 */
  foir_limit?: number | null;
  foir_limit_pct?: number | null;
  hard_limit?: boolean | null;
  limit_basis?: string | null;
  /** e.g. "Varunika Loan Partners calculator, eligibility tab: 'FOIR 70%' …" */
  limit_source?: string | null;
  limit_url?: string | null;
  limit_note?: string | null;
  limit_alternatives?: FoirLimitAlternative[] | null;
  net_monthly_income?: number | null;
  income_source?: string | null;
  income_verified?: boolean | null;
  existing_emis?: number | null;
  existing_emis_basis?: string | null;
  existing_emi_ratio?: number | null;
  existing_emi_ratio_pct?: number | null;
  max_new_emi?: number | null;
  within_limit?: boolean | null;
  /** OK, REVIEW, or MISMATCH (hard_limit only) */
  status?: string | null;
  detail?: string | null;
  [key: string]: unknown;
}

export interface PendingDocument {
  document_id?: string | null;
  document_name?: string | null;
  status?: string | null;
}

export interface FailedDocument {
  document_id?: string | null;
  document_name?: string | null;
}

export interface NoFactsDocument {
  document_id?: string | null;
  document_name?: string | null;
  reason: string;
}

export interface UnsupportedDocument {
  document_id?: string | null;
  document_name?: string | null;
  file_type?: string | null;
  reason: string;
}

export interface UnassignedDocument {
  document_id?: string | null;
  document_name?: string | null;
  doc_type: string;
}

/** Any project document the engine did not evaluate. */
export type FileCheckSkippedDocument = PendingDocument &
  Partial<NoFactsDocument & UnsupportedDocument & UnassignedDocument>;

export interface FileCheckResult {
  project_id?: string | null;
  engine_version: string;
  /** YYYY-MM-DD */
  as_of: string;
  checklist: ChecklistRef;
  overall_verdict: FileCheckVerdict;
  summary: string;
  applicants: ApplicantResult[];
  pending_documents: PendingDocument[];
  failed_documents: FailedDocument[];
  no_facts_documents: NoFactsDocument[];
  unsupported_documents: UnsupportedDocument[];
  unassigned_documents: UnassignedDocument[];
}

// Names used by the panel components.
export type FileCheckApplicant = ApplicantResult;
export type FileCheckItemRow = ChecklistItemResult;
export type FileCheckConsistencyRow = ConsistencyResult;
export type FileCheckIncome = IncomeSummary;

// ------------------------------------------------------------------ ask
// POST /projects/{project_id}/file-check/ask and GET .../file-check/usage.
// The answer is grounded on the engine's verdict (obligations and FOIR
// included) and the documents' extracted facts and page text only.

export interface FileCheckAskMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface FileCheckAskRequest {
  /** 1..1000 characters */
  question: string;
  checklist_id?: string;
  applicant?: string;
  /** The last turns of this conversation, at most 6. */
  history?: FileCheckAskMessage[];
}

export interface FileCheckAskPricing {
  input_per_million_usd: number;
  output_per_million_usd: number;
  region?: string | null;
}

export interface FileCheckAskResponse {
  answer: string;
  model_id: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  pricing: FileCheckAskPricing;
  grounded_on: { applicants: string[]; documents: string[] };
}

/** Ask calls of this project over the retention window. */
export interface FileCheckUsage {
  window_days: number;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

// ------------------------------------------------------------------ erase
// POST /projects/{project_id}/applicants/erase: permanently deletes every
// document of one applicant and the data derived from them, and removes the
// name from the webhook delivery log. `confirm` must repeat the applicant's
// name exactly (400 otherwise) and `document_ids` must be exactly the
// applicant's documents now (409 otherwise); 404 when the project has no such
// applicant.

export interface ApplicantEraseRequest {
  /** PAN (preferred: a name can match two applicants) or name. */
  applicant: string;
  confirm: string;
  /** The documents the verdict listed under the applicant (what the user confirmed). */
  document_ids: string[];
}

export interface ErasedDocument {
  document_id: string;
  name: string;
}

export interface EraseFailedDocument {
  document_id: string;
  name: string;
  error: string;
}

export interface ApplicantEraseResponse {
  applicant: string;
  documents_deleted: ErasedDocument[];
  failed: EraseFailedDocument[];
  /** Webhook delivery log entries the name was removed from; null when that failed. */
  delivery_log_redacted: number | null;
  /** What the erase does not delete, in the backend's words. */
  not_erased: string[];
  /** ISO timestamp */
  erased_at: string;
}
