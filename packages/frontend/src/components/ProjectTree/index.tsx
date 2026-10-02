import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight,
  FileAudio,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
} from 'lucide-react';
import type { Project } from '../ProjectSettingsModal';
import type { Document } from '../../types/project';
import {
  PAGE_SIZES,
  countByProduct,
  documentProgress,
  filterTreeProjects,
  groupTreeProjects,
  pageCount,
  pageWindow,
  toTreeProjects,
  type PageSize,
  type ProductId,
  type TreeProject,
} from '../../lib/projectTree';

/** The fields of GET projects/{id}/documents the tree shows. */
type ListedDocument = Pick<
  Document,
  'document_id' | 'name' | 'status' | 'file_type' | 'file_size'
>;

type DocumentsEntry =
  | { state: 'loading' }
  | { state: 'error' }
  | { state: 'loaded'; documents: ListedDocument[] };

export interface ProjectTreeViewProps {
  projects: Project[];
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  onCreate: () => void;
  onEdit: (project: Project) => void;
  onDelete: (project: Project) => void;
}

const COLUMNS = 6;

const PRODUCT_BADGE: Record<ProductId, string> = {
  personalLoan:
    'bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300',
  homeLoan:
    'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300',
  loanAgainstProperty:
    'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300',
  businessLoan:
    'bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300',
  carLoan: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-500/15 dark:text-cyan-300',
  educationLoan:
    'bg-pink-100 text-pink-800 dark:bg-pink-500/15 dark:text-pink-300',
  balanceTransfer:
    'bg-indigo-100 text-indigo-800 dark:bg-indigo-500/15 dark:text-indigo-300',
  creditCard:
    'bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300',
  telecallerQa:
    'bg-orange-100 text-orange-800 dark:bg-orange-500/15 dark:text-orange-300',
  other: 'bg-slate-200/70 text-slate-700 dark:bg-white/10 dark:text-slate-300',
};

const PRODUCT_DOT: Record<ProductId, string> = {
  personalLoan: 'bg-blue-500',
  homeLoan: 'bg-emerald-500',
  loanAgainstProperty: 'bg-amber-500',
  businessLoan: 'bg-violet-500',
  carLoan: 'bg-cyan-500',
  educationLoan: 'bg-pink-500',
  balanceTransfer: 'bg-indigo-500',
  creditCard: 'bg-rose-500',
  telecallerQa: 'bg-orange-500',
  other: 'bg-slate-400',
};

// Same colours as the documents list of a project (SidePanel), one shade
// darker in light mode for contrast on the small badge.
const STATUS_BADGE: Record<string, string> = {
  completed:
    'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400',
  processing:
    'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400',
  in_progress:
    'bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400',
  reanalyzing:
    'bg-purple-50 text-purple-700 dark:bg-purple-900/20 dark:text-purple-400',
  failed: 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400',
  needs_user_fix:
    'bg-orange-50 text-orange-700 dark:bg-orange-900/20 dark:text-orange-400',
  uploading: 'bg-cyan-50 text-cyan-700 dark:bg-cyan-900/20 dark:text-cyan-400',
  uploaded:
    'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/20 dark:text-indigo-400',
  pending: 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-400',
};
const STATUS_BADGE_OTHER =
  'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-400';

const FIELD =
  'rounded-lg border border-white/60 bg-white/70 px-2 py-1.5 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-white';
const FOCUS_RING =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500';
const ROW_FOCUS =
  'focus-visible:bg-blue-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500 dark:focus-visible:bg-blue-500/15';
const ICON_BUTTON = `rounded-md p-1.5 text-slate-500 hover:bg-slate-200/70 dark:text-slate-400 dark:hover:bg-white/10 ${FOCUS_RING}`;
const PAGE_BUTTON = `min-w-8 rounded-md border px-2.5 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`;
const PAGE_BUTTON_IDLE =
  'border-slate-300/70 bg-white/60 text-slate-700 enabled:hover:bg-white dark:border-white/10 dark:bg-white/5 dark:text-slate-300 dark:enabled:hover:bg-white/10';
