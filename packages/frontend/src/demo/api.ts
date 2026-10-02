// The public demo's API: answers every call the web app makes in the browser,
// from snapshots of the real app (captured from its API with synthetic data,
// brand-free). GETs return what the real app returned; the file check, "Ask
// about this file", the lender check and the chat agents replay real answers;
// anything that would change data is refused with a friendly read-only note.
import { ApiError } from '../lib/apiError';
import {
  calculateRequestBody,
  parseInputsResponse,
} from '../lib/eligibility';
import type { ContentBlock, StreamEvent } from '../hooks/useAwsClient';
import {
  DEMO_USERNAME,
  DemoReadOnlyError,
  notifyReadOnly,
  READ_ONLY_MESSAGE,
} from './mode';

interface DemoProjectMeta {
  name: string;
  kind: 'loanFile' | 'calls';
  applicants: { name: string; pan: string | null }[];
  defaultChecklist?: string | null;
}

interface DemoAnswer {
  projectId: string;
  agentId: string | null;
  prompt: string;
  shared: boolean;
  events: StreamEvent[];
}

export interface DemoData {
  meta: { captured_at: string; projects: Record<string, DemoProjectMeta> };
  get: Record<string, unknown>;
  /** "<projectId>|<checklistId>" -> file-check verdict */
  fileCheck: Record<string, unknown>;
  /** "<projectId>|<checklistId>|<question>" -> ask answer */
  ask: Record<string, unknown>;
  /** "<projectId>|<applicant id>" -> calculate result */
  calculate: Record<string, unknown>;
  chat: DemoAnswer[];
}

let dataPromise: Promise<DemoData> | null = null;

/** The snapshot (a separate chunk, loaded once). */
export function loadDemoData(): Promise<DemoData> {
  // The literal env check lets a normal build drop the snapshot chunk entirely.
  dataPromise ??=
    import.meta.env.VITE_PUBLIC_DEMO === '1'
      ? import('./data/demo-data.json').then(
          (m) => (m as { default: unknown }).default as DemoData,
        )
      : Promise.reject(new Error('Not a public demo build.'));
  return dataPromise;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const jitter = (base: number, spread: number) =>
  base + Math.round(Math.random() * spread);
const clone = <T>(value: T): T =>
  value === undefined ? value : (JSON.parse(JSON.stringify(value)) as T);

function safeDecode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/** Decoded path + sorted decoded query: the key the capture used. */
export function normPath(path: string): string {
  const p = path.replace(/^\/+/, '');
  const q = p.indexOf('?');
  const base = (q < 0 ? p : p.slice(0, q))
    .split('/')
    .map(safeDecode)
    .join('/');
  if (q < 0) return base;
  const pairs = [...new URLSearchParams(p.slice(q + 1)).entries()].sort(
    ([a, av], [b, bv]) => (a < b ? -1 : a > b ? 1 : av < bv ? -1 : av > bv ? 1 : 0),
  );
  return `${base}?${pairs.map(([k, v]) => `${k}=${v}`).join('&')}`;
}

/** Lower case, single spaces, no trailing punctuation. */
export function normText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[‘’]/g, "'")
    .replace(/\s+/g, ' ')
    .replace(/[\s.?!:;,]+$/u, '')
    .trim();
}

function words(text: string): Set<string> {
  return new Set(
    normText(text)
      .replace(/[^\p{L}\p{N}₹ ]+/gu, ' ')
      .split(' ')
      .filter((w) => w.length > 1),
  );
}

/** Share of words two texts have in common (Jaccard). */
function similarity(a: string, b: string): number {
  const x = words(a);
  const y = words(b);
  if (x.size === 0 || y.size === 0) return 0;
  let common = 0;
  for (const w of x) if (y.has(w)) common += 1;
  return common / (x.size + y.size - common);
}

