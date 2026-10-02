// Synthetic eligibility responses for tests, in the shape of
// packages/backend/app/eligibility.calculate. ICICI Bank and HDFC Bank
// reproduce the client's worked example (income 98,000, FOIR 0.70,
// multiplier 21, obligation 15,000, tenure for calculation 60 months, ROI
// 11%); the other lenders' statuses are illustrative. The applicant, employer
// and loans are the platform's synthetic demo data.

/** GET .../eligibility/inputs for a synthetic applicant, pre-filled from the documents. */
export const PREFILLED_INPUTS_RESPONSE = {
  applicant: 'Sneha Anil Kulkarni',
  saved: false,
  from_documents: ['name', 'pan', 'company', 'employment_type', 'net_income'],
  prefill: {
    available: true,
    detail: 'Pre-filled from the file check (5 documents)',
    applicant_name: 'Sneha Anil Kulkarni',
    pan_masked: 'XXXXXX314M',
    employer: 'Konkan Softworks Pvt Ltd',
    verified_net_income: 98000,
    income_source: 'verified: salary slips, median net pay',
    dob: null,
    suggested_tradelines: 0,
    documents: 5,
  },
  notes: [],
  label: 'sample policy — replace with your lender grid',
  inputs: {
    profile: {
      pan: 'XXXXXX314M',
      name: 'Sneha Anil Kulkarni',
      mobile: null,
      dob: null,
      house_ownership: null,
      pincode: null,
      current_address: null,
      permanent_address: null,
      company: 'Konkan Softworks Pvt Ltd',
      employment_type: 'private_limited',
      net_income: 98000,
      other_income: [],
    },
    cibil: {
      score: null,
      enquiries: { d30: null, d60: null, d90: null, d120: null },
      tradelines: [],
    },
    loan: { amount: null, tenure_months: null },
  },
};

/** Saved inputs: two loans on the report, one obligated (EMI 15,000). */
export const SAVED_INPUTS_RESPONSE = {
  applicant: 'CKRPK7314M',
  saved: true,
  created_at: '2026-09-30T10:00:00.000000+00:00',
  updated_at: '2026-09-30T10:05:00.000000+00:00',
  expires_at: '2026-10-07T10:00:00.000000+00:00',
  from_documents: [],
  prefill: null,
  notes: [],
  inputs: {
    profile: {
      ...PREFILLED_INPUTS_RESPONSE.inputs.profile,
      house_ownership: 'rented',
      pincode: '400615',
      other_income: [
        { type: 'bonus', frequency: 'yearly', amount: 60000 },
        { type: 'rented', agreement: 'registered', amount: 12000 },
      ],
    },
    cibil: {
      score: 772,
      enquiries: { d30: 1, d60: 2, d90: 3, d120: 4 },
      tradelines: [
        {
          loan_type: 'personal',
          lender: 'Axis Bank',
          sanction_amount: 600000,
          outstanding: 410000,
          emi: 15000,
          status: 'active',
          account_number: 'PL00001234',
          emis_paid: 14,
          emis_pending: 34,
          open_date: '2025-07-05',
          last_payment_date: '2026-09-05',
          action: 'obligate',
        },
        {
          loan_type: 'credit_card',
          lender: 'Kotak Mahindra Bank',
          sanction_amount: 150000,
          outstanding: 18000,
          emi: 0,
          status: 'active',
          action: 'close',
        },
      ],
    },
    loan: { amount: 1000000, tenure_months: 60 },
  },
};

// The engine's parameter sources (the sheet's legend).
const SOURCES = {
  income: 'table',
  obligations: 'table',
  bt_amount: 'table',
  pincode: 'table',
  company_category: 'table',
  foir: 'policy',
  multiplier: 'policy',
  roi: 'policy',
  tenure_months: 'policy',
  max_amount: 'policy',
  calculation_tenure_months: 'table',
  per_lakh_emi: 'formula',
  foir_eligibility: 'formula',
  multiplier_eligibility: 'formula',
  eligible_amount: 'formula',
  emi: 'formula',
};

