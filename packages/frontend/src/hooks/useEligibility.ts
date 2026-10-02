import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  CompanyCheck,
  DocumentRows,
  EligibilityInputs,
  EligibilityLoginResponse,
  EligibilityPrefill,
  EligibilityResult,
  FieldSources,
  LendersResponse,
  PincodeCheck,
  PrefillField,
} from '../types/eligibility';
import { apiErrorStatus } from '../lib/fileCheck';
import {
  calculateRequestBody,
  emptyInputs,
  expiresAtIso,
  fieldValue,
  inputsKey,
  inputsRequestBody,
  parseCalculateResponse,
  parseCompanyCheck,
  parseInputsResponse,
  parseLenders,
  parseLoginResponse,
  parsePincodeCheck,
} from '../lib/eligibility';

interface UseEligibilityOptions {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  projectId: string;
}

export interface LenderLoginState {
  sending: boolean;
  error: unknown;
  response: EligibilityLoginResponse | null;
  at: Date | null;
}

/**
 * What the documents give the form: per field its value (the field shows its
 * source while it holds that value) and the files it was read from, and the
 * documents' rows (other income, loans), in the inputs or not.
 */
export interface PrefillValues {
  fields?: FieldSources;
  rows?: DocumentRows;
}

/** One applicant's eligibility inputs and results, as edited in this tab. */
export interface EligibilityDraft {
  /** What the API calls the applicant: the PAN when known, else the name. */
  applicant: string;
  loading: boolean;
  loaded: boolean;
  loadError: unknown;
  inputs: EligibilityInputs;
  prefillValues: PrefillValues;
  /** How the draft was pre-filled (null for saved inputs). */
  prefill: EligibilityPrefill | null;
  /** The API's notes on the draft (e.g. bank EMIs added as tradelines). */
  notes: string[];
  /** inputsKey of the inputs last loaded or saved: unsaved when it differs. */
  savedKey: string | null;
  /** The API holds saved inputs (a login needs them). */
  saved: boolean;
  saving: boolean;
  saveError: unknown;
  savedAt: Date | null;
  /** When the saved inputs are deleted (ISO). */
  expiresAt: string | null;
  calculating: boolean;
  calcError: unknown;
  result: EligibilityResult | null;
  /** inputsKey of the inputs the result was calculated with. */
  resultKey: string | null;
  calculatedAt: Date | null;
  /** Logins made from this tab, by lender name. */
  logins: Record<string, LenderLoginState>;
}

export function newDraft(applicant: string): EligibilityDraft {
  return {
    applicant,
    loading: false,
    loaded: false,
    loadError: null,
    inputs: emptyInputs(),
    prefillValues: {},
    prefill: null,
    notes: [],
    savedKey: null,
    saved: false,
    saving: false,
    saveError: null,
    savedAt: null,
    expiresAt: null,
    calculating: false,
    calcError: null,
    result: null,
    resultKey: null,
    calculatedAt: null,
    logins: {},
  };
}

/**
 * What the documents give the form: the API's `sources` and `document_rows`,
 * else (an API without them) the values the draft took for the fields it
 * names, without their files.
 */
export function prefillValuesOf(
  inputs: EligibilityInputs,
  fromDocuments: PrefillField[],
  sources: FieldSources = {},
  rows: DocumentRows = { other_income: [], tradelines: [] },
): PrefillValues {
  const fields: FieldSources = { ...sources };
  for (const field of fromDocuments) {
    if (field === 'other_income' || field === 'tradelines' || fields[field]) {
      continue;
    }
    const value = fieldValue(inputs, field);
    if (typeof value === 'string' ? value === '' : typeof value !== 'number') {
      continue;
    }
    fields[field] = {
      source: 'document',
      value,
      documents: [],
      detail: null,
      unverified: false,
    };
  }
  return { fields, rows };
}

/**
 * A login with this lender is sent with confirm_not_ready: the result on
 * screen is NOT READY, or the lender's last login was refused as NOT READY
 * (428). The Lenders tab sends either only after the user confirmed the open
 * issues it names.
 */
