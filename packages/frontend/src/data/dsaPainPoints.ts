// "Why DSAs need this": the loan-file problems of a DSA that sends files to
// lenders, in a DSA's words, each tied to the feature that addresses it.
// DPDP wording follows the official text: masking is one example safeguard in
// Rule 6 (next to encryption, obfuscation and virtual tokens), Rule 3 is about
// the notice given to the customer, and most Rules (3, 5-16, 22, 23) apply from
// 13 May 2027.

import { normalizeStatus } from '../lib/fileCheck';

/** Where "Show me" takes the viewer inside the File Check panel. */
export type PainPointTarget =
  | 'file-check'
  | 'ask'
  | 'obligations'
  | 'retention';

export type PainPointId =
  | 'incomplete-files'
  | 'manual-verification'
  | 'bank-statement-structuring'
  | 'deciding-not-extracting'
  | 'hours-per-file'
  | 'crm-integration'
  | 'old-files'
  | 'kyc-protection';

export interface DsaPainPoint {
  id: PainPointId;
  /** The pain, in DSA wording. */
  title: string;
  /** Short label for the tags on verdict findings. */
  tag: string;
  /** What it means for a DSA sending files to lenders. */
  forDsa: string;
  /** What this platform does about it. */
  feature: string;
  showMe: PainPointTarget;
  /** What the official text says, said more precisely. */
  correction?: string;
}

export const SERIES_NOTE =
  'Common problems with loan files, in the words of a DSA that sends files to lenders, and what the app does about each. DPDP dates and safeguards follow the official DPDP Rules, 2025.';

export const DSA_PAIN_POINTS: readonly DsaPainPoint[] = [
  {
    id: 'incomplete-files',
    title: 'Files reach the lender incomplete and come back',
    tag: 'Incomplete files',
    forDsa:
      "Check the file before 'send loan login': a missing June slip or a short bank statement is caught here, not by the lender's credit desk, so the telecaller does not have to call the customer again.",
    feature:
      'READY / NOT READY file check against the lender checklist, with the missing items and months named.',
    showMe: 'file-check',
  },
  {
    id: 'manual-verification',
    title: 'Declared salary, PAN and name are cross-checked by eye',
    tag: 'Manual cross-checks',
    forDsa:
      'Declared vs actual salary, PAN, name and employer are compared across the application, salary slips and bank statement, with both numbers and the documents shown, before the lender finds the mismatch.',
    feature:
      'Consistency checks with evidence; anything the rules cannot verify is marked "Needs a person".',
    showMe: 'file-check',
  },
  {
    id: 'bank-statement-structuring',
    title: 'Bank statements are read page by page to find EMIs',
    tag: 'Bank statement EMIs',
    forDsa:
      "Existing EMIs, rent and SIPs come out of the statement with the debit day and the months seen, and declared EMIs are matched to the bank, so you can quote an indicative FOIR and max new EMI before choosing a lender. The lender's policy decides.",
    feature:
      'Obligations & FOIR (indicative): fixed loan EMIs, other fixed debits, totals, FOIR vs the checklist limit and the max new EMI.',
    showMe: 'obligations',
  },
  {
    id: 'deciding-not-extracting',
    title: 'Your team spends its time extracting, not deciding',
    tag: 'Extracting vs deciding',
    forDsa:
      'Ask a plain question about the file and get an answer from its own documents. Fixed rules give the verdict and the lender still makes the credit call; the model only reads.',
    feature:
      'Ask about this file: Amazon Nova 2 Lite answers from the extracted fields, check results and document text only, and says when something is not in the file.',
    showMe: 'ask',
  },
  {
    id: 'hours-per-file',
    title: 'Nobody measures what checking a file costs',
    tag: 'Cost per file',
    forDsa:
      'Every answer shows its tokens and dollar cost, with the spend of the last 7 days. A pilot measures minutes and rupees per file on your own masked files.',
    feature: 'Per-answer token and cost meter, and 7-day spend.',
    showMe: 'ask',
  },
  {
    id: 'crm-integration',
    title: 'The check sits outside your CRM',
    tag: 'CRM integration',
    forDsa:
      "Your CRM can make one signed API call before 'send loan login' and get the same READY / NOT READY verdict you see here, or you download the findings as CSV.",
    feature: 'File-check integration API (IAM-signed) and CSV export.',
    showMe: 'file-check',
  },
  {
    id: 'old-files',
    title: 'Old customer files stay in shared folders and phones for years',
    tag: 'Old KYC copies',
    forDsa:
      'A KYC copy from a file you sent last year is still personal data. Keep customer documents only while the loan file needs them; the lender keeps its own records.',
    feature:
      'Nothing is kept longer than 7 days: documents, extracted facts, chat sessions and artifacts are deleted automatically.',
    showMe: 'retention',
  },
  {
    id: 'kyc-protection',
    title: 'Customer KYC must be protected, and you must be able to show it',
    tag: 'KYC data protection',
    forDsa:
      'A DSA handles Aadhaar, PAN and bank statements for its lenders and must control who can see them. Most DPDP Rules, including the Rule 6 security safeguards, apply from 13 May 2027.',
    feature:
      'The extracted facts keep only the last 4 Aadhaar digits (the uploaded file itself is not redacted yet) and the file-check API is private and IAM-signed. No tool makes a business compliant on its own.',
    showMe: 'file-check',
    correction:
      'Rule 6 lists masking as one example safeguard, next to encryption, obfuscation and virtual tokens, alongside access control and access logs. Rule 3 sets what the notice to the customer must contain. Rules 3, 5-16, 22 and 23 apply from 13 May 2027.',
  },
];

export const PAIN_POINTS_BY_ID: Readonly<Record<PainPointId, DsaPainPoint>> =
  Object.fromEntries(DSA_PAIN_POINTS.map((p) => [p.id, p])) as Record<
    PainPointId,
    DsaPainPoint
  >;

const CONSISTENCY_PAIN_POINTS: Record<string, PainPointId> = {
  pan: 'manual-verification',
  aadhaar_last4: 'manual-verification',
  applicant_name: 'manual-verification',
  employer: 'manual-verification',
  employer_vs_bank_credits: 'manual-verification',
  declared_vs_slip_net: 'manual-verification',
  declared_vs_bank_credits: 'manual-verification',
  slip_net_vs_bank_credits: 'manual-verification',
  form16_vs_slip_gross: 'manual-verification',
  declared_emis_vs_bank_debits: 'bank-statement-structuring',
  foir: 'bank-statement-structuring',
};

/**
 * The card a verdict finding illustrates, or null. Only findings that need
 * action are tagged (a missing required item, a mismatch, a review item), so
 * a clean READY file stays uncluttered.
 */
export function painPointForFinding(
  finding:
    | { kind: 'item'; status: string; required?: boolean | null }
    | { kind: 'consistency'; checkId: string; status: string },
): PainPointId | null {
  const status = normalizeStatus(finding.status);
  if (finding.kind === 'item') {
    if (status === 'MISSING') {
      return finding.required === false ? null : 'incomplete-files';
    }
    if (status === 'REVIEW' || status === 'NEEDS REVIEW') {
      return 'manual-verification';
    }
    return null;
  }
  if (
    status !== 'MISMATCH' &&
    status !== 'REVIEW' &&
    status !== 'NEEDS REVIEW'
  ) {
    return null;
  }
  return CONSISTENCY_PAIN_POINTS[finding.checkId] ?? null;
}
