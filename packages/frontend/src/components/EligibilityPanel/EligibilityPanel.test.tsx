// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.
// Controls without hooks are called directly to reach their handlers.)
import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import EligibilityPanel, { branchLendersOf } from '.';
import VerdictCard from '../FileCheckPanel/VerdictCard';
import {
  READY_OBLIGATIONS_RESULT,
  SNEHA_NOT_READY_RESULT,
} from '../FileCheckPanel/fixtures';
import LendersSection from './LendersSection';
import CibilSection, { TradelineActionToggle } from './CibilSection';
import ProfileSection from './ProfileSection';
import { Segmented } from './fields';
import { companyCheckRows, pincodeCheckRows } from './checks';
import {
  LOGIN_NOTIFIED,
  LOGIN_NO_WEBHOOK,
  LOGIN_WEBHOOK_FAILED,
  PREFILLED_INPUTS_RESPONSE,
  SAVED_INPUTS_RESPONSE,
  WORKED_EXAMPLE_RESPONSE,
} from './fixtures';
import {
  calculateRequestBody,
  inputsKey,
  normalizeInputs,
  parseCalculateResponse,
  parseCompanyCheck,
  parseInputsResponse,
  parseLoginResponse,
  parsePincodeCheck,
} from '../../lib/eligibility';
import {
  newDraft,
  prefillValuesOf,
  type EligibilityState,
  type LenderLoginState,
} from '../../hooks/useEligibility';
import type {
  EligibilityInputs,
  TradelineAction,
} from '../../types/eligibility';

// The Lenders tab's branch finder gets its fetchApi itself.
vi.mock('../../hooks/useAwsClient', () => ({
  useAwsClient: () => ({ fetchApi: async () => undefined }),
}));

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

/** The <tbody> of one lender, by data-lender. */
function lenderBody(html: string, lender: string): string {
  const start = html.indexOf(`data-lender="${lender}"`);
  if (start < 0) throw new Error(`${lender} not rendered`);
  const open = html.lastIndexOf('<tbody', start);
  return html.slice(open, html.indexOf('</tbody>', start) + 8);
}

/** A cell of a lender's first row, by data-col. */
function cell(body: string, col: string): string {
  const at = body.indexOf(`data-col="${col}"`);
  if (at < 0) throw new Error(`${col} not rendered`);
  const open = body.indexOf('>', at) + 1;
  return plain(body.slice(open, body.indexOf('</td>', open)));
}

const noop = () => undefined;
const SNEHA = 'Sneha Anil Kulkarni';
const RESULT = parseCalculateResponse(WORKED_EXAMPLE_RESPONSE, SNEHA);

function lenders(
  extra: Partial<Parameters<typeof LendersSection>[0]> = {},
): string {
  return render(
    <LendersSection
      applicantName={SNEHA}
      result={RESULT}
      stale={false}
      calculating={false}
      calcError={null}
      onCalculate={noop}
      onLogin={noop}
      {...extra}
    />,
  );
}

