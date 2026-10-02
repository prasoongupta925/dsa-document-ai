// Display helpers for the File Check panel. Nothing here decides a verdict:
// statuses and verdicts are mapped to colours / icons / CSV cells only.
import type {
  ApplicantDocument,
  ApplicantEraseRequest,
  ApplicantEraseResponse,
  ApplicantUsageTotal,
  ChecklistFoirPolicy,
  DocumentUsage,
  FileCheckApplicant,
  FileCheckChecklistSummary,
  FileCheckDebit,
  FileCheckDeclaredEmi,
  FileCheckObligations,
  FileCheckResult,
  FileCheckSkippedDocument,
} from '../types/fileCheck';
import { ApiError, apiErrorDetail } from './apiError';

/** Plan B's salaried personal-loan checklist (the engine's shipped default). */
export const SALARIED_PERSONAL_LOAN_ID = 'salaried_personal_loan';

export type VerdictTone = 'ready' | 'notReady' | 'review' | 'unknown';
export type StatusTone = 'ok' | 'bad' | 'review' | 'info' | 'muted';

/** 'not_ready' / 'Needs-Review' -> 'NOT READY' / 'NEEDS REVIEW'. */
export function normalizeStatus(value: unknown): string {
  return String(value ?? '')
    .trim()
    .toUpperCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ');
}

const REVIEW_STATUSES = new Set([
  'REVIEW',
  'NEEDS REVIEW',
  'MANUAL',
  'MANUAL REVIEW',
]);

export function verdictTone(verdict: unknown): VerdictTone {
  const v = normalizeStatus(verdict);
  if (v === 'READY') return 'ready';
  if (v === 'NOT READY') return 'notReady';
  if (REVIEW_STATUSES.has(v)) return 'review';
  return 'unknown';
}

/** Checklist item status (PRESENT / MISSING / REVIEW) -> tone. */
export function itemTone(status: unknown, required = true): StatusTone {
  const s = normalizeStatus(status);
  if (s === 'PRESENT' || s === 'OK') return 'ok';
  if (REVIEW_STATUSES.has(s)) return 'review';
  if (s === 'MISSING') return required ? 'bad' : 'muted';
  return 'info';
}

/** Consistency status (OK / MISMATCH / REVIEW / INFO / N/A) -> tone. */
export function consistencyTone(status: unknown): StatusTone {
  const s = normalizeStatus(status);
  if (s === 'OK') return 'ok';
  if (s === 'MISMATCH') return 'bad';
  if (REVIEW_STATUSES.has(s)) return 'review';
  if (s === 'N/A' || s === 'NA') return 'muted';
  return 'info';
}

