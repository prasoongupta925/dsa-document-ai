import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { ChevronDown, ChevronRight, Info, Plus, Trash2 } from 'lucide-react';
import type {
  CibilEnquiries,
  EligibilityCibil,
  EligibilityInputs,
  StillNeeded,
  Tradeline,
  TradelineAction,
} from '../../types/eligibility';
import {
  LOAN_TYPES,
  MAX_TRADELINES,
  TRADELINE_ACTIONS,
  TRADELINE_STATUSES,
  accountNumberLooksValid,
  addTradeline,
  addTradelineRow,
  cibilFieldSources,
  cibilInputs,
  documentRowsMissing,
  enquiriesOutOfOrder,
  fillFromDocuments,
  fillableFields,
  formatRupees,
  isFutureDate,
  localToday,
  otherSourceFor,
  removeTradeline,
  rowFromDocument,
  scoreLooksValid,
  setCibil,
  setEnquiries,
  setFieldValue,
  setTradelineAction,
  sourceFor,
  stillNeeded,
  updateTradeline,
} from '../../lib/eligibility';
import {
  BUTTON_CLASS,
  CONTROL_CLASS,
  DocumentRowOffers,
  DocumentValueOffer,
  Field,
  FieldSourceBadge,
  NumberInput,
  SECTION_CLASS,
  SectionTitle,
  Segmented,
  SourceLegend,
  goToField,
  SourceNote,
  StillNeededList,
} from './fields';

type Edit = (update: (inputs: EligibilityInputs) => EligibilityInputs) => void;

const ENQUIRY_WINDOWS = ['d30', 'd60', 'd90', 'd120'] as const;

const ACTION_ROW_CLASS: Record<TradelineAction, string> = {
  bt: 'border-sky-200/80 bg-sky-50/40 dark:border-sky-800/40 dark:bg-sky-900/10',
  obligate:
    'border-white/50 bg-white/30 dark:border-white/[0.08] dark:bg-white/[0.03]',
  close:
    'border-slate-200/80 bg-slate-50/50 dark:border-white/[0.06] dark:bg-white/[0.01]',
};

interface TradelineActionToggleProps {
  index: number;
  value: TradelineAction;
  /** Radio group name, unique in the page. */
  name: string;
  legend: string;
  labels: Record<TradelineAction, string>;
  describedBy?: string;
  disabled?: boolean;
  onEdit: Edit;
}

/**
 * BT / Obligate / Close of one tradeline: a choice edits that row's action
 * only (setTradelineAction), which the next save or calculation sends. No
 * hooks, so tests can call it directly.
 */
export function TradelineActionToggle({
  index,
  value,
  name,
  legend,
  labels,
  describedBy,
  disabled,
  onEdit,
}: TradelineActionToggleProps) {
  return (
    <Segmented<TradelineAction>
      name={name}
      legend={legend}
      options={TRADELINE_ACTIONS.map((action) => ({
        value: action,
        label: labels[action],
      }))}
      value={value}
      onChange={(action) =>
        onEdit((inputs) => setTradelineAction(inputs, index, action))
      }
      describedBy={describedBy}
      disabled={disabled}
      testId="tradeline-action"
    />
  );
}

