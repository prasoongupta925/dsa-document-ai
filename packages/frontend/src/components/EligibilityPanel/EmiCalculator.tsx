import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  ArrowRightLeft,
  Calculator,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import type { EligibilityResult } from '../../types/eligibility';
import { formatRupees, lenderTone, roiPercent } from '../../lib/eligibility';
import {
  BUTTON_CLASS,
  CONTROL_CLASS,
  Field,
  IndicativeTag,
  NumberInput,
  SECTION_CLASS,
  Segmented,
} from './fields';

// The Lenders tab's EMI and balance-transfer calculator: the reducing-balance
// EMI (Excel PMT) of any amount, rate and tenure, worked out and rounded as the
// chat's emi_calculator tool does (packages/lambda/file-check-mcp/
// loan_tools.py). Lender eligibility stays with the backend.

/** The chat tool's limits: ₹100 crore, 60% a year, 480 months. */
export const MAX_LOAN_AMOUNT = 1_000_000_000;
export const MAX_RATE_PCT = 60;
export const MAX_TENURE_MONTHS = 480;

/** To the rupee, halves away from zero (ROUND_HALF_UP, as the chat tool); never -0. */
export function roundRupees(value: number): number {
  const n = Math.sign(value) * Math.round(Math.abs(value));
  return n === 0 ? 0 : n;
}

/** Monthly EMI, unrounded: P × r × (1 + r)^n ÷ ((1 + r)^n − 1), r = rate ÷ 12 ÷ 100; P ÷ n at 0%. */
export function emiOf(
  principal: number,
  annualRatePct: number,
  months: number,
): number {
  const r = annualRatePct / 1200;
  if (r === 0) return principal / months;
  const growth = Math.pow(1 + r, months);
  return (principal * r * growth) / (growth - 1);
}

/** One loan year (months 1-12 are year 1), in rupees. */
export interface ScheduleYear {
  year: number;
  /** 12, or fewer in a last part year. */
  months: number;
  opening: number;
  principalPaid: number;
  interestPaid: number;
  closing: number;
}

/** Per loan year: opening balance, principal and interest paid, closing balance. */
export function yearlySchedule(
  principal: number,
  annualRatePct: number,
  months: number,
  exactEmi = emiOf(principal, annualRatePct, months),
): ScheduleYear[] {
  const r = annualRatePct / 1200;
  const rows: ScheduleYear[] = [];
  let balance = principal;
  for (let start = 0; start < months; start += 12) {
    const opening = balance;
    const count = Math.min(12, months - start);
    let principalPaid = 0;
    let interestPaid = 0;
    for (let i = 0; i < count; i++) {
      const interest = balance * r;
      interestPaid += interest;
      principalPaid += exactEmi - interest;
      balance -= exactEmi - interest;
    }
    // The last EMI clears the loan (a rounding trace is left).
    if (start + count === months) balance = 0;
    rows.push({
      year: start / 12 + 1,
      months: count,
      opening: roundRupees(opening),
      principalPaid: roundRupees(principalPaid),
      interestPaid: roundRupees(interestPaid),
      closing: roundRupees(balance),
    });
  }
  return rows;
}

export interface EmiPlan {
  emi: number;
  totalInterest: number;
  totalPayable: number;
  /** Shares of the total payable, adding up to 100. */
  principalPct: number;
  interestPct: number;
  schedule: ScheduleYear[];
}

/** EMI, totals (from the unrounded EMI), split and yearly schedule, in rupees. */
export function emiPlan(
  principal: number,
  annualRatePct: number,
  months: number,
): EmiPlan {
  const exact = emiOf(principal, annualRatePct, months);
  const total = exact * months;
  const principalPct = roundRupees((principal / total) * 100);
  return {
    emi: roundRupees(exact),
    totalInterest: roundRupees(total - principal),
    totalPayable: roundRupees(total),
    principalPct,
    interestPct: 100 - principalPct,
    schedule: yearlySchedule(principal, annualRatePct, months, exact),
  };
}

