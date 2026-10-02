// Synthetic file-check responses (shape of engine.run_file_check) for tests.
import type { FileCheckResult } from '../../types/fileCheck';

export const NOT_READY_RESULT: FileCheckResult = {
  project_id: 'proj_demo',
  engine_version: '1.0',
  as_of: '2026-09-28',
  checklist: {
    id: 'salaried_personal_loan',
    name: 'Personal Loan - Salaried',
  },
  overall_verdict: 'NOT READY',
  summary: '1 applicant: Amit Suresh Patil NOT READY (3 issues)',
  applicants: [
    {
      applicant: 'Amit Suresh Patil',
      pan: 'ABCPP1234K',
      verdict: 'NOT READY',
      reference_month: '2026-08',
      reference_month_label: 'Aug 2026',
      documents: [
        {
          document_id: 'd1',
          document_name: 'application.pdf',
          doc_type: 'loan_application',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 'd2',
          document_name: 'slip_aug.pdf',
          doc_type: 'salary_slip',
          grounded: false,
          grounding_notes: [],
          unverified_fields: ['net_salary'],
        },
      ],
      checklist: [
        {
          item_id: 'application',
          item: 'Loan application form',
          required: true,
          status: 'PRESENT',
          ok: true,
          detail: 'application.pdf',
          documents: ['application.pdf'],
        },
        {
          item_id: 'salary_slips',
          item: 'Salary slips (last 3 months)',
          required: true,
          status: 'MISSING',
          ok: false,
          detail:
            'missing Jun 2026, Jul 2026 slip(s); found: Aug 2026 (slip_aug.pdf)',
          documents: ['slip_aug.pdf'],
          required_months: ['2026-06', '2026-07', '2026-08'],
          missing_months: ['2026-06', '2026-07'],
        },
        {
          item_id: 'address_proof',
          item: 'Address proof',
          required: true,
          status: 'REVIEW',
          ok: null,
          detail: 'cannot be verified automatically; review manually',
          documents: [],
        },
        {
          item_id: 'form16',
          item: 'Form-16 / ITR',
          required: false,
          status: 'MISSING',
          ok: false,
          detail: 'not found in the file',
          documents: [],
        },
      ],
      consistency: [
        {
          check_id: 'pan',
          check: 'PAN',
          status: 'OK',
          detail: 'ABCPP1234K on all 2 documents that carry a PAN',
          documents: ['application.pdf', 'slip_aug.pdf'],
        },
        {
          check_id: 'declared_vs_slip_net',
          check: 'Declared net salary vs salary slips',
          status: 'MISMATCH',
          detail:
            'declared ₹72,000 [application.pdf] vs slip net ₹55,000 [slip_aug.pdf] – 23.6% apart',
          documents: ['application.pdf', 'slip_aug.pdf'],
        },
      ],
      income: {
        declared_net: 72000,
        slip_net: 55000,
        slip_gross: 72000,
        bank_salary_credit: null,
        form16_gross: null,
        slip_gross_x12: 864000,
        bank_credits: [],
      },
      reasons: [
        'MISSING – Salary slips (last 3 months): missing Jun 2026, Jul 2026 slip(s); found: Aug 2026 (slip_aug.pdf)',
        'REVIEW – Address proof: cannot be verified automatically; review manually',
        'MISMATCH – Declared net salary vs salary slips: declared ₹72,000 [application.pdf] vs slip net ₹55,000 [slip_aug.pdf] – 23.6% apart',
      ],
      missing_items: ['Salary slips: Jun 2026, Jul 2026'],
      mismatches: [
        'Declared net salary vs salary slips: declared ₹72,000 [application.pdf] vs slip net ₹55,000 [slip_aug.pdf] – 23.6% apart',
      ],
      manual_review: ['Address proof'],
    },
  ],
  pending_documents: [],
  failed_documents: [],
  no_facts_documents: [],
  unsupported_documents: [
    {
      document_id: 'd9',
      document_name: 'statement.xlsx',
      file_type:
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      reason:
        'spreadsheet documents are not read by the file check; upload the statement as PDF',
    },
  ],
  unassigned_documents: [],
};

/**
 * Demo file B (Sneha Anil Kulkarni) as the engine reports it
 * (test_filecheck_engine.test_sneha_not_ready): the June 2026 salary slip,
 * three months of bank statement (Mar–May 2026) and the Form-16 / ITR are
 * missing, and the declared salary does not match the slips or the bank.
 */
