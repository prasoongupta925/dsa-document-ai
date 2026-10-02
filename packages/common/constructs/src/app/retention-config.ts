import { RetentionDays } from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

/**
 * Maximum number of days client data (documents, derived analysis, chat
 * sessions, artifacts) and logs are kept. Override with
 * `-c retentionDays=<n>`.
 */
export const DEFAULT_RETENTION_DAYS = 7;

/**
 * CloudWatch Logs retention steps in ascending order (INFINITE excluded).
 */
const LOG_RETENTION_STEPS: RetentionDays[] = [
  RetentionDays.ONE_DAY,
  RetentionDays.THREE_DAYS,
  RetentionDays.FIVE_DAYS,
  RetentionDays.ONE_WEEK,
  RetentionDays.TWO_WEEKS,
  RetentionDays.ONE_MONTH,
  RetentionDays.TWO_MONTHS,
  RetentionDays.THREE_MONTHS,
  RetentionDays.FOUR_MONTHS,
  RetentionDays.FIVE_MONTHS,
  RetentionDays.SIX_MONTHS,
  RetentionDays.ONE_YEAR,
  RetentionDays.THIRTEEN_MONTHS,
  RetentionDays.EIGHTEEN_MONTHS,
  RetentionDays.TWO_YEARS,
  RetentionDays.THREE_YEARS,
  RetentionDays.FIVE_YEARS,
  RetentionDays.SIX_YEARS,
  RetentionDays.SEVEN_YEARS,
  RetentionDays.EIGHT_YEARS,
  RetentionDays.NINE_YEARS,
  RetentionDays.TEN_YEARS,
];

/**
 * Integer >= 1 from CDK context `key`, or undefined when it is not set.
 * Throws when the value is not an integer >= 1.
 */
function getDaysContext(scope: Construct, key: string): number | undefined {
  const value: unknown = scope.node.tryGetContext(key);
  if (value === undefined || value === null || String(value).trim() === '') {
    return undefined;
  }
  const days = Number(String(value).trim());
  if (!Number.isInteger(days) || days < 1) {
    throw new Error(
      `Invalid CDK context ${key}=${String(value)}: ` +
        `expected an integer >= 1 (e.g. -c ${key}=7).`,
    );
  }
  return days;
}

/**
 * Retention in days from CDK context `retentionDays` (default 7).
 * Throws when the value is not an integer >= 1.
 */
export function getRetentionDays(scope: Construct): number {
  return getDaysContext(scope, 'retentionDays') ?? DEFAULT_RETENTION_DAYS;
}

/**
 * Retention in days for security logs (access, audit and network flow logs)
 * from CDK context `securityLogRetentionDays`. Defaults to retentionDays (7
 * for the demo), so nothing changes unless it is set. Throws when the value
 * is not an integer >= 1.
 *
 * Production: the Digital Personal Data Protection Rules, 2025, Rule 6(1)(e)
 * require a Data Fiduciary to retain such logs, and the personal data they
 * hold, for one year to detect, investigate and remediate unauthorised
 * access, unless another law requires otherwise. Before production, set
 * `-c securityLogRetentionDays=365` and apply this value
 * (`toLogRetention(getSecurityLogRetentionDays(this))`) to the security log
 * groups, and exempt them from the LogRetentionEnforcer (RetentionStack),
 * which caps every log group at retentionDays. Client data keeps
 * retentionDays. No resource reads this value yet.
 */
export function getSecurityLogRetentionDays(scope: Construct): number {
  return (
    getDaysContext(scope, 'securityLogRetentionDays') ?? getRetentionDays(scope)
  );
}

/**
 * The largest CloudWatch Logs retention that does not exceed `days`
 * (minimum ONE_DAY).
 */
export function toLogRetention(days: number): RetentionDays {
  let retention = RetentionDays.ONE_DAY;
  for (const step of LOG_RETENTION_STEPS) {
    if (step <= days) {
      retention = step;
    }
  }
  return retention;
}
