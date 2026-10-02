// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
// Suggestion chips on the chat's welcome screen.
import { renderToStaticMarkup } from 'react-dom/server';
import i18next, { type TFunction } from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../../i18n/locales/en.json';
import ChatPanel from './index';
import SuggestionChips from './SuggestionChips';
import {
  CALL_QA_ID,
  FILE_CHECKER_ID,
  LOAN_ASSISTANT_ID,
  REMINDER_WRITER_ID,
  applySuggestion,
  chatSuggestions,
  isRecording,
  projectKind,
  type ChatSuggestion,
} from './suggestions';
import { DSA_PAIN_POINTS } from '../../data/dsaPainPoints';
import type { Agent, ChatPanelProps, Document } from './types';

// The panel's modals, message list and AWS hooks are not under test.
vi.mock('./MessageList', () => ({ default: () => null }));
vi.mock('../GraphSearchResultModal', () => ({ default: () => null }));
vi.mock('../ToolResultDetailModal', () => ({ default: () => null }));
vi.mock('../ImageModal', () => ({ default: () => null }));
vi.mock('isomorphic-dompurify', () => ({
  default: { sanitize: (html: string) => html },
}));
vi.mock('../Toast', () => ({
  useToast: () => ({ showToast: () => undefined }),
}));
vi.mock('../../hooks/useAwsClient', () => ({
  useAwsClient: () => ({ getArtifactDownloadUrl: async () => '' }),
}));
vi.mock('../../hooks/useRuntimeConfig', () => ({
  useRuntimeConfig: () => ({ documentStorageBucketName: 'docs-bucket' }),
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

const t = ((key: string, opts?: Record<string, unknown>) =>
  i18n.t(key, opts)) as unknown as TFunction;

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>{node}</I18nextProvider>,
  );
}

function decode(html: string): string {
  return html
    .replace(/<[^>]+>/g, '')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&');
}

// The built-in agents as shipped (packages/infra/src/prompts/builtin_agents).
const SHIPPED = import.meta.glob<{ agent_id: string; name: string }>(
  '../../../../infra/src/prompts/builtin_agents/*.json',
  { eager: true, import: 'default' },
);
const BUILTIN: Agent[] = Object.values(SHIPPED).map((a) => ({
  agent_id: a.agent_id,
  name: a.name,
  created_at: '2026-09-29T10:00:00+00:00',
  builtin: true,
}));
const SHIPPED_NAMES = new Map(BUILTIN.map((a) => [a.agent_id, a.name]));

function doc(name: string, fileType: string): Document {
  return {
    document_id: `d-${name}`,
    name,
    file_type: fileType,
    file_size: 1000,
    status: 'completed',
    use_bda: false,
    started_at: '2026-10-01T08:00:00+00:00',
    ended_at: '2026-10-01T08:01:00+00:00',
  };
}

const PDF = 'application/pdf';
const LOAN_FILE = [
  doc('01_loan_application_form.pdf', PDF),
  doc('02_identity_details_self_declaration.pdf', PDF),
  doc('06_bank_statement_2026-03_to_2026-08.pdf', PDF),
];
// "Telecaller QA – Sample calls"
const CALLS = [
  doc('SAMPLE_call_varunika_to_sneha_kulkarni_2026-09-30.wav', 'audio/x-wav'),
];

const ids = (list: ChatSuggestion[]) => list.map((s) => s.id);
const all = (r: { primary: ChatSuggestion[]; more: ChatSuggestion[] }) => [
  ...r.primary,
  ...r.more,
];

/** The chip `id` of the list; the test fails when it is missing. */
function chip(list: ChatSuggestion[], id: string): ChatSuggestion {
  const found = list.find((s) => s.id === id);
  if (!found) throw new Error(`no ${id} chip`);
  return found;
}

describe('call recordings', () => {
  it('tells recordings apart by MIME type, else by file extension', () => {
    for (const fileType of ['audio/wav', 'audio/x-wav', 'audio/mpeg']) {
      expect(isRecording({ name: 'x', file_type: fileType })).toBe(true);
    }
    expect(
      isRecording({ name: 'kyc.mp4', file_type: 'video/mp4; codecs=avc1' }),
    ).toBe(true);
    expect(isRecording({ name: 'Call 2.M4A', file_type: '' })).toBe(true);
    expect(
      isRecording({ name: 'call.mp3', file_type: 'application/octet-stream' }),
    ).toBe(true);
    // A PDF stays a PDF whatever it is called.
    expect(isRecording({ name: 'call.wav', file_type: PDF })).toBe(false);
    expect(isRecording({ name: 'slip.pdf', file_type: PDF })).toBe(false);
  });

  it('calls a project of mostly recordings a call project', () => {
    expect(projectKind([])).toBe('loanFile');
    expect(projectKind(LOAN_FILE)).toBe('loanFile');
    expect(projectKind(CALLS)).toBe('calls');
    expect(projectKind([...CALLS, LOAN_FILE[0]])).toBe('calls');
    expect(projectKind([...CALLS, ...LOAN_FILE.slice(0, 2)])).toBe('loanFile');
  });
});

describe('chatSuggestions', () => {
  const loanFile = () =>
    chatSuggestions({ agents: BUILTIN, documents: LOAN_FILE, t });

  it('asks the shipped built-in agents by id and name, or the AI Assistant', () => {
    expect([...SHIPPED_NAMES.keys()].sort()).toEqual(
      [
        CALL_QA_ID,
        REMINDER_WRITER_ID,
        FILE_CHECKER_ID,
        LOAN_ASSISTANT_ID,
      ].sort(),
    );
    const chips = all(loanFile());
    for (const s of chips) {
      if (s.agentId === null) {
        expect(s.agentName).toBe('AI Assistant');
      } else {
        expect(s.agentName).toBe(SHIPPED_NAMES.get(s.agentId));
      }
      // Every chip has its own text (a missing key would show the key).
      expect(s.label).not.toMatch(/^chat\./);
      expect(s.prompt).not.toMatch(/^chat\./);
    }
    const agentOf = (id: string) => chips.find((s) => s.id === id)?.agentName;
    expect(agentOf('fileReady')).toBe('File Checker');
    expect(agentOf('reminderWhatsapp')).toBe('Document Reminder Writer');
    expect(agentOf('customerDocs')).toBe('Loan Assistant');
    expect(agentOf('callScore')).toBe('Call QA Reviewer');
    expect(agentOf('eligibilityBest')).toBe('AI Assistant');
    expect(agentOf('voice')).toBe('AI Assistant');
  });

  it('covers everything the app does', () => {
    const chips = all(loanFile());
    expect(new Set(chips.map((s) => s.group))).toEqual(
      new Set([
        'fileCheck',
        'eligibility',
        'reminders',
        'customer',
        'calls',
        'voice',
        'about',
      ]),
    );
    const text = Object.fromEntries(chips.map((s) => [s.id, s.prompt]));
    expect(text.fileReady).toBe(
      'Is this file ready for lender login? What is missing?',
    );
    expect(text.fileMissingHinglish).toBe(
      'Kya sab documents hain? Kya missing hai?',
    );
    expect(text.reminderWhatsapp).toMatch(/WhatsApp/);
    expect(text.reminderSms).toMatch(/SMS/);
    expect(text.cibilSummary).toMatch(/credit report/);
    expect(text.emiSplit).toMatch(/EMI for ₹10 lakh at 11% for 5 years/);
    expect(text.balanceTransfer).toMatch(/^Balance transfer: /);
    expect(text.customerEmi).toMatch(/EMI kitni hogi\?$/);
    expect(text.callScore).toBe('Score this call against the QA rubric');
    expect(text.callCompliance).toMatch(/compliance flags/);
  });

  it('a loan file leads with the file check and keeps Call QA under More', () => {
    const r = loanFile();
    expect(r.kind).toBe('loanFile');
    expect(ids(r.primary)).toEqual([
      'fileReady',
      'fileMissingHinglish',
      'eligibilityBest',
      'reminderWhatsapp',
      'customerDocs',
      'voice',
      'why',
    ]);
    expect(r.primary.map((s) => s.agentId)).toEqual([
      FILE_CHECKER_ID,
      FILE_CHECKER_ID,
      null,
      REMINDER_WRITER_ID,
      LOAN_ASSISTANT_ID,
      null,
      null,
    ]);
    expect(ids(r.more)).toEqual([
      'filePan',
      'fileSalary',
      'fileEmis',
      'eligibilityWhyNot',
      'cibilSummary',
      'emiSplit',
      'balanceTransfer',
      'reminderSms',
      'customerEmi',
      'customerEligibility',
      'callScore',
      'callCompliance',
      'callSummary',
      'callCoaching',
    ]);
  });

  it('a call project leads with Call QA and keeps the loan-file questions under More', () => {
    const r = chatSuggestions({ agents: BUILTIN, documents: CALLS, t });
    expect(r.kind).toBe('calls');
    expect(ids(r.primary)).toEqual([
      'callScore',
      'callCompliance',
      'voice',
      'why',
      'customerDocs',
    ]);
    expect(r.primary[0].agentId).toBe(CALL_QA_ID);
    expect(ids(r.more).slice(0, 4)).toEqual([
      'callSummary',
      'callCoaching',
      'customerEmi',
      'customerEligibility',
    ]);
    expect(r.more.map((s) => s.group).slice(4)).toEqual([
      ...Array(5).fill('fileCheck'),
      ...Array(5).fill('eligibility'),
      'reminders',
      'reminders',
    ]);
  });

  it('a loan file with a recording offers one call question up front', () => {
    const r = chatSuggestions({
      agents: BUILTIN,
      documents: [...LOAN_FILE, ...CALLS],
      t,
    });
    expect(r.kind).toBe('loanFile');
    expect(ids(r.primary).slice(0, 2)).toEqual([
      'fileReady',
      'fileMissingHinglish',
    ]);
    expect(ids(r.primary)).toContain('callScore');
    expect(ids(r.more)).not.toContain('callScore');
  });

  it('leaves out the chips of built-in agents the project does not list', () => {
    const none = chatSuggestions({ agents: [], documents: LOAN_FILE, t });
    expect(all(none).every((s) => s.agentId === null)).toBe(true);
    expect(ids(none.primary)).toEqual([
      'eligibilityBest',
      'eligibilityWhyNot',
      'voice',
      'why',
    ]);

    const fileChecker = BUILTIN.filter((a) => a.agent_id === FILE_CHECKER_ID);
    const some = chatSuggestions({
      agents: fileChecker,
      documents: LOAN_FILE,
      t,
    });
    expect(
      new Set(
        all(some)
          .map((s) => s.agentId)
          .filter(Boolean),
      ),
    ).toEqual(new Set([FILE_CHECKER_ID]));
  });

  it('offers only the selected agent when the agent cannot be changed', () => {
    const r = chatSuggestions({
      agents: BUILTIN,
      documents: LOAN_FILE,
      selectedAgentId: CALL_QA_ID,
      canSelectAgent: false,
      t,
    });
    expect(all(r).every((s) => s.agentId === CALL_QA_ID)).toBe(true);
    // The call questions are the only ones left, so they lead even in a loan file.
    expect(ids(r.primary)).toEqual(['callScore', 'callCompliance']);
    expect(ids(r.more)).toEqual(['callSummary', 'callCoaching']);
  });

  it('explains phone calls and the voice bot from facts, voice chat only when it is on', () => {
    const voice = (available: boolean) =>
      chip(
        all(
          chatSuggestions({
            agents: BUILTIN,
            documents: LOAN_FILE,
            voiceChatAvailable: available,
            t,
          }),
        ),
        'voice',
      );
    const off = voice(false);
    expect(off.label).toBe('How do phone calls and the voice bot work?');
    const lines = off.prompt.split('\n- ');
    expect(lines[0]).toMatch(
      /^How do phone calls and the voice bot work with this app\?.*using only these facts/,
    );
    expect(lines.slice(1).map((l) => l.split(':')[0])).toEqual([
      'Works today',
      'Pilot, in its own browser link',
      'Not included yet',
    ]);
    expect(off.prompt).toMatch(/Call QA Reviewer agent scores each call/);
    expect(off.prompt).toMatch(
      /never promises approval and never asks for an OTP/,
    );
    expect(off.prompt).not.toMatch(/Voice Chat/);
    expect(voice(true).prompt).toMatch(
      /\n- Works today: Tools → Voice Chat in this chat/,
    );
  });

  it('answers "why this helps a DSA" from the Why DSAs need this points only', () => {
    const why = chip(all(loanFile()), 'why');
    expect(why.label).toBe('Why does this help a DSA?');
    const lines = why.prompt.split('\n- ');
    expect(lines[0]).toMatch(/^Why does this app help a loan DSA\?/);
    expect(lines.slice(1)).toEqual(
      DSA_PAIN_POINTS.map((p) => `${p.title}: ${p.feature}`),
    );
  });
});

describe('applySuggestion', () => {
  function target(selectedAgentId: string | null) {
    const calls: string[] = [];
    const input = {
      textContent: '',
      focus: () => calls.push('focus'),
    };
    return {
      calls,
      input,
      target: {
        selectedAgentId,
        onAgentSelect: (id: string | null) => calls.push(`agent:${id}`),
        onInputChange: (value: string) => calls.push(`input:${value}`),
        input,
      },
    };
  }

  it('selects the chip agent, then puts the question in the input', () => {
    const { calls, input, target: tg } = target(null);
    applySuggestion(
      { agentId: FILE_CHECKER_ID, prompt: 'Is this file ready?' },
      tg,
    );
    expect(input.textContent).toBe('Is this file ready?');
    expect(calls).toEqual([
      `agent:${FILE_CHECKER_ID}`,
      'focus',
      'input:Is this file ready?',
    ]);
  });

  it('keeps the chat when the agent is already selected', () => {
    const { calls, target: tg } = target(CALL_QA_ID);
    applySuggestion({ agentId: CALL_QA_ID, prompt: 'Score' }, tg);
    expect(calls).toEqual(['focus', 'input:Score']);
    // Back to the AI Assistant from a built-in agent.
    const back = target(CALL_QA_ID);
    applySuggestion({ agentId: null, prompt: 'Why?' }, back.target);
    expect(back.calls[0]).toBe('agent:null');
  });

  it('still sets the question without an input element', () => {
    const { calls, target: tg } = target(null);
    applySuggestion({ agentId: null, prompt: 'Why?' }, { ...tg, input: null });
    expect(calls).toEqual(['input:Why?']);
  });
});

describe('welcome screen', () => {
  const noop = () => undefined;

  function renderPanel(patch: Partial<ChatPanelProps> = {}) {
    return render(
      <ChatPanel
        messages={[]}
        inputMessage=""
        sending={false}
        streamingBlocks={[]}
        agents={BUILTIN}
        selectedAgent={null}
        documents={LOAN_FILE}
        onInputChange={noop}
        onSendMessage={noop}
        onAgentSelect={noop}
        onAgentClick={noop}
        onNewChat={noop}
        {...patch}
      />,
    );
  }

  const chipIds = (html: string) =>
    [...html.matchAll(/data-suggestion="([^"]+)"/g)].map((m) => m[1]);

  it('shows the chips under the input before the user types', () => {
    const html = renderPanel();
    expect(html).toContain('data-testid="chat-suggestions"');
    expect(html).toContain('data-kind="loanFile"');
    expect(html.indexOf('chat-input-editable')).toBeLessThan(
      html.indexOf('data-testid="chat-suggestions"'),
    );
    expect(chipIds(html)).toEqual(
      ids(
        chatSuggestions({ agents: BUILTIN, documents: LOAN_FILE, t }).primary,
      ),
    );
    const text = decode(html);
    expect(text).toContain('Try asking');
    expect(text).toContain('More suggestions (14)');
    expect(html).toContain(`data-agent-id="${FILE_CHECKER_ID}"`);
    expect(html).toContain('title="Asks File Checker"');
  });

  it('puts the call questions first in a call project', () => {
    const html = renderPanel({ documents: CALLS });
    expect(html).toContain('data-kind="calls"');
    expect(chipIds(html).slice(0, 2)).toEqual(['callScore', 'callCompliance']);
    expect(html).toContain(`data-agent-id="${CALL_QA_ID}"`);
  });

  it('hides the chips once the user types, and in voice chat', () => {
    expect(renderPanel({ inputMessage: 'Kya missing hai' })).not.toContain(
      'chat-suggestions',
    );
    expect(
      renderPanel({ voiceChat: { available: true, mode: true } }),
    ).not.toContain('chat-suggestions');
  });

  it('lists the rest under their part of the app, and marks the picked chip', () => {
    const r = chatSuggestions({ agents: BUILTIN, documents: LOAN_FILE, t });
    const html = render(
      <SuggestionChips
        suggestions={r}
        activeId="filePan"
        onPick={noop}
        defaultExpanded
      />,
    );
    expect(html).toMatch(/aria-expanded="true"/);
    expect(decode(html)).toContain('Fewer suggestions');
    const groups = [...html.matchAll(/data-group="([^"]+)"/g)].map((m) => m[1]);
    expect(groups).toEqual([
      'fileCheck',
      'eligibility',
      'reminders',
      'customer',
      'calls',
    ]);
    expect(decode(html)).toContain('Call QA');
    expect(html).toMatch(/data-suggestion="filePan"[^>]*data-active="true"/);
    expect(html.match(/data-active="true"/g)).toHaveLength(1);
    // Nothing to suggest: nothing rendered.
    expect(
      render(
        <SuggestionChips
          suggestions={{ kind: 'loanFile', primary: [], more: [] }}
          onPick={noop}
        />,
      ),
    ).toBe('');
  });
});