const PAGE_BUTTON_CURRENT =
  'border-blue-600 bg-blue-600 text-white dark:border-blue-500 dark:bg-blue-500';
const DOC_ROW = `border-t border-slate-200/50 bg-slate-50/60 text-[13px] dark:border-white/[0.04] dark:bg-black/20 ${ROW_FOCUS}`;
const DOC_CELL = 'py-1.5 pl-10 pr-2 sm:pl-20 sm:pr-3';

function toggled<T>(set: ReadonlySet<T>, item: T): ReadonlySet<T> {
  const next = new Set(set);
  if (!next.delete(item)) next.add(item);
  return next;
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const levelOf = (row: Element) => Number(row.getAttribute('aria-level')) || 0;

/** Moves focus to another row of the tree; false when there is none. */
function focusRow(
  row: HTMLTableRowElement,
  to: 'next' | 'previous' | 'first' | 'last' | 'parent' | 'child',
): boolean {
  const rows = Array.from(
    row
      .closest('tbody')
      ?.querySelectorAll<HTMLTableRowElement>('tr[data-tree-row]') ?? [],
  );
  const index = rows.indexOf(row);
  const level = levelOf(row);
  let target: HTMLTableRowElement | undefined;
  if (to === 'next') target = rows[index + 1];
  else if (to === 'previous') target = rows[index - 1];
  else if (to === 'first') target = rows[0];
  else if (to === 'last') target = rows[rows.length - 1];
  else if (to === 'child') {
    const next = rows[index + 1];
    target = next && levelOf(next) > level ? next : undefined;
  } else {
    target = rows
      .slice(0, index)
      .reverse()
      .find((r) => levelOf(r) < level);
  }
  if (!target || target === row) return false;
  target.focus();
  return true;
}

/**
 * Keys on a focused row (treegrid pattern, row focus): Enter or Space
 * toggles it, Right opens it or moves to its first child, Left closes it or
 * moves to its parent, Up/Down/Home/End move between rows. Keys pressed on
 * the link or a button inside the row keep their own meaning.
 */
function onRowKeyDown(
  event: KeyboardEvent<HTMLTableRowElement>,
  toggle?: () => void,
  expanded?: boolean,
) {
  if (event.target !== event.currentTarget) return;
  const row = event.currentTarget;
  let handled = false;
  switch (event.key) {
    case 'Enter':
    case ' ':
      if (toggle) {
        toggle();
        handled = true;
      }
      break;
    case 'ArrowRight':
      if (toggle && !expanded) {
        toggle();
        handled = true;
      } else handled = focusRow(row, 'child');
      break;
    case 'ArrowLeft':
      if (toggle && expanded) {
        toggle();
        handled = true;
      } else handled = focusRow(row, 'parent');
      break;
    case 'ArrowDown':
      handled = focusRow(row, 'next');
      break;
    case 'ArrowUp':
      handled = focusRow(row, 'previous');
      break;
    case 'Home':
      handled = focusRow(row, 'first');
      break;
    case 'End':
      handled = focusRow(row, 'last');
      break;
  }
  if (handled) event.preventDefault();
}

/** A click on the row toggles it, except on its link or buttons. */
function onRowClick(
  event: MouseEvent<HTMLTableRowElement>,
  toggle: () => void,
) {
  const target = event.target as Element;
  if (target.closest('a, button, input, select, textarea')) return;
  toggle();
}

function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronRight
      aria-hidden="true"
      className={`h-4 w-4 shrink-0 text-slate-400 transition-transform duration-150 motion-reduce:transition-none ${open ? 'rotate-90' : ''}`}
    />
  );
}

function DocumentIcon({ fileType }: { fileType: string }) {
  const type = fileType || '';
  const Icon = type.startsWith('audio/')
    ? FileAudio
    : type.startsWith('video/')
      ? FileVideo
      : type.startsWith('image/')
        ? FileImage
        : /sheet|excel|csv/i.test(type)
          ? FileSpreadsheet
          : FileText;
  return (
    <Icon
      aria-hidden="true"
      className="h-4 w-4 shrink-0 text-slate-400 dark:text-slate-500"
    />
  );
}

