import { LayoutGrid, ListTree } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ProjectsView } from '../../lib/projectTree';

const OPTIONS = [
  { view: 'tree', Icon: ListTree, label: 'projects.tree.viewTree' },
  { view: 'cards', Icon: LayoutGrid, label: 'projects.tree.viewCards' },
] as const;

/** "Tree | Cards" toggle of the projects page. */
export default function ViewSwitch({
  view,
  onChange,
}: {
  view: ProjectsView;
  onChange: (view: ProjectsView) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t('projects.tree.viewLabel')}
      className="inline-flex rounded-lg border border-white/60 bg-white/50 p-0.5 backdrop-blur-sm dark:border-[var(--color-border)] dark:bg-[var(--color-bg-secondary)] dark:backdrop-blur-none"
    >
      {OPTIONS.map(({ view: option, Icon, label }) => (
        <button
          key={option}
          type="button"
          aria-pressed={view === option}
          onClick={() => onChange(option)}
          className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 ${
            view === option
              ? 'bg-blue-600 text-white shadow-sm dark:bg-blue-500'
              : 'text-slate-600 hover:bg-white/70 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white'
          }`}
        >
          <Icon aria-hidden="true" className="h-4 w-4" />
          {t(label)}
        </button>
      ))}
    </div>
  );
}
