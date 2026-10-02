import { useId, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  Check,
  Headphones,
  Info,
  Landmark,
  Loader2,
  MessageCircle,
  Minus,
  Trash2,
  Undo2,
  UserCheck,
  UserRound,
  X,
} from 'lucide-react';
import type {
  ApplicantUsageTotal,
  FileCheckApplicant,
  FileCheckConsistencyRow,
  FileCheckIncome,
  FileCheckItemRow,
  FileCheckResult,
  PendingDocument,
} from '../../types/fileCheck';
import {
  apiErrorStatus,
  applicantUsageTotal,
  consistencyTone,
  documentUsage,
  formatInr,
  formatYearMonth,
  itemTone,
  normalizeStatus,
  reasonTone,
  skippedDocuments,
  verdictTone,
  type StatusTone,
  type VerdictTone,
} from '../../lib/fileCheck';
import {
  RETENTION_DAYS,
  formatTokens,
  formatUsd,
} from '../../lib/fileCheckAsk';
import {
  painPointForFinding,
  type PainPointId,
} from '../../data/dsaPainPoints';
import ObligationsSection from './ObligationsSection';
import PainPointTag, { PainPointOpenContext } from './PainPointTag';
import ReminderDraft from './ReminderDraft';
import { recordingDocuments } from './CallProjectNote';
import {
  canConfirmItem,
  confirmationDocumentIds,
  confirmationRowKey,
  formatConfirmedAt,
  isConfirmedItem,
  type ConfirmableItemRow,
} from './confirmations';

// Colours / icons only: every verdict and status comes from the engine.

const BANNER_CLASS: Record<VerdictTone, string> = {
  ready: 'bg-gradient-to-br from-emerald-600 to-green-700',
  notReady: 'bg-gradient-to-br from-red-600 to-rose-700',
  review: 'bg-gradient-to-br from-amber-500 to-orange-600',
  unknown: 'bg-gradient-to-br from-slate-500 to-slate-700',
};

const PILL_CLASS: Record<VerdictTone, string> = {
  ready:
    'bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800/50',
  notReady:
    'bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800/50',
  review:
    'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-800/50',
  unknown:
    'bg-slate-50 text-slate-600 border-slate-200 dark:bg-white/10 dark:text-slate-300 dark:border-white/10',
};

const ICON_CLASS: Record<StatusTone, string> = {
  ok: 'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-400',
  bad: 'bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400',
  review:
    'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400',
  info: 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-400',
  muted: 'bg-slate-100 text-slate-500 dark:bg-white/10 dark:text-slate-400',
};

const ROW_CLASS: Record<StatusTone, string> = {
  ok: 'border-white/50 bg-white/30 dark:border-white/[0.08] dark:bg-white/[0.03]',
  bad: 'border-red-200/80 bg-red-50/50 dark:border-red-800/40 dark:bg-red-900/10',
  review:
    'border-amber-300/80 bg-amber-50/60 dark:border-amber-700/50 dark:bg-amber-900/10',
  info: 'border-blue-200/70 bg-blue-50/40 dark:border-blue-800/40 dark:bg-blue-900/10',
  muted:
    'border-white/50 bg-white/20 dark:border-white/[0.06] dark:bg-white/[0.02]',
};

const REASON_CLASS: Record<StatusTone, string> = {
  ok: 'border-green-500 bg-green-50 text-green-900 dark:bg-green-900/20 dark:text-green-200',
  bad: 'border-red-500 bg-red-50 text-red-900 dark:bg-red-900/20 dark:text-red-200',
  review:
    'border-amber-500 bg-amber-50 text-amber-900 dark:bg-amber-900/20 dark:text-amber-200',
  info: 'border-blue-500 bg-blue-50 text-blue-900 dark:bg-blue-900/20 dark:text-blue-200',
  muted:
    'border-slate-400 bg-slate-50 text-slate-700 dark:bg-white/5 dark:text-slate-300',
};

