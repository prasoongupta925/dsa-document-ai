// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import VerdictCard from './VerdictCard';
import {
  NOT_READY_RESULT,
  READY_OBLIGATIONS_RESULT,
  READY_WITH_REVIEW_RESULT,
} from './fixtures';
import type {
  FileCheckApplicant,
  FileCheckResult,
} from '../../types/fileCheck';

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

function renderCard(
  result: FileCheckResult,
  onPainPoint?: (id: string) => void,
): string {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>
      <VerdictCard result={result} onPainPoint={onPainPoint} />
    </I18nextProvider>,
  );
}

/** The result with its single applicant patched. */
function withApplicant(
  result: FileCheckResult,
  patch: Partial<FileCheckApplicant>,
): FileCheckResult {
  return {
    ...result,
    applicants: [{ ...result.applicants[0], ...patch }],
  };
}

/** Markup of the Obligations & FOIR section. */
function obligations(html: string): string {
  const start = html.indexOf('data-focus="obligations"');
  if (start < 0) throw new Error('obligations section not rendered');
  return html.slice(start, html.indexOf('</section>', start));
}

/** A link the UI must never render (built so lint does not flag it). */
const SCRIPT_URL = ['javascript', 'alert(1)'].join(':');

/** Opening tag of the verdict banner. */
function bannerTag(html: string): string {
  const m = /<section aria-label="Verdict"[^>]*>/.exec(html);
  if (!m) throw new Error('verdict banner not rendered');
  return m[0];
}

/** Markup of the banner section (up to its closing tag). */
function banner(html: string): string {
  const start = html.indexOf('<section aria-label="Verdict"');
  return html.slice(start, html.indexOf('</section>', start));
}

/** Markup of the finding row whose label is `label`. */
function row(html: string, label: string): string {
  const found = html
    .split('<li')
    .find((chunk) => chunk.includes(`>${label}</span>`));
  if (!found) throw new Error(`row ${label} not rendered`);
  return found.slice(0, found.indexOf('</li>'));
}