export interface BalanceTransfer {
  currentEmi: number;
  newEmi: number;
  /** Negative when the new rate is higher. */
  monthlySaving: number;
  totalSaving: number;
  currentTotalInterest: number;
  newTotalInterest: number;
}

/** The outstanding at the current rate vs the new rate over the remaining months. */
export function balanceTransfer(
  outstanding: number,
  currentRatePct: number,
  months: number,
  newRatePct: number,
): BalanceTransfer {
  const current = emiOf(outstanding, currentRatePct, months);
  const next = emiOf(outstanding, newRatePct, months);
  const monthly = current - next;
  return {
    currentEmi: roundRupees(current),
    newEmi: roundRupees(next),
    monthlySaving: roundRupees(monthly),
    totalSaving: roundRupees(monthly * months),
    currentTotalInterest: roundRupees(current * months - outstanding),
    newTotalInterest: roundRupees(next * months - outstanding),
  };
}

// ------------------------------------------------------------------ inputs

export type TenureUnit = 'years' | 'months';

/** A typed rate: '11', '10.49', '8.875 %' -> the number; blank -> null; else NaN. */
export function parseRate(text: string): number | null {
  const s = text.replace(/[%\s]/g, '');
  if (!s) return null;
  return /^\d+(\.\d{1,4})?$/.test(s) ? Number(s) : Number.NaN;
}

/**
 * Months of a typed tenure: '5' years -> 60, '2.5' years -> 30, '18' months
 * -> 18; blank -> null; not a whole number of months, or not 1 to 480 -> NaN.
 */
export function tenureMonths(text: string, unit: TenureUnit): number | null {
  const s = text.replace(/\s/g, '');
  if (!s) return null;
  const pattern = unit === 'years' ? /^\d+(\.\d{1,2})?$/ : /^\d+$/;
  if (!pattern.test(s)) return Number.NaN;
  const raw = unit === 'years' ? Number(s) * 12 : Number(s);
  const months = Math.round(raw);
  if (Math.abs(raw - months) > 1e-9) return Number.NaN;
  return months >= 1 && months <= MAX_TENURE_MONTHS ? months : Number.NaN;
}

function validAmount(value: number | null): number | null {
  return value !== null && value > 0 && value <= MAX_LOAN_AMOUNT ? value : null;
}

function validRate(value: number | null): number | null {
  return value !== null && value >= 0 && value <= MAX_RATE_PCT ? value : null;
}

/**
 * A typed tenure in the other unit: '60' months -> '5' years, '2.5' years ->
 * '30' months; kept as typed when it is not a valid tenure or not a whole
 * number of hundredths of a year ('13' months).
 */
export function convertTenure(
  text: string,
  from: TenureUnit,
  to: TenureUnit,
): string {
  const months = tenureMonths(text, from);
  if (from === to || months === null || Number.isNaN(months)) return text;
  if (to === 'months') return String(months);
  const hundredths = (months * 100) / 12;
  return Number.isInteger(hundredths) ? String(hundredths / 100) : text;
}

/** The best lender's offer, to start the EMI calculator from. */
export interface EmiStart {
  lender: string;
  amount: number;
  ratePct: number;
  months: number;
}

/** The best eligible lender's amount, ROI and tenure (the EMI column's), if any. */
export function emiStartOf(result: EligibilityResult | null): EmiStart | null {
  const row = result?.per_lender.find((r) => r.lender === result.best_lender);
  const ratePct = roiPercent(row?.roi);
  if (
    !row ||
    lenderTone(row.status) !== 'eligible' ||
    !row.eligible_amount ||
    ratePct === null ||
    !row.tenure_months
  ) {
    return null;
  }
  return {
    lender: row.lender,
    amount: row.eligible_amount,
    ratePct,
    months: row.tenure_months,
  };
}

// ------------------------------------------------------------------ view

export type CalculatorMode = 'emi' | 'bt';

export interface EmiForm {
  amount: number | null;
  rate: string;
  tenure: string;
  unit: TenureUnit;
}

export interface BtForm {
  outstanding: number | null;
  currentRate: string;
  newRate: string;
  tenure: string;
  unit: TenureUnit;
}

