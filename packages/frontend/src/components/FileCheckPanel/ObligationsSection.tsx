import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { ExternalLink, Landmark } from 'lucide-react';
import type {
  FileCheckDebit,
  FileCheckDeclaredEmi,
  FileCheckFoir,
  FileCheckObligations,
} from '../../types/fileCheck';
import {
  consistencyTone,
  emiStatusTone,
  formatInr,
  loanEmiRows,
  monthRangeLabel,
  normalizeStatus,
  ordinal,
  possibleEmiPayees,
  safeHttpUrl,
  unmatchedDeclaredEmis,
  type EmiDeclarationStatus,
  type StatusTone,
} from '../../lib/fileCheck';
import PainPointTag from './PainPointTag';

// Renders the engine's obligations and indicative FOIR exactly as returned:
// nothing here adds up debits or recomputes a ratio.

const TILE_CLASS =
  'rounded-lg border border-white/50 bg-white/30 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.03]';

const ROW_CLASS: Record<StatusTone, string> = {
  ok: 'border-white/50 bg-white/30 dark:border-white/[0.08] dark:bg-white/[0.03]',
  bad: 'border-red-200/80 bg-red-50/50 dark:border-red-800/40 dark:bg-red-900/10',
  review:
    'border-amber-300/80 bg-amber-50/60 dark:border-amber-700/50 dark:bg-amber-900/10',
  info: 'border-blue-200/70 bg-blue-50/40 dark:border-blue-800/40 dark:bg-blue-900/10',
  muted:
    'border-white/50 bg-white/20 dark:border-white/[0.06] dark:bg-white/[0.02]',
};

const PILL_CLASS: Record<StatusTone, string> = {
  ok: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  bad: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  review:
    'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  info: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  muted: 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-300',
};

const EMI_STATUS_KEYS: Record<EmiDeclarationStatus, string> = {
  matched: 'fileCheck.obligations.emiStatus.matched',
  partial: 'fileCheck.obligations.emiStatus.partial',
  amount_differs: 'fileCheck.obligations.emiStatus.amountDiffers',
  declared: 'fileCheck.obligations.emiStatus.declared',
  not_declared: 'fileCheck.obligations.emiStatus.notDeclared',
  not_checked: 'fileCheck.obligations.emiStatus.notChecked',
};

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function categoryLabel(t: TFunction, category: unknown): string {
  const c = text(category);
  if (!c) return '';
  return t(`fileCheck.obligations.category.${c}`, c.replace(/_/g, ' '));
}

/** 'on the 5th · 6 of 6 months (Mar 2026 – Aug 2026) · ACH' */
function debitMeta(t: TFunction, debit: FileCheckDebit): string {
  const parts: string[] = [];
  const day = num(debit.day_of_month);
  if (day !== null) {
    parts.push(
      t('fileCheck.obligations.onDay', { day, ordinal: ordinal(day) }),
    );
  }
  const seen = num(debit.months_seen);
  const total = num(debit.months_total);
  if (seen !== null) {
    const months =
      total !== null
        ? t('fileCheck.obligations.monthsSeen', { seen, total })
        : t('fileCheck.obligations.monthsSeenOnly', { count: seen });
    const range = monthRangeLabel(debit.months);
    parts.push(range ? `${months} (${range})` : months);
  }
  const channel = text(debit.channel);
  if (channel && channel !== 'other') parts.push(channel);
  return parts.join(' · ');
}

function debitAmount(t: TFunction, debit: FileCheckDebit): string {
  const amount = num(debit.amount);
  const min = num(debit.min_amount);
  const max = num(debit.max_amount);
  if (debit.fixed === false && amount !== null) {
    return min !== null && max !== null && min !== max
      ? t('fileCheck.obligations.averageRange', {
          amount: formatInr(amount),
          min: formatInr(min),
          max: formatInr(max),
        })
      : t('fileCheck.obligations.average', { amount: formatInr(amount) });
  }
  return formatInr(amount);
}

function declaredLabel(d: FileCheckDeclaredEmi): string {
  const who = text(d.lender);
  const kind = text(d.loan_type);
  return [formatInr(num(d.amount)), who, kind && `(${kind})`]
    .filter(Boolean)
    .join(' ');
}

function SubHeading({ children }: { children: ReactNode }) {
  return (
    <h6 className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
    </h6>
  );
}

