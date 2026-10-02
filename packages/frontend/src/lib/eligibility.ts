// Helpers for the Eligibility & lenders panel (projects/{id}/eligibility).
// The eligibility maths is done by the backend (app/eligibility.py): nothing
// here computes an amount, a FOIR or an EMI. These functions build the
// requests, check the responses and format the backend's numbers with Indian
// digit grouping.
import type {
  CheckSource,
  CibilEnquiries,
  CibilSource,
  CibilSources,
  CompanyCategory,
  CompanyCheck,
  CountedObligation,
  DocumentRef,
  DocumentRows,
  EligibilityCalculateRequest,
  EligibilityCibil,
  EligibilityFileCheck,
  EligibilityIncome,
  EligibilityInputs,
  EligibilityInputsRequest,
  EligibilityInputsResponse,
  EligibilityLoan,
  EligibilityLoginResponse,
  EligibilityPrefill,
  EligibilityProfile,
  EligibilityResult,
  EmploymentType,
  FieldSource,
  FieldSources,
  HouseOwnership,
  IncomeFrequency,
  LenderEligibility,
  LenderPolicy,
  LendersResponse,
  LoanType,
  LoginDelivery,
  OtherIncome,
  OtherIncomeConsidered,
  OtherIncomeType,
  PincodeCheck,
  PrefillField,
  RentAgreement,
  SourcedField,
  StillNeeded,
  Tradeline,
  TradelineAction,
  TradelineSource,
  TradelineStatus,
} from '../types/eligibility';

// ------------------------------------------------------------------ options
// The ids of app/routers/eligibility.py (the API also takes the labels).

export const EMPLOYMENT_TYPES: readonly EmploymentType[] = [
  'defence',
  'government',
  'grade_4',
  'llp',
  'merchant_navy',
  'partnership_proprietorship',
  'private_limited',
  'public_limited',
];

export const HOUSE_OWNERSHIP: readonly HouseOwnership[] = [
  'owned',
  'rented',
  'parental',
  'company_provided',
];

export const OTHER_INCOME_TYPES: readonly OtherIncomeType[] = [
  'rented',
  'bonus',
  'incentive',
  'pension',
];

export const INCOME_FREQUENCIES: readonly IncomeFrequency[] = [
  'yearly',
  'half_yearly',
  'quarterly',
  'monthly',
];

export const RENT_AGREEMENTS: readonly RentAgreement[] = [
  'notary',
  'registered',
];

export const LOAN_TYPES: readonly LoanType[] = [
  'personal',
  'home',
  'mortgage',
  'car',
  'education',
  'application',
  'consumer',
  'credit_card',
];

export const TRADELINE_ACTIONS: readonly TradelineAction[] = [
  'bt',
  'obligate',
  'close',
];

export const TRADELINE_STATUSES: readonly TradelineStatus[] = [
  'active',
  'closed',
  'settled',
  'written_off',
  'suit_filed',
  'wilful_default',
  'restructured',
];

const TRADELINE_SOURCES: readonly TradelineSource[] = [
  'manual',
  'bureau',
  'credit_report',
  'bank_statement',
];

const CIBIL_SOURCES: readonly CibilSource[] = [
  'manual',
  'bureau',
  'credit_report',
];

// The API's limits (Field max_length / le), so a row is not refused.
export const MAX_OTHER_INCOME = 10;
export const MAX_TRADELINES = 50;

/** Other income rows that carry a frequency (bonus, incentive). */
export function incomeHasFrequency(type: OtherIncomeType): boolean {
  return type === 'bonus' || type === 'incentive';
}

// ------------------------------------------------------------------ numbers

const INDIAN_NUMBER = new Intl.NumberFormat('en-IN', {
  maximumFractionDigits: 2,
});

// Money: whole rupees without decimals, else always both paise digits
// (₹39,172.10, never ₹39,172.1).
const INDIAN_WHOLE = new Intl.NumberFormat('en-IN', {
  maximumFractionDigits: 0,
});
const INDIAN_PAISE = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const INR_WHOLE = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});
const INR_PAISE = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** An amount with no paise left once rounded to the paisa (39172.004 -> true). */
function wholeRupees(n: number): boolean {
  return Math.round(n * 100) % 100 === 0;
}

/** 2058000 -> '20,58,000'; 98000.5 -> '98,000.50' (Indian digit grouping). */
export function formatIndianNumber(value: number | null | undefined): string {
  const n = finite(value);
  if (n === null) return '';
  return (wholeRupees(n) ? INDIAN_WHOLE : INDIAN_PAISE).format(n);
}

/** 2058000 -> '₹20,58,000'; 39172.1345 -> '₹39,172.13'; 39172.1 -> '₹39,172.10'; null -> '–'. */
export function formatRupees(value: number | null | undefined): string {
  const n = finite(value);
  if (n === null) return '–';
  return (wholeRupees(n) ? INR_WHOLE : INR_PAISE).format(n);
}

/**
 * A typed amount: '20,58,000', '₹ 98,000' or '98000.50' -> the number; blank
 * -> null; anything else -> NaN (the field shows it is not a number).
 */
export function parseAmount(text: string): number | null {
  const s = text.replace(/[₹,\s]/g, '').replace(/^rs\.?/i, '');
  if (!s) return null;
  if (!/^\d+(\.\d{0,2})?$/.test(s)) return Number.NaN;
  return Number(s);
}

/** A typed whole number ('12'); blank -> null; else NaN. */
export function parseCount(text: string): number | null {
  const s = text.replace(/[,\s]/g, '');
  if (!s) return null;
  return /^\d+$/.test(s) ? Number(s) : Number.NaN;
}

/** The ROI as a percentage: 11 (the API's percent) or 0.11 (a fraction) -> 11. */
export function roiPercent(value: number | null | undefined): number | null {
  const n = finite(value);
  if (n === null) return null;
  const pct = n > 0 && n < 1 ? n * 100 : n;
  return Math.round(pct * 1e6) / 1e6;
}

/** 11 or 0.11 -> '11%'; 10.75 -> '10.75%'; null -> '–'. */
export function formatRoi(value: number | null | undefined): string {
  const pct = roiPercent(value);
  return pct === null ? '–' : `${INDIAN_NUMBER.format(pct)}%`;
}

