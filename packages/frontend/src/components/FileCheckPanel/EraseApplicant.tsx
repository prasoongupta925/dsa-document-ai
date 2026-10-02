import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Loader2, ShieldAlert, Trash2, X } from 'lucide-react';
import type { ApplicantEraseResponse } from '../../types/fileCheck';
import {
  apiErrorStatus,
  eraseConfirmMatches,
  eraseMayBeIncomplete,
} from '../../lib/fileCheck';
import { apiErrorDetail } from '../../lib/apiError';

// Status codes of packages/backend/app/routers/applicants.py. A 5xx or a
// lost answer that is not one of the API's own errors from before deleting
// may have left the erase half done (eraseMayBeIncomplete): it says so
// instead of blaming the configuration.
export function describeEraseError(t: TFunction, error: unknown): string {
  const status = apiErrorStatus(error);
  const detail = apiErrorDetail(error);
  if (eraseMayBeIncomplete(error)) {
    return t('fileCheck.erase.errors.uncertain', {
      reason:
        status !== null
          ? `HTTP ${status}`
          : t('fileCheck.erase.errors.noAnswer'),
    });
  }
  let message: string;
  if (status === 400) message = t('fileCheck.erase.errors.mismatch');
  else if (status === 404) message = t('fileCheck.erase.errors.notFound');
  else if (status === 409) message = t('fileCheck.erase.errors.conflict');
  else if (status === 503) message = t('fileCheck.erase.errors.notConfigured');
  else if (status !== null && status >= 500) {
    message = t('fileCheck.erase.errors.failed', { status });
  } else {
    message = t('fileCheck.errors.rejected', { status });
  }
  return detail ? `${message} (${detail})` : message;
}

const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

interface EraseApplicantDialogProps {
  /** The applicant's name as the verdict shows it: what must be typed. */
  applicant: string;
  /** Masked PAN, shown to tell same-name applicants apart. */
  pan?: string | null;
  /** Names of the documents the verdict lists under the applicant: what is erased. */
  documents?: string[];
  erasing: boolean;
  error: unknown;
  onCancel: () => void;
  /** Called with the typed name once it matches exactly. */
  onConfirm: (typed: string) => void;
  /** The user edited the name (clears a previous error). */
  onEdit?: () => void;
  /** Initial text of the name field (tests render the dialog statically). */
  initialTyped?: string;
}

/**
 * In-page confirmation for erasing an applicant: explains what is deleted and
 * enables the button only when the applicant's name is typed exactly.
 */