export const SNEHA_NOT_READY_RESULT: FileCheckResult = {
  project_id: 'proj_demo',
  engine_version: '1.0',
  as_of: '2026-09-28',
  checklist: {
    id: 'salaried_personal_loan',
    name: 'Personal Loan - Salaried',
  },
  overall_verdict: 'NOT READY',
  summary: '1 applicant: Sneha Anil Kulkarni NOT READY (5 issues)',
  applicants: [
    {
      applicant: 'Sneha Anil Kulkarni',
      pan: 'CKRPK7314M',
      verdict: 'NOT READY',
      reference_month: '2026-08',
      reference_month_label: 'Aug 2026',
      documents: [
        {
          document_id: 's1',
          document_name: '01_loan_application_form.pdf',
          doc_type: 'loan_application',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 's2',
          document_name: '02_identity_details_self_declaration.pdf',
          doc_type: 'identity_details',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 's3',
          document_name: '03_salary_slip_2026-07_jul.pdf',
          doc_type: 'salary_slip',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 's4',
          document_name: '04_salary_slip_2026-08_aug.pdf',
          doc_type: 'salary_slip',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
        {
          document_id: 's5',
          document_name: '05_bank_statement_2026-06_to_2026-08.pdf',
          doc_type: 'bank_statement',
          grounded: true,
          grounding_notes: [],
          unverified_fields: [],
        },
      ],
      checklist: [
        {
          item_id: 'loan_application',
          item: 'Loan application form',
          required: true,
          status: 'PRESENT',
          ok: true,
          detail: '01_loan_application_form.pdf',
          documents: ['01_loan_application_form.pdf'],
        },
        {
          item_id: 'identity_details',
          item: 'Identity details (PAN + masked Aadhaar)',
          required: true,
          status: 'PRESENT',
          ok: true,
          detail: '02_identity_details_self_declaration.pdf',
          documents: ['02_identity_details_self_declaration.pdf'],
        },
        {
          item_id: 'salary_slips',
          item: "Last 3 months' salary slips",
          required: true,
          status: 'MISSING',
          ok: false,
          detail:
            'missing Jun 2026 slip(s); found: Jul 2026 (03_salary_slip_2026-07_jul.pdf), Aug 2026 (04_salary_slip_2026-08_aug.pdf)',
          documents: [
            '03_salary_slip_2026-07_jul.pdf',
            '04_salary_slip_2026-08_aug.pdf',
          ],
          required_months: ['2026-06', '2026-07', '2026-08'],
          missing_months: ['2026-06'],
        },
        {
          item_id: 'bank_statement',
          item: "Last 6 months' bank statement",
          required: true,
          status: 'MISSING',
          ok: false,
          detail:
            'covers Jun 2026 – Aug 2026 (05_bank_statement_2026-06_to_2026-08.pdf); missing Mar 2026, Apr 2026, May 2026',
          documents: ['05_bank_statement_2026-06_to_2026-08.pdf'],
          required_months: [
            '2026-03',
            '2026-04',
            '2026-05',
            '2026-06',
            '2026-07',
            '2026-08',
          ],
          missing_months: ['2026-03', '2026-04', '2026-05'],
        },
        {
          item_id: 'form16_itr',
          item: 'Form-16 / ITR (latest FY)',
          required: true,
          status: 'MISSING',
          ok: false,
          detail: 'no Form-16 / ITR in the file',
          documents: [],
        },
      ],
      consistency: [
        {
          check_id: 'pan',
          check: 'PAN',
          status: 'OK',
          detail: 'CKRPK7314M on all 4 documents that carry a PAN',
          documents: [
            '01_loan_application_form.pdf',
            '02_identity_details_self_declaration.pdf',
            '03_salary_slip_2026-07_jul.pdf',
            '04_salary_slip_2026-08_aug.pdf',
          ],
        },
        {
          check_id: 'declared_vs_slip_net',
          check: 'Declared net salary vs salary slips',
          status: 'MISMATCH',
          detail:
            'declared ₹65,000 [01_loan_application_form.pdf] vs slip net ₹58,000 – 12.1% apart; slip gross ₹65,000: gross appears declared as net',
          documents: [
            '01_loan_application_form.pdf',
            '03_salary_slip_2026-07_jul.pdf',
            '04_salary_slip_2026-08_aug.pdf',
          ],
        },
        {
          check_id: 'declared_vs_bank_credits',
          check: 'Declared net salary vs bank credits',
          status: 'MISMATCH',
          detail:
            'declared ₹65,000 vs bank salary credit ₹58,000 [05_bank_statement_2026-06_to_2026-08.pdf] – 12.1% apart',
          documents: [
            '01_loan_application_form.pdf',
            '05_bank_statement_2026-06_to_2026-08.pdf',
          ],
        },
        {
          check_id: 'slip_net_vs_bank_credits',
          check: 'Salary slips vs bank credits',
          status: 'OK',
          detail: 'slip net ₹58,000 = bank credit ₹58,000',
          documents: [
            '03_salary_slip_2026-07_jul.pdf',
            '05_bank_statement_2026-06_to_2026-08.pdf',
          ],
        },
      ],
      income: {
        declared_net: 65000,
        slip_net: 58000,
        slip_gross: 65000,
        bank_salary_credit: 58000,
        form16_gross: null,
        slip_gross_x12: 780000,
        bank_credits: [],
      },
      reasons: [
        "MISSING – Last 3 months' salary slips: missing Jun 2026 slip(s); found: Jul 2026 (03_salary_slip_2026-07_jul.pdf), Aug 2026 (04_salary_slip_2026-08_aug.pdf)",
        "MISSING – Last 6 months' bank statement: covers Jun 2026 – Aug 2026 (05_bank_statement_2026-06_to_2026-08.pdf); missing Mar 2026, Apr 2026, May 2026",
        'MISSING – Form-16 / ITR (latest FY): no Form-16 / ITR in the file',
        'MISMATCH – Declared net salary vs salary slips: declared ₹65,000 [01_loan_application_form.pdf] vs slip net ₹58,000 – 12.1% apart; slip gross ₹65,000: gross appears declared as net',
        'MISMATCH – Declared net salary vs bank credits: declared ₹65,000 vs bank salary credit ₹58,000 [05_bank_statement_2026-06_to_2026-08.pdf] – 12.1% apart',
      ],
      missing_items: [
        'Salary slip: Jun 2026',
        'Bank statement: Mar 2026, Apr 2026, May 2026',
        'Form-16 / ITR (latest FY)',
      ],
      mismatches: [
        'Declared net salary vs salary slips: declared ₹65,000 [01_loan_application_form.pdf] vs slip net ₹58,000 – 12.1% apart; slip gross ₹65,000: gross appears declared as net',
        'Declared net salary vs bank credits: declared ₹65,000 vs bank salary credit ₹58,000 [05_bank_statement_2026-06_to_2026-08.pdf] – 12.1% apart',
      ],
      needs_review: [],
      manual_review: [],
    },
  ],
  pending_documents: [],
  failed_documents: [],
  no_facts_documents: [],
  unsupported_documents: [],
  unassigned_documents: [],
};

/** Demo file C (Amit Suresh Patil): complete, but the PAN differs by one character. */
export const AMIT_PAN_MISMATCH_RESULT: FileCheckResult = {
  ...NOT_READY_RESULT,
  summary: '1 applicant: Amit Suresh Patil NOT READY (1 issue)',
  unsupported_documents: [],
  applicants: [
    {
      ...NOT_READY_RESULT.applicants[0],
      pan: 'DMVPP5928L',
      checklist: NOT_READY_RESULT.applicants[0].checklist.map((row) => ({
        ...row,
        status: row.status === 'REVIEW' ? row.status : ('PRESENT' as const),
        ok: row.status === 'REVIEW' ? null : true,
        missing_months: row.missing_months ? [] : row.missing_months,
      })),
      consistency: [
        {
          check_id: 'pan',
          check: 'PAN',
          status: 'MISMATCH',
          detail:
            'DMVPP5926L on application.pdf; DMVPP5928L on slip_aug.pdf (differs at character 9)',
          documents: ['application.pdf', 'slip_aug.pdf'],
        },
      ],
      reasons: [
        'MISMATCH – PAN: DMVPP5926L on application.pdf; DMVPP5928L on slip_aug.pdf (differs at character 9)',
      ],
      missing_items: [],
      mismatches: [
        'PAN: DMVPP5926L on application.pdf; DMVPP5928L on slip_aug.pdf (differs at character 9)',
      ],
    },
  ],
};

/** NOT_READY_RESULT with the per-document facts extraction cost the backend records. */
export const USAGE_RESULT: FileCheckResult = {
  ...NOT_READY_RESULT,
  applicants: [
    {
      ...NOT_READY_RESULT.applicants[0],
      documents: [
        {
          ...NOT_READY_RESULT.applicants[0].documents[0],
          usage: {
            model_id: 'global.amazon.nova-2-lite-v1:0',
            input_tokens: 12345,
            output_tokens: 678,
            cost_usd: 0.00632085,
          },
        },
        // Analysed before costs were recorded.
        { ...NOT_READY_RESULT.applicants[0].documents[1], usage: null },
      ],
      usage_total: {
        input_tokens: 12345,
        output_tokens: 678,
        cost_usd: 0.00632085,
        documents_with_usage: 1,
        documents_total: 2,
      },
    },
  ],
};

export {
  READY_OBLIGATIONS_RESULT,
  READY_WITH_REVIEW_RESULT,
} from './engineFixtures';
