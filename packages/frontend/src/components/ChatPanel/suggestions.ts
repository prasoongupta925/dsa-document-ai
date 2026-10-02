// Suggestion chips on the chat's welcome screen: ready-made questions for
// everything the app does, each asked of the agent that handles it (a
// built-in agent, by its id, or the default AI Assistant). A project of call
// recordings shows the call questions first, a loan file the file-check ones.
import type { TFunction } from 'i18next';
import type { Agent, Document } from './types';
import { DSA_PAIN_POINTS } from '../../data/dsaPainPoints';

// Built-in agents (packages/infra/src/prompts/builtin_agents/*.json).
export const FILE_CHECKER_ID = 'builtin-file-checker';
export const REMINDER_WRITER_ID = 'builtin-document-reminder';
export const LOAN_ASSISTANT_ID = 'builtin-loan-assistant';
export const CALL_QA_ID = 'builtin-call-qa-reviewer';

export type SuggestionGroup =
  | 'fileCheck'
  | 'eligibility'
  | 'reminders'
  | 'customer'
  | 'calls'
  | 'voice'
  | 'about';

interface SuggestionDef {
  /** chat.chips.<id> is the chip text and the question, unless built below. */
  id: string;
  group: SuggestionGroup;
  /** A built-in agent id; null: the default AI Assistant. */
  agentId: string | null;
}

const SUGGESTIONS: readonly SuggestionDef[] = [
  { id: 'fileReady', group: 'fileCheck', agentId: FILE_CHECKER_ID },
  { id: 'fileMissingHinglish', group: 'fileCheck', agentId: FILE_CHECKER_ID },
  { id: 'filePan', group: 'fileCheck', agentId: FILE_CHECKER_ID },
  { id: 'fileSalary', group: 'fileCheck', agentId: FILE_CHECKER_ID },
  { id: 'fileEmis', group: 'fileCheck', agentId: FILE_CHECKER_ID },
  // The AI Assistant answers eligibility and EMI questions with the
  // deterministic eligibility and EMI tools (never its own arithmetic).
  { id: 'eligibilityBest', group: 'eligibility', agentId: null },
  { id: 'eligibilityWhyNot', group: 'eligibility', agentId: null },
  { id: 'cibilSummary', group: 'eligibility', agentId: null },
  { id: 'emiSplit', group: 'eligibility', agentId: null },
  { id: 'balanceTransfer', group: 'eligibility', agentId: null },
  { id: 'reminderWhatsapp', group: 'reminders', agentId: REMINDER_WRITER_ID },
  { id: 'reminderSms', group: 'reminders', agentId: REMINDER_WRITER_ID },
  // Varunika Loans' website guide, asked the way customers write (Hinglish).
  { id: 'customerDocs', group: 'customer', agentId: LOAN_ASSISTANT_ID },
  { id: 'customerEmi', group: 'customer', agentId: LOAN_ASSISTANT_ID },
  { id: 'customerEligibility', group: 'customer', agentId: LOAN_ASSISTANT_ID },
  { id: 'callScore', group: 'calls', agentId: CALL_QA_ID },
  { id: 'callCompliance', group: 'calls', agentId: CALL_QA_ID },
  { id: 'callSummary', group: 'calls', agentId: CALL_QA_ID },
  { id: 'callCoaching', group: 'calls', agentId: CALL_QA_ID },
  { id: 'voice', group: 'voice', agentId: null },
  { id: 'why', group: 'about', agentId: null },
];

/** Chips whose question is built from facts (the chip shows chat.chips.<id>Label). */
const BUILT_PROMPTS = new Set(['voice', 'why']);

export type ProjectKind = 'calls' | 'loanFile';

const GROUP_ORDER: Record<ProjectKind, readonly SuggestionGroup[]> = {
  loanFile: [
    'fileCheck',
    'eligibility',
    'reminders',
    'customer',
    'calls',
    'voice',
    'about',
  ],
  calls: [
    'calls',
    'voice',
    'about',
    'customer',
    'fileCheck',
    'eligibility',
    'reminders',
  ],
};

// Questions about a loan file: not offered up front in a call project.
const LOAN_FILE_GROUPS: ReadonlySet<SuggestionGroup> = new Set([
  'fileCheck',
  'eligibility',
  'reminders',
]);

// Audio / video, as the file-check engine tells call recordings apart.
const RECORDING_MIME = /^(audio|video)\//i;
const RECORDING_EXTENSION =
  /\.(wav|mp3|m4a|aac|ogg|oga|opus|flac|amr|wma|mp4|m4v|mov|webm|mkv|avi|3gp)$/i;

export function isRecording(
  doc: Pick<Document, 'name' | 'file_type'>,
): boolean {
  const mime = (doc.file_type ?? '').split(';')[0].trim();
  if (RECORDING_MIME.test(mime)) return true;
  return (
    (mime === '' || mime === 'application/octet-stream') &&
    RECORDING_EXTENSION.test(doc.name ?? '')
  );
}

/** 'calls' when at least half of the project's documents are call recordings. */
export function projectKind(
  documents: Pick<Document, 'name' | 'file_type'>[],
): ProjectKind {
  const recordings = documents.filter(isRecording).length;
  return recordings > 0 && recordings * 2 >= documents.length
    ? 'calls'
    : 'loanFile';
}

export interface ChatSuggestion {
  id: string;
  group: SuggestionGroup;
  /** A built-in agent id; null: the default AI Assistant. */
  agentId: string | null;
  /** The agent's name as the API lists it. */
  agentName: string;
  label: string;
  /** What goes into the input. */
  prompt: string;
}

