// @vitest-environment node
import i18next, { type TFunction } from 'i18next';
import en from '../i18n/locales/en.json';
import { ApiError, apiErrorDetail, errorDetailFromBody } from './apiError';
import { apiErrorStatus } from './fileCheck';
import {
  looksLikeHttpsUrl,
  parseWebhookSecret,
  parseWebhookSettings,
  parseWebhookTestResult,
  webhookUrlValue,
} from './integrations';
import { describeWebhookError } from '../components/WebhookSettings';

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

describe('API error details', () => {
  it('keeps the FastAPI detail of a rejected request', () => {
    const error = new ApiError(
      400,
      errorDetailFromBody('{"detail":"Webhook URL must use https"}'),
    );
    expect(error.message).toBe('API error: 400');
    expect(apiErrorStatus(error)).toBe(400);
    expect(apiErrorDetail(error)).toBe('Webhook URL must use https');
    // Validation errors and structured details.
    expect(
      apiErrorDetail(
        new ApiError(
          422,
          errorDetailFromBody(
            '{"detail":[{"loc":["body","url"],"msg":"Field required"}]}',
          ),
        ),
      ),
    ).toBe('Field required');
    expect(
      apiErrorDetail(
        new ApiError(400, { message: 'unknown checklist', available: [] }),
      ),
    ).toBe('unknown checklist');
    expect(errorDetailFromBody('')).toBeUndefined();
    expect(apiErrorDetail(new Error('API error: 500'))).toBeNull();
    expect(apiErrorStatus(new Error('API error: 503'))).toBe(503);
  });

  it('shows a localized message, then the reason a request was refused', () => {
    const t = i18n.getFixedT('en');
    expect(
      describeWebhookError(
        t,
        new ApiError(400, 'Webhook URL host is an internal name'),
      ),
    ).toBe(
      'The request was rejected (HTTP 400). (Webhook URL host is an internal name)',
    );
    expect(
      describeWebhookError(t, new ApiError(404, 'Project not found')),
    ).toBe('The project was not found (HTTP 404).');
    expect(describeWebhookError(t, new ApiError(502))).toBe(
      'The request was rejected (HTTP 502).',
    );
    expect(
      describeWebhookError(
        t,
        new ApiError(429, 'Wait 6 s before sending another test event'),
      ),
    ).toBe(
      'Too many test events (HTTP 429): wait a few seconds and try again. (Wait 6 s before sending another test event)',
    );
  });

  it('says "not configured" only when the API says so', () => {
    const t = i18n.getFixedT('en');
    for (const detail of [
      'Webhook delivery is not configured',
      'Webhook secret encryption is not configured',
    ]) {
      expect(describeWebhookError(t, new ApiError(503, detail))).toBe(
        'Webhook delivery is not configured for this deployment (HTTP 503).',
      );
    }
    // A gateway 503 while the backend restarts is not a missing setting.
    expect(
      describeWebhookError(
        t,
        new ApiError(503, { message: 'Service Unavailable' }),
      ),
    ).toBe('The request was rejected (HTTP 503). (Service Unavailable)');
    // The locale's message is used (Korean, Japanese), not the API's English.
    const keyOnly = ((key: string) => key) as unknown as TFunction;
    expect(
      describeWebhookError(
        keyOnly,
        new ApiError(503, 'Webhook delivery is not configured'),
      ),
    ).toBe('integrations.webhook.errors.notConfigured');
  });
});

describe('webhook responses', () => {
  it('parses the settings and deliveries', () => {
    const settings = parseWebhookSettings({
      url: 'https://crm.example.com/hooks/idp',
      enabled: true,
      secret_set: true,
      deliveries: [
        {
          delivery_id: 'a1',
          at: '2026-09-30T10:00:00+00:00',
          event: 'file_check.completed',
          applicant: 'Sneha Anil Kulkarni',
          status: 'delivered',
          http_status: 200,
          error: null,
        },
        {
          delivery_id: 'a2',
          at: '2026-09-30T09:00:00+00:00',
          event: 'test',
          applicant: null,
          status: 'failed',
          http_status: null,
          error: 'blocked: resolves to a private address',
        },
        { event: 'no id' },
      ],
    });
    expect(settings.deliveries.map((d) => [d.delivery_id, d.status])).toEqual([
      ['a1', 'delivered'],
      ['a2', 'failed'],
    ]);
    expect(settings.deliveries[1].http_status).toBeNull();
    expect(() => parseWebhookSettings({ url: null })).toThrow();
    expect(parseWebhookSettings({ url: '', enabled: false }).url).toBeNull();
  });

  it('parses the secret and the test result', () => {
    expect(parseWebhookSecret({ secret: 'abc' })).toBe('abc');
    expect(() => parseWebhookSecret({})).toThrow();
    expect(
      parseWebhookTestResult({
        delivery_id: 't1',
        status: 'failed',
        http_status: 503,
        error: 'HTTP 503',
      }),
    ).toEqual({
      delivery_id: 't1',
      status: 'failed',
      http_status: 503,
      error: 'HTTP 503',
    });
  });

  it('sends an empty URL as null and hints at https', () => {
    expect(webhookUrlValue('  ')).toBeNull();
    expect(webhookUrlValue(' https://a.example.com/x ')).toBe(
      'https://a.example.com/x',
    );
    expect(looksLikeHttpsUrl('https://crm.example.com/hooks?x=1')).toBe(true);
    expect(looksLikeHttpsUrl('http://crm.example.com')).toBe(false);
    expect(looksLikeHttpsUrl('crm.example.com')).toBe(false);
  });
});
