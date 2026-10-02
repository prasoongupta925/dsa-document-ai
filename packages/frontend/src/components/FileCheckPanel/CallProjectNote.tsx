import { useTranslation } from 'react-i18next';
import { Headphones } from 'lucide-react';
import type { FileCheckResult } from '../../types/fileCheck';
import { skippedDocuments } from '../../lib/fileCheck';

/** An audio / video document the engine lists apart from the loan file. */
export interface RecordingDocument {
  document_id?: string | null;
  document_name?: string | null;
  file_type?: string | null;
}

/** A verdict with the engine's recording_documents (absent from engines before call projects). */
export type FileCheckResultWithRecordings = FileCheckResult & {
  recording_documents?: RecordingDocument[] | null;
};

export function recordingDocuments(
  result: FileCheckResultWithRecordings,
): RecordingDocument[] {
  const list = result.recording_documents;
  return Array.isArray(list) ? list : [];
}

/**
 * The project holds call recordings and nothing the file check could read
 * (no applicant, no other unchecked document): a Call QA project, not a
 * loan file that is NOT READY.
 */
export function isCallRecordingsOnly(result: FileCheckResult): boolean {
  return (
    (result.applicants ?? []).length === 0 &&
    recordingDocuments(result).length > 0 &&
    skippedDocuments(result).length === 0
  );
}

/** Shown instead of the verdict in a call-recording project. */
export default function CallProjectNote({
  result,
}: {
  result: FileCheckResult;
}) {
  const { t } = useTranslation();
  const recordings = recordingDocuments(result);
  return (
    <section
      aria-label={t('fileCheck.callProject.title')}
      className="space-y-2 rounded-xl border border-sky-200 bg-sky-50/70 px-4 py-3 dark:border-sky-800/50 dark:bg-sky-900/20"
      data-testid="call-project-note"
    >
      <div className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-300">
          <Headphones className="h-4 w-4" aria-hidden="true" />
        </span>
        <h4 className="text-sm font-semibold text-sky-900 dark:text-sky-100">
          {t('fileCheck.callProject.title')}
        </h4>
      </div>
      <p className="text-xs leading-snug text-slate-700 dark:text-slate-200">
        {t('fileCheck.callProject.body')}
      </p>
      <p
        className="rounded-lg border border-sky-200/80 bg-white/50 px-2.5 py-1.5 text-[11px] leading-snug text-sky-900 dark:border-sky-800/40 dark:bg-white/[0.03] dark:text-sky-200"
        data-testid="call-qa-hint"
      >
        {t('fileCheck.callProject.hint', {
          suggestion: t('chat.chips.callScore'),
        })}
      </p>
      <div>
        <h5 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {t('fileCheck.callProject.recordings', { count: recordings.length })}
        </h5>
        <ul className="mt-1 space-y-0.5">
          {recordings.map((d, i) => (
            <li
              key={`${d.document_id ?? i}`}
              className="break-words text-[11px] text-slate-600 dark:text-slate-300"
            >
              {d.document_name || d.document_id || '–'}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
