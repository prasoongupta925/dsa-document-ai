// Mirrors packages/backend/app/routers/eligibility.py
// (prefix /projects/{project_id}/eligibility):
//   GET  .../lenders              SAMPLE lender policies and the form's choices
//   GET  .../pincodes/{pincode}   "Check availability": the lenders serving a pincode
//   GET  .../companies?name=      "Check category": a company's category per lender
//   GET  .../inputs?applicant=    saved inputs, else a draft filled from the documents
//   PUT  .../inputs               save an applicant's inputs (deleted by TTL after 7 days)
//   POST .../calculate            eligibility at every lender (app/eligibility.py)
//   POST .../login                record a login request, notify the CRM webhook
// Every number is the backend's (fixed formulas, never a model); the UI shows
// them as returned. Every lender policy, pincode list and company category is
// SAMPLE data. Choices are sent and returned as ids (private_limited, bt).

// ------------------------------------------------------------------ sources

/** A document a value was read from: "from <file>, page N". */
export interface DocumentRef {
  document_id: string | null;
  /** The file name, e.g. 01_loan_application_form.pdf */
  file: string;
  /** 1-based; null when the facts record does not say. */
  page: number | null;
  doc_type: string | null;
}

/**
 * Where a value of the form came from (GET .../inputs): `value` is what the
 * documents give, and the field shows the source while it holds that value
 * (typed values always win). Never sent back.
 */
export interface FieldSource {
  /** document = From Document (the files below); table = From Table (the company list). */
  source: 'document' | 'table';
  value: unknown;
  documents: DocumentRef[];
  /** e.g. "verified: salary slips, median net pay" */
  detail: string | null;
  /** An amount the facts check did not find in the document's text. */
  unverified: boolean;
}

/**
 * The legend of the panel: the sheet's From Policy (grey), Formula
 * Calculation (yellow) and From Table (green), and From Document (blue).
 */
export type SourceKind = 'document' | 'table' | 'policy' | 'formula';

/**
 * "Still needed before Check eligibility": a field no document filled and
 * nobody typed, or a loan a lender cannot count yet.
 */
export interface StillNeeded {
  /** e.g. "pincode", "score", "enquiries.d90", "tradelines.2.emi" (1-based loan). */
  field: string;
  /** Every lender's check needs it (else a field of the sheet left empty). */
  required: boolean;
  /** A document holds a value for it that the inputs do not have. */
  fromDocuments: boolean;
  /** The tab the field is on. */
  tab: 'profile' | 'cibil';
  /** tradelines.N.*: the loan's number, lender and type. */
  loan?: { number: number; lender: string | null; loanType: LoanType | null };
}

// ------------------------------------------------------------------ profile

/** The documents also give parental and company-provided homes. */
export type HouseOwnership =
  | 'owned'
  | 'rented'
  | 'parental'
  | 'company_provided';

/** The client's "Employement Type" list. */
export type EmploymentType =
  | 'defence'
  | 'government'
  | 'grade_4'
  | 'llp'
  | 'merchant_navy'
  | 'partnership_proprietorship'
  | 'private_limited'
  | 'public_limited';

export type OtherIncomeType = 'rented' | 'bonus' | 'incentive' | 'pension';

export type IncomeFrequency =
  | 'yearly'
  | 'half_yearly'
  | 'quarterly'
  | 'monthly';

/** Rent agreement of a rented income. */
export type RentAgreement = 'notary' | 'registered';

export interface OtherIncome {
  /** Row key in the form; never sent. */
  key?: string;
  type: OtherIncomeType;
  /** Rupees per `frequency` (rented income and pension: per month). */
  amount: number | null;
  /** Bonus and incentive (without it the backend counts yearly). */
  frequency?: IncomeFrequency | null;
  /** Rented income only (without it the backend counts notary). */
  agreement?: RentAgreement | null;
  /** The document row it came from (GET .../inputs); never sent. */
  document?: FieldSource | null;
}

export interface EligibilityProfile {
  /** ABCDE1234F, or the masked PAN of a pre-fill (XXXXXX234F). */
  pan: string | null;
  /** Name as per PAN */
  name: string | null;
  mobile: string | null;
  /** YYYY-MM-DD */
  dob: string | null;
  house_ownership: HouseOwnership | null;
  pincode: string | null;
  current_address: string | null;
  permanent_address: string | null;
  company: string | null;
  employment_type: EmploymentType | null;
  /** Net monthly salary; a salary the file check verified is used instead. */
  net_income: number | null;
  other_income: OtherIncome[];
}

// ------------------------------------------------------------------ CIBIL

export type LoanType =
  | 'personal'
  | 'home'
  | 'mortgage'
  | 'car'
  | 'education'
  | 'application'
  | 'consumer'
  | 'credit_card';

