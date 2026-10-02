import { createContext, useContext } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import { PAIN_POINTS_BY_ID, type PainPointId } from '../../data/dsaPainPoints';

/** Opens the "Why DSAs need this" panel at a card; null renders plain tags. */
export const PainPointOpenContext = createContext<
  ((id: PainPointId) => void) | null
>(null);

const TAG_CLASS =
  'inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:border-violet-700/50 dark:bg-violet-900/30 dark:text-violet-300';

/** Small tag linking a verdict finding to the DSA pain point it illustrates. */
export default function PainPointTag({ id }: { id: PainPointId }) {
  const { t } = useTranslation();
  const open = useContext(PainPointOpenContext);
  const card = PAIN_POINTS_BY_ID[id];
  if (!card) return null;
  const title = t('painPoints.tagTitle', { title: card.title });
  const content = (
    <>
      <Lightbulb className="h-2.5 w-2.5 flex-shrink-0" aria-hidden="true" />
      {card.tag}
    </>
  );
  return open ? (
    <button
      type="button"
      onClick={() => open(id)}
      title={title}
      data-pain-point={id}
      className={`${TAG_CLASS} hover:bg-violet-100 dark:hover:bg-violet-900/50 transition-colors`}
    >
      {content}
    </button>
  ) : (
    <span title={title} data-pain-point={id} className={TAG_CLASS}>
      {content}
    </span>
  );
}