/** The Documents cell of a project row. */
function DocumentsSummary({ entry }: { entry: DocumentsEntry | undefined }) {
  const { t } = useTranslation();
  if (!entry || entry.state === 'error') {
    const why = t(
      entry ? 'projects.tree.docsError' : 'projects.tree.docsNotLoaded',
    );
    return (
      <span className="text-slate-400 dark:text-slate-500" title={why}>
        <span aria-hidden="true">—</span>
        <span className="sr-only">{why}</span>
      </span>
    );
  }
  if (entry.state === 'loading') {
    return (
      <span className="inline-flex text-slate-400">
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        <span className="sr-only">{t('projects.tree.docsLoading')}</span>
      </span>
    );
  }
  const { total, completed } = documentProgress(entry.documents);
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span className="tabular-nums">
        {t('projects.tree.docsDone', { completed, total })}
      </span>
      {total > 0 && (
        <span
          aria-hidden="true"
          className="hidden h-1.5 w-12 overflow-hidden rounded-full bg-slate-200 lg:inline-block dark:bg-white/10"
        >
          <span
            className={`block h-full rounded-full ${completed === total ? 'bg-emerald-500' : 'bg-blue-500'}`}
            style={{ width: `${Math.round((completed / total) * 100)}%` }}
          />
        </span>
      )}
    </span>
  );
}

