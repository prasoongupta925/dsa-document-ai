import { useEffect, useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  CircleCheck,
  FilePlus2,
  FileText,
  FlaskConical,
  Landmark,
  ListChecks,
  Scale,
  Sigma,
  Table2,
  TriangleAlert,
  Wand2,
} from 'lucide-react';
import type {
  DocumentRef,
  FieldSource,
  SourceKind,
  StillNeeded,
} from '../../types/eligibility';
import {
  formatIndianNumber,
  parseAmount,
  parseCount,
} from '../../lib/eligibility';

// Form controls of the Eligibility & lenders panel (the File Check panel's look).

export const CONTROL_CLASS =
  'w-full px-2.5 py-1.5 text-xs border border-black/10 dark:border-[#3b4264] rounded-lg bg-white/40 dark:bg-[#0d1117] text-[#0f172a] dark:text-[#f1f5f9] placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent disabled:opacity-60 read-only:bg-slate-100/70 dark:read-only:bg-white/[0.04] aria-[invalid=true]:border-red-400 dark:aria-[invalid=true]:border-red-500/70';

export const LABEL_CLASS =
  'flex flex-wrap items-center gap-1 text-[11px] font-semibold text-slate-600 dark:text-slate-300';

export const SECTION_CLASS =
  'space-y-2.5 rounded-xl border border-white/50 bg-white/20 p-3 dark:border-white/[0.08] dark:bg-white/[0.02]';

export const BUTTON_CLASS =
  'inline-flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] font-medium text-slate-700 transition-colors hover:bg-white/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/15 dark:text-slate-200 dark:hover:bg-white/10';

export const PRIMARY_CLASS =
  'inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60';

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
    </h4>
  );
}

// The sheet's colours (From Policy grey, Formula Calculation yellow, From
// Table green), and blue for the values read from the applicant's documents.
export const SOURCE_CHIP_CLASS: Record<SourceKind, string> = {
  policy:
    'border-slate-300 bg-slate-100 text-slate-700 dark:border-white/15 dark:bg-white/10 dark:text-slate-200',
  formula:
    'border-yellow-300 bg-yellow-100 text-yellow-900 dark:border-yellow-700/60 dark:bg-yellow-900/30 dark:text-yellow-200',
  table:
    'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-900/30 dark:text-emerald-300',
  document:
    'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800/60 dark:bg-blue-900/30 dark:text-blue-300',
};

/** The same colours as a column's underline (the lenders table). */
export const SOURCE_RULE_CLASS: Record<SourceKind, string> = {
  policy: 'border-b-2 border-slate-300 dark:border-white/25',
  formula: 'border-b-2 border-yellow-400 dark:border-yellow-500/70',
  table: 'border-b-2 border-emerald-400 dark:border-emerald-500/70',
  document: 'border-b-2 border-blue-400 dark:border-blue-500/70',
};

const SOURCE_ICON = {
  policy: Landmark,
  formula: Sigma,
  table: Table2,
  document: FileText,
} as const;

export const SOURCE_KINDS: readonly SourceKind[] = [
  'policy',
  'formula',
  'table',
  'document',
];

/** 'policy' -> 'policy'; anything that is not a source -> null. */
export function sourceKind(value: unknown): SourceKind | null {
  return SOURCE_KINDS.includes(value as SourceKind)
    ? (value as SourceKind)
    : null;
}

/** "From Policy", "Formula Calculation", "From Table" or "From Document", in its colour. */
export function SourceBadge({
  kind,
  title,
  testId,
}: {
  kind: SourceKind;
  title?: string;
  testId?: string;
}) {
  const { t } = useTranslation();
  const Icon = SOURCE_ICON[kind];
  return (
    <span
      className={`inline-flex items-center gap-0.5 whitespace-nowrap rounded-full border px-1.5 py-px text-[9px] font-semibold normal-case tracking-normal ${SOURCE_CHIP_CLASS[kind]}`}
      title={title ?? t(`eligibility.legend.${kind}Hint`)}
      data-source={kind}
      data-testid={testId ?? `source-${kind}`}
    >
      <Icon className="h-2.5 w-2.5" aria-hidden="true" />
      {t(`eligibility.legend.${kind}`)}
    </span>
  );
}

/** "From Document": the value was read from the applicant's documents. */
export function FromDocumentsBadge() {
  const { t } = useTranslation();
  return (
    <SourceBadge
      kind="document"
      title={t('eligibility.fromDocumentsHint')}
      testId="from-documents"
    />
  );
}

