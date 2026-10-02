import type { TFunction } from 'i18next';
import { apiErrorDetail } from '../../lib/apiError';
import { apiErrorStatus } from '../../lib/fileCheck';

// Status codes of packages/backend/app/routers/eligibility.py: a localized
// message, then the API's reason (e.g. which input was rejected) when it
// gives one.
export function describeEligibilityError(t: TFunction, error: unknown): string {
  const status = apiErrorStatus(error);
  if (status === null) {
    return error instanceof Error ? error.message : String(error);
  }
  const detail = apiErrorDetail(error);
  let message: string;
  if (status === 404) message = t('eligibility.errors.notFound');
  else if (status === 400 || status === 422) {
    message = t('eligibility.errors.invalid');
  } else if (status === 409) message = t('eligibility.errors.conflict');
  else if (status === 429) message = t('eligibility.errors.tooMany');
  else if (status === 503) message = t('eligibility.errors.notConfigured');
  else if (status === 502 || status === 504) {
    message = t('eligibility.errors.unavailable', { status });
  } else {
    message = t('eligibility.errors.status', { status });
  }
  return detail ? `${message} (${detail})` : message;
}