const ITEM_STATUS_KEYS: Record<string, string> = {
  PRESENT: 'fileCheck.status.present',
  MISSING: 'fileCheck.status.missing',
  REVIEW: 'fileCheck.status.review',
  'NEEDS REVIEW': 'fileCheck.status.review',
  CONFIRMED: 'fileCheck.status.confirmed',
};

const CONSISTENCY_STATUS_KEYS: Record<string, string> = {
  OK: 'fileCheck.status.ok',
  MISMATCH: 'fileCheck.status.mismatch',
  INFO: 'fileCheck.status.info',
  'N/A': 'fileCheck.status.na',
  REVIEW: 'fileCheck.status.review',
  'NEEDS REVIEW': 'fileCheck.status.review',
};

const SKIPPED_STATUS: Record<string, { key: string; tone: StatusTone }> = {
  PENDING: { key: 'fileCheck.status.pending', tone: 'info' },
  FAILED: { key: 'fileCheck.status.failed', tone: 'bad' },
  'NO FACTS': { key: 'fileCheck.status.noFacts', tone: 'review' },
  UNSUPPORTED: { key: 'fileCheck.status.unsupported', tone: 'review' },
  UNASSIGNED: { key: 'fileCheck.status.unassigned', tone: 'review' },
};

function statusLabel(
  t: TFunction,
  status: string,
  keys: Record<string, string>,
): string {
  const key = keys[normalizeStatus(status)];
  return key ? t(key) : status;
}

export function verdictLabel(t: TFunction, verdict: string): string {
  const tone = verdictTone(verdict);
  if (tone === 'ready') return t('fileCheck.verdict.ready');
  if (tone === 'notReady') return t('fileCheck.verdict.notReady');
  if (tone === 'review') return t('fileCheck.verdict.needsReview');
  return verdict || '–';
}

function StatusIcon({ tone, label }: { tone: StatusTone; label: string }) {
  const icon =
    tone === 'ok' ? (
      <Check className="h-3 w-3" strokeWidth={3} />
    ) : tone === 'bad' ? (
      <X className="h-3 w-3" strokeWidth={3} />
    ) : tone === 'review' ? (
      <span className="text-[11px] font-black leading-none">!</span>
    ) : tone === 'info' ? (
      <Info className="h-3 w-3" strokeWidth={2.5} />
    ) : (
      <Minus className="h-3 w-3" strokeWidth={3} />
    );
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={`mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full ${ICON_CLASS[tone]}`}
    >
      {icon}
    </span>
  );
}

function NeedsPersonPill() {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-700/60 dark:bg-amber-900/40 dark:text-amber-300">
      <UserRound className="h-2.5 w-2.5" />
      {t('fileCheck.needsPerson')}
    </span>
  );
}

