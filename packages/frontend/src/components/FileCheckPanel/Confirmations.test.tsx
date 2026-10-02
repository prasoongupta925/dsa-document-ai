// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
// Confirming needs-review checklist items, and the call-project note.
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import VerdictCard, { type VerdictConfirmations } from './VerdictCard';
import FileCheckPanel from './index';
import CallProjectNote, {
  isCallRecordingsOnly,
  recordingDocuments,
} from './CallProjectNote';
import {
  canConfirmItem,
  confirmRequestBody,
  confirmationApplicant,
  confirmationDocumentIds,
  confirmationRowKey,
  formatConfirmedAt,
  isConfirmedItem,
  undoRequestBody,
  useItemConfirmations,
} from './confirmations';
import { NOT_READY_RESULT } from './fixtures';
import { BRAND_CONFIRMED_RESULT, CALL_PROJECT_RESULT } from './confirmFixtures';
import type { FileCheckState } from '../../hooks/useFileCheck';
import type { FileCheckAskState } from '../../hooks/useFileCheckAsk';
import { ASK_DEFAULT_PRICING } from '../../lib/fileCheckAsk';
import { ApiError } from '../../lib/apiError';

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

/** The <li> of the checklist row labelled `label`. */
function rowOf(html: string, label: string): string {
  const at = html.indexOf(`>${label}</span>`);
  if (at < 0) throw new Error(`${label} not rendered`);
  const start = html.lastIndexOf('<li', at);
  return html.slice(start, html.indexOf('</li>', at) + 5);
}

function decode(html: string): string {
  return html
    .replace(/<[^>]+>/g, '')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&');
}

const noop = () => undefined;
const asyncNoop = async () => undefined;

function confirmations(
  patch: Partial<VerdictConfirmations> = {},
): VerdictConfirmations {
  return {
    busyKey: null,
    error: null,
    onConfirm: noop,
    onUndo: noop,
    ...patch,
  };
}

const AMIT = NOT_READY_RESULT.applicants[0];
const RAHUL = BRAND_CONFIRMED_RESULT.applicants[0];

describe('confirm helpers', () => {
  it('stores the confirmation under the PAN, else the name, with the documents shown', () => {
    expect(confirmationApplicant(RAHUL)).toBe('BQXPD4821K');
    expect(confirmationApplicant({ ...RAHUL, pan: '  ' })).toBe(
      'Rahul Vijay Deshmukh',
    );
    expect(confirmationApplicant({ ...RAHUL, pan: null })).toBe(
      'Rahul Vijay Deshmukh',
    );
    expect(
      confirmationDocumentIds({
        documents: [
          ...RAHUL.documents,
          { ...RAHUL.documents[0] },
          { ...RAHUL.documents[1], document_id: null },
        ],
      }),
    ).toEqual(['r-01', 'r-02', 'r-06']);
    expect(confirmRequestBody(RAHUL, { item_id: 'x05' }, 'vlp_pl_sal')).toEqual(
      {
        applicant: 'BQXPD4821K',
        item_id: 'x05',
        checklist_id: 'vlp_pl_sal',
        document_ids: ['r-01', 'r-02', 'r-06'],
      },
    );
    expect(confirmRequestBody(RAHUL, { item_id: 'x05' })).not.toHaveProperty(
      'checklist_id',
    );
    // Undo by PAN and name: one saved by name before the PAN was read counts too.
    expect(undoRequestBody(RAHUL, { item_id: 'x02' })).toEqual({
      applicant: 'BQXPD4821K',
      applicant_name: 'Rahul Vijay Deshmukh',
      item_id: 'x02',
    });
    expect(
      undoRequestBody({ ...RAHUL, pan: null }, { item_id: 'x02' }),
    ).toEqual({ applicant: 'Rahul Vijay Deshmukh', item_id: 'x02' });
    expect(confirmationRowKey(RAHUL, { item_id: 'x02' })).toBe(
      'BQXPD4821K|x02',
    );
  });

  it('only REVIEW items can be confirmed; CONFIRMED ones can be undone', () => {
    expect(canConfirmItem({ status: 'REVIEW' })).toBe(true);
    expect(canConfirmItem({ status: 'needs_review' })).toBe(true);
    for (const status of ['PRESENT', 'MISSING', 'CONFIRMED']) {
      expect(canConfirmItem({ status })).toBe(false);
    }
    expect(isConfirmedItem({ status: 'CONFIRMED' })).toBe(true);
    expect(isConfirmedItem({ status: 'REVIEW' })).toBe(false);
  });

  it('formats the confirmation time in the viewer zone', () => {
    const text = formatConfirmedAt(
      '2026-10-01T08:35:00.000000+00:00',
      'Asia/Kolkata',
    );
    expect(text).toMatch(/2026/);
    expect(text).toMatch(/Oct/);
    expect(text).toMatch(/(^|\D)(2:05|14:05)/);
    expect(formatConfirmedAt('not a date')).toBeNull();
    expect(formatConfirmedAt(null)).toBeNull();
  });
});

