// @vitest-environment node
// (Rendered to static markup: the workspace's jsdom install cannot start.)
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import en from '../i18n/locales/en.json';
import { DeliveriesSection } from './WebhookSettings';
import { ApiError } from '../lib/apiError';
import type { WebhookDelivery } from '../types/integrations';

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: 'en',
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
    showSupportNotice: false,
  });
});

const DELIVERY: WebhookDelivery = {
  delivery_id: 'd-1',
  at: '2026-09-30T10:00:00+00:00',
  event: 'test',
  applicant: null,
  status: 'delivered',
  http_status: 204,
  error: null,
};

function render(loadError: unknown) {
  return renderToStaticMarkup(
    <I18nextProvider i18n={i18n}>
      <DeliveriesSection
        deliveries={[DELIVERY]}
        loading={false}
        loadError={loadError}
        onRefresh={() => undefined}
      />
    </I18nextProvider>,
  );
}

describe('DeliveriesSection', () => {
  it('says when refreshing the deliveries failed, next to the stale table', () => {
    const html = render(new ApiError(502, 'Webhook delivery failed'));

    expect(html).toContain('data-testid="webhook-deliveries"');
    expect(html).toContain('role="alert"');
    expect(html).toContain(
      'Could not refresh the deliveries, so the list may be out of date: The request was rejected (HTTP 502). (Webhook delivery failed)',
    );
  });

  it('shows no alert after a successful load', () => {
    const html = render(null);

    expect(html).not.toContain('data-testid="webhook-reload-error"');
    expect(html).toContain('>test</td>');
  });
});
