// Builds a WhatsApp / SMS reminder for a NOT READY applicant from the
// engine's verdict and the templates in data/reminderTemplates.ts. No API
// call and no model: the missing items and months are exactly the verdict's
// (its required MISSING checklist rows, i.e. `missing_items`), and a
// mismatch message names only the document type, never a value.
import type { FileCheckApplicant, FileCheckItemRow } from '../types/fileCheck';
import {
  REMINDER_DOC_NAMES,
  REMINDER_MONTHS,
  REMINDER_MONTH_WORDS,
  REMINDER_SMS_MONTHS_EN,
  REMINDER_TEMPLATES,
  REMINDER_TEMPLATE_ORDER,
  type ReminderChannel,
  type ReminderDocCode,
  type ReminderLanguage,
  type ReminderTemplateId,
} from '../data/reminderTemplates';
import { SALARIED_PERSONAL_LOAN_ID, normalizeStatus } from './fileCheck';

export type ReminderMonthNoun = 'salary_slip' | 'bank_statement';

/** One missing required checklist item, as a reminder words it. */
export interface ReminderItem {
  itemId: string;
  /** The engine's item label, used verbatim when the glossary has no name. */
  label: string;
  /** YYYY-MM months the item is missing, sorted; empty for other items. */
  months: string[];
  /** A month-based item the glossary words ("June 2026 salary slip"). */
  noun: ReminderMonthNoun | null;
  /** Glossary names of an item without months (the default checklist's). */
  docs: ReminderDocCode[];
}

/** The default checklist's items the glossary has names for. */
const DEFAULT_CHECKLIST_DOCS: Record<string, ReminderDocCode[]> = {
  loan_application: ['APPLICATION_FORM'],
  // "Identity details (PAN + masked Aadhaar)": both copies are asked for.
  identity_details: ['PAN_COPY', 'AADHAAR_MASKED'],
  form16_itr: ['FORM16_ITR'],
};
/** The default checklist's "Form-16 / ITR (latest FY)" keeps its year hint. */
const LATEST_FY_ITEMS = new Set(['form16_itr']);

/**
 * The source's rule for English SMS lists (T1–T3): longer than about 36
 * characters, send "3 documents" instead; the upload page lists them. Its
 * own sample list, "Jun slip, Mar-May bank stmt, Form-16", is 36.
 */
export const SMS_EN_LIST_MAX = 36;

/** Document type named by a MISMATCH, by the engine's check id. */
const MISMATCH_DOCS: Record<string, ReminderDocCode> = {
  pan: 'PAN_COPY',
  aadhaar_last4: 'AADHAAR_MASKED',
  applicant_name: 'PAN_COPY',
  employer: 'SALARY_SLIP',
  employer_vs_bank_credits: 'BANK_STATEMENT',
  declared_vs_slip_net: 'SALARY_SLIP',
  declared_vs_bank_credits: 'BANK_STATEMENT',
  slip_net_vs_bank_credits: 'BANK_STATEMENT',
  form16_vs_slip_gross: 'FORM16_ITR',
  declared_emis_vs_bank_debits: 'BANK_STATEMENT',
};
/** Checks that are not about a customer's document (FOIR is a policy figure). */
const NOT_DOCUMENT_CHECKS = new Set([
  'foir',
  'unclassified_documents',
  'declared_net_salary',
]);
/** Fallback for other checks: the documents' types (the application excluded). */
const DOC_TYPE_CODES: Record<string, ReminderDocCode> = {
  identity_details: 'PAN_COPY',
  salary_slip: 'SALARY_SLIP',
  bank_statement: 'BANK_STATEMENT',
  form16_itr: 'FORM16_ITR',
};

const PRODUCT_LABELS: Record<string, string> = {
  personal_loan: 'personal loan',
  business_loan: 'business loan',
  home_loan: 'home loan',
  car_loan: 'car loan',
  education_loan: 'education loan',
  lap: 'loan against property',
};

const HONORIFICS = new Set([
  'mr',
  'mrs',
  'ms',
  'miss',
  'dr',
  'shri',
  'sri',
  'smt',
  'kumari',
  'km',
]);
const PAN_RE = /^[a-z]{5}[0-9]{4}[a-z]$/i;
const YM_RE = /^(\d{4})-(0[1-9]|1[0-2])$/;
const ENGINE_MONTHS = [
  'jan',
  'feb',
  'mar',
  'apr',
  'may',
  'jun',
  'jul',
  'aug',
  'sep',
  'oct',
  'nov',
  'dec',
];