const COMMON = {
  income_considered: 98000,
  other_income_considered: [],
  obligations: 15000,
  bt_amount: 0,
  covers_bt: null,
  covers_requested: true,
  region: 'Thane (Ghodbunder Road)',
  sources: SOURCES,
  label: 'sample policy — replace with your lender grid',
};

/**
 * POST .../eligibility/calculate: the worked example. ICICI Bank is limited by
 * the multiplier (20,58,000 < 24,65,226.61), its EMI shown at its 72-month
 * maximum tenure; HDFC Bank by its 15,00,000 cap (60 months, 12%).
 */
export const WORKED_EXAMPLE_RESPONSE = {
  applicant: 'Sneha Anil Kulkarni',
  sample: true,
  policy_label: 'sample policy — replace with your lender grid',
  label: 'indicative — the lender decides',
  income_considered: 98000,
  income: {
    net_salary: 98000,
    net_salary_source: 'verified',
    net_salary_source_label:
      'verified from the documents (salary slips and bank credits)',
    entered_net_income: 98000,
    verified_net_income: 98000,
    other_income: [],
    other_income_monthly: 0,
  },
  obligations: 15000,
  obligation_details: {
    counted: [
      {
        index: 1,
        lender: 'Axis Bank',
        loan_type: 'personal',
        action: 'obligate',
        emi: 15000,
        outstanding: 410000,
        source: 'tradeline',
        flag: null,
      },
    ],
    bt: [],
    closed: [],
    not_counted: [],
    bank_statement_emis: [],
  },
  bt_amount: 0,
  requested: { amount: 1000000, tenure_months: 60 },
  per_lender: [
    {
      ...COMMON,
      lender: 'ICICI Bank',
      lender_id: 'icici_bank',
      status: 'eligible',
      status_label: 'Eligible',
      reasons: [],
      notes: [],
      eligible_amount: 2058000,
      computed_amount: 2058000,
      tenure_months: 72,
      roi: 11,
      emi: 39172.13,
      calculation_tenure_months: 60,
      emi_at_calculation_tenure: 44745.91,
      per_lakh_emi: 2174.24,
      foir_eligibility: 2465226.61,
      multiplier_eligibility: 2058000,
      foir: 0.7,
      multiplier: 21,
      company_category: 'CAT A',
      company_policy: 'category',
      max_amount: 4000000,
      min_amount: 50000,
      serviceable: true,
    },
    {
      ...COMMON,
      lender: 'HDFC Bank',
      lender_id: 'hdfc_bank',
      status: 'eligible',
      status_label: 'Eligible',
      reasons: [],
      notes: [],
      eligible_amount: 1500000,
      computed_amount: 1500000,
      tenure_months: 60,
      roi: 12,
      emi: 33366.67,
      calculation_tenure_months: 60,
      emi_at_calculation_tenure: 33366.67,
      per_lakh_emi: 2224.44,
      foir_eligibility: 2409590.06,
      multiplier_eligibility: 1960000,
      foir: 0.7,
      multiplier: 20,
      company_category: 'CAT A',
      company_policy: 'category',
      max_amount: 1500000,
      min_amount: 50000,
      serviceable: true,
    },
    {
      ...COMMON,
      lender: 'Axis Bank',
      lender_id: 'axis_bank',
      status: 'not_serviceable',
      status_label: 'Not serviceable',
      reasons: ['Pincode 400615 is not serviceable by Axis Bank'],
      notes: [
        'Tradeline 1 (Axis Bank, Personal Loan) is with Axis Bank itself: a lender cannot take over its own loan by balance transfer (a top-up instead)',
      ],
      eligible_amount: 0,
      computed_amount: 1470000,
      tenure_months: 60,
      roi: 11.5,
      emi: 0,
      calculation_tenure_months: 60,
      emi_at_calculation_tenure: 0,
      per_lakh_emi: 2199.26,
      foir_eligibility: 1991578.32,
      multiplier_eligibility: 1470000,
      foir: 0.6,
      multiplier: 15,
      company_category: 'CAT B',
      company_policy: 'category',
      max_amount: 2500000,
      min_amount: 50000,
      covers_requested: null,
      serviceable: false,
    },
    {
      ...COMMON,
      lender: 'Bajaj Finance',
      lender_id: 'bajaj_finance',
      status: 'not_eligible',
      status_label: 'Not eligible',
      reasons: [
        "7 enquiries in the last 90 days: more than Bajaj Finance's limit of 6",
      ],
      notes: [],
      eligible_amount: 0,
      computed_amount: 1960000,
      tenure_months: 84,
      roi: 14,
      emi: 0,
      calculation_tenure_months: 60,
      emi_at_calculation_tenure: 0,
      per_lakh_emi: 2326.83,
      foir_eligibility: 2303568.08,
      multiplier_eligibility: 1960000,
      foir: 0.7,
      multiplier: 20,
      company_category: 'Prime',
      company_policy: 'category',
      max_amount: 3500000,
      min_amount: 100000,
      covers_requested: null,
      serviceable: true,
    },
  ],
  best_lender: 'ICICI Bank',
  best_lender_id: 'icici_bank',
  best_lender_reason: 'lowest ROI among the lenders that cover ₹10,00,000',
  notes: [],
  disclaimers: [
    'Indicative — the lender decides: the final eligibility, amount, ROI and tenure are the lender’s decision.',
    'Sample policy — replace with your lender grid: the lender policies, pincode lists and company categories used here are SAMPLE data, not any lender’s real grid.',
  ],
};

