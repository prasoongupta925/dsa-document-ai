// @vitest-environment node
// (The workspace's jsdom install cannot start.) The hook runs once in a
// server render: its callbacks are real, effects do not run and state
// updates are dropped, so these tests check what it sends to fetchApi and
// what it returns.
import { renderToStaticMarkup } from 'react-dom/server';
import { useEligibility } from './useEligibility';
import { ApiError } from '../lib/apiError';
import {
  inputsRequestBody,
  normalizeInputs,
  setTradelineAction,
} from '../lib/eligibility';
import {
  LENDERS_RESPONSE,
  LOGIN_NOTIFIED,
  PREFILLED_INPUTS_RESPONSE,
  SAVED_INPUTS_RESPONSE,
  WORKED_EXAMPLE_RESPONSE,
} from '../components/EligibilityPanel/fixtures';

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

const body = (call: Call) => JSON.parse(String(call.init?.body));
const PAN = 'CKRPK7314M';
const BASE = 'projects/proj_demo/eligibility';

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('useEligibility', () => {
  const setup = (...replies: unknown[]) => {
    const api = fakeApi(...replies);
    const state = hookOnce(() =>
      useEligibility({ fetchApi: api.fetchApi, projectId: 'proj_demo' }),
    );
    return { ...api, state };
  };
  const inputs = normalizeInputs(SAVED_INPUTS_RESPONSE.inputs);

  it('loads the policies and an applicant’s inputs by PAN', async () => {
    const { calls, state } = setup(LENDERS_RESPONSE, PREFILLED_INPUTS_RESPONSE);
    await state.loadLenders();
    await state.load(PAN);
    expect(calls.map((c) => [c.url, c.init?.method ?? 'GET'])).toEqual([
      [`${BASE}/lenders`, 'GET'],
      [`${BASE}/inputs?applicant=${PAN}`, 'GET'],
    ]);
    const { calls: named, state: other } = setup(PREFILLED_INPUTS_RESPONSE);
    await other.load('Sneha Anil Kulkarni');
    expect(named[0].url).toBe(
      `${BASE}/inputs?applicant=Sneha%20Anil%20Kulkarni`,
    );
  });

  it('saves with PUT {applicant, profile, cibil, loan}', async () => {
    const { calls, state } = setup(SAVED_INPUTS_RESPONSE);
    expect(await state.save(PAN, inputs)).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`${BASE}/inputs`);
    expect(calls[0].init?.method).toBe('PUT');
    expect(calls[0].init?.headers).toEqual({
      'Content-Type': 'application/json',
    });
    expect(body(calls[0])).toEqual(
      JSON.parse(JSON.stringify(inputsRequestBody(PAN, inputs))),
    );
  });

  it('a BT / Obligate / Close choice goes to POST .../calculate', async () => {
    const { calls, state } = setup(WORKED_EXAMPLE_RESPONSE);
    const toggled = setTradelineAction(inputs, 0, 'bt');
    const result = await state.calculate(PAN, toggled);
    expect(calls[0].url).toBe(`${BASE}/calculate`);
    expect(calls[0].init?.method).toBe('POST');
    const sent = body(calls[0]);
    expect(sent.applicant).toBe(PAN);
    expect(
      sent.inputs.cibil.tradelines.map((r: { action: string }) => r.action),
    ).toEqual(['bt', 'close']);
    expect(result?.best_lender).toBe('ICICI Bank');
    expect(result?.per_lender[0].eligible_amount).toBe(2058000);
  });

  it('reports a failed calculation as null', async () => {
    const { state } = setup(new ApiError(422, [{ msg: 'bad' }]));
    expect(await state.calculate(PAN, inputs)).toBeNull();
  });

  it('logs in by lender id; unsaved inputs are saved first', async () => {
    const { calls, state } = setup(SAVED_INPUTS_RESPONSE, LOGIN_NOTIFIED);
    const outcome = await state.login(
      PAN,
      { id: 'icici_bank', name: 'ICICI Bank' },
      inputs,
    );
    expect(calls.map((c) => [c.url, c.init?.method])).toEqual([
      [`${BASE}/inputs`, 'PUT'],
      [`${BASE}/login`, 'POST'],
    ]);
    expect(body(calls[1])).toEqual({ applicant: PAN, lender: 'icici_bank' });
    expect(outcome.kind).toBe('logged_in');
    if (outcome.kind === 'logged_in') {
      expect(outcome.response.webhook).toBe('delivered');
    }
  });

  it('does not log in when the save first fails', async () => {
    const { calls, state } = setup(new ApiError(422, 'bad input'));
    const outcome = await state.login(
      PAN,
      { id: 'icici_bank', name: 'ICICI Bank' },
      inputs,
    );
    expect(outcome).toEqual({ kind: 'failed' });
    expect(calls.map((c) => c.init?.method)).toEqual(['PUT']);
  });

  it('a refused login (not eligible) is a failure', async () => {
    const { calls, state } = setup(
      new ApiError(409, 'Axis Bank: Not serviceable: Pincode 400615 ...'),
    );
    const outcome = await state.login(PAN, {
      id: 'axis_bank',
      name: 'Axis Bank',
    });
    expect(outcome).toEqual({ kind: 'failed' });
    expect(calls.map((c) => c.url)).toEqual([`${BASE}/login`]);
  });

  it('checks a pincode and a company against the sample lists', async () => {
    const { calls, state } = setup(
      {
        pincode: '400615',
        region: 'Thane (Ghodbunder Road)',
        serviceable_by: 1,
        lenders: [
          { lender_id: 'icici_bank', lender: 'ICICI Bank', serviceable: true },
        ],
        sample: true,
        label: 'sample policy — replace with your lender grid',
      },
      {
        query: 'Konkan Soft',
        match: null,
        categories: [],
        suggestions: ['Konkan Softworks Pvt Ltd'],
        sample: true,
        label: 'sample policy — replace with your lender grid',
      },
    );
    const pin = await state.checkPincode(' 400615 ');
    const company = await state.checkCompany('Konkan Soft');
    expect(calls.map((c) => c.url)).toEqual([
      `${BASE}/pincodes/400615`,
      `${BASE}/companies?name=Konkan%20Soft`,
    ]);
    expect(pin.region).toBe('Thane (Ghodbunder Road)');
    expect(company.suggestions).toEqual(['Konkan Softworks Pvt Ltd']);
  });
});