function FindingRow({
  tone,
  label,
  statusText,
  optional,
  detail,
  documents,
  missingMonths,
  painPoint,
  children,
}: {
  tone: StatusTone;
  label: string;
  statusText: string;
  optional?: boolean;
  detail: string;
  documents: string[];
  missingMonths?: string[];
  painPoint?: PainPointId | null;
  /** Shown under the finding, e.g. the Confirm button of a review item. */
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  // The engine usually names the documents inside the detail already.
  const showDocuments =
    documents.length > 0 && !documents.every((d) => detail.includes(d));
  return (
    <li
      className={`flex gap-2 rounded-lg border px-2.5 py-2 ${ROW_CLASS[tone]}`}
      data-tone={tone}
    >
      <StatusIcon tone={tone} label={statusText} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-slate-800 dark:text-slate-100">
            {label}
          </span>
          {optional && (
            <span className="rounded-full bg-slate-200/70 px-1.5 py-0.5 text-[10px] text-slate-600 dark:bg-white/10 dark:text-slate-300">
              {t('fileCheck.optional')}
            </span>
          )}
          {tone === 'review' && <NeedsPersonPill />}
          {painPoint && <PainPointTag id={painPoint} />}
          {statusText !== t('fileCheck.status.review') && (
            <span className="ml-auto text-[10px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {statusText}
            </span>
          )}
        </div>
        {detail && (
          <p className="mt-0.5 break-words text-[11px] leading-snug text-slate-600 dark:text-slate-300">
            {detail}
          </p>
        )}
        {missingMonths && missingMonths.length > 0 && (
          <p className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-red-700 dark:text-red-400">
            <span className="font-semibold">
              {t('fileCheck.missingMonthsLabel')}
            </span>
            {missingMonths.map((m) => (
              <span
                key={m}
                className="rounded bg-red-100 px-1 py-0.5 font-medium dark:bg-red-900/40"
              >
                {formatYearMonth(m)}
              </span>
            ))}
          </p>
        )}
        {showDocuments && (
          <p className="mt-0.5 break-words text-[10px] text-slate-500 dark:text-slate-400">
            {t('fileCheck.documentsLabel', { names: documents.join(', ') })}
          </p>
        )}
        {children}
      </div>
    </li>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h5 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {title}
      </h5>
      {children}
    </div>
  );
}

const ACTION_BUTTON_CLASS =
  'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1';

/** Confirm / undo of the needs-review checklist items (see ./confirmations). */
export interface VerdictConfirmations {
  /** Row (confirmationRowKey) whose request is in flight. */
  busyKey: string | null;
  error: { key: string; error: unknown } | null;
  /** A check is running: the buttons wait for its verdict. */
  disabled?: boolean;
  onConfirm: (applicant: FileCheckApplicant, row: ConfirmableItemRow) => void;
  onUndo: (applicant: FileCheckApplicant, row: ConfirmableItemRow) => void;
}

function describeConfirmError(t: TFunction, error: unknown): string {
  const status = apiErrorStatus(error);
  if (status !== null) return t('fileCheck.confirm.failed', { status });
  return t('fileCheck.confirm.failedOffline', {
    message: error instanceof Error ? error.message : String(error),
  });
}

/** "Confirm" on a needs-review item: the person checked it, the engine counts it as met. */
function ConfirmControls({
  applicant,
  row,
  confirmations,
}: {
  applicant: FileCheckApplicant;
  row: ConfirmableItemRow;
  confirmations: VerdictConfirmations;
}) {
  const { t } = useTranslation();
  const key = confirmationRowKey(applicant, row);
  const busy = confirmations.busyKey === key;
  const noDocuments = confirmationDocumentIds(applicant).length === 0;
  const error =
    confirmations.error?.key === key ? confirmations.error.error : null;
  return (
    <div className="mt-1 space-y-1">
      <button
        type="button"
        onClick={() => confirmations.onConfirm(applicant, row)}
        disabled={
          confirmations.busyKey !== null ||
          !!confirmations.disabled ||
          noDocuments
        }
        title={
          noDocuments
            ? t('fileCheck.confirm.noDocuments')
            : t('fileCheck.confirm.title', { days: RETENTION_DAYS })
        }
        className={`${ACTION_BUTTON_CLASS} border-emerald-200 bg-emerald-50/70 text-emerald-800 hover:bg-emerald-100 focus-visible:ring-emerald-500 disabled:pointer-events-none disabled:opacity-50 dark:border-emerald-800/50 dark:bg-emerald-900/20 dark:text-emerald-300 dark:hover:bg-emerald-900/40`}
        data-testid="confirm-item"
      >
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        ) : (
          <UserCheck className="h-3 w-3" aria-hidden="true" />
        )}
        {t('fileCheck.confirm.action')}
      </button>
      {error != null && (
        <p
          role="alert"
          className="break-words text-[10px] text-red-700 dark:text-red-400"
        >
          {describeConfirmError(t, error)}
        </p>
      )}
    </div>
  );
}

