import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Lightbulb, X } from 'lucide-react';
import {
  DSA_PAIN_POINTS,
  SERIES_NOTE,
  type DsaPainPoint,
  type PainPointId,
  type PainPointTarget,
} from '../../data/dsaPainPoints';

const SHOW_ME_KEYS: Record<PainPointTarget, string> = {
  'file-check': 'painPoints.showMe.fileCheck',
  ask: 'painPoints.showMe.ask',
  obligations: 'painPoints.showMe.obligations',
  retention: 'painPoints.showMe.retention',
};

function Card({
  card,
  highlighted,
  onShowMe,
}: {
  card: DsaPainPoint;
  highlighted: boolean;
  onShowMe: (target: PainPointTarget) => void;
}) {
  const { t } = useTranslation();
  return (
    <li
      data-card={card.id}
      className={`scroll-mt-3 space-y-2 rounded-xl border p-3 transition-shadow ${
        highlighted
          ? 'border-violet-400 bg-violet-50/70 ring-2 ring-violet-400/60 dark:border-violet-500/70 dark:bg-violet-900/20'
          : 'border-white/50 bg-white/30 dark:border-white/[0.08] dark:bg-white/[0.03]'
      }`}
    >
      <h4 className="text-sm font-semibold leading-snug text-slate-800 dark:text-slate-100">
        {card.title}
      </h4>
      <dl className="space-y-1 text-[11px] leading-snug">
        <div>
          <dt className="font-semibold text-slate-600 dark:text-slate-300">
            {t('painPoints.forDsa')}
          </dt>
          <dd className="text-slate-700 dark:text-slate-200">{card.forDsa}</dd>
        </div>
        <div>
          <dt className="font-semibold text-slate-600 dark:text-slate-300">
            {t('painPoints.feature')}
          </dt>
          <dd className="text-slate-700 dark:text-slate-200">{card.feature}</dd>
        </div>
        {card.correction && (
          <div className="rounded-md border border-amber-200 bg-amber-50/70 px-2 py-1 dark:border-amber-800/50 dark:bg-amber-900/20">
            <dt className="font-semibold text-amber-800 dark:text-amber-300">
              {t('painPoints.correction')}
            </dt>
            <dd className="text-amber-900 dark:text-amber-200">
              {card.correction}
            </dd>
          </div>
        )}
      </dl>
      <button
        type="button"
        onClick={() => onShowMe(card.showMe)}
        className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 dark:hover:bg-emerald-500"
      >
        {t(SHOW_ME_KEYS[card.showMe])}
        <ArrowRight className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}

interface DsaPainPointsPanelProps {
  onClose: () => void;
  /** Opens the File Check panel at the part that shows the card's feature. */
  onShowMe: (target: PainPointTarget) => void;
  /** Card to scroll to and highlight (from a tag on a verdict finding). */
  focusId?: PainPointId | null;
}

/** "Why DSAs need this": overlays the side panel, like the File Check panel. */
export default function DsaPainPointsPanel({
  onClose,
  onShowMe,
  focusId,
}: DsaPainPointsPanelProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!focusId) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-card="${focusId}"]`,
    );
    el?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }, [focusId]);

  return (
    <div
      className="artifact-viewer-container absolute inset-0 z-10 flex flex-col border border-white/60 dark:border-indigo-500/20 rounded-xl overflow-hidden animate-fade-in"
      role="region"
      aria-label={t('painPoints.title')}
    >
      <div className="flex items-center gap-3 px-4 py-3 border-b border-black/[0.08] dark:border-white/[0.08] bg-transparent dark:bg-white/[0.05] flex-shrink-0">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 flex-shrink-0">
          <Lightbulb className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200 truncate">
            {t('painPoints.title')}
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            {t('painPoints.subtitle')}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-2 rounded-lg text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/10 transition-colors flex-shrink-0"
          title={t('common.close', 'Close')}
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 px-4 py-3 space-y-3">
        <p className="text-[11px] leading-snug text-slate-500 dark:text-slate-400">
          {SERIES_NOTE}
        </p>
        <ul ref={listRef} className="space-y-2.5">
          {DSA_PAIN_POINTS.map((card) => (
            <Card
              key={card.id}
              card={card}
              highlighted={card.id === focusId}
              onShowMe={onShowMe}
            />
          ))}
        </ul>
      </div>

      <p className="px-4 py-2 border-t border-black/[0.08] dark:border-white/[0.08] text-[10px] leading-snug text-slate-500 dark:text-slate-400 flex-shrink-0">
        {t('painPoints.footer')}
      </p>
    </div>
  );
}
