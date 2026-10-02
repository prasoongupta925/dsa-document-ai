// Confirming the needs-review checklist items of a verdict
// (POST / DELETE /projects/{id}/file-check/confirmations in
// packages/backend/app/routers/file_check.py). Nothing is decided here: after
// a confirm or an undo the check runs again and the engine shows the item as
// CONFIRMED (it counts as met) or REVIEW.
import { useCallback, useState } from 'react';
import type {
  FileCheckApplicant,
  FileCheckItemRow,
} from '../../types/fileCheck';
import { normalizeStatus } from '../../lib/fileCheck';

/** Who confirmed a needs-review item, and when (ISO timestamp, UTC). */
export interface ItemConfirmation {
  confirmed_by?: string | null;
  confirmed_at?: string | null;
}

/**
 * A checklist row with the engine's confirmation fields: status CONFIRMED (a
 * manual item a person confirmed; it counts as met) and who / when.
 */
export type ConfirmableItemRow = Omit<FileCheckItemRow, 'status'> & {
  status: FileCheckItemRow['status'] | 'CONFIRMED';
  /** CONFIRMED rows: who and when. */
  confirmation?: ItemConfirmation | null;
  /** REVIEW rows confirmed before a document they were made on left the file. */
  stale_confirmation?: ItemConfirmation | null;
};

type ApplicantRef = Pick<FileCheckApplicant, 'applicant' | 'pan'>;

export function isConfirmedItem(row: { status: unknown }): boolean {
  return normalizeStatus(row.status) === 'CONFIRMED';
}

/** A REVIEW (manual) item: the only kind a person can confirm. */
export function canConfirmItem(row: { status: unknown }): boolean {
  const status = normalizeStatus(row.status);
  return status === 'REVIEW' || status === 'NEEDS REVIEW';
}

/**
 * The applicant as the confirmation is stored under: the verdict's PAN when
 * it has one, else its name (the engine keys an applicant by both).
 */
export function confirmationApplicant(applicant: ApplicantRef): string {
  return applicant.pan?.trim() || applicant.applicant;
}

/** The applicant's documents in the verdict: the confirmation holds while they all stay in the file. */
export function confirmationDocumentIds(
  applicant: Pick<FileCheckApplicant, 'documents'>,
): string[] {
  const ids = (applicant.documents ?? [])
    .map((d) => d.document_id?.trim())
    .filter((id): id is string => !!id);
  return Array.from(new Set(ids));
}

export function confirmRequestBody(
  applicant: FileCheckApplicant,
  row: Pick<FileCheckItemRow, 'item_id'>,
  checklistId?: string | null,
): {
  applicant: string;
  item_id: string;
  checklist_id?: string;
  document_ids: string[];
} {
  return {
    applicant: confirmationApplicant(applicant),
    item_id: row.item_id,
    ...(checklistId ? { checklist_id: checklistId } : {}),
    document_ids: confirmationDocumentIds(applicant),
  };
}

/**
 * Undo by the PAN and the name: the engine applies a confirmation saved under
 * either (one saved by name before a PAN was read still counts).
 */
export function undoRequestBody(
  applicant: ApplicantRef,
  row: Pick<FileCheckItemRow, 'item_id'>,
): { applicant: string; applicant_name?: string; item_id: string } {
  const key = confirmationApplicant(applicant);
  const name = applicant.applicant?.trim();
  return {
    applicant: key,
    ...(name && name !== key ? { applicant_name: name } : {}),
    item_id: row.item_id,
  };
}

/** One row of one applicant (a confirm or undo is in flight for it). */
export function confirmationRowKey(
  applicant: ApplicantRef,
  row: Pick<FileCheckItemRow, 'item_id'>,
): string {
  return `${confirmationApplicant(applicant)}|${row.item_id}`;
}

/** '2026-10-01T08:35:00+00:00' -> '1 Oct 2026, 2:05 pm' in the viewer's time zone; null if unreadable. */
export function formatConfirmedAt(
  iso: string | null | undefined,
  timeZone?: string,
): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString([], {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    ...(timeZone ? { timeZone } : {}),
  });
}

interface UseItemConfirmationsOptions {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  /** The verdict's project (null before a check has run). */
  projectId: string | null | undefined;
  /** Checklist of the shown verdict, kept with the confirmation. */
  checklistId?: string | null;
  /** Runs the check again, so the verdict shows the change. */
  onChanged: () => void | Promise<void>;
}

export interface ItemConfirmationsState {
  /** Row (confirmationRowKey) whose confirm / undo is in flight. */
  busyKey: string | null;
  /** The last failed request and its row. */
  error: { key: string; error: unknown } | null;
  confirm: (
    applicant: FileCheckApplicant,
    row: Pick<FileCheckItemRow, 'item_id'>,
  ) => Promise<boolean>;
  undo: (
    applicant: FileCheckApplicant,
    row: Pick<FileCheckItemRow, 'item_id'>,
  ) => Promise<boolean>;
}

/** Confirm / undo one needs-review item at a time, then run the check again. */
export function useItemConfirmations({
  fetchApi,
  projectId,
  checklistId,
  onChanged,
}: UseItemConfirmationsOptions): ItemConfirmationsState {
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<{ key: string; error: unknown } | null>(
    null,
  );

  const send = useCallback(
    async (
      method: 'POST' | 'DELETE',
      key: string,
      body: Record<string, unknown>,
    ): Promise<boolean> => {
      if (!projectId) return false;
      setBusyKey(key);
      setError(null);
      try {
        await fetchApi<unknown>(
          `projects/${projectId}/file-check/confirmations`,
          {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          },
        );
      } catch (err) {
        console.error('File check confirmation failed:', err);
        setError({ key, error: err });
        setBusyKey(null);
        return false;
      }
      try {
        await onChanged();
      } finally {
        setBusyKey(null);
      }
      return true;
    },
    [fetchApi, projectId, onChanged],
  );

  const confirm = useCallback(
    (applicant: FileCheckApplicant, row: Pick<FileCheckItemRow, 'item_id'>) =>
      send(
        'POST',
        confirmationRowKey(applicant, row),
        confirmRequestBody(applicant, row, checklistId),
      ),
    [send, checklistId],
  );

  const undo = useCallback(
    (applicant: FileCheckApplicant, row: Pick<FileCheckItemRow, 'item_id'>) =>
      send(
        'DELETE',
        confirmationRowKey(applicant, row),
        undoRequestBody(applicant, row),
      ),
    [send],
  );

  return { busyKey, error, confirm, undo };
}
