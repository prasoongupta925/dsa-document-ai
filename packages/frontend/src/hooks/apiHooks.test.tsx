// @vitest-environment node
// (The workspace's jsdom install cannot start.) Each hook runs once in a
// server render: its callbacks are real, effects do not run and state
// updates are dropped, so these tests check what the hooks send to fetchApi
// and what they return or report.
import { renderToStaticMarkup } from 'react-dom/server';
import { useFileCheck } from './useFileCheck';
import { useWebhookSettings } from './useWebhookSettings';
import { useAgents } from './useAgents';
import { ApiError } from '../lib/apiError';
import { SNEHA_NOT_READY_RESULT } from '../components/FileCheckPanel/fixtures';

const noop = () => undefined;

function hookOnce<T>(useHook: () => T): T {
  let value: T | undefined;
  function Probe() {
    value = useHook();
    return null;
  }
  renderToStaticMarkup(<Probe />);
  return value as T;
}

interface Call {
  url: string;
  init?: RequestInit;
}

/** fetchApi stub: records each call and answers with the next reply (an Error is thrown). */
function fakeApi(...replies: unknown[]) {
  const calls: Call[] = [];
  const fetchApi = async <T,>(url: string, init?: RequestInit): Promise<T> => {
    calls.push({ url, init });
    const reply = replies.length > 1 ? replies.shift() : replies[0];
    if (reply instanceof Error) throw reply;
    return reply as T;
  };
  return { calls, fetchApi };
}

// The hooks log failed calls; the tests cause them on purpose.
beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(noop);
});
afterEach(() => {
  vi.restoreAllMocks();
});

const SNEHA = SNEHA_NOT_READY_RESULT.applicants[0];
const ERASED = {
  applicant: 'Sneha Anil Kulkarni',
  documents_deleted: SNEHA.documents.map((d) => ({
    document_id: d.document_id,
    name: d.document_name,
  })),
  failed: [],
  delivery_log_redacted: 2,
  not_erased: [
    'Verdicts the CRM webhook already delivered: erase them in the CRM',
  ],
  erased_at: '2026-09-30T10:00:00+00:00',
};

describe('useFileCheck.eraseApplicant', () => {
  const setup = (...replies: unknown[]) => {
    const api = fakeApi(...replies);
    const erased: unknown[] = [];
    const state = hookOnce(() =>
      useFileCheck({
        fetchApi: api.fetchApi,
        projectId: 'proj_demo',
        onApplicantErased: (response) => erased.push(response),
      }),
    );
    return { ...api, erased, state };
  };

  it('sends the PAN, the typed name and the documents the verdict shows', async () => {
    const { calls, erased, state } = setup(ERASED);

    const outcome = await state.eraseApplicant(SNEHA, 'Sneha Anil Kulkarni');

    expect(calls).toEqual([
      {
        url: 'projects/proj_demo/applicants/erase',
        init: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            applicant: 'CKRPK7314M',
            confirm: 'Sneha Anil Kulkarni',
            document_ids: ['s1', 's2', 's3', 's4', 's5'],
          }),
        },
      },
    ]);
    expect(outcome).toEqual({ kind: 'erased', response: ERASED });
    expect(erased).toEqual([ERASED]);
  });

  it('sends the name when the verdict has no PAN', async () => {
    const { calls, state } = setup(ERASED);

    await state.eraseApplicant({ ...SNEHA, pan: null }, 'Sneha Anil Kulkarni');

    expect(JSON.parse(String(calls[0].init?.body)).applicant).toBe(
      'Sneha Anil Kulkarni',
    );
  });

  it('a refusal deletes nothing: no reload of the documents', async () => {
    const { erased, state } = setup(
      new ApiError(409, "The applicant's documents changed since the check"),
    );

    const outcome = await state.eraseApplicant(SNEHA, 'Sneha Anil Kulkarni');

    expect(outcome).toEqual({ kind: 'failed' });
    expect(erased).toEqual([]);
  });

  it.each([
    ['a gateway timeout', new ApiError(504, { message: 'Endpoint timed out' })],
    ['a lost connection', new TypeError('Failed to fetch')],
    ['a server error', new ApiError(500, 'Internal Server Error')],
  ])(
    'after %s the erase may have run: the documents are reloaded',
    async (_, error) => {
      const { erased, state } = setup(error);

      const outcome = await state.eraseApplicant(SNEHA, 'Sneha Anil Kulkarni');

      expect(outcome).toEqual({ kind: 'uncertain' });
      expect(erased).toEqual([null]);
    },
  );

  it('an unreadable answer to a sent erase is uncertain too', async () => {
    const { erased, state } = setup({ unexpected: true });

    expect(await state.eraseApplicant(SNEHA, 'Sneha Anil Kulkarni')).toEqual({
      kind: 'uncertain',
    });
    expect(erased).toEqual([null]);
  });

  it('the API’s own errors before deleting are plain failures', async () => {
    for (const error of [
      new ApiError(503, 'File check is not configured'),
      new ApiError(502, 'Applicant lookup failed: Unknown tool'),
    ]) {
      const { erased, state } = setup(error);
      expect(await state.eraseApplicant(SNEHA, 'Sneha Anil Kulkarni')).toEqual({
        kind: 'failed',
      });
      expect(erased).toEqual([]);
    }
  });
});