describe('VerdictCard confirmations', () => {
  it('offers Confirm on needs-review items only', () => {
    const html = render(
      <VerdictCard result={NOT_READY_RESULT} confirmations={confirmations()} />,
    );
    const review = rowOf(html, 'Address proof');
    expect(review).toContain('data-testid="confirm-item"');
    expect(review).toContain('Needs a person');
    expect(review).toContain(
      'title="I checked this myself: count it as met. Saved with your name for 7 days; you can undo it."',
    );
    expect(decode(review)).toContain('Confirm');
    for (const label of [
      'Loan application form',
      'Salary slips (last 3 months)',
      'Form-16 / ITR',
    ]) {
      expect(rowOf(html, label)).not.toContain('confirm-item');
    }
    expect(html.match(/data-testid="confirm-item"/g)).toHaveLength(1);
    // Without the wiring (e.g. an older shell) there is nothing to click.
    const plain = render(<VerdictCard result={NOT_READY_RESULT} />);
    expect(plain).not.toContain('confirm-item');
  });

  it('shows who confirmed an item and when, with Undo', () => {
    const html = render(
      <VerdictCard
        result={BRAND_CONFIRMED_RESULT}
        confirmations={confirmations()}
      />,
    );
    const address = rowOf(html, 'Address Proof (Passport/Utility Bill)');
    expect(address).toContain('data-tone="ok"');
    expect(address).not.toContain('Needs a person');
    expect(address).not.toContain('confirm-item');
    expect(address).toContain('data-testid="undo-confirmation"');
    expect(address).toContain('title="2026-10-01T08:35:00.000000+00:00"');
    const line = decode(
      address.slice(address.indexOf('data-testid="confirmed-by"')),
    );
    expect(line).toMatch(/^[^>]*>Confirmed by asha\.verma at .*2026/);
    expect(decode(address)).toContain('Confirmed');
    // The engine's own sentence is replaced by the line above.
    expect(address).not.toContain('confirmed by asha.verma on 01 Oct 2026');
    expect(decode(rowOf(html, 'Cross-check: PAN format'))).toContain(
      'Confirmed by rohan.iyer at',
    );
    // Confirmed again after a document left the file: Confirm, and the engine says why.
    const stale = rowOf(html, 'Cross-check: DOB match and age');
    expect(stale).toContain('data-testid="confirm-item"');
    expect(decode(stale)).toContain(
      'but a document it was made on is no longer in this file: confirm again',
    );
    // Optional review items can be confirmed too.
    expect(rowOf(html, 'Eligibility: Credit score of 750+')).toContain(
      'confirm-item',
    );
  });

  it('waits while a request or a check runs and reports a failure on its row', () => {
    const busy = render(
      <VerdictCard
        result={BRAND_CONFIRMED_RESULT}
        confirmations={confirmations({ busyKey: 'BQXPD4821K|x05' })}
      />,
    );
    const stale = rowOf(busy, 'Cross-check: DOB match and age');
    expect(stale).toContain('animate-spin');
    expect(stale).toMatch(
      /<button[^>]*disabled=""[^>]*data-testid="confirm-item"/,
    );
    // Every other button waits for the verdict too.
    expect(rowOf(busy, 'Cross-check: PAN format')).toMatch(
      /<button[^>]*disabled=""[^>]*data-testid="undo-confirmation"/,
    );

    const running = render(
      <VerdictCard
        result={BRAND_CONFIRMED_RESULT}
        confirmations={confirmations({ disabled: true })}
      />,
    );
    expect(rowOf(running, 'Eligibility: Credit score of 750+')).toMatch(
      /<button[^>]*disabled=""/,
    );

    const failed = render(
      <VerdictCard
        result={BRAND_CONFIRMED_RESULT}
        confirmations={confirmations({
          error: { key: 'BQXPD4821K|x02', error: new ApiError(502, 'x') },
        })}
      />,
    );
    expect(decode(rowOf(failed, 'Cross-check: PAN format'))).toContain(
      'Could not save the confirmation (HTTP 502). Try again.',
    );
    expect(failed.match(/role="alert"/g)).toHaveLength(1);
  });

  it('cannot confirm for an applicant without analysed documents', () => {
    const result = {
      ...NOT_READY_RESULT,
      applicants: [{ ...AMIT, documents: [] }],
    };
    const html = render(
      <VerdictCard result={result} confirmations={confirmations()} />,
    );
    const row = rowOf(html, 'Address proof');
    expect(row).toMatch(/<button[^>]*disabled=""/);
    expect(row).toContain(
      'title="This applicant has no analysed documents to confirm against."',
    );
  });
});