// ------------------------------------------------------------------ inputs

/** The customer's first name for the greeting; null when not a usable name. */
export function reminderFirstName(
  applicant: string | null | undefined,
): string | null {
  const tokens = String(applicant ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const first = tokens.find(
    (t) => !HONORIFICS.has(t.replace(/\.$/, '').toLowerCase()),
  );
  // Never put an ID number in a message; 'Unknown' is the engine's no-name label.
  if (
    !first ||
    /^unknown$/i.test(first) ||
    PAN_RE.test(first) ||
    /\d/.test(first)
  ) {
    return null;
  }
  return first;
}

/**
 * 'FY 2025-26': the latest financial year (April to March) whose Form-16 is
 * out on `asOf` (employers issue it by 15 June), written as the glossary
 * writes it; null without a valid YYYY-MM-DD date.
 */
export function latestForm16Fy(asOf: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(asOf ?? ''));
  if (!m) return null;
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  // FY (end-1)-(end) closes on 31 March of `end`; its Form-16 comes by 15 June.
  const end = month > 6 || (month === 6 && day >= 15) ? year : year - 1;
  return `FY ${end - 1}-${String(end % 100).padStart(2, '0')}`;
}

/** " (FY 2025-26)" after Form-16 or ITR; English only says "latest FY" without a date. */
function form16YearHint(
  language: ReminderLanguage,
  asOf: string | null | undefined,
): string {
  const fy = latestForm16Fy(asOf);
  if (fy) return ` (${fy})`;
  return language === 'en' ? ' (latest FY)' : '';
}

/** 'personal_loan' -> 'personal loan'; null when the product is unknown. */
export function reminderProductLabel(
  product: string | null | undefined,
): string | null {
  return (product && PRODUCT_LABELS[product]) || null;
}

function monthNoun(text: string): ReminderMonthNoun | null {
  if (/salary\s*slip/i.test(text)) return 'salary_slip';
  if (/bank\s*statement/i.test(text)) return 'bank_statement';
  return null;
}

function validMonths(months: readonly unknown[] | null | undefined): string[] {
  return Array.from(
    new Set(
      (months ?? []).filter(
        (m): m is string => typeof m === 'string' && YM_RE.test(m),
      ),
    ),
  ).sort();
}

/** 'Bank statement: Mar 2026, Apr 2026' -> ['Bank statement', ['2026-03', '2026-04']] */
function parseMissingItemText(text: string): {
  prefix: string;
  months: string[];
} {
  const colon = text.lastIndexOf(':');
  if (colon < 0) return { prefix: text.trim(), months: [] };
  const tail = text
    .slice(colon + 1)
    .split(',')
    .map((s) => s.trim());
  const months: string[] = [];
  for (const part of tail) {
    const m = /^([a-z]{3})[a-z]*\s+(\d{4})$/i.exec(part);
    const idx = m ? ENGINE_MONTHS.indexOf(m[1].toLowerCase()) : -1;
    if (!m || idx < 0) return { prefix: text.trim(), months: [] };
    months.push(`${m[2]}-${String(idx + 1).padStart(2, '0')}`);
  }
  return { prefix: text.slice(0, colon).trim(), months: validMonths(months) };
}

function isMissingRequired(row: FileCheckItemRow): boolean {
  return normalizeStatus(row.status) === 'MISSING' && row.required !== false;
}

/**
 * The applicant's missing required items, in the engine's order: its
 * MISSING required checklist rows (the rows behind `missing_items`) with
 * their exact missing months.
 */
export function reminderItems(
  applicant: FileCheckApplicant,
  checklistId?: string | null,
): ReminderItem[] {
  const rows = (applicant.checklist ?? []).filter(isMissingRequired);
  const texts = (applicant.missing_items ?? []).filter(
    (s): s is string => typeof s === 'string' && !!s.trim(),
  );
  const isDefault = checklistId === SALARIED_PERSONAL_LOAN_ID;
  if (rows.length === 0) {
    // Only the summary list (not expected from the engine): parse its months.
    return texts.map((text, i) => {
      const { prefix, months } = parseMissingItemText(text);
      return {
        itemId: `missing_${i + 1}`,
        label: months.length > 0 ? prefix : text.trim(),
        months,
        noun: months.length > 0 ? monthNoun(prefix) : null,
        docs: [],
      };
    });
  }
  // missing_items has one entry per row, prefixed with the item's
  // missing_label ("Salary slip: Jun 2026") where the checklist sets one.
  const aligned = texts.length === rows.length;
  return rows.map((row, i) => {
    const months = validMonths(row.missing_months);
    const prefix = aligned ? parseMissingItemText(texts[i]).prefix : '';
    const label = String(row.item || row.item_id || '').trim();
    return {
      itemId: String(row.item_id ?? ''),
      label,
      months,
      // The missing_label prefix is the engine's own noun: it wins.
      noun: months.length > 0 ? (monthNoun(prefix) ?? monthNoun(label)) : null,
      docs:
        months.length === 0 && isDefault
          ? (DEFAULT_CHECKLIST_DOCS[String(row.item_id)] ?? [])
          : [],
    };
  });
}