function DebitRow({
  tone,
  debit,
  pill,
  children,
}: {
  tone: StatusTone;
  debit: FileCheckDebit;
  pill?: string;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const payee = text(debit.payee) || text(debit.narration) || '–';
  const category =
    debit.category && debit.category !== 'loan_emi'
      ? categoryLabel(t, debit.category)
      : '';
  const meta = debitMeta(t, debit);
  return (
    <li
      className={`rounded-lg border px-2.5 py-1.5 ${ROW_CLASS[tone]}`}
      data-tone={tone}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="min-w-0 break-words text-xs font-semibold text-slate-800 dark:text-slate-100">
          {payee}
        </span>
        {category && (
          <span className="text-[10px] text-slate-500 dark:text-slate-400">
            {category}
          </span>
        )}
        <span className="ml-auto text-xs font-semibold tabular-nums text-slate-800 dark:text-slate-100">
          {debitAmount(t, debit)}
        </span>
      </div>
      {(meta || pill || debit.unverified) && (
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-600 dark:text-slate-300">
          {meta && <span>{meta}</span>}
          {pill && (
            <span
              className={`rounded-full px-1.5 py-0.5 font-semibold ${PILL_CLASS[tone]}`}
            >
              {pill}
            </span>
          )}
          {debit.unverified && (
            <span className="text-amber-700 dark:text-amber-400">
              {t('fileCheck.obligations.unverified')}
            </span>
          )}
        </div>
      )}
      {children}
    </li>
  );
}

function FoirTiles({ foir }: { foir: FileCheckFoir }) {
  const { t } = useTranslation();
  const ratio = num(foir.existing_emi_ratio_pct);
  const limit = num(foir.foir_limit_pct);
  const income = num(foir.net_monthly_income);
  const emis = num(foir.existing_emis);
  const maxNew = num(foir.max_new_emi);
  const tiles: { key: string; label: string; value: string; sub?: string }[] =
    [];
  if (income !== null) {
    tiles.push({
      key: 'income',
      label: t('fileCheck.foir.netIncome'),
      value: formatInr(income),
    });
  }
  if (emis !== null) {
    tiles.push({
      key: 'emis',
      label: t('fileCheck.foir.existingEmis'),
      value: formatInr(emis),
    });
  }
  if (ratio !== null) {
    tiles.push({
      key: 'ratio',
      label: t('fileCheck.foir.ratio'),
      value: `${ratio}%`,
      sub:
        limit !== null
          ? t('fileCheck.obligations.vsLimit', { limit })
          : undefined,
    });
  }
  if (maxNew !== null) {
    tiles.push({
      key: 'maxNew',
      label:
        limit !== null
          ? t('fileCheck.foir.maxNewEmiAt', { limit })
          : t('fileCheck.foir.maxNewEmi'),
      value: formatInr(maxNew),
    });
  }
  // Not computed (missing income or EMIs): the detail below says why.
  if (tiles.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-1.5" data-testid="foir-tiles">
      {tiles.map((tile) => (
        <div key={tile.key} className={TILE_CLASS}>
          <dt className="text-[10px] text-slate-500 dark:text-slate-400">
            {tile.label}
          </dt>
          <dd className="text-sm font-semibold tabular-nums text-slate-800 dark:text-slate-100">
            {tile.value}
            {tile.sub && (
              <span className="ml-1 text-[10px] font-medium text-slate-500 dark:text-slate-400">
                {tile.sub}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function FoirPolicy({ foir }: { foir: FileCheckFoir }) {
  const { t } = useTranslation();
  const limit = num(foir.foir_limit_pct);
  const source = text(foir.limit_source);
  const url = safeHttpUrl(foir.limit_url);
  const note = text(foir.limit_note);
  const alternatives = (foir.limit_alternatives ?? []).filter(
    (a) => a && (num(a.value) !== null || text(a.source)),
  );
  if (!source && limit === null && !note && alternatives.length === 0) {
    return null;
  }
  return (
    <div className="space-y-0.5 text-[10px] leading-snug text-slate-500 dark:text-slate-400">
      {(source || limit !== null) && (
        <p data-testid="foir-limit-source">
          {source
            ? limit !== null
              ? t('fileCheck.obligations.limitSource', { limit, source })
              : t('fileCheck.obligations.limitSourceOnly', { source })
            : t('fileCheck.obligations.limitOnly', { limit })}
          {foir.hard_limit === true && (
            <span className="ml-1 font-semibold text-red-600 dark:text-red-400">
              {t('fileCheck.obligations.hardLimit')}
            </span>
          )}
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-1 inline-flex items-center gap-0.5 font-medium text-emerald-700 hover:underline dark:text-emerald-400"
            >
              {t('fileCheck.obligations.sourceLink')}
              <ExternalLink className="h-2.5 w-2.5" aria-hidden="true" />
            </a>
          )}
        </p>
      )}
      {alternatives.map((a, i) => {
        const value = num(a.value);
        return (
          <p key={i}>
            {t('fileCheck.obligations.alternative', {
              value: value !== null ? Math.round(value * 1000) / 10 : '–',
              source: text(a.source) || '–',
            })}
          </p>
        );
      })}
      {note && <p>{note}</p>}
    </div>
  );
}

interface ObligationsSectionProps {
  obligations?: FileCheckObligations | null;
  foir?: FileCheckFoir | null;
}

/** "Obligations & FOIR (indicative)" for one applicant. */
export default function ObligationsSection({
  obligations,
  foir,
}: ObligationsSectionProps) {
  const { t } = useTranslation();
  const o = obligations && typeof obligations === 'object' ? obligations : null;
  const f = foir && typeof foir === 'object' ? foir : null;
  if (!o && !f) return null;

  const emiRows = o ? loanEmiRows(o) : [];
  const unmatched = o ? unmatchedDeclaredEmis(o) : [];
  const possible = o ? possibleEmiPayees(o) : new Set<string>();
  const otherFixed = o?.other_fixed_debits ?? [];
  const variable = [
    ...(o?.variable_debits ?? []),
    ...(o?.one_off_debits ?? []),
  ];
  const oneOff = new Set(o?.one_off_debits ?? []);
  const totals = o?.totals && typeof o.totals === 'object' ? o.totals : null;
  const totalTiles = totals
    ? (
        [
          ['fileCheck.obligations.totalLoanEmis', totals.loan_emis],
          ['fileCheck.obligations.totalOtherFixed', totals.other_fixed],
          ['fileCheck.obligations.totalFixed', totals.fixed_monthly],
          [
            'fileCheck.obligations.totalVariable',
            totals.variable_monthly_average,
          ],
        ] as const
      ).filter(([, v]) => num(v) !== null)
    : [];
  const reasons = o?.unavailable_reasons ?? [];
  // The FOIR note below already says which debits count.
  const notes = (o?.notes ?? []).filter(
    (n) => !(f && /^only loan emis count/i.test(n)),
  );
  const label = text(f?.label) || t('fileCheck.obligations.indicativeLabel');
  const foirStatus = normalizeStatus(f?.status);
  const foirTone = f ? consistencyTone(f.status) : 'muted';
  const foirComputed = num(f?.existing_emi_ratio_pct) !== null;
  const showFoirDetail =
    !!f && !!text(f.detail) && (!foirComputed || foirStatus !== 'OK');
  const bankAvailable = o?.available !== false;
  const incomeSource = text(f?.income_source);

  return (
    <section
      aria-label={t('fileCheck.obligations.title')}
      data-focus="obligations"
      className="space-y-2 scroll-mt-3"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Landmark
          className="h-3.5 w-3.5 text-slate-500 dark:text-slate-400"
          aria-hidden="true"
        />
        <h5 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {t('fileCheck.obligations.title')}
        </h5>
        <PainPointTag id="bank-statement-structuring" />
      </div>
      <p className="inline-flex rounded-full border border-slate-300/80 bg-white/40 px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:border-white/15 dark:bg-white/5 dark:text-slate-300">
        {label}
      </p>

      {f && <FoirTiles foir={f} />}
      {showFoirDetail && (
        <p
          className={`break-words rounded-md border px-2.5 py-1.5 text-[11px] leading-snug ${ROW_CLASS[foirTone]} text-slate-700 dark:text-slate-200`}
          data-tone={foirTone}
        >
          {text(f?.detail)}
        </p>
      )}
      {f && (
        <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
          {t('fileCheck.foir.note')}
          {incomeSource
            ? ` ${t('fileCheck.foir.incomeSource', { source: incomeSource })}`
            : ''}
          {text(f.existing_emis_basis)
            ? ` ${t('fileCheck.obligations.emiBasis', { basis: text(f.existing_emis_basis) })}`
            : ''}
        </p>
      )}
      {f && <FoirPolicy foir={f} />}

      {reasons.length > 0 && (
        <ul className="space-y-1">
          {reasons.map((r, i) => (
            <li
              key={i}
              data-tone="review"
              className={`break-words rounded-md border px-2.5 py-1.5 text-[11px] leading-snug text-amber-900 dark:text-amber-200 ${ROW_CLASS.review}`}
            >
              {r}
            </li>
          ))}
        </ul>
      )}

      {o && (bankAvailable || emiRows.length > 0 || unmatched.length > 0) && (
        <div className="space-y-1" data-testid="loan-emis">
          <SubHeading>
            {t('fileCheck.obligations.fixedLoanEmis', {
              count: emiRows.length + unmatched.length,
            })}
          </SubHeading>
          {emiRows.length === 0 && unmatched.length === 0 ? (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              {t('fileCheck.obligations.noLoanEmis')}
            </p>
          ) : (
            <ul className="space-y-1">
              {emiRows.map((row, i) => {
                const tone = emiStatusTone(row.status);
                return (
                  <DebitRow
                    key={`emi-${row.debit.payee ?? ''}-${i}`}
                    tone={tone}
                    debit={row.debit}
                    pill={t(EMI_STATUS_KEYS[row.status])}
                  >
                    {row.declared && (
                      <p className="mt-0.5 break-words text-[10px] text-slate-500 dark:text-slate-400">
                        {t('fileCheck.obligations.declaredAs', {
                          label: declaredLabel(row.declared),
                        })}
                        {row.declared.document_name
                          ? ` [${row.declared.document_name}]`
                          : ''}
                      </p>
                    )}
                  </DebitRow>
                );
              })}
              {unmatched.map((d, i) => {
                const notFound = d.status === 'not_found';
                const tone: StatusTone = notFound ? 'review' : 'muted';
                return (
                  <li
                    key={`declared-${i}`}
                    data-tone={tone}
                    className={`rounded-lg border px-2.5 py-1.5 ${ROW_CLASS[tone]}`}
                  >
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="min-w-0 break-words text-xs font-semibold text-slate-800 dark:text-slate-100">
                        {text(d.lender) ||
                          t('fileCheck.obligations.lenderNotNamed')}
                      </span>
                      <span className="ml-auto text-xs font-semibold tabular-nums text-slate-800 dark:text-slate-100">
                        {formatInr(num(d.amount))}
                      </span>
                    </div>
                    <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-600 dark:text-slate-300">
                      <span
                        className={`rounded-full px-1.5 py-0.5 font-semibold ${PILL_CLASS[tone]}`}
                      >
                        {notFound
                          ? t('fileCheck.obligations.declaredNotFound')
                          : t('fileCheck.obligations.declaredNotChecked')}
                      </span>
                      {d.document_name && <span>[{d.document_name}]</span>}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {otherFixed.length > 0 && (
        <div className="space-y-1" data-testid="other-fixed">
          <SubHeading>
            {t('fileCheck.obligations.otherFixed', {
              count: otherFixed.length,
            })}
          </SubHeading>
          <ul className="space-y-1">
            {otherFixed.map((debit, i) => {
              const isPossible = !!debit.payee && possible.has(debit.payee);
              return (
                <DebitRow
                  key={`fixed-${debit.payee ?? ''}-${i}`}
                  tone={isPossible ? 'review' : 'ok'}
                  debit={debit}
                  pill={
                    isPossible
                      ? t('fileCheck.obligations.possibleEmi')
                      : undefined
                  }
                />
              );
            })}
          </ul>
        </div>
      )}

      {totalTiles.length > 0 && (
        <div className="space-y-1">
          <SubHeading>{t('fileCheck.obligations.totals')}</SubHeading>
          <dl
            className="grid grid-cols-2 gap-1.5"
            data-testid="obligation-totals"
          >
            {totalTiles.map(([key, value]) => (
              <div key={key} className={TILE_CLASS}>
                <dt className="text-[10px] text-slate-500 dark:text-slate-400">
                  {t(key)}
                </dt>
                <dd className="text-sm font-semibold tabular-nums text-slate-800 dark:text-slate-100">
                  {formatInr(num(value))}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {variable.length > 0 && (
        <details className="rounded-lg border border-white/50 bg-white/20 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.02]">
          <summary className="cursor-pointer select-none text-[11px] font-semibold text-slate-600 dark:text-slate-300">
            {t('fileCheck.obligations.variableOneOff', {
              count: variable.length,
            })}
          </summary>
          <ul className="mt-1.5 space-y-1">
            {variable.map((debit, i) => (
              <DebitRow
                key={`var-${debit.payee ?? ''}-${i}`}
                tone="muted"
                debit={debit}
                pill={
                  oneOff.has(debit)
                    ? t('fileCheck.obligations.oneOff')
                    : undefined
                }
              />
            ))}
          </ul>
        </details>
      )}

      {notes.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[10px] leading-snug text-slate-500 dark:text-slate-400">
          {notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