/** Engine reason lines start with 'MISSING –', 'MISMATCH –', 'REVIEW –', 'PENDING –'. */
export function reasonTone(reason: string): StatusTone {
  const head = normalizeStatus(reason.split(/\s[–—-]\s|:/)[0]);
  if (head === 'MISSING' || head === 'MISMATCH') return 'bad';
  if (REVIEW_STATUSES.has(head)) return 'review';
  return 'info';
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function toSummary(value: unknown): FileCheckChecklistSummary | null {
  if (typeof value === 'string') {
    return str(value) ? { id: value.trim(), name: value.trim() } : null;
  }
  if (!value || typeof value !== 'object') return null;
  const o = value as Record<string, unknown>;
  const id = str(o.id) ?? str(o.checklist_id);
  if (!id) return null;
  const summary: FileCheckChecklistSummary = {
    id,
    name: str(o.name) ?? str(o.label) ?? id,
    product: str(o.product),
    applicant_type: str(o.applicant_type),
    description: str(o.description),
  };
  if (o.foir && typeof o.foir === 'object' && !Array.isArray(o.foir)) {
    summary.foir = o.foir as ChecklistFoirPolicy;
  }
  return summary;
}

/**
 * Accepts either a bare list of checklists or the engine's list_checklists
 * object ({default_checklist, checklists}) or an {items} wrapper.
 */
export function normalizeChecklists(raw: unknown): {
  checklists: FileCheckChecklistSummary[];
  defaultId: string | null;
} {
  let list: unknown[] = [];
  let defaultId: string | null = null;
  if (Array.isArray(raw)) {
    list = raw;
  } else if (raw && typeof raw === 'object') {
    const o = raw as Record<string, unknown>;
    if (Array.isArray(o.checklists)) list = o.checklists;
    else if (Array.isArray(o.items)) list = o.items;
    defaultId = str(o.default_checklist) ?? str(o.default_checklist_id);
  }
  const seen = new Set<string>();
  const checklists: FileCheckChecklistSummary[] = [];
  for (const entry of list) {
    const summary = toSummary(entry);
    if (summary && !seen.has(summary.id)) {
      seen.add(summary.id);
      checklists.push(summary);
    }
  }
  if (defaultId && !seen.has(defaultId)) defaultId = null;
  return { checklists, defaultId };
}

/**
 * The checklist the panel selects first: the API's default (the deployment's
 * configured default), else the salaried personal-loan checklist, else the
 * first salaried personal-loan product, else the first checklist.
 */
export function pickDefaultChecklistId(
  checklists: FileCheckChecklistSummary[],
  apiDefault?: string | null,
): string {
  if (apiDefault && checklists.some((c) => c.id === apiDefault)) {
    return apiDefault;
  }
  const byId = checklists.find((c) => c.id === SALARIED_PERSONAL_LOAN_ID);
  if (byId) return byId.id;
  const byProduct = checklists.find(
    (c) => c.product === 'personal_loan' && c.applicant_type === 'salaried',
  );
  return byProduct?.id ?? checklists[0]?.id ?? '';
}

const MONTH_ABBR = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/** '2026-07' -> 'Jul 2026' (the engine's month wording). */
export function formatYearMonth(ym: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(ym.trim());
  if (!m) return ym;
  const month = Number(m[2]);
  return month >= 1 && month <= 12 ? `${MONTH_ABBR[month - 1]} ${m[1]}` : ym;
}

const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

/** 600000 -> '₹6,00,000' (Indian digit grouping); null -> '–'. */
export function formatInr(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? INR.format(value)
    : '–';
}

/** 5 -> '5th', 22 -> '22nd' (the engine's day-of-month wording). */
export function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  const suffix = ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th';
  return `${n}${suffix}`;
}

/** ['2026-03', …, '2026-08'] -> 'Mar 2026 – Aug 2026'; one month -> 'May 2026'. */
export function monthRangeLabel(months: string[] | null | undefined): string {
  const list = (months ?? []).filter((m) => typeof m === 'string' && m);
  if (list.length === 0) return '';
  const sorted = [...list].sort();
  const first = formatYearMonth(sorted[0]);
  const last = formatYearMonth(sorted[sorted.length - 1]);
  return first === last ? first : `${first} – ${last}`;
}

/** An http(s) URL from the engine, else null (never a javascript: link). */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const url = value.trim();
  return /^https?:\/\/[^\s]+$/i.test(url) ? url : null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

// ------------------------------------------------------------------ obligations

/**
 * How a bank loan EMI relates to the application: matched to a declared EMI,
 * matched only partly, a different amount, not on the application, or not
 * checked (declared EMIs were not extracted).
 */
export type EmiDeclarationStatus =
  | 'matched'
  | 'partial'
  | 'amount_differs'
  | 'declared'
  | 'not_declared'
  | 'not_checked';

export interface LoanEmiRow {
  debit: FileCheckDebit;
  declared: FileCheckDeclaredEmi | null;
  status: EmiDeclarationStatus;
}

const MATCH_STATUSES = new Set(['matched', 'partial', 'amount_differs']);

/** The fixed loan EMIs with the declared EMI each one was matched to. */
export function loanEmiRows(obligations: FileCheckObligations): LoanEmiRow[] {
  const declared = obligations.declared_emis ?? [];
  const used = new Set<FileCheckDeclaredEmi>();
  return (obligations.fixed_loan_emis ?? []).map((debit) => {
    const match =
      declared.find(
        (d) =>
          !used.has(d) &&
          MATCH_STATUSES.has(String(d.status)) &&
          !!d.matched_payee &&
          d.matched_payee === debit.payee,
      ) ?? null;
    if (match) used.add(match);
    let status: EmiDeclarationStatus;
    if (match) status = match.status as EmiDeclarationStatus;
    else if (debit.declared) status = 'declared';
    else if (obligations.declared_available === false) status = 'not_checked';
    else status = 'not_declared';
    return { debit, declared: match, status };
  });
}

