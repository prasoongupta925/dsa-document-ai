// Mirrors packages/backend (prefix /projects/{project_id}/integrations): the
// project's outgoing webhook for CallVarta CRM or another CRM.

export type WebhookDeliveryStatus = 'delivered' | 'failed';

export interface WebhookDelivery {
  delivery_id: string;
  /** ISO timestamp */
  at: string;
  event: string;
  applicant: string | null;
  status: WebhookDeliveryStatus;
  http_status: number | null;
  error: string | null;
}

/** GET / PUT .../integrations/webhook */
export interface WebhookSettings {
  url: string | null;
  enabled: boolean;
  /** The secret itself is only returned once, by POST .../webhook/secret. */
  secret_set: boolean;
  deliveries: WebhookDelivery[];
}

/** PUT .../integrations/webhook (400 with a detail for an invalid URL) */
export interface WebhookUpdateRequest {
  url: string | null;
  enabled: boolean;
}

/** POST .../integrations/webhook/secret */
export interface WebhookSecretResponse {
  secret: string;
}

/** POST .../integrations/webhook/test */
export interface WebhookTestResult {
  delivery_id: string;
  status: WebhookDeliveryStatus;
  http_status: number | null;
  error: string | null;
}