/** POST .../eligibility/login answers (LoginResponse). */
const LOGIN = {
  status: 'recorded',
  applicant: 'Sneha Anil Kulkarni',
  lender: 'ICICI Bank',
  lender_id: 'icici_bank',
  eligible_amount: 2058000,
  emi: 39172.13,
  tenure_months: 72,
  roi: 11,
  requested_at: '2026-09-30T16:10:00.000000+00:00',
  label: 'indicative — the lender decides',
  policy_label: 'sample policy — replace with your lender grid',
};

export const LOGIN_NOTIFIED = {
  ...LOGIN,
  webhook: 'delivered',
  webhook_detail: null,
  delivery: {
    delivery_id: 'dlv_01',
    status: 'delivered',
    http_status: 200,
    error: null,
  },
};

export const LOGIN_NO_WEBHOOK = {
  ...LOGIN,
  webhook: 'not_enabled',
  webhook_detail: null,
  delivery: null,
};

export const LOGIN_WEBHOOK_FAILED = {
  ...LOGIN,
  webhook: 'failed',
  webhook_detail: null,
  delivery: {
    delivery_id: 'dlv_02',
    status: 'failed',
    http_status: 500,
    error: 'receiver answered HTTP 500',
  },
};

/** GET .../eligibility/lenders (subset of the sample grid). */
export const LENDERS_RESPONSE = {
  sample: true,
  label: 'sample policy — replace with your lender grid',
  lenders: [
    {
      id: 'hdfc_bank',
      name: 'HDFC Bank',
      roi: 12.0,
      foir: 0.7,
      multiplier: 20,
      min_tenure_months: 12,
      max_tenure_months: 60,
      min_amount: 50000,
      max_amount: 1500000,
      min_cibil_score: 750,
      max_enquiries_90d: 4,
      unlisted_company: { accepted: false },
    },
    {
      id: 'icici_bank',
      name: 'ICICI Bank',
      roi: 11.0,
      foir: 0.7,
      multiplier: 21,
      min_tenure_months: 12,
      max_tenure_months: 72,
      min_amount: 50000,
      max_amount: 4000000,
      min_cibil_score: 725,
      max_enquiries_90d: 5,
      unlisted_company: { accepted: true, foir: 0.5, multiplier: 10 },
    },
  ],
};