export function confirmsNotReady(
  draft: EligibilityDraft | undefined,
  lender: string,
): boolean {
  return (
    draft?.result?.file_check?.ready === false ||
    apiErrorStatus(draft?.logins[lender]?.error) === 428
  );
}

// Request sequence numbers: an answer is used only while its number is the
// latest for its key (a newer request, an erase or a project switch bumps it).
type Seqs = Map<string, number>;

const SEP = '\u001f';

function seqKey(...parts: string[]): string {
  return parts.join(SEP);
}

function nextSeq(seqs: Seqs, key: string): number {
  const n = (seqs.get(key) ?? 0) + 1;
  seqs.set(key, n);
  return n;
}

function isCurrent(seqs: Seqs, key: string, n: number): boolean {
  return seqs.get(key) === n;
}

const NO_LOGIN: LenderLoginState = {
  sending: false,
  error: null,
  response: null,
  at: null,
};

export type LoginOutcome =
  | { kind: 'logged_in'; response: EligibilityLoginResponse }
  | { kind: 'failed' }
  | { kind: 'ignored' };

type SaveOutcome = { ok: true } | { ok: false; error: unknown };

/**
 * State for the Eligibility & lenders panel, one draft per applicant. The
 * eligibility is whatever POST .../eligibility/calculate returns; nothing is
 * computed here. Requests take the applicant and the inputs explicitly.
 */