export interface ChatSuggestions {
  kind: ProjectKind;
  /** On the welcome screen: one or two questions per part of the app the project uses. */
  primary: ChatSuggestion[];
  /** Behind "More suggestions", in the same order. */
  more: ChatSuggestion[];
}

function promptOf(
  t: TFunction,
  def: SuggestionDef,
  voiceChatAvailable: boolean,
): string {
  if (def.id === 'voice') {
    // What is live and what is not, so the answer cannot overstate it.
    return [
      t('chat.chips.voicePrompt'),
      t('chat.chips.voiceFactCallQa'),
      voiceChatAvailable ? t('chat.chips.voiceFactVoiceChat') : null,
      t('chat.chips.voiceFactBot'),
      t('chat.chips.voiceFactLine'),
    ]
      .filter(Boolean)
      .join('\n- ');
  }
  if (def.id === 'why') {
    // The "Why DSAs need this" points, so the answer stays within them.
    return [
      t('chat.chips.whyPrompt'),
      ...DSA_PAIN_POINTS.map((p) => `${p.title}: ${p.feature}`),
    ].join('\n- ');
  }
  return t(`chat.chips.${def.id}`);
}

/**
 * The welcome screen's chips, in the order the project calls for. Up front:
 * two questions of the leading group and one of every other group the
 * project uses (no Call QA in a loan file without recordings, no loan-file
 * questions in a call project); everything else is under "More suggestions".
 * A chip of a built-in agent the project does not list is left out, and so
 * is any chip of another agent when the agent cannot be changed here.
 */
export function chatSuggestions({
  agents,
  documents,
  voiceChatAvailable = false,
  selectedAgentId = null,
  canSelectAgent = true,
  t,
}: {
  agents: Pick<Agent, 'agent_id' | 'name'>[];
  documents: Pick<Document, 'name' | 'file_type'>[];
  voiceChatAvailable?: boolean;
  /** agent_id of the selected agent; null: the AI Assistant. */
  selectedAgentId?: string | null;
  canSelectAgent?: boolean;
  t: TFunction;
}): ChatSuggestions {
  const names = new Map(agents.map((a) => [a.agent_id, a.name]));
  const kind = projectKind(documents);
  const hasRecordings = documents.some(isRecording);
  const upFront = (group: SuggestionGroup) =>
    kind === 'calls'
      ? !LOAN_FILE_GROUPS.has(group)
      : group !== 'calls' || hasRecordings;

  const ordered = GROUP_ORDER[kind].flatMap((group) =>
    SUGGESTIONS.filter(
      (def) =>
        def.group === group &&
        (def.agentId === null || names.has(def.agentId)) &&
        (canSelectAgent || def.agentId === selectedAgentId),
    ).map(
      (def): ChatSuggestion => ({
        id: def.id,
        group: def.group,
        agentId: def.agentId,
        agentName:
          def.agentId === null
            ? t('agent.default')
            : (names.get(def.agentId) ?? def.agentId),
        label: t(
          BUILT_PROMPTS.has(def.id)
            ? `chat.chips.${def.id}Label`
            : `chat.chips.${def.id}`,
        ),
        prompt: promptOf(t, def, voiceChatAvailable),
      }),
    ),
  );

  // With none of those groups left (agents missing), the first group leads.
  const offered = ordered.some((s) => upFront(s.group)) ? upFront : () => true;
  const lead = ordered.find((s) => offered(s.group))?.group;
  const shown = new Map<SuggestionGroup, number>();
  const primary: ChatSuggestion[] = [];
  const more: ChatSuggestion[] = [];
  for (const s of ordered) {
    const n = shown.get(s.group) ?? 0;
    const room = s.group === lead ? 2 : 1;
    if (offered(s.group) && n < room) {
      shown.set(s.group, n + 1);
      primary.push(s);
    } else {
      more.push(s);
    }
  }
  return { kind, primary, more };
}

/** The parts of the chat a chip drives (ChatPanel). */
export interface SuggestionTarget {
  /** agent_id of the selected agent; null: the AI Assistant. */
  selectedAgentId: string | null;
  /** Starts a new chat with the agent, as the agent menu does. */
  onAgentSelect?: (agentId: string | null) => void;
  onInputChange: (value: string) => void;
  /** The chat's contenteditable input; sending reads its text. */
  input: Pick<HTMLElement, 'textContent' | 'focus'> | null;
}

/** Moves the caret to the end of the input, where typing continues. */
function caretToEnd(input: SuggestionTarget['input']): void {
  if (!input || typeof window === 'undefined' || !window.getSelection) return;
  const selection = window.getSelection();
  if (!selection || !(input instanceof Node)) return;
  const range = document.createRange();
  range.selectNodeContents(input);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
}

/**
 * A chip click: select the chip's agent (only when it is not the selected
 * one, since a change starts a new chat) and put its question in the input,
 * ready to edit or send with Enter.
 */
export function applySuggestion(
  suggestion: Pick<ChatSuggestion, 'agentId' | 'prompt'>,
  target: SuggestionTarget,
): void {
  if (suggestion.agentId !== target.selectedAgentId) {
    target.onAgentSelect?.(suggestion.agentId);
  }
  if (target.input) {
    target.input.textContent = suggestion.prompt;
    target.input.focus();
    caretToEnd(target.input);
  }
  target.onInputChange(suggestion.prompt);
}
