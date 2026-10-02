// Errors thrown by useAwsClient().fetchApi for non-2xx responses. The message
// stays 'API error: <status>' (apiErrorStatus in lib/fileCheck parses it);
// `detail` carries the FastAPI error body so a form can show why a request
// was rejected (e.g. an invalid webhook URL).

export class ApiError extends Error {
  readonly status: number;
  /** The response body's `detail` (FastAPI), else the body text, if any. */
  readonly detail: unknown;

  constructor(status: number, detail?: unknown) {
    super(`API error: ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** Parses an error response body: JSON `detail`, else the trimmed text. */
export function errorDetailFromBody(text: string): unknown {
  const body = text.trim();
  if (!body) return undefined;
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      return (parsed as { detail: unknown }).detail;
    }
    return parsed;
  } catch {
    // Not JSON (e.g. an API Gateway HTML page): short text only.
    return body.length <= 300 ? body : undefined;
  }
}

function detailText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail.trim() || null;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    const messages = detail
      .map((d) =>
        d &&
        typeof d === 'object' &&
        typeof (d as { msg?: unknown }).msg === 'string'
          ? (d as { msg: string }).msg
          : typeof d === 'string'
            ? d
            : null,
      )
      .filter((m): m is string => !!m && !!m.trim());
    return messages.length > 0 ? messages.join('; ') : null;
  }
  if (detail && typeof detail === 'object') {
    const o = detail as Record<string, unknown>;
    if (typeof o.message === 'string' && o.message.trim()) {
      return o.message.trim();
    }
    if (typeof o.detail === 'string' && o.detail.trim()) {
      return o.detail.trim();
    }
  }
  return null;
}

/** The backend's reason for a rejected request, as text; null if none. */
export function apiErrorDetail(error: unknown): string | null {
  if (error instanceof ApiError) return detailText(error.detail);
  if (error && typeof error === 'object' && 'detail' in error) {
    return detailText((error as { detail: unknown }).detail);
  }
  return null;
}
