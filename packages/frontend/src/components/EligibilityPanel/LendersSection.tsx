import { useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  Calculator,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Landmark,
  Loader2,
  LogIn,
  RefreshCw,
  TriangleAlert,
  Trophy,
} from 'lucide-react';
import type {
  EligibilityFileCheck,
  EligibilityResult,
  LenderEligibility,
  LenderPolicy,
  SourceKind,
  StillNeeded,
} from '../../types/eligibility';
import type { LenderLoginState } from '../../hooks/useEligibility';
import {
  formatFoir,
  formatRoi,
  formatRupees,
  lenderTone,
  webhookOutcome,
  type LenderTone,
} from '../../lib/eligibility';
import { apiErrorDetail } from '../../lib/apiError';
import { apiErrorStatus } from '../../lib/fileCheck';
import {
  BUTTON_CLASS,
  IndicativeTag,
  PRIMARY_CLASS,
  SOURCE_CHIP_CLASS,
  SOURCE_RULE_CLASS,
  SamplePolicyTag,
  SourceLegend,
  StillNeededList,
  sourceKind,
} from './fields';
import { describeEligibilityError } from './errors';

// Renders POST .../eligibility/calculate as returned: every amount, EMI and
// ratio comes from the backend's deterministic formulas.

const STATUS_PILL: Record<LenderTone, string> = {
  eligible:
    'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50',
  notServiceable:
    'bg-slate-100 text-slate-700 border-slate-300 dark:bg-white/10 dark:text-slate-300 dark:border-white/15',
  notEligible:
    'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/50',
  unknown:
    'bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/50',
};

const STATUS_KEYS: Record<LenderTone, string> = {
  eligible: 'eligibility.lenders.statuses.eligible',
  notServiceable: 'eligibility.lenders.statuses.notServiceable',
  notEligible: 'eligibility.lenders.statuses.notEligible',
  unknown: '',
};

export function lenderStatusLabel(
  t: TFunction,
  row: Pick<LenderEligibility, 'status' | 'status_label'>,
): string {
  const key = STATUS_KEYS[lenderTone(row.status)];
  return key ? t(key) : row.status_label || row.status || '–';
}

/** 'policy' -> 'From Policy' (the sheet's legend); other values as sent. */
export function sourceLabel(t: TFunction, source: string | undefined): string {
  if (!source) return '';
  return t(`eligibility.lenders.sourceLabels.${source}`, source);
}

/** 'From Policy' + the company category that set the value: 'From Policy · CAT A'. */
function withCategory(source: string, category: string | null): string {
  return [source, category].filter(Boolean).join(' · ');
}

function months(t: TFunction, value: number | null): string {
  return value === null ? '–' : t('eligibility.months', { count: value });
}

const TH_CLASS =
  'px-1.5 py-1.5 text-left align-bottom text-[10px] font-semibold uppercase tracking-wide text-slate-500 @lg:px-2 dark:text-slate-400';
const TD_CLASS =
  'whitespace-nowrap px-1.5 py-1.5 text-right text-xs tabular-nums @lg:px-2';
// A lender that is not eligible offers nothing: its ₹0 amounts are muted.
const NUMBER_COLOR: Record<LenderTone, string> = {
  eligible: 'text-slate-800 dark:text-slate-100',
  notServiceable: 'text-slate-400 dark:text-slate-500',
  notEligible: 'text-slate-400 dark:text-slate-500',
  unknown: 'text-slate-800 dark:text-slate-100',
};
// Shown when the panel is wide; the details row has them otherwise.
const WIDE = 'hidden @3xl:table-cell';
const COLUMNS = 9;

function Metric({
  label,
  value,
  source,
  kind,
}: {
  label: string;
  value: string;
  source?: string;
  /** The source's colour (the sheet's legend). */
  kind?: SourceKind | null;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/50 bg-white/40 px-2 py-1 dark:border-white/[0.08] dark:bg-white/[0.03]">
      <dt className="text-[10px] text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd className="text-xs font-semibold tabular-nums text-slate-800 dark:text-slate-100">
        {value}
      </dd>
      {source && (
        <dd className="text-[9px] text-slate-500 dark:text-slate-400">
          {kind ? (
            <span
              className={`inline-block rounded-full border px-1 leading-snug ${SOURCE_CHIP_CLASS[kind]}`}
              data-source={kind}
            >
              {source}
            </span>
          ) : (
            source
          )}
        </dd>
      )}
    </div>
  );
}

