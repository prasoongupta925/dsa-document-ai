// Synthetic file-check responses with confirmed needs-review items and call
// recordings, as the engine reports them (packages/lambda/file-check-mcp/
// engine.run_file_check on test_filecheck_engine's rahul() under Smart
// Solutions PL salaried, with the address proof and the PAN format
// confirmed, and the DOB cross-check confirmed before a document left the
// file). Obligations, income and most consistency rows are left out.
import type { FileCheckItemRow, FileCheckResult } from '../../types/fileCheck';
import type { FileCheckResultWithRecordings } from './CallProjectNote';
import type { ConfirmableItemRow } from './confirmations';

const REVIEW_DETAIL = 'cannot be verified automatically; review manually';
const STALE_DETAIL = `${REVIEW_DETAIL}; confirmed by asha.verma on 30 Sep 2026, 16:30 IST, but a document it was made on is no longer in this file: confirm again`;

const BRAND_ROWS: ConfirmableItemRow[] = [
  {
    item_id: 'vlp_pl_01',
    item: 'Identity Proof (Aadhar/PAN)',
    required: true,
    status: 'PRESENT',
    ok: true,
    detail: '02_identity_details_self_declaration.pdf',
    documents: ['02_identity_details_self_declaration.pdf'],
  },
  {
    item_id: 'vlp_pl_02',
    item: 'Address Proof (Passport/Utility Bill)',
    required: true,
    status: 'CONFIRMED',
    ok: true,
    detail: 'confirmed by asha.verma on 01 Oct 2026, 14:05 IST',
    documents: [],
    confirmation: {
      confirmed_by: 'asha.verma',
      confirmed_at: '2026-10-01T08:35:00.000000+00:00',
    },
  },
  {
    item_id: 'vlp_pl_e01',
    item: 'Eligibility: Salaried individuals with minimum income of ₹25,000/month',
    required: true,
    status: 'REVIEW',
    ok: null,
    detail: REVIEW_DETAIL,
    documents: [],
  },
  {
    item_id: 'vlp_pl_e03',
    item: 'Eligibility: Credit score of 750+',
    required: false,
    status: 'REVIEW',
    ok: null,
    detail: REVIEW_DETAIL,
    documents: [],
  },
  {
    item_id: 'x02',
    item: 'Cross-check: PAN format',
    required: true,
    status: 'CONFIRMED',
    ok: true,
    detail: 'confirmed by rohan.iyer on 01 Oct 2026, 14:06 IST',
    documents: [],
    confirmation: {
      confirmed_by: 'rohan.iyer',
      confirmed_at: '2026-10-01T08:36:10.000000+00:00',
    },
  },
  {
    item_id: 'x05',
    item: 'Cross-check: DOB match and age',
    required: true,
    status: 'REVIEW',
    ok: null,
    detail: STALE_DETAIL,
    documents: [],
    stale_confirmation: {
      confirmed_by: 'asha.verma',
      confirmed_at: '2026-09-30T11:00:00.000000+00:00',
    },
  },
];

export const BRAND_CONFIRMED_RESULT: FileCheckResult = {
  project_id: 'proj_demo',
  engine_version: '1.0',
  as_of: '2026-10-01',
  checklist: {
    id: 'vlp_pl_sal',
    name: 'Varunika Loan Partners - Personal Loan - Salaried',
  },
  overall_verdict: 'NOT READY',
  summary:
    '1 applicant: Rahul Vijay Deshmukh NOT READY (3 issues, 2 confirmed)',
  applicants: [
    {
      applicant: 'Rahul Vijay Deshmukh',
      pan: 'BQXPD4821K',
      verdict: 'NOT READY',
      reference_month: '2026-08',
      reference_month_label: 'Aug 2026',
      documents: [
        {
          document_id: 'r-01',
          document_name: '01_loan_application_form.pdf',
          doc_type: 'loan_application',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 'r-02',
          document_name: '02_identity_details_self_declaration.pdf',
          doc_type: 'identity_details',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 'r-06',
          document_name: '06_bank_statement_2026-03_to_2026-08.pdf',
          doc_type: 'bank_statement',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
      ],
      // The shared row type predates CONFIRMED (see ./confirmations).
      checklist: BRAND_ROWS as unknown as FileCheckItemRow[],
      consistency: [
        {
          check_id: 'pan',
          check: 'PAN',
          status: 'OK',
          detail: 'BQXPD4821K on all 5 documents that carry a PAN',
          documents: ['01_loan_application_form.pdf'],
        },
      ],
      income: { bank_credits: [] },
      reasons: [
        `REVIEW – Eligibility: Salaried individuals with minimum income of ₹25,000/month: ${REVIEW_DETAIL}`,
        `REVIEW – Cross-check: DOB match and age: ${STALE_DETAIL}`,
      ],
      missing_items: [],
      mismatches: [],
      needs_review: [],
      manual_review: [
        'Eligibility: Salaried individuals with minimum income of ₹25,000/month',
        'Eligibility: Credit score of 750+',
        'Cross-check: DOB match and age',
      ],
    },
  ],
  pending_documents: [],
  failed_documents: [],
  no_facts_documents: [],
  unsupported_documents: [],
  unassigned_documents: [],
};

/** "Telecaller QA – Sample calls": one transcribed Hinglish call, nothing else. */
export const CALL_PROJECT_RESULT: FileCheckResultWithRecordings = {
  project_id: 'proj_calls',
  engine_version: '1.0',
  as_of: '2026-10-01',
  checklist: {
    id: 'salaried_personal_loan',
    name: 'Personal Loan - Salaried',
  },
  overall_verdict: 'NOT READY',
  summary:
    'No loan documents in this project, only 1 call recording: the file check applies to loan files; review calls with Call QA',
  applicants: [],
  pending_documents: [],
  failed_documents: [],
  no_facts_documents: [],
  unsupported_documents: [],
  unassigned_documents: [],
  recording_documents: [
    {
      document_id: 'c-01',
      document_name: 'SAMPLE_call_varunika_to_sneha_kulkarni_2026-09-30.wav',
      file_type: 'audio/wav',
    },
  ],
};