/** A FOIR (always a fraction in the API: 0.7) as '70%'; 1 -> '100%'. */
export function formatFoir(value: number | null | undefined): string {
  const n = finite(value);
  if (n === null) return '–';
  return `${INDIAN_NUMBER.format(Math.round(n * 100 * 1e6) / 1e6)}%`;
}

// ------------------------------------------------------------------ inputs

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
): T | null {
  if (typeof value !== 'string') return null;
  const v = value
    .trim()
    .toLowerCase()
    .replace(/[\s/-]+/g, '_') as T;
  return allowed.includes(v) ? v : null;
}

function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === 'string' && !!v.trim())
    : [];
}

function bool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function emptyProfile(): EligibilityProfile {
  return {
    pan: null,
    name: null,
    mobile: null,
    dob: null,
    house_ownership: null,
    pincode: null,
    current_address: null,
    permanent_address: null,
    company: null,
    employment_type: null,
    net_income: null,
    other_income: [],
  };
}

// Row keys keep a row's inputs in place when a row above it is removed.
let rowSeq = 0;

export function rowKey(): string {
  rowSeq += 1;
  return `row-${rowSeq}`;
}

export function emptyTradeline(): Tradeline {
  return {
    key: rowKey(),
    loan_type: 'personal',
    lender: null,
    sanction_amount: null,
    outstanding: null,
    emi: null,
    status: 'active',
    action: 'obligate',
    source: 'manual',
  };
}

export function emptyOtherIncome(type: OtherIncomeType = 'bonus'): OtherIncome {
  return {
    key: rowKey(),
    type,
    amount: null,
    frequency: incomeHasFrequency(type) ? 'yearly' : null,
    agreement: type === 'rented' ? 'registered' : null,
  };
}

export function emptyInputs(): EligibilityInputs {
  return {
    profile: emptyProfile(),
    cibil: {
      score: null,
      enquiries: { d30: null, d60: null, d90: null, d120: null },
      tradelines: [],
    },
    loan: { amount: null, tenure_months: null },
  };
}

function normalizeOtherIncome(raw: unknown): OtherIncome | null {
  const o = obj(raw);
  const type = oneOf(o.type, OTHER_INCOME_TYPES);
  if (!type) return null;
  return {
    key: rowKey(),
    type,
    amount: finite(o.amount),
    frequency: incomeHasFrequency(type)
      ? oneOf(o.frequency, INCOME_FREQUENCIES)
      : null,
    agreement: type === 'rented' ? oneOf(o.agreement, RENT_AGREEMENTS) : null,
  };
}

function normalizeProfile(raw: unknown): EligibilityProfile {
  const o = obj(raw);
  return {
    pan: text(o.pan),
    name: text(o.name),
    mobile: text(o.mobile),
    dob: text(o.dob),
    house_ownership: oneOf(o.house_ownership, HOUSE_OWNERSHIP),
    pincode: text(o.pincode),
    current_address: text(o.current_address),
    permanent_address: text(o.permanent_address),
    company: text(o.company),
    employment_type: oneOf(o.employment_type, EMPLOYMENT_TYPES),
    net_income: finite(o.net_income),
    other_income: Array.isArray(o.other_income)
      ? o.other_income
          .map(normalizeOtherIncome)
          .filter((r): r is OtherIncome => r !== null)
      : [],
  };
}

function normalizeTradeline(raw: unknown): Tradeline {
  const o = obj(raw);
  const row: Tradeline = {
    key: rowKey(),
    loan_type: oneOf(o.loan_type, LOAN_TYPES),
    lender: text(o.lender),
    sanction_amount: finite(o.sanction_amount),
    outstanding: finite(o.outstanding),
    emi: finite(o.emi),
    status: oneOf(o.status, TRADELINE_STATUSES),
    action: oneOf(o.action, TRADELINE_ACTIONS) ?? 'obligate',
    source: oneOf(o.source, TRADELINE_SOURCES) ?? 'manual',
  };
  const account = text(o.account_number);
  if (account) row.account_number = account;
  for (const key of ['overdue', 'emis_paid', 'emis_pending'] as const) {
    const n = finite(o[key]);
    if (n !== null) row[key] = n;
  }
  for (const key of ['open_date', 'last_payment_date'] as const) {
    const d = text(o[key]);
    if (d) row[key] = d;
  }
  return row;
}

function normalizeCibil(raw: unknown): EligibilityCibil {
  const o = obj(raw);
  const e = obj(o.enquiries);
  const enquiries: CibilEnquiries = {
    d30: finite(e.d30),
    d60: finite(e.d60),
    d90: finite(e.d90),
    d120: finite(e.d120),
  };
  const cibil: EligibilityCibil = {
    score: finite(o.score),
    enquiries,
    tradelines: Array.isArray(o.tradelines)
      ? o.tradelines.map(normalizeTradeline)
      : [],
  };
  const source = oneOf(o.source, CIBIL_SOURCES);
  if (source && source !== 'manual') cibil.source = source;
  const reportDate = text(o.report_date);
  if (reportDate) cibil.report_date = reportDate;
  return cibil;
}

function normalizeLoan(raw: unknown): EligibilityLoan {
  const o = obj(raw);
  return {
    amount: finite(o.amount),
    tenure_months: finite(o.tenure_months),
  };
}

/** Any {profile, cibil, loan} object -> complete inputs (unknown values dropped). */
export function normalizeInputs(raw: unknown): EligibilityInputs {
  const o = obj(raw);
  return {
    profile: normalizeProfile(o.profile),
    cibil: normalizeCibil(o.cibil),
    loan: normalizeLoan(o.loan),
  };
}

export const PREFILL_FIELDS: readonly PrefillField[] = [
  'name',
  'pan',
  'mobile',
  'dob',
  'house_ownership',
  'pincode',
  'current_address',
  'permanent_address',
  'company',
  'employment_type',
  'net_income',
  'other_income',
  'loan_amount',
  'tenure_months',
  'score',
  'enquiries',
  'tradelines',
];

function prefill(raw: unknown): EligibilityPrefill | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = obj(raw);
  return {
    available: o.available === true,
    detail: text(o.detail),
    income_source: text(o.income_source),
    suggested_tradelines: finite(o.suggested_tradelines) ?? 0,
    credit_report: text(o.credit_report),
    documents: finite(o.documents) ?? 0,
  };
}

/** The fields of GET .../inputs `sources` that hold one value each. */
export const SOURCED_FIELDS: readonly SourcedField[] = PREFILL_FIELDS.filter(
  (f): f is SourcedField => f !== 'other_income' && f !== 'tradelines',
);