describe('VerdictCard', () => {
  it('renders a NOT READY verdict with per-item findings', () => {
    const html = renderCard(NOT_READY_RESULT);

    expect(bannerTag(html)).toContain('data-verdict="notReady"');
    expect(banner(html)).toContain('>NOT READY<');
    expect(banner(html)).toContain('>Amit Suresh Patil<');
    expect(banner(html)).toContain('Personal Loan - Salaried');

    // Item row: label, the engine's detail line and the missing months.
    const slips = row(html, 'Salary slips (last 3 months)');
    expect(slips).toContain('data-tone="bad"');
    expect(slips).toContain(
      'missing Jun 2026, Jul 2026 slip(s); found: Aug 2026 (slip_aug.pdf)',
    );
    expect(slips).toContain('>Jun 2026<');
    expect(slips).toContain('>Jul 2026<');

    // REVIEW items are flagged for a person.
    const review = row(html, 'Address proof');
    expect(review).toContain('data-tone="review"');
    expect(review).toContain('Needs a person');

    // A missing optional item is muted, not a blocker.
    const optional = row(html, 'Form-16 / ITR');
    expect(optional).toContain('data-tone="muted"');
    expect(optional).toContain('>optional<');

    // Consistency: declared vs actual with both numbers.
    const income = row(html, 'Declared net salary vs salary slips');
    expect(income).toContain('data-tone="bad"');
    expect(income).toContain('₹72,000');
    expect(income).toContain('₹55,000');
    expect(row(html, 'PAN')).toContain('data-tone="ok"');

    // Reasons summary and the document the engine could not read.
    expect(html).toContain('Issues to fix (3)');
    const skipped = row(html, 'statement.xlsx');
    expect(skipped).toContain('Not supported');
    expect(skipped).toContain('upload the statement as PDF');
  });

  it('shows the API verdict verbatim instead of deriving it', () => {
    const applicant = NOT_READY_RESULT.applicants[0];
    // Every row looks fine, but the engine said NOT READY (e.g. a document
    // is still being analysed): the UI must show NOT READY.
    const allPresent = renderCard({
      ...NOT_READY_RESULT,
      overall_verdict: 'NOT READY',
      applicants: [
        {
          ...applicant,
          checklist: [applicant.checklist[0]],
          consistency: [applicant.consistency[0]],
          reasons: [],
        },
      ],
      unsupported_documents: [],
    });
    expect(bannerTag(allPresent)).toContain('data-verdict="notReady"');
    expect(banner(allPresent)).toContain('>NOT READY<');

    // Rows have a MISSING item, but the engine said READY: show READY.
    const html = renderCard({ ...NOT_READY_RESULT, overall_verdict: 'READY' });
    expect(bannerTag(html)).toContain('data-verdict="ready"');
    expect(banner(html)).toContain('>READY<');
    expect(banner(html)).not.toContain('NOT READY');
  });

  it('flags REVIEW findings on a READY verdict without changing it', () => {
    const html = renderCard(READY_WITH_REVIEW_RESULT);

    // The verdict stays the engine's READY; the banner says a person must look.
    expect(bannerTag(html)).toContain('data-verdict="ready"');
    expect(banner(html)).toContain('>READY<');
    expect(banner(html)).toContain('1 finding needs a person');
    expect(banner(html)).toContain('a person should look at the flagged');
    expect(banner(html)).not.toContain('present and consistent');

    // The needs_review list and the REVIEW consistency row.
    expect(html).toContain('Needs a person (1) – does not change the verdict');
    const emi = row(html, 'Declared EMIs vs bank debits');
    expect(emi).toContain('data-tone="review"');
    expect(emi).toContain('Needs a person');
    expect(emi).toContain('undeclared loan debit: NACH debit ₹6,500');

    // FOIR numbers exactly as the engine returned them, labelled indicative.
    expect(html).toContain('FOIR (indicative)');
    expect(html).toContain('₹14,700');
    expect(html).toContain('17.8%');
    expect(html).toContain('Max new EMI at FOIR 70% (indicative)');
    expect(html).toContain('₹43,050');
    expect(html).toContain('Indicative – the lender&#x27;s policy decides.');
  });

  it('shows why FOIR was not computed instead of numbers', () => {
    const html = renderCard(
      withApplicant(READY_WITH_REVIEW_RESULT, {
        needs_review: [],
        foir: { status: 'REVIEW', detail: 'not computed: no income' },
      }),
    );
    const section = obligations(html);
    expect(section).not.toContain('data-testid="foir-tiles"');
    expect(section).not.toContain('Max new EMI');
    expect(section).toContain('not computed: no income');
    expect(section).toContain('data-tone="review"');
    expect(banner(html)).not.toContain('needs a person');
    expect(banner(html)).toContain('present and consistent');
  });

  it('renders no obligations section for older backends', () => {
    // NOT_READY_RESULT has neither obligations nor foir (pre-FOIR engine).
    const html = renderCard(NOT_READY_RESULT);
    expect(html).not.toContain('data-focus="obligations"');
    expect(html).not.toContain('FOIR');
  });

  it('shows the engine summary for an empty project', () => {
    const html = renderCard({
      ...NOT_READY_RESULT,
      summary: 'No analysed documents found in this project',
      applicants: [],
      unsupported_documents: [],
    });
    expect(banner(html)).toContain('>NOT READY<');
    expect(banner(html)).toContain(
      'No analysed documents found in this project',
    );
    expect(banner(html)).not.toContain('Fix the issues below');
  });

  it('renders NEEDS REVIEW in the review tone', () => {
    const html = renderCard({
      ...NOT_READY_RESULT,
      overall_verdict: 'NEEDS REVIEW',
    });
    expect(bannerTag(html)).toContain('data-verdict="review"');
    expect(banner(html)).toContain('>NEEDS REVIEW<');
    expect(banner(html)).toContain('A person must review');
  });

  it('renders obligations and the indicative FOIR as the engine returned them', () => {
    const html = renderCard(READY_OBLIGATIONS_RESULT);
    const section = obligations(html);

    expect(section).toContain('Obligations &amp; FOIR (indicative)');
    // The engine's label, verbatim.
    expect(section).toContain('indicative — the lender&#x27;s policy decides');

    // Fixed loan EMI: payee, amount, day, months seen, matched to the application.
    const emi = row(section, 'MULSHI AUTO FINANCE / CAR LOAN EMI');
    expect(emi).toContain('data-tone="ok"');
    expect(emi).toContain('>₹8,200<');
    expect(emi).toContain('on the 5th');
    expect(emi).toContain('6 of 6 months (Mar 2026 – Aug 2026)');
    expect(emi).toContain('ACH');
    expect(emi).toContain('Declared · matched');
    expect(emi).toContain(
      'Declared: ₹8,200 Mulshi Auto Finance Ltd (sample) (Car loan) [01_loan_application_form.pdf]',
    );
    expect(section).toContain('Fixed loan EMIs (1)');

    // Other fixed debits (README Q4: rent 3rd, SIP 7th, mobile 25th).
    expect(section).toContain('Other fixed debits (3)');
    const rent = row(section, 'RENT / VASANT JOSHI');
    expect(rent).toContain('>₹18,000<');
    expect(rent).toContain('on the 3rd');
    expect(rent).toContain('>rent<');
    expect(row(section, 'SIP / SAMPLE ASSET MGMT MF')).toContain('>₹5,000<');
    expect(row(section, 'MOBILE &amp; BROADBAND')).toContain('>₹1,299<');

    // Totals exactly as returned.
    expect(section).toContain('Fixed monthly total');
    expect(section).toContain('₹32,499');
    expect(section).toContain('₹24,299');
    expect(section).toContain('₹13,265.5');

    // FOIR vs the checklist limit, max new EMI and the limit source.
    expect(section).toContain('>9.9%<');
    expect(section).toContain('vs limit 70%');
    expect(section).toContain('₹82,500');
    expect(section).toContain('Max new EMI at FOIR 70% (indicative)');
    expect(section).toContain('₹49,550');
    expect(section).toContain(
      'Limit FOIR 70%: Varunika Loan Partners calculator, eligibility tab: &#x27;FOIR 70%&#x27;',
    );
    expect(section).toContain(
      'href="https://www.varunikaloanpartners.example/calculator" target="_blank" rel="noopener noreferrer"',
    );
    expect(section).toContain('Indicative – the lender&#x27;s policy decides.');
    expect(section).toContain('Income: salary slips, median net pay.');
    // The engine note is not repeated next to the same UI note.
    expect(section).not.toContain('Only loan EMIs count toward FOIR');

    // Variable and one-off debits are listed, collapsed.
    expect(section).toContain('Variable and one-off debits (3)');
    const insurance = row(section, 'HEALTH INSURANCE PREMIUM');
    expect(insurance).toContain('>one-off<');
    const card = row(section, 'CC PAYMENT / TAMRAPUR UCB CREDIT CARD');
    expect(card).toContain('avg ₹11,316.67 (₹9,000–₹13,900)');
  });

  it('flags an undeclared bank EMI amber and counts it as the engine did', () => {
    const section = obligations(renderCard(READY_WITH_REVIEW_RESULT));
    const kesari = row(section, 'KESARI FINSERV / PERSONAL LOAN EMI');
    expect(kesari).toContain('data-tone="review"');
    expect(kesari).toContain('Not on the application');
    expect(kesari).toContain('on the 10th');
    expect(kesari).toContain('NACH');
    expect(row(section, 'MULSHI AUTO FINANCE / CAR LOAN EMI')).toContain(
      'data-tone="ok"',
    );
    expect(section).toContain('Fixed loan EMIs (2)');
    expect(section).toContain('₹14,700');
    expect(section).toContain('>17.8%<');
    expect(section).toContain('₹43,050');
  });

  it('lists a declared EMI missing from the bank', () => {
    const a = READY_OBLIGATIONS_RESULT.applicants[0];
    const html = renderCard(
      withApplicant(READY_OBLIGATIONS_RESULT, {
        obligations: {
          ...a.obligations,
          fixed_loan_emis: [],
          declared_emis: [
            {
              lender: 'Mulshi Auto Finance Ltd (sample)',
              loan_type: 'Car loan',
              amount: 8200,
              document_name: '01_loan_application_form.pdf',
              status: 'not_found',
            },
          ],
        },
        foir: { ...a.foir, limit_url: SCRIPT_URL },
      }),
    );
    const section = obligations(html);
    const declared = row(section, 'Mulshi Auto Finance Ltd (sample)');
    expect(declared).toContain('data-tone="review"');
    expect(declared).toContain(
      'Declared, not found in the bank debits (still counted toward FOIR)',
    );
    expect(declared).toContain('₹8,200');
    // Only http(s) links are rendered.
    expect(section).not.toContain(SCRIPT_URL);
    expect(section).not.toContain('>source<');
  });

  it('tags findings with the DSA pain point they illustrate', () => {
    const html = renderCard(NOT_READY_RESULT);
    const slips = row(html, 'Salary slips (last 3 months)');
    expect(slips).toContain('data-pain-point="incomplete-files"');
    expect(slips).toContain('Incomplete files');
    expect(row(html, 'Address proof')).toContain(
      'data-pain-point="manual-verification"',
    );
    expect(row(html, 'Declared net salary vs salary slips')).toContain(
      'data-pain-point="manual-verification"',
    );
    // Clean rows and a missing optional item carry no tag.
    expect(row(html, 'PAN')).not.toContain('data-pain-point');
    expect(row(html, 'Form-16 / ITR')).not.toContain('data-pain-point');
    // Without a handler the tag is plain text, with one it is a button.
    expect(slips).toMatch(
      /<span title="Why DSAs need this: [^"]+" data-pain-point/,
    );
    const clickable = row(
      renderCard(NOT_READY_RESULT, () => undefined),
      'Salary slips (last 3 months)',
    );
    expect(clickable).toMatch(
      /<button type="button" title="Why DSAs need this: [^"]+" data-pain-point="incomplete-files"/,
    );

    // EMI / FOIR findings point at the bank-statement card.
    const review = renderCard(READY_WITH_REVIEW_RESULT);
    expect(row(review, 'Declared EMIs vs bank debits')).toContain(
      'data-pain-point="bank-statement-structuring"',
    );
    expect(obligations(review)).toContain(
      'data-pain-point="bank-statement-structuring"',
    );
  });
});