describe('useWebhookSettings', () => {
  const SETTINGS = {
    url: 'https://crm.example.com/hooks/idp',
    enabled: false,
    secret_set: true,
    deliveries: [],
  };

  it('uses the integrations paths and methods', async () => {
    const { calls, fetchApi } = fakeApi(
      SETTINGS,
      SETTINGS,
      { secret: 's'.repeat(43) },
      { delivery_id: 'd1', status: 'delivered', http_status: 204, error: null },
      SETTINGS,
    );
    const state = hookOnce(() =>
      useWebhookSettings({ fetchApi, projectId: 'proj_demo' }),
    );

    await state.load();
    expect(
      await state.save({
        url: 'https://crm.example.com/hooks/idp',
        enabled: true,
      }),
    ).toBe(true);
    expect(await state.generateSecret()).toBe(true);
    await state.sendTest();

    const base = 'projects/proj_demo/integrations/webhook';
    expect(calls.map((c) => [c.url, c.init?.method ?? 'GET'])).toEqual([
      [base, 'GET'],
      [base, 'PUT'],
      [`${base}/secret`, 'POST'],
      [`${base}/test`, 'POST'],
      [base, 'GET'], // the deliveries are reloaded after a test
    ]);
    expect(calls[1].init?.headers).toEqual({
      'Content-Type': 'application/json',
    });
    // The PUT always carries both fields (the API forbids others).
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      url: 'https://crm.example.com/hooks/idp',
      enabled: true,
    });
    expect(calls[2].init?.body).toBeUndefined();
  });

  it('reports a refused save as false', async () => {
    const { fetchApi } = fakeApi(
      new ApiError(
        400,
        'Webhook URL port must be 443 (the https default) or 8443',
      ),
    );
    const state = hookOnce(() =>
      useWebhookSettings({ fetchApi, projectId: 'proj_demo' }),
    );

    expect(
      await state.save({ url: 'https://crm.example.com:22/x', enabled: false }),
    ).toBe(false);
  });
});

describe('useAgents', () => {
  it('never deletes a built-in agent', async () => {
    const { calls, fetchApi } = fakeApi([]);
    const state = hookOnce(() =>
      useAgents({ fetchApi, projectId: 'proj_demo', onNewSession: noop }),
    );

    await expect(
      state.handleAgentDelete('builtin-file-checker'),
    ).rejects.toThrow('Built-in agents cannot be edited or deleted');
    expect(calls).toEqual([]);
  });

  it('deletes a custom agent by its encoded id, then reloads the list', async () => {
    const { calls, fetchApi } = fakeApi(null, []);
    const state = hookOnce(() =>
      useAgents({ fetchApi, projectId: 'proj_demo', onNewSession: noop }),
    );

    await state.handleAgentDelete('my agent/1');

    expect(calls.map((c) => [c.url, c.init?.method ?? 'GET'])).toEqual([
      ['projects/proj_demo/agents/my%20agent%2F1', 'DELETE'],
      ['projects/proj_demo/agents', 'GET'],
    ]);
  });

  it('selecting an agent starts a new session', () => {
    let sessions = 0;
    const { fetchApi } = fakeApi([]);
    const state = hookOnce(() =>
      useAgents({
        fetchApi,
        projectId: 'proj_demo',
        onNewSession: () => {
          sessions += 1;
        },
      }),
    );

    state.handleAgentSelect('builtin-file-checker');
    state.handleAgentSelect(null);

    expect(sessions).toBe(2);
  });
});