/** Declared EMIs with no bank debit behind them (not found / not checked). */
export function unmatchedDeclaredEmis(
  obligations: FileCheckObligations,
): FileCheckDeclaredEmi[] {
  return (obligations.declared_emis ?? []).filter(
    (d) => d.status === 'not_found' || d.status === 'not_checked',
  );
}

/** Payees the engine flags as a possible (undeclared, uncategorised) EMI. */
export function possibleEmiPayees(
  obligations: FileCheckObligations,
): Set<string> {
  return new Set(
    (obligations.undeclared_loan_debits ?? [])
      .filter((d) => d.kind === 'possible_emi' && d.payee)
      .map((d) => d.payee as string),
  );
}

export function emiStatusTone(status: EmiDeclarationStatus): StatusTone {
  if (status === 'matched' || status === 'declared') return 'ok';
  if (status === 'not_checked') return 'muted';
  return 'review';
}

/** '₹8,200 on the 5th, 6 of 6 months (Mar 2026 – Aug 2026), ACH' */
export function debitSummary(debit: FileCheckDebit): string {
  const parts: string[] = [];
  const amount = finite(debit.amount);
  const min = finite(debit.min_amount);
  const max = finite(debit.max_amount);
  if (amount !== null) {
    parts.push(
      debit.fixed === false && min !== null && max !== null && min !== max
        ? `${formatInr(amount)} average (${formatInr(min)}–${formatInr(max)})`
        : formatInr(amount),
    );
  }
  const day = finite(debit.day_of_month);
  if (day !== null) parts.push(`on the ${ordinal(day)}`);
  const seen = finite(debit.months_seen);
  const total = finite(debit.months_total);
  if (seen !== null) {
    const range = monthRangeLabel(debit.months);
    parts.push(
      `${total !== null ? `${seen} of ${total}` : seen} month${
        (total ?? seen) === 1 ? '' : 's'
      }${range ? ` (${range})` : ''}`,
    );
  }
  if (debit.channel && debit.channel !== 'other') parts.push(debit.channel);
  if (debit.unverified) parts.push('amount not verified against the text');
  return parts.join(', ');
}

// ------------------------------------------------------------------ CSV

export type FindingSection =
  | 'Checklist'
  | 'Consistency'
  | 'Obligations'
  | 'Documents'
  | 'Not checked';

/**
 * Usage cells of a CSV row (documents read and their total): the tokens and
 * cost of each document's facts extraction, the file-check step's one model
 * call; the other analysis steps are not included.
 */