describe('Lenders table (the worked example)', () => {
  it('shows ICICI Bank at 20,58,000, 72 months, 11%, EMI 39,172.13', () => {
    const icici = lenderBody(lenders(), 'ICICI Bank');
    expect(cell(icici, 'eligible_amount')).toBe('₹20,58,000');
    expect(cell(icici, 'tenure_months')).toBe('72');
    expect(cell(icici, 'roi')).toBe('11%');
    expect(cell(icici, 'emi')).toBe('₹39,172.13');
    expect(cell(icici, 'per_lakh_emi')).toBe('₹2,174.24');
    expect(cell(icici, 'foir_eligibility')).toBe('₹24,65,226.61');
    expect(cell(icici, 'multiplier_eligibility')).toBe('₹20,58,000');
    expect(cell(icici, 'bt_amount')).toBe('₹0');
  });

  it('shows HDFC Bank at its 15,00,000 cap: 60 months, 12%, EMI 33,366.67', () => {
    const hdfc = lenderBody(lenders(), 'HDFC Bank');
    expect(cell(hdfc, 'eligible_amount')).toBe('₹15,00,000');
    expect(cell(hdfc, 'tenure_months')).toBe('60');
    expect(cell(hdfc, 'roi')).toBe('12%');
    expect(cell(hdfc, 'emi')).toBe('₹33,366.67');
    expect(cell(hdfc, 'multiplier_eligibility')).toBe('₹19,60,000');
  });

  it('highlights the best lender and explains its figures', () => {
    const html = lenders();
    const icici = lenderBody(html, 'ICICI Bank');
    expect(icici).toContain('data-best="true"');
    expect(icici).toContain('data-testid="best-lender"');
    expect(lenderBody(html, 'HDFC Bank')).not.toContain('data-best');
    expect(plain(html)).toContain('Best lender ICICI Bank ₹20,58,000');
    // The best lender's details are open: the sheet's formulas with its numbers.
    const how = plain(
      icici.slice(icici.indexOf('data-testid="how-calculated"')),
    );
    expect(how).toContain(
      'Per-lakh EMI: EMI of ₹1,00,000 at 11% over 60 months = ₹2,174.24',
    );
    expect(how).toContain(
      'FOIR eligibility: (₹98,000 × 70% − ₹15,000) ÷ ₹2,174.24 × 1,00,000 = ₹24,65,226.61',
    );
    expect(how).toContain('Multiplier eligibility: ₹98,000 × 21 = ₹20,58,000');
    expect(how).toContain(
      'EMI: EMI of ₹20,58,000 at 11% over 72 months = ₹39,172.13',
    );
    // The EMI over the requested 60 months, next to the one at 72 months.
    expect(plain(icici)).toContain('EMI over 60 months ₹44,745.91');
    // HDFC's calculation tenure is its max tenure: no second EMI.
    expect(
      plain(
        lenderBody(lenders({ initialExpanded: ['HDFC Bank'] }), 'HDFC Bank'),
      ),
    ).not.toContain('EMI over 60 months');
  });

  it('gives each lender its status with the reasons', () => {
    const html = lenders();
    const status = (lender: string) => {
      const body = lenderBody(html, lender);
      const at = body.indexOf('data-testid="lender-status"');
      return plain(
        body.slice(body.indexOf('>', at) + 1, body.indexOf('</span>', at)),
      );
    };
    expect(status('ICICI Bank')).toBe('Eligible');
    expect(status('Axis Bank')).toBe('Not serviceable');
    expect(status('Bajaj Finance')).toBe('Not eligible');
    expect(plain(lenderBody(html, 'Axis Bank'))).toContain(
      'Pincode 400615 is not serviceable by Axis Bank',
    );
    // Only an eligible lender can be logged in (the API refuses others).
    expect(lenderBody(html, 'ICICI Bank')).toContain(
      'data-testid="login-button"',
    );
    expect(lenderBody(html, 'Axis Bank')).not.toContain(
      'data-testid="login-button"',
    );
    expect(plain(lenderBody(html, 'Axis Bank'))).toContain(
      'A file is logged in only with an eligible lender.',
    );
  });

  it('labels the result as sample policy and indicative', () => {
    const html = lenders();
    const text = plain(html);
    expect(text).toContain('2 of 4 lenders eligible');
    // Both labels sit on the table itself.
    const caption = plain(
      html.slice(html.indexOf('<caption'), html.indexOf('</caption>')),
    );
    expect(caption).toBe(
      '2 of 4 lenders eligible Sample policy — replace with your lender grid Indicative: the lender decides.',
    );
    expect(html).toContain('data-testid="sample-policy"');
    expect(
      plain(html.slice(html.indexOf('data-testid="disclaimers"'))),
    ).toContain(
      'Sample policy — replace with your lender grid: the lender policies',
    );
    expect(plain(html)).toContain('Net income used ₹98,000 / month');
    expect(plain(html)).toContain('Obligations ₹15,000 / month 1 EMI counted');
  });

  it('dims a result calculated for other inputs and blocks the login', () => {
    const html = lenders({ stale: true });
    expect(html).toContain('data-testid="stale-result"');
    expect(html).toMatch(
      /<button type="button" disabled="" title="Check the eligibility again before logging in\."[^>]*data-testid="login-button"/,
    );
  });

  it.each([
    [LOGIN_NOTIFIED, 'notified', 'CRM webhook notified (HTTP 200).'],
    [
      LOGIN_NO_WEBHOOK,
      'notSent',
      'No CRM webhook is enabled, so the CRM was not notified',
    ],
    [
      LOGIN_WEBHOOK_FAILED,
      'failed',
      'CRM webhook not delivered: receiver answered HTTP 500.',
    ],
  ])(
    'shows whether the CRM webhook was notified (%#)',
    (raw, outcome, text) => {
      const login: LenderLoginState = {
        sending: false,
        error: null,
        response: parseLoginResponse(raw),
        at: new Date('2026-09-30T16:10:00Z'),
      };
      const html = lenders({ logins: { 'ICICI Bank': login } });
      const icici = lenderBody(html, 'ICICI Bank');
      expect(icici).toContain(`data-outcome="${outcome}"`);
      expect(plain(icici)).toContain('Logged in with ICICI Bank at');
      expect(plain(icici)).toContain(text);
      expect(plain(icici)).toContain('Log in again');
    },
  );

  it('asks to calculate before there is a result', () => {
    const html = lenders({ result: null });
    expect(html).not.toContain('data-testid="lenders-table"');
    expect(plain(html)).toContain('No eligibility calculated yet');
    expect(html).toContain('>Check eligibility</button>');
  });
});