export function useEligibility({ fetchApi, projectId }: UseEligibilityOptions) {
  const [lenders, setLenders] = useState<LendersResponse | null>(null);
  const [lendersLoading, setLendersLoading] = useState(false);
  const [lendersError, setLendersError] = useState<unknown>(null);
  const [drafts, setDrafts] = useState<Record<string, EligibilityDraft>>({});
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  const seqs = useRef<Seqs>(new Map());
  const projectRef = useRef(projectId);

  // The project page is reused across projects: start clean on a switch.
  useEffect(() => {
    if (projectRef.current === projectId) return;
    projectRef.current = projectId;
    for (const key of seqs.current.keys()) nextSeq(seqs.current, key);
    setLenders(null);
    setLendersLoading(false);
    setLendersError(null);
    setDrafts({});
  }, [projectId]);

  const base = `projects/${projectId}/eligibility`;

  const patchDraft = useCallback(
    (
      applicant: string,
      patch:
        | Partial<EligibilityDraft>
        | ((draft: EligibilityDraft) => Partial<EligibilityDraft>),
    ) => {
      setDrafts((prev) => {
        const current = prev[applicant] ?? newDraft(applicant);
        const next = typeof patch === 'function' ? patch(current) : patch;
        return { ...prev, [applicant]: { ...current, ...next } };
      });
    },
    [],
  );

  /** GET .../lenders: the SAMPLE policies (the Lenders tab shows them). */
  const loadLenders = useCallback(async () => {
    const n = nextSeq(seqs.current, 'lenders');
    const current = () => isCurrent(seqs.current, 'lenders', n);
    setLendersLoading(true);
    setLendersError(null);
    try {
      const raw = await fetchApi<unknown>(`${base}/lenders`);
      if (!current()) return;
      setLenders(parseLenders(raw));
    } catch (err) {
      if (!current()) return;
      console.error('Failed to load the lender policies:', err);
      setLendersError(err);
    } finally {
      if (current()) setLendersLoading(false);
    }
  }, [fetchApi, base]);

  /**
   * GET .../inputs: the saved inputs, else a draft pre-filled from the
   * documents. A draft already loaded in this tab is kept unless `force`.
   */
  const load = useCallback(
    async (applicant: string, force = false) => {
      const existing = draftsRef.current[applicant];
      if (!force && existing && (existing.loaded || existing.loading)) return;
      const key = seqKey('load', applicant);
      const n = nextSeq(seqs.current, key);
      const current = () => isCurrent(seqs.current, key, n);
      patchDraft(applicant, { loading: true, loadError: null });
      try {
        const raw = await fetchApi<unknown>(
          `${base}/inputs?applicant=${encodeURIComponent(applicant)}`,
        );
        if (!current()) return;
        const parsed = parseInputsResponse(raw, applicant);
        patchDraft(applicant, {
          loading: false,
          loaded: true,
          inputs: parsed.inputs,
          prefillValues: prefillValuesOf(
            parsed.inputs,
            parsed.fromDocuments,
            parsed.sources,
            parsed.documentRows,
          ),
          prefill: parsed.prefill,
          notes: parsed.notes,
          savedKey: inputsKey(parsed.inputs),
          saved: parsed.saved,
          expiresAt: parsed.expiresAt,
          saveError: null,
          calcError: null,
          result: null,
          resultKey: null,
          calculatedAt: null,
        });
      } catch (err) {
        if (!current()) return;
        console.error('Failed to load the eligibility inputs:', err);
        patchDraft(applicant, { loading: false, loadError: err });
      }
    },
    [fetchApi, base, patchDraft],
  );

  /** Edits the applicant's inputs on screen (saved by save()). */
  const edit = useCallback(
    (
      applicant: string,
      update: (inputs: EligibilityInputs) => EligibilityInputs,
    ) => patchDraft(applicant, (d) => ({ inputs: update(d.inputs) })),
    [patchDraft],
  );

  const saveInputs = useCallback(
    async (
      applicant: string,
      inputs: EligibilityInputs,
    ): Promise<SaveOutcome | null> => {
      const key = seqKey('save', applicant);
      const n = nextSeq(seqs.current, key);
      const current = () => isCurrent(seqs.current, key, n);
      const savedKey = inputsKey(inputs);
      patchDraft(applicant, { saving: true, saveError: null });
      try {
        const raw = await fetchApi<unknown>(`${base}/inputs`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(inputsRequestBody(applicant, inputs)),
        });
        if (!current()) return null;
        const expires =
          raw && typeof raw === 'object'
            ? expiresAtIso((raw as Record<string, unknown>).expires_at)
            : null;
        patchDraft(applicant, (d) => ({
          saving: false,
          saved: true,
          savedKey,
          savedAt: new Date(),
          expiresAt: expires ?? d.expiresAt,
        }));
        return { ok: true };
      } catch (err) {
        if (!current()) return null;
        console.error('Failed to save the eligibility inputs:', err);
        patchDraft(applicant, { saving: false, saveError: err });
        return { ok: false, error: err };
      }
    },
    [fetchApi, base, patchDraft],
  );

  /** PUT .../inputs; true when saved. */
  const save = useCallback(
    async (applicant: string, inputs: EligibilityInputs): Promise<boolean> =>
      (await saveInputs(applicant, inputs))?.ok === true,
    [saveInputs],
  );

  /** POST .../calculate with the inputs on screen (nothing is saved). */
  const calculate = useCallback(
    async (
      applicant: string,
      inputs: EligibilityInputs,
    ): Promise<EligibilityResult | null> => {
      const key = seqKey('calc', applicant);
      const n = nextSeq(seqs.current, key);
      const current = () => isCurrent(seqs.current, key, n);
      const resultKey = inputsKey(inputs);
      patchDraft(applicant, { calculating: true, calcError: null });
      try {
        const raw = await fetchApi<unknown>(`${base}/calculate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(calculateRequestBody(applicant, inputs)),
        });
        if (!current()) return null;
        const result = parseCalculateResponse(raw, applicant);
        patchDraft(applicant, {
          calculating: false,
          result,
          resultKey,
          calculatedAt: new Date(),
        });
        return result;
      } catch (err) {
        if (!current()) return null;
        console.error('Eligibility calculation failed:', err);
        patchDraft(applicant, { calculating: false, calcError: err });
        return null;
      }
    },
    [fetchApi, base, patchDraft],
  );

  /**
   * POST .../login {applicant, lender}. The backend recomputes from the SAVED
   * inputs, so `unsavedInputs` (the inputs on screen when they are not saved)
   * are saved first. `lender` is the lender id (or name). The backend refuses
   * a NOT READY file (428) unless confirm_not_ready is sent: it is sent when
   * the result on screen is NOT READY or this lender's last login was refused
   * as NOT READY, because the Lenders tab logs such a file in only after the
   * user confirmed its open issues; `confirmNotReady` overrides that.
   */
  const login = useCallback(
    async (
      applicant: string,
      lender: { id: string | null; name: string },
      unsavedInputs?: EligibilityInputs,
      confirmNotReady?: boolean,
    ): Promise<LoginOutcome> => {
      const confirm =
        confirmNotReady ??
        confirmsNotReady(draftsRef.current[applicant], lender.name);
      const key = seqKey('login', applicant, lender.name);
      const n = nextSeq(seqs.current, key);
      const current = () => isCurrent(seqs.current, key, n);
      const setLogin = (state: Partial<LenderLoginState>) =>
        patchDraft(applicant, (d) => ({
          logins: {
            ...d.logins,
            [lender.name]: { ...(d.logins[lender.name] ?? NO_LOGIN), ...state },
          },
        }));
      setLogin({ sending: true, error: null, response: null, at: null });
      if (unsavedInputs) {
        const saved = await saveInputs(applicant, unsavedInputs);
        if (!current() || saved === null) return { kind: 'ignored' };
        if (!saved.ok) {
          setLogin({ sending: false, error: saved.error });
          return { kind: 'failed' };
        }
      }
      try {
        const raw = await fetchApi<unknown>(`${base}/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            applicant,
            lender: lender.id || lender.name,
            ...(confirm ? { confirm_not_ready: true } : {}),
          }),
        });
        if (!current()) return { kind: 'ignored' };
        const response = parseLoginResponse(raw);
        setLogin({ sending: false, response, at: new Date() });
        return { kind: 'logged_in', response };
      } catch (err) {
        if (!current()) return { kind: 'ignored' };
        console.error('Login with the lender failed:', err);
        setLogin({ sending: false, error: err });
        return { kind: 'failed' };
      }
    },
    [fetchApi, base, patchDraft, saveInputs],
  );

  /** GET .../pincodes/{pincode}: which lenders serve it (SAMPLE lists). */
  const checkPincode = useCallback(
    async (pincode: string): Promise<PincodeCheck> => {
      const value = pincode.trim();
      const raw = await fetchApi<unknown>(
        `${base}/pincodes/${encodeURIComponent(value)}`,
      );
      return parsePincodeCheck(raw, value);
    },
    [fetchApi, base],
  );

  /** GET .../companies?name=: the company's category per lender (SAMPLE lists). */
  const checkCompany = useCallback(
    async (name: string): Promise<CompanyCheck> => {
      const value = name.trim();
      const raw = await fetchApi<unknown>(
        `${base}/companies?name=${encodeURIComponent(value)}`,
      );
      return parseCompanyCheck(raw, value);
    },
    [fetchApi, base],
  );

  /**
   * Drops drafts, e.g. after an applicant's data was erased; answers still
   * on the way are ignored. No names: every draft.
   */
  const forget = useCallback((applicants?: (string | null | undefined)[]) => {
    const names = (applicants ?? []).filter((a): a is string => !!a);
    const all = !applicants;
    for (const key of seqs.current.keys()) {
      const [, who] = key.split(SEP);
      if (key !== 'lenders' && (all || names.includes(who))) {
        nextSeq(seqs.current, key);
      }
    }
    setDrafts((prev) => {
      if (all) return {};
      if (!names.some((a) => a in prev)) return prev;
      const next = { ...prev };
      for (const a of names) delete next[a];
      return next;
    });
  }, []);

  return {
    lenders,
    lendersLoading,
    lendersError,
    loadLenders,
    drafts,
    load,
    edit,
    save,
    calculate,
    login,
    checkPincode,
    checkCompany,
    forget,
  };
}

export type EligibilityState = ReturnType<typeof useEligibility>;
