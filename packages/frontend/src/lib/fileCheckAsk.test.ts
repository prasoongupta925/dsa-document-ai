// @vitest-environment node
import {
  ASK_DEFAULT_PRICING,
  ASK_MAX_HISTORY,
  ASK_MAX_HISTORY_CONTENT,
  addToSession,
  buildAskHistory,
  costUsd,
  formatPrice,
  formatTokens,
  formatUsd,
  parseAskResponse,
  parseUsage,
  type AskTurn,
} from './fileCheckAsk';

const CONTRACT_RESPONSE = {
  answer: 'The file is **not complete**: the June 2026 salary slip is missing.',
  model_id: 'global.amazon.nova-2-lite-v1:0',
  input_tokens: 1200,
  output_tokens: 300,
  cost_usd: 0.001305,
  pricing: {
    input_per_million_usd: 0.35,
    output_per_million_usd: 2.95,
    region: 'ap-south-1',
  },
  grounded_on: {
    applicants: ['Sneha Ramesh Kulkarni'],
    documents: ['01_loan_application_form.pdf', '06_bank_statement.pdf'],
  },
};

describe('cost math', () => {
  it('prices tokens per 1M at the Nova 2 Lite rates', () => {
    // 1,200 × 0.35 / 1M + 300 × 2.95 / 1M = 0.00042 + 0.000885
    expect(costUsd(1200, 300)).toBeCloseTo(0.001305, 9);
    expect(formatUsd(costUsd(1200, 300))).toBe('$0.0013');
    expect(costUsd(0, 0)).toBe(0);
    expect(formatUsd(0)).toBe('$0.0000');
    // A real but tiny cost is not shown as zero.
    expect(formatUsd(costUsd(10, 2))).toBe('<$0.0001');
    expect(formatUsd(null)).toBe('–');
  });

  it('uses the pricing the API returned', () => {
    expect(
      costUsd(1_000_000, 1_000_000, {
        input_per_million_usd: 1,
        output_per_million_usd: 2,
      }),
    ).toBe(3);
  });

  it('adds every answered call to the session totals', () => {
    let session = { calls: 0, input_tokens: 0, output_tokens: 0 };
    session = addToSession(session, 1200, 300);
    session = addToSession(session, 11_145, 378);
    expect(session).toEqual({
      calls: 2,
      input_tokens: 12_345,
      output_tokens: 678,
    });
    // 12,345 × 0.35 / 1M + 678 × 2.95 / 1M = 0.00432075 + 0.0020001
    expect(
      formatUsd(
        costUsd(
          session.input_tokens,
          session.output_tokens,
          ASK_DEFAULT_PRICING,
        ),
      ),
    ).toBe('$0.0063');
  });

  it('formats tokens and prices', () => {
    expect(formatTokens(12345)).toBe('12,345');
    expect(formatTokens(undefined)).toBe('–');
    expect(formatPrice(0.35)).toBe('$0.35');
    expect(formatPrice(2.95)).toBe('$2.95');
  });
});

describe('buildAskHistory', () => {
  const done = (i: number): AskTurn => ({
    id: i,
    question: `q${i}`,
    status: 'done',
    answer: `a${i}`,
  });

  it('sends the last answered turns, at most 6 messages', () => {
    const turns: AskTurn[] = [
      done(1),
      done(2),
      { id: 3, question: 'failed', status: 'error', error: new Error('x') },
      done(4),
      done(5),
      { id: 6, question: 'pending', status: 'pending' },
    ];
    const history = buildAskHistory(turns);
    expect(history).toHaveLength(ASK_MAX_HISTORY);
    expect(history).toEqual([
      { role: 'user', content: 'q2' },
      { role: 'assistant', content: 'a2' },
      { role: 'user', content: 'q4' },
      { role: 'assistant', content: 'a4' },
      { role: 'user', content: 'q5' },
      { role: 'assistant', content: 'a5' },
    ]);
  });

  it('is empty for a new conversation and clips long answers', () => {
    expect(buildAskHistory([])).toEqual([]);
    const long = 'x'.repeat(5000);
    const turn: AskTurn = {
      id: 1,
      question: 'q',
      status: 'done',
      answer: long,
    };
    const [, assistant] = buildAskHistory([turn]);
    expect(assistant.content).toHaveLength(ASK_MAX_HISTORY_CONTENT);
    expect(assistant.content.endsWith('…')).toBe(true);
  });
});

describe('parseAskResponse', () => {
  it('accepts the API contract', () => {
    expect(parseAskResponse(CONTRACT_RESPONSE)).toEqual(CONTRACT_RESPONSE);
  });

  it('fills pricing and cost when they are missing', () => {
    const res = parseAskResponse({
      answer: 'not in the file',
      input_tokens: 1200,
      output_tokens: 300,
    });
    expect(res.pricing).toEqual(ASK_DEFAULT_PRICING);
    expect(res.cost_usd).toBeCloseTo(0.001305, 9);
    expect(res.grounded_on).toEqual({ applicants: [], documents: [] });
    expect(res.model_id).toBe('');
  });

  it('rejects anything else', () => {
    expect(() => parseAskResponse(null)).toThrow(/unexpected response/);
    expect(() =>
      parseAskResponse({ answer: 'x', input_tokens: '1', output_tokens: 1 }),
    ).toThrow(/unexpected response/);
    expect(() => parseAskResponse({ error: 'model timed out' })).toThrow(
      'model timed out',
    );
  });
});

describe('parseUsage', () => {
  it('accepts the usage contract', () => {
    expect(
      parseUsage({
        window_days: 7,
        calls: 5,
        input_tokens: 9000,
        output_tokens: 1500,
        cost_usd: 0.0076,
      }),
    ).toEqual({
      window_days: 7,
      calls: 5,
      input_tokens: 9000,
      output_tokens: 1500,
      cost_usd: 0.0076,
    });
  });

  it('returns null for anything else', () => {
    expect(parseUsage(undefined)).toBeNull();
    expect(parseUsage({ calls: 'many', cost_usd: 1 })).toBeNull();
  });
});