/** Every element of a rendered tree that matches. */
function findAll(
  node: ReactNode,
  match: (el: ReactElement<Record<string, unknown>>) => boolean,
): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap((n) => findAll(n, match));
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  const own = match(node) ? [node] : [];
  return [...own, ...findAll(node.props.children as ReactNode, match)];
}

describe('BT / Obligate / Close', () => {
  const inputs = normalizeInputs(SAVED_INPUTS_RESPONSE.inputs);

  /** Clicks one choice of loan `index`'s toggle; returns the edit it made. */
  function choose(index: number, action: TradelineAction) {
    const edits: ((i: EligibilityInputs) => EligibilityInputs)[] = [];
    const toggle = TradelineActionToggle({
      index,
      value: inputs.cibil.tradelines[index].action,
      name: `loan-${index}`,
      legend: `What to do with loan ${index + 1}`,
      labels: { bt: 'BT', obligate: 'Obligate', close: 'Close' },
      onEdit: (update) => edits.push(update),
    });
    const segmented = Segmented(
      toggle.props as Parameters<typeof Segmented<TradelineAction>>[0],
    );
    const radios = findAll(segmented, (el) => el.type === 'input');
    expect(radios.map((r) => [r.props.value, r.props.checked])).toEqual([
      ['bt', inputs.cibil.tradelines[index].action === 'bt'],
      ['obligate', inputs.cibil.tradelines[index].action === 'obligate'],
      ['close', inputs.cibil.tradelines[index].action === 'close'],
    ]);
    const radio = radios.find((r) => r.props.value === action);
    (radio?.props.onChange as () => void)();
    expect(edits).toHaveLength(1);
    return edits[0];
  }

  it('each choice updates that loan in the calculate request', () => {
    const actions = (i: EligibilityInputs) =>
      calculateRequestBody(SNEHA, i).inputs?.cibil.tradelines.map(
        (r) => r.action,
      );
    expect(actions(inputs)).toEqual(['obligate', 'close']);
    expect(actions(choose(0, 'bt')(inputs))).toEqual(['bt', 'close']);
    expect(actions(choose(0, 'close')(inputs))).toEqual(['close', 'close']);
    expect(actions(choose(1, 'obligate')(inputs))).toEqual([
      'obligate',
      'obligate',
    ]);
    // A choice changes the inputs key: the result on screen becomes stale.
    expect(inputsKey(choose(1, 'bt')(inputs))).not.toBe(inputsKey(inputs));
  });

  it('renders one radio group per loan with the saved action checked', () => {
    const html = render(
      <CibilSection cibil={inputs.cibil} onEdit={noop} initialExpanded={[0]} />,
    );
    const rows = html.split('data-testid="tradeline"').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatch(/checked="" value="obligate"/);
    expect(rows[1]).toMatch(/checked="" value="close"/);
    expect(rows[0].match(/checked=""/g)).toHaveLength(1);
    expect(html).toContain('data-action="obligate"');
    expect(html).toContain(
      '<legend class="sr-only">What to do with loan 1</legend>',
    );
    expect(plain(rows[0])).toContain(
      'Keeps running: this EMI is counted as an obligation.',
    );
    expect(plain(rows[1])).toContain(
      'Closed before the new loan: this EMI is not counted.',
    );
    // Loan 1 is expanded: the report's account details.
    expect(rows[0]).toContain('data-testid="tradeline-details"');
    expect(rows[0]).toContain('value="PL00001234"');
    expect(rows[1]).not.toContain('data-testid="tradeline-details"');
    // Amounts are shown with Indian grouping.
    expect(rows[0]).toContain('value="6,00,000"');
    expect(rows[0]).toContain('value="4,10,000"');
    expect(rows[0]).toContain('value="15,000"');
    expect(plain(html)).toContain(
      'A bureau pull can fill these fields automatically later.',
    );
  });

  it('flags enquiries that do not add up', () => {
    const html = render(
      <CibilSection
        cibil={{
          ...inputs.cibil,
          enquiries: { d30: 5, d60: 2, d90: 3, d120: 4 },
        }}
        onEdit={noop}
      />,
    );
    expect(plain(html)).toContain(
      'Enquiries add up over time: the 30 days count cannot be more than the 60 days count.',
    );
  });
});