/** "Car loan · Mulshi Auto Finance · EMI ₹8,200" */
function tradelineSummary(t: TFunction, row: Tradeline): string {
  return [
    row.loan_type ? t(`eligibility.cibil.loanTypes.${row.loan_type}`) : '',
    row.lender ?? '',
    row.emi !== null && Number.isFinite(row.emi)
      ? t('eligibility.cibil.emiSummary', { amount: formatRupees(row.emi) })
      : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** "0 / 1 / 1 / 2 in 30 / 60 / 90 / 120 days" ('–' for a window not given). */
function enquiriesText(t: TFunction, value: unknown): string {
  const e = (value ?? {}) as Partial<CibilEnquiries>;
  const n = (w: keyof CibilEnquiries) =>
    typeof e[w] === 'number' ? String(e[w]) : '–';
  return t('eligibility.cibil.enquiriesShown', {
    d30: n('d30'),
    d60: n('d60'),
    d90: n('d90'),
    d120: n('d120'),
  });
}

interface TradelineRowProps {
  row: Tradeline;
  index: number;
  groupName: string;
  onEdit: Edit;
  disabled?: boolean;
  initiallyExpanded?: boolean;
}

function TradelineRow({
  row,
  index,
  groupName,
  onEdit,
  disabled,
  initiallyExpanded = false,
}: TradelineRowProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const hintId = useId();
  const detailsId = useId();
  const number = index + 1;
  const patch = (p: Partial<Tradeline>) =>
    onEdit((inputs) => updateTradeline(inputs, index, p));
  const today = localToday();
  const accountError =
    row.account_number && !accountNumberLooksValid(row.account_number)
      ? t('eligibility.cibil.accountNumberInvalid')
      : null;
  const openDateError = isFutureDate(row.open_date)
    ? t('eligibility.profile.dateInPast')
    : null;
  const lastPaymentError = isFutureDate(row.last_payment_date)
    ? t('eligibility.profile.dateInPast')
    : row.open_date &&
        row.last_payment_date &&
        row.last_payment_date < row.open_date
      ? t('eligibility.cibil.lastPaymentBeforeOpen')
      : null;
  // What the backend needs for the chosen action (else it reports the row as incomplete).
  const actionNeeds =
    row.action === 'bt' && row.outstanding === null
      ? t('eligibility.cibil.needsOutstanding')
      : row.action === 'obligate' && row.emi === null && row.status !== 'closed'
        ? t('eligibility.cibil.needsEmi')
        : null;
  const summary = tradelineSummary(t, row);
  // The row's data is still the document's (the action is the user's choice).
  const fromDocument = rowFromDocument(row);

  return (
    <li
      className={`space-y-2 rounded-lg border p-2.5 ${ACTION_ROW_CLASS[row.action]}`}
      data-testid="tradeline"
      data-action={row.action}
      data-source={fromDocument ? 'document' : undefined}
      aria-label={t('eligibility.cibil.tradeline', { number })}
    >
      {/* Minimised row: what the report shows per loan, and the action */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          id={`${groupName}-loan-${number}`}
          tabIndex={-1}
          className="text-xs font-semibold text-slate-800 focus:outline-none dark:text-slate-100"
        >
          {t('eligibility.cibil.tradeline', { number })}
        </span>
        {summary && (
          <span className="min-w-0 truncate text-[11px] text-slate-500 dark:text-slate-400">
            {summary}
          </span>
        )}
        {fromDocument && <FieldSourceBadge source={row.document} />}
        {row.source === 'bank_statement' && (
          <span
            className="rounded-full border border-dashed border-blue-300 px-1.5 py-px text-[9px] font-semibold text-blue-700 dark:border-blue-700/60 dark:text-blue-300"
            title={t('eligibility.cibil.suggestedHint')}
            data-testid="suggested"
          >
            {t('eligibility.cibil.suggested')}
          </span>
        )}
        {row.source === 'bureau' && (
          <span className="rounded-full border border-sky-200 bg-sky-50 px-1.5 py-px text-[9px] font-semibold text-sky-700 dark:border-sky-800/60 dark:bg-sky-900/30 dark:text-sky-300">
            {t('eligibility.cibil.fromBureau')}
          </span>
        )}
        <button
          type="button"
          onClick={() => onEdit((inputs) => removeTradeline(inputs, index))}
          disabled={disabled}
          className="ml-auto rounded-md p-1 text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:opacity-50 dark:hover:bg-red-900/20"
          aria-label={t('eligibility.cibil.removeTradeline', { number })}
          title={t('eligibility.cibil.removeTradeline', { number })}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
      {row.document && (
        <SourceNote
          source={row.document}
          edited={!fromDocument}
          showDetail={fromDocument}
        />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <TradelineActionToggle
          index={index}
          value={row.action}
          name={`${groupName}-action-${row.key ?? index}`}
          legend={t('eligibility.cibil.actionFor', { number })}
          labels={{
            bt: t('eligibility.cibil.actions.bt'),
            obligate: t('eligibility.cibil.actions.obligate'),
            close: t('eligibility.cibil.actions.close'),
          }}
          describedBy={hintId}
          disabled={disabled}
          onEdit={onEdit}
        />
        <p
          id={hintId}
          className="min-w-0 flex-1 text-[10px] leading-snug text-slate-500 dark:text-slate-400"
        >
          {row.status === 'closed'
            ? t('eligibility.cibil.closedOnReport')
            : t(`eligibility.cibil.actionHints.${row.action}`)}
          {actionNeeds && (
            <span className="ml-1 font-medium text-amber-700 dark:text-amber-400">
              {actionNeeds}
            </span>
          )}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2 @lg:grid-cols-3">
        <Field label={t('eligibility.cibil.loanType')}>
          {({ id }) => (
            <select
              id={id}
              value={row.loan_type ?? ''}
              onChange={(e) =>
                patch({
                  loan_type: (e.target.value || null) as Tradeline['loan_type'],
                })
              }
              disabled={disabled}
              className={CONTROL_CLASS}
            >
              <option value="">{t('eligibility.select')}</option>
              {LOAN_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`eligibility.cibil.loanTypes.${type}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t('eligibility.cibil.lender')}>
          {({ id }) => (
            <input
              id={id}
              type="text"
              value={row.lender ?? ''}
              onChange={(e) => patch({ lender: e.target.value })}
              placeholder={t('eligibility.cibil.lenderPlaceholder')}
              maxLength={100}
              autoComplete="off"
              disabled={disabled}
              className={CONTROL_CLASS}
            />
          )}
        </Field>
        <Field label={t('eligibility.cibil.sanctionAmount')}>
          {({ id }) => (
            <NumberInput
              id={id}
              value={row.sanction_amount}
              onChange={(v) => patch({ sanction_amount: v })}
              disabled={disabled}
            />
          )}
        </Field>
        <Field
          id={`${groupName}-loan-${number}-outstanding`}
          label={t('eligibility.cibil.outstanding')}
        >
          {({ id }) => (
            <NumberInput
              id={id}
              value={row.outstanding}
              onChange={(v) => patch({ outstanding: v })}
              disabled={disabled}
            />
          )}
        </Field>
        <Field
          id={`${groupName}-loan-${number}-emi`}
          label={t('eligibility.cibil.emi')}
        >
          {({ id }) => (
            <NumberInput
              id={id}
              value={row.emi}
              onChange={(v) => patch({ emi: v })}
              disabled={disabled}
            />
          )}
        </Field>
        <Field label={t('eligibility.cibil.status')}>
          {({ id }) => (
            <select
              id={id}
              value={row.status ?? ''}
              onChange={(e) =>
                patch({
                  status: (e.target.value || null) as Tradeline['status'],
                })
              }
              disabled={disabled}
              className={CONTROL_CLASS}
            >
              <option value="">{t('eligibility.select')}</option>
              {TRADELINE_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {t(`eligibility.cibil.statuses.${status}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>

      {/* Expanded: the rest of the report's account details */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls={expanded ? detailsId : undefined}
        className="inline-flex items-center gap-0.5 rounded text-[11px] font-medium text-indigo-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-indigo-300"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
        )}
        {expanded
          ? t('eligibility.cibil.hideDetails')
          : t('eligibility.cibil.details')}
      </button>
      {expanded && (
        <div
          id={detailsId}
          className="grid grid-cols-2 gap-2 @lg:grid-cols-3"
          data-testid="tradeline-details"
        >
          <Field
            label={t('eligibility.cibil.accountNumber')}
            error={accountError}
          >
            {({ id, describedBy }) => (
              <input
                id={id}
                type="text"
                value={row.account_number ?? ''}
                onChange={(e) => patch({ account_number: e.target.value })}
                maxLength={30}
                autoComplete="off"
                spellCheck={false}
                aria-describedby={describedBy}
                aria-invalid={accountError ? true : undefined}
                disabled={disabled}
                className={CONTROL_CLASS}
              />
            )}
          </Field>
          <Field label={t('eligibility.cibil.overdue')}>
            {({ id }) => (
              <NumberInput
                id={id}
                value={row.overdue ?? null}
                onChange={(v) => patch({ overdue: v })}
                disabled={disabled}
              />
            )}
          </Field>
          <Field label={t('eligibility.cibil.emisPaid')}>
            {({ id }) => (
              <NumberInput
                id={id}
                kind="count"
                value={row.emis_paid ?? null}
                onChange={(v) => patch({ emis_paid: v })}
                disabled={disabled}
              />
            )}
          </Field>
          <Field label={t('eligibility.cibil.emisPending')}>
            {({ id }) => (
              <NumberInput
                id={id}
                kind="count"
                value={row.emis_pending ?? null}
                onChange={(v) => patch({ emis_pending: v })}
                disabled={disabled}
              />
            )}
          </Field>
          <Field label={t('eligibility.cibil.openDate')} error={openDateError}>
            {({ id, describedBy }) => (
              <input
                id={id}
                type="date"
                value={row.open_date ?? ''}
                min="1900-01-01"
                max={today}
                onChange={(e) => patch({ open_date: e.target.value || null })}
                aria-describedby={describedBy}
                aria-invalid={openDateError ? true : undefined}
                disabled={disabled}
                className={CONTROL_CLASS}
              />
            )}
          </Field>
          <Field
            label={t('eligibility.cibil.lastPaymentDate')}
            error={lastPaymentError}
          >
            {({ id, describedBy }) => (
              <input
                id={id}
                type="date"
                value={row.last_payment_date ?? ''}
                min={row.open_date || '1900-01-01'}
                max={today}
                onChange={(e) =>
                  patch({ last_payment_date: e.target.value || null })
                }
                aria-describedby={describedBy}
                aria-invalid={lastPaymentError ? true : undefined}
                disabled={disabled}
                className={CONTROL_CLASS}
              />
            )}
          </Field>
        </div>
      )}
    </li>
  );
}

/** Where the CIBIL block came from: the credit report, a bureau pull, or by hand. */
function ReportNote({ cibil }: { cibil: EligibilityCibil }) {
  const { t } = useTranslation();
  const report = cibil.sources?.report ?? null;
  const file = report?.documents[0]?.file ?? null;
  const date = cibil.report_date ?? null;
  const kind = report?.detail || t('eligibility.cibil.creditReport');
  let text: string;
  if (cibil.source === 'credit_report') {
    text = file
      ? date
        ? t('eligibility.cibil.creditReportOn', { file, report: kind, date })
        : t('eligibility.cibil.creditReportRead', { file })
      : date
        ? t('eligibility.cibil.creditReportSavedOn', { date })
        : t('eligibility.cibil.creditReportSaved');
    text = `${text} ${t('eligibility.cibil.creditReportCheck')}`;
  } else if (cibil.source === 'bureau') {
    text = date
      ? t('eligibility.cibil.fromBureauOn', { date })
      : t('eligibility.cibil.fromBureauNote');
  } else if (file) {
    // Typed by hand, and a credit report is in the documents.
    text = t('eligibility.cibil.creditReportAvailable', { file });
  } else {
    text = t('eligibility.cibil.bureauNote');
  }
  return (
    <p
      className="flex items-start gap-1.5 rounded-lg border border-blue-200 bg-blue-50/70 px-2.5 py-1.5 text-[11px] leading-snug text-blue-800 dark:border-blue-800/50 dark:bg-blue-900/20 dark:text-blue-300"
      data-testid="bureau-note"
      data-source={cibil.source ?? 'manual'}
    >
      <Info className="mt-0.5 h-3 w-3 flex-shrink-0" aria-hidden="true" />
      {text}
    </p>
  );
}

interface CibilSectionProps {
  /** The CIBIL block; its `sources` and rows' `document` say what the documents give. */
  cibil: EligibilityCibil;
  onEdit: Edit;
  disabled?: boolean;
  /** Tradelines shown expanded at first (by index; tests render statically). */
  initialExpanded?: number[];
}

/** Sheet 2 of the client's page: score, enquiries and tradelines with BT / Obligate / Close. */
export default function CibilSection({
  cibil,
  onEdit,
  disabled,
  initialExpanded = [],
}: CibilSectionProps) {
  const { t } = useTranslation();
  const groupName = useId();
  const enquiriesErrorId = useId();
  const sources = cibilFieldSources(cibil);
  const score = cibil.score;
  // A score typed as text that is not a number is flagged by the input itself.
  const scoreError =
    score !== null && !Number.isNaN(score) && !scoreLooksValid(score)
      ? t('eligibility.cibil.scoreInvalid')
      : null;
  const rows = cibil.tradelines;
  const order = enquiriesOutOfOrder(cibil.enquiries);
  const enquiriesError = order
    ? t('eligibility.cibil.enquiriesCumulative', {
        first: t(`eligibility.cibil.window.${order[0]}`),
        second: t(`eligibility.cibil.window.${order[1]}`),
      })
    : null;
  const scoreSource = sourceFor('score', score, sources.score);
  const scoreOther = otherSourceFor('score', score, sources.score);
  const enquiriesSource = sourceFor(
    'enquiries',
    cibil.enquiries,
    sources.enquiries,
  );
  const enquiriesOther = otherSourceFor(
    'enquiries',
    cibil.enquiries,
    sources.enquiries,
  );
  const asInputs = cibilInputs(cibil);
  const needed = stillNeeded(asInputs, sources, 'cibil');
  // Saved inputs typed otherwise: the documents' loans they do not hold.
  const missing = documentRowsMissing(rows, cibil.sources?.tradelines);
  const goTo = (item: StillNeeded) => {
    const [head, n, key] = item.field.split('.');
    goToField(
      head === 'score'
        ? `${groupName}-score`
        : head === 'enquiries'
          ? `${groupName}-enquiries-${n}`
          : `${groupName}-loan-${n}-${key}`,
    );
  };

  return (
    <div className="space-y-3">
      <SourceLegend />
      <StillNeededList
        items={needed}
        tab="cibil"
        onGo={goTo}
        fillable={fillableFields(asInputs, sources).length}
        onFill={() => onEdit((i) => fillFromDocuments(i, sources))}
        disabled={disabled}
      />
      <ReportNote cibil={cibil} />

      <section
        className={SECTION_CLASS}
        aria-label={t('eligibility.cibil.report')}
      >
        <SectionTitle>{t('eligibility.cibil.report')}</SectionTitle>
        <div className="grid grid-cols-1 gap-2 @md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]">
          <Field
            id={`${groupName}-score`}
            label={
              <>
                {t('eligibility.cibil.score')}
                <FieldSourceBadge source={scoreSource} />
              </>
            }
            hint={t('eligibility.cibil.scoreHint')}
            error={scoreError}
            note={
              scoreSource ? (
                <SourceNote source={scoreSource} />
              ) : scoreOther ? (
                <DocumentValueOffer
                  source={scoreOther}
                  value={String(scoreOther.value)}
                  onUse={() =>
                    onEdit((i) => setFieldValue(i, 'score', scoreOther.value))
                  }
                  disabled={disabled}
                />
              ) : undefined
            }
          >
            {({ id, describedBy }) => (
              <NumberInput
                id={id}
                kind="count"
                value={score}
                onChange={(v) =>
                  onEdit((inputs) => setCibil(inputs, { score: v }))
                }
                describedBy={describedBy}
                invalid={!!scoreError}
                disabled={disabled}
                placeholder="300–900"
                testId="cibil-score"
              />
            )}
          </Field>
          <fieldset
            className="min-w-0 space-y-1"
            disabled={disabled}
            aria-describedby={enquiriesError ? enquiriesErrorId : undefined}
          >
            <legend className="flex flex-wrap items-center gap-1 text-[11px] font-semibold text-slate-600 dark:text-slate-300">
              {t('eligibility.cibil.enquiries')}
              <FieldSourceBadge source={enquiriesSource} />
            </legend>
            <div className="grid grid-cols-4 gap-1.5">
              {ENQUIRY_WINDOWS.map((w) => (
                <Field
                  key={w}
                  id={`${groupName}-enquiries-${w}`}
                  label={
                    <span className="font-normal text-slate-500 dark:text-slate-400">
                      {t(`eligibility.cibil.window.${w}`)}
                    </span>
                  }
                >
                  {({ id }) => (
                    <NumberInput
                      id={id}
                      kind="count"
                      value={cibil.enquiries[w]}
                      onChange={(v) =>
                        onEdit((inputs) =>
                          setEnquiries(inputs, { [w]: v } as Partial<
                            EligibilityCibil['enquiries']
                          >),
                        )
                      }
                      invalid={!!order && order.includes(w)}
                      testId={`enquiries-${w}`}
                    />
                  )}
                </Field>
              ))}
            </div>
            {enquiriesError && (
              <p
                id={enquiriesErrorId}
                className="text-[10px] leading-snug text-red-600 dark:text-red-400"
              >
                {enquiriesError}
              </p>
            )}
            {enquiriesSource ? (
              <SourceNote source={enquiriesSource} />
            ) : (
              enquiriesOther && (
                <DocumentValueOffer
                  source={enquiriesOther}
                  value={enquiriesText(t, enquiriesOther.value)}
                  onUse={() =>
                    onEdit((i) =>
                      setFieldValue(i, 'enquiries', enquiriesOther.value),
                    )
                  }
                  disabled={disabled}
                />
              )
            )}
          </fieldset>
        </div>
      </section>

      <section
        className={SECTION_CLASS}
        aria-label={t('eligibility.cibil.tradelines', { count: rows.length })}
      >
        <div className="flex items-center gap-2">
          <SectionTitle>
            {t('eligibility.cibil.tradelines', { count: rows.length })}
          </SectionTitle>
          <button
            type="button"
            onClick={() => onEdit(addTradeline)}
            disabled={disabled || rows.length >= MAX_TRADELINES}
            className={`${BUTTON_CLASS} ml-auto`}
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            {t('eligibility.cibil.addTradeline')}
          </button>
        </div>
        {rows.length === 0 ? (
          <p className="text-[11px] text-slate-500 dark:text-slate-400">
            {t('eligibility.cibil.tradelinesEmpty')}
          </p>
        ) : (
          <ul className="space-y-2">
            {rows.map((row, i) => (
              <TradelineRow
                key={row.key ?? i}
                row={row}
                index={i}
                groupName={groupName}
                onEdit={onEdit}
                disabled={disabled}
                initiallyExpanded={initialExpanded.includes(i)}
              />
            ))}
          </ul>
        )}
        <DocumentRowOffers
          rows={missing}
          summary={(row, i) =>
            tradelineSummary(t, row) ||
            t('eligibility.cibil.tradeline', { number: i + 1 })
          }
          onAdd={(row) => onEdit((inputs) => addTradelineRow(inputs, row))}
          disabled={disabled || rows.length >= MAX_TRADELINES}
        />
      </section>
    </div>
  );
}
