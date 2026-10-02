import { useState, useCallback, useEffect, useRef } from 'react';
import type {
  ApplicantEraseResponse,
  FileCheckApplicant,
  FileCheckChecklistSummary,
  FileCheckRequest,
  FileCheckResult,
} from '../types/fileCheck';
import {
  eraseMayBeIncomplete,
  eraseRequestBody,
  normalizeChecklists,
  parseEraseResponse,
  pickDefaultChecklistId,
} from '../lib/fileCheck';

interface UseFileCheckOptions {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  projectId: string;
  /**
   * After an applicant's data was erased (e.g. reload the documents list);
   * null when the erase failed without an answer and may have run in part.
   */
  onApplicantErased?: (response: ApplicantEraseResponse | null) => void;
}

/** The applicant of the verdict to erase: name, PAN and the documents shown. */
export type EraseTarget = Pick<
  FileCheckApplicant,
  'applicant' | 'pan' | 'documents'
>;

export type EraseOutcome =
  | { kind: 'erased'; response: ApplicantEraseResponse }
  /** Refused before anything was deleted; eraseError says why. */
  | { kind: 'failed' }
  /**
   * No usable answer (connection lost, gateway timeout, server error): the
   * erase may have run in part. The verdict was cleared; eraseError says so.
   */
  | { kind: 'uncertain' }
  /** A newer erase or a project switch replaced this one. */
  | { kind: 'ignored' };

/**
 * State for the File Check panel. The verdict is whatever
 * POST /projects/{id}/file-check returns; nothing is computed here.
 */