describe('Profile', () => {
  const parsed = parseInputsResponse(PREFILLED_INPUTS_RESPONSE, SNEHA);
  const prefill = prefillValuesOf(parsed.inputs, parsed.fromDocuments);

  it('marks the values read from the documents', () => {
    const html = render(
      <ProfileSection
        inputs={parsed.inputs}
        onEdit={noop}
        prefill={prefill}
        incomeSource="verified: salary slips, median net pay"
      />,
    );
    // name, PAN, company, employment type and net income
    expect(html.match(/data-testid="from-documents"/g)).toHaveLength(5);
    expect(html).toMatch(/readOnly=""[^>]*value="XXXXXX314M"/);
    expect(html).toContain('value="98,000"');
    expect(plain(html)).toContain(
      'From the documents (verified: salary slips, median net pay).',
    );
    expect(plain(html)).toContain('Masked: the documents hold the full PAN.');
    // An edited value loses its badge.
    const edited = render(
      <ProfileSection
        inputs={{
          ...parsed.inputs,
          profile: { ...parsed.inputs.profile, net_income: 90000 },
        }}
        onEdit={noop}
        prefill={prefill}
      />,
    );
    expect(edited.match(/data-testid="from-documents"/g)).toHaveLength(4);
  });

  it('shows "Check availability" and "Check category" per lender', () => {
    const t = i18n.t.bind(i18n);
    const pincode = pincodeCheckRows(
      t,
      parsePincodeCheck(
        {
          pincode: '400615',
          region: 'Thane (Ghodbunder Road)',
          serviceable_by: 1,
          lenders: [
            {
              lender_id: 'icici_bank',
              lender: 'ICICI Bank',
              serviceable: true,
            },
            { lender_id: 'axis_bank', lender: 'Axis Bank', serviceable: false },
          ],
          sample: true,
          label: 'sample policy — replace with your lender grid',
        },
        '400615',
      ),
    );
    const company = companyCheckRows(
      t,
      parseCompanyCheck(
        {
          query: 'Konkan Softworks',
          match: {
            name: 'Konkan Softworks Pvt Ltd',
            employment_type: 'private_limited',
          },
          categories: [
            {
              lender_id: 'icici_bank',
              lender: 'ICICI Bank',
              category: 'CAT A',
              listed: true,
              accepted: true,
              foir: 0.7,
              multiplier: 21,
            },
            {
              lender_id: 'tata_capital',
              lender: 'Tata Capital',
              category: null,
              listed: false,
              accepted: true,
              foir: 0.5,
              multiplier: 10,
            },
            {
              lender_id: 'hdfc_bank',
              lender: 'HDFC Bank',
              category: null,
              listed: false,
              accepted: false,
              foir: null,
              multiplier: null,
            },
          ],
          suggestions: [],
          sample: true,
        },
        'Konkan Softworks',
      ),
    );
    const html = render(
      <ProfileSection
        inputs={{
          ...parsed.inputs,
          profile: {
            ...parsed.inputs.profile,
            pincode: '400615',
            company: 'Konkan Softworks',
          },
        }}
        onEdit={noop}
        prefill={{}}
        pincodeCheck={pincode}
        companyCheck={company}
        onCheckPincode={noop}
        onCheckCompany={noop}
      />,
    );
    const pin = plain(html.slice(html.indexOf('data-testid="pincode-check"')));
    expect(pin).toContain(
      '400615: Thane (Ghodbunder Road) · 1 of 2 lenders serve it',
    );
    expect(pin).toContain('ICICI Bank Serviceable');
    expect(pin).toContain('Axis Bank Not serviceable');
    const cat = plain(html.slice(html.indexOf('data-testid="company-check"')));
    expect(cat).toContain('Listed as Konkan Softworks Pvt Ltd');
    expect(cat).toContain('ICICI Bank CAT A · FOIR 70%, multiplier 21');
    expect(cat).toContain(
      "Tata Capital Unlisted: the lender's unlisted-company terms · FOIR 50%, multiplier 10",
    );
    expect(cat).toContain('HDFC Bank Unlisted: not accepted');
    // Both answers come from the SAMPLE lists and say so.
    expect(
      html.match(/Sample policy — replace with your lender grid/g)?.length,
    ).toBeGreaterThanOrEqual(2);
    expect(html).toContain('>Check availability</button>');
    expect(html).toContain('>Check category</button>');
  });

  it('marks the answers of the uploaded lists', () => {
    const t = i18n.t.bind(i18n);
    const pincode = pincodeCheckRows(
      t,
      parsePincodeCheck(
        {
          pincode: '400606',
          region: null,
          lenders: [
            {
              lender_id: 'icici_bank',
              lender: 'ICICI Bank',
              serviceable: false,
              source: 'dsa_list',
            },
            {
              lender_id: 'hdfc_bank',
              lender: 'HDFC Bank',
              serviceable: true,
              source: 'sample',
            },
            // An API without the field: the SAMPLE list.
            { lender_id: 'axis_bank', lender: 'Axis Bank', serviceable: true },
          ],
          sample: true,
        },
        '400606',
      ),
    );
    expect(pincode.rows.map((row) => row.text)).toEqual([
      'Not serviceable · your list',
      'Serviceable',
      'Serviceable',
    ]);
    const company = companyCheckRows(
      t,
      parseCompanyCheck(
        {
          query: 'Konkan Softworks',
          match: null,
          categories: [
            {
              lender_id: 'hdfc_bank',
              lender: 'HDFC Bank',
              category: 'CAT B',
              listed: true,
              accepted: true,
              foir: 0.55,
              multiplier: 16,
              source: 'dsa_list',
            },
            {
              lender_id: 'icici_bank',
              lender: 'ICICI Bank',
              category: null,
              listed: false,
              accepted: false,
              foir: null,
              multiplier: null,
              source: 'dsa_list',
            },
          ],
          suggestions: [],
          sample: true,
        },
        'Konkan Softworks',
      ),
    );
    expect(company.rows.map((row) => row.text)).toEqual([
      'CAT B · FOIR 55%, multiplier 16 · your list',
      'Unlisted: not accepted · your list',
    ]);
  });

  it("shows a FOIR grid lender's FOIR range by salary slab", () => {
    const t = i18n.t.bind(i18n);
    const company = companyCheckRows(
      t,
      parseCompanyCheck(
        {
          query: 'Konkan Softworks',
          match: null,
          categories: [
            {
              lender_id: 'hdfc_bank',
              lender: 'HDFC Bank',
              category: 'CAT A',
              listed: true,
              accepted: true,
              foir: 0.6,
              multiplier: 20,
              foir_range: [0.6, 0.75],
            },
            {
              lender_id: 'icici_bank',
              lender: 'ICICI Bank',
              category: 'CAT A',
              listed: true,
              accepted: true,
              foir: 0.7,
              multiplier: 21,
              foir_range: null,
            },
          ],
          suggestions: [],
          sample: true,
        },
        'Konkan Softworks',
      ),
    );
    expect(company.rows.map((row) => row.text)).toEqual([
      'CAT A · FOIR 60%–75% by net salary slab, multiplier 20',
      'CAT A · FOIR 70%, multiplier 21',
    ]);
  });
});