/** The sheet's formulas with this lender's numbers, as the backend returned them. */
function HowCalculated({ row }: { row: LenderEligibility }) {
  const { t } = useTranslation();
  if (
    row.per_lakh_emi === null ||
    row.foir_eligibility === null ||
    row.multiplier_eligibility === null
  ) {
    return null;
  }
  const lines = [
    t('eligibility.lenders.how.perLakh', {
      roi: formatRoi(row.roi),
      months: row.calculation_tenure_months ?? '–',
      value: formatRupees(row.per_lakh_emi),
    }),
    t('eligibility.lenders.how.foir', {
      income: formatRupees(row.income_considered),
      foir: formatFoir(row.foir),
      obligations: formatRupees(row.obligations),
      perLakh: formatRupees(row.per_lakh_emi),
      value: formatRupees(row.foir_eligibility),
    }),
    t('eligibility.lenders.how.multiplier', {
      income: formatRupees(row.income_considered),
      multiplier: row.multiplier ?? '–',
      value: formatRupees(row.multiplier_eligibility),
    }),
    lenderTone(row.status) === 'eligible'
      ? t('eligibility.lenders.how.eligible', {
          max: formatRupees(row.max_amount),
          value: formatRupees(row.eligible_amount),
        })
      : t('eligibility.lenders.how.computed', {
          max: formatRupees(row.max_amount),
          value: formatRupees(row.computed_amount),
        }),
  ];
  if (lenderTone(row.status) === 'eligible' && row.emi !== null) {
    lines.push(
      t('eligibility.lenders.how.emi', {
        amount: formatRupees(row.eligible_amount),
        roi: formatRoi(row.roi),
        months: row.tenure_months ?? '–',
        value: formatRupees(row.emi),
      }),
    );
  }
  return (
    <div className="space-y-0.5" data-testid="how-calculated">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {t('eligibility.lenders.how.title')}
      </p>
      <ol className="list-decimal space-y-0.5 pl-4 text-[11px] leading-snug text-slate-700 dark:text-slate-200">
        {lines.map((line, i) => (
          <li key={i} className="break-words tabular-nums">
            {line}
          </li>
        ))}
      </ol>
    </div>
  );
}

interface LoginControlProps {
  row: LenderEligibility;
  applicantName: string;
  state: LenderLoginState | undefined;
  /** The result is for other inputs: log in only after a new calculation. */
  stale: boolean;
  busy: boolean;
  /** The file check of the result: a NOT READY file is logged in only once its issues are confirmed. */
  fileCheck: EligibilityFileCheck | null;
  onLogin: (row: LenderEligibility) => void;
  /** Shows the confirmation at first (tests render statically). */
  initiallyConfirming?: boolean;
}

/** How many open issues the NOT READY confirmation lists before "+N more". */
const SHOWN_ISSUES = 8;