/** The rows under an open project: its documents, or why they are missing. */
function DocumentRows({
  entry,
  onRetry,
}: {
  entry: DocumentsEntry | undefined;
  onRetry: () => void;
}) {
  const { t } = useTranslation();

  const note = (content: ReactNode) => (
    <tr
      data-tree-row
      tabIndex={-1}
      aria-level={3}
      onKeyDown={(e) => onRowKeyDown(e)}
      className={DOC_ROW}
    >
      <td colSpan={COLUMNS} className={DOC_CELL}>
        {content}
      </td>
    </tr>
  );

  if (!entry || entry.state === 'loading') {
    return note(
      <span className="inline-flex items-center gap-2 text-slate-500 dark:text-slate-400">
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        {t('projects.tree.docsLoading')}
      </span>,
    );
  }
  if (entry.state === 'error') {
    return note(
      <span className="inline-flex flex-wrap items-center gap-2">
        <span role="alert" className="text-red-700 dark:text-red-400">
          {t('projects.tree.docsError')}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className={`rounded-md border border-slate-300/70 bg-white/70 px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-white dark:border-white/10 dark:bg-white/5 dark:text-slate-200 dark:hover:bg-white/10 ${FOCUS_RING}`}
        >
          {t('projects.tree.retry')}
        </button>
      </span>,
    );
  }
  if (entry.documents.length === 0) {
    return note(
      <span className="text-slate-500 dark:text-slate-400">
        {t('projects.tree.noDocuments')}
      </span>,
    );
  }
  return (
    <>
      {entry.documents.map((doc, index) => (
        <tr
          key={doc.document_id}
          data-tree-row
          tabIndex={-1}
          aria-level={3}
          aria-setsize={entry.documents.length}
          aria-posinset={index + 1}
          onKeyDown={(e) => onRowKeyDown(e)}
          className={DOC_ROW}
        >
          <td colSpan={COLUMNS} className={DOC_CELL}>
            <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
              <DocumentIcon fileType={doc.file_type} />
              <span className="min-w-0 text-slate-700 wrap-anywhere dark:text-slate-200">
                {doc.name}
              </span>
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium ${STATUS_BADGE[doc.status] ?? STATUS_BADGE_OTHER}`}
              >
                {t(`documents.${doc.status}`, doc.status)}
              </span>
              <span className="hidden shrink-0 text-xs text-slate-400 sm:inline dark:text-slate-500">
                {formatSize(doc.file_size)}
              </span>
            </span>
          </td>
        </tr>
      ))}
    </>
  );
}

/**
 * Projects as a table-like tree, like a CRM admin table: a group per loan
 * product, a row per project (customer, product, case handler, documents
 * done, last update), and the project's documents under it once opened.
 * Documents load when a project is first opened, one request per project.
 */
export default function ProjectTreeView({
  projects,
  fetchApi,
  onCreate,
  onEdit,
  onDelete,
}: ProjectTreeViewProps) {
  const { t, i18n } = useTranslation();
  const tableId = useId();
  const [query, setQuery] = useState('');
  const [pageSize, setPageSize] = useState<PageSize>(PAGE_SIZES[0]);
  const [page, setPage] = useState(1);
  const [closedGroups, setClosedGroups] = useState<ReadonlySet<ProductId>>(
    () => new Set(),
  );
  const [openProjects, setOpenProjects] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [documents, setDocuments] = useState<Record<string, DocumentsEntry>>(
    {},
  );
  // Projects whose documents are loading or loaded: one request each.
  const requested = useRef(new Set<string>());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const productLabel = useCallback(
    (product: ProductId) => t(`projects.tree.products.${product}`),
    [t],
  );
  const rows = useMemo(() => toTreeProjects(projects), [projects]);
  const matches = useMemo(
    () => filterTreeProjects(rows, query, productLabel),
    [rows, query, productLabel],
  );
  const totals = useMemo(() => countByProduct(matches), [matches]);
  const pages = pageCount(matches.length, pageSize);
  const current = Math.min(page, pages);
  const first = (current - 1) * pageSize;
  const pageRows = matches.slice(first, first + pageSize);
  const groups = groupTreeProjects(pageRows);
  const striped = new Set(
    pageRows.filter((_, i) => i % 2 === 1).map((r) => r.project.project_id),
  );

  const dateLocale = i18n.language?.startsWith('en')
    ? 'en-IN'
    : i18n.language || undefined;
  const formatDate = (iso: string) => {
    const date = new Date(iso);
    if (!iso || Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString(dateLocale, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
  };

  const loadDocuments = useCallback(
    async (projectId: string) => {
      if (requested.current.has(projectId)) return;
      requested.current.add(projectId);
      setDocuments((prev) => ({ ...prev, [projectId]: { state: 'loading' } }));
      try {
        const list = await fetchApi<ListedDocument[]>(
          `projects/${projectId}/documents`,
        );
        if (!mounted.current) return;
        setDocuments((prev) => ({
          ...prev,
          [projectId]: {
            state: 'loaded',
            documents: Array.isArray(list) ? list : [],
          },
        }));
      } catch (error) {
        console.error('Failed to load documents:', error);
        requested.current.delete(projectId);
        if (!mounted.current) return;
        setDocuments((prev) => ({ ...prev, [projectId]: { state: 'error' } }));
      }
    },
    [fetchApi],
  );

  const toggleGroup = (product: ProductId) =>
    setClosedGroups((prev) => toggled(prev, product));

  const toggleProject = (projectId: string) => {
    if (!openProjects.has(projectId)) void loadDocuments(projectId);
    setOpenProjects((prev) => toggled(prev, projectId));
  };

  const renderProject = (row: TreeProject, index: number, count: number) => {
    const { project } = row;
    const id = project.project_id;
    const open = openProjects.has(id);
    const entry = documents[id];
    const toggle = () => toggleProject(id);
    const updated = formatDate(row.updated);
    const stripe = striped.has(id)
      ? 'bg-slate-100/70 dark:bg-white/[0.04]'
      : 'bg-white/70 dark:bg-transparent';
    return (
      <Fragment key={id}>
        <tr
          data-tree-row
          tabIndex={0}
          aria-level={2}
          aria-expanded={open}
          aria-setsize={count}
          aria-posinset={index + 1}
          aria-busy={entry?.state === 'loading' || undefined}
          onClick={(e) => onRowClick(e, toggle)}
          onKeyDown={(e) => onRowKeyDown(e, toggle, open)}
          className={`cursor-pointer border-t border-slate-200/70 hover:bg-blue-50/70 dark:border-white/[0.06] dark:hover:bg-blue-500/[0.07] ${stripe} ${ROW_FOCUS}`}
        >
          <td className="py-2 pl-3 pr-2 sm:pl-8 sm:pr-3">
            <div className="flex min-w-0 items-start gap-2">
              <span className="mt-0.5">
                <Chevron open={open} />
              </span>
              <div className="min-w-0">
                <Link
                  to="/projects/$projectId"
                  params={{ projectId: id }}
                  title={project.name}
                  className={`group/link rounded-sm font-medium ${FOCUS_RING}`}
                >
                  <span className="text-blue-700 group-hover/link:underline dark:text-blue-400">
                    {row.customer}
                  </span>
                </Link>
                <div className="mt-0.5 text-xs text-slate-500 wrap-anywhere sm:hidden dark:text-slate-400">
                  {[project.created_by, updated].filter(Boolean).join(' · ')}
                </div>
              </div>
            </div>
          </td>
          <td className="hidden px-3 py-2 md:table-cell">
            <span
              className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${PRODUCT_BADGE[row.product]}`}
            >
              {productLabel(row.product)}
            </span>
          </td>
          <td className="hidden px-3 py-2 sm:table-cell">
            <span
              className="block max-w-[12rem] truncate"
              title={project.created_by ?? undefined}
            >
              {project.created_by || '—'}
            </span>
          </td>
          <td className="px-2 py-2 sm:px-3">
            <DocumentsSummary entry={entry} />
          </td>
          <td className="hidden whitespace-nowrap px-3 py-2 sm:table-cell">
            {updated ? (
              <time
                dateTime={row.updated}
                title={new Date(row.updated).toLocaleString(dateLocale)}
              >
                {updated}
              </time>
            ) : (
              '—'
            )}
          </td>
          <td className="px-1 py-1.5 text-right sm:px-2">
            <span className="inline-flex flex-col gap-0.5 sm:flex-row">
              <button
                type="button"
                onClick={() => onEdit(project)}
                aria-label={t('projects.tree.edit', { name: project.name })}
                title={t('projects.editProject')}
                className={`${ICON_BUTTON} hover:text-blue-700 dark:hover:text-blue-400`}
              >
                <Pencil aria-hidden="true" className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => onDelete(project)}
                aria-label={t('projects.tree.delete', { name: project.name })}
                title={t('projects.deleteProject')}
                className={`${ICON_BUTTON} hover:text-red-600 dark:hover:text-red-400`}
              >
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              </button>
            </span>
          </td>
        </tr>
        {open && (
          <DocumentRows entry={entry} onRetry={() => void loadDocuments(id)} />
        )}
      </Fragment>
    );
  };

  const from = matches.length === 0 ? 0 : first + 1;
  const to = Math.min(first + pageSize, matches.length);

  return (
    <div>
      {/* Show N entries · Search · New project */}
      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          <span>{t('projects.tree.show')}</span>
          <select
            value={pageSize}
            aria-label={t('projects.tree.entriesLabel')}
            aria-controls={tableId}
            onChange={(e) => {
              setPageSize(Number(e.target.value) as PageSize);
              setPage(1);
            }}
            className={FIELD}
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
          <span>{t('projects.tree.entries')}</span>
        </label>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <span className="shrink-0">{t('projects.tree.search')}</span>
            <span className="relative w-full sm:w-64">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
              />
              <input
                type="search"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
                placeholder={t('projects.tree.searchPlaceholder')}
                aria-controls={tableId}
                className={`${FIELD} w-full pl-8`}
              />
            </span>
          </label>
          <button
            type="button"
            onClick={onCreate}
            className={`inline-flex items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-400 ${FOCUS_RING}`}
          >
            <Plus aria-hidden="true" className="h-4 w-4" />
            {t('projects.newProject')}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-white/60 bg-white/55 shadow-sm backdrop-blur-sm dark:border-[var(--color-border)] dark:bg-[var(--color-bg-secondary)] dark:backdrop-blur-none">
        <table
          id={tableId}
          role="treegrid"
          aria-label={t('projects.tree.tableLabel')}
          className="w-full text-left text-sm text-slate-700 dark:text-slate-300"
        >
          <thead className="bg-slate-100/70 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:bg-white/[0.04] dark:text-slate-400">
            <tr>
              <th scope="col" className="py-2.5 pl-3 pr-2 sm:pl-8 sm:pr-3">
                {t('projects.tree.colCustomer')}
              </th>
              <th scope="col" className="hidden px-3 py-2.5 md:table-cell">
                {t('projects.tree.colProduct')}
              </th>
              <th scope="col" className="hidden px-3 py-2.5 sm:table-cell">
                {t('projects.tree.colHandler')}
              </th>
              <th scope="col" className="px-2 py-2.5 sm:px-3">
                {t('projects.tree.colDocuments')}
              </th>
              <th scope="col" className="hidden px-3 py-2.5 sm:table-cell">
                {t('projects.tree.colUpdated')}
              </th>
              <th scope="col" className="px-1 py-2.5 sm:px-2">
                <span className="sr-only">{t('projects.tree.colActions')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {groups.length === 0 ? (
              <tr>
                <td
                  colSpan={COLUMNS}
                  className="px-3 py-8 text-center text-slate-500 dark:text-slate-400"
                >
                  {t('projects.tree.noMatches')}
                </td>
              </tr>
            ) : (
              groups.map((group, groupIndex) => {
                const open = !closedGroups.has(group.product);
                const toggle = () => toggleGroup(group.product);
                return (
                  <Fragment key={group.product}>
                    <tr
                      data-tree-row
                      tabIndex={0}
                      aria-level={1}
                      aria-expanded={open}
                      aria-setsize={groups.length}
                      aria-posinset={groupIndex + 1}
                      onClick={(e) => onRowClick(e, toggle)}
                      onKeyDown={(e) => onRowKeyDown(e, toggle, open)}
                      className={`cursor-pointer border-t border-slate-200/80 bg-slate-200/40 hover:bg-slate-200/70 dark:border-white/10 dark:bg-white/[0.06] dark:hover:bg-white/[0.09] ${ROW_FOCUS}`}
                    >
                      <td colSpan={COLUMNS} className="px-3 py-2">
                        <span className="flex items-center gap-2 font-semibold text-slate-800 dark:text-slate-100">
                          <Chevron open={open} />
                          <span
                            aria-hidden="true"
                            className={`h-2.5 w-2.5 shrink-0 rounded-full ${PRODUCT_DOT[group.product]}`}
                          />
                          {productLabel(group.product)}
                          <span className="rounded-full bg-white/80 px-2 py-0.5 text-xs font-medium text-slate-600 dark:bg-white/10 dark:text-slate-300">
                            {t('projects.tree.caseCount', {
                              count:
                                totals.get(group.product) ?? group.rows.length,
                            })}
                          </span>
                        </span>
                      </td>
                    </tr>
                    {open &&
                      group.rows.map((row, index) =>
                        renderProject(row, index, group.rows.length),
                      )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Showing X to Y of Z entries · pages */}
      <div className="mt-3 flex flex-col gap-2 text-sm text-slate-600 sm:flex-row sm:items-center sm:justify-between dark:text-slate-400">
        <p aria-live="polite">
          {t('projects.tree.showing', { from, to, total: matches.length })}
          {matches.length !== rows.length &&
            ` ${t('projects.tree.filteredFrom', { total: rows.length })}`}
        </p>
        <nav
          aria-label={t('projects.tree.pagination')}
          className="flex flex-wrap items-center gap-1"
        >
          <button
            type="button"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
            className={`${PAGE_BUTTON} ${PAGE_BUTTON_IDLE}`}
          >
            {t('projects.tree.previous')}
          </button>
          {pageWindow(current, pages).map((number, i) =>
            number === null ? (
              <span key={`gap-${i}`} aria-hidden="true" className="px-1">
                …
              </span>
            ) : (
              <button
                key={number}
                type="button"
                onClick={() => setPage(number)}
                aria-current={number === current ? 'page' : undefined}
                aria-label={t('projects.tree.page', { page: number })}
                className={`${PAGE_BUTTON} ${number === current ? PAGE_BUTTON_CURRENT : PAGE_BUTTON_IDLE}`}
              >
                {number}
              </button>
            ),
          )}
          <button
            type="button"
            disabled={current === pages}
            onClick={() => setPage(current + 1)}
            className={`${PAGE_BUTTON} ${PAGE_BUTTON_IDLE}`}
          >
            {t('projects.tree.next')}
          </button>
        </nav>
      </div>
    </div>
  );
}
