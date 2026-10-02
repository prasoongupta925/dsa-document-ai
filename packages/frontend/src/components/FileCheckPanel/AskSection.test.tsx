// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import AskSection, { AskMeter } from './AskSection';
import FileCheckPanel from './index';
import { NOT_READY_RESULT } from './fixtures';
import type { FileCheckAskState } from '../../hooks/useFileCheckAsk';
import type { FileCheckState } from '../../hooks/useFileCheck';
import { ASK_DEFAULT_PRICING } from '../../lib/fileCheckAsk';

// The panel signs its confirm / undo calls with the app's AWS client.
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

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>{node}</I18nextProvider>,
  );
}

/** Text of the element marked data-testid=`id`, tags stripped. */
function textOf(html: string, id: string): string {
  const start = html.indexOf(`data-testid="${id}"`);
  if (start < 0) throw new Error(`${id} not rendered`);
  const open = html.indexOf('>', start) + 1;
  return html
    .slice(open, html.indexOf('</p>', open))
    .replace(/<[^>]+>/g, '')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, '&');
}

const noop = () => undefined;
const asyncNoop = async () => undefined;

function askState(patch: Partial<FileCheckAskState> = {}): FileCheckAskState {
  return {
    turns: [],
    pending: false,
    ask: asyncNoop,
    clear: noop,
    session: { calls: 0, input_tokens: 0, output_tokens: 0 },
    pricing: ASK_DEFAULT_PRICING,
    modelId: null,
    usage: null,
    usageError: null,
    loadUsage: asyncNoop,
    ...patch,
  };
}

describe('AskMeter', () => {
  it('shows the session tokens × price, the 7-day spend and the pricing', () => {
    const html = render(
      <AskMeter
        session={{ calls: 2, input_tokens: 12_345, output_tokens: 678 }}
        pricing={{
          input_per_million_usd: 0.35,
          output_per_million_usd: 2.95,
          region: 'ap-south-1',
        }}
        modelId="global.amazon.nova-2-lite-v1:0"
        usage={{
          window_days: 7,
          calls: 5,
          input_tokens: 30_000,
          output_tokens: 2_000,
          cost_usd: 0.0164,
        }}
      />,
    );
    expect(textOf(html, 'ask-meter')).toBe(
      [
        'Answered by Amazon Nova 2 Lite using ONLY the extracted fields, check results and document text',
        'Model global.amazon.nova-2-lite-v1:0 (ap-south-1)',
        // 12,345 × $0.35/1M + 678 × $2.95/1M = $0.00632085
        'This session: 12,345 input + 678 output tokens = $0.0063',
        'Last 7 days: $0.0164 over 5 calls',
        'Pricing $0.35 / 1M input, $2.95 / 1M output',
        'Nothing is kept longer than 7 days',
        'Synthetic data only.',
      ].join(' · '),
    );
  });

  it('starts at zero and says when the 7-day spend is unavailable', () => {
    const text = textOf(
      render(
        <AskMeter
          session={{ calls: 0, input_tokens: 0, output_tokens: 0 }}
          pricing={ASK_DEFAULT_PRICING}
          modelId={null}
          usage={null}
          usageError={new Error('API error: 404')}
        />,
      ),
      'ask-meter',
    );
    expect(text).toContain('This session: 0 input + 0 output tokens = $0.0000');
    expect(text).toContain('Last 7 days: not available');
    expect(text).not.toContain('Model ');
    expect(text).toContain('Pricing $0.35 / 1M input, $2.95 / 1M output');
  });

  it('uses the singular for one call', () => {
    const text = textOf(
      render(
        <AskMeter
          session={{ calls: 1, input_tokens: 1200, output_tokens: 300 }}
          pricing={ASK_DEFAULT_PRICING}
          modelId="global.amazon.nova-2-lite-v1:0"
          usage={{
            window_days: 7,
            calls: 1,
            input_tokens: 1200,
            output_tokens: 300,
            cost_usd: 0.001305,
          }}
        />,
      ),
      'ask-meter',
    );
    expect(text).toContain('= $0.0013');
    expect(text).toContain('Last 7 days: $0.0013 over 1 call ·');
    expect(text).toContain('Model global.amazon.nova-2-lite-v1:0 ·');
  });
});