/** The badge of a field's source: From Document, or From Table (the company list). */
export function FieldSourceBadge({
  source,
}: {
  source: FieldSource | null | undefined;
}) {
  if (!source) return null;
  return source.source === 'table' ? (
    <SourceBadge kind="table" testId="from-table" />
  ) : (
    <FromDocumentsBadge />
  );
}

/** The colours of the panel (the client's sheet), and that typed values have none. */
export function SourceLegend({
  kinds = SOURCE_KINDS,
}: {
  kinds?: readonly SourceKind[];
}) {
  const { t } = useTranslation();
  return (
    <div
      className="flex flex-wrap items-center gap-1 text-[10px] leading-snug text-slate-500 dark:text-slate-400"
      role="note"
      aria-label={t('eligibility.legend.title')}
      data-testid="source-legend"
    >
      <span className="font-semibold text-slate-600 dark:text-slate-300">
        {t('eligibility.legend.title')}
      </span>
      {kinds.map((kind) => (
        <SourceBadge key={kind} kind={kind} testId={`legend-${kind}`} />
      ))}
      <span>{t('eligibility.legend.typed')}</span>
    </div>
  );
}

/** "01_loan_application_form.pdf, page 1; 03_salary_slip.pdf, page 1 (+2 more)". */
export function documentsText(
  t: TFunction,
  documents: readonly DocumentRef[],
  shown = 2,
): string {
  const names = documents
    .slice(0, shown)
    .map((d) =>
      d.page !== null
        ? t('eligibility.source.filePage', { file: d.file, page: d.page })
        : d.file,
    );
  const more = documents.length - names.length;
  return (
    names.join('; ') +
    (more > 0 ? ` ${t('eligibility.source.more', { count: more })}` : '')
  );
}

/**
 * Where a value was read: "from <file>, page N", the source's detail
 * (e.g. how the income was verified) and a warning for an amount that is
 * not in the document's text.
 */
export function SourceNote({
  source,
  showDetail = true,
  edited = false,
}: {
  source: FieldSource;
  showDetail?: boolean;
  /** The row came from this document and was changed since. */
  edited?: boolean;
}) {
  const { t } = useTranslation();
  const files = documentsText(t, source.documents);
  const parts = [
    files
      ? t(
          edited ? 'eligibility.source.editedFrom' : 'eligibility.source.from',
          {
            files,
          },
        )
      : edited
        ? t('eligibility.source.edited')
        : null,
    showDetail ? source.detail : null,
  ].filter((p): p is string => !!p);
  if (parts.length === 0 && !source.unverified) return null;
  return (
    <p
      className={`text-[10px] leading-snug ${
        edited
          ? 'text-slate-400 dark:text-slate-500'
          : source.source === 'table'
            ? 'text-emerald-700 dark:text-emerald-400'
            : 'text-blue-700 dark:text-blue-400'
      }`}
      data-testid="source-note"
    >
      {parts.join(' · ')}
      {source.unverified && (
        <span className="ml-1 inline-flex items-center gap-0.5 font-medium text-amber-700 dark:text-amber-400">
          <TriangleAlert className="h-2.5 w-2.5" aria-hidden="true" />
          {t('eligibility.source.unverified')}
        </span>
      )}
    </p>
  );
}

/**
 * The documents' value of a field that holds another one (typed, or saved
 * earlier): shown, never put in by itself; "Use" puts it in.
 */