describe('File check entry point', () => {
  it('offers "Eligibility & lenders" per applicant when wired', () => {
    expect(
      render(<VerdictCard result={SNEHA_NOT_READY_RESULT} />),
    ).not.toContain('data-testid="open-eligibility"');
    for (const result of [SNEHA_NOT_READY_RESULT, READY_OBLIGATIONS_RESULT]) {
      const html = render(
        <VerdictCard result={result} onOpenEligibility={noop} />,
      );
      expect(html.match(/data-testid="open-eligibility"/g)).toHaveLength(
        result.applicants.length,
      );
      expect(html).toContain('Eligibility &amp; lenders</button>');
    }
  });
});

describe('EligibilityPanel', () => {
  const state = (drafts: EligibilityState['drafts']): EligibilityState => ({
    lenders: null,
    lendersLoading: false,
    lendersError: null,
    loadLenders: async () => undefined,
    drafts,
    load: async () => undefined,
    edit: noop,
    save: async () => true,
    calculate: async () => null,
    login: async () => ({ kind: 'ignored' }),
    checkPincode: async () => {
      throw new Error('not used');
    },
    checkCompany: async () => {
      throw new Error('not used');
    },
    forget: noop,
  });

  it('shows the three sheets as tabs with the sample-policy banner', () => {
    const parsed = parseInputsResponse(SAVED_INPUTS_RESPONSE, SNEHA);
    const draft = {
      ...newDraft('CKRPK7314M'),
      loaded: true,
      inputs: parsed.inputs,
      savedKey: inputsKey(parsed.inputs),
      saved: true,
      expiresAt: parsed.expiresAt,
      result: RESULT,
      resultKey: inputsKey(parsed.inputs),
    };
    const html = render(
      <EligibilityPanel
        state={state({ CKRPK7314M: draft })}
        applicant="CKRPK7314M"
        name={SNEHA}
        pan="CKRPK7314M"
        onBack={noop}
        onClose={noop}
        initialTab="lenders"
      />,
    );
    const text = plain(html);
    expect(text).toContain(`Eligibility & lenders ${SNEHA} · PAN XXXXXX314M`);
    expect(html).not.toContain('CKRPK7314M');
    expect(html).toContain('data-testid="sample-banner"');
    expect(html.match(/role="tab"/g)).toHaveLength(3);
    expect(html).toMatch(/aria-selected="true"[^>]*>.*?Lenders/);
    expect(html).toContain('data-testid="lenders-table"');
    expect(text).toContain('Saved');
    expect(text).toContain('Saved inputs are deleted automatically on');
  });

  it('says when the inputs are not saved yet', () => {
    const parsed = parseInputsResponse(PREFILLED_INPUTS_RESPONSE, SNEHA);
    const draft = {
      ...newDraft(SNEHA),
      loaded: true,
      inputs: parsed.inputs,
      prefillValues: prefillValuesOf(parsed.inputs, parsed.fromDocuments),
      savedKey: inputsKey(parsed.inputs),
      notes: ['1 loan EMI(s) from the bank statement were added as tradelines'],
    };
    const html = render(
      <EligibilityPanel
        state={state({ [SNEHA]: draft })}
        applicant={SNEHA}
        name={SNEHA}
        onBack={noop}
        onClose={noop}
      />,
    );
    expect(plain(html)).toContain('Not saved yet');
    expect(plain(html)).toContain(
      'Saved inputs are deleted automatically 7 days after the first save.',
    );
    expect(html).toContain('data-testid="draft-notes"');
    expect(html).toContain('data-testid="profile-net-income"');
  });
});