function bodyOf(init?: RequestInit): Record<string, unknown> {
  if (!init?.body || typeof init.body !== 'string') return {};
  try {
    const parsed: unknown = JSON.parse(init.body);
    return parsed && typeof parsed === 'object'
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/** Key-sorted JSON, so two equal objects compare equal. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    return `{${Object.keys(o)
      .filter((k) => o[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(o[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value ?? null);
}

function readOnly(message: string = READ_ONLY_MESSAGE): never {
  notifyReadOnly(message);
  throw new DemoReadOnlyError(message);
}

function project(data: DemoData, projectId: string): DemoProjectMeta | null {
  return data.meta.projects[projectId] ?? null;
}

/** The project's applicant for a PAN or a name (case-insensitive). */
function applicantOf(data: DemoData, projectId: string, who: string) {
  const w = who.trim().toLowerCase();
  return (
    project(data, projectId)?.applicants.find(
      (a) => a.pan?.toLowerCase() === w || a.name.toLowerCase() === w,
    ) ?? null
  );
}

function defaultChecklist(data: DemoData, projectId: string): string | null {
  const meta = project(data, projectId);
  if (meta?.defaultChecklist) return meta.defaultChecklist;
  const lists = data.get[`projects/${projectId}/checklists`] as
    | { default_checklist?: string }
    | undefined;
  return lists?.default_checklist ?? null;
}

// ------------------------------------------------------------------ GET

function demoGet(data: DemoData, path: string): unknown {
  const key = normPath(path);
  if (key in data.get) return clone(data.get[key]);
  const [base, query = ''] = key.split('?');
  const params = new URLSearchParams(query);
  let m: RegExpMatchArray | null;

  if (/^chat\/projects\/[^/]+\/sessions$/.test(base)) {
    return { sessions: [], next_cursor: null };
  }
  if (/^chat\/projects\/[^/]+\/sessions\/[^/]+$/.test(base)) {
    throw new ApiError(404, 'No saved conversations in the read-only demo.');
  }
  if (base === 'artifacts') return { items: [], next_cursor: null };
  if ((m = base.match(/^projects\/([^/]+)\/agents\/([^/]+)$/))) {
    const agents = (data.get[`projects/${m[1]}/agents`] ?? []) as {
      agent_id: string;
      description?: string | null;
    }[];
    const agent = agents.find((a) => a.agent_id === m![2]);
    if (!agent) throw new ApiError(404, 'Agent not found.');
    return {
      ...clone(agent),
      content: `${agent.description ?? ''}\n\nThe agent's full instructions ship with the app; they are not shown in this read-only demo.`,
    };
  }
  if (base.startsWith('prompts/')) return { content: '' };
  if (base === 'sagemaker/status') {
    return {
      endpoint_name: 'PaddleOCR endpoint (not part of the demo)',
      status: 'NotDeployed',
      current_instance_count: 0,
      desired_instance_count: 0,
    };
  }
  if (base === 'sagemaker/settings') return { evaluation_periods: 15 };
  if ((m = base.match(/^projects\/([^/]+)\/eligibility\/inputs$/))) {
    const who = params.get('applicant') ?? '';
    const a = applicantOf(data, m[1], who);
    for (const id of a ? [a.pan, a.name] : []) {
      const hit = id && data.get[`${base}?applicant=${id}`];
      if (hit) return clone(hit);
    }
    throw new ApiError(
      404,
      'The demo has the CIBIL page of the file’s own applicant only.',
    );
  }
  if ((m = base.match(/^projects\/([^/]+)\/eligibility\/branches$/))) {
    // The capture asked for every lender: answer any subset from it.
    const pincode = params.get('pincode') ?? '';
    const wanted = (params.get('lenders') ?? '')
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
    const prefix = `${base}?`;
    const full = Object.entries(data.get)
      .filter(
        ([k]) => k.startsWith(prefix) && k.endsWith(`&pincode=${pincode}`),
      )
      .map(([, v]) => v as { lenders?: { lender: string }[] })
      .sort((a, b) => (b.lenders?.length ?? 0) - (a.lenders?.length ?? 0))[0];
    if (full) {
      const rows = full.lenders ?? [];
      return {
        ...clone(full),
        lenders: wanted
          .map((w) => rows.find((r) => r.lender.toLowerCase() === w))
          .filter(Boolean)
          .map(clone),
      };
    }
    throw new DemoReadOnlyError(
      'The demo has branch data for the applicants’ own pincodes only.',
    );
  }
  if (/^projects\/[^/]+\/eligibility\/(pincodes|companies)/.test(base)) {
    throw new DemoReadOnlyError(
      'The demo can check the applicant’s own pincode and company only.',
    );
  }
  if (/^projects\/[^/]+\/documents\/progress$/.test(base)) return {};
  if (/^projects\/[^/]+\/graph/.test(base)) {
    throw new ApiError(404, 'The knowledge graph is off in this edition.');
  }
  if (/\/download-url$|\/presigned/.test(base)) {
    throw new DemoReadOnlyError(
      'Downloading documents is not part of the read-only demo.',
    );
  }
  throw new ApiError(404, 'Not part of the read-only demo.');
}

// ------------------------------------------------------------------ POST

const ASK_FALLBACK =
  'This read-only demo replays the real answers to the four suggested questions above (recorded from the app on this synthetic file). In the full app, Ask answers any question about the file from its documents and check results, and says when something is not in the file.';

function demoAsk(data: DemoData, projectId: string, body: Record<string, unknown>) {
  const question = String(body.question ?? '');
  const checklist =
    (body.checklist_id as string | undefined) ??
    defaultChecklist(data, projectId) ??
    '';
  const q = normText(question);
  const exact = (cid: string) =>
    Object.entries(data.ask).find(
      ([k]) => k.startsWith(`${projectId}|${cid}|`) && normText(k.split('|').slice(2).join('|')) === q,
    )?.[1];
  const hit = exact(checklist) ?? exact(defaultChecklist(data, projectId) ?? '');
  if (hit) return clone(hit);
  // A near-identical question (typed by hand): the closest recorded one.
  const near = Object.entries(data.ask)
    .filter(([k]) => k.startsWith(`${projectId}|`))
    .map(([k, v]) => ({ v, s: similarity(k.split('|').slice(2).join('|'), question) }))
    .sort((a, b) => b.s - a.s)[0];
  if (near && near.s >= 0.75) return clone(near.v);
  const any = Object.entries(data.ask).find(([k]) => k.startsWith(`${projectId}|`))?.[1] as
    | Record<string, unknown>
    | undefined;
  return {
    answer: ASK_FALLBACK,
    model_id: any?.model_id ?? null,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    pricing: any?.pricing,
    grounded_on: [],
  };
}

/** The request without the contact fields no lender rule reads (name, mobile, addresses). */
function material(body: unknown): unknown {
  const copy = clone(body) as { inputs?: { profile?: Record<string, unknown> } };
  const profile = copy?.inputs?.profile;
  if (profile) {
    for (const k of ['name', 'mobile', 'current_address', 'permanent_address']) delete profile[k];
  }
  return copy;
}

function demoCalculate(data: DemoData, projectId: string, body: Record<string, unknown>) {
  const who = String(body.applicant ?? '');
  const a = applicantOf(data, projectId, who);
  if (!a) {
    throw new DemoReadOnlyError(
      'The demo has the lender check of the file’s own applicant only.',
    );
  }
  const result =
    data.calculate[`${projectId}|${a.pan ?? ''}`] ??
    data.calculate[`${projectId}|${a.name}`];
  const saved =
    data.get[`projects/${projectId}/eligibility/inputs?applicant=${who}`] ??
    data.get[`projects/${projectId}/eligibility/inputs?applicant=${a.pan}`] ??
    data.get[`projects/${projectId}/eligibility/inputs?applicant=${a.name}`];
  if (result && saved) {
    const unedited = calculateRequestBody(
      who,
      parseInputsResponse(clone(saved), who).inputs,
    );
    if (canonical(material(unedited)) === canonical(material(body))) {
      return { ...clone(result as Record<string, unknown>), calculated_at: new Date().toISOString() };
    }
  }
  return readOnly(
    'Read-only demo: the lender check runs on the values read from the documents. Changed income, loan, CIBIL or company values are recalculated in the full app (the calculation runs on the server); reopen the applicant to get the original values back.',
  );
}

function demoPost(data: DemoData, path: string, init?: RequestInit): unknown {
  const base = normPath(path).split('?')[0];
  const body = bodyOf(init);
  let m: RegExpMatchArray | null;
  if ((m = base.match(/^projects\/([^/]+)\/file-check$/))) {
    const cid =
      (body.checklist_id as string | undefined) ?? defaultChecklist(data, m[1]);
    const hit = data.fileCheck[`${m[1]}|${cid}`];
    if (hit) return clone(hit);
    throw new DemoReadOnlyError(
      'The demo has the check for the listed checklists only.',
    );
  }
  if ((m = base.match(/^projects\/([^/]+)\/file-check\/ask$/))) {
    return demoAsk(data, m[1], body);
  }
  if ((m = base.match(/^projects\/([^/]+)\/eligibility\/calculate$/))) {
    return demoCalculate(data, m[1], body);
  }
  if (/\/eligibility\/login$/.test(base)) {
    readOnly(
      'Read-only demo: logging a file in with a lender (and the CRM webhook it sends) is switched off here.',
    );
  }
  if (/\/integrations\/webhook\/test$/.test(base)) {
    readOnly('Read-only demo: the CRM webhook test is switched off here.');
  }
  if (/\/applicants\/erase$/.test(base)) {
    readOnly('Read-only demo: erasing an applicant is switched off here.');
  }
  return readOnly();
}

/** useAwsClient().fetchApi in the public demo. */
export async function demoFetchApi<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const data = await loadDemoData();
  const method = (init?.method ?? 'GET').toUpperCase();
  const isCheck = /file-check$|\/calculate$/.test(path);
  const isAsk = /file-check\/ask$/.test(path);
  await sleep(
    method === 'GET'
      ? jitter(90, 160)
      : isAsk
        ? jitter(1400, 900)
        : isCheck
          ? jitter(500, 400)
          : 150,
  );
  if (method === 'GET' || method === 'HEAD') return demoGet(data, path) as T;
  if (method === 'POST') return demoPost(data, path, init) as T;
  return readOnly();
}

/** useAwsClient().fetchApiBlob in the public demo: no binary downloads. */
export async function demoFetchApiBlob(): Promise<Blob> {
  throw new DemoReadOnlyError(
    'Document images and files are not part of the read-only demo.',
  );
}

// ------------------------------------------------------------------ chat

const CHAT_FALLBACK =
  '**Read-only demo.** The chat here replays the real agents’ answers to the suggestion chips, recorded from the app on this synthetic file. Start a **New chat** and pick one of the chips (for example *Is this file ready for lender login? What is missing?*). In the full app the agents answer anything about the file, with the same file-check, eligibility and EMI tools.';

function findAnswer(
  data: DemoData,
  projectId: string,
  agentId: string | null,
  prompt: string,
): DemoAnswer | null {
  const p = normText(prompt);
  const candidates = data.chat.filter(
    (a) => a.projectId === projectId || a.shared,
  );
  const sameAgent = (a: DemoAnswer) => (a.agentId ?? null) === agentId;
  const rank = (a: DemoAnswer) =>
    (a.projectId === projectId ? 2 : 0) + (sameAgent(a) ? 1 : 0);
  const exact = candidates
    .filter((a) => normText(a.prompt) === p)
    .sort((a, b) => rank(b) - rank(a));
  if (exact.length && (sameAgent(exact[0]) || exact[0].agentId === null)) {
    return exact[0];
  }
  if (exact.length) return exact[0];
  const near = candidates
    .filter(sameAgent)
    .map((a) => ({ a, s: similarity(a.prompt, prompt) }))
    .sort((x, y) => y.s - x.s)[0];
  return near && near.s >= 0.7 ? near.a : null;
}

function chunks(text: string, size: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}

function abortError(): DOMException {
  return new DOMException('The user aborted a request.', 'AbortError');
}

/** useAwsClient().invokeAgent in the public demo: replays a recorded answer. */
export async function demoInvokeAgent(
  prompt: ContentBlock[],
  _sessionId: string,
  projectId: string,
  onEvent?: (event: StreamEvent) => void,
  agentId?: string,
  _runtimeArn?: string,
  signal?: AbortSignal,
): Promise<string> {
  const data = await loadDemoData();
  const text = prompt
    .map((b) => b.text ?? '')
    .join('\n')
    .trim();
  const attached = prompt.some((b) => b.image || b.document);
  const recorded = attached
    ? null
    : findAnswer(data, projectId, agentId ?? null, text);
  const events: StreamEvent[] = recorded
    ? clone(recorded.events)
    : [{ type: 'text', content: CHAT_FALLBACK }];
  let result = '';
  await sleep(jitter(500, 400));
  for (const event of events) {
    if (signal?.aborted) throw abortError();
    if (event.type === 'text' && typeof event.content === 'string') {
      for (const piece of chunks(event.content, 28)) {
        if (signal?.aborted) throw abortError();
        await sleep(14);
        onEvent?.({ type: 'text', content: piece });
        result += piece;
      }
    } else if (event.type !== 'complete') {
      await sleep(event.type === 'tool_use' ? jitter(350, 300) : jitter(450, 500));
      onEvent?.(event);
    }
  }
  onEvent?.({ type: 'complete' });
  return result;
}

export const DEMO_USER_ID = DEMO_USERNAME;