export default function EraseApplicantDialog({
  applicant,
  pan,
  documents = [],
  erasing,
  error,
  onCancel,
  onConfirm,
  onEdit,
  initialTyped = '',
}: EraseApplicantDialogProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const bodyId = useId();
  const inputId = useId();
  const hintId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState(initialTyped);
  const matches = eraseConfirmMatches(typed, applicant);
  const showMismatch = typed.trim().length > 0 && !matches;

  useEffect(() => {
    inputRef.current?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      // Keep the File Check panel open: only this dialog closes.
      e.preventDefault();
      e.stopPropagation();
      if (!erasing) onCancel();
      return;
    }
    if (e.key !== 'Tab' || !dialogRef.current) return;
    // Keep focus inside the dialog.
    const nodes = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE),
    );
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const handleBackdrop = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget && !erasing) onCancel();
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (matches && !erasing) onConfirm(typed.trim().replace(/\s+/g, ' '));
  };

  const content = (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm dark:bg-black/65"
      onMouseDown={handleBackdrop}
      data-testid="erase-dialog"
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="relative w-full max-w-md rounded-2xl border border-red-200 bg-white p-5 shadow-2xl focus:outline-none dark:border-red-500/30 dark:bg-[#1a1d2e]"
      >
        <button
          type="button"
          onClick={onCancel}
          disabled={erasing}
          className="absolute right-3 top-3 rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50 dark:hover:bg-white/10 dark:hover:text-white"
          aria-label={t('common.close')}
          title={t('common.close')}
        >
          <X className="h-4 w-4" />
        </button>
        <div className="flex items-start gap-3 pr-6">
          <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-red-100 text-red-600 dark:bg-red-500/10 dark:text-red-400">
            <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2
              id={titleId}
              className="text-base font-semibold text-slate-900 dark:text-white"
            >
              {t('fileCheck.erase.title', { name: applicant })}
            </h2>
            {pan && (
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                {t('fileCheck.erase.pan', { pan })}
              </p>
            )}
          </div>
        </div>

        <div
          id={bodyId}
          className="mt-3 space-y-2 text-xs leading-relaxed text-slate-600 dark:text-slate-300"
        >
          <p>{t('fileCheck.erase.body')}</p>
          {documents.length > 0 && (
            <div data-testid="erase-documents">
              <p className="font-semibold text-slate-700 dark:text-slate-200">
                {t('fileCheck.erase.documents', { count: documents.length })}
              </p>
              <ul className="mt-0.5 max-h-28 list-disc space-y-0.5 overflow-y-auto pl-4 text-[11px]">
                {documents.map((name, i) => (
                  <li key={`${name}-${i}`} className="break-words">
                    {name}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="font-semibold text-red-700 dark:text-red-400">
            {t('fileCheck.erase.cannotUndo')}
          </p>
          <p className="text-[11px] text-slate-500 dark:text-slate-400">
            {t('fileCheck.erase.scope')}
          </p>
          <p
            className="text-[11px] text-slate-500 dark:text-slate-400"
            data-testid="erase-not-erased"
          >
            {t('fileCheck.erase.notErased')}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-2">
          <label
            htmlFor={inputId}
            className="block text-xs font-medium text-slate-700 dark:text-slate-200"
          >
            {t('fileCheck.erase.typeLabel')}{' '}
            <span className="select-all font-mono font-semibold text-slate-900 dark:text-white">
              {applicant}
            </span>
          </label>
          <input
            id={inputId}
            ref={inputRef}
            type="text"
            value={typed}
            onChange={(e) => {
              setTyped(e.target.value);
              onEdit?.();
            }}
            disabled={erasing}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            aria-invalid={showMismatch}
            aria-describedby={hintId}
            data-testid="erase-confirm-input"
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-500/30 disabled:opacity-60 dark:border-slate-600 dark:bg-slate-900 dark:text-white"
          />
          <p
            id={hintId}
            className={`text-[11px] ${
              showMismatch
                ? 'text-red-600 dark:text-red-400'
                : 'text-slate-500 dark:text-slate-400'
            }`}
          >
            {showMismatch
              ? t('fileCheck.erase.mismatchHint')
              : t('fileCheck.erase.exactHint')}
          </p>
          {error != null && (
            <p
              role="alert"
              className="break-words rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
            >
              {describeEraseError(t, error)}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={erasing}
              className="rounded-lg border border-black/10 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={!matches || erasing}
              data-testid="erase-confirm-button"
              className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {erasing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
              {erasing
                ? t('fileCheck.erase.erasing')
                : t('fileCheck.erase.confirm')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );

  // Rendered on <body> so no panel stacking context can cover it (inline
  // when there is no DOM, e.g. static rendering in tests).
  return typeof document === 'undefined'
    ? content
    : createPortal(content, document.body);
}

interface EraseResultCardProps {
  result: ApplicantEraseResponse;
  onDismiss: () => void;
}

/** What the erase deleted (and what it could not), after the verdict is cleared. */
export const EraseResultCard = forwardRef<HTMLElement, EraseResultCardProps>(
  function EraseResultCard({ result, onDismiss }, ref) {
    const { t } = useTranslation();
    const deleted = result.documents_deleted;
    const failed = result.failed;
    const at = result.erased_at ? new Date(result.erased_at) : null;
    const time =
      at && !Number.isNaN(at.getTime())
        ? at.toLocaleString([], {
            dateStyle: 'medium',
            timeStyle: 'short',
          })
        : '';
    return (
      <section
        ref={ref}
        tabIndex={-1}
        role="status"
        aria-label={t('fileCheck.erase.result.title', {
          name: result.applicant,
        })}
        data-testid="erase-result"
        className={`space-y-1.5 rounded-lg border px-3 py-2 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 ${
          failed.length > 0
            ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700/50 dark:bg-amber-900/20 dark:text-amber-200'
            : 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-800/50 dark:bg-emerald-900/20 dark:text-emerald-200'
        }`}
      >
        <div className="flex items-start gap-2">
          <p className="min-w-0 flex-1 font-semibold">
            {t('fileCheck.erase.result.title', { name: result.applicant })}
          </p>
          <button
            type="button"
            onClick={onDismiss}
            className="rounded p-0.5 opacity-70 hover:opacity-100"
            aria-label={t('fileCheck.erase.result.dismiss')}
            title={t('fileCheck.erase.result.dismiss')}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <p>
          {t('fileCheck.erase.result.deleted', { count: deleted.length })}
          {time && ` · ${time}`}
        </p>
        {result.delivery_log_redacted === null ? (
          <p className="text-[11px] font-semibold text-red-700 dark:text-red-300">
            {t('fileCheck.erase.result.deliveryLogFailed')}
          </p>
        ) : (
          result.delivery_log_redacted > 0 && (
            <p className="text-[11px]">
              {t('fileCheck.erase.result.deliveryLog', {
                count: result.delivery_log_redacted,
              })}
            </p>
          )
        )}
        {deleted.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-4 text-[11px]">
            {deleted.map((d, i) => (
              <li key={`${d.document_id}-${i}`} className="break-words">
                {d.name}
              </li>
            ))}
          </ul>
        )}
        {failed.length > 0 && (
          <div className="space-y-0.5" role="alert">
            <p className="font-semibold text-red-700 dark:text-red-300">
              {t('fileCheck.erase.result.failed', { count: failed.length })}
            </p>
            <ul className="list-disc space-y-0.5 pl-4 text-[11px]">
              {failed.map((f, i) => (
                <li key={`${f.document_id}-${i}`} className="break-words">
                  {f.name}
                  {f.error ? ` – ${f.error}` : ''}
                </li>
              ))}
            </ul>
            <p className="text-[11px]">
              {t('fileCheck.erase.result.failedHint')}
            </p>
          </div>
        )}
        <p className="text-[11px] opacity-90">
          {t('fileCheck.erase.notErased')}
        </p>
        <p className="text-[11px] opacity-90">
          {t('fileCheck.erase.result.rerun')}
        </p>
      </section>
    );
  },
);