function documentRef(raw: unknown): DocumentRef | null {
  const o = obj(raw);
  const file = text(o.file);
  if (!file) return null;
  const page = finite(o.page);
  return {
    document_id: text(o.document_id),
    file,
    page: page !== null && Number.isInteger(page) && page > 0 ? page : null,
    doc_type: text(o.doc_type),
  };
}

/** One entry of GET .../inputs `sources` (or a row source); null when it is not one. */
export function parseFieldSource(raw: unknown): FieldSource | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = obj(raw);
  if (o.source !== 'document' && o.source !== 'table') return null;
  return {
    source: o.source,
    value: o.value ?? null,
    documents: Array.isArray(o.documents)
      ? o.documents.map(documentRef).filter((d): d is DocumentRef => d !== null)
      : [],
    detail: text(o.detail),
    unverified: o.unverified === true,
  };
}

function rowSources(raw: unknown): (FieldSource | null)[] {
  return Array.isArray(raw) ? raw.map(parseFieldSource) : [];
}

/** `document_rows`: each source's value as a row of the form, with its `document`. */
function documentRows<T extends OtherIncome | Tradeline>(
  raw: unknown,
  normalize: (row: unknown) => T | null,
): T[] {
  return rowSources(raw).flatMap((source) => {
    const row = source ? normalize(source.value) : null;
    return row && source ? [{ ...row, document: source }] : [];
  });
}

const STILL_NEEDED_TABS: Record<string, StillNeeded['tab']> = {
  score: 'cibil',
  enquiries: 'cibil',
  tradelines: 'cibil',
};

/** GET .../inputs `still_needed` (the API's list for the inputs it returned). */
function parseStillNeeded(raw: unknown): StillNeeded[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item): StillNeeded[] => {
    const o = obj(item);
    const field = text(o.field);
    if (!field) return [];
    const [head, n] = field.split('.');
    const number = Number(n);
    return [
      {
        field,
        required: o.required === true,
        fromDocuments: o.from_documents === true,
        tab: STILL_NEEDED_TABS[head] ?? 'profile',
        ...(head === 'tradelines' && Number.isInteger(number)
          ? { loan: { number, lender: null, loanType: null } }
          : {}),
      },
    ];
  });
}

/** expires_at as ISO: an ISO string or epoch seconds (the DynamoDB TTL). */
export function expiresAtIso(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return new Date(value * 1000).toISOString();
  }
  const s = text(value);
  return s && !Number.isNaN(Date.parse(s)) ? s : null;
}

/**
 * GET / PUT .../inputs: the inputs to edit, which fields the documents filled
 * and what the documents give each field. Each row of the inputs gets its
 * `document` (row_sources) and the CIBIL block its `sources` (never sent).
 */
export function parseInputsResponse(
  raw: unknown,
  applicant: string,
): EligibilityInputsResponse {
  const o = obj(raw);
  const raws = o.inputs && typeof o.inputs === 'object' ? o.inputs : null;
  if (!raws || typeof o.saved !== 'boolean') {
    throw new Error('unexpected response from the eligibility inputs API');
  }
  const fields = strings(o.from_documents);
  const sources: FieldSources = {};
  for (const [key, value] of Object.entries(obj(o.sources))) {
    const source = parseFieldSource(value);
    if (
      source &&
      (key === 'report' || SOURCED_FIELDS.includes(key as SourcedField))
    ) {
      sources[key as SourcedField | 'report'] = source;
    }
  }
  const rows = obj(o.document_rows);
  const documents: DocumentRows = {
    other_income: documentRows(rows.other_income, normalizeOtherIncome),
    tradelines: documentRows(rows.tradelines, normalizeTradeline),
  };

  // Each row of the inputs gets the document row it is (null: typed by hand).
  const inputs = normalizeInputs(raws);
  const row = obj(o.row_sources);
  const incomeSources = rowSources(row.other_income);
  const tradelineSources = rowSources(row.tradelines);
  inputs.profile.other_income = inputs.profile.other_income.map((r, i) =>
    incomeSources[i] ? { ...r, document: incomeSources[i] } : r,
  );
  inputs.cibil.tradelines = inputs.cibil.tradelines.map((r, i) =>
    tradelineSources[i] ? { ...r, document: tradelineSources[i] } : r,
  );
  const cibil: CibilSources = {};
  for (const key of ['score', 'enquiries', 'report'] as const) {
    if (sources[key]) cibil[key] = sources[key];
  }
  if (documents.tradelines.length > 0) cibil.tradelines = documents.tradelines;
  if (Object.keys(cibil).length > 0) inputs.cibil.sources = cibil;
  return {
    applicant: text(o.applicant) ?? applicant,
    inputs,
    fromDocuments: PREFILL_FIELDS.filter((f) => fields.includes(f)),
    prefill: prefill(o.prefill),
    sources,
    documentRows: documents,
    stillNeeded: parseStillNeeded(o.still_needed),
    saved: o.saved,
    expiresAt: expiresAtIso(o.expires_at),
    notes: strings(o.notes),
  };
}

// ------------------------------------------------------------------ document values
// What the documents give a field is shown with it while the field holds that
// value; typed values always win (nothing here overwrites one).

/** A field's value in the inputs (enquiries: the four windows). */
export function fieldValue(
  inputs: EligibilityInputs,
  field: SourcedField,
): unknown {
  if (field === 'loan_amount') return inputs.loan.amount;
  if (field === 'tenure_months') return inputs.loan.tenure_months;
  if (field === 'score') return inputs.cibil.score;
  if (field === 'enquiries') return inputs.cibil.enquiries;
  return inputs.profile[field];
}

function present(value: unknown): boolean {
  if (typeof value === 'string') return value.trim() !== '';
  if (typeof value === 'number') return Number.isFinite(value);
  return value !== null && value !== undefined;
}

/** Text as compared: spacing collapsed, case ignored. */
function folded(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase();
}