/** "Confirmed by <user> at <time>" on a CONFIRMED item, with Undo. */
function ConfirmedLine({
  applicant,
  row,
  confirmations,
}: {
  applicant: FileCheckApplicant;
  row: ConfirmableItemRow;
  confirmations?: VerdictConfirmations;
}) {
  const { t } = useTranslation();
  const confirmation = row.confirmation;
  const user = confirmation?.confirmed_by || t('fileCheck.confirm.unnamedUser');
  const time = formatConfirmedAt(confirmation?.confirmed_at);
  const key = confirmationRowKey(applicant, row);
  const busy = confirmations?.busyKey === key;
  const error =
    confirmations?.error?.key === key ? confirmations.error.error : null;
  return (
    <div className="mt-0.5 space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <p
          className="text-[11px] leading-snug text-emerald-800 dark:text-emerald-300"
          title={confirmation?.confirmed_at ?? undefined}
          data-testid="confirmed-by"
        >
          {time
            ? t('fileCheck.confirm.confirmedBy', { user, time })
            : t('fileCheck.confirm.confirmedByUnknownTime', { user })}
        </p>
        {confirmations && (
          <button
            type="button"
            onClick={() => confirmations.onUndo(applicant, row)}
            disabled={
              confirmations.busyKey !== null || !!confirmations.disabled
            }
            title={t('fileCheck.confirm.undoTitle')}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 disabled:pointer-events-none disabled:opacity-50 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-slate-200"
            data-testid="undo-confirmation"
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <Undo2 className="h-3 w-3" aria-hidden="true" />
            )}
            {t('fileCheck.confirm.undo')}
          </button>
        )}
      </div>
      {error != null && (
        <p
          role="alert"
          className="break-words text-[10px] text-red-700 dark:text-red-400"
        >
          {describeConfirmError(t, error)}
        </p>
      )}
    </div>
  );
}

function ItemRows({
  rows,
  applicant,
  confirmations,
}: {
  rows: FileCheckItemRow[];
  applicant: FileCheckApplicant;
  confirmations?: VerdictConfirmations;
}) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-1">
      {rows.map((row: ConfirmableItemRow, i) => {
        const confirmed = isConfirmedItem(row);
        return (
          <FindingRow
            key={`${row.item_id ?? row.item}-${i}`}
            tone={
              confirmed ? 'ok' : itemTone(row.status, row.required !== false)
            }
            label={row.item}
            statusText={statusLabel(t, row.status, ITEM_STATUS_KEYS)}
            optional={row.required === false}
            // The line below names who confirmed it, in the viewer's time.
            detail={confirmed && row.confirmation ? '' : row.detail}
            documents={row.documents ?? []}
            missingMonths={
              normalizeStatus(row.status) === 'MISSING'
                ? (row.missing_months ?? undefined)
                : undefined
            }
            painPoint={painPointForFinding({
              kind: 'item',
              status: row.status,
              required: row.required,
            })}
          >
            {confirmed ? (
              <ConfirmedLine
                applicant={applicant}
                row={row}
                confirmations={confirmations}
              />
            ) : (
              confirmations &&
              canConfirmItem(row) && (
                <ConfirmControls
                  applicant={applicant}
                  row={row}
                  confirmations={confirmations}
                />
              )
            )}
          </FindingRow>
        );
      })}
    </ul>
  );
}

function ConsistencyRows({ rows }: { rows: FileCheckConsistencyRow[] }) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-1">
      {rows.map((row, i) => (
        <FindingRow
          key={`${row.check_id ?? row.check}-${i}`}
          tone={consistencyTone(row.status)}
          label={row.check}
          statusText={statusLabel(t, row.status, CONSISTENCY_STATUS_KEYS)}
          detail={row.detail}
          documents={row.documents ?? []}
          painPoint={painPointForFinding({
            kind: 'consistency',
            checkId: row.check_id,
            status: row.status,
          })}
        />
      ))}
    </ul>
  );
}