/**
 * Document types named by the applicant's MISMATCH findings (T5), in order,
 * without duplicates. FOIR and other non-document checks are left out.
 */
export function reminderMismatchDocs(
  applicant: FileCheckApplicant,
): ReminderDocCode[] {
  const typeByName = new Map(
    (applicant.documents ?? []).map((d) => [d.document_name, d.doc_type]),
  );
  const codes: ReminderDocCode[] = [];
  const add = (code: ReminderDocCode | undefined) => {
    if (code && !codes.includes(code)) codes.push(code);
  };
  for (const row of applicant.consistency ?? []) {
    if (normalizeStatus(row.status) !== 'MISMATCH') continue;
    const id = String(row.check_id ?? '');
    if (NOT_DOCUMENT_CHECKS.has(id)) continue;
    if (MISMATCH_DOCS[id]) {
      add(MISMATCH_DOCS[id]);
      continue;
    }
    for (const name of row.documents ?? []) {
      add(DOC_TYPE_CODES[typeByName.get(name) ?? '']);
    }
  }
  return codes;
}

// ------------------------------------------------------------------ months

interface Ym {
  year: number;
  month: number;
}

function toYm(value: string): Ym {
  const m = YM_RE.exec(value) as RegExpExecArray;
  return { year: Number(m[1]), month: Number(m[2]) };
}

function isContiguous(months: Ym[]): boolean {
  return months.every(
    (m, i) =>
      i === 0 ||
      m.year * 12 + m.month ===
        months[i - 1].year * 12 + months[i - 1].month + 1,
  );
}

/** WhatsApp wording: 'June 2026', 'March to May 2026', 'June and July 2026'. */
export function whatsappMonthPhrase(
  months: string[],
  language: ReminderLanguage,
): string {
  const yms = validMonths(months).map(toYm);
  if (yms.length === 0) return '';
  const names = REMINDER_MONTHS[language];
  const words = REMINDER_MONTH_WORDS[language];
  const label = (ym: Ym, withYear: boolean) =>
    withYear ? `${names[ym.month - 1]} ${ym.year}` : names[ym.month - 1];
  if (yms.length === 1) return label(yms[0], true);
  const sameYear = yms.every((m) => m.year === yms[0].year);
  const last = yms[yms.length - 1];
  if (yms.length >= 3 && isContiguous(yms)) {
    return `${label(yms[0], !sameYear)}${words.rangeJoin}${label(last, true)}${words.rangeSuffix}`;
  }
  const parts = yms.map((m) => label(m, !sameYear));
  const list = `${parts.slice(0, -1).join(', ')}${words.listAnd}${parts[parts.length - 1]}`;
  return sameYear ? `${list} ${yms[0].year}` : list;
}

/** SMS wording: 'Jun', 'Mar-May' ('Mar-May 2026' with the year), 'Mar/May'. */
export function smsMonthPhrase(
  months: string[],
  language: ReminderLanguage,
  withYear: boolean,
): string {
  const yms = validMonths(months).map(toYm);
  if (yms.length === 0) return '';
  const names =
    language === 'en' ? REMINDER_SMS_MONTHS_EN : REMINDER_MONTHS[language];
  const sameYear = yms.every((m) => m.year === yms[0].year);
  const label = (ym: Ym, year: boolean) =>
    year ? `${names[ym.month - 1]} ${ym.year}` : names[ym.month - 1];
  const each = withYear && !sameYear;
  const tail = withYear && sameYear ? ` ${yms[0].year}` : '';
  if (yms.length === 1) return label(yms[0], withYear);
  if (isContiguous(yms)) {
    return `${label(yms[0], each)}-${label(yms[yms.length - 1], each)}${tail}`;
  }
  return `${yms.map((m) => label(m, each)).join('/')}${tail}`;
}

// ------------------------------------------------------------------ wording