function sameValue(field: SourcedField, a: unknown, b: unknown): boolean {
  if (!present(a) || !present(b)) return false;
  if (field === 'enquiries') {
    const x = obj(a);
    const y = obj(b);
    return (['d30', 'd60', 'd90', 'd120'] as const).every(
      (w) => (finite(x[w]) ?? null) === (finite(y[w]) ?? null),
    );
  }
  if (typeof a === 'number' || typeof b === 'number') return a === b;
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  if (field === 'pan') {
    const x = a.replace(/\s/g, '').toUpperCase();
    const y = b.replace(/\s/g, '').toUpperCase();
    // A masked PAN is the same PAN when its last 4 are.
    return isMaskedPan(x) || isMaskedPan(y)
      ? x.slice(-4) === y.slice(-4)
      : x === y;
  }
  if (field === 'mobile') {
    const digits = (s: string) => s.replace(/\D/g, '').slice(-10);
    return digits(a) === digits(b);
  }
  return folded(a) === folded(b);
}

/** `source` while `value` is the documents' value; null once typed over (or none). */
export function sourceFor(
  field: SourcedField,
  value: unknown,
  source: FieldSource | null | undefined,
): FieldSource | null {
  return source && sameValue(field, value, source.value) ? source : null;
}

/** `source` when the documents give a value other than `value` (an empty one too), else null. */
export function otherSourceFor(
  field: SourcedField,
  value: unknown,
  source: FieldSource | null | undefined,
): FieldSource | null {
  if (!source || !present(source.value)) return null;
  return sameValue(field, value, source.value) ? null : source;
}

/** The field's source while it holds the documents' value; null once typed over (or none). */
export function sourceOf(
  inputs: EligibilityInputs,
  sources: FieldSources | null | undefined,
  field: SourcedField,
): FieldSource | null {
  return sourceFor(field, fieldValue(inputs, field), sources?.[field]);
}

/** The documents' value of a field the inputs hold something else in (else null). */
export function otherDocumentValue(
  inputs: EligibilityInputs,
  sources: FieldSources | null | undefined,
  field: SourcedField,
): FieldSource | null {
  return otherSourceFor(field, fieldValue(inputs, field), sources?.[field]);
}

/** The CIBIL block alone as inputs, for the helpers that take inputs. */
export function cibilInputs(cibil: EligibilityCibil): EligibilityInputs {
  return {
    profile: emptyProfile(),
    cibil,
    loan: { amount: null, tenure_months: null },
  };
}

/** The CIBIL block's sources as FieldSources (score, enquiries, report). */
export function cibilFieldSources(cibil: EligibilityCibil): FieldSources {
  const { score, enquiries, report } = cibil.sources ?? {};
  return {
    ...(score ? { score } : {}),
    ...(enquiries ? { enquiries } : {}),
    ...(report ? { report } : {}),
  };
}

const TRADELINE_DATA = [
  'loan_type',
  'lender',
  'sanction_amount',
  'outstanding',
  'emi',
  'status',
  'account_number',
  'overdue',
  'emis_paid',
  'emis_pending',
  'open_date',
  'last_payment_date',
] as const;

/**
 * A row still holds what its document gives (a tradeline's BT / Obligate /
 * Close is the user's choice, not the document's).
 */
export function rowFromDocument(row: OtherIncome | Tradeline): boolean {
  const value = row.document ? obj(row.document.value) : null;
  if (!value) return false;
  const same = (a: unknown, b: unknown) =>
    (a ?? null) === (b ?? null) ||
    (typeof a === 'string' && typeof b === 'string' && folded(a) === folded(b));
  if ('type' in row) {
    return (['type', 'amount', 'frequency', 'agreement'] as const).every((k) =>
      same(row[k], value[k]),
    );
  }
  return TRADELINE_DATA.every((k) => same(row[k], value[k]));
}

function sourceKey(source: FieldSource): string {
  return JSON.stringify([
    source.value,
    source.documents.map((d) => [d.document_id, d.file, d.page]),
  ]);
}

/** The documents' rows that no row of the inputs came from (saved inputs typed otherwise). */
export function documentRowsMissing<T extends OtherIncome | Tradeline>(
  rows: readonly T[],
  documentRowsOf: readonly T[] | null | undefined,
): T[] {
  const used = new Set(
    rows.flatMap((r) => (r.document ? [sourceKey(r.document)] : [])),
  );
  return (documentRowsOf ?? []).filter(
    (r) => r.document && !used.has(sourceKey(r.document)),
  );
}

/** A document's row as a new row of the inputs (its own key; its document kept). */
export function rowOfDocument<T extends OtherIncome | Tradeline>(row: T): T {
  return { ...row, key: rowKey() };
}

export function addOtherIncomeRow(
  inputs: EligibilityInputs,
  row: OtherIncome,
): EligibilityInputs {
  if (inputs.profile.other_income.length >= MAX_OTHER_INCOME) return inputs;
  return setProfile(inputs, {
    other_income: [...inputs.profile.other_income, rowOfDocument(row)],
  });
}

export function addTradelineRow(
  inputs: EligibilityInputs,
  row: Tradeline,
): EligibilityInputs {
  if (inputs.cibil.tradelines.length >= MAX_TRADELINES) return inputs;
  return setCibil(inputs, {
    tradelines: [...inputs.cibil.tradelines, rowOfDocument(row)],
  });
}

/** The value of one field set (the documents' value for "Use" and the fill below). */
export function setFieldValue(
  inputs: EligibilityInputs,
  field: SourcedField,
  value: unknown,
): EligibilityInputs {
  if (field === 'loan_amount' || field === 'tenure_months') {
    const n = finite(value);
    return setLoan(
      inputs,
      field === 'loan_amount' ? { amount: n } : { tenure_months: n },
    );
  }
  if (field === 'score') return setCibil(inputs, { score: finite(value) });
  if (field === 'enquiries') {
    const e = obj(value);
    return setCibil(inputs, {
      enquiries: {
        d30: finite(e.d30),
        d60: finite(e.d60),
        d90: finite(e.d90),
        d120: finite(e.d120),
      },
    });
  }
  return setProfile(inputs, {
    [field]: field === 'net_income' ? finite(value) : value,
  } as Partial<EligibilityProfile>);
}

function blank(inputs: EligibilityInputs, field: SourcedField): boolean {
  if (field === 'enquiries') {
    // Taken whole: the windows add up only as the report gives them.
    return Object.values(inputs.cibil.enquiries).every((v) => !present(v));
  }
  return !present(fieldValue(inputs, field));
}

/** The fields that are empty and a document fills ("Fill from the documents"). */
export function fillableFields(
  inputs: EligibilityInputs,
  sources: FieldSources | null | undefined,
): SourcedField[] {
  return SOURCED_FIELDS.filter(
    (f) => present(sources?.[f]?.value) && blank(inputs, f),
  );
}