// ------------------------------------------------------------------ hook
function hookOnce<T>(useHook: () => T): T {
  let value: T | undefined;
  function Probe() {
    value = useHook();
    return null;
  }
  renderToStaticMarkup(<Probe />);
  return value as T;
}

function fakeApi(reply?: unknown) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchApi = async <T,>(url: string, init?: RequestInit): Promise<T> => {
    calls.push({ url, init });
    if (reply instanceof Error) throw reply;
    return reply as T;
  };
  return { calls, fetchApi };
}

describe('useItemConfirmations', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(noop);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const setup = (reply?: unknown, projectId: string | null = 'proj_demo') => {
    const api = fakeApi(reply);
    let reruns = 0;
    const state = hookOnce(() =>
      useItemConfirmations({
        fetchApi: api.fetchApi,
        projectId,
        checklistId: 'vlp_pl_sal',
        onChanged: async () => {
          reruns += 1;
        },
      }),
    );
    return { ...api, state, reruns: () => reruns };
  };

  it('confirms with the PAN, the item and the documents shown, then runs the check again', async () => {
    const { calls, state, reruns } = setup({ item_id: 'x05' });
    await expect(state.confirm(RAHUL, { item_id: 'x05' })).resolves.toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('projects/proj_demo/file-check/confirmations');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      applicant: 'BQXPD4821K',
      item_id: 'x05',
      checklist_id: 'vlp_pl_sal',
      document_ids: ['r-01', 'r-02', 'r-06'],
    });
    expect(reruns()).toBe(1);
  });

  it('undoes with a DELETE that carries the applicant in the body', async () => {
    const { calls, state, reruns } = setup({ item_id: 'x02', deleted: true });
    await expect(state.undo(RAHUL, { item_id: 'x02' })).resolves.toBe(true);
    expect(calls[0].url).toBe('projects/proj_demo/file-check/confirmations');
    expect(calls[0].init?.method).toBe('DELETE');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      applicant: 'BQXPD4821K',
      applicant_name: 'Rahul Vijay Deshmukh',
      item_id: 'x02',
    });
    expect(reruns()).toBe(1);
  });

  it('keeps the verdict when the call fails, and does nothing before a check', async () => {
    const failed = setup(new ApiError(502, 'down'));
    await expect(failed.state.confirm(RAHUL, { item_id: 'x05' })).resolves.toBe(
      false,
    );
    expect(failed.reruns()).toBe(0);
    const none = setup(undefined, null);
    await expect(none.state.confirm(RAHUL, { item_id: 'x05' })).resolves.toBe(
      false,
    );
    expect(none.calls).toEqual([]);
  });
});