function monthItemPhrase(
  item: ReminderItem,
  language: ReminderLanguage,
  channel: ReminderChannel,
  single: boolean,
): string {
  const many = item.months.length > 1;
  if (channel === 'whatsapp') {
    const m = whatsappMonthPhrase(item.months, language);
    if (item.noun === 'salary_slip') {
      if (language === 'en') {
        return many ? `salary slips for ${m}` : `${m} salary slip`;
      }
      if (language === 'hi') {
        return many ? `${m} की salary slips` : `${m} की salary slip`;
      }
      return many ? `${m} च्या salary slips` : `${m} ची salary slip`;
    }
    if (item.noun === 'bank_statement') {
      if (language === 'en') return `bank statement for ${m}`;
      if (language === 'hi') return `${m} का bank statement`;
      return `${m} चे bank statement`;
    }
    return `${item.label}: ${m}`;
  }
  // SMS: a list (T1–T3) drops the year; the one T6 item keeps it.
  const m = smsMonthPhrase(item.months, language, single);
  const slip = many ? 'slips' : 'slip';
  if (item.noun === 'salary_slip') {
    if (!single) return `${m} ${slip}`;
    if (language === 'en') return `${slip} for ${m}`;
    if (language === 'hi') return `${m} की ${slip}`;
    return `${m} ${many ? 'च्या' : 'ची'} ${slip}`;
  }
  if (item.noun === 'bank_statement') {
    if (language === 'en') {
      return single ? `bank stmt for ${m}` : `${m} bank stmt`;
    }
    if (!single) return `${m} bank statement`;
    return language === 'hi'
      ? `${m} का bank statement`
      : `${m} चे bank statement`;
  }
  return `${item.label}: ${m}`;
}

function docName(
  code: ReminderDocCode,
  language: ReminderLanguage,
  channel: ReminderChannel,
): string {
  const name = REMINDER_DOC_NAMES[code];
  return channel === 'whatsapp' ? name.whatsapp[language] : name.sms;
}

/**
 * The names a reminder lists for the missing items, in order. `asOf` (the
 * verdict's date) gives the Form-16 / ITR year on WhatsApp.
 */
export function reminderDocumentPhrases(
  items: ReminderItem[],
  language: ReminderLanguage,
  channel: ReminderChannel,
  single = false,
  asOf?: string | null,
): string[] {
  return items.flatMap((item) => {
    if (item.months.length > 0) {
      return [monthItemPhrase(item, language, channel, single)];
    }
    if (item.docs.length > 0) {
      const fy =
        channel === 'whatsapp' && LATEST_FY_ITEMS.has(item.itemId)
          ? form16YearHint(language, asOf)
          : '';
      return item.docs.map(
        (code) => `${docName(code, language, channel)}${fy}`,
      );
    }
    return [item.label];
  });
}

// ------------------------------------------------------------------ build

export interface ReminderChoice {
  /** Templates that apply to this verdict, in the picker's order. */
  available: ReminderTemplateId[];
  /** The best fit, or null when there is nothing to ask the customer for. */
  preferred: ReminderTemplateId | null;
  items: ReminderItem[];
  mismatchDocs: ReminderDocCode[];
}

/**
 * Which messages fit: T1–T3 when items are missing, T6 when exactly one
 * month-based item is left, T5 when a document mismatch was found.
 */
export function reminderChoice(
  applicant: FileCheckApplicant,
  checklistId?: string | null,
): ReminderChoice {
  const items = reminderItems(applicant, checklistId);
  const mismatchDocs = reminderMismatchDocs(applicant);
  const fits = new Set<ReminderTemplateId>();
  if (items.length > 0) {
    fits.add('T1').add('T2').add('T3');
    if (items.length === 1 && items[0].months.length > 0) fits.add('T6');
  }
  if (mismatchDocs.length > 0) fits.add('T5');
  const available = REMINDER_TEMPLATE_ORDER.filter((id) => fits.has(id));
  const preferred = fits.has('T6')
    ? 'T6'
    : fits.has('T2')
      ? 'T2'
      : fits.has('T5')
        ? 'T5'
        : null;
  return { available, preferred, items, mismatchDocs };
}

export interface ReminderOptions {
  template: ReminderTemplateId;
  language: ReminderLanguage;
  channel: ReminderChannel;
  /** The verdict's checklist (its default items have glossary names). */
  checklistId?: string | null;
  /** The checklist's product id (e.g. personal_loan): fills {{product}}. */
  product?: string | null;
  /** The verdict's as_of date (YYYY-MM-DD): the Form-16 / ITR year. */
  asOf?: string | null;
}