/**
 * Every empty field a document fills, filled; typed values are kept. The CIBIL
 * block becomes the credit report's once its score or enquiries come from it.
 */
export function fillFromDocuments(
  inputs: EligibilityInputs,
  sources: FieldSources | null | undefined,
): EligibilityInputs {
  let next = inputs;
  const fields = fillableFields(inputs, sources);
  for (const field of fields) {
    next = setFieldValue(next, field, sources?.[field]?.value);
  }
  const report = sources?.report;
  if (
    report &&
    (fields.includes('score') || fields.includes('enquiries')) &&
    (next.cibil.source ?? 'manual') === 'manual'
  ) {
    const date = typeof report.value === 'string' ? report.value : null;
    next = setCibil(next, {
      source: 'credit_report',
      ...(date ? { report_date: date } : {}),
    });
  }
  return next;
}

// "Still needed before Check eligibility", in the order of the sheet:
// app/routers/eligibility.py NEEDED_FIELDS (required: every lender's check needs it).
const NEEDED_FIELDS: readonly {
  field: string;
  required: boolean;
  tab: StillNeeded['tab'];
}[] = [
  { field: 'pan', required: false, tab: 'profile' },
  { field: 'name', required: false, tab: 'profile' },
  { field: 'mobile', required: false, tab: 'profile' },
  { field: 'dob', required: false, tab: 'profile' },
  { field: 'house_ownership', required: false, tab: 'profile' },
  { field: 'pincode', required: true, tab: 'profile' },
  { field: 'current_address', required: false, tab: 'profile' },
  { field: 'permanent_address', required: false, tab: 'profile' },
  { field: 'company', required: true, tab: 'profile' },
  { field: 'employment_type', required: true, tab: 'profile' },
  { field: 'net_income', required: true, tab: 'profile' },
  { field: 'loan_amount', required: false, tab: 'profile' },
  { field: 'tenure_months', required: false, tab: 'profile' },
  { field: 'score', required: true, tab: 'cibil' },
  { field: 'enquiries.d30', required: false, tab: 'cibil' },
  { field: 'enquiries.d60', required: false, tab: 'cibil' },
  { field: 'enquiries.d90', required: true, tab: 'cibil' },
  { field: 'enquiries.d120', required: false, tab: 'cibil' },
];

/**
 * The fields still empty (no document filled them and nobody typed them),
 * then the loans a lender cannot count yet (Obligate without an EMI, BT
 * without the outstanding); `fromDocuments` when a document holds a value.
 */
export function stillNeeded(
  inputs: EligibilityInputs,
  sources?: FieldSources | null,
  tab?: StillNeeded['tab'],
): StillNeeded[] {
  const out: StillNeeded[] = [];
  for (const { field, required, tab: on } of NEEDED_FIELDS) {
    if (tab && on !== tab) continue;
    const [name, window] = field.split('.') as [
      SourcedField,
      keyof CibilEnquiries | undefined,
    ];
    const value = fieldValue(inputs, name);
    const documented = sources?.[name]?.value;
    const held = window ? present(obj(value)[window]) : present(value);
    if (held) continue;
    out.push({
      field,
      required,
      tab: on,
      fromDocuments: window
        ? present(obj(documented)[window])
        : present(documented),
    });
  }
  if (tab === 'profile') return out;
  inputs.cibil.tradelines.forEach((row, i) => {
    if (row.status === 'closed') return;
    const key =
      row.action === 'obligate' && !present(row.emi)
        ? 'emi'
        : row.action === 'bt' && !present(row.outstanding)
          ? 'outstanding'
          : null;
    if (!key) return;
    out.push({
      field: `tradelines.${i + 1}.${key}`,
      required: true,
      tab: 'cibil',
      fromDocuments: present(obj(row.document?.value)[key]),
      loan: { number: i + 1, lender: row.lender, loanType: row.loan_type },
    });
  });
  return out;
}

function trimmed(value: string | null | undefined): string | null {
  return value?.trim() || null;
}

/** Drops optional tradeline details that are empty (the API makes them optional). */
function tradelineBody(row: Tradeline): Tradeline {
  const body: Tradeline = {
    loan_type: row.loan_type,
    lender: trimmed(row.lender),
    sanction_amount: finite(row.sanction_amount),
    outstanding: finite(row.outstanding),
    emi: finite(row.emi),
    status: row.status,
    action: row.action,
  };
  const account = trimmed(row.account_number);
  if (account) body.account_number = account;
  for (const key of ['overdue', 'emis_paid', 'emis_pending'] as const) {
    const n = finite(row[key]);
    if (n !== null) body[key] = n;
  }
  for (const key of ['open_date', 'last_payment_date'] as const) {
    const d = trimmed(row[key]);
    if (d) body[key] = d;
  }
  // The pre-fill's bank-statement rows and a bureau pull's rows keep their source.
  if (row.source && row.source !== 'manual') body.source = row.source;
  return body;
}

/** The inputs as the API takes them: trimmed text, NaN (bad typing) as null. */
export function inputsBody(inputs: EligibilityInputs): EligibilityInputs {
  const p = inputs.profile;
  const c = inputs.cibil;
  const cibil: EligibilityCibil = {
    score: finite(c.score),
    enquiries: {
      d30: finite(c.enquiries.d30),
      d60: finite(c.enquiries.d60),
      d90: finite(c.enquiries.d90),
      d120: finite(c.enquiries.d120),
    },
    tradelines: c.tradelines.map(tradelineBody),
  };
  if (c.source && c.source !== 'manual') cibil.source = c.source;
  const reportDate = trimmed(c.report_date);
  if (reportDate) cibil.report_date = reportDate;
  return {
    profile: {
      pan: trimmed(p.pan)?.replace(/\s/g, '').toUpperCase() ?? null,
      name: trimmed(p.name),
      mobile: trimmed(p.mobile),
      dob: trimmed(p.dob),
      house_ownership: p.house_ownership,
      pincode: trimmed(p.pincode),
      current_address: trimmed(p.current_address),
      permanent_address: trimmed(p.permanent_address),
      company: trimmed(p.company),
      employment_type: p.employment_type,
      net_income: finite(p.net_income),
      other_income: p.other_income.map((r) => ({
        type: r.type,
        amount: finite(r.amount),
        frequency: incomeHasFrequency(r.type) ? (r.frequency ?? null) : null,
        agreement: r.type === 'rented' ? (r.agreement ?? null) : null,
      })),
    },
    cibil,
    loan: {
      amount: finite(inputs.loan.amount),
      tenure_months: finite(inputs.loan.tenure_months),
    },
  };
}