// The documents' rows of FULL_DRAFT_RESPONSE: in a draft, the rows of the
// inputs are the documents' rows (row_sources = document_rows).
const FULL_DRAFT_ROWS = {
  other_income: [
    {
      source: 'document',
      value: {
        type: 'rented',
        amount: 12000.0,
        frequency: null,
        agreement: 'registered',
      },
      documents: [
        {
          document_id: 'r-08',
          file: '08_rent_agreement_flat_12.pdf',
          page: null,
          doc_type: 'rent_agreement',
        },
      ],
      detail: null,
      unverified: false,
    },
    {
      source: 'document',
      value: {
        type: 'bonus',
        amount: 60000.0,
        frequency: 'yearly',
        agreement: null,
      },
      documents: [
        {
          document_id: 'r-05',
          file: '05_salary_slip_2026-08.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
      ],
      detail:
        'Bonus on 1 of 3 salary slips: counted as yearly (the lowest); change how often it is paid if it is more often',
      unverified: false,
    },
    {
      source: 'document',
      value: {
        type: 'incentive',
        amount: 5000.0,
        frequency: 'monthly',
        agreement: null,
      },
      documents: [
        {
          document_id: 'r-03',
          file: '03_salary_slip_2026-06.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
        {
          document_id: 'r-04',
          file: '04_salary_slip_2026-07.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
        {
          document_id: 'r-05',
          file: '05_salary_slip_2026-08.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
      ],
      detail:
        'Incentive on each of the 3 salary slips: counted monthly (the lowest month)',
      unverified: false,
    },
  ],
  tradelines: [
    {
      source: 'document',
      value: {
        loan_type: 'car',
        lender: 'Mulshi Auto Finance Ltd (sample)',
        sanction_amount: 450000.0,
        outstanding: 176000.0,
        emi: 8200.0,
        status: 'active',
        account_number: 'XXXX4410',
        overdue: 0.0,
        emis_paid: 30,
        emis_pending: 18,
        open_date: '2024-03-05',
        last_payment_date: '2026-09-05',
        action: 'obligate',
        source: 'credit_report',
      },
      documents: [
        {
          document_id: 'r-09',
          file: '09_sample_credit_report.pdf',
          page: 2,
          doc_type: 'credit_report',
        },
      ],
      detail: null,
      unverified: false,
    },
    {
      source: 'document',
      value: {
        loan_type: 'credit_card',
        lender: 'Sample Bank Card (sample)',
        sanction_amount: 150000.0,
        outstanding: 18000.0,
        emi: null,
        status: 'active',
        account_number: 'XXXX9921',
        overdue: null,
        emis_paid: null,
        emis_pending: null,
        open_date: null,
        last_payment_date: null,
        action: 'obligate',
        source: 'credit_report',
      },
      documents: [
        {
          document_id: 'r-09',
          file: '09_sample_credit_report.pdf',
          page: 2,
          doc_type: 'credit_report',
        },
      ],
      detail: null,
      unverified: false,
    },
    {
      source: 'document',
      value: {
        loan_type: 'consumer',
        lender: 'Deccan Consumer Finance (sample)',
        sanction_amount: 45000.0,
        outstanding: 0.0,
        emi: 0.0,
        status: 'closed',
        account_number: 'XXXX1188',
        overdue: null,
        emis_paid: null,
        emis_pending: null,
        open_date: null,
        last_payment_date: null,
        action: 'close',
        source: 'credit_report',
      },
      documents: [
        {
          document_id: 'r-09',
          file: '09_sample_credit_report.pdf',
          page: 3,
          doc_type: 'credit_report',
        },
      ],
      detail: null,
      unverified: false,
    },
  ],
};

/**
 * GET .../eligibility/inputs for a synthetic applicant whose file holds every
 * document the CIBIL page reads (the backend's answer in
 * tests/test_eligibility_api.py TestDocumentDraft): each field with its source
 * (file and page), the credit report's CAM block, the documents' rows and
 * what is still needed.
 */
export const FULL_DRAFT_RESPONSE = {
  applicant: 'BQXPD4821K',
  saved: false,
  inputs: {
    profile: {
      pan: 'XXXXXX821K',
      name: 'Rahul Vijay Deshmukh',
      mobile: '9000000101',
      dob: '1992-02-14',
      house_ownership: 'rented',
      pincode: '400606',
      current_address:
        'Flat 12, Sample Residency, Pokhran Road, Thane West, Thane 400606',
      permanent_address:
        'Flat 12, Sample Residency, Pokhran Road, Thane West, Thane 400606',
      company: 'Konkan Softworks Pvt Ltd',
      employment_type: 'private_limited',
      net_income: 82500.0,
      other_income: [
        {
          type: 'rented',
          amount: 12000.0,
          frequency: null,
          agreement: 'registered',
        },
        {
          type: 'bonus',
          amount: 60000.0,
          frequency: 'yearly',
          agreement: null,
        },
        {
          type: 'incentive',
          amount: 5000.0,
          frequency: 'monthly',
          agreement: null,
        },
      ],
    },
    cibil: {
      score: 771,
      enquiries: {
        d30: 0,
        d60: 1,
        d90: 1,
        d120: 2,
      },
      tradelines: [
        {
          loan_type: 'car',
          lender: 'Mulshi Auto Finance Ltd (sample)',
          sanction_amount: 450000.0,
          outstanding: 176000.0,
          emi: 8200.0,
          status: 'active',
          account_number: 'XXXX4410',
          overdue: 0.0,
          emis_paid: 30,
          emis_pending: 18,
          open_date: '2024-03-05',
          last_payment_date: '2026-09-05',
          action: 'obligate',
          source: 'credit_report',
        },
        {
          loan_type: 'credit_card',
          lender: 'Sample Bank Card (sample)',
          sanction_amount: 150000.0,
          outstanding: 18000.0,
          emi: null,
          status: 'active',
          account_number: 'XXXX9921',
          overdue: null,
          emis_paid: null,
          emis_pending: null,
          open_date: null,
          last_payment_date: null,
          action: 'obligate',
          source: 'credit_report',
        },
        {
          loan_type: 'consumer',
          lender: 'Deccan Consumer Finance (sample)',
          sanction_amount: 45000.0,
          outstanding: 0.0,
          emi: 0.0,
          status: 'closed',
          account_number: 'XXXX1188',
          overdue: null,
          emis_paid: null,
          emis_pending: null,
          open_date: null,
          last_payment_date: null,
          action: 'close',
          source: 'credit_report',
        },
      ],
      source: 'credit_report',
      report_date: '2026-09-20',
    },
    loan: {
      amount: null,
      tenure_months: 48,
    },
  },
  from_documents: [
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
    'tenure_months',
    'score',
    'enquiries',
    'tradelines',
  ],
  prefill: {
    available: true,
    detail: 'Pre-filled from the file check (9 documents)',
    applicant_name: 'Rahul Vijay Deshmukh',
    pan_masked: 'XXXXXX821K',
    employer: 'Konkan Softworks Pvt Ltd',
    verified_net_income: 82500.0,
    income_source: 'verified: salary slips, median net pay',
    dob: '1992-02-14',
    suggested_tradelines: 0,
    credit_report: '09_sample_credit_report.pdf',
    documents: 9,
  },
  sources: {
    name: {
      source: 'document',
      value: 'Rahul Vijay Deshmukh',
      documents: [
        {
          document_id: 'r-02',
          file: '02_identity_details_self_declaration.pdf',
          page: null,
          doc_type: 'identity_details',
        },
      ],
      detail: null,
      unverified: false,
    },
    pan: {
      source: 'document',
      value: 'XXXXXX821K',
      documents: [
        {
          document_id: 'r-02',
          file: '02_identity_details_self_declaration.pdf',
          page: null,
          doc_type: 'identity_details',
        },
      ],
      detail: null,
      unverified: false,
    },
    mobile: {
      source: 'document',
      value: '9000000101',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: 1,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    house_ownership: {
      source: 'document',
      value: 'rented',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: 1,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    dob: {
      source: 'document',
      value: '1992-02-14',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: 1,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    current_address: {
      source: 'document',
      value:
        'Flat 12, Sample Residency, Pokhran Road, Thane West, Thane 400606',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: null,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    pincode: {
      source: 'document',
      value: '400606',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: 1,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    permanent_address: {
      source: 'document',
      value:
        'Flat 12, Sample Residency, Pokhran Road, Thane West, Thane 400606',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: null,
          doc_type: 'loan_application',
        },
      ],
      detail: 'the form says: same as the current address',
      unverified: false,
    },
    company: {
      source: 'document',
      value: 'Konkan Softworks Pvt Ltd',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: 1,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    employment_type: {
      source: 'document',
      value: 'private_limited',
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: null,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    net_income: {
      source: 'document',
      value: 82500.0,
      documents: [
        {
          document_id: 'r-03',
          file: '03_salary_slip_2026-06.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
        {
          document_id: 'r-04',
          file: '04_salary_slip_2026-07.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
        {
          document_id: 'r-05',
          file: '05_salary_slip_2026-08.pdf',
          page: null,
          doc_type: 'salary_slip',
        },
      ],
      detail: 'verified: salary slips, median net pay',
      unverified: false,
    },
    tenure_months: {
      source: 'document',
      value: 48,
      documents: [
        {
          document_id: 'r-01',
          file: '01_loan_application_form.pdf',
          page: null,
          doc_type: 'loan_application',
        },
      ],
      detail: null,
      unverified: false,
    },
    score: {
      source: 'document',
      value: 771,
      documents: [
        {
          document_id: 'r-09',
          file: '09_sample_credit_report.pdf',
          page: 1,
          doc_type: 'credit_report',
        },
      ],
      detail: null,
      unverified: false,
    },
    enquiries: {
      source: 'document',
      value: {
        d30: 0,
        d60: 1,
        d90: 1,
        d120: 2,
      },
      documents: [
        {
          document_id: 'r-09',
          file: '09_sample_credit_report.pdf',
          page: 1,
          doc_type: 'credit_report',
        },
      ],
      detail: null,
      unverified: false,
    },
    report: {
      source: 'document',
      value: '2026-09-20',
      documents: [
        {
          document_id: 'r-09',
          file: '09_sample_credit_report.pdf',
          page: 1,
          doc_type: 'credit_report',
        },
      ],
      detail: 'CIBIL report',
      unverified: false,
    },
  },
  still_needed: [
    {
      field: 'loan_amount',
      label: 'Loan amount',
      required: false,
      from_documents: false,
    },
    {
      field: 'tradelines.2.emi',
      label: 'EMI of loan 2 (Sample Bank Card (sample))',
      required: true,
      from_documents: false,
    },
  ],
  created_at: null,
  updated_at: null,
  expires_at: null,
  notes: [
    '07_form16_itr_summary_FY2025-26.pdf shows other income of ₹24,000 a year: add it as an income row if a lender counts it',
    'CIBIL block read from the credit report 09_sample_credit_report.pdf (CIBIL, report of 2026-09-20): active loans are marked Obligate and closed ones Close',
    '1 loan EMI(s) of the bank statement are on the credit report: not added twice',
  ],
  label: 'sample policy — replace with your lender grid',
  row_sources: FULL_DRAFT_ROWS,
  document_rows: FULL_DRAFT_ROWS,
};

/** The calculation's file check of a NOT READY file (the demo's Sneha: 5 open issues). */
export const NOT_READY_FILE_CHECK = {
  used: true,
  detail: 'verified figures from the file check',
  applicant: 'Sneha Anil Kulkarni',
  verdict: 'NOT READY',
  ready: false,
  issues: [
    "MISSING – Last 3 months' salary slips: missing Jun 2026 slip(s); found: Jul 2026 (03_salary_slip_2026-07_jul.pdf), Aug 2026 (04_salary_slip_2026-08_aug.pdf)",
    "MISSING – Last 6 months' bank statement: covers 3 of 6 months (Jun 2026 – Aug 2026); missing Mar 2026, Apr 2026, May 2026 [05_bank_statement_2026-06_to_2026-08.pdf]",
    'MISSING – Form-16 / ITR (latest FY): not found in the file',
    'MISMATCH – Declared net salary vs salary slips: declared ₹65,000 [01_loan_application_form.pdf] vs slip net ₹58,000 [03_salary_slip_2026-07_jul.pdf, 04_salary_slip_2026-08_aug.pdf] – 10.8% apart; declared amount equals slip GROSS ₹65,000 → gross appears declared as net',
    'MISMATCH – Declared net salary vs bank credits: declared ₹65,000 [01_loan_application_form.pdf] vs bank salary credits ₹58,000 (3 credits on 2026-06-01, 2026-07-01, 2026-08-01) [05_bank_statement_2026-06_to_2026-08.pdf] – ₹7,000/month, 10.8% apart',
  ],
};

/** The worked example for a file the file check finds NOT READY. */
export const NOT_READY_RESULT_RESPONSE = {
  ...WORKED_EXAMPLE_RESPONSE,
  file_check: NOT_READY_FILE_CHECK,
};
