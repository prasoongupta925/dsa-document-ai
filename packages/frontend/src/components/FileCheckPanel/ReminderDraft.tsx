import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
} from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { AlertTriangle, Check, Copy, MessageCircle, X } from 'lucide-react';
import type {
  FileCheckApplicant,
  PendingDocument,
} from '../../types/fileCheck';
import {
  REMINDER_LANGUAGES,
  type ReminderChannel,
  type ReminderLanguage,
  type ReminderTemplateId,
} from '../../data/reminderTemplates';
import {
  WHATSAPP_BODY_LIMIT,
  buildReminder,
  reminderChoice,
  smsInfo,
} from '../../lib/reminder';
import { copyText } from '../../lib/clipboard';

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  lang?: string;
}

function Segmented<T extends string>({
  legend,
  name,
  value,
  options,
  onChange,
  firstRef,
}: {
  legend: string;
  name: string;
  value: T;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
  firstRef?: Ref<HTMLInputElement>;
}) {
  return (
    <fieldset className="min-w-0 flex-1">
      <legend className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {legend}
      </legend>
      <div className="flex gap-1">
        {options.map((o, i) => (
          <label key={o.value} className="min-w-0 flex-1">
            <input
              ref={i === 0 ? firstRef : undefined}
              type="radio"
              name={name}
              value={o.value}
              checked={value === o.value}
              onChange={() => onChange(o.value)}
              className="peer sr-only"
            />
            <span
              lang={o.lang}
              className="block cursor-pointer truncate rounded-md border border-black/10 bg-white/50 px-2 py-1 text-center text-[11px] font-medium text-slate-600 transition-colors hover:bg-white peer-checked:border-emerald-600 peer-checked:bg-emerald-600 peer-checked:text-white peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-500 peer-focus-visible:ring-offset-1 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300 dark:hover:bg-white/10"
            >
              {o.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** "123 characters · 1 SMS segment · GSM-7: 160 per SMS, 153 per part". */
export function reminderCountText(
  t: TFunction,
  text: string,
  channel: ReminderChannel,
): string {
  if (channel === 'whatsapp') {
    return t('fileCheck.reminder.countWhatsapp', {
      count: text.length,
      limit: WHATSAPP_BODY_LIMIT.toLocaleString('en-US'),
    });
  }
  const info = smsInfo(text);
  return [
    t('fileCheck.reminder.characters', { count: info.length }),
    t('fileCheck.reminder.segments', { count: info.segments }),
    t(
      info.encoding === 'GSM-7'
        ? 'fileCheck.reminder.encodingGsm'
        : 'fileCheck.reminder.encodingUnicode',
      { single: info.singleLimit, part: info.partLimit },
    ),
  ].join(' · ');
}

interface ReminderDraftProps {
  applicant: FileCheckApplicant;
  /** The verdict's checklist id (its default items have glossary names). */
  checklistId?: string | null;
  /** The checklist's product id: fills {{product}}. */
  product?: string | null;
  /** The verdict's as_of date (YYYY-MM-DD): the Form-16 / ITR year. */
  asOf?: string | null;
  /**
   * Documents of the project still being analysed (the verdict's
   * pending_documents): the missing list may change when they finish.
   */
  pendingDocuments?: PendingDocument[];
  /** id of the region (the toggle button's aria-controls). */
  id?: string;
  onClose: () => void;
  initialLanguage?: ReminderLanguage;
  initialChannel?: ReminderChannel;
  /** Move focus into the box when it opens (off for static rendering). */
  autoFocus?: boolean;
}

/**
 * WhatsApp / SMS reminder built from the verdict with the ported templates:
 * no API call, the missing items and months are exactly the engine's.
 */
export default function ReminderDraft({
  applicant,
  checklistId,
  product,
  asOf,
  pendingDocuments = [],
  id,
  onClose,
  initialLanguage = 'en',
  initialChannel = 'whatsapp',
  autoFocus = true,
}: ReminderDraftProps) {
  const { t } = useTranslation();
  const nameId = useId();
  const textId = useId();
  const templateId = useId();
  const regionRef = useRef<HTMLDivElement>(null);
  const firstRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [language, setLanguage] = useState<ReminderLanguage>(initialLanguage);
  const [channel, setChannel] = useState<ReminderChannel>(initialChannel);
  const [picked, setPicked] = useState<ReminderTemplateId | null>(null);
  const [copyState, setCopyState] = useState<'copied' | 'manual' | null>(null);

  const choice = useMemo(
    () => reminderChoice(applicant, checklistId),
    [applicant, checklistId],
  );
  const template =
    picked && choice.available.includes(picked) ? picked : choice.preferred;
  const draft = useMemo(
    () =>
      template
        ? buildReminder(applicant, {
            template,
            language,
            channel,
            checklistId,
            product,
            asOf,
          })
        : null,
    [applicant, template, language, channel, checklistId, product, asOf],
  );
  const text = draft?.text ?? '';

  // Focus the first choice (or the box when there is nothing to draft).
  useEffect(() => {
    if (autoFocus) (firstRef.current ?? regionRef.current)?.focus();
  }, [autoFocus]);

  // A new text has not been copied yet.
  useEffect(() => setCopyState(null), [text]);
  useEffect(() => {
    if (copyState !== 'copied') return;
    const timer = setTimeout(() => setCopyState(null), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && !e.defaultPrevented) {
      // Close this box only, not the whole File Check panel.
      e.preventDefault();
      onClose();
    }
  };

  const copy = async () => {
    if (!text) return;
    // Falls back to selecting the text (Ctrl+C) without the Clipboard API.
    setCopyState(await copyText(text, textRef.current));
  };

  const languageOptions = REMINDER_LANGUAGES.map((l) => ({
    value: l.code,
    label: l.label,
    lang: l.code,
  }));
  const channelOptions: SegmentedOption<ReminderChannel>[] = [
    { value: 'whatsapp', label: t('fileCheck.reminder.whatsapp') },
    { value: 'sms', label: t('fileCheck.reminder.sms') },
  ];
  const placeholders = draft?.placeholders ?? [];

  return (
    <div
      ref={regionRef}
      id={id}
      role="region"
      aria-labelledby={nameId}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      data-testid="reminder-draft"
      className="space-y-2 rounded-lg border border-emerald-200/80 bg-emerald-50/40 p-2.5 focus:outline-none dark:border-emerald-800/40 dark:bg-emerald-900/10"
    >
      <div className="flex items-center gap-1.5">
        <MessageCircle
          className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400"
          aria-hidden="true"
        />
        <h5
          id={nameId}
          className="min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-700 dark:text-slate-200"
        >
          {t('fileCheck.reminder.title', { name: applicant.applicant })}
        </h5>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-slate-200"
          aria-label={t('fileCheck.reminder.close')}
          title={t('fileCheck.reminder.close')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {pendingDocuments.length > 0 && (
        <p
          role="note"
          data-testid="reminder-pending"
          className="flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] font-medium leading-snug text-amber-900 dark:border-amber-700/60 dark:bg-amber-900/20 dark:text-amber-200"
        >
          <AlertTriangle
            className="mt-0.5 h-3.5 w-3.5 flex-shrink-0"
            aria-hidden="true"
          />
          <span>
            {t('fileCheck.reminder.pending', {
              count: pendingDocuments.length,
              names: pendingDocuments
                .map((d) => d.document_name || d.document_id || '')
                .filter(Boolean)
                .join(', '),
            })}
          </span>
        </p>
      )}
      {!draft ? (
        <p
          role="note"
          className="text-[11px] leading-snug text-slate-600 dark:text-slate-300"
        >
          {t('fileCheck.reminder.nothingToRequest')}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <Segmented
              legend={t('fileCheck.reminder.language')}
              name={`${textId}-language`}
              value={language}
              options={languageOptions}
              onChange={setLanguage}
              firstRef={firstRef}
            />
            <Segmented
              legend={t('fileCheck.reminder.channel')}
              name={`${textId}-channel`}
              value={channel}
              options={channelOptions}
              onChange={setChannel}
            />
          </div>

          {choice.available.length > 1 && (
            <div className="space-y-1">
              <label
                htmlFor={templateId}
                className="block text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400"
              >
                {t('fileCheck.reminder.message')}
              </label>
              <select
                id={templateId}
                value={draft.template}
                onChange={(e) =>
                  setPicked(e.target.value as ReminderTemplateId)
                }
                className="w-full rounded-lg border border-black/10 bg-white/60 px-2 py-1 text-[11px] text-slate-700 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-[#3b4264] dark:bg-[#0d1117] dark:text-slate-200"
              >
                {choice.available.map((tid) => (
                  <option key={tid} value={tid}>
                    {t(`fileCheck.reminder.templates.${tid}`)}
                  </option>
                ))}
              </select>
              <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
                {t(`fileCheck.reminder.templateHints.${draft.template}`)}
              </p>
            </div>
          )}

          <div className="space-y-1">
            <label
              htmlFor={textId}
              className="block text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400"
            >
              {t('fileCheck.reminder.textLabel')}
            </label>
            <textarea
              id={textId}
              ref={textRef}
              readOnly
              value={text}
              lang={language}
              rows={channel === 'sms' ? 4 : 6}
              data-testid="reminder-text"
              className="w-full resize-y rounded-lg border border-black/10 bg-white/70 px-2.5 py-1.5 text-xs leading-relaxed text-slate-800 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-[#3b4264] dark:bg-[#0d1117] dark:text-slate-100"
            />
            <div className="flex flex-wrap items-center gap-2">
              <p
                data-testid="reminder-count"
                className="min-w-0 flex-1 text-[10px] text-slate-500 dark:text-slate-400"
              >
                {reminderCountText(t, text, channel)}
              </p>
              <button
                type="button"
                onClick={copy}
                className="inline-flex flex-shrink-0 items-center gap-1 rounded-md bg-emerald-600 px-2 py-1 text-[11px] font-semibold text-white shadow-sm hover:bg-emerald-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-1 dark:hover:bg-emerald-500"
              >
                {copyState === 'copied' ? (
                  <Check className="h-3 w-3" />
                ) : (
                  <Copy className="h-3 w-3" />
                )}
                {copyState === 'copied' ? t('common.copied') : t('common.copy')}
              </button>
            </div>
            <p
              role="status"
              className="text-[10px] text-amber-700 dark:text-amber-400"
            >
              {copyState === 'manual' ? t('fileCheck.reminder.copyManual') : ''}
            </p>
          </div>

          {draft.countOnly && (
            <p
              data-testid="reminder-count-only"
              className="text-[10px] leading-snug text-slate-600 dark:text-slate-300"
            >
              {t('fileCheck.reminder.smsCountOnly', {
                count: draft.documents.length,
              })}
            </p>
          )}
          {placeholders.length > 0 && (
            <p className="text-[10px] leading-snug text-slate-600 dark:text-slate-300">
              {t('fileCheck.reminder.placeholders', {
                names: placeholders.map((p) => `{{${p}}}`).join(', '),
              })}
            </p>
          )}
          <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
            {t('fileCheck.reminder.draftNote')}
          </p>
        </>
      )}
    </div>
  );
}