/** The file check's open issues, as the NOT READY confirmation names them. */
export function OpenIssues({ issues }: { issues: string[] }) {
  const { t } = useTranslation();
  const more = issues.length - SHOWN_ISSUES;
  return (
    <div className="w-full space-y-0.5" data-testid="open-issues">
      <p className="flex items-center gap-1 font-semibold text-amber-900 dark:text-amber-200">
        <TriangleAlert className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
        {t('eligibility.lenders.notReadyTitle', { count: issues.length })}
      </p>
      {issues.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[10px] leading-snug text-amber-900 dark:text-amber-200">
          {issues.slice(0, SHOWN_ISSUES).map((issue, i) => (
            <li key={i} className="break-words">
              {issue}
            </li>
          ))}
          {more > 0 && (
            <li className="list-none text-amber-700 dark:text-amber-300">
              {t('eligibility.lenders.moreIssues', { count: more })}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

/** What the CRM webhook did with a login: notified, failed, or not sent (and why). */
function webhookText(
  t: TFunction,
  response: NonNullable<LenderLoginState['response']>,
): string {
  const outcome = webhookOutcome(response);
  const delivery = response.delivery;
  if (outcome === 'notified') {
    return delivery?.http_status != null
      ? t('eligibility.lenders.webhookNotifiedStatus', {
          status: delivery.http_status,
        })
      : t('eligibility.lenders.webhookNotified');
  }
  if (outcome === 'failed') {
    return t('eligibility.lenders.webhookFailed', {
      error:
        delivery?.error ||
        response.webhook_detail ||
        (delivery?.http_status != null
          ? `HTTP ${delivery.http_status}`
          : t('eligibility.lenders.noAnswer')),
    });
  }
  if (response.webhook === 'not_configured') {
    return t('eligibility.lenders.webhookNotConfigured');
  }
  if (response.webhook === 'skipped') {
    return t('eligibility.lenders.webhookSkipped', {
      reason: response.webhook_detail || t('eligibility.lenders.noAnswer'),
    });
  }
  return t('eligibility.lenders.webhookNone');
}

function LoginControl({
  row,
  applicantName,
  state,
  stale,
  busy,
  fileCheck,
  onLogin,
  initiallyConfirming = false,
}: LoginControlProps) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(initiallyConfirming);
  const eligible = lenderTone(row.status) === 'eligible';
  const sending = state?.sending ?? false;
  const response = state?.response ?? null;
  const outcome = response ? webhookOutcome(response) : null;
  const notReady = fileCheck?.ready === false;

  let result: ReactNode = null;
  if (sending) {
    result = (
      <span className="inline-flex items-center gap-1 text-slate-500 dark:text-slate-400">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        {t('eligibility.lenders.loggingIn')}
      </span>
    );
  } else if (state?.error != null && apiErrorStatus(state.error) === 428) {
    // The file check turned NOT READY after this result: its issues are in the answer.
    const detail = apiErrorDetail(state.error);
    result = (
      <span
        className="inline-flex flex-wrap items-center gap-1.5 text-amber-800 dark:text-amber-300"
        data-testid="login-not-ready"
      >
        <TriangleAlert className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
        <span className="min-w-0 break-words">
          {t('eligibility.lenders.notReadyNow')}
          {detail ? ` ${detail}` : ''}
        </span>
        <button
          type="button"
          onClick={() => onLogin(row)}
          disabled={stale || busy}
          className={BUTTON_CLASS}
          data-testid="confirm-login-not-ready"
        >
          <LogIn className="h-3 w-3" aria-hidden="true" />
          {t('eligibility.lenders.confirmLoginAnyway')}
        </button>
      </span>
    );
  } else if (state?.error != null) {
    result = (
      <span className="text-red-600 dark:text-red-400">
        {t('eligibility.lenders.loginFailed', {
          message: describeEligibilityError(t, state.error),
        })}
      </span>
    );
  } else if (response) {
    const time = state?.at
      ? state.at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : '';
    result = (
      <span className="space-x-1">
        <span className="font-semibold text-green-700 dark:text-green-400">
          {t('eligibility.lenders.loggedIn', { lender: row.lender, time })}
        </span>
        {response.file_ready === false && (
          <span
            className="font-medium text-amber-700 dark:text-amber-400"
            data-testid="logged-in-not-ready"
          >
            {t('eligibility.lenders.loggedInNotReady', {
              count: response.open_issues,
            })}
          </span>
        )}
        <span
          data-testid="webhook-outcome"
          data-outcome={outcome ?? 'notSent'}
          className={
            outcome === 'notified'
              ? 'text-green-700 dark:text-green-400'
              : outcome === 'failed'
                ? 'text-amber-700 dark:text-amber-400'
                : 'text-slate-500 dark:text-slate-400'
          }
        >
          {webhookText(t, response)}
        </span>
      </span>
    );
  }

  if (!eligible) {
    // The backend logs a file in only with an eligible lender (409 otherwise).
    return (
      <p className="text-[10px] text-slate-500 dark:text-slate-400">
        {t('eligibility.lenders.loginOnlyEligible')}
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
      {confirming ? (
        <div
          role="group"
          aria-label={t('eligibility.lenders.login')}
          onKeyDown={(e) => {
            // Escape cancels the confirmation only (the panel stays open).
            if (e.key === 'Escape') {
              e.preventDefault();
              setConfirming(false);
            }
          }}
          className={`inline-flex max-w-full flex-wrap items-center gap-1.5 rounded-lg border px-2 py-1 ${
            notReady
              ? 'border-amber-300 bg-amber-50/90 dark:border-amber-700/60 dark:bg-amber-900/20'
              : 'border-indigo-200 bg-indigo-50/80 dark:border-indigo-800/50 dark:bg-indigo-900/20'
          }`}
          data-testid={notReady ? 'confirm-not-ready' : 'confirm'}
        >
          {/* A NOT READY file: the user confirms its open issues by name. */}
          {notReady && <OpenIssues issues={fileCheck?.issues ?? []} />}
          <span
            className={
              notReady
                ? 'text-amber-900 dark:text-amber-200'
                : 'text-indigo-900 dark:text-indigo-200'
            }
          >
            {notReady
              ? t('eligibility.lenders.loginConfirmNotReady', {
                  name: applicantName,
                  lender: row.lender,
                })
              : t('eligibility.lenders.loginConfirm', {
                  name: applicantName,
                  lender: row.lender,
                })}
          </span>
          <button
            type="button"
            onClick={() => {
              setConfirming(false);
              onLogin(row);
            }}
            className={PRIMARY_CLASS}
            autoFocus
            data-testid="confirm-login"
          >
            <LogIn className="h-3 w-3" aria-hidden="true" />
            {notReady
              ? t('eligibility.lenders.confirmLoginAnyway')
              : t('eligibility.lenders.confirmLogin')}
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className={BUTTON_CLASS}
          >
            {t('common.cancel')}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={stale || busy || sending}
          title={
            stale
              ? t('eligibility.lenders.loginNeedsFresh')
              : notReady
                ? t('eligibility.lenders.loginNotReadyHint')
                : undefined
          }
          className={PRIMARY_CLASS}
          data-testid="login-button"
        >
          {notReady ? (
            <TriangleAlert className="h-3 w-3" aria-hidden="true" />
          ) : (
            <LogIn className="h-3 w-3" aria-hidden="true" />
          )}
          {response
            ? t('eligibility.lenders.loginAgain')
            : t('eligibility.lenders.login')}
          <span className="sr-only">: {row.lender}</span>
        </button>
      )}
      {result && (
        <span role="status" className="min-w-0 break-words">
          {result}
        </span>
      )}
    </div>
  );
}

interface LenderRowsProps {
  row: LenderEligibility;
  best: boolean;
  expanded: boolean;
  onToggle: () => void;
  applicantName: string;
  login: LenderLoginState | undefined;
  stale: boolean;
  busy: boolean;
  fileCheck: EligibilityFileCheck | null;
  onLogin: (row: LenderEligibility) => void;
  confirming?: boolean;
}

function LenderRows({
  row,
  best,
  expanded,
  onToggle,
  applicantName,
  login,
  stale,
  busy,
  fileCheck,
  onLogin,
  confirming,
}: LenderRowsProps) {
  const { t } = useTranslation();
  const detailsId = useId();
  const tone = lenderTone(row.status);
  const src = (key: string) => sourceLabel(t, row.sources[key]);
  const kind = (key: string) => sourceKind(row.sources[key]);
  const td = `${TD_CLASS} ${NUMBER_COLOR[tone]}`;
  return (
    <tbody
      className={`border-t border-black/[0.06] dark:border-white/[0.08] ${
        best
          ? 'bg-emerald-50/80 shadow-[inset_4px_0_0_0_rgb(16,185,129)] dark:bg-emerald-900/20'
          : ''
      }`}
      data-testid="lender-row"
      data-lender={row.lender}
      data-best={best ? 'true' : undefined}
      data-tone={tone}
    >
      <tr className="align-top">
        <th
          scope="row"
          className="px-1.5 py-1.5 text-left text-xs font-semibold text-slate-800 @lg:px-2 dark:text-slate-100"
        >
          <span className="block min-w-[5rem]">{row.lender}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1">
            <span
              className={`inline-block whitespace-nowrap rounded-full border px-1.5 py-px text-[10px] font-semibold ${STATUS_PILL[tone]}`}
              data-testid="lender-status"
            >
              {lenderStatusLabel(t, row)}
            </span>
            {best && (
              <span
                className="inline-flex items-center gap-0.5 whitespace-nowrap rounded-full bg-emerald-600 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-white"
                data-testid="best-lender"
              >
                <Trophy className="h-2.5 w-2.5" aria-hidden="true" />
                {t('eligibility.lenders.best')}
              </span>
            )}
          </span>
        </th>
        <td className={`${td} font-semibold`} data-col="eligible_amount">
          {formatRupees(row.eligible_amount)}
        </td>
        <td className={td} data-col="tenure_months">
          {row.tenure_months === null ? '–' : row.tenure_months}
        </td>
        <td className={td} data-col="roi">
          {formatRoi(row.roi)}
        </td>
        <td className={td} data-col="emi">
          {formatRupees(row.emi)}
        </td>
        <td className={`${td} ${WIDE}`} data-col="per_lakh_emi">
          {formatRupees(row.per_lakh_emi)}
        </td>
        <td className={`${td} ${WIDE}`} data-col="foir_eligibility">
          {formatRupees(row.foir_eligibility)}
        </td>
        <td className={`${td} ${WIDE}`} data-col="multiplier_eligibility">
          {formatRupees(row.multiplier_eligibility)}
        </td>
        <td className={`${td} ${WIDE}`} data-col="bt_amount">
          {formatRupees(row.bt_amount)}
        </td>
      </tr>
      {/* The client's "Login | Details" line */}
      <tr>
        <td colSpan={COLUMNS} className="px-1.5 pb-2 pt-0 @lg:px-2">
          {row.reasons.length > 0 && (
            <ul className="mb-1 space-y-0.5 text-[10px] leading-snug text-slate-600 dark:text-slate-300">
              {row.reasons.map((reason, i) => (
                <li key={i} className="break-words">
                  {reason}
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <LoginControl
              row={row}
              applicantName={applicantName}
              state={login}
              stale={stale}
              busy={busy}
              fileCheck={fileCheck}
              onLogin={onLogin}
              initiallyConfirming={confirming}
            />
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              aria-controls={expanded ? detailsId : undefined}
              className="inline-flex items-center gap-0.5 rounded text-[11px] font-medium text-indigo-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-indigo-300"
            >
              {expanded ? (
                <ChevronDown className="h-3 w-3" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3 w-3" aria-hidden="true" />
              )}
              {t('eligibility.lenders.details')}
              <span className="sr-only">: {row.lender}</span>
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={COLUMNS} className="px-1.5 pb-2.5 pt-0 @lg:px-2">
            <div
              id={detailsId}
              className="space-y-2"
              data-testid="lender-details"
            >
              <dl className="grid grid-cols-2 gap-1.5 @md:grid-cols-4">
                <Metric
                  label={t('eligibility.lenders.perLakhEmi')}
                  value={formatRupees(row.per_lakh_emi)}
                  source={src('per_lakh_emi')}
                  kind={kind('per_lakh_emi')}
                />
                <Metric
                  label={t('eligibility.lenders.foirEligibility')}
                  value={formatRupees(row.foir_eligibility)}
                  source={src('foir_eligibility')}
                  kind={kind('foir_eligibility')}
                />
                <Metric
                  label={t('eligibility.lenders.multiplierEligibility')}
                  value={formatRupees(row.multiplier_eligibility)}
                  source={src('multiplier_eligibility')}
                  kind={kind('multiplier_eligibility')}
                />
                <Metric
                  label={t('eligibility.lenders.btAmount')}
                  value={formatRupees(row.bt_amount)}
                  source={src('bt_amount')}
                  kind={kind('bt_amount')}
                />
                <Metric
                  label={t('eligibility.lenders.incomeConsidered')}
                  value={formatRupees(row.income_considered)}
                  source={src('income')}
                  kind={kind('income')}
                />
                <Metric
                  label={t('eligibility.lenders.obligations')}
                  value={formatRupees(row.obligations)}
                  source={src('obligations')}
                  kind={kind('obligations')}
                />
                <Metric
                  label={t('eligibility.lenders.foir')}
                  value={formatFoir(row.foir)}
                  source={withCategory(src('foir'), row.company_category)}
                  kind={kind('foir')}
                />
                <Metric
                  label={t('eligibility.lenders.multiplier')}
                  value={row.multiplier === null ? '–' : `${row.multiplier}×`}
                  source={withCategory(src('multiplier'), row.company_category)}
                  kind={kind('multiplier')}
                />
                <Metric
                  label={t('eligibility.lenders.calcTenure')}
                  value={months(t, row.calculation_tenure_months)}
                  source={src('calculation_tenure_months')}
                  kind={kind('calculation_tenure_months')}
                />
                {lenderTone(row.status) === 'eligible' &&
                  row.calculation_tenure_months !== null &&
                  row.calculation_tenure_months !== row.tenure_months && (
                    <Metric
                      label={t('eligibility.lenders.emiAtCalcTenure', {
                        count: row.calculation_tenure_months,
                      })}
                      value={formatRupees(row.emi_at_calculation_tenure)}
                      source={src('emi')}
                      kind={kind('emi')}
                    />
                  )}
                <Metric
                  label={t('eligibility.lenders.maxAmount')}
                  value={formatRupees(row.max_amount)}
                  source={src('max_amount')}
                  kind={kind('max_amount')}
                />
              </dl>
              <HowCalculated row={row} />
              {row.other_income_considered.length > 0 && (
                <p className="text-[10px] leading-snug text-slate-600 dark:text-slate-300">
                  <span className="font-semibold">
                    {t('eligibility.lenders.otherIncomeCounted')}
                  </span>{' '}
                  {row.other_income_considered
                    .map((o) =>
                      t('eligibility.lenders.otherIncomeRow', {
                        label: o.label || o.type,
                        pct: o.consideration_pct ?? '–',
                        monthly: formatRupees(o.monthly_amount),
                        value: formatRupees(o.considered),
                      }),
                    )
                    .join(' · ')}
                </p>
              )}
              {row.notes.length > 0 && (
                <ul className="space-y-0.5 text-[10px] leading-snug text-slate-600 dark:text-slate-300">
                  {row.notes.map((note, i) => (
                    <li
                      key={i}
                      className="break-words rounded border-l-2 border-slate-300 pl-1.5 dark:border-white/20"
                    >
                      {note}
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-[9px] text-slate-400">
                {t('eligibility.lenders.sourcesLegend')}
              </p>
            </div>
          </td>
        </tr>
      )}
    </tbody>
  );
}

function PolicyTable({ lenders }: { lenders: LenderPolicy[] }) {
  const { t } = useTranslation();
  if (lenders.length === 0) return null;
  return (
    <details className="rounded-lg border border-white/50 bg-white/20 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.02]">
      <summary className="cursor-pointer select-none text-[11px] font-semibold text-slate-600 dark:text-slate-300">
        {t('eligibility.lenders.policies', { count: lenders.length })}{' '}
        <SamplePolicyTag className="align-middle" />
      </summary>
      <div className="mt-1.5 overflow-x-auto">
        <table
          className="w-full text-[11px] text-slate-700 dark:text-slate-200"
          data-testid="lender-policies"
        >
          <thead>
            <tr>
              <th scope="col" className={TH_CLASS}>
                {t('eligibility.lenders.lender')}
              </th>
              <th scope="col" className={`${TH_CLASS} text-right`}>
                {t('eligibility.lenders.roi')}
              </th>
              <th scope="col" className={`${TH_CLASS} text-right`}>
                {t('eligibility.lenders.maxTenure')}
              </th>
              <th scope="col" className={`${TH_CLASS} text-right`}>
                {t('eligibility.lenders.foir')}
              </th>
              <th scope="col" className={`${TH_CLASS} text-right`}>
                {t('eligibility.lenders.multiplier')}
              </th>
              <th scope="col" className={`${TH_CLASS} text-right`}>
                {t('eligibility.lenders.maxAmount')}
              </th>
              <th scope="col" className={`${TH_CLASS} text-right`}>
                {t('eligibility.lenders.minScore')}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-black/[0.06] dark:divide-white/[0.06]">
            {lenders.map((p) => (
              <tr key={p.id}>
                <th
                  scope="row"
                  className="px-2 py-1 text-left font-medium text-slate-800 dark:text-slate-100"
                >
                  {p.name}
                </th>
                <td className="px-2 py-1 text-right tabular-nums">
                  {formatRoi(p.roi)}
                </td>
                <td className="whitespace-nowrap px-2 py-1 text-right tabular-nums">
                  {months(t, p.max_tenure_months)}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {formatFoir(p.foir)}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {p.multiplier === null ? '–' : `${p.multiplier}×`}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {formatRupees(p.max_amount)}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {p.min_cibil_score ?? '–'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1 text-[10px] text-slate-500 dark:text-slate-400">
          {t('eligibility.lenders.policiesNote')}
        </p>
      </div>
    </details>
  );
}

function SummaryTile({
  label,
  children,
  sub,
  tone = 'plain',
  testId,
}: {
  label: string;
  children: ReactNode;
  sub?: string | null;
  tone?: 'plain' | 'best';
  testId?: string;
}) {
  return (
    <div
      className={`min-w-0 rounded-lg border px-2.5 py-1.5 ${
        tone === 'best'
          ? 'border-emerald-200 bg-emerald-50/70 dark:border-emerald-800/50 dark:bg-emerald-900/20'
          : 'border-white/50 bg-white/30 dark:border-white/[0.08] dark:bg-white/[0.03]'
      }`}
    >
      <dt
        className={`text-[10px] ${
          tone === 'best'
            ? 'text-emerald-700 dark:text-emerald-400'
            : 'text-slate-500 dark:text-slate-400'
        }`}
      >
        {label}
      </dt>
      <dd
        className="text-sm font-semibold tabular-nums text-slate-800 dark:text-slate-100"
        data-testid={testId}
      >
        {children}
      </dd>
      {sub && (
        <dd className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
          {sub}
        </dd>
      )}
    </div>
  );
}

interface LendersSectionProps {
  /** The applicant's name (the login confirmation names them). */
  applicantName: string;
  result: EligibilityResult | null;
  /** The inputs changed since the result was calculated. */
  stale: boolean;
  calculating: boolean;
  calcError: unknown;
  calculatedAt?: Date | null;
  onCalculate: () => void;
  /** false while a typed number is invalid (the footer says which). */
  canCalculate?: boolean;
  /** GET .../lenders, for the policy table. */
  lenders?: LenderPolicy[];
  logins?: Record<string, LenderLoginState>;
  onLogin: (row: LenderEligibility) => void;
  /**
   * "Still needed before Check eligibility" (lib/eligibility stillNeeded of
   * the inputs on screen), shown before the first calculation.
   */
  stillNeeded?: StillNeeded[];
  /** Lenders whose details show at first; default: the best lender. */
  initialExpanded?: string[];
  /** Lenders whose login confirmation shows at first (tests render statically). */
  initialConfirming?: string[];
}

// The lenders table's columns underlined in their source's colour, as the
// sheet colours its cells.
const COLUMN_SOURCES: readonly [string, SourceKind][] = [
  ['eligible_amount', 'formula'],
  ['tenure_months', 'policy'],
  ['roi', 'policy'],
  ['emi', 'formula'],
  ['per_lakh_emi', 'formula'],
  ['foir_eligibility', 'formula'],
  ['multiplier_eligibility', 'formula'],
  ['bt_amount', 'table'],
];

const COLUMN_LABELS: Record<string, string> = {
  eligible_amount: 'eligibility.lenders.eligibleAmount',
  tenure_months: 'eligibility.lenders.tenure',
  roi: 'eligibility.lenders.roi',
  emi: 'eligibility.lenders.emi',
  per_lakh_emi: 'eligibility.lenders.perLakhEmi',
  foir_eligibility: 'eligibility.lenders.foirEligibility',
  multiplier_eligibility: 'eligibility.lenders.multiplierEligibility',
  bt_amount: 'eligibility.lenders.btAmount',
};

/** The file check behind the result: READY, or NOT READY with how many open issues. */
function FileCheckLine({ fileCheck }: { fileCheck: EligibilityFileCheck }) {
  const { t } = useTranslation();
  if (fileCheck.ready === null) return null;
  return fileCheck.ready ? (
    <p
      className="flex items-center gap-1 text-[11px] text-green-700 dark:text-green-400"
      data-testid="file-check-verdict"
      data-ready="true"
    >
      <CircleCheck className="h-3 w-3" aria-hidden="true" />
      {t('eligibility.lenders.readyNote')}
    </p>
  ) : (
    <p
      className="flex items-start gap-1 rounded-md border-l-4 border-amber-500 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-snug text-amber-900 dark:bg-amber-900/20 dark:text-amber-200"
      data-testid="file-check-verdict"
      data-ready="false"
    >
      <TriangleAlert
        className="mt-0.5 h-3 w-3 flex-shrink-0"
        aria-hidden="true"
      />
      {t('eligibility.lenders.notReadyNote', {
        count: fileCheck.issues.length,
      })}
    </p>
  );
}

/** Sheet 3 of the client's page: eligibility per lender, the best highlighted, and login. */
export default function LendersSection({
  applicantName,
  result,
  stale,
  calculating,
  calcError,
  calculatedAt,
  onCalculate,
  canCalculate = true,
  lenders = [],
  logins = {},
  onLogin,
  stillNeeded,
  initialExpanded,
  initialConfirming = [],
}: LendersSectionProps) {
  const { t } = useTranslation();
  const bestOf = (r: EligibilityResult | null) =>
    new Set(r?.best_lender ? [r.best_lender] : []);
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    initialExpanded ? new Set(initialExpanded) : bestOf(result),
  );
  const [shownFor, setShownFor] = useState(result);
  // A new calculation opens the new best lender's details.
  if (shownFor !== result) {
    setShownFor(result);
    setExpanded(bestOf(result));
  }
  const toggle = (lender: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(lender)) next.delete(lender);
      else next.add(lender);
      return next;
    });

  const rows = result?.per_lender ?? [];
  const best = rows.find((r) => r.lender === result?.best_lender) ?? null;
  const eligibleCount = rows.filter(
    (r) => lenderTone(r.status) === 'eligible',
  ).length;
  const flags = (result?.counted_obligations ?? [])
    .map((c) => c.flag)
    .filter((f): f is string => !!f);
  const incomeSource = result?.income?.net_salary_source_label ?? null;

  const calculateButton = (
    <button
      type="button"
      onClick={onCalculate}
      disabled={calculating || !canCalculate}
      className={PRIMARY_CLASS}
    >
      {calculating ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
      ) : result ? (
        <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
      ) : (
        <Calculator className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {calculating
        ? t('eligibility.lenders.calculating')
        : result
          ? t('eligibility.lenders.recalculate')
          : t('eligibility.lenders.calculate')}
    </button>
  );

  return (
    <div className="space-y-3">
      {calcError != null && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
        >
          <p className="font-semibold">{t('eligibility.lenders.calcFailed')}</p>
          <p className="mt-0.5 break-words">
            {describeEligibilityError(t, calcError)}
          </p>
        </div>
      )}

      <SourceLegend />
      {!result ? (
        <>
          {stillNeeded && <StillNeededList items={stillNeeded} tab="lenders" />}
          <div className="flex flex-col items-center justify-center gap-2 px-4 py-8 text-center">
            <Landmark
              className="h-8 w-8 text-slate-300 dark:text-slate-500"
              aria-hidden="true"
            />
            <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
              {t('eligibility.lenders.empty')}
            </p>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              {t('eligibility.lenders.emptyHint')}
            </p>
            {calculateButton}
          </div>
        </>
      ) : (
        <>
          {result.file_check && <FileCheckLine fileCheck={result.file_check} />}
          {stale && (
            <div
              role="status"
              className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:border-amber-800/50 dark:bg-amber-900/20 dark:text-amber-300"
              data-testid="stale-result"
            >
              <span className="min-w-0 flex-1">
                {t('eligibility.lenders.stale')}
              </span>
              {calculateButton}
            </div>
          )}

          <dl className="grid grid-cols-1 gap-1.5 @sm:grid-cols-3">
            <SummaryTile
              label={t('eligibility.lenders.netIncome')}
              sub={incomeSource}
              testId="income-considered"
            >
              {formatRupees(result.income_considered)}
              <span className="ml-1 text-[10px] font-normal text-slate-500">
                {t('eligibility.lenders.perMonth')}
              </span>
            </SummaryTile>
            <SummaryTile
              label={t('eligibility.lenders.obligations')}
              sub={t('eligibility.lenders.obligationsCounted', {
                count: result.counted_obligations.length,
              })}
              testId="obligations"
            >
              {formatRupees(result.obligations)}
              <span className="ml-1 text-[10px] font-normal text-slate-500">
                {t('eligibility.lenders.perMonth')}
              </span>
            </SummaryTile>
            <SummaryTile
              label={t('eligibility.lenders.best')}
              sub={best ? result.best_lender_reason : null}
              tone="best"
              testId="best-summary"
            >
              {best ? (
                <>
                  {best.lender}{' '}
                  <span className="tabular-nums">
                    {formatRupees(best.eligible_amount)}
                  </span>
                </>
              ) : (
                t('eligibility.lenders.bestNone')
              )}
            </SummaryTile>
          </dl>

          {flags.length > 0 && (
            <ul className="space-y-1">
              {flags.map((flag, i) => (
                <li
                  key={i}
                  className="break-words rounded-md border-l-4 border-amber-500 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-snug text-amber-900 dark:bg-amber-900/20 dark:text-amber-200"
                >
                  {flag}
                </li>
              ))}
            </ul>
          )}

          <div
            className={`overflow-x-auto rounded-lg border border-white/50 bg-white/20 dark:border-white/[0.08] dark:bg-white/[0.02] ${
              stale ? 'opacity-60' : ''
            }`}
          >
            <table
              className="w-full border-collapse"
              data-testid="lenders-table"
            >
              <caption className="px-2 pt-2 text-left text-[11px] font-semibold text-slate-600 dark:text-slate-300">
                <span className="mr-1.5">
                  {t('eligibility.lenders.caption', {
                    eligible: eligibleCount,
                    count: rows.length,
                  })}
                </span>
                {result.sample && (
                  <SamplePolicyTag className="mr-1 align-middle" />
                )}
                <IndicativeTag className="align-middle" />
              </caption>
              <thead>
                <tr>
                  <th scope="col" className={TH_CLASS}>
                    {t('eligibility.lenders.lenderStatus')}
                  </th>
                  {COLUMN_SOURCES.map(([col, fallback], i) => {
                    const kind = sourceKind(rows[0]?.sources[col]) ?? fallback;
                    return (
                      <th
                        key={col}
                        scope="col"
                        className={`${TH_CLASS} ${i >= 4 ? WIDE : ''} text-right ${SOURCE_RULE_CLASS[kind]}`}
                        title={sourceLabel(t, kind)}
                        data-source={kind}
                      >
                        {t(COLUMN_LABELS[col])}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              {rows.map((row) => (
                <LenderRows
                  key={row.lender}
                  row={row}
                  best={row.lender === result.best_lender}
                  expanded={expanded.has(row.lender)}
                  onToggle={() => toggle(row.lender)}
                  applicantName={applicantName}
                  login={logins[row.lender]}
                  stale={stale}
                  busy={calculating}
                  fileCheck={result.file_check}
                  onLogin={onLogin}
                  confirming={initialConfirming.includes(row.lender)}
                />
              ))}
            </table>
          </div>

          <ul
            className="space-y-1 text-[10px] leading-snug text-slate-500 dark:text-slate-400"
            data-testid="disclaimers"
          >
            {result.disclaimers.length > 0 ? (
              result.disclaimers.map((d, i) => <li key={i}>{d}</li>)
            ) : (
              <li className="flex flex-wrap items-center gap-1">
                {result.sample && <SamplePolicyTag />}
                <span className="font-semibold text-slate-600 dark:text-slate-300">
                  {t('eligibility.indicative')}
                </span>
              </li>
            )}
            {result.notes.map((note, i) => (
              <li key={`n${i}`}>{note}</li>
            ))}
            {calculatedAt && (
              <li>
                {t('eligibility.lenders.calculatedAt', {
                  time: calculatedAt.toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  }),
                })}
              </li>
            )}
          </ul>
        </>
      )}

      <PolicyTable lenders={lenders} />
    </div>
  );
}
