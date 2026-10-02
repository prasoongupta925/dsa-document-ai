import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Calculator,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  Headphones,
  Languages,
  Lightbulb,
  MessageCircle,
  PhoneCall,
  type LucideIcon,
} from 'lucide-react';
import type {
  ChatSuggestion,
  ChatSuggestions,
  SuggestionGroup,
} from './suggestions';

const GROUP_ICONS: Record<SuggestionGroup, LucideIcon> = {
  fileCheck: ClipboardCheck,
  eligibility: Calculator,
  reminders: MessageCircle,
  customer: Languages,
  calls: Headphones,
  voice: PhoneCall,
  about: Lightbulb,
};

const CHIP_CLASS =
  'inline-flex max-w-full items-start gap-1.5 rounded-2xl border px-3 py-1.5 text-left text-xs leading-snug transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400';
const IDLE_CLASS =
  'border-white/40 bg-white/30 text-slate-700 backdrop-blur-sm hover:border-white/70 hover:bg-white/60 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200 dark:hover:border-slate-600 dark:hover:bg-slate-700/70';
const ACTIVE_CLASS =
  'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900/30 dark:text-blue-300';

function Chip({
  suggestion,
  active,
  onPick,
}: {
  suggestion: ChatSuggestion;
  active: boolean;
  onPick: (suggestion: ChatSuggestion) => void;
}) {
  const { t } = useTranslation();
  const Icon = GROUP_ICONS[suggestion.group];
  return (
    <button
      type="button"
      onClick={() => onPick(suggestion)}
      title={t('chat.chips.usesAgent', { agent: suggestion.agentName })}
      className={`${CHIP_CLASS} ${active ? ACTIVE_CLASS : IDLE_CLASS}`}
      data-suggestion={suggestion.id}
      data-agent-id={suggestion.agentId ?? 'default'}
      data-active={active ? 'true' : undefined}
    >
      <Icon
        className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 opacity-70"
        aria-hidden="true"
      />
      <span className="min-w-0">
        {suggestion.label}
        <span className="ml-1.5 whitespace-nowrap text-[10px] font-medium text-slate-400 dark:text-slate-500">
          · {suggestion.agentName}
        </span>
      </span>
    </button>
  );
}

interface SuggestionChipsProps {
  suggestions: ChatSuggestions;
  /** The chip whose question is in the input. */
  activeId?: string | null;
  /** Selects the chip's agent and puts its question in the input. */
  onPick: (suggestion: ChatSuggestion) => void;
  /** "More suggestions" starts open. */
  defaultExpanded?: boolean;
}

/** Ready-made questions on the chat's welcome screen (see ./suggestions). */
export default function SuggestionChips({
  suggestions,
  activeId = null,
  onPick,
  defaultExpanded = false,
}: SuggestionChipsProps) {
  const { t } = useTranslation();
  const moreId = useId();
  const [expanded, setExpanded] = useState(defaultExpanded);
  const { primary, more } = suggestions;
  if (primary.length === 0 && more.length === 0) return null;

  // "More suggestions" lists the rest under their part of the app.
  const groups: [SuggestionGroup, ChatSuggestion[]][] = [];
  for (const s of more) {
    const last = groups[groups.length - 1];
    if (last && last[0] === s.group) last[1].push(s);
    else groups.push([s.group, [s]]);
  }

  return (
    <section
      aria-label={t('chat.trySaying')}
      className="mt-6 flex w-full flex-col items-center gap-2.5"
      data-testid="chat-suggestions"
      data-kind={suggestions.kind}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {t('chat.trySaying')}
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {primary.map((s) => (
          <Chip
            key={s.id}
            suggestion={s}
            active={s.id === activeId}
            onPick={onPick}
          />
        ))}
      </div>
      {more.length > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls={expanded ? moreId : undefined}
          className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium text-slate-500 transition-colors hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 dark:text-slate-400 dark:hover:text-slate-200"
          data-testid="more-suggestions"
        >
          {expanded ? (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {expanded
            ? t('chat.chips.less')
            : t('chat.chips.more', { count: more.length })}
        </button>
      )}
      {expanded && (
        <div id={moreId} className="w-full space-y-2.5">
          {groups.map(([group, items]) => (
            <div key={group} data-group={group}>
              <p className="mb-1 text-center text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                {t(`chat.chips.groups.${group}`)}
              </p>
              <div className="flex flex-wrap justify-center gap-1.5">
                {items.map((s) => (
                  <Chip
                    key={s.id}
                    suggestion={s}
                    active={s.id === activeId}
                    onPick={onPick}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