/**
 * What happens to an existing loan: BT (the new lender takes over the
 * outstanding; its EMI is not an obligation), Obligate (it keeps running:
 * its EMI is an obligation) or Close (closed before disbursal).
 */
export type TradelineAction = 'bt' | 'obligate' | 'close';

/** The bureau's account status; the adverse ones are reported as notes. */
export type TradelineStatus =
  | 'active'
  | 'closed'
  | 'settled'
  | 'written_off'
  | 'suit_filed'
  | 'wilful_default'
  | 'restructured';

/**
 * Who filled a tradeline: by hand, a bureau pull, the uploaded credit report,
 * or the bank statement (a suggestion of the pre-fill).
 */
export type TradelineSource =
  | 'manual'
  | 'bureau'
  | 'credit_report'
  | 'bank_statement';

export interface Tradeline {
  /** Row key in the form; never sent. */
  key?: string;
  loan_type: LoanType | null;
  lender: string | null;
  sanction_amount: number | null;
  /** Needed for BT: the balance-transfer amount. */
  outstanding: number | null;
  /** Monthly EMI; needed for Obligate. */
  emi: number | null;
  status: TradelineStatus | null;
  account_number?: string | null;
  overdue?: number | null;
  emis_paid?: number | null;
  emis_pending?: number | null;
  /** YYYY-MM-DD */
  open_date?: string | null;
  /** YYYY-MM-DD */
  last_payment_date?: string | null;
  action: TradelineAction;
  /** Kept as received (a bureau pull or the pre-fill sets it); default manual. */
  source?: TradelineSource | null;
  /** The document row it came from (GET .../inputs); never sent. */
  document?: FieldSource | null;
}

/** Cumulative enquiries in the last 30 / 60 / 90 / 120 days. */
export interface CibilEnquiries {
  d30: number | null;
  d60: number | null;
  d90: number | null;
  d120: number | null;
}

/** Who filled the CIBIL block: by hand, the uploaded credit report, or a bureau pull. */
export type CibilSource = 'manual' | 'bureau' | 'credit_report';

/** What the documents give the CIBIL block (GET .../inputs). */
export interface CibilSources {
  score?: FieldSource;
  /** value: {d30, d60, d90, d120} */
  enquiries?: FieldSource;
  /** value: the report's date; detail: "CIBIL report"; documents: the report. */
  report?: FieldSource;
  /**
   * The documents' loans (the credit report's, and the bank statement's other
   * loan EMIs as suggestions), each with its `document`, saved or not.
   */
  tradelines?: Tradeline[];
}

export interface EligibilityCibil {
  score: number | null;
  enquiries: CibilEnquiries;
  tradelines: Tradeline[];
  /** Kept as received; default manual. */
  source?: CibilSource | null;
  /** YYYY-MM-DD of the credit report or bureau pull. */
  report_date?: string | null;
  /** What the documents give this block (GET .../inputs); never sent. */
  sources?: CibilSources | null;
}

export interface EligibilityLoan {
  amount: number | null;
  tenure_months: number | null;
}

export interface EligibilityInputs {
  profile: EligibilityProfile;
  cibil: EligibilityCibil;
  loan: EligibilityLoan;
}

/** PUT .../inputs (the API forbids other fields). */
export interface EligibilityInputsRequest extends EligibilityInputs {
  /** PAN (preferred) or name as the file check shows it; the same value finds the inputs again. */
  applicant: string;
}

/** Fields a document can fill, in the order of the client's sheets. */
export type PrefillField =
  | 'name'
  | 'pan'
  | 'mobile'
  | 'dob'
  | 'house_ownership'
  | 'pincode'
  | 'current_address'
  | 'permanent_address'
  | 'company'
  | 'employment_type'
  | 'net_income'
  | 'other_income'
  | 'loan_amount'
  | 'tenure_months'
  | 'score'
  | 'enquiries'
  | 'tradelines';

/** A field holding one value (the rows, other income and tradelines, have their own sources). */
export type SourcedField = Exclude<PrefillField, 'other_income' | 'tradelines'>;

/**
 * GET .../inputs `sources`: per field (and `report`, the credit report), what
 * the documents give it.
 */
export type FieldSources = Partial<
  Record<SourcedField | 'report', FieldSource>
>;

/** GET .../inputs `document_rows`: the documents' rows, each with its `document`. */
export interface DocumentRows {
  other_income: OtherIncome[];
  tradelines: Tradeline[];
}

/** How a draft was pre-filled from the file check (GET .../inputs). */
export interface EligibilityPrefill {
  available: boolean;
  detail: string | null;
  /** e.g. "verified: salary slips, median net pay" */
  income_source: string | null;
  suggested_tradelines: number;
  /** The credit report the CIBIL block was read from. */
  credit_report: string | null;
  documents: number;
}

