import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  FileCheckAskPricing,
  FileCheckAskRequest,
  FileCheckUsage,
} from '../types/fileCheck';
import {
  ASK_DEFAULT_PRICING,
  ASK_MAX_QUESTION_LENGTH,
  ASK_MAX_THREAD,
  addToSession,
  buildAskHistory,
  parseAskResponse,
  parseUsage,
  type AskSessionTotals,
  type AskTurn,
} from '../lib/fileCheckAsk';

interface UseFileCheckAskOptions {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  projectId: string;
}

export interface AskScope {
  checklistId?: string;
  applicant?: string;
}

const EMPTY_SESSION: AskSessionTotals = {
  calls: 0,
  input_tokens: 0,
  output_tokens: 0,
};

/**
 * State for "Ask about this file". The answer, tokens and cost come from
 * POST /projects/{id}/file-check/ask; the 7-day spend from GET .../usage.
 * The deterministic verdict (useFileCheck) is separate and never changed here.
 */
export function useFileCheckAsk({
  fetchApi,
  projectId,
}: UseFileCheckAskOptions) {
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const [pending, setPending] = useState(false);
  // Every answered call of this browser session, also those the thread dropped.
  const [session, setSession] = useState<AskSessionTotals>(EMPTY_SESSION);
  const [pricing, setPricing] =
    useState<FileCheckAskPricing>(ASK_DEFAULT_PRICING);
  const [modelId, setModelId] = useState<string | null>(null);
  const [usage, setUsage] = useState<FileCheckUsage | null>(null);
  const [usageError, setUsageError] = useState<unknown>(null);

  const turnSeq = useRef(0);
  const askSeq = useRef(0);
  const usageSeq = useRef(0);
  const projectRef = useRef(projectId);

  // The project page is reused across projects: start clean on a switch.
  useEffect(() => {
    if (projectRef.current === projectId) return;
    projectRef.current = projectId;
    askSeq.current += 1;
    usageSeq.current += 1;
    setTurns([]);
    setPending(false);
    setSession(EMPTY_SESSION);
    setPricing(ASK_DEFAULT_PRICING);
    setModelId(null);
    setUsage(null);
    setUsageError(null);
  }, [projectId]);

  const loadUsage = useCallback(async () => {
    const seq = ++usageSeq.current;
    try {
      const raw = await fetchApi<unknown>(
        `projects/${projectId}/file-check/usage`,
      );
      if (seq !== usageSeq.current) return;
      const parsed = parseUsage(raw);
      if (!parsed) throw new Error('unexpected response from the usage API');
      setUsage(parsed);
      setUsageError(null);
    } catch (err) {
      if (seq !== usageSeq.current) return;
      console.error('Failed to load file-check ask usage:', err);
      setUsageError(err);
    }
  }, [fetchApi, projectId]);

  const ask = useCallback(
    async (rawQuestion: string, scope: AskScope = {}) => {
      const question = rawQuestion.trim().slice(0, ASK_MAX_QUESTION_LENGTH);
      if (!question || pending) return;
      const seq = ++askSeq.current;
      const id = ++turnSeq.current;
      const body: FileCheckAskRequest = { question };
      if (scope.checklistId) body.checklist_id = scope.checklistId;
      const applicant = scope.applicant?.trim();
      if (applicant) body.applicant = applicant;
      const history = buildAskHistory(turns);
      if (history.length > 0) body.history = history;

      setPending(true);
      setTurns((prev) =>
        [...prev, { id, question, status: 'pending' as const }].slice(
          -ASK_MAX_THREAD,
        ),
      );
      try {
        const raw = await fetchApi<unknown>(
          `projects/${projectId}/file-check/ask`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          },
        );
        if (seq !== askSeq.current) return;
        const res = parseAskResponse(raw);
        setTurns((prev) =>
          prev.map((t) =>
            t.id === id
              ? {
                  ...t,
                  status: 'done',
                  answer: res.answer,
                  input_tokens: res.input_tokens,
                  output_tokens: res.output_tokens,
                  cost_usd: res.cost_usd,
                  model_id: res.model_id,
                  grounded_on: res.grounded_on,
                }
              : t,
          ),
        );
        setSession((s) => addToSession(s, res.input_tokens, res.output_tokens));
        setPricing(res.pricing);
        if (res.model_id) setModelId(res.model_id);
        loadUsage();
      } catch (err) {
        if (seq !== askSeq.current) return;
        console.error('File check ask failed:', err);
        setTurns((prev) =>
          prev.map((t) =>
            t.id === id ? { ...t, status: 'error', error: err } : t,
          ),
        );
      } finally {
        if (seq === askSeq.current) setPending(false);
      }
    },
    [fetchApi, projectId, pending, turns, loadUsage],
  );

  /** Clears the thread (the session meter keeps counting). */
  const clear = useCallback(() => {
    askSeq.current += 1;
    setTurns([]);
    setPending(false);
  }, []);

  return {
    turns,
    pending,
    ask,
    clear,
    session,
    pricing,
    modelId,
    usage,
    usageError,
    loadUsage,
  };
}

export type FileCheckAskState = ReturnType<typeof useFileCheckAsk>;
