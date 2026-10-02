import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  ClipboardCheck,
  Download,
  Loader2,
  Play,
  RefreshCw,
  X,
} from 'lucide-react';
import type { FileCheckState } from '../../hooks/useFileCheck';
import type { FileCheckAskState } from '../../hooks/useFileCheckAsk';
import { useAwsClient } from '../../hooks/useAwsClient';
import type { PainPointId, PainPointTarget } from '../../data/dsaPainPoints';
import type { FileCheckApplicant } from '../../types/fileCheck';
import {
  apiErrorStatus,
  buildFileCheckCsv,
  downloadTextFile,
  fileCheckCsvFileName,
  fileCheckFindings,
  maskPan,
} from '../../lib/fileCheck';
import VerdictCard from './VerdictCard';
import AskSection from './AskSection';
import EraseApplicantDialog, { EraseResultCard } from './EraseApplicant';
import CallProjectNote, { isCallRecordingsOnly } from './CallProjectNote';
import { useItemConfirmations } from './confirmations';

/** Where to scroll when the panel is opened from a "Show me" button. */
export interface FileCheckFocus {
  target: PainPointTarget;
  /** Changes on every request, so the same target can be shown again. */
  key: number;
}

const HIGHLIGHT_CLASSES = ['ring-2', 'ring-violet-400/70', 'ring-offset-1'];

const CONTROL_CLASS =
  'w-full px-2.5 py-1.5 text-xs border border-black/10 dark:border-[#3b4264] rounded-lg bg-white/40 dark:bg-[#0d1117] text-[#0f172a] dark:text-[#f1f5f9] placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent disabled:opacity-60';

// Status codes of packages/backend/app/routers/file_check.py.
function describeError(t: TFunction, error: unknown): string {
  const status = apiErrorStatus(error);
  if (status === 400) return t('fileCheck.errors.unknownChecklist');
  if (status === 404) return t('fileCheck.errors.notFound');
  if (status === 503) return t('fileCheck.errors.notConfigured');
  if (status === 502 || status === 504) {
    return t('fileCheck.errors.unavailable', { status });
  }
  if (status !== null && status >= 400 && status < 500) {
    return t('fileCheck.errors.rejected', { status });
  }
  return error instanceof Error ? error.message : String(error);
}

interface FileCheckPanelProps {
  state: FileCheckState;
  askState: FileCheckAskState;
  onClose: () => void;
  focus?: FileCheckFocus | null;
  /** Opens the "Why DSAs need this" card a finding's tag points to. */
  onPainPoint?: (id: PainPointId) => void;
  /** Opens Eligibility & lenders for an applicant of the verdict. */
  onOpenEligibility?: (applicant: FileCheckApplicant) => void;
}