/** PUT .../inputs */
export function inputsRequestBody(
  applicant: string,
  inputs: EligibilityInputs,
): EligibilityInputsRequest {
  return { applicant, ...inputsBody(inputs) };
}

/** POST .../calculate: the inputs on screen (calculated, not saved). */
export function calculateRequestBody(
  applicant: string,
  inputs?: EligibilityInputs,
): EligibilityCalculateRequest {
  return inputs ? { applicant, inputs: inputsBody(inputs) } : { applicant };
}

/** Numbers typed that are not numbers (NaN): the form cannot be sent until they are fixed. */
export function countInvalidNumbers(inputs: EligibilityInputs): number {
  const values: (number | null | undefined)[] = [
    inputs.profile.net_income,
    ...inputs.profile.other_income.map((r) => r.amount),
    inputs.cibil.score,
    inputs.cibil.enquiries.d30,
    inputs.cibil.enquiries.d60,
    inputs.cibil.enquiries.d90,
    inputs.cibil.enquiries.d120,
    ...inputs.cibil.tradelines.flatMap((r) => [
      r.sanction_amount,
      r.outstanding,
      r.emi,
      r.overdue,
      r.emis_paid,
      r.emis_pending,
    ]),
    inputs.loan.amount,
    inputs.loan.tenure_months,
  ];
  return values.filter((v) => typeof v === 'number' && Number.isNaN(v)).length;
}

/** Same inputs, same key: tells whether a result is for the inputs on screen. */
export function inputsKey(inputs: EligibilityInputs): string {
  return JSON.stringify(inputsBody(inputs));
}

// ------------------------------------------------------------------ edits

export function setProfile(
  inputs: EligibilityInputs,
  patch: Partial<EligibilityProfile>,
): EligibilityInputs {
  return { ...inputs, profile: { ...inputs.profile, ...patch } };
}

export function setLoan(
  inputs: EligibilityInputs,
  patch: Partial<EligibilityLoan>,
): EligibilityInputs {
  return { ...inputs, loan: { ...inputs.loan, ...patch } };
}

export function setCibil(
  inputs: EligibilityInputs,
  patch: Partial<EligibilityCibil>,
): EligibilityInputs {
  return { ...inputs, cibil: { ...inputs.cibil, ...patch } };
}

export function setEnquiries(
  inputs: EligibilityInputs,
  patch: Partial<CibilEnquiries>,
): EligibilityInputs {
  return setCibil(inputs, {
    enquiries: { ...inputs.cibil.enquiries, ...patch },
  });
}

export function updateTradeline(
  inputs: EligibilityInputs,
  index: number,
  patch: Partial<Tradeline>,
): EligibilityInputs {
  return setCibil(inputs, {
    tradelines: inputs.cibil.tradelines.map((row, i) =>
      i === index ? { ...row, ...patch } : row,
    ),
  });
}

/** BT / Obligate / Close of one tradeline; the others are unchanged. */
export function setTradelineAction(
  inputs: EligibilityInputs,
  index: number,
  action: TradelineAction,
): EligibilityInputs {
  return updateTradeline(inputs, index, { action });
}

export function addTradeline(inputs: EligibilityInputs): EligibilityInputs {
  if (inputs.cibil.tradelines.length >= MAX_TRADELINES) return inputs;
  return setCibil(inputs, {
    tradelines: [...inputs.cibil.tradelines, emptyTradeline()],
  });
}

export function removeTradeline(
  inputs: EligibilityInputs,
  index: number,
): EligibilityInputs {
  return setCibil(inputs, {
    tradelines: inputs.cibil.tradelines.filter((_, i) => i !== index),
  });
}

export function updateOtherIncome(
  inputs: EligibilityInputs,
  index: number,
  patch: Partial<OtherIncome>,
): EligibilityInputs {
  return setProfile(inputs, {
    other_income: inputs.profile.other_income.map((row, i) => {
      if (i !== index) return row;
      if (patch.type && patch.type !== row.type) {
        // A new income type starts with that type's own fields.
        return {
          ...emptyOtherIncome(patch.type),
          key: row.key,
          amount: patch.amount !== undefined ? patch.amount : row.amount,
        };
      }
      return { ...row, ...patch };
    }),
  });
}

export function addOtherIncome(
  inputs: EligibilityInputs,
  type?: OtherIncomeType,
): EligibilityInputs {
  if (inputs.profile.other_income.length >= MAX_OTHER_INCOME) return inputs;
  const used = new Set(inputs.profile.other_income.map((r) => r.type));
  const next = type ?? OTHER_INCOME_TYPES.find((t) => !used.has(t)) ?? 'bonus';
  return setProfile(inputs, {
    other_income: [...inputs.profile.other_income, emptyOtherIncome(next)],
  });
}

export function removeOtherIncome(
  inputs: EligibilityInputs,
  index: number,
): EligibilityInputs {
  return setProfile(inputs, {
    other_income: inputs.profile.other_income.filter((_, i) => i !== index),
  });
}

// ------------------------------------------------------------------ checks
// Format hints only: the API validates the inputs (422 with the reason).

export function panLooksValid(pan: string): boolean {
  return /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(pan.replace(/\s/g, '').toUpperCase());
}

/** 'XXXXXX314M': the masked PAN of a pre-fill (the API accepts it back). */
export function isMaskedPan(pan: string | null | undefined): boolean {
  return /^X{6}[0-9]{3}[A-Z]$/.test(
    String(pan ?? '')
      .replace(/\s/g, '')
      .toUpperCase(),
  );
}

export function mobileLooksValid(mobile: string): boolean {
  let digits = mobile.replace(/[\s-]/g, '');
  if (digits.startsWith('+91')) digits = digits.slice(3);
  else if (digits.length === 12 && digits.startsWith('91')) {
    digits = digits.slice(2);
  } else if (digits.length === 11 && digits.startsWith('0')) {
    digits = digits.slice(1);
  }
  return /^[6-9]\d{9}$/.test(digits);
}

export function pincodeLooksValid(pincode: string): boolean {
  return /^[1-9]\d{5}$/.test(pincode.trim());
}

