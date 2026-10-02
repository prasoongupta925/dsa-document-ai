import {
  useEffect,
  useId,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type Ref,
} from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Eraser,
  Loader2,
  MessageSquareText,
  RotateCcw,
  SendHorizontal,
} from 'lucide-react';
import type {
  FileCheckAskPricing,
  FileCheckUsage,
} from '../../types/fileCheck';
import type { FileCheckAskState } from '../../hooks/useFileCheckAsk';
import { apiErrorStatus } from '../../lib/fileCheck';
import {
  ASK_MAX_QUESTION_LENGTH,
  RETENTION_DAYS,
  costUsd,
  formatPrice,
  formatTokens,
  formatUsd,
  type AskSessionTotals,
  type AskTurn,
} from '../../lib/fileCheckAsk';
import PainPointTag from './PainPointTag';

/** The four Plan B suggestions; the first names the selected checklist. */
export function askSuggestions(t: TFunction, checklistName: string): string[] {
  return [
    t('fileCheck.ask.suggestions.complete', { checklist: checklistName }),
    t('fileCheck.ask.suggestions.salary'),
    t('fileCheck.ask.suggestions.pan'),
    t('fileCheck.ask.suggestions.obligations'),
  ];
}

function describeAskError(t: TFunction, error: unknown): string {
  const status = apiErrorStatus(error);
  if (status === 400) return t('fileCheck.errors.unknownChecklist');
  if (status === 404) return t('fileCheck.ask.errors.notFound');
  if (status === 422) return t('fileCheck.ask.errors.invalid');
  if (status === 429) return t('fileCheck.ask.errors.throttled');
  if (status === 503) return t('fileCheck.ask.errors.notConfigured');
  if (status !== null && status >= 500) {
    return t('fileCheck.ask.errors.failed', { status });
  }
  if (status !== null) return t('fileCheck.errors.rejected', { status });
  return error instanceof Error ? error.message : String(error);
}

interface AskMeterProps {
  session: AskSessionTotals;
  pricing: FileCheckAskPricing;
  modelId: string | null;
  usage: FileCheckUsage | null;
  usageError?: unknown;
}

/**
 * The footer meter: this session's tokens and cost (tokens × the API's
 * per-1M prices), the 7-day spend from GET .../usage, and the pricing.
 */
export function AskMeter({
  session,
  pricing,
  modelId,
  usage,
  usageError,
}: AskMeterProps) {
  const { t } = useTranslation();
  const sessionCost = costUsd(
    session.input_tokens,
    session.output_tokens,
    pricing,
  );
  const parts = [t('fileCheck.ask.meter.answeredBy')];
  if (modelId) {
    parts.push(
      pricing.region
        ? t('fileCheck.ask.meter.model', {
            model: modelId,
            region: pricing.region,
          })
        : t('fileCheck.ask.meter.modelNoRegion', { model: modelId }),
    );
  }
  parts.push(
    t('fileCheck.ask.meter.session', {
      input: formatTokens(session.input_tokens),
      output: formatTokens(session.output_tokens),
      cost: formatUsd(sessionCost),
    }),
  );
  parts.push(
    usage
      ? t('fileCheck.ask.meter.window', {
          days: usage.window_days,
          cost: formatUsd(usage.cost_usd),
          count: usage.calls,
        })
      : usageError != null
        ? t('fileCheck.ask.meter.windowUnavailable', { days: RETENTION_DAYS })
        : t('fileCheck.ask.meter.windowLoading', { days: RETENTION_DAYS }),
  );
  parts.push(
    t('fileCheck.ask.meter.pricing', {
      input: formatPrice(pricing.input_per_million_usd),
      output: formatPrice(pricing.output_per_million_usd),
    }),
  );
  parts.push(t('fileCheck.ask.meter.retention', { days: RETENTION_DAYS }));
  parts.push(t('fileCheck.ask.meter.synthetic'));
  return (
    <p
      data-focus="retention"
      data-testid="ask-meter"
      className="scroll-mt-3 rounded-lg border border-black/[0.06] bg-white/30 px-2.5 py-1.5 text-[10px] leading-snug text-slate-500 dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-slate-400"
    >
      {parts.join(' · ')}
    </p>
  );
}

function AnswerCaption({ turn }: { turn: AskTurn }) {
  const { t } = useTranslation();
  const docs = turn.grounded_on?.documents ?? [];
  return (
    <p
      className="mt-1 text-[10px] text-slate-500 dark:text-slate-400"
      data-testid="ask-caption"
    >
      {t('fileCheck.ask.caption', {
        input: formatTokens(turn.input_tokens),
        output: formatTokens(turn.output_tokens),
        cost: formatUsd(turn.cost_usd),
      })}
      {docs.length > 0 && (
        <span title={docs.join(', ')}>
          {' · '}
          {t('fileCheck.ask.groundedOn', { count: docs.length })}
        </span>
      )}
    </p>
  );
}

const MARKDOWN_CLASS =
  'prose prose-sm dark:prose-invert max-w-none text-xs text-slate-700 dark:text-slate-200 [&_p]:my-1 [&_p]:leading-snug [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0 [&_strong]:!text-inherit [&_table]:text-[11px]';