export function DocumentValueOffer({
  source,
  value,
  onUse,
  disabled,
}: {
  source: FieldSource;
  /** The documents' value as shown, e.g. "₹82,500". */
  value: string;
  onUse?: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const files = documentsText(t, source.documents, 1);
  return (
    <p
      className="break-words text-[10px] leading-snug text-slate-500 dark:text-slate-400"
      data-testid="document-value"
    >
      <FileText
        className="mr-0.5 inline h-2.5 w-2.5 align-[-1px] text-blue-500"
        aria-hidden="true"
      />
      {files
        ? t('eligibility.source.documentsGiveFrom', { value, files })
        : t('eligibility.source.documentsGive', { value })}
      {onUse && (
        <button
          type="button"
          onClick={onUse}
          disabled={disabled}
          className="ml-1 inline rounded-full border border-blue-200 bg-blue-50 px-1.5 font-semibold text-blue-700 hover:bg-blue-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50 dark:border-blue-800/60 dark:bg-blue-900/30 dark:text-blue-300"
          title={t('eligibility.source.useTitle')}
        >
          {t('eligibility.source.use')}
        </button>
      )}
    </p>
  );
}

/**
 * "In the documents, not in this form": the documents' rows (other income,
 * loans) that saved inputs do not hold, each with "Add".
 */
export function DocumentRowOffers<
  T extends { key?: string; document?: FieldSource | null },
>({
  rows,
  summary,
  onAdd,
  disabled,
}: {
  rows: readonly T[];
  /** The row as one line, e.g. "Rented income · ₹12,000 · Registered". */
  summary: (row: T, index: number) => string;
  onAdd: (row: T) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  if (rows.length === 0) return null;
  return (
    <div
      className="space-y-1 rounded-lg border border-blue-200/80 bg-blue-50/50 px-2 py-1.5 dark:border-blue-800/40 dark:bg-blue-900/10"
      data-testid="document-rows"
    >
      <p className="text-[10px] font-semibold text-blue-800 dark:text-blue-300">
        {t('eligibility.source.notInForm')}
      </p>
      <ul className="space-y-1">
        {rows.map((row, i) => (
          <li key={row.key ?? i} className="flex items-start gap-2 text-[11px]">
            <div className="min-w-0 flex-1">
              <p className="font-medium text-slate-700 dark:text-slate-200">
                {summary(row, i)}
              </p>
              {row.document && <SourceNote source={row.document} />}
            </div>
            <button
              type="button"
              onClick={() => onAdd(row)}
              disabled={disabled}
              className={`${BUTTON_CLASS} flex-shrink-0`}
            >
              <FilePlus2 className="h-3 w-3" aria-hidden="true" />
              {t('eligibility.source.add')}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Moves to a field of the form: scrolls it into view and focuses it. */
export function goToField(id: string): void {
  const element = document.getElementById(id);
  element?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  element?.focus({ preventScroll: true });
}

/** A still-needed field's name: "Pincode", "EMI of loan 2 (Sample Bank Card)". */
export function stillNeededLabel(t: TFunction, item: StillNeeded): string {
  if (item.loan) {
    const name =
      item.loan.lender ||
      (item.loan.loanType
        ? t(`eligibility.cibil.loanTypes.${item.loan.loanType}`)
        : null);
    const kind = item.field.endsWith('.outstanding') ? 'Outstanding' : 'Emi';
    return name
      ? t(`eligibility.needed.loan${kind}Named`, {
          number: item.loan.number,
          name,
        })
      : t(`eligibility.needed.loan${kind}`, { number: item.loan.number });
  }
  return t(`eligibility.needed.fields.${item.field.replace('.', '_')}`);
}

interface StillNeededListProps {
  items: StillNeeded[];
  /** The tab shown: the other tabs' fields are named with their tab. */
  tab: 'profile' | 'cibil' | 'lenders';
  /** Focuses the field of an item on this tab (no button without it). */
  onGo?: (item: StillNeeded) => void;
  /** Empty fields a document fills: "Fill N from the documents". */
  fillable?: number;
  onFill?: () => void;
  disabled?: boolean;
}

/**
 * "Still needed before Check eligibility": the fields no document filled and
 * nobody typed, those every lender needs first.
 */
export function StillNeededList({
  items,
  tab,
  onGo,
  fillable = 0,
  onFill,
  disabled,
}: StillNeededListProps) {
  const { t } = useTranslation();
  const required = items.filter((i) => i.required);
  const optional = items.filter((i) => !i.required);
  const entry = (item: StillNeeded) => {
    const label = stillNeededLabel(t, item);
    const here = item.tab === tab;
    return (
      <li
        key={item.field}
        className="inline-flex items-center gap-1"
        data-field={item.field}
        data-required={item.required ? 'true' : undefined}
      >
        {here && onGo ? (
          <button
            type="button"
            onClick={() => onGo(item)}
            className="rounded font-medium text-indigo-700 underline decoration-indigo-300 underline-offset-2 hover:decoration-indigo-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-indigo-300"
          >
            {label}
          </button>
        ) : (
          <span className="font-medium text-slate-700 dark:text-slate-200">
            {label}
          </span>
        )}
        {!here && (
          <span className="text-slate-400">
            ({t(`eligibility.tabs.${item.tab}`)})
          </span>
        )}
        {item.fromDocuments && (
          <span
            className="rounded-full border border-blue-200 bg-blue-50 px-1 text-[9px] font-semibold text-blue-700 dark:border-blue-800/60 dark:bg-blue-900/30 dark:text-blue-300"
            title={t('eligibility.needed.inDocumentsHint')}
          >
            {t('eligibility.needed.inDocuments')}
          </span>
        )}
      </li>
    );
  };

  if (items.length === 0) {
    return (
      <p
        className="flex items-center gap-1.5 rounded-lg border border-green-200 bg-green-50/70 px-2.5 py-1.5 text-[11px] text-green-800 dark:border-green-800/50 dark:bg-green-900/20 dark:text-green-300"
        data-testid="still-needed"
        data-count="0"
      >
        <CircleCheck className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
        {t('eligibility.needed.none')}
      </p>
    );
  }
  return (
    <section
      className="space-y-1 rounded-lg border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-[11px] leading-snug text-amber-900 dark:border-amber-800/50 dark:bg-amber-900/15 dark:text-amber-200"
      aria-label={t('eligibility.needed.title')}
      data-testid="still-needed"
      data-count={items.length}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="flex items-center gap-1 text-[11px] font-semibold">
          <ListChecks className="h-3 w-3" aria-hidden="true" />
          {t('eligibility.needed.title')} ({items.length})
        </h4>
        {onFill && fillable > 0 && (
          <button
            type="button"
            onClick={onFill}
            disabled={disabled}
            className={`${BUTTON_CLASS} ml-auto border-blue-200 bg-white/70 text-blue-700 dark:border-blue-800/60 dark:bg-transparent dark:text-blue-300`}
            data-testid="fill-from-documents"
          >
            <Wand2 className="h-3 w-3" aria-hidden="true" />
            {t('eligibility.needed.fill', { count: fillable })}
          </button>
        )}
      </div>
      {required.length > 0 && (
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-semibold">
            {t('eligibility.needed.required')}
          </span>
          <ul className="contents">{required.map(entry)}</ul>
        </div>
      )}
      {optional.length > 0 && (
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-amber-800/90 dark:text-amber-200/80">
          <span className="font-semibold">
            {t('eligibility.needed.optional')}
          </span>
          <ul className="contents">{optional.map(entry)}</ul>
        </div>
      )}
    </section>
  );
}

/** "Sample policy — replace with your lender grid", next to anything from the policies. */
export function SamplePolicyTag({ className = '' }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full border border-amber-300 bg-amber-50 px-1.5 py-px text-[9px] font-semibold text-amber-800 dark:border-amber-700/60 dark:bg-amber-900/30 dark:text-amber-300 ${className}`}
      data-testid="sample-policy"
    >
      <FlaskConical className="h-2.5 w-2.5" aria-hidden="true" />
      {t('eligibility.samplePolicy')}
    </span>
  );
}

/** "Indicative: the lender decides", next to every eligibility result. */
export function IndicativeTag({ className = '' }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full border border-slate-300 bg-slate-50 px-1.5 py-px text-[9px] font-semibold text-slate-700 dark:border-white/15 dark:bg-white/10 dark:text-slate-200 ${className}`}
      data-testid="indicative"
    >
      <Scale className="h-2.5 w-2.5" aria-hidden="true" />
      {t('eligibility.indicative')}
    </span>
  );
}

interface FieldProps {
  label: ReactNode;
  /** Renders the control with the ids the label and messages use. */
  children: (ids: { id: string; describedBy: string | undefined }) => ReactNode;
  hint?: ReactNode;
  error?: string | null;
  /** Where the value came from (SourceNote, DocumentValueOffer), under the control. */
  note?: ReactNode;
  /** The control's id (default: a generated one), e.g. to focus it from a list. */
  id?: string;
  className?: string;
}

/** Label, control, the source note, then the hint or the error the control is described by. */
export function Field({
  label,
  children,
  hint,
  error,
  note,
  id: fixedId,
  className,
}: FieldProps) {
  const generatedId = useId();
  const id = fixedId ?? generatedId;
  const messageId = useId();
  const noteId = useId();
  const message = error || hint;
  const describedBy =
    [note ? noteId : null, message ? messageId : null]
      .filter(Boolean)
      .join(' ') || undefined;
  return (
    <div className={`min-w-0 space-y-1 ${className ?? ''}`}>
      <label htmlFor={id} className={LABEL_CLASS}>
        {label}
      </label>
      {children({ id, describedBy })}
      {note && <div id={noteId}>{note}</div>}
      {message && (
        <p
          id={messageId}
          className={`text-[10px] leading-snug ${
            error
              ? 'text-red-600 dark:text-red-400'
              : 'text-slate-500 dark:text-slate-400'
          }`}
        >
          {message}
        </p>
      )}
    </div>
  );
}

interface NumberInputProps {
  id: string;
  value: number | null;
  /** The typed number; null when blank; NaN when it is not a number. */
  onChange: (value: number | null) => void;
  kind?: 'amount' | 'count';
  describedBy?: string;
  /** Shows a rupee sign in front (amounts). */
  rupees?: boolean;
  readOnly?: boolean;
  disabled?: boolean;
  placeholder?: string;
  /** Set by the field when the value is out of range. */
  invalid?: boolean;
  testId?: string;
}

/**
 * Amounts and counts typed as text: '20,58,000' is accepted, and amounts
 * are shown with Indian digit grouping once the field is left.
 */
export function NumberInput({
  id,
  value,
  onChange,
  kind = 'amount',
  describedBy,
  rupees = kind === 'amount',
  readOnly,
  disabled,
  placeholder,
  invalid,
  testId,
}: NumberInputProps) {
  const show = (n: number | null) =>
    n === null || Number.isNaN(n)
      ? ''
      : kind === 'amount'
        ? formatIndianNumber(n)
        : String(n);
  const { t } = useTranslation();
  const errorId = useId();
  const [text, setText] = useState(() => show(value));
  const [focused, setFocused] = useState(false);
  const parse = kind === 'amount' ? parseAmount : parseCount;
  const typedInvalid = Number.isNaN(parse(text) as number);
  const describedByIds =
    [describedBy, typedInvalid ? errorId : null].filter(Boolean).join(' ') ||
    undefined;

  // Show a new value from outside (a reload) unless the user is typing.
  useEffect(() => {
    if (!focused && !(value !== null && Number.isNaN(value))) {
      setText(show(value));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, focused]);

  return (
    <div>
      <div className="relative">
        {rupees && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-slate-400"
          >
            ₹
          </span>
        )}
        <input
          id={id}
          type="text"
          inputMode={kind === 'amount' ? 'decimal' : 'numeric'}
          autoComplete="off"
          value={text}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false);
            const n = parse(text);
            if (n !== null && !Number.isNaN(n)) setText(show(n));
          }}
          onChange={(e) => {
            setText(e.target.value);
            onChange(parse(e.target.value));
          }}
          readOnly={readOnly}
          disabled={disabled}
          placeholder={placeholder}
          aria-describedby={describedByIds}
          aria-invalid={typedInvalid || invalid ? true : undefined}
          data-testid={testId}
          className={`${CONTROL_CLASS} tabular-nums ${rupees ? 'pl-6' : ''}`}
        />
      </div>
      {typedInvalid && (
        <p
          id={errorId}
          className="mt-0.5 text-[10px] leading-snug text-red-600 dark:text-red-400"
        >
          {kind === 'amount'
            ? t('eligibility.notAnAmount')
            : t('eligibility.notAWholeNumber')}
        </p>
      )}
    </div>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  /** Radio group name, unique in the page. */
  name: string;
  legend: ReactNode;
  options: readonly SegmentOption<T>[];
  value: T | null;
  onChange: (value: T) => void;
  /** Shows the legend above the buttons (else it is for screen readers only). */
  showLegend?: boolean;
  disabled?: boolean;
  describedBy?: string;
  testId?: string;
}

/**
 * A segmented control: native radio buttons (arrow keys move between them)
 * drawn as one button bar. No hooks, so tests can call it directly.
 */
export function Segmented<T extends string>({
  name,
  legend,
  options,
  value,
  onChange,
  showLegend = false,
  disabled,
  describedBy,
  testId,
}: SegmentedProps<T>) {
  return (
    <fieldset
      className="min-w-0 space-y-1"
      aria-describedby={describedBy}
      data-testid={testId}
      disabled={disabled}
    >
      <legend className={showLegend ? LABEL_CLASS : 'sr-only'}>{legend}</legend>
      <div className="inline-flex max-w-full flex-wrap overflow-hidden rounded-lg border border-black/10 bg-white/40 dark:border-[#3b4264] dark:bg-[#0d1117]">
        {options.map((option) => (
          <label
            key={option.value}
            className="relative cursor-pointer border-r border-black/10 last:border-r-0 dark:border-[#3b4264]"
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="peer sr-only"
            />
            <span className="block whitespace-nowrap px-2.5 py-1 text-[11px] font-semibold text-slate-600 transition-colors hover:bg-white/60 peer-checked:bg-indigo-600 peer-checked:text-white peer-focus-visible:ring-2 peer-focus-visible:ring-inset peer-focus-visible:ring-indigo-400 peer-disabled:cursor-not-allowed peer-disabled:opacity-60 dark:text-slate-300 dark:hover:bg-white/10 dark:peer-checked:bg-indigo-500">
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
