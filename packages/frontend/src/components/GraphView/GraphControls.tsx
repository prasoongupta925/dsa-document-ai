import { useState, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, SlidersHorizontal, FileText } from 'lucide-react';
import AlertModal from '../AlertModal';

type GraphQueryMode = 'range' | 'page' | 'search';

interface GraphControlsProps {
  mode: GraphQueryMode;
  onModeChange: (mode: GraphQueryMode) => void;
  totalSegments: number;
  pageRange: [number, number];
  onPageRangeChange: (range: [number, number]) => void;
  specificPage: number;
  onSpecificPageChange: (page: number) => void;
  searchTerm: string;
  onSearchSubmit: (term: string) => void;
  onApply: () => void;
  loading?: boolean;
}

const MAX_RANGE = 100;

export default function GraphControls({
  mode,
  onModeChange,
  totalSegments,
  pageRange,
  onPageRangeChange,
  specificPage,
  onSpecificPageChange,
  searchTerm,
  onSearchSubmit,
  onApply,
  loading,
}: GraphControlsProps) {
  const { t } = useTranslation();
  const [localSearch, setLocalSearch] = useState(searchTerm);
  const [localFrom, setLocalFrom] = useState(String(pageRange[0]));
  const [localTo, setLocalTo] = useState(String(pageRange[1]));
  const [localPage, setLocalPage] = useState(String(specificPage));
  const [alertOpen, setAlertOpen] = useState(false);

  const maxPage = Math.max(1, totalSegments);

  // Sync local state when props change externally
  const rangeFrom = pageRange[0];
  const rangeTo = pageRange[1];
  useEffect(() => setLocalSearch(searchTerm), [searchTerm]);
  useEffect(() => setLocalFrom(String(rangeFrom)), [rangeFrom]);
  useEffect(() => setLocalTo(String(rangeTo)), [rangeTo]);
  useEffect(() => setLocalPage(String(specificPage)), [specificPage]);

  const validateRange = useCallback((): [number, number] => {
    let from = parseInt(localFrom, 10);
    let to = parseInt(localTo, 10);
    if (isNaN(from) || from < 1) from = 1;
    if (isNaN(to) || to < 1) to = totalSegments;
    if (from > totalSegments) from = totalSegments;
    if (to > totalSegments) to = totalSegments;
    if (from >= to) {
      if (from < totalSegments) {
        to = from + 1;
      } else {
        from = totalSegments - 1;
        to = totalSegments;
      }
    }
    if (from < 1) from = 1;
    return [from, to];
  }, [localFrom, localTo, totalSegments]);

  const commitRange = useCallback(() => {
    const [from, to] = validateRange();
    setLocalFrom(String(from));
    setLocalTo(String(to));
    onPageRangeChange([from, to]);
  }, [validateRange, onPageRangeChange]);

  const applyRange = useCallback(() => {
    const [from, to] = validateRange();
    if (to - from + 1 > MAX_RANGE) {
      setLocalFrom(String(from));
      setLocalTo(String(to));
      setAlertOpen(true);
      return;
    }
    setLocalFrom(String(from));
    setLocalTo(String(to));
    onPageRangeChange([from, to]);
    onApply();
  }, [validateRange, onPageRangeChange, onApply]);

  const commitPage = useCallback(() => {
    let page = parseInt(localPage, 10) || 1;
    page = Math.max(1, Math.min(page, maxPage));
    setLocalPage(String(page));
    onSpecificPageChange(page);
  }, [localPage, maxPage, onSpecificPageChange]);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && localSearch.trim()) {
        onSearchSubmit(localSearch.trim());
        onApply();
      }
    },
    [localSearch, onSearchSubmit, onApply],
  );

  const modes: {
    key: GraphQueryMode;
    icon: typeof SlidersHorizontal;
    label: string;
  }[] = [
    {
      key: 'range',
      icon: SlidersHorizontal,
      label: t('workflow.graph.modeRange', 'Range'),
    },
    {
      key: 'page',
      icon: FileText,
      label: t('workflow.graph.modePage', 'Page'),
    },
    {
      key: 'search',
      icon: Search,
      label: t('workflow.graph.modeSearch', 'Search'),
    },
  ];

  return (
    <>
      <AlertModal
        isOpen={alertOpen}
        onClose={() => setAlertOpen(false)}
        title={t('workflow.graph.rangeLimitTitle', 'Range Too Large')}
        message={t('workflow.graph.rangeLimitMessage', {
          max: MAX_RANGE,
          defaultValue: `Please select up to ${MAX_RANGE} pages at a time.`,
        })}
        variant="warning"
        transparentBackdrop
      />
      <div className="flex items-center gap-2 px-2 py-1.5 bg-slate-100/90 dark:bg-slate-800/90 backdrop-blur-sm border-b border-slate-200 dark:border-slate-700/50 text-xs">
        {/* Mode selector */}
        <div className="flex gap-0.5 bg-slate-200/70 dark:bg-slate-700/50 rounded-md p-0.5">
          {modes.map(({ key, icon: Icon, label }) => (
            <button
              key={key}
              onClick={() => {
                onModeChange(key);
                if (key === 'range') {
                  applyRange();
                } else if (key !== 'search') {
                  onApply();
                }
              }}
              className={`flex items-center gap-1 px-2 py-1 rounded transition-all cursor-pointer ${
                mode === key
                  ? 'bg-white dark:bg-slate-600 text-slate-800 dark:text-white shadow-sm'
                  : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
              }`}
            >
              <Icon className="h-3 w-3" />
              {label}
            </button>
          ))}
        </div>

        {/* Mode-specific controls */}
        <div className="flex-1 flex items-center gap-2">
          {mode === 'range' && (
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                inputMode="numeric"
                value={localFrom}
                onChange={(e) =>
                  setLocalFrom(e.target.value.replace(/[^0-9]/g, ''))
                }
                onBlur={commitRange}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    applyRange();
                  }
                }}
                className="w-14 px-1 py-0.5 bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 rounded text-slate-700 dark:text-slate-200 text-center"
                disabled={loading}
              />
              <span className="text-slate-400">-</span>
              <input
                type="text"
                inputMode="numeric"
                value={localTo}
                onChange={(e) =>
                  setLocalTo(e.target.value.replace(/[^0-9]/g, ''))
                }
                onBlur={commitRange}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    applyRange();
                  }
                }}
                className="w-14 px-1 py-0.5 bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 rounded text-slate-700 dark:text-slate-200 text-center"
                disabled={loading}
              />
              <span className="text-slate-400 dark:text-slate-500">
                / {totalSegments}
              </span>
              <button
                onClick={applyRange}
                disabled={loading}
                className="px-2 py-0.5 bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors disabled:opacity-50 cursor-pointer"
              >
                {t('workflow.graph.apply', 'Apply')}
              </button>
            </div>
          )}

          {mode === 'page' && (
            <div className="flex items-center gap-1.5">
              <span className="text-slate-500 dark:text-slate-400">
                {t('workflow.graph.page', 'Page')}
              </span>
              <input
                type="text"
                inputMode="numeric"
                value={localPage}
                onChange={(e) =>
                  setLocalPage(e.target.value.replace(/[^0-9]/g, ''))
                }
                onBlur={commitPage}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    commitPage();
                    onApply();
                  }
                }}
                className="w-16 px-1.5 py-0.5 bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 rounded text-slate-700 dark:text-slate-200 text-center"
                disabled={loading}
              />
              <span className="text-slate-400 dark:text-slate-500">
                / {maxPage}
              </span>
              <button
                onClick={() => {
                  commitPage();
                  onApply();
                }}
                disabled={loading}
                className="px-2 py-0.5 bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors disabled:opacity-50 cursor-pointer"
              >
                {t('workflow.graph.apply', 'Apply')}
              </button>
            </div>
          )}

          {mode === 'search' && (
            <div className="flex items-center gap-1.5 flex-1">
              <div className="relative flex-1 max-w-xs">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-slate-400 dark:text-slate-500" />
                <input
                  type="text"
                  value={localSearch}
                  onChange={(e) => setLocalSearch(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder={t(
                    'workflow.graph.searchPlaceholder',
                    'Search entities...',
                  )}
                  className="w-full pl-7 pr-2 py-0.5 bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 rounded text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500"
                  disabled={loading}
                />
              </div>
              <button
                onClick={() => {
                  if (localSearch.trim()) {
                    onSearchSubmit(localSearch.trim());
                    onApply();
                  }
                }}
                disabled={loading || !localSearch.trim()}
                className="px-2 py-0.5 bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors disabled:opacity-50 cursor-pointer"
              >
                {t('workflow.graph.searchButton', 'Search')}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export type { GraphQueryMode };