export function useFileCheck({
  fetchApi,
  projectId,
  onApplicantErased,
}: UseFileCheckOptions) {
  const [checklists, setChecklists] = useState<FileCheckChecklistSummary[]>([]);
  const [checklistsLoaded, setChecklistsLoaded] = useState(false);
  const [checklistsLoading, setChecklistsLoading] = useState(false);
  const [checklistsError, setChecklistsError] = useState<unknown>(null);
  const [checklistId, setChecklistId] = useState('');
  const [applicant, setApplicant] = useState('');
  // Applicant names seen in the last run without an applicant filter; they
  // feed the applicant dropdown.
  const [knownApplicants, setKnownApplicants] = useState<string[]>([]);
  const [result, setResult] = useState<FileCheckResult | null>(null);
  const [resultApplicant, setResultApplicant] = useState('');
  const [lastRunAt, setLastRunAt] = useState<Date | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Erase applicant (POST .../applicants/erase)
  const [erasing, setErasing] = useState(false);
  const [eraseError, setEraseError] = useState<unknown>(null);
  const [eraseResult, setEraseResult] = useState<ApplicantEraseResponse | null>(
    null,
  );

  // Ignore responses that arrive after a newer request or a project switch.
  const checklistsSeq = useRef(0);
  const runSeq = useRef(0);
  const eraseSeq = useRef(0);
  const projectRef = useRef(projectId);
  const onErasedRef = useRef(onApplicantErased);
  onErasedRef.current = onApplicantErased;

  // The project page is reused across projects: start clean on a switch.
  useEffect(() => {
    if (projectRef.current === projectId) return;
    projectRef.current = projectId;
    checklistsSeq.current += 1;
    runSeq.current += 1;
    eraseSeq.current += 1;
    setErasing(false);
    setEraseError(null);
    setEraseResult(null);
    setChecklists([]);
    setChecklistsLoaded(false);
    setChecklistsLoading(false);
    setChecklistsError(null);
    setChecklistId('');
    setApplicant('');
    setKnownApplicants([]);
    setResult(null);
    setResultApplicant('');
    setLastRunAt(null);
    setRunning(false);
    setError(null);
  }, [projectId]);

  const loadChecklists = useCallback(async () => {
    const seq = ++checklistsSeq.current;
    setChecklistsLoading(true);
    setChecklistsError(null);
    try {
      const raw = await fetchApi<unknown>(`projects/${projectId}/checklists`);
      if (seq !== checklistsSeq.current) return;
      const { checklists: list, defaultId } = normalizeChecklists(raw);
      setChecklists(list);
      setChecklistId((current) =>
        current && list.some((c) => c.id === current)
          ? current
          : pickDefaultChecklistId(list, defaultId),
      );
      setChecklistsLoaded(true);
    } catch (err) {
      if (seq !== checklistsSeq.current) return;
      console.error('Failed to load file-check checklists:', err);
      setChecklistsError(err);
    } finally {
      if (seq === checklistsSeq.current) setChecklistsLoading(false);
    }
  }, [fetchApi, projectId]);

  const runCheck = useCallback(async () => {
    const seq = ++runSeq.current;
    const wantedApplicant = applicant.trim();
    const body: FileCheckRequest = {};
    if (checklistId) body.checklist_id = checklistId;
    if (wantedApplicant) body.applicant = wantedApplicant;
    setRunning(true);
    setError(null);
    // A new verdict replaces the "data was erased" note.
    setEraseResult(null);
    try {
      const raw = await fetchApi<unknown>(`projects/${projectId}/file-check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (seq !== runSeq.current) return;
      const obj =
        raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
      if (typeof obj.overall_verdict !== 'string') {
        throw new Error(
          typeof obj.error === 'string'
            ? obj.error
            : 'unexpected response from the file-check service',
        );
      }
      const data = obj as unknown as FileCheckResult;
      setResult(data);
      setResultApplicant(wantedApplicant);
      setLastRunAt(new Date());
      if (!wantedApplicant) {
        setKnownApplicants(
          Array.from(
            new Set(
              (data.applicants ?? [])
                .map((a) => a.applicant)
                .filter((name): name is string => !!name),
            ),
          ),
        );
      }
    } catch (err) {
      if (seq !== runSeq.current) return;
      console.error('File check failed:', err);
      setError(err);
    } finally {
      if (seq === runSeq.current) setRunning(false);
    }
  }, [fetchApi, projectId, checklistId, applicant]);

  /**
   * Permanently erases one applicant's documents and derived data
   * (eraseRequestBody: PAN first, the typed name, the documents the verdict
   * shows). On success the verdict is cleared: it lists erased data. So it is
   * when the answer is lost, since the erase may have run in part; the
   * documents list is reloaded then too (onApplicantErased(null)).
   */
  const eraseApplicant = useCallback(
    async (target: EraseTarget, confirm: string): Promise<EraseOutcome> => {
      const seq = ++eraseSeq.current;
      const name = target.applicant;
      const pan = target.pan?.trim();
      const body = eraseRequestBody(target, confirm);
      // The verdict (and any check still running) may describe erased data.
      const clearVerdict = () => {
        runSeq.current += 1;
        setRunning(false);
        setResult(null);
        setResultApplicant('');
        setLastRunAt(null);
        setError(null);
        setKnownApplicants((prev) => prev.filter((a) => a !== name));
        setApplicant((current) =>
          current.trim() === name || (!!pan && current.trim() === pan)
            ? ''
            : current,
        );
      };
      setErasing(true);
      setEraseError(null);
      try {
        const raw = await fetchApi<unknown>(
          `projects/${projectId}/applicants/erase`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          },
        );
        if (seq !== eraseSeq.current) return { kind: 'ignored' };
        const response = parseEraseResponse(raw, name);
        clearVerdict();
        setEraseResult(response);
        onErasedRef.current?.(response);
        return { kind: 'erased', response };
      } catch (err) {
        if (seq !== eraseSeq.current) return { kind: 'ignored' };
        console.error('Erase applicant failed:', err);
        setEraseError(err);
        if (!eraseMayBeIncomplete(err)) return { kind: 'failed' };
        clearVerdict();
        onErasedRef.current?.(null);
        return { kind: 'uncertain' };
      } finally {
        if (seq === eraseSeq.current) setErasing(false);
      }
    },
    [fetchApi, projectId],
  );

  const clearEraseError = useCallback(() => setEraseError(null), []);
  const dismissEraseResult = useCallback(() => setEraseResult(null), []);

  return {
    checklists,
    checklistsLoaded,
    checklistsLoading,
    checklistsError,
    loadChecklists,
    checklistId,
    setChecklistId,
    applicant,
    setApplicant,
    knownApplicants,
    result,
    resultApplicant,
    lastRunAt,
    running,
    error,
    runCheck,
    erasing,
    eraseError,
    eraseResult,
    eraseApplicant,
    clearEraseError,
    dismissEraseResult,
  };
}

export type FileCheckState = ReturnType<typeof useFileCheck>;