describe('Lenders tab: nearest branches and what is still needed', () => {
  const state = (
    drafts: EligibilityState['drafts'],
    lenders: EligibilityState['lenders'] = null,
  ): EligibilityState => ({
    lenders,
    lendersLoading: false,
    lendersError: null,
    loadLenders: async () => undefined,
    drafts,
    load: async () => undefined,
    edit: noop,
    save: async () => true,
    calculate: async () => null,
    login: async () => ({ kind: 'ignored' }),
    checkPincode: async () => {
      throw new Error('not used');
    },
    checkCompany: async () => {
      throw new Error('not used');
    },
    forget: noop,
  });
  const saved = () => {
    const parsed = parseInputsResponse(SAVED_INPUTS_RESPONSE, SNEHA);
    return {
      ...newDraft('CKRPK7314M'),
      loaded: true,
      inputs: parsed.inputs,
      savedKey: inputsKey(parsed.inputs),
      saved: true,
    };
  };
  const policy = (name: string) => ({
    id: name.toLowerCase().replace(/\W+/g, '_'),
    name,
    product: null,
    roi: null,
    min_tenure_months: null,
  });

  it('looks up the eligible lenders, the best first', () => {
    // ICICI and HDFC are eligible; Axis is not serviceable, Bajaj not eligible.
    expect(RESULT?.best_lender).toBe('ICICI Bank');
    expect(branchLendersOf(RESULT, [])).toEqual(['ICICI Bank', 'HDFC Bank']);
    const hdfcBest = RESULT && { ...RESULT, best_lender: 'HDFC Bank' };
    expect(branchLendersOf(hdfcBest, [])).toEqual(['HDFC Bank', 'ICICI Bank']);
    const none = RESULT && {
      ...RESULT,
      best_lender: null,
      per_lender: RESULT.per_lender.map((row) => ({
        ...row,
        status: 'not_eligible',
      })),
    };
    expect(branchLendersOf(none, [])).toEqual([]);
  });

  it('looks up every lender of the policies before a calculation', () => {
    const lenders = [policy('HDFC Bank'), policy('Bajaj Finance')];
    expect(
      branchLendersOf(null, lenders as Parameters<typeof branchLendersOf>[1]),
    ).toEqual(['HDFC Bank', 'Bajaj Finance']);
    expect(branchLendersOf(null, undefined)).toEqual([]);
  });

  it('shows the branch finder under the lenders table of a project', () => {
    const draft = {
      ...saved(),
      result: RESULT,
      resultKey: saved().savedKey,
    };
    const panel = (projectId?: string, initialTab?: 'profile' | 'lenders') =>
      render(
        <EligibilityPanel
          state={state({ CKRPK7314M: draft })}
          projectId={projectId}
          applicant="CKRPK7314M"
          name={SNEHA}
          pan="CKRPK7314M"
          onBack={noop}
          onClose={noop}
          initialTab={initialTab}
        />,
      );
    const html = panel('proj-1', 'lenders');
    expect(draft.inputs.profile.pincode).toBe('400615');
    expect(html).toContain('data-testid="branch-finder"');
    expect(plain(html)).toContain('Nearest branches');
    expect(html.indexOf('data-testid="lenders-table"')).toBeLessThan(
      html.indexOf('data-testid="branch-finder"'),
    );
    expect(panel(undefined, 'lenders')).not.toContain(
      'data-testid="branch-finder"',
    );
    expect(panel('proj-1', 'profile')).not.toContain(
      'data-testid="branch-finder"',
    );
  });

  it('shows the branch finder before a calculation once the lenders load', () => {
    const panel = (lenders: EligibilityState['lenders']) =>
      render(
        <EligibilityPanel
          state={state({ CKRPK7314M: saved() }, lenders)}
          projectId="proj-1"
          applicant="CKRPK7314M"
          name={SNEHA}
          pan="CKRPK7314M"
          onBack={noop}
          onClose={noop}
          initialTab="lenders"
        />,
      );
    expect(panel(null)).not.toContain('data-testid="branch-finder"');
    const loaded = {
      sample: true,
      label: null,
      lenders: [policy('HDFC Bank')],
      disclaimers: [],
    } as unknown as EligibilityState['lenders'];
    expect(panel(loaded)).toContain('data-testid="branch-finder"');
  });

  it('lists what is still needed before the first calculation', () => {
    const draft = saved();
    draft.inputs = {
      ...draft.inputs,
      cibil: { ...draft.inputs.cibil, score: null },
    };
    const html = render(
      <EligibilityPanel
        state={state({ CKRPK7314M: draft })}
        applicant="CKRPK7314M"
        name={SNEHA}
        pan="CKRPK7314M"
        onBack={noop}
        onClose={noop}
        initialTab="lenders"
      />,
    );
    expect(html).toContain('data-testid="still-needed"');
    expect(plain(html)).toContain('Still needed before Check eligibility');
  });
});