describe('AskSection', () => {
  it('offers the four Plan B questions, naming the checklist', () => {
    const html = render(
      <AskSection
        state={askState()}
        checklistName="Personal Loan - Salaried"
      />,
    );
    const chips = html.slice(html.indexOf('data-testid="ask-suggestions"'));
    expect(chips).toContain(
      '>Is this file complete for Personal Loan - Salaried? What is missing?<',
    );
    expect(chips).toContain(
      '>Does the declared salary match the bank statement credits?<',
    );
    expect(chips).toContain('>Is the PAN consistent across all documents?<');
    expect(chips).toContain(
      '>Summarise the monthly obligations from the bank statement.<',
    );
    expect(html).toContain('maxLength="1000"');
    expect(html).toContain('data-focus="ask"');
  });

  it('renders answers with their token caption and errors inline', () => {
    const html = render(
      <AskSection
        checklistName="Personal Loan - Salaried"
        state={askState({
          turns: [
            {
              id: 1,
              question: 'Is the PAN consistent across all documents?',
              status: 'done',
              answer: 'No. The application shows **ABCPP1234K**.',
              input_tokens: 1200,
              output_tokens: 300,
              cost_usd: 0.001305,
              grounded_on: {
                applicants: ['Amit Suresh Patil'],
                documents: ['application.pdf', 'slip_aug.pdf'],
              },
            },
            {
              id: 2,
              question: 'Summarise the monthly obligations.',
              status: 'error',
              error: new Error('API error: 502'),
            },
          ],
          session: { calls: 1, input_tokens: 1200, output_tokens: 300 },
        })}
      />,
    );
    expect(html).toContain('<strong>ABCPP1234K</strong>');
    expect(textOf(html, 'ask-caption')).toBe(
      '1,200 in / 300 out tokens · $0.0013 · from 2 documents',
    );
    expect(html).toContain('The model call failed (HTTP 502)');
    expect(html).toContain(
      'The verdict above is deterministic and still valid.',
    );
    expect(textOf(html, 'ask-meter')).toContain(
      'This session: 1,200 input + 300 output tokens = $0.0013',
    );
  });

  it('does not render raw HTML from the model', () => {
    const html = render(
      <AskSection
        checklistName="x"
        state={askState({
          turns: [
            {
              id: 1,
              question: 'q',
              status: 'done',
              answer:
                'ok <img src=x onerror="alert(1)"> [link](javascript:alert(1))',
              input_tokens: 1,
              output_tokens: 1,
              cost_usd: 0,
            },
          ],
        })}
      />,
    );
    expect(html).not.toContain('<img');
    expect(html).not.toContain('href="javascript:');
  });
});

describe('FileCheckPanel', () => {
  const fileCheckState = (patch: Partial<FileCheckState> = {}) =>
    ({
      checklists: [
        {
          id: 'salaried_personal_loan',
          name: 'Personal Loan - Salaried',
          foir: {
            value: 0.7,
            source:
              "Varunika Loan Partners calculator, eligibility tab: 'FOIR 70%'",
          },
        },
      ],
      checklistsLoaded: true,
      checklistsLoading: false,
      checklistsError: null,
      loadChecklists: asyncNoop,
      checklistId: 'salaried_personal_loan',
      setChecklistId: noop,
      applicant: '',
      setApplicant: noop,
      knownApplicants: [],
      result: NOT_READY_RESULT,
      resultApplicant: '',
      lastRunAt: null,
      running: false,
      error: null,
      runCheck: asyncNoop,
      ...patch,
    }) as FileCheckState;

  it('keeps the deterministic verdict visible when Ask fails', () => {
    const html = render(
      <FileCheckPanel
        state={fileCheckState()}
        askState={askState({
          turns: [
            {
              id: 1,
              question: 'Why is this file not ready?',
              status: 'error',
              error: new Error('API error: 503'),
            },
          ],
        })}
        onClose={noop}
      />,
    );
    // Verdict first, then Ask below it with the error inline.
    const verdict = html.indexOf('data-verdict="notReady"');
    const ask = html.indexOf('data-focus="ask"');
    expect(verdict).toBeGreaterThan(-1);
    expect(ask).toBeGreaterThan(verdict);
    expect(html).toContain('Ask is not configured for this deployment');
    expect(html).toContain('Salary slips (last 3 months)');
    // The checklist's indicative FOIR limit is shown before a run.
    expect(html).toContain(
      'Indicative FOIR limit 70% – Varunika Loan Partners calculator, eligibility tab: &#x27;FOIR 70%&#x27;',
    );
  });

  it('offers Ask before any check has run', () => {
    const html = render(
      <FileCheckPanel
        state={fileCheckState({ result: null })}
        askState={askState()}
        onClose={noop}
      />,
    );
    expect(html).toContain('No check run yet');
    expect(html).toContain('Ask about this file');
  });
});