/** Overlays the right-hand side panel, like the artifact viewer. */
export default function FileCheckPanel({
  state,
  askState,
  onClose,
  focus,
  onPainPoint,
  onOpenEligibility,
}: FileCheckPanelProps) {
  const { t } = useTranslation();
  const checklistFieldId = useId();
  const applicantFieldId = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const askInputRef = useRef<HTMLTextAreaElement>(null);
  const runButtonRef = useRef<HTMLButtonElement>(null);
  // "Show me" asked for the obligations before any check was run.
  const [focusHint, setFocusHint] = useState<PainPointTarget | null>(null);
  // Erase applicant: the confirmation dialog's applicant and its trigger.
  const [eraseTarget, setEraseTarget] = useState<FileCheckApplicant | null>(
    null,
  );
  const eraseOpenRef = useRef(false);
  eraseOpenRef.current = eraseTarget !== null;
  const eraseTriggerRef = useRef<HTMLElement | null>(null);
  const eraseResultRef = useRef<HTMLElement>(null);
  const {
    checklists,
    checklistsLoaded,
    checklistsLoading,
    checklistsError,
    loadChecklists,
    checklistId,
    setChecklistId,
    applicant,
    setApplicant,
    knownApplicants,
    result,
    resultApplicant,
    lastRunAt,
    running,
    error,
    runCheck,
    erasing,
    eraseError,
    eraseResult,
    eraseApplicant,
    clearEraseError,
    dismissEraseResult,
  } = state;

  // Confirm / undo a needs-review item, then run the check again: the engine
  // shows it CONFIRMED (met) or REVIEW.
  const { fetchApi } = useAwsClient();
  const itemConfirmations = useItemConfirmations({
    fetchApi,
    projectId: result?.project_id,
    checklistId: result?.checklist?.id,
    onChanged: runCheck,
  });

  useEffect(() => {
    if (!checklistsLoaded && !checklistsLoading && !checklistsError) {
      loadChecklists();
    }
  }, [checklistsLoaded, checklistsLoading, checklistsError, loadChecklists]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      // The erase dialog closes itself on Escape; the panel stays open.
      if (e.key === 'Escape' && !e.defaultPrevented && !eraseOpenRef.current) {
        onClose();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  // "Show me": scroll to the part of the panel a pain-point card points to.
  const focusKey = focus?.key;
  const focusTarget = focus?.target;
  useEffect(() => {
    if (focusKey === undefined || !focusTarget) return;
    const container = scrollRef.current;
    if (!container) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const frame = requestAnimationFrame(() => {
      if (focusTarget === 'file-check') {
        container.scrollTo?.({ top: 0, behavior: 'smooth' });
        setFocusHint(null);
        return;
      }
      const el = container.querySelector<HTMLElement>(
        `[data-focus="${focusTarget}"]`,
      );
      if (!el) {
        // Obligations appear once a check has run: point at the Run button.
        setFocusHint(focusTarget);
        runButtonRef.current?.focus();
        return;
      }
      setFocusHint(null);
      el.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
      el.classList.add(...HIGHLIGHT_CLASSES);
      timer = setTimeout(() => el.classList.remove(...HIGHLIGHT_CLASSES), 2000);
      if (focusTarget === 'ask') {
        askInputRef.current?.focus({ preventScroll: true });
      }
    });
    return () => {
      cancelAnimationFrame(frame);
      if (timer) clearTimeout(timer);
    };
  }, [focusKey, focusTarget]);

  // The hint is about running the check: drop it once a result is shown.
  useEffect(() => {
    if (result) setFocusHint(null);
  }, [result]);

  const selected = checklists.find((c) => c.id === checklistId);
  // The product of the checklist the shown verdict used (reminder {{product}}).
  const resultProduct =
    checklists.find((c) => c.id === result?.checklist?.id)?.product ?? null;
  const selectedFoir = selected?.foir;
  const foirLimitPct =
    typeof selectedFoir?.value === 'number' &&
    Number.isFinite(selectedFoir.value)
      ? Math.round(selectedFoir.value * 1000) / 10
      : null;
  const findings = useMemo(
    () => (result ? fileCheckFindings(result) : []),
    [result],
  );
  const resultChecklistId = result?.checklist?.id;
  const stale =
    !!result &&
    !!resultChecklistId &&
    !!checklistId &&
    resultChecklistId !== checklistId;
  const showApplicantSelect =
    knownApplicants.length > 1 &&
    (!applicant || knownApplicants.includes(applicant));

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!running) runCheck();
  };

  const openErase = (target: FileCheckApplicant, trigger: HTMLElement) => {
    clearEraseError();
    eraseTriggerRef.current = trigger;
    setEraseTarget(target);
  };

  const cancelErase = () => {
    setEraseTarget(null);
    clearEraseError();
    const trigger = eraseTriggerRef.current;
    setTimeout(() => {
      if (trigger?.isConnected) trigger.focus();
    }, 0);
  };

  const confirmErase = async (typed: string) => {
    if (!eraseTarget) return;
    const outcome = await eraseApplicant(eraseTarget, typed);
    if (outcome.kind === 'failed' || outcome.kind === 'ignored') return;
    // Answers in the Ask thread may quote the erased applicant's data.
    askState.clear();
    // Uncertain: the dialog stays open and says the erase may be incomplete.
    if (outcome.kind === 'uncertain') return;
    setEraseTarget(null);
    setTimeout(() => eraseResultRef.current?.focus(), 0);
  };

  const handleDownload = () => {
    if (!result) return;
    downloadTextFile(
      fileCheckCsvFileName(result, resultApplicant),
      buildFileCheckCsv(result),
    );
  };

  return (
    <div
      className="artifact-viewer-container absolute inset-0 z-10 flex flex-col border border-white/60 dark:border-indigo-500/20 rounded-xl overflow-hidden animate-fade-in"
      role="region"
      aria-label={t('fileCheck.title')}
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-black/[0.08] dark:border-white/[0.08] bg-transparent dark:bg-white/[0.05] flex-shrink-0">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex-shrink-0">
          <ClipboardCheck className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200 truncate">
            {t('fileCheck.title')}
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            {t('fileCheck.subtitle')}
          </p>
        </div>
        <button
          type="button"
          onClick={handleDownload}
          disabled={!result || findings.length === 0}
          className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium text-slate-600 hover:text-slate-800 dark:text-slate-300 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10 transition-colors disabled:opacity-40 disabled:pointer-events-none flex-shrink-0"
          title={t('fileCheck.downloadCsv')}
        >
          <Download className="w-4 h-4" />
          <span className="hidden sm:inline">CSV</span>
        </button>
        <button
          type="button"
          onClick={onClose}
          className="p-2 rounded-lg text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/10 transition-colors flex-shrink-0"
          title={t('common.close', 'Close')}
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Controls */}
      <form
        onSubmit={handleSubmit}
        className="px-4 py-3 space-y-2.5 border-b border-black/[0.08] dark:border-white/[0.08] flex-shrink-0"
      >
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <label
              htmlFor={checklistFieldId}
              className="text-[11px] font-semibold text-slate-600 dark:text-slate-300"
            >
              {t('fileCheck.checklist')}
            </label>
            {checklistsLoading && (
              <Loader2 className="h-3 w-3 animate-spin text-slate-400" />
            )}
          </div>
          <select
            id={checklistFieldId}
            value={checklistId}
            onChange={(e) => setChecklistId(e.target.value)}
            disabled={checklists.length === 0 || running}
            className={CONTROL_CLASS}
          >
            {checklists.length === 0 && (
              <option value="">
                {checklistsLoading
                  ? t('fileCheck.loadingChecklists')
                  : t('fileCheck.defaultChecklist')}
              </option>
            )}
            {checklists.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          {selected?.description && (
            <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
              {selected.description}
            </p>
          )}
          {foirLimitPct !== null && (
            <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
              {typeof selectedFoir?.source === 'string' && selectedFoir.source
                ? t('fileCheck.obligations.checklistLimit', {
                    limit: foirLimitPct,
                    source: selectedFoir.source,
                  })
                : t('fileCheck.obligations.checklistLimitOnly', {
                    limit: foirLimitPct,
                  })}
            </p>
          )}
          {checklistsError != null && (
            <div className="flex items-start gap-2 text-[11px] text-red-600 dark:text-red-400">
              <span className="flex-1 break-words">
                {t('fileCheck.checklistsFailed', {
                  message: describeError(t, checklistsError),
                })}
              </span>
              <button
                type="button"
                onClick={() => loadChecklists()}
                className="flex items-center gap-1 flex-shrink-0 font-medium hover:underline"
              >
                <RefreshCw className="h-3 w-3" />
                {t('fileCheck.retry')}
              </button>
            </div>
          )}
        </div>

        <div className="space-y-1">
          <label
            htmlFor={applicantFieldId}
            className="text-[11px] font-semibold text-slate-600 dark:text-slate-300"
          >
            {t('fileCheck.applicant')}{' '}
            <span className="font-normal text-slate-400">
              {t('fileCheck.applicantOptional')}
            </span>
          </label>
          {showApplicantSelect ? (
            <select
              id={applicantFieldId}
              value={applicant}
              onChange={(e) => setApplicant(e.target.value)}
              disabled={running}
              className={CONTROL_CLASS}
            >
              <option value="">{t('fileCheck.allApplicants')}</option>
              {knownApplicants.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : (
            <input
              id={applicantFieldId}
              type="text"
              value={applicant}
              onChange={(e) => setApplicant(e.target.value)}
              disabled={running}
              placeholder={t('fileCheck.applicantPlaceholder')}
              maxLength={200}
              autoComplete="off"
              className={CONTROL_CLASS}
            />
          )}
        </div>

        {focusHint === 'obligations' && !result && (
          <p
            role="status"
            className="rounded-lg border border-violet-200 bg-violet-50 px-2.5 py-1.5 text-[11px] text-violet-800 dark:border-violet-800/50 dark:bg-violet-900/20 dark:text-violet-300"
          >
            {t('fileCheck.focusHint.obligations')}
          </p>
        )}

        <button
          ref={runButtonRef}
          type="submit"
          disabled={running}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-700 dark:bg-emerald-600 dark:hover:bg-emerald-500 shadow-sm transition-colors disabled:opacity-70 disabled:cursor-wait"
        >
          {running ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          {running ? t('fileCheck.running') : t('fileCheck.run')}
        </button>
      </form>

      {/* Result */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto min-h-0 px-4 py-3 space-y-3"
      >
        {error != null && (
          <div
            role="alert"
            className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
          >
            <p className="font-semibold">{t('fileCheck.runFailed')}</p>
            <p className="mt-0.5 break-words">{describeError(t, error)}</p>
          </div>
        )}

        {eraseResult && (
          <EraseResultCard
            ref={eraseResultRef}
            result={eraseResult}
            onDismiss={dismissEraseResult}
          />
        )}

        {stale && (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:border-amber-800/50 dark:bg-amber-900/20 dark:text-amber-300">
            {t('fileCheck.staleResult', {
              name: result?.checklist?.name || resultChecklistId,
            })}
          </p>
        )}

        {result ? (
          <div
            className={running ? 'opacity-60 transition-opacity' : undefined}
            aria-busy={running}
          >
            {isCallRecordingsOnly(result) ? (
              // A call project is not a NOT READY loan file: point to Call QA.
              <CallProjectNote result={result} />
            ) : (
              <VerdictCard
                result={result}
                lastRunAt={lastRunAt}
                onPainPoint={onPainPoint}
                product={resultProduct}
                onEraseApplicant={openErase}
                onOpenEligibility={onOpenEligibility}
                confirmations={{
                  busyKey: itemConfirmations.busyKey,
                  error: itemConfirmations.error,
                  disabled: running,
                  onConfirm: itemConfirmations.confirm,
                  onUndo: itemConfirmations.undo,
                }}
              />
            )}
          </div>
        ) : running ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-xs text-slate-500">
            <Loader2 className="h-6 w-6 animate-spin text-emerald-500" />
            {t('fileCheck.running')}
          </div>
        ) : (
          error == null && (
            <div className="flex flex-col items-center justify-center text-center gap-2 py-10 px-4">
              <ClipboardCheck className="h-8 w-8 text-slate-300 dark:text-slate-500" />
              <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                {t('fileCheck.emptyTitle')}
              </p>
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                {t('fileCheck.emptyHint')}
              </p>
            </div>
          )
        )}

        {/* Grounded Q&A; the deterministic verdict above stays as it is. */}
        <AskSection
          state={askState}
          checklistName={selected?.name || result?.checklist?.name || ''}
          checklistId={checklistId || undefined}
          applicant={applicant.trim() || undefined}
          inputRef={askInputRef}
        />
      </div>

      {/* Footer */}
      <p className="px-4 py-2 border-t border-black/[0.08] dark:border-white/[0.08] text-[10px] leading-snug text-slate-500 dark:text-slate-400 flex-shrink-0">
        {t('fileCheck.syntheticNote')}
      </p>

      {eraseTarget && (
        <EraseApplicantDialog
          applicant={eraseTarget.applicant}
          pan={maskPan(eraseTarget.pan)}
          documents={(eraseTarget.documents ?? []).map(
            (d) => d.document_name || d.document_id || '',
          )}
          erasing={erasing}
          error={eraseError}
          onCancel={cancelErase}
          onConfirm={confirmErase}
          onEdit={eraseError != null ? clearEraseError : undefined}
        />
      )}
    </div>
  );
}
