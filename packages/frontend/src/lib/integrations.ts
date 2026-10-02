// Validation and display helpers for the project webhook
// (projects/{id}/integrations/webhook). Values are shown as the API returns
// them; nothing here signs or sends anything.
import type {
  WebhookDelivery,
  WebhookDeliveryStatus,
  WebhookSettings,
  WebhookTestResult,
} from '../types/integrations';

function obj(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === 'object' && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : {};
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function status(value: unknown): WebhookDeliveryStatus {
  return value === 'delivered' ? 'delivered' : 'failed';
}

function httpStatus(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function delivery(raw: unknown): WebhookDelivery | null {
  const o = obj(raw);
  const id = text(o.delivery_id);
  if (!id) return null;
  return {
    delivery_id: id,
    at: text(o.at) ?? '',
    event: text(o.event) ?? '',
    applicant: text(o.applicant),
    status: status(o.status),
    http_status: httpStatus(o.http_status),
    error: text(o.error),
  };
}

/** GET / PUT response; throws when it is not the API contract. */
export function parseWebhookSettings(raw: unknown): WebhookSettings {
  const o = obj(raw);
  if (typeof o.enabled !== 'boolean') {
    throw new Error('unexpected response from the webhook settings API');
  }
  return {
    url: text(o.url),
    enabled: o.enabled,
    secret_set: o.secret_set === true,
    deliveries: Array.isArray(o.deliveries)
      ? o.deliveries
          .map(delivery)
          .filter((d): d is WebhookDelivery => d !== null)
      : [],
  };
}

export function parseWebhookSecret(raw: unknown): string {
  const secret = text(obj(raw).secret);
  if (!secret) throw new Error('the API returned no secret');
  return secret;
}

export function parseWebhookTestResult(raw: unknown): WebhookTestResult {
  const o = obj(raw);
  return {
    delivery_id: text(o.delivery_id) ?? '',
    status: status(o.status),
    http_status: httpStatus(o.http_status),
    error: text(o.error),
  };
}

/** The form's URL as the API expects it: trimmed, empty -> null. */
export function webhookUrlValue(input: string): string | null {
  const url = input.trim();
  return url ? url : null;
}

/** Only a hint: the API validates the URL (and answers 400 with a reason). */
export function looksLikeHttpsUrl(input: string): boolean {
  return /^https:\/\/[^\s/?#]+[^\s]*$/i.test(input.trim());
}

/**
 * The signature header a receiver checks:
 * X-DocAI-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>
 */
export const WEBHOOK_SIGNATURE_EXAMPLE =
  'X-DocAI-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>';

/** Receiver-side check, as a short Python snippet for the settings note. */
export const WEBHOOK_VERIFY_SNIPPET = [
  'import hashlib, hmac, time',
  '',
  'def verify(secret: str, header: str, body: bytes) -> bool:',
  '    parts = dict(p.split("=", 1) for p in header.split(","))',
  '    t, v1 = parts["t"], parts["v1"]',
  '    if abs(time.time() - int(t)) > 300:  # replay window',
  '        return False',
  '    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256)',
  '    return hmac.compare_digest(mac.hexdigest(), v1)',
].join('\n');