function Turn({
  turn,
  onRetry,
  retryDisabled,
}: {
  turn: AskTurn;
  onRetry: (question: string) => void;
  retryDisabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <li className="space-y-1.5" data-status={turn.status}>
      <div className="flex justify-end">
        <p className="max-w-[90%] whitespace-pre-wrap break-words rounded-lg rounded-br-sm bg-emerald-600 px-2.5 py-1.5 text-xs text-white">
          {turn.question}
        </p>
      </div>
      {turn.status === 'pending' && (
        <p className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-500" />
          {t('fileCheck.ask.thinking')}
        </p>
      )}
      {turn.status === 'done' && (
        <div className="rounded-lg rounded-bl-sm border border-white/50 bg-white/40 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.04]">
          <div className={MARKDOWN_CLASS}>
            <ReactMarkdown
              remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
              components={{
                a: ({ children, href }) => (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {turn.answer ?? ''}
            </ReactMarkdown>
          </div>
          <AnswerCaption turn={turn} />
        </div>
      )}
      {turn.status === 'error' && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
        >
          <p className="font-semibold">{t('fileCheck.ask.failed')}</p>
          <p className="mt-0.5 break-words">
            {describeAskError(t, turn.error)}
          </p>
          <p className="mt-0.5 text-red-600/80 dark:text-red-300/80">
            {t('fileCheck.ask.verdictStillValid')}
          </p>
          <button
            type="button"
            onClick={() => onRetry(turn.question)}
            disabled={retryDisabled}
            className="mt-1 inline-flex items-center gap-1 font-medium hover:underline disabled:opacity-50"
          >
            <RotateCcw className="h-3 w-3" />
            {t('fileCheck.retry')}
          </button>
        </div>
      )}
    </li>
  );
}

interface AskSectionProps {
  state: FileCheckAskState;
  /** Name of the selected checklist (the first suggestion names it). */
  checklistName: string;
  checklistId?: string;
  applicant?: string;
  inputRef?: Ref<HTMLTextAreaElement>;
}

/** "Ask about this file": Plan B's grounded Q&A, below the verdict. */
export default function AskSection({
  state,
  checklistName,
  checklistId,
  applicant,
  inputRef,
}: AskSectionProps) {
  const { t } = useTranslation();
  const inputId = useId();
  const [draft, setDraft] = useState('');
  const {
    turns,
    pending,
    ask,
    clear,
    session,
    pricing,
    modelId,
    usage,
    usageError,
    loadUsage,
  } = state;

  useEffect(() => {
    if (!usage && usageError == null) loadUsage();
  }, [usage, usageError, loadUsage]);

  const send = (question: string) => {
    const q = question.trim();
    if (!q || pending) return;
    ask(q, { checklistId, applicant });
    setDraft('');
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(draft);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(draft);
    }
  };

  const suggestions = askSuggestions(
    t,
    checklistName || t('fileCheck.ask.thisChecklist'),
  );

  return (
    <section
      aria-label={t('fileCheck.ask.title')}
      data-focus="ask"
      className="scroll-mt-3 space-y-2 rounded-xl border border-white/50 bg-white/20 p-3 dark:border-white/[0.08] dark:bg-white/[0.02]"
    >
      <header className="flex flex-wrap items-center gap-1.5">
        <MessageSquareText
          className="h-4 w-4 text-emerald-600 dark:text-emerald-400"
          aria-hidden="true"
        />
        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          {t('fileCheck.ask.title')}
        </h4>
        <PainPointTag id="deciding-not-extracting" />
        {turns.length > 0 && (
          <button
            type="button"
            onClick={clear}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-slate-200"
            title={t('fileCheck.ask.clearTitle')}
          >
            <Eraser className="h-3 w-3" />
            {t('fileCheck.ask.clear')}
          </button>
        )}
      </header>
      <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
        {t('fileCheck.ask.hint')}
      </p>

      <div className="flex flex-wrap gap-1.5" data-testid="ask-suggestions">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => send(s)}
            disabled={pending}
            className="rounded-full border border-emerald-200 bg-emerald-50/70 px-2 py-1 text-left text-[11px] leading-snug text-emerald-800 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-800/50 dark:bg-emerald-900/20 dark:text-emerald-300 dark:hover:bg-emerald-900/40"
          >
            {s}
          </button>
        ))}
      </div>

      {turns.length > 0 && (
        <ul className="space-y-2.5" aria-live="polite">
          {turns.map((turn) => (
            <Turn
              key={turn.id}
              turn={turn}
              onRetry={send}
              retryDisabled={pending}
            />
          ))}
        </ul>
      )}

      <form onSubmit={handleSubmit} className="space-y-1">
        <label htmlFor={inputId} className="sr-only">
          {t('fileCheck.ask.inputLabel')}
        </label>
        <div className="flex items-end gap-1.5">
          <textarea
            id={inputId}
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            maxLength={ASK_MAX_QUESTION_LENGTH}
            placeholder={t('fileCheck.ask.placeholder')}
            className="min-h-[2.5rem] flex-1 resize-y rounded-lg border border-black/10 bg-white/40 px-2.5 py-1.5 text-xs text-[#0f172a] placeholder:text-slate-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-[#3b4264] dark:bg-[#0d1117] dark:text-[#f1f5f9]"
          />
          <button
            type="submit"
            disabled={pending || !draft.trim()}
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:opacity-50 dark:hover:bg-emerald-500"
            title={t('fileCheck.ask.send')}
            aria-label={t('fileCheck.ask.send')}
          >
            {pending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <SendHorizontal className="h-4 w-4" />
            )}
          </button>
        </div>
        {draft.length > ASK_MAX_QUESTION_LENGTH - 100 && (
          <p className="text-right text-[10px] text-slate-500 dark:text-slate-400">
            {draft.length} / {ASK_MAX_QUESTION_LENGTH}
          </p>
        )}
      </form>

      <AskMeter
        session={session}
        pricing={pricing}
        modelId={modelId}
        usage={usage}
        usageError={usageError}
      />
    </section>
  );
}