const EMPTY_EMI: EmiForm = {
  amount: null,
  rate: '',
  tenure: '',
  unit: 'years',
};
const EMPTY_BT: BtForm = {
  outstanding: null,
  currentRate: '',
  newRate: '',
  tenure: '',
  unit: 'years',
};

// Principal and interest: two categorical hues, checked for colour-blind
// separation and contrast on the light and dark surfaces.
const PRINCIPAL_SWATCH = 'bg-indigo-600 dark:bg-indigo-500';
const INTEREST_SWATCH = 'bg-amber-600';

function Stat({
  label,
  value,
  emphasis = false,
  testId,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
  testId?: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/50 bg-white/40 px-2 py-1 dark:border-white/[0.08] dark:bg-white/[0.03]">
      <dt className="text-[10px] text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd
        className={`break-words font-semibold tabular-nums text-slate-800 dark:text-slate-100 ${
          emphasis ? 'text-base' : 'text-xs'
        }`}
        data-testid={testId}
      >
        {value}
      </dd>
    </div>
  );
}

function RateInput({
  id,
  value,
  onChange,
  describedBy,
  invalid,
  testId,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  describedBy?: string;
  invalid: boolean;
  testId?: string;
}) {
  return (
    <div className="relative">
      <input
        id={id}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        data-testid={testId}
        className={`${CONTROL_CLASS} pr-6 tabular-nums`}
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-slate-400"
      >
        %
      </span>
    </div>
  );
}

function TenureInput({
  name,
  label,
  tenure,
  unit,
  onChange,
  error,
  testId,
}: {
  name: string;
  label: string;
  tenure: string;
  unit: TenureUnit;
  onChange: (tenure: string, unit: TenureUnit) => void;
  error: string | null;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <Field label={label} error={error}>
      {({ id, describedBy }) => (
        <div className="flex items-center gap-1.5">
          <input
            id={id}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            value={tenure}
            onChange={(e) => onChange(e.target.value, unit)}
            aria-describedby={describedBy}
            aria-invalid={error ? true : undefined}
            data-testid={testId}
            className={`${CONTROL_CLASS} min-w-0 flex-1 tabular-nums`}
          />
          <div className="flex-shrink-0">
            <Segmented<TenureUnit>
              name={name}
              legend={t('eligibility.emi.tenureUnit')}
              options={[
                { value: 'years', label: t('eligibility.emi.years') },
                { value: 'months', label: t('eligibility.emi.monthsUnit') },
              ]}
              value={unit}
              // The same tenure in the new unit (60 months -> 5 years).
              onChange={(next) =>
                onChange(convertTenure(tenure, unit, next), next)
              }
            />
          </div>
        </div>
      )}
    </Field>
  );
}

/** The principal / interest split of the total payable as one stacked bar. */
function SplitBar({ plan, principal }: { plan: EmiPlan; principal: number }) {
  const { t } = useTranslation();
  const parts = [
    {
      key: 'principal',
      label: t('eligibility.emi.principal'),
      pct: plan.principalPct,
      value: principal,
      swatch: PRINCIPAL_SWATCH,
    },
    {
      key: 'interest',
      label: t('eligibility.emi.interest'),
      pct: plan.interestPct,
      value: plan.totalInterest,
      swatch: INTEREST_SWATCH,
    },
  ];
  return (
    <figure className="space-y-1" data-testid="emi-split">
      <figcaption className="text-[10px] text-slate-500 dark:text-slate-400">
        {t('eligibility.emi.splitLabel')}
      </figcaption>
      <div className="flex h-2.5 gap-0.5" aria-hidden="true">
        {parts
          .filter((part) => part.pct > 0)
          .map((part) => (
            <div
              key={part.key}
              className={`h-full rounded ${part.swatch}`}
              style={{ width: `${part.pct}%` }}
              title={`${part.label}: ${formatRupees(part.value)} (${part.pct}%)`}
            />
          ))}
      </div>
      <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-700 dark:text-slate-200">
        {parts.map((part) => (
          <li key={part.key} className="flex items-center gap-1 tabular-nums">
            <span
              aria-hidden="true"
              className={`inline-block h-2 w-2 rounded-sm ${part.swatch}`}
            />
            <span className="font-semibold">{part.label}</span>
            {part.pct}% · {formatRupees(part.value)}
          </li>
        ))}
      </ul>
    </figure>
  );
}

