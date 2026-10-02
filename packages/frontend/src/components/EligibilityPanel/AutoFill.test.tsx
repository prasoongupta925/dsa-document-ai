// @vitest-environment node
// (Rendered to static markup like EligibilityPanel.test.tsx: the workspace's
// jsdom install cannot start.)
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import ProfileSection from './ProfileSection';
import CibilSection from './CibilSection';
import LendersSection from './LendersSection';
import {
  FULL_DRAFT_RESPONSE,
  LOGIN_NOTIFIED,
  NOT_READY_RESULT_RESPONSE,
  SAVED_INPUTS_RESPONSE,
  WORKED_EXAMPLE_RESPONSE,
} from './fixtures';
import {
  normalizeInputs,
  parseCalculateResponse,
  parseInputsResponse,
  parseLoginResponse,
  setCibil,
  setProfile,
} from '../../lib/eligibility';
import { ApiError } from '../../lib/apiError';
import {
  confirmsNotReady,
  newDraft,
  prefillValuesOf,
  type LenderLoginState,
} from '../../hooks/useEligibility';
import type { EligibilityInputs } from '../../types/eligibility';

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

function render(node: ReactNode): string {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>{node}</I18nextProvider>,
  );
}

/** Text of an HTML fragment, tags stripped and entities decoded. */
function plain(html: string): string {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

/** The markup from a test id to the end of its element's block (rough, enough for text checks). */
function from(html: string, testId: string): string {
  const at = html.indexOf(`data-testid="${testId}"`);
  if (at < 0) throw new Error(`${testId} not rendered`);
  return html.slice(html.lastIndexOf('<', at));
}

const noop = () => undefined;
const RAHUL_PAN = 'BQXPD4821K';
const SNEHA = 'Sneha Anil Kulkarni';
const DRAFT = parseInputsResponse(FULL_DRAFT_RESPONSE, RAHUL_PAN);
const PREFILL = prefillValuesOf(
  DRAFT.inputs,
  DRAFT.fromDocuments,
  DRAFT.sources,
  DRAFT.documentRows,
);

function profile(inputs: EligibilityInputs = DRAFT.inputs, prefill = PREFILL) {
  return render(
    <ProfileSection
      inputs={inputs}
      onEdit={noop}
      prefill={prefill}
      incomeSource={DRAFT.prefill?.income_source}
    />,
  );
}

describe('Profile filled from the documents', () => {
  it('marks every field a document filled, with its file and page', () => {
    const html = profile();
    // PAN, name, mobile, DOB, house ownership, pincode, both addresses,
    // company, employment type, net income, tenure and the 3 income rows.
    expect(html.match(/data-testid="from-documents"/g)).toHaveLength(15);
    const text = plain(html);
    expect(text).toContain('from 01_loan_application_form.pdf, page 1');
    expect(text).toContain(
      'from 01_loan_application_form.pdf · the form says: same as the current address',
    );
    // The net income: how it was verified (the hint) and its files (the note).
    expect(text).toContain(
      'From the documents (verified: salary slips, median net pay).',
    );
    expect(text).toContain(
      'from 03_salary_slip_2026-06.pdf; 04_salary_slip_2026-07.pdf (+1 more)',
    );
    // Other income rows: their documents and how they were counted.
    expect(text).toContain('from 08_rent_agreement_flat_12.pdf');
    expect(text).toContain(
      'Bonus on 1 of 3 salary slips: counted as yearly (the lowest)',
    );
    expect(html).toContain('value="9000000101"');
    expect(html).toContain('value="82,500"');
  });

  it('shows the sheet’s colour legend with From Document', () => {
    const legend = plain(from(profile(), 'source-legend'));
    expect(legend).toContain(
      'Value sources: From Policy Formula Calculation From Table From Document No colour: typed by you.',
    );
    for (const kind of ['policy', 'formula', 'table', 'document']) {
      expect(profile()).toContain(`data-testid="legend-${kind}"`);
    }
  });

  it('lists exactly what no document filled before Check eligibility', () => {
    const html = profile();
    const needed = from(html, 'still-needed');
    expect(needed).toContain('data-count="2"');
    expect(plain(needed)).toMatch(
      /^Still needed before Check eligibility \(2\) Every lender needs: EMI of loan 2 \(Sample Bank Card \(sample\)\) \(CIBIL\) Also empty: Loan amount/,
    );
    // Nothing to fill from the documents: they gave all they hold.
    expect(html).not.toContain('data-testid="fill-from-documents"');
  });

  it('keeps a typed value and shows the documents’ one next to it', () => {
    const typed = setProfile(DRAFT.inputs, { mobile: '9820012345' });
    const html = profile(typed);
    expect(html.match(/data-testid="from-documents"/g)).toHaveLength(14);
    expect(html).toContain('value="9820012345"');
    expect(plain(from(html, 'document-value'))).toMatch(
      /^The documents give 9000000101 \(01_loan_application_form\.pdf, page 1\)\. Use/,
    );
  });

  it('offers the documents’ values for saved inputs typed by hand', () => {
    // Saved by hand before the documents were read: no pincode or mobile, a
    // typed net income, no other income.
    const saved = setProfile(DRAFT.inputs, {
      pincode: null,
      mobile: null,
      net_income: 98000,
      other_income: [],
    });
    const html = profile(saved);
    const needed = plain(from(html, 'still-needed'));
    expect(needed).toContain(
      'Still needed before Check eligibility (4) Fill 2 empty fields from the documents',
    );
    expect(needed).toContain('Every lender needs: Pincode in the documents');
    expect(needed).toContain('Also empty: Mobile number in the documents');
    expect(html).toContain('data-testid="fill-from-documents"');
    // A typed value that differs: shown next to the field, not put in.
    expect(html).toContain('value="98,000"');
    expect(plain(html)).toContain(
      'The documents give ₹82,500 (03_salary_slip_2026-06.pdf (+2 more)). Use',
    );
    // The documents' income rows the saved inputs do not hold, to add.
    const rows = plain(from(html, 'document-rows'));
    expect(rows).toContain('In the documents, not in this form:');
    expect(rows).toContain('Rented income · ₹12,000 · Registered');
    expect(rows).toContain('Incentive · ₹5,000 · Monthly');
    expect(rows.match(/Add/g)).toHaveLength(3);
  });

  it('works with an API that gives no sources (badges without files)', () => {
    const inputs = normalizeInputs(FULL_DRAFT_RESPONSE.inputs);
    const prefill = prefillValuesOf(inputs, ['name', 'net_income']);
    const html = profile(inputs, prefill);
    expect(html.match(/data-testid="from-documents"/g)).toHaveLength(2);
    expect(html).not.toContain('data-testid="source-note"');
  });
});

describe('CIBIL block from the credit report', () => {
  const cibil = DRAFT.inputs.cibil;

  it('says which report filled it and marks each loan', () => {
    const html = render(<CibilSection cibil={cibil} onEdit={noop} />);
    expect(plain(from(html, 'bureau-note'))).toMatch(
      /^Read from 09_sample_credit_report\.pdf: CIBIL report of 2026-09-20\. Active loans are marked Obligate and closed ones Close: check each loan\./,
    );
    expect(html).toContain('value="771"');
    expect(plain(html)).toContain('from 09_sample_credit_report.pdf, page 1');
    const rows = html.split('data-testid="tradeline"').slice(1);
    expect(rows).toHaveLength(3);
    for (const row of rows) expect(row).toContain('data-source="document"');
    expect(plain(rows[0])).toContain(
      'from 09_sample_credit_report.pdf, page 2',
    );
    expect(plain(rows[2])).toContain(
      'from 09_sample_credit_report.pdf, page 3',
    );
    expect(rows.map((r) => /data-action="(\w+)"/.exec(r)?.[1])).toEqual([
      'obligate',
      'obligate',
      'close',
    ]);
    // Only the last 4 of an account number.
    expect(html).not.toMatch(/\d{5,}4410/);
    // The CIBIL tab lists its own part of what is still needed.
    expect(plain(from(html, 'still-needed'))).toContain(
      'Still needed before Check eligibility (1) Every lender needs: EMI of loan 2 (Sample Bank Card (sample))',
    );
  });

  it('warns of a value not found in the document’s text', () => {
    const score = cibil.sources?.score;
    if (!score) throw new Error('no score source');
    const html = render(
      <CibilSection
        cibil={{
          ...cibil,
          sources: { ...cibil.sources, score: { ...score, unverified: true } },
        }}
        onEdit={noop}
      />,
    );
    expect(plain(html)).toContain(
      "from 09_sample_credit_report.pdf, page 1 Not found in the document's text: check it.",
    );
  });

  it('marks a bank-statement EMI as a suggestion', () => {
    const suggested = parseInputsResponse(
      {
        ...FULL_DRAFT_RESPONSE,
        inputs: {
          ...FULL_DRAFT_RESPONSE.inputs,
          cibil: {
            score: null,
            enquiries: { d30: null, d60: null, d90: null, d120: null },
            tradelines: [
              {
                loan_type: 'personal',
                lender: 'Sample Finance',
                emi: 4100,
                status: 'active',
                action: 'obligate',
                source: 'bank_statement',
              },
            ],
          },
        },
        row_sources: {
          other_income: [],
          tradelines: [
            {
              source: 'document',
              value: {
                loan_type: 'personal',
                lender: 'Sample Finance',
                emi: 4100,
                status: 'active',
                action: 'obligate',
                source: 'bank_statement',
              },
              documents: [
                {
                  document_id: 'r-06',
                  file: '06_bank_statement.pdf',
                  page: null,
                  doc_type: 'bank_statement',
                },
              ],
              detail: 'loan EMI in 6 of 6 months of the bank statement',
              unverified: false,
            },
          ],
        },
        document_rows: { other_income: [], tradelines: [] },
      },
      RAHUL_PAN,
    );
    const html = render(
      <CibilSection cibil={suggested.inputs.cibil} onEdit={noop} />,
    );
    expect(html).toContain('data-testid="suggested"');
    expect(plain(html)).toContain(
      'from 06_bank_statement.pdf · loan EMI in 6 of 6 months of the bank statement',
    );
  });

  it('offers the report’s values and loans to inputs typed by hand', () => {
    const typed = setCibil(normalizeInputs(SAVED_INPUTS_RESPONSE.inputs), {
      sources: cibil.sources,
    }).cibil;
    const html = render(<CibilSection cibil={typed} onEdit={noop} />);
    expect(plain(from(html, 'bureau-note'))).toMatch(
      /^Entered by hand\. The documents hold a credit report \(09_sample_credit_report\.pdf\)/,
    );
    expect(plain(html)).toContain(
      'The documents give 771 (09_sample_credit_report.pdf, page 1). Use',
    );
    expect(plain(html)).toContain(
      'The documents give 0 / 1 / 1 / 2 in 30 / 60 / 90 / 120 days',
    );
    const rows = plain(from(html, 'document-rows'));
    expect(rows).toContain(
      'Car loan · Mulshi Auto Finance Ltd (sample) · EMI ₹8,200',
    );
    expect(rows.match(/Add/g)).toHaveLength(3);
  });
});

describe('Log in with lender on a NOT READY file', () => {
  const notReady = parseCalculateResponse(NOT_READY_RESULT_RESPONSE, SNEHA);
  const ready = parseCalculateResponse(
    {
      ...WORKED_EXAMPLE_RESPONSE,
      file_check: {
        used: true,
        detail: 'verified figures from the file check',
        verdict: 'READY',
        ready: true,
        issues: [],
      },
    },
    SNEHA,
  );

  function lenders(
    extra: Partial<Parameters<typeof LendersSection>[0]> = {},
  ): string {
    return render(
      <LendersSection
        applicantName={SNEHA}
        result={notReady}
        stale={false}
        calculating={false}
        calcError={null}
        onCalculate={noop}
        onLogin={noop}
        {...extra}
      />,
    );
  }

  it('says the file is NOT READY above the lenders', () => {
    expect(plain(from(lenders(), 'file-check-verdict'))).toMatch(
      /^File check: NOT READY \(5 open issues\)\. A login asks you to confirm them first\./,
    );
    expect(
      plain(from(lenders({ result: ready }), 'file-check-verdict')),
    ).toMatch(/^File check: READY\./);
  });

  it('asks to confirm the open issues by name before logging in', () => {
    const html = lenders({ initialConfirming: ['ICICI Bank'] });
    const confirm = plain(from(html, 'confirm-not-ready'));
    expect(confirm).toContain('This file is NOT READY: 5 open issues');
    expect(confirm).toContain(
      "MISSING – Last 3 months' salary slips: missing Jun 2026 slip(s)",
    );
    expect(confirm).toContain('MISMATCH – Declared net salary vs bank credits');
    expect(confirm).toContain(
      `Log ${SNEHA} in with ICICI Bank anyway? Your CRM webhook is told the file is NOT READY.`,
    );
    expect(confirm).toContain('Log in anyway');
    // A READY file: the usual confirmation, no issues.
    const plainConfirm = lenders({
      result: ready,
      initialConfirming: ['ICICI Bank'],
    });
    expect(plainConfirm).not.toContain('data-testid="confirm-not-ready"');
    expect(plain(from(plainConfirm, 'confirm'))).toContain(
      `Log ${SNEHA} in with ICICI Bank? Your CRM webhook is notified.`,
    );
  });

  it('asks again when the file check turned NOT READY after the result', () => {
    const login: LenderLoginState = {
      sending: false,
      error: new ApiError(
        428,
        'The file check is NOT READY (5 open issues): MISSING – Form-16 / ITR (latest FY): not found in the file. Confirm to log it in anyway (confirm_not_ready)',
      ),
      response: null,
      at: null,
    };
    const html = lenders({ result: ready, logins: { 'ICICI Bank': login } });
    const text = plain(from(html, 'login-not-ready'));
    expect(text).toContain(
      'The file check is NOT READY now: The file check is NOT READY (5 open issues): MISSING – Form-16 / ITR',
    );
    expect(html).toContain('data-testid="confirm-login-not-ready"');
  });

  it('says a login was made NOT READY', () => {
    const login: LenderLoginState = {
      sending: false,
      error: null,
      response: parseLoginResponse({
        ...LOGIN_NOTIFIED,
        file_ready: false,
        open_issues: 5,
      }),
      at: new Date('2026-10-02T06:10:00Z'),
    };
    const html = lenders({ logins: { 'ICICI Bank': login } });
    expect(plain(from(html, 'logged-in-not-ready'))).toMatch(
      /^Logged in NOT READY \(5 open issues confirmed\)\./,
    );
  });

  it('sends confirm_not_ready only after a NOT READY result or refusal', () => {
    const draft = (patch: Partial<ReturnType<typeof newDraft>>) => ({
      ...newDraft(SNEHA),
      ...patch,
    });
    expect(confirmsNotReady(undefined, 'ICICI Bank')).toBe(false);
    expect(confirmsNotReady(draft({ result: ready }), 'ICICI Bank')).toBe(
      false,
    );
    expect(confirmsNotReady(draft({ result: notReady }), 'ICICI Bank')).toBe(
      true,
    );
    const refused = (status: number) =>
      draft({
        result: ready,
        logins: {
          'ICICI Bank': {
            sending: false,
            error: new ApiError(status, 'x'),
            response: null,
            at: null,
          },
        },
      });
    expect(confirmsNotReady(refused(428), 'ICICI Bank')).toBe(true);
    expect(confirmsNotReady(refused(428), 'HDFC Bank')).toBe(false);
    expect(confirmsNotReady(refused(409), 'ICICI Bank')).toBe(false);
  });

  it('colours each value by its source, as the sheet does', () => {
    const html = lenders();
    expect(plain(from(html, 'source-legend'))).toContain(
      'From Policy Formula Calculation From Table From Document',
    );
    // Column underlines: formula (yellow), policy (grey), table (green).
    expect(html).toMatch(/data-source="formula"[^>]*>Eligible amount</);
    expect(html).toMatch(/data-source="policy"[^>]*>ROI</);
    expect(html).toMatch(/data-source="table"[^>]*>BT amount</);
    // The best lender's details: each parameter's source in its colour.
    expect(html).toMatch(/data-source="policy">From Policy · CAT A</);
    expect(html).toMatch(/data-source="formula">Formula Calculation</);
    expect(html).toMatch(/data-source="table">From Table</);
  });
});