/** CIBIL scores run from 300 to 900. */
export function scoreLooksValid(score: number): boolean {
  return Number.isInteger(score) && score >= 300 && score <= 900;
}

/** The API refuses enquiries that are not cumulative (d30 <= d60 <= d90 <= d120). */
export function enquiriesOutOfOrder(
  enquiries: CibilEnquiries,
): [keyof CibilEnquiries, keyof CibilEnquiries] | null {
  const known = (['d30', 'd60', 'd90', 'd120'] as const).filter(
    (k) => finite(enquiries[k]) !== null,
  );
  for (let i = 1; i < known.length; i += 1) {
    const a = enquiries[known[i - 1]] as number;
    const b = enquiries[known[i]] as number;
    if (a > b) return [known[i - 1], known[i]];
  }
  return null;
}

/** Today as a date input's value (YYYY-MM-DD, local time). */
export function localToday(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A date input's value lies after today (the API wants past dates). */
export function isFutureDate(
  value: string | null | undefined,
  now: Date = new Date(),
): boolean {
  return !!value && value > localToday(now);
}

/** The API's account number format (letters, digits, space . / -; 30 at most). */
export function accountNumberLooksValid(value: string): boolean {
  return /^[A-Za-z0-9 ./-]{1,30}$/.test(value.trim());
}

// ------------------------------------------------------------------ applicant

/**
 * Who the eligibility API is asked about for an applicant of the verdict:
 * the PAN when the verdict has one (a name can match two applicants), else
 * the name; the same value finds the saved inputs again.
 */
export function eligibilityApplicant(applicant: {
  applicant: string;
  pan?: string | null;
}): { id: string; name: string; pan: string | null } {
  const pan = applicant.pan?.replace(/\s/g, '').toUpperCase() || null;
  return { id: pan ?? applicant.applicant, name: applicant.applicant, pan };
}

// ------------------------------------------------------------------ results

export type LenderTone =
  | 'eligible'
  | 'notServiceable'
  | 'notEligible'
  | 'unknown';

function statusKey(value: unknown): string {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
}

export function lenderTone(status: unknown): LenderTone {
  const s = statusKey(status);
  if (s === 'eligible') return 'eligible';
  if (s === 'not_serviceable') return 'notServiceable';
  if (s === 'not_eligible' || s === 'ineligible') return 'notEligible';
  return 'unknown';
}

function sources(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj(value))) {
    if (typeof v === 'string' && v.trim()) out[k] = v.trim();
  }
  return out;
}

function otherIncomeConsidered(raw: unknown): OtherIncomeConsidered | null {
  const o = obj(raw);
  const type = text(o.type);
  if (!type) return null;
  return {
    type,
    label: text(o.label),
    agreement: text(o.agreement),
    frequency: text(o.frequency),
    monthly_amount: finite(o.monthly_amount),
    consideration_pct: finite(o.consideration_pct),
    considered: finite(o.considered),
  };
}

function lenderRow(raw: unknown): LenderEligibility | null {
  const o = obj(raw);
  const lender = text(o.lender) ?? text(o.name);
  if (!lender) return null;
  return {
    lender,
    lender_id: text(o.lender_id),
    status: text(o.status) ?? '',
    status_label: text(o.status_label),
    reasons: strings(o.reasons),
    notes: strings(o.notes),
    eligible_amount: finite(o.eligible_amount),
    computed_amount: finite(o.computed_amount),
    tenure_months: finite(o.tenure_months),
    calculation_tenure_months: finite(o.calculation_tenure_months),
    roi: finite(o.roi),
    emi: finite(o.emi),
    emi_at_calculation_tenure: finite(o.emi_at_calculation_tenure),
    per_lakh_emi: finite(o.per_lakh_emi),
    foir_eligibility: finite(o.foir_eligibility),
    multiplier_eligibility: finite(o.multiplier_eligibility),
    income_considered: finite(o.income_considered),
    other_income_considered: Array.isArray(o.other_income_considered)
      ? o.other_income_considered
          .map(otherIncomeConsidered)
          .filter((r): r is OtherIncomeConsidered => r !== null)
      : [],
    obligations: finite(o.obligations),
    foir: finite(o.foir),
    multiplier: finite(o.multiplier),
    company_category: text(o.company_category),
    company_policy: text(o.company_policy),
    max_amount: finite(o.max_amount),
    min_amount: finite(o.min_amount),
    bt_amount: finite(o.bt_amount),
    covers_bt: bool(o.covers_bt),
    covers_requested: bool(o.covers_requested),
    serviceable: bool(o.serviceable),
    region: text(o.region),
    sources: sources(o.sources),
  };
}

function income(raw: unknown): EligibilityIncome | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = obj(raw);
  return {
    net_salary: finite(o.net_salary),
    net_salary_source: text(o.net_salary_source),
    net_salary_source_label: text(o.net_salary_source_label),
    entered_net_income: finite(o.entered_net_income),
    verified_net_income: finite(o.verified_net_income),
    other_income_monthly: finite(o.other_income_monthly),
  };
}

function counted(raw: unknown): CountedObligation[] {
  const list = obj(raw).counted;
  if (!Array.isArray(list)) return [];
  return list.map((row) => {
    const o = obj(row);
    return {
      index: finite(o.index),
      lender: text(o.lender),
      loan_type: text(o.loan_type),
      emi: finite(o.emi),
      source: text(o.source),
      flag: text(o.flag),
    };
  });
}

/** Validates POST .../calculate; throws on anything but the contract. */
export function parseCalculateResponse(
  raw: unknown,
  applicant: string,
): EligibilityResult {
  const o = obj(raw);
  if (!Array.isArray(o.per_lender)) {
    throw new Error('unexpected response from the eligibility service');
  }
  const perLender = o.per_lender
    .map(lenderRow)
    .filter((r): r is LenderEligibility => r !== null);
  const best = text(o.best_lender);
  const check = obj(o.file_check);
  const fileCheck: EligibilityFileCheck | null =
    o.file_check && typeof o.file_check === 'object'
      ? {
          used: check.used === true,
          detail: text(check.detail),
          verdict: text(check.verdict),
          ready: bool(check.ready),
          issues: strings(check.issues),
        }
      : null;
  return {
    applicant: text(o.applicant) ?? applicant,
    calculatedAt: text(o.calculated_at),
    income_considered: finite(o.income_considered),
    income: income(o.income),
    obligations: finite(o.obligations),
    counted_obligations: counted(o.obligation_details),
    bt_amount: finite(o.bt_amount),
    per_lender: perLender,
    best_lender: best && perLender.some((r) => r.lender === best) ? best : null,
    best_lender_reason: text(o.best_lender_reason),
    // Shipped policies are SAMPLE data: only an explicit false says otherwise.
    sample: o.sample !== false,
    file_check: fileCheck,
    notes: strings(o.notes),
    disclaimers: strings(o.disclaimers),
  };
}