function Schedule({ rows }: { rows: ScheduleYear[] }) {
  const { t } = useTranslation();
  const th =
    'px-1.5 py-1 text-right align-bottom text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400';
  const td = 'whitespace-nowrap px-1.5 py-1 text-right tabular-nums';
  return (
    <details className="rounded-lg border border-white/50 bg-white/20 px-2.5 py-1.5 dark:border-white/[0.08] dark:bg-white/[0.02]">
      <summary className="cursor-pointer select-none text-[11px] font-semibold text-slate-600 dark:text-slate-300">
        {t('eligibility.emi.schedule', { count: rows.length })}
      </summary>
      <div className="mt-1.5 max-h-64 overflow-auto">
        <table
          className="w-full text-[11px] text-slate-700 dark:text-slate-200"
          data-testid="emi-schedule"
        >
          <thead>
            <tr>
              <th scope="col" className={`${th} text-left`}>
                {t('eligibility.emi.year')}
              </th>
              <th scope="col" className={th}>
                {t('eligibility.emi.opening')}
              </th>
              <th scope="col" className={th}>
                {t('eligibility.emi.principalPaid')}
              </th>
              <th scope="col" className={th}>
                {t('eligibility.emi.interestPaid')}
              </th>
              <th scope="col" className={th}>
                {t('eligibility.emi.closing')}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.year}
                className="border-t border-black/[0.06] dark:border-white/[0.06]"
              >
                <th
                  scope="row"
                  className="whitespace-nowrap px-1.5 py-1 text-left font-medium"
                >
                  {row.year}
                  {row.months < 12 &&
                    ` (${t('eligibility.months', { count: row.months })})`}
                </th>
                <td className={td}>{formatRupees(row.opening)}</td>
                <td className={td}>{formatRupees(row.principalPaid)}</td>
                <td className={td}>{formatRupees(row.interestPaid)}</td>
                <td className={td}>{formatRupees(row.closing)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function amountError(t: TFunction, value: number | null): string | null {
  if (value === null || Number.isNaN(value)) return null; // blank, or the field says it
  return validAmount(value) === null ? t('eligibility.emi.amountRange') : null;
}

function rateError(t: TFunction, text: string): string | null {
  const rate = parseRate(text);
  if (rate === null) return null;
  return validRate(rate) === null ? t('eligibility.emi.rateRange') : null;
}

function tenureError(t: TFunction, text: string, unit: TenureUnit) {
  return Number.isNaN(tenureMonths(text, unit))
    ? t('eligibility.emi.tenureRange')
    : null;
}

function EmiView({
  form,
  onChange,
  start,
}: {
  form: EmiForm;
  onChange: (form: EmiForm) => void;
  start: EmiStart | null;
}) {
  const { t } = useTranslation();
  const baseId = useId();
  const amount = validAmount(form.amount);
  const rate = validRate(parseRate(form.rate));
  const months = tenureMonths(form.tenure, form.unit);
  const plan =
    amount !== null && rate !== null && months !== null && !Number.isNaN(months)
      ? emiPlan(amount, rate, months)
      : null;
  return (
    <div className="space-y-2.5">
      {start && (
        <button
          type="button"
          onClick={() =>
            onChange({
              amount: start.amount,
              rate: String(start.ratePct),
              tenure: String(start.months),
              unit: 'months',
            })
          }
          className={BUTTON_CLASS}
          title={t('eligibility.emi.useOfferHint', { lender: start.lender })}
          data-testid="emi-use-offer"
        >
          <Calculator className="h-3 w-3" aria-hidden="true" />
          {t('eligibility.emi.useOffer', { lender: start.lender })}
        </button>
      )}
      <div className="grid gap-2 @md:grid-cols-2 @3xl:grid-cols-3">
        <Field
          label={t('eligibility.emi.amount')}
          error={amountError(t, form.amount)}
        >
          {({ id, describedBy }) => (
            <NumberInput
              id={id}
              value={form.amount}
              onChange={(value) => onChange({ ...form, amount: value })}
              describedBy={describedBy}
              invalid={amountError(t, form.amount) !== null}
              testId="emi-amount"
            />
          )}
        </Field>
        <Field
          label={t('eligibility.emi.rate')}
          error={rateError(t, form.rate)}
        >
          {({ id, describedBy }) => (
            <RateInput
              id={id}
              value={form.rate}
              onChange={(rate) => onChange({ ...form, rate })}
              describedBy={describedBy}
              invalid={rateError(t, form.rate) !== null}
              testId="emi-rate"
            />
          )}
        </Field>
        <TenureInput
          name={`${baseId}-unit`}
          label={t('eligibility.emi.tenure')}
          tenure={form.tenure}
          unit={form.unit}
          onChange={(tenure, unit) => onChange({ ...form, tenure, unit })}
          error={tenureError(t, form.tenure, form.unit)}
          testId="emi-tenure"
        />
      </div>
      {plan && amount !== null ? (
        <div className="space-y-2" data-testid="emi-result">
          <dl className="grid grid-cols-3 gap-1.5">
            <Stat
              label={t('eligibility.emi.monthlyEmi')}
              value={formatRupees(plan.emi)}
              emphasis
              testId="emi-value"
            />
            <Stat
              label={t('eligibility.emi.totalInterest')}
              value={formatRupees(plan.totalInterest)}
              testId="emi-total-interest"
            />
            <Stat
              label={t('eligibility.emi.totalPayable')}
              value={formatRupees(plan.totalPayable)}
              testId="emi-total-payable"
            />
          </dl>
          <SplitBar plan={plan} principal={amount} />
          <Schedule rows={plan.schedule} />
        </div>
      ) : (
        <p className="text-[11px] text-slate-500 dark:text-slate-400">
          {t('eligibility.emi.empty')}
        </p>
      )}
    </div>
  );
}

function BtView({
  form,
  onChange,
}: {
  form: BtForm;
  onChange: (form: BtForm) => void;
}) {
  const { t } = useTranslation();
  const baseId = useId();
  const outstanding = validAmount(form.outstanding);
  const current = validRate(parseRate(form.currentRate));
  const next = validRate(parseRate(form.newRate));
  const months = tenureMonths(form.tenure, form.unit);
  const bt =
    outstanding !== null &&
    current !== null &&
    next !== null &&
    months !== null &&
    !Number.isNaN(months)
      ? balanceTransfer(outstanding, current, months, next)
      : null;
  const saves = bt !== null && bt.totalSaving >= 0;
  return (
    <div className="space-y-2.5">
      <div className="grid gap-2 @md:grid-cols-2">
        <Field
          label={t('eligibility.emi.outstanding')}
          error={amountError(t, form.outstanding)}
        >
          {({ id, describedBy }) => (
            <NumberInput
              id={id}
              value={form.outstanding}
              onChange={(value) => onChange({ ...form, outstanding: value })}
              describedBy={describedBy}
              invalid={amountError(t, form.outstanding) !== null}
              testId="bt-outstanding"
            />
          )}
        </Field>
        <TenureInput
          name={`${baseId}-unit`}
          label={t('eligibility.emi.remaining')}
          tenure={form.tenure}
          unit={form.unit}
          onChange={(tenure, unit) => onChange({ ...form, tenure, unit })}
          error={tenureError(t, form.tenure, form.unit)}
          testId="bt-tenure"
        />
        <Field
          label={t('eligibility.emi.currentRate')}
          error={rateError(t, form.currentRate)}
        >
          {({ id, describedBy }) => (
            <RateInput
              id={id}
              value={form.currentRate}
              onChange={(currentRate) => onChange({ ...form, currentRate })}
              describedBy={describedBy}
              invalid={rateError(t, form.currentRate) !== null}
              testId="bt-current-rate"
            />
          )}
        </Field>
        <Field
          label={t('eligibility.emi.newRate')}
          error={rateError(t, form.newRate)}
        >
          {({ id, describedBy }) => (
            <RateInput
              id={id}
              value={form.newRate}
              onChange={(newRate) => onChange({ ...form, newRate })}
              describedBy={describedBy}
              invalid={rateError(t, form.newRate) !== null}
              testId="bt-new-rate"
            />
          )}
        </Field>
      </div>
      {bt ? (
        <div className="space-y-1.5" data-testid="bt-result">
          <dl className="grid grid-cols-2 gap-1.5 @md:grid-cols-4">
            <Stat
              label={t('eligibility.emi.currentEmi')}
              value={formatRupees(bt.currentEmi)}
              testId="bt-current-emi"
            />
            <Stat
              label={t('eligibility.emi.newEmi')}
              value={formatRupees(bt.newEmi)}
              testId="bt-new-emi"
            />
            <Stat
              label={t(
                saves
                  ? 'eligibility.emi.monthlySaving'
                  : 'eligibility.emi.monthlyExtra',
              )}
              value={formatRupees(Math.abs(bt.monthlySaving))}
              emphasis
              testId="bt-monthly"
            />
            <Stat
              label={t(
                saves
                  ? 'eligibility.emi.totalSaving'
                  : 'eligibility.emi.totalExtra',
              )}
              value={formatRupees(Math.abs(bt.totalSaving))}
              emphasis
              testId="bt-total"
            />
          </dl>
          <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
            {t('eligibility.emi.btNote')}
          </p>
        </div>
      ) : (
        <p className="text-[11px] text-slate-500 dark:text-slate-400">
          {t('eligibility.emi.btEmpty')}
        </p>
      )}
    </div>
  );
}

interface EmiCalculatorProps {
  /** The best eligible lender: one click fills the EMI form with its offer. */
  start?: EmiStart | null;
  /** Open at first (tests render it statically). */
  initialOpen?: boolean;
  initialMode?: CalculatorMode;
  initialEmi?: Partial<EmiForm>;
  initialBt?: Partial<BtForm>;
}

/** EMI calculator (EMI, split, yearly schedule) and balance-transfer view. */
export default function EmiCalculator({
  start = null,
  initialOpen = false,
  initialMode = 'emi',
  initialEmi,
  initialBt,
}: EmiCalculatorProps) {
  const { t } = useTranslation();
  const baseId = useId();
  const [open, setOpen] = useState(initialOpen);
  const [mode, setMode] = useState<CalculatorMode>(initialMode);
  const [emiForm, setEmiForm] = useState<EmiForm>({
    ...EMPTY_EMI,
    ...initialEmi,
  });
  const [btForm, setBtForm] = useState<BtForm>({ ...EMPTY_BT, ...initialBt });
  const bodyId = `${baseId}-body`;

  return (
    <section className={SECTION_CLASS} data-testid="emi-calculator">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={open ? bodyId : undefined}
        className="flex w-full items-center gap-1.5 rounded text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-slate-400 dark:hover:text-slate-200"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        <ArrowRightLeft className="h-3.5 w-3.5" aria-hidden="true" />
        {t('eligibility.emi.title')}
      </button>
      {open && (
        <div id={bodyId} className="space-y-2.5">
          <p className="text-[11px] leading-snug text-slate-600 dark:text-slate-300">
            {t('eligibility.emi.intro')}
          </p>
          <Segmented<CalculatorMode>
            name={`${baseId}-mode`}
            legend={t('eligibility.emi.modes.label')}
            options={[
              { value: 'emi', label: t('eligibility.emi.modes.emi') },
              { value: 'bt', label: t('eligibility.emi.modes.bt') },
            ]}
            value={mode}
            onChange={setMode}
            testId="emi-mode"
          />
          {mode === 'emi' ? (
            <EmiView form={emiForm} onChange={setEmiForm} start={start} />
          ) : (
            <BtView form={btForm} onChange={setBtForm} />
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <IndicativeTag />
            <p className="min-w-0 flex-1 text-[9px] leading-snug text-slate-400">
              {t('eligibility.emi.formula')}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