// ------------------------------------------------------------------ call projects
function fileCheckState(patch: Partial<FileCheckState> = {}): FileCheckState {
  return {
    checklists: [],
    checklistsLoaded: true,
    checklistsLoading: false,
    checklistsError: null,
    loadChecklists: asyncNoop,
    checklistId: 'salaried_personal_loan',
    setChecklistId: noop,
    applicant: '',
    setApplicant: noop,
    knownApplicants: [],
    result: null,
    resultApplicant: '',
    lastRunAt: null,
    running: false,
    error: null,
    runCheck: asyncNoop,
    ...patch,
  } as FileCheckState;
}

const askState = {
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
} as FileCheckAskState;

describe('call projects', () => {
  it('tells a call project from a loan file', () => {
    expect(isCallRecordingsOnly(CALL_PROJECT_RESULT)).toBe(true);
    expect(recordingDocuments(CALL_PROJECT_RESULT)).toHaveLength(1);
    // Older engines send no recording_documents.
    expect(recordingDocuments(NOT_READY_RESULT)).toEqual([]);
    expect(isCallRecordingsOnly(NOT_READY_RESULT)).toBe(false);
    // Recordings next to an applicant, or next to another unchecked document: a loan file.
    expect(
      isCallRecordingsOnly({
        ...CALL_PROJECT_RESULT,
        applicants: NOT_READY_RESULT.applicants,
      }),
    ).toBe(false);
    expect(
      isCallRecordingsOnly({
        ...CALL_PROJECT_RESULT,
        pending_documents: [{ document_id: 'd1', document_name: 'scan.pdf' }],
      }),
    ).toBe(false);
  });

  it('shows the note with the Call QA hint instead of NOT READY', () => {
    const html = render(
      <FileCheckPanel
        state={fileCheckState({ result: CALL_PROJECT_RESULT })}
        askState={askState}
        onClose={noop}
      />,
    );
    expect(html).toContain('data-testid="call-project-note"');
    expect(html).not.toContain('data-verdict=');
    expect(html).not.toContain('NOT READY');
    const text = decode(html);
    expect(text).toContain('This project holds call recordings');
    expect(text).toContain('The file check applies to loan files');
    expect(text).toContain('pick Call QA Reviewer (Tools → Use agent)');
    expect(text).toContain('"Score this call against the QA rubric"');
    expect(text).toContain('Call recordings (1)');
    expect(text).toContain(
      'SAMPLE_call_varunika_to_sneha_kulkarni_2026-09-30.wav',
    );
  });

  it('a loan file keeps its verdict and lists its call recordings apart', () => {
    const result = {
      ...NOT_READY_RESULT,
      recording_documents: CALL_PROJECT_RESULT.recording_documents,
    };
    const html = render(
      <FileCheckPanel
        state={fileCheckState({ result })}
        askState={askState}
        onClose={noop}
      />,
    );
    expect(html).toContain('data-verdict="notReady"');
    expect(html).not.toContain('call-project-note');
    const block = html.slice(html.indexOf('data-testid="recording-documents"'));
    expect(decode(block)).toContain(
      'Call recordings (1) – not part of the loan file',
    );
    expect(decode(block)).toContain(
      'SAMPLE_call_varunika_to_sneha_kulkarni_2026-09-30.wav',
    );
    // The panel wires Confirm for the review item of the verdict.
    expect(rowOf(html, 'Address proof')).toContain('confirm-item');
  });

  it('renders the note on its own', () => {
    const html = render(<CallProjectNote result={CALL_PROJECT_RESULT} />);
    expect(html).toContain('aria-label="This project holds call recordings"');
    expect(html).toContain('data-testid="call-qa-hint"');
  });
});