function delivery(raw: unknown): LoginDelivery | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = obj(raw);
  return {
    delivery_id: text(o.delivery_id),
    status: text(o.status) ?? 'failed',
    http_status: finite(o.http_status),
    error: text(o.error),
  };
}

/** POST .../login -> {status: recorded, webhook, delivery|null, ...}. */
export function parseLoginResponse(raw: unknown): EligibilityLoginResponse {
  const o = obj(raw);
  const status = text(o.status);
  if (!status) throw new Error('unexpected response from the login service');
  const d = delivery(o.delivery);
  return {
    status,
    lender: text(o.lender),
    eligible_amount: finite(o.eligible_amount),
    emi: finite(o.emi),
    tenure_months: finite(o.tenure_months),
    roi: finite(o.roi),
    requested_at: text(o.requested_at),
    webhook:
      text(o.webhook) ??
      (d
        ? statusKey(d.status) === 'delivered'
          ? 'delivered'
          : 'failed'
        : 'not_enabled'),
    webhook_detail: text(o.webhook_detail),
    delivery: d,
    file_ready: bool(o.file_ready),
    open_issues: finite(o.open_issues) ?? 0,
  };
}

/** Whether the CRM heard of the login: notified, failed, or not sent. */
export function webhookOutcome(
  response: Pick<EligibilityLoginResponse, 'webhook' | 'delivery'>,
): 'notified' | 'failed' | 'notSent' {
  const w = statusKey(response.webhook);
  if (w === 'delivered') return 'notified';
  if (w === 'failed') return 'failed';
  return 'notSent';
}

function policy(raw: unknown): LenderPolicy | null {
  const o = obj(raw);
  const name = text(o.name) ?? text(o.lender);
  if (!name) return null;
  const categories: LenderPolicy['company_categories'] = {};
  for (const [category, value] of Object.entries(obj(o.company_categories))) {
    const c = obj(value);
    categories[category] = {
      foir: finite(c.foir),
      multiplier: finite(c.multiplier),
    };
  }
  const unlisted =
    o.unlisted_company && typeof o.unlisted_company === 'object'
      ? obj(o.unlisted_company)
      : null;
  return {
    id: text(o.id) ?? text(o.lender_id) ?? name,
    name,
    product: text(o.product),
    roi: finite(o.roi),
    min_tenure_months: finite(o.min_tenure_months),
    max_tenure_months: finite(o.max_tenure_months),
    foir: finite(o.foir),
    multiplier: finite(o.multiplier),
    min_amount: finite(o.min_amount),
    max_amount: finite(o.max_amount),
    min_cibil_score: finite(o.min_cibil_score),
    max_enquiries_90d: finite(o.max_enquiries_90d),
    employment_types: strings(o.employment_types),
    company_categories: categories,
    unlisted_company: unlisted
      ? {
          accepted: unlisted.accepted === true,
          foir: finite(unlisted.foir),
          multiplier: finite(unlisted.multiplier),
        }
      : null,
    serviceable_regions: strings(o.serviceable_regions),
  };
}

/** GET .../lenders: {sample, label, lenders, disclaimers} (or a bare list). */
export function parseLenders(raw: unknown): LendersResponse {
  const o = obj(raw);
  const list = Array.isArray(raw) ? raw : o.lenders;
  if (!Array.isArray(list)) {
    throw new Error('unexpected response from the lenders API');
  }
  return {
    sample: o.sample !== false,
    label: text(o.label),
    lenders: list.map(policy).filter((p): p is LenderPolicy => p !== null),
    disclaimers: strings(o.disclaimers),
  };
}

/** GET .../pincodes/{pincode} ("Check availability"). */
export function parsePincodeCheck(raw: unknown, pincode: string): PincodeCheck {
  const o = obj(raw);
  if (!Array.isArray(o.lenders)) {
    throw new Error('unexpected response from the pincode check');
  }
  return {
    pincode: text(o.pincode) ?? pincode,
    region: text(o.region),
    lenders: o.lenders
      .map((row): PincodeLenderCheck | null => {
        const r = obj(row);
        const lender = text(r.lender);
        return lender
          ? {
              lender_id: text(r.lender_id) ?? lender,
              lender,
              serviceable: r.serviceable === true,
              source: checkSource(r.source),
            }
          : null;
      })
      .filter((r): r is PincodeLenderCheck => r !== null),
    sample: o.sample !== false,
  };
}

type PincodeLenderCheck = PincodeCheck['lenders'][number];

function checkSource(value: unknown): CheckSource {
  return value === 'dsa_list' ? 'dsa_list' : 'sample';
}

function foirRange(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const [low, high] = value.map(finite);
  return low !== null && high !== null && low < high ? [low, high] : null;
}

/** GET .../companies?name= ("Check category"). */
export function parseCompanyCheck(raw: unknown, name: string): CompanyCheck {
  const o = obj(raw);
  if (!Array.isArray(o.categories)) {
    throw new Error('unexpected response from the company check');
  }
  const match = o.match && typeof o.match === 'object' ? obj(o.match) : null;
  return {
    query: text(o.query) ?? name,
    match:
      match && text(match.name)
        ? {
            name: text(match.name) as string,
            employment_type: text(match.employment_type),
          }
        : null,
    categories: o.categories
      .map((row): CompanyCategory | null => {
        const r = obj(row);
        const lender = text(r.lender);
        if (!lender) return null;
        return {
          lender_id: text(r.lender_id) ?? lender,
          lender,
          category: text(r.category),
          listed: r.listed === true,
          accepted: r.accepted === true,
          foir: finite(r.foir),
          multiplier: finite(r.multiplier),
          foir_range: foirRange(r.foir_range),
          source: checkSource(r.source),
        };
      })
      .filter((r): r is CompanyCategory => r !== null),
    suggestions: strings(o.suggestions),
    sample: o.sample !== false,
  };
}