export interface FindingUsage {
  model_id?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface FileCheckFinding {
  applicant: string;
  verdict: string;
  checklist: string;
  section: FindingSection;
  item: string;
  status: string;
  detail: string;
  documents: string[];
  usage?: FindingUsage | null;
}

export const CSV_HEADERS = [
  'Applicant',
  'Verdict',
  'Checklist',
  'Section',
  'Item',
  'Status',
  'Detail',
  'Documents',
  // The facts extraction of each document only (see FindingUsage).
  'Facts extraction model',
  'Facts extraction input tokens',
  'Facts extraction output tokens',
  'Facts extraction cost (USD)',
] as const;

const SKIPPED_GROUPS: {
  key:
    | 'pending_documents'
    | 'failed_documents'
    | 'no_facts_documents'
    | 'unsupported_documents'
    | 'unassigned_documents';
  status: string;
}[] = [
  { key: 'pending_documents', status: 'PENDING' },
  { key: 'failed_documents', status: 'FAILED' },
  { key: 'no_facts_documents', status: 'NO FACTS' },
  { key: 'unsupported_documents', status: 'UNSUPPORTED' },
  { key: 'unassigned_documents', status: 'UNASSIGNED' },
];

export interface SkippedDocumentRow {
  status: string;
  document: FileCheckSkippedDocument;
}

/** Project documents the engine did not evaluate, in a fixed order. */
export function skippedDocuments(
  result: FileCheckResult,
): SkippedDocumentRow[] {
  return SKIPPED_GROUPS.flatMap(({ key, status }) =>
    (result[key] ?? []).map((document) => ({ status, document })),
  );
}

function skippedDetail({ status, document }: SkippedDocumentRow): string {
  if (document.reason) return document.reason;
  if (status === 'PENDING') {
    return `still being analysed${document.status ? ` (${document.status})` : ''}`;
  }
  if (status === 'FAILED') return 'document analysis failed';
  if (status === 'UNASSIGNED') {
    return `could not be matched to an applicant${document.doc_type ? ` (${document.doc_type})` : ''}`;
  }
  return '';
}

const EMI_STATUS_CSV: Record<EmiDeclarationStatus, string> = {
  matched: 'MATCHED',
  partial: 'PARTIAL',
  amount_differs: 'AMOUNT DIFFERS',
  declared: 'DECLARED',
  not_declared: 'NOT DECLARED',
  not_checked: 'NOT CHECKED',
};

function declaredLabel(d: FileCheckDeclaredEmi): string {
  const who = d.lender || 'lender not named';
  return `${formatInr(d.amount)} ${who}${d.loan_type ? ` (${d.loan_type})` : ''}`;
}

/** Obligations & FOIR rows of one applicant (CSV only; the UI renders them itself). */
export function obligationFindings(
  applicant: FileCheckApplicant,
  checklist: string,
): FileCheckFinding[] {
  const base = {
    applicant: applicant.applicant,
    verdict: applicant.verdict,
    checklist,
    section: 'Obligations' as const,
  };
  const findings: FileCheckFinding[] = [];
  const o = applicant.obligations;
  if (o && typeof o === 'object') {
    for (const row of loanEmiRows(o)) {
      const d = row.declared;
      let detail = debitSummary(row.debit);
      if (d) {
        detail += `; declared ${declaredLabel(d)}${d.document_name ? ` [${d.document_name}]` : ''}`;
      } else if (row.status === 'not_declared') {
        detail += '; not on the loan application';
      }
      findings.push({
        ...base,
        item: `Loan EMI – ${row.debit.payee || row.debit.narration || '–'}`,
        status: EMI_STATUS_CSV[row.status],
        detail,
        documents: row.debit.documents ?? [],
      });
    }
    for (const d of unmatchedDeclaredEmis(o)) {
      findings.push({
        ...base,
        item: `Declared EMI – ${d.lender || 'lender not named'}`,
        status: d.status === 'not_found' ? 'NOT FOUND' : 'NOT CHECKED',
        detail:
          d.status === 'not_found'
            ? `${declaredLabel(d)} declared, not found in the bank debits (still counted toward FOIR)`
            : `${declaredLabel(d)} declared; bank debits not available`,
        documents: d.document_name ? [d.document_name] : [],
      });
    }
    const possible = possibleEmiPayees(o);
    for (const debit of o.other_fixed_debits ?? []) {
      findings.push({
        ...base,
        item: `Fixed debit – ${debit.payee || debit.narration || '–'}`,
        status:
          debit.payee && possible.has(debit.payee) ? 'POSSIBLE EMI' : 'FIXED',
        detail: [debit.category, debitSummary(debit)]
          .filter(Boolean)
          .join(': '),
        documents: debit.documents ?? [],
      });
    }
    const totals = o.totals;
    if (totals && typeof totals === 'object') {
      const cells = (
        [
          ['loan EMIs', totals.loan_emis],
          ['other fixed', totals.other_fixed],
          ['fixed monthly', totals.fixed_monthly],
          ['variable monthly average', totals.variable_monthly_average],
        ] as const
      )
        .filter(([, v]) => finite(v) !== null)
        .map(([label, v]) => `${label} ${formatInr(v)}`);
      if (cells.length > 0) {
        findings.push({
          ...base,
          item: 'Monthly obligations (totals)',
          status: 'INFO',
          detail: cells.join('; '),
          documents: o.documents ?? [],
        });
      }
    }
  }
  const f = applicant.foir;
  if (f && typeof f === 'object') {
    const ratio = finite(f.existing_emi_ratio_pct);
    const limit = finite(f.foir_limit_pct);
    const maxNew = finite(f.max_new_emi);
    const parts: string[] = [];
    if (ratio !== null) {
      parts.push(
        `FOIR ${ratio}%${limit !== null ? ` vs limit ${limit}%` : ''}`,
      );
    } else if (f.detail) {
      parts.push(f.detail);
    }
    if (maxNew !== null) parts.push(`max new EMI ${formatInr(maxNew)}`);
    if (f.limit_source) parts.push(`limit source: ${f.limit_source}`);
    parts.push(f.label || "indicative — the lender's policy decides");
    findings.push({
      ...base,
      item: 'FOIR (indicative)',
      status: f.status || 'INFO',
      detail: parts.join('; '),
      documents: o?.documents ?? [],
    });
  }
  return findings;
}

// ------------------------------------------------------------------ usage

/** A document's recorded reading usage; null when absent or malformed. */
export function documentUsage(
  doc: Pick<ApplicantDocument, 'usage'>,
): DocumentUsage | null {
  const u = doc.usage;
  if (!u || typeof u !== 'object') return null;
  const input = finite(u.input_tokens);
  const output = finite(u.output_tokens);
  const cost = finite(u.cost_usd);
  if (input === null || output === null || cost === null) return null;
  return {
    model_id: typeof u.model_id === 'string' && u.model_id ? u.model_id : null,
    input_tokens: input,
    output_tokens: output,
    cost_usd: cost,
  };
}

/** The applicant's usage total; null for backends without usage. */
export function applicantUsageTotal(
  applicant: Pick<FileCheckApplicant, 'usage_total'>,
): ApplicantUsageTotal | null {
  const u = applicant.usage_total;
  if (!u || typeof u !== 'object') return null;
  const input = finite(u.input_tokens);
  const output = finite(u.output_tokens);
  const cost = finite(u.cost_usd);
  if (input === null || output === null || cost === null) return null;
  return {
    input_tokens: input,
    output_tokens: output,
    cost_usd: cost,
    documents_with_usage: finite(u.documents_with_usage) ?? 0,
    documents_total: finite(u.documents_total) ?? 0,
  };
}

/** 0.00632085 -> '0.006321' (a CSV number cell, no float noise). */
export function csvUsd(value: number): string {
  return String(Number(value.toFixed(6)));
}

function checklistFindings(
  a: FileCheckApplicant,
  checklist: string,
): FileCheckFinding[] {
  const findings: FileCheckFinding[] = [];
  for (const row of a.checklist ?? []) {
    findings.push({
      applicant: a.applicant,
      verdict: a.verdict,
      checklist,
      section: 'Checklist',
      item: row.required === false ? `${row.item} (optional)` : row.item,
      status: row.status,
      detail: row.detail,
      documents: row.documents ?? [],
    });
  }
  for (const row of a.consistency ?? []) {
    findings.push({
      applicant: a.applicant,
      verdict: a.verdict,
      checklist,
      section: 'Consistency',
      item: row.check,
      status: row.status,
      detail: row.detail,
      documents: row.documents ?? [],
    });
  }
  findings.push(...obligationFindings(a, checklist));
  return findings;
}

/**
 * CSV rows of the documents read for one applicant, with the tokens and cost
 * of each one's facts extraction, and a total row when the backend reports
 * one (empty usage cells when no document has recorded usage, as the panel
 * says "not recorded" rather than $0).
 */
export function documentFindings(
  a: FileCheckApplicant,
  checklist: string,
): FileCheckFinding[] {
  const base = {
    applicant: a.applicant,
    verdict: a.verdict,
    checklist,
    section: 'Documents' as const,
  };
  const findings: FileCheckFinding[] = (a.documents ?? []).map((d) => {
    const usage = documentUsage(d);
    const unverified = d.unverified_fields ?? [];
    return {
      ...base,
      item: d.document_name,
      status: 'READ',
      detail: [
        String(d.doc_type ?? '').replace(/_/g, ' '),
        unverified.length > 0 ? `unverified: ${unverified.join(', ')}` : '',
        usage ? '' : 'usage not recorded',
      ]
        .filter(Boolean)
        .join('; '),
      documents: [d.document_name],
      usage,
    };
  });
  const total = applicantUsageTotal(a);
  if (total) {
    const recorded = total.documents_with_usage > 0;
    findings.push({
      ...base,
      item: 'Facts extraction cost (total)',
      status: 'INFO',
      detail: [
        recorded
          ? 'file-check facts step only, other analysis steps not included'
          : 'not recorded (analysed before costs were recorded)',
        `${total.documents_with_usage} of ${total.documents_total} documents with recorded usage`,
      ].join('; '),
      documents: [],
      usage: recorded
        ? {
            model_id: null,
            input_tokens: total.input_tokens,
            output_tokens: total.output_tokens,
            cost_usd: total.cost_usd,
          }
        : null,
    });
  }
  return findings;
}

function skippedFindings(
  result: FileCheckResult,
  checklist: string,
): FileCheckFinding[] {
  return skippedDocuments(result).map((skipped) => {
    const name =
      skipped.document.document_name || skipped.document.document_id || '';
    return {
      applicant: '',
      verdict: '',
      checklist,
      section: 'Not checked' as const,
      item: name,
      status: skipped.status,
      detail: skippedDetail(skipped),
      documents: name ? [name] : [],
    };
  });
}

function checklistLabel(result: FileCheckResult): string {
  return result.checklist?.name || result.checklist?.id || '';
}

/**
 * One finding per checklist item, consistency check, obligation row and
 * unchecked document.
 */
export function fileCheckFindings(result: FileCheckResult): FileCheckFinding[] {
  const checklist = checklistLabel(result);
  return [
    ...(result.applicants ?? []).flatMap((a) =>
      checklistFindings(a, checklist),
    ),
    ...skippedFindings(result, checklist),
  ];
}

/** The CSV's rows: each applicant's findings, then its documents read. */
export function fileCheckCsvRows(result: FileCheckResult): FileCheckFinding[] {
  const checklist = checklistLabel(result);
  return [
    ...(result.applicants ?? []).flatMap((a) => [
      ...checklistFindings(a, checklist),
      ...documentFindings(a, checklist),
    ]),
    ...skippedFindings(result, checklist),
  ];
}

/**
 * One CSV cell: RFC 4180 quoting, plus a leading apostrophe on values Excel
 * would run as a formula (document names come from user uploads).
 */
export function csvCell(value: unknown): string {
  let s = value == null ? '' : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Lets Excel detect UTF-8, so ₹ and Devanagari names survive. */
const BOM = '\uFEFF';

/**
 * Excel-friendly CSV: UTF-8 BOM, CRLF line endings, one row per finding and
 * per document read (with its tokens and cost when recorded).
 */
export function buildFileCheckCsv(result: FileCheckResult): string {
  const rows: unknown[][] = [
    [...CSV_HEADERS],
    ...fileCheckCsvRows(result).map((f) => [
      f.applicant,
      f.verdict,
      f.checklist,
      f.section,
      f.item,
      f.status,
      f.detail,
      f.documents.join('; '),
      f.usage?.model_id ?? '',
      f.usage ? f.usage.input_tokens : '',
      f.usage ? f.usage.output_tokens : '',
      f.usage ? csvUsd(f.usage.cost_usd) : '',
    ]),
  ];
  return BOM + rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

function slug(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
}

export function fileCheckCsvFileName(
  result: FileCheckResult,
  applicant?: string,
): string {
  const parts = [
    'file-check',
    slug(result.checklist?.id || result.checklist?.name || ''),
    slug(applicant || ''),
    slug(result.as_of || ''),
  ].filter(Boolean);
  return `${parts.join('_')}.csv`;
}

/** Save text as a file in the browser (no server round trip). */
export function downloadTextFile(
  filename: string,
  content: string,
  mimeType = 'text/csv;charset=utf-8',
): void {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** 'API error: 503' (useAwsClient.fetchApi) -> 503. */
export function apiErrorStatus(error: unknown): number | null {
  if (error instanceof ApiError) return error.status;
  const message = error instanceof Error ? error.message : String(error ?? '');
  const m = /API error:\s*(\d{3})/.exec(message);
  return m ? Number(m[1]) : null;
}

// ------------------------------------------------------------------ erase

function eraseRows<T extends object>(
  value: unknown,
  pick: (o: Record<string, unknown>) => T | null,
): T[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((v) =>
      v && typeof v === 'object' ? pick(v as Record<string, unknown>) : null,
    )
    .filter((v): v is T => v !== null);
}

/** Validates POST .../applicants/erase; throws on anything but the contract. */
export function parseEraseResponse(
  raw: unknown,
  applicant: string,
): ApplicantEraseResponse {
  if (!raw || typeof raw !== 'object') {
    throw new Error('unexpected response from the erase service');
  }
  const o = raw as Record<string, unknown>;
  if (!Array.isArray(o.documents_deleted) && !Array.isArray(o.failed)) {
    throw new Error('unexpected response from the erase service');
  }
  const text = (v: unknown) => (typeof v === 'string' ? v : '');
  return {
    applicant: str(o.applicant) ?? applicant,
    documents_deleted: eraseRows(o.documents_deleted, (d) => ({
      document_id: text(d.document_id),
      name: text(d.name) || text(d.document_id),
    })),
    failed: eraseRows(o.failed, (d) => ({
      document_id: text(d.document_id),
      name: text(d.name) || text(d.document_id),
      error: text(d.error),
    })),
    delivery_log_redacted:
      typeof o.delivery_log_redacted === 'number' &&
      Number.isFinite(o.delivery_log_redacted)
        ? o.delivery_log_redacted
        : null,
    not_erased: Array.isArray(o.not_erased)
      ? o.not_erased.filter((v): v is string => typeof v === 'string')
      : [],
    erased_at: text(o.erased_at),
  };
}

/** The document ids the verdict lists under an applicant, once each. */
export function eraseDocumentIds(
  applicant: Pick<FileCheckApplicant, 'documents'>,
): string[] {
  return Array.from(
    new Set(
      (applicant.documents ?? [])
        .map((d) => d.document_id)
        .filter((id): id is string => typeof id === 'string' && !!id.trim()),
    ),
  );
}

/**
 * Body of POST .../applicants/erase for an applicant of the verdict: the PAN
 * when there is one (a name can match two applicants: 409), the typed name,
 * and the documents the verdict shows (409 when they changed since).
 */
export function eraseRequestBody(
  applicant: Pick<FileCheckApplicant, 'applicant' | 'pan' | 'documents'>,
  confirm: string,
): ApplicantEraseRequest {
  const pan = applicant.pan?.trim();
  return {
    applicant: pan || applicant.applicant,
    confirm,
    document_ids: eraseDocumentIds(applicant),
  };
}

// The erase API's errors that come before anything is deleted
// (packages/backend/app/routers/applicants.py).
const ERASE_NOT_STARTED_DETAILS = [
  'File check is not configured',
  'Applicant lookup',
];

/**
 * True when a failed erase may have deleted some or all documents: no answer
 * (the connection dropped, or the answer was unreadable) or a 5xx that is not
 * one of the API's own errors from before deleting (e.g. a gateway timeout).
 * 4xx answers mean nothing was deleted.
 */
export function eraseMayBeIncomplete(error: unknown): boolean {
  const status = apiErrorStatus(error);
  if (status === null) return true;
  if (status < 500) return false;
  const detail = apiErrorDetail(error) ?? '';
  return !ERASE_NOT_STARTED_DETAILS.some((d) => detail.startsWith(d));
}

/** Runs of spaces as one, as the page shows the name. */
function collapseSpaces(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}

/**
 * The typed confirmation is the applicant's name exactly as shown (case
 * included; leading, trailing and repeated spaces do not count).
 */
export function eraseConfirmMatches(typed: string, applicant: string): boolean {
  const want = collapseSpaces(applicant);
  return want.length > 0 && collapseSpaces(typed) === want;
}

/** 'CKRPK7314M' -> 'XXXXXX314M' (the engine's masking: last 4 shown). */
export function maskPan(pan: string | null | undefined): string | null {
  const p = String(pan ?? '')
    .replace(/\s+/g, '')
    .toUpperCase();
  if (!p) return null;
  return p.length <= 4
    ? 'X'.repeat(p.length)
    : 'X'.repeat(p.length - 4) + p.slice(-4);
}