/** GET / PUT .../inputs (normalized by lib/eligibility.parseInputsResponse). */
export interface EligibilityInputsResponse {
  applicant: string;
  /** Each row carries its `document`, the CIBIL block its `sources`. */
  inputs: EligibilityInputs;
  /** Fields the draft took from the applicant's documents. */
  fromDocuments: PrefillField[];
  prefill: EligibilityPrefill | null;
  /** What the documents give each field (saved inputs too: typed values win). */
  sources: FieldSources;
  /** The documents' rows, in the inputs or not. */
  documentRows: DocumentRows;
  /** The API's "Still needed before Check eligibility" for the inputs returned. */
  stillNeeded: StillNeeded[];
  /** The inputs were saved (else a draft, pre-filled when possible). */
  saved: boolean;
  /** When the saved inputs are deleted (DynamoDB TTL), ISO. */
  expiresAt: string | null;
  notes: string[];
}

// ------------------------------------------------------------------ lenders

/** A lender's policy for companies missing from its category list. */
export interface UnlistedCompanyPolicy {
  accepted: boolean;
  foir: number | null;
  multiplier: number | null;
}

/**
 * A SAMPLE lender policy (GET .../lenders, app/data/lender_policies.json);
 * every value is shown as returned.
 */
export interface LenderPolicy {
  id: string;
  name: string;
  product: string | null;
  /** Annual rate, percent (11.0). */
  roi: number | null;
  min_tenure_months: number | null;
  max_tenure_months: number | null;
  /** Base FOIR, a fraction of the monthly income (0.7). */
  foir: number | null;
  multiplier: number | null;
  min_amount: number | null;
  max_amount: number | null;
  min_cibil_score: number | null;
  max_enquiries_90d: number | null;
  employment_types: string[];
  /** Category name -> the FOIR and multiplier it gives. */
  company_categories: Record<
    string,
    { foir: number | null; multiplier: number | null }
  >;
  unlisted_company: UnlistedCompanyPolicy | null;
  serviceable_regions: string[];
}

export interface LendersResponse {
  sample: boolean;
  label: string | null;
  lenders: LenderPolicy[];
  disclaimers: string[];
}

/** GET .../pincodes/{pincode} */
export interface PincodeCheck {
  pincode: string;
  /** The SAMPLE region the pincode is in, if any. */
  region: string | null;
  lenders: {
    lender_id: string;
    lender: string;
    serviceable: boolean;
    /** dsa_list: the DSA's uploaded serviceability list; sample: the SAMPLE list. */
    source?: CheckSource;
  }[];
  sample: boolean;
}

/** Where a check's answer came from: the DSA's uploaded list, or the SAMPLE list. */
export type CheckSource = 'dsa_list' | 'sample';

/** One lender's answer in GET .../companies?name= */
export interface CompanyCategory {
  lender_id: string;
  lender: string;
  /** null when the company is not in this lender's list. */
  category: string | null;
  listed: boolean;
  /** Listed, or unlisted and the lender takes unlisted companies. */
  accepted: boolean;
  foir: number | null;
  multiplier: number | null;
  /**
   * A lender with a FOIR grid: the category's lowest and highest FOIR over
   * the net salary slabs (foir is the lowest slab's).
   */
  foir_range?: [number, number] | null;
  /** dsa_list: the DSA's uploaded company list; sample: the SAMPLE list. */
  source?: CheckSource;
}

/** GET .../companies?name= */
export interface CompanyCheck {
  query: string;
  /** The listed company the name is (legal suffixes ignored), if any. */
  match: { name: string; employment_type: string | null } | null;
  categories: CompanyCategory[];
  /** Listed companies whose name contains the query. */
  suggestions: string[];
  sample: boolean;
}

// ------------------------------------------------------------------ calculate
// app/eligibility.calculate: money is rounded to the paisa, the ROI is a
// percent (11.0), FOIR a fraction (0.7).

export type LenderStatus = 'eligible' | 'not_serviceable' | 'not_eligible';

/** The sheet's legend: policy (From Policy), formula (Formula Calculation), table (From Table). */
export type ParameterSource = 'policy' | 'formula' | 'table';

/** Parameter -> where its value came from (ids as above). */
export type ParameterSources = Record<string, string>;

/** One other income as a lender counts it. */
export interface OtherIncomeConsidered {
  type: string;
  label: string | null;
  agreement: string | null;
  frequency: string | null;
  monthly_amount: number | null;
  consideration_pct: number | null;
  considered: number | null;
}