function IncomeGrid({ income }: { income: FileCheckIncome }) {
  const { t } = useTranslation();
  const cells = (
    [
      ['fileCheck.income.declaredNet', income.declared_net],
      ['fileCheck.income.slipNet', income.slip_net],
      ['fileCheck.income.bankCredit', income.bank_salary_credit],
      ['fileCheck.income.slipGross', income.slip_gross],
      ['fileCheck.income.form16Gross', income.form16_gross],
    ] as const
  ).filter(([, v]) => typeof v === 'number');
  if (cells.length === 0) return null;
  return (
    <Section title={t('fileCheck.sections.income')}>
      <dl className="grid grid-cols-2 gap-1.5">
        {cells.map(([key, value]) => (
          <div
            key={key}
            className="rounded-lg border border-white/50 bg-white/30 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.03]"
          >
            <dt className="text-[10px] text-slate-500 dark:text-slate-400">
              {t(key)}
            </dt>
            <dd className="text-sm font-semibold tabular-nums text-slate-800 dark:text-slate-100">
              {formatInr(value)}
            </dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/** "Facts extraction (the file-check step only) cost $0.0123 for 5 documents (…tokens). …" */
export function usageTotalText(
  t: TFunction,
  total: ApplicantUsageTotal,
): string {
  if (total.documents_with_usage <= 0) return t('fileCheck.usage.none');
  const main = t('fileCheck.usage.total', {
    cost: formatUsd(total.cost_usd),
    count: total.documents_with_usage,
    input: formatTokens(total.input_tokens),
    output: formatTokens(total.output_tokens),
  });
  const missing = total.documents_total - total.documents_with_usage;
  return missing > 0
    ? `${main} ${t('fileCheck.usage.partial', { count: missing })}`
    : main;
}

interface ApplicantSectionProps {
  applicant: FileCheckApplicant;
  checklistId?: string | null;
  /** The checklist's product id, for the reminder's {{product}}. */
  product?: string | null;
  /** The verdict's as_of date, for the reminder's Form-16 / ITR year. */
  asOf?: string | null;
  /** The verdict's documents still being analysed (the reminder warns). */
  pendingDocuments?: PendingDocument[];
  /** Opens the erase confirmation; the trigger gets focus back on cancel. */
  onErase?: (applicant: FileCheckApplicant, trigger: HTMLElement) => void;
  /** Opens Eligibility & lenders for the applicant. */
  onOpenEligibility?: (applicant: FileCheckApplicant) => void;
  /** Confirm / undo on the needs-review checklist items. */
  confirmations?: VerdictConfirmations;
}

function ApplicantSection({
  applicant,
  checklistId,
  product,
  asOf,
  pendingDocuments,
  onErase,
  onOpenEligibility,
  confirmations,
}: ApplicantSectionProps) {
  const { t } = useTranslation();
  const reminderId = useId();
  const reminderButtonRef = useRef<HTMLButtonElement>(null);
  const [reminderOpen, setReminderOpen] = useState(false);
  const closeReminder = () => {
    setReminderOpen(false);
    reminderButtonRef.current?.focus();
  };
  const tone = verdictTone(applicant.verdict);
  const reasons = applicant.reasons ?? [];
  const needsReview = applicant.needs_review ?? [];
  const items = applicant.checklist ?? [];
  const checks = applicant.consistency ?? [];
  const docs = applicant.documents ?? [];
  const usageTotal = applicantUsageTotal(applicant);
  const month =
    applicant.reference_month_label ||
    (applicant.reference_month
      ? formatYearMonth(applicant.reference_month)
      : '');
  return (
    <section
      aria-label={applicant.applicant}
      className="space-y-3 rounded-xl border border-white/50 bg-white/20 p-3 dark:border-white/[0.08] dark:bg-white/[0.02]"
    >
      <header className="flex flex-wrap items-center gap-2">
        <UserRound className="h-4 w-4 text-slate-500 dark:text-slate-400" />
        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          {applicant.applicant}
        </h4>
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] font-bold tracking-wide ${PILL_CLASS[tone]}`}
        >
          {verdictLabel(t, applicant.verdict)}
        </span>
        <span className="ml-auto text-[10px] text-slate-500 dark:text-slate-400">
          {[
            month && t('fileCheck.referenceMonth', { month }),
            docs.length > 0 &&
              t('fileCheck.documentsCount', { count: docs.length }),
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </header>

      {(tone === 'notReady' || onOpenEligibility) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {onOpenEligibility && (
            <button
              type="button"
              onClick={() => onOpenEligibility(applicant)}
              title={t('eligibility.openHint')}
              className={`${ACTION_BUTTON_CLASS} border-indigo-200 bg-indigo-50/70 text-indigo-800 hover:bg-indigo-100 focus-visible:ring-indigo-500 dark:border-indigo-800/50 dark:bg-indigo-900/20 dark:text-indigo-300 dark:hover:bg-indigo-900/40`}
              data-testid="open-eligibility"
            >
              <Landmark className="h-3 w-3" aria-hidden="true" />
              {t('eligibility.open')}
            </button>
          )}
          {tone === 'notReady' && (
            <button
              ref={reminderButtonRef}
              type="button"
              onClick={() =>
                reminderOpen ? closeReminder() : setReminderOpen(true)
              }
              aria-expanded={reminderOpen}
              aria-controls={reminderOpen ? reminderId : undefined}
              className={`${ACTION_BUTTON_CLASS} border-emerald-200 bg-emerald-50/70 text-emerald-800 hover:bg-emerald-100 focus-visible:ring-emerald-500 dark:border-emerald-800/50 dark:bg-emerald-900/20 dark:text-emerald-300 dark:hover:bg-emerald-900/40`}
            >
              <MessageCircle className="h-3 w-3" aria-hidden="true" />
              {t('fileCheck.reminder.open')}
            </button>
          )}
        </div>
      )}
      {tone === 'notReady' && reminderOpen && (
        <ReminderDraft
          id={reminderId}
          applicant={applicant}
          checklistId={checklistId}
          product={product}
          asOf={asOf}
          pendingDocuments={pendingDocuments}
          onClose={closeReminder}
        />
      )}

      {reasons.length > 0 && (
        <Section
          title={t('fileCheck.sections.reasons', { count: reasons.length })}
        >
          <ul className="space-y-1">
            {reasons.map((reason, i) => (
              <li
                key={i}
                className={`break-words rounded-md border-l-4 px-2.5 py-1.5 text-[11px] leading-snug ${REASON_CLASS[reasonTone(reason)]}`}
              >
                {reason}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {needsReview.length > 0 && (
        <Section
          title={t('fileCheck.sections.needsReview', {
            count: needsReview.length,
          })}
        >
          <ul className="space-y-1">
            {needsReview.map((finding, i) => (
              <li
                key={i}
                data-tone="review"
                className={`flex items-start gap-2 break-words rounded-md border-l-4 px-2.5 py-1.5 text-[11px] leading-snug ${REASON_CLASS.review}`}
              >
                <NeedsPersonPill />
                <span className="min-w-0 flex-1">{finding}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {items.length > 0 && (
        <Section title={t('fileCheck.sections.checklist')}>
          <ItemRows
            rows={items}
            applicant={applicant}
            confirmations={confirmations}
          />
        </Section>
      )}

      {checks.length > 0 && (
        <Section title={t('fileCheck.sections.consistency')}>
          <ConsistencyRows rows={checks} />
        </Section>
      )}

      {applicant.income && <IncomeGrid income={applicant.income} />}

      <ObligationsSection
        obligations={applicant.obligations}
        foir={applicant.foir}
      />

      {docs.length > 0 && (
        <details className="group rounded-lg border border-white/50 bg-white/20 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.02]">
          <summary className="cursor-pointer select-none text-[11px] font-semibold text-slate-600 dark:text-slate-300">
            {t('fileCheck.sections.documents', { count: docs.length })}
          </summary>
          <ul className="mt-1.5 space-y-1">
            {docs.map((d, i) => {
              const unverified = d.unverified_fields ?? [];
              const usage = documentUsage(d);
              return (
                <li
                  key={`${d.document_id ?? d.document_name}-${i}`}
                  className="break-words text-[11px] text-slate-600 dark:text-slate-300"
                >
                  <span className="font-medium">{d.document_name}</span>
                  <span className="text-slate-400">
                    {' · '}
                    {d.doc_type.replace(/_/g, ' ')}
                  </span>
                  {unverified.length > 0 && (
                    <span className="ml-1 text-amber-700 dark:text-amber-400">
                      {t('fileCheck.unverified', {
                        fields: unverified.join(', '),
                      })}
                    </span>
                  )}
                  {usage ? (
                    <span
                      className="text-slate-500 dark:text-slate-400"
                      data-testid="document-usage"
                      title={
                        usage.model_id
                          ? t('fileCheck.usage.model', {
                              model: usage.model_id,
                            })
                          : undefined
                      }
                    >
                      {' · '}
                      {t('fileCheck.usage.perDocument', {
                        input: formatTokens(usage.input_tokens),
                        output: formatTokens(usage.output_tokens),
                        cost: formatUsd(usage.cost_usd),
                      })}
                    </span>
                  ) : (
                    usageTotal && (
                      <span className="text-slate-400 dark:text-slate-500">
                        {' · '}
                        {t('fileCheck.usage.notRecorded')}
                      </span>
                    )
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
      {docs.length > 0 && usageTotal && (
        <p
          data-testid="usage-total"
          className="text-[10px] leading-snug text-slate-500 dark:text-slate-400"
        >
          {usageTotalText(t, usageTotal)}
        </p>
      )}

      {onErase && (
        <div className="flex justify-end border-t border-black/[0.06] pt-2 dark:border-white/[0.06]">
          <button
            type="button"
            onClick={(e) => onErase(applicant, e.currentTarget)}
            className={`${ACTION_BUTTON_CLASS} border-red-200 bg-white/40 text-red-700 hover:border-red-300 hover:bg-red-50 focus-visible:ring-red-500 dark:border-red-800/50 dark:bg-transparent dark:text-red-400 dark:hover:bg-red-900/20`}
          >
            <Trash2 className="h-3 w-3" aria-hidden="true" />
            {t('fileCheck.erase.action')}
          </button>
        </div>
      )}
    </section>
  );
}

function SkippedDocuments({ result }: { result: FileCheckResult }) {
  const { t } = useTranslation();
  const rows = skippedDocuments(result);
  if (rows.length === 0) return null;
  return (
    <Section title={t('fileCheck.sections.notChecked')}>
      <ul className="space-y-1">
        {rows.map(({ status, document }, i) => {
          const meta = SKIPPED_STATUS[status];
          const tone = meta?.tone ?? 'info';
          const label = meta ? t(meta.key) : status;
          const detail =
            document.reason ||
            (document.status
              ? t(`documents.${document.status}`, document.status)
              : '') ||
            (document.doc_type ? document.doc_type.replace(/_/g, ' ') : '');
          return (
            <FindingRow
              key={`${status}-${document.document_id ?? i}`}
              tone={tone}
              label={document.document_name || document.document_id || '–'}
              statusText={label}
              detail={detail}
              documents={[]}
            />
          );
        })}
      </ul>
    </Section>
  );
}

/** Call recordings next to a loan file: not checked, reviewed with Call QA. */
function RecordingDocuments({ result }: { result: FileCheckResult }) {
  const { t } = useTranslation();
  const recordings = recordingDocuments(result);
  if (recordings.length === 0) return null;
  return (
    <div
      className="rounded-lg border border-sky-200/80 bg-sky-50/50 px-2.5 py-2 dark:border-sky-800/40 dark:bg-sky-900/10"
      data-testid="recording-documents"
    >
      <p className="flex items-center gap-1.5 text-[11px] font-semibold text-sky-900 dark:text-sky-200">
        <Headphones className="h-3.5 w-3.5" aria-hidden="true" />
        {t('fileCheck.callProject.mixedTitle', { count: recordings.length })}
      </p>
      <p className="mt-0.5 break-words text-[11px] text-slate-600 dark:text-slate-300">
        {recordings.map((d) => d.document_name || d.document_id).join(', ')}
      </p>
      <p className="mt-0.5 text-[10px] text-slate-500 dark:text-slate-400">
        {t('fileCheck.callProject.mixedHint')}
      </p>
    </div>
  );
}

interface VerdictCardProps {
  result: FileCheckResult;
  lastRunAt?: Date | null;
  /** Opens the "Why DSAs need this" card a finding's tag points to. */
  onPainPoint?: (id: PainPointId) => void;
  /** Product id of the result's checklist (fills a reminder's {{product}}). */
  product?: string | null;
  /** Shows "Erase applicant data" per applicant; opens the confirmation. */
  onEraseApplicant?: (
    applicant: FileCheckApplicant,
    trigger: HTMLElement,
  ) => void;
  /** Shows "Eligibility & lenders" per applicant. */
  onOpenEligibility?: (applicant: FileCheckApplicant) => void;
  /** Shows Confirm on needs-review checklist items and Undo on confirmed ones. */
  confirmations?: VerdictConfirmations;
}

export default function VerdictCard({
  result,
  lastRunAt,
  onPainPoint,
  product,
  onEraseApplicant,
  onOpenEligibility,
  confirmations,
}: VerdictCardProps) {
  const { t } = useTranslation();
  const applicants = result.applicants ?? [];
  const tone = verdictTone(result.overall_verdict);
  const headline =
    applicants.length === 1 ? applicants[0].applicant : result.summary || '';
  // Counted from the engine's needs_review lists; the verdict is not changed.
  const reviewCount = applicants.reduce(
    (n, a) => n + (a.needs_review?.length ?? 0),
    0,
  );
  // No applicant and no unchecked document (e.g. an empty project): the
  // engine's summary is the whole story, there is nothing "below" to fix.
  const nothingBelow =
    applicants.length === 0 && skippedDocuments(result).length === 0;
  const sub = nothingBelow
    ? ''
    : tone === 'ready'
      ? reviewCount > 0
        ? t('fileCheck.verdictSub.readyWithReview')
        : t('fileCheck.verdictSub.ready')
      : tone === 'notReady'
        ? t('fileCheck.verdictSub.notReady')
        : tone === 'review'
          ? t('fileCheck.verdictSub.needsReview')
          : '';
  const meta = [
    result.checklist?.name || result.checklist?.id,
    result.as_of && t('fileCheck.asOf', { date: result.as_of }),
    lastRunAt &&
      t('fileCheck.checkedAt', {
        time: lastRunAt.toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
        }),
      }),
  ].filter(Boolean);
  const bannerIcon =
    tone === 'ready' ? (
      <Check className="h-6 w-6" strokeWidth={3} />
    ) : tone === 'notReady' ? (
      <X className="h-6 w-6" strokeWidth={3} />
    ) : tone === 'review' ? (
      <span className="text-xl font-black leading-none">!</span>
    ) : (
      <Info className="h-6 w-6" />
    );

  return (
    <PainPointOpenContext.Provider value={onPainPoint ?? null}>
      <div className="space-y-3">
        <section
          aria-label={t('fileCheck.verdictLabel')}
          data-verdict={tone}
          className={`rounded-xl px-4 py-3 text-white shadow-sm ${BANNER_CLASS[tone]}`}
        >
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-white/20">
              {bannerIcon}
            </span>
            <span className="text-2xl font-extrabold tracking-wide">
              {verdictLabel(t, result.overall_verdict)}
            </span>
          </div>
          {headline && (
            <p className="mt-1.5 text-sm font-semibold">{headline}</p>
          )}
          {applicants.length !== 1 && applicants.length > 0 && (
            <p className="text-xs opacity-90">
              {t('fileCheck.applicantsCount', { count: applicants.length })}
            </p>
          )}
          {sub && <p className="mt-0.5 text-xs opacity-95">{sub}</p>}
          {reviewCount > 0 && (
            <p className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-white/20 px-2 py-0.5 text-[11px] font-semibold">
              <UserRound className="h-3 w-3" />
              {t('fileCheck.needsReviewBanner', { count: reviewCount })}
            </p>
          )}
          {meta.length > 0 && (
            <p className="mt-1 text-[11px] opacity-80">{meta.join(' · ')}</p>
          )}
        </section>

        {applicants.map((a, i) => (
          <ApplicantSection
            key={`${a.applicant}-${i}`}
            applicant={a}
            checklistId={result.checklist?.id}
            product={product}
            asOf={result.as_of}
            pendingDocuments={result.pending_documents ?? []}
            onErase={onEraseApplicant}
            onOpenEligibility={onOpenEligibility}
            confirmations={confirmations}
          />
        ))}

        <SkippedDocuments result={result} />
        <RecordingDocuments result={result} />
      </div>
    </PainPointOpenContext.Provider>
  );
}
