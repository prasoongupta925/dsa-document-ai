// Helpers for "Ask about this file" (POST /projects/{id}/file-check/ask and
// GET /projects/{id}/file-check/usage). The answer and its token counts come
// from the backend; this module only validates, sums and formats them.
import type {
  FileCheckAskMessage,
  FileCheckAskPricing,
  FileCheckAskResponse,
  FileCheckUsage,
} from '../types/fileCheck';

/** The API's question length limit. */
export const ASK_MAX_QUESTION_LENGTH = 1000;
/** The API accepts at most 6 history messages. */
export const ASK_MAX_HISTORY = 6;
/** Question/answer pairs the thread keeps on screen. */
export const ASK_MAX_THREAD = 6;
/** Longest history message sent back (older answers are context only). */
export const ASK_MAX_HISTORY_CONTENT = 1000;
/** Model the backend is configured with by default (a config value). */
export const ASK_DEFAULT_MODEL_ID = 'global.amazon.nova-2-lite-v1:0';
/** Amazon Nova 2 Lite list price, USD per 1M tokens (the API returns the live values). */
export const ASK_DEFAULT_PRICING: FileCheckAskPricing = {
  input_per_million_usd: 0.35,
  output_per_million_usd: 2.95,
  region: null,
};
/** Nothing is kept longer than this (usage window, retention). */
export const RETENTION_DAYS = 7;

export interface AskTurn {
  id: number;
  question: string;
  status: 'pending' | 'done' | 'error';
  answer?: string;
  error?: unknown;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  model_id?: string;
  grounded_on?: FileCheckAskResponse['grounded_on'];
}

export interface AskSessionTotals {
  calls: number;
  input_tokens: number;
  output_tokens: number;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === 'string')
    : [];
}

/** Validates an ask response; throws on anything but the API contract. */
export function parseAskResponse(raw: unknown): FileCheckAskResponse {
  const o =
    raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const inTok = num(o.input_tokens);
  const outTok = num(o.output_tokens);
  if (typeof o.answer !== 'string' || inTok === null || outTok === null) {
    throw new Error(
      typeof o.error === 'string'
        ? o.error
        : 'unexpected response from the ask service',
    );
  }
  const p =
    o.pricing && typeof o.pricing === 'object'
      ? (o.pricing as Record<string, unknown>)
      : {};
  const pricing: FileCheckAskPricing = {
    input_per_million_usd:
      num(p.input_per_million_usd) ?? ASK_DEFAULT_PRICING.input_per_million_usd,
    output_per_million_usd:
      num(p.output_per_million_usd) ??
      ASK_DEFAULT_PRICING.output_per_million_usd,
    region: typeof p.region === 'string' && p.region ? p.region : null,
  };
  const g =
    o.grounded_on && typeof o.grounded_on === 'object'
      ? (o.grounded_on as Record<string, unknown>)
      : {};
  return {
    answer: o.answer,
    model_id: typeof o.model_id === 'string' ? o.model_id : '',
    input_tokens: inTok,
    output_tokens: outTok,
    cost_usd: num(o.cost_usd) ?? costUsd(inTok, outTok, pricing),
    pricing,
    grounded_on: {
      applicants: strings(g.applicants),
      documents: strings(g.documents),
    },
  };
}

/** Validates a usage response; null when it is not the API contract. */
export function parseUsage(raw: unknown): FileCheckUsage | null {
  const o =
    raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null;
  if (!o) return null;
  const calls = num(o.calls);
  const cost = num(o.cost_usd);
  if (calls === null || cost === null) return null;
  return {
    window_days: num(o.window_days) ?? RETENTION_DAYS,
    calls,
    input_tokens: num(o.input_tokens) ?? 0,
    output_tokens: num(o.output_tokens) ?? 0,
    cost_usd: cost,
  };
}

/** Token cost at the per-1M prices. */
export function costUsd(
  inputTokens: number,
  outputTokens: number,
  pricing: FileCheckAskPricing = ASK_DEFAULT_PRICING,
): number {
  return (
    (inputTokens * pricing.input_per_million_usd +
      outputTokens * pricing.output_per_million_usd) /
    1_000_000
  );
}

/** Adds one answered call to the running session totals. */
export function addToSession(
  totals: AskSessionTotals,
  inputTokens: number,
  outputTokens: number,
): AskSessionTotals {
  return {
    calls: totals.calls + 1,
    input_tokens: totals.input_tokens + inputTokens,
    output_tokens: totals.output_tokens + outputTokens,
  };
}

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * The conversation sent with the next question: the last answered turns as
 * user/assistant messages, at most ASK_MAX_HISTORY. Failed turns are left out.
 */
export function buildAskHistory(
  turns: Pick<AskTurn, 'status' | 'question' | 'answer'>[],
): FileCheckAskMessage[] {
  const messages: FileCheckAskMessage[] = [];
  for (const t of turns) {
    if (t.status !== 'done' || typeof t.answer !== 'string') continue;
    messages.push(
      {
        role: 'user',
        content: clip(t.question, ASK_MAX_HISTORY_CONTENT),
      },
      {
        role: 'assistant',
        content: clip(t.answer, ASK_MAX_HISTORY_CONTENT),
      },
    );
  }
  return messages.slice(-ASK_MAX_HISTORY);
}

/** $0.0013 (4 decimals, like Plan B); a tiny non-zero cost is '<$0.0001'. */
export function formatUsd(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '–';
  if (value > 0 && value < 0.00005) return '<$0.0001';
  return `$${value.toFixed(4)}`;
}

/** $0.35 (per-1M prices, 2 decimals). */
export function formatPrice(value: number): string {
  return `$${value.toFixed(2)}`;
}

const COUNT = new Intl.NumberFormat('en-US');

export function formatTokens(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? COUNT.format(value)
    : '–';
}