export interface ReminderDraft {
  template: ReminderTemplateId;
  language: ReminderLanguage;
  channel: ReminderChannel;
  text: string;
  /** The document names behind {{documents}} / {{document}}. */
  documents: string[];
  /** An English SMS lists only how many (the list was over SMS_EN_LIST_MAX). */
  countOnly: boolean;
  /** Placeholders left in the text for the user to fill, in order. */
  placeholders: string[];
}

const PLACEHOLDER_RE = /\{\{(\w+)\}\}/g;

/** The placeholders in a text, in order, without duplicates. */
export function reminderPlaceholders(text: string): string[] {
  return Array.from(
    new Set(Array.from(text.matchAll(PLACEHOLDER_RE), (m) => m[1])),
  );
}

/** Fills one template from the verdict; unknown values stay as {{placeholders}}. */
export function buildReminder(
  applicant: FileCheckApplicant,
  options: ReminderOptions,
): ReminderDraft {
  const { template, language, channel } = options;
  const tpl = REMINDER_TEMPLATES[template];
  const values: Record<string, string> = {};
  const firstName = reminderFirstName(applicant.applicant);
  if (firstName) values.first_name = firstName;
  const product = reminderProductLabel(options.product);
  if (product) values.product = product;

  let documents: string[] = [];
  let countOnly = false;
  if (tpl.purpose === 'mismatch') {
    // T5 SMS keeps the English name, like the source's sample ("PAN card copy").
    documents = reminderMismatchDocs(applicant).map((code) =>
      channel === 'whatsapp'
        ? REMINDER_DOC_NAMES[code].whatsapp[language]
        : REMINDER_DOC_NAMES[code].whatsapp.en,
    );
    if (documents.length > 0) values.document = documents.join(', ');
  } else {
    const items = reminderItems(applicant, options.checklistId);
    documents = reminderDocumentPhrases(
      items,
      language,
      channel,
      template === 'T6',
      options.asOf,
    );
    const list = documents.join(', ');
    countOnly =
      channel === 'sms' &&
      language === 'en' &&
      template !== 'T6' &&
      list.length > SMS_EN_LIST_MAX;
    if (countOnly) {
      values.documents =
        documents.length === 1 ? '1 document' : `${documents.length} documents`;
    } else if (documents.length > 0) {
      values.documents = list;
    }
  }

  const body =
    channel === 'whatsapp' ? tpl.whatsapp[language] : tpl.sms[language];
  let text = body.replace(PLACEHOLDER_RE, (whole, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? values[key] : whole,
  );
  const footer =
    channel === 'whatsapp' ? tpl.whatsappFooter?.[language] : undefined;
  if (footer) text = `${text}\n\n${footer}`;
  return {
    template,
    language,
    channel,
    text,
    documents,
    countOnly,
    placeholders: reminderPlaceholders(text),
  };
}

// ------------------------------------------------------------------ SMS

// GSM 03.38 basic character set and its extension table (2 septets each).
const GSM_BASIC =
  '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà';
const GSM_EXTENDED = '^{}\\[~]|€\f';

export interface SmsInfo {
  encoding: 'GSM-7' | 'UCS-2';
  /** GSM-7 septets or UTF-16 code units. */
  length: number;
  segments: number;
  /** Characters in a one-part SMS: 160 or 70. */
  singleLimit: number;
  /** Characters per part of a multi-part SMS: 153 or 67. */
  partLimit: number;
}

/**
 * Encoding, length and segments of an SMS (AWS End User Messaging limits):
 * GSM-7 160 / 153 per part, anything else UCS-2 70 / 67 per part.
 */
export function smsInfo(text: string): SmsInfo {
  let septets = 0;
  let gsm = true;
  for (const ch of text) {
    if (GSM_BASIC.includes(ch)) septets += 1;
    else if (GSM_EXTENDED.includes(ch)) septets += 2;
    else {
      gsm = false;
      break;
    }
  }
  const [length, singleLimit, partLimit] = gsm
    ? [septets, 160, 153]
    : [text.length, 70, 67];
  const segments =
    length === 0
      ? 0
      : length <= singleLimit
        ? 1
        : Math.ceil(length / partLimit);
  return {
    encoding: gsm ? 'GSM-7' : 'UCS-2',
    length,
    segments,
    singleLimit,
    partLimit,
  };
}

/** WhatsApp's template body limit. */
export const WHATSAPP_BODY_LIMIT = 1024;