export interface LenderEligibility {
  lender: string;
  lender_id: string | null;
  /** eligible, not_serviceable or not_eligible (other values shown as sent). */
  status: string;
  status_label: string | null;
  /** Why the lender is not eligible / not serviceable (every reason). */
  reasons: string[];
  /** Remarks that do not change the status (e.g. tenure capped). */
  notes: string[];
  /** min(FOIR eligibility, multiplier eligibility, max amount), to the paisa; 0 unless eligible. */
  eligible_amount: number | null;
  /** What the formulas give, whatever the status. */
  computed_amount: number | null;
  /** The lender's max tenure: the EMI is shown at it. */
  tenure_months: number | null;
  /**
   * The per-lakh EMI's tenure: the lender's calculation tenure, or a shorter
   * requested tenure (at least the lender's min).
   */
  calculation_tenure_months: number | null;
  roi: number | null;
  /** At the lender's max tenure; 0 unless eligible. */
  emi: number | null;
  /** The EMI over calculation_tenure_months; 0 unless eligible. */
  emi_at_calculation_tenure: number | null;
  per_lakh_emi: number | null;
  foir_eligibility: number | null;
  multiplier_eligibility: number | null;
  /** Net salary plus the other income at this lender's consideration %. */
  income_considered: number | null;
  other_income_considered: OtherIncomeConsidered[];
  obligations: number | null;
  foir: number | null;
  multiplier: number | null;
  company_category: string | null;
  /** category, unlisted, or base (no company entered). */
  company_policy: string | null;
  max_amount: number | null;
  min_amount: number | null;
  bt_amount: number | null;
  covers_bt: boolean | null;
  covers_requested: boolean | null;
  /** The pincode is serviceable; null when no pincode was entered. */
  serviceable: boolean | null;
  region: string | null;
  sources: ParameterSources;
}

/** POST .../calculate {applicant, inputs?}: inputs are calculated, not saved. */
export interface EligibilityCalculateRequest {
  applicant: string;
  inputs?: EligibilityInputs;
}

export interface EligibilityIncome {
  net_salary: number | null;
  /** verified (from the file check), entered, or null */
  net_salary_source: string | null;
  net_salary_source_label: string | null;
  entered_net_income: number | null;
  verified_net_income: number | null;
  other_income_monthly: number | null;
}

/** One obligation the calculation counted. */
export interface CountedObligation {
  /** 1-based tradeline number; null for a bank-statement EMI. */
  index: number | null;
  lender: string | null;
  loan_type: string | null;
  emi: number | null;
  /** tradeline or bank_statement */
  source: string | null;
  /** Why a bank-statement EMI was counted (it matches no tradeline). */
  flag: string | null;
}

export interface EligibilityResult {
  applicant: string;
  calculatedAt: string | null;
  /** Net monthly salary used (verified from the documents, else as entered). */
  income_considered: number | null;
  income: EligibilityIncome | null;
  /** Monthly obligations: EMIs of the loans marked Obligate and unmatched bank EMIs. */
  obligations: number | null;
  counted_obligations: CountedObligation[];
  bt_amount: number | null;
  per_lender: LenderEligibility[];
  best_lender: string | null;
  best_lender_reason: string | null;
  /** The policies are SAMPLE data. */
  sample: boolean;
  /** Whether the file check's verified figures were used (and why not), and its verdict. */
  file_check: EligibilityFileCheck | null;
  notes: string[];
  disclaimers: string[];
}

/** The file check behind a calculation. */
export interface EligibilityFileCheck {
  used: boolean;
  detail: string | null;
  /** READY or NOT READY; null without the file check. */
  verdict: string | null;
  ready: boolean | null;
  /** The file check's reasons for NOT READY, in order. */
  issues: string[];
}

// ------------------------------------------------------------------ login

/** POST .../login {applicant, lender (id or name)} */
export interface EligibilityLoginRequest {
  applicant: string;
  lender: string;
  /** The user saw the open issues of a NOT READY file and logs it in anyway (else 428). */
  confirm_not_ready?: boolean;
}

/** The CRM webhook delivery made for a login. */
export interface LoginDelivery {
  delivery_id: string | null;
  /** delivered or failed */
  status: string;
  http_status: number | null;
  error: string | null;
}

/** delivered, failed, not_enabled, not_configured or skipped */
export type LoginWebhook =
  | 'delivered'
  | 'failed'
  | 'not_enabled'
  | 'not_configured'
  | 'skipped';

export interface EligibilityLoginResponse {
  /** "recorded" */
  status: string;
  lender: string | null;
  eligible_amount: number | null;
  emi: number | null;
  tenure_months: number | null;
  roi: number | null;
  requested_at: string | null;
  webhook: LoginWebhook | string;
  webhook_detail: string | null;
  /** The CRM webhook delivery; null when none was attempted. */
  delivery: LoginDelivery | null;
  /** The file check's verdict at login: false = logged in NOT READY (confirmed). */
  file_ready: boolean | null;
  open_issues: number;
}
