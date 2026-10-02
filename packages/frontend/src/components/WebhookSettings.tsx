import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  AlertTriangle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  RefreshCw,
  Send,
  Webhook,
} from 'lucide-react';
import { useWebhookSettings } from '../hooks/useWebhookSettings';
import { apiErrorDetail } from '../lib/apiError';
import { apiErrorStatus } from '../lib/fileCheck';
import { copyText, type CopyOutcome } from '../lib/clipboard';
import {
  WEBHOOK_VERIFY_SNIPPET,
  looksLikeHttpsUrl,
  webhookUrlValue,
} from '../lib/integrations';
import type { WebhookDelivery } from '../types/integrations';

// The API's own 503 details (packages/backend/app/routers/integrations.py);
// any other 503 comes from the gateway, not from a missing setting.
const NOT_CONFIGURED_DETAILS = [
  'Webhook delivery is not configured',
  'Webhook secret encryption is not configured',
];

// Status codes of packages/backend/app/routers/integrations.py: a localized
// message first, then the API's detail (e.g. why a URL is refused, in
// English) where the message is generic.
export function describeWebhookError(t: TFunction, error: unknown): string {
  const detail = apiErrorDetail(error);
  const status = apiErrorStatus(error);
  if (status === null) {
    return error instanceof Error ? error.message : String(error);
  }
  if (status === 404) return t('integrations.webhook.errors.notFound');
  if (status === 503 && detail && NOT_CONFIGURED_DETAILS.includes(detail)) {
    return t('integrations.webhook.errors.notConfigured');
  }
  const message =
    status === 429
      ? t('integrations.webhook.errors.tooMany')
      : t('integrations.webhook.errors.status', { status });
  return detail ? `${message} (${detail})` : message;
}

function formatAt(iso: string): string {
  const date = iso ? new Date(iso) : null;
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' })
    : iso || '–';
}

const SECTION_CLASS =
  'space-y-3 rounded-xl border border-black/[0.08] bg-white/30 p-4 dark:border-white/[0.08] dark:bg-white/[0.03]';
const BUTTON_CLASS =
  'inline-flex items-center gap-1.5 rounded-lg border border-black/10 px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-white/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/15 dark:text-slate-200 dark:hover:bg-white/10';
const PRIMARY_CLASS =
  'inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50';

function DeliveryResult({ delivery }: { delivery: WebhookDelivery }) {
  const { t } = useTranslation();
  const ok = delivery.status === 'delivered';
  return (
    <div className="min-w-0">
      <span
        className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
          ok
            ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
            : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
        }`}
      >
        {ok
          ? t('integrations.webhook.deliveries.delivered')
          : t('integrations.webhook.deliveries.failed')}
        {delivery.http_status !== null && ` · ${delivery.http_status}`}
      </span>
      {delivery.error && (
        <p className="mt-0.5 break-words text-[10px] text-red-600 dark:text-red-400">
          {delivery.error}
        </p>
      )}
    </div>
  );
}

interface DeliveriesSectionProps {
  deliveries: WebhookDelivery[];
  loading: boolean;
  /** A failed (re)load: the table may be out of date. */
  loadError: unknown;
  onRefresh: () => void;
}

/** Recent deliveries, with the reason when refreshing them failed. */
export function DeliveriesSection({
  deliveries,
  loading,
  loadError,
  onRefresh,
}: DeliveriesSectionProps) {
  const { t } = useTranslation();
  return (
    <div className={SECTION_CLASS}>
      <div className="flex items-center gap-2">
        <h4 className="flex-1 text-sm font-medium text-slate-700 dark:text-slate-200">
          {t('integrations.webhook.deliveries.title')}
        </h4>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className={BUTTON_CLASS}
          aria-label={t('integrations.webhook.deliveries.refresh')}
          title={t('integrations.webhook.deliveries.refresh')}
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`}
          />
        </button>
      </div>
      {loadError != null && (
        <p
          role="alert"
          data-testid="webhook-reload-error"
          className="break-words rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
        >
          {t('integrations.webhook.errors.reload', {
            message: describeWebhookError(t, loadError),
          })}
        </p>
      )}
      {deliveries.length === 0 ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {t('integrations.webhook.deliveries.empty')}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table
            className="w-full text-left text-[11px] text-slate-600 dark:text-slate-300"
            data-testid="webhook-deliveries"
          >
            <thead className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
              <tr>
                <th scope="col" className="py-1 pr-2 font-semibold">
                  {t('integrations.webhook.deliveries.at')}
                </th>
                <th scope="col" className="py-1 pr-2 font-semibold">
                  {t('integrations.webhook.deliveries.event')}
                </th>
                <th scope="col" className="py-1 pr-2 font-semibold">
                  {t('integrations.webhook.deliveries.applicant')}
                </th>
                <th scope="col" className="py-1 font-semibold">
                  {t('integrations.webhook.deliveries.result')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[0.06] dark:divide-white/[0.06]">
              {deliveries.map((d) => (
                <tr key={d.delivery_id} className="align-top">
                  <td
                    className="whitespace-nowrap py-1.5 pr-2 tabular-nums"
                    title={d.delivery_id}
                  >
                    {formatAt(d.at)}
                  </td>
                  <td className="py-1.5 pr-2 font-mono text-[10px]">
                    {d.event || '–'}
                  </td>
                  <td className="break-words py-1.5 pr-2">
                    {d.applicant || '–'}
                  </td>
                  <td className="py-1.5">
                    <DeliveryResult delivery={d} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-slate-400">
        {t('integrations.webhook.deliveries.retention')}
      </p>
    </div>
  );
}

interface WebhookSettingsProps {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  projectId: string;
}

/** Project settings › Integrations: the CallVarta / CRM webhook. */
export default function WebhookSettings({
  fetchApi,
  projectId,
}: WebhookSettingsProps) {
  const { t } = useTranslation();
  const urlId = useId();
  const urlHintId = useId();
  const enabledId = useId();
  const secretId = useId();
  const {
    settings,
    loading,
    loadError,
    load,
    saving,
    saveError,
    savedAt,
    save,
    secret,
    secretBusy,
    secretError,
    generateSecret,
    dismissSecret,
    testing,
    testResult,
    testError,
    sendTest,
  } = useWebhookSettings({ fetchApi, projectId });

  const [url, setUrl] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [secretCopy, setSecretCopy] = useState<CopyOutcome | null>(null);
  const secretInputRef = useRef<HTMLInputElement>(null);
  const generateRef = useRef<HTMLButtonElement>(null);
  const replaceRef = useRef<HTMLButtonElement>(null);
  const hadSecretRef = useRef(false);

  useEffect(() => {
    load();
  }, [load]);

  // Show the server's values unless the user is editing them.
  useEffect(() => {
    if (settings && !dirty) {
      setUrl(settings.url ?? '');
      setEnabled(settings.enabled);
    }
  }, [settings, dirty]);

  // A new secret is shown once: move focus to it, and back to the button
  // once it is dismissed.
  useEffect(() => {
    setSecretCopy(null);
    if (secret) secretInputRef.current?.focus();
    else if (hadSecretRef.current) generateRef.current?.focus();
    hadSecretRef.current = !!secret;
  }, [secret]);

  useEffect(() => {
    if (confirmReplace) replaceRef.current?.focus();
  }, [confirmReplace]);

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (saving) return;
    const ok = await save({ url: webhookUrlValue(url), enabled });
    if (ok) setDirty(false);
  };

  const handleGenerate = async () => {
    if (settings?.secret_set && !confirmReplace) {
      setConfirmReplace(true);
      return;
    }
    setConfirmReplace(false);
    await generateSecret();
  };

  const urlValue = url.trim();
  const httpsWarning = urlValue !== '' && !looksLikeHttpsUrl(urlValue);
  const canTest = !!settings?.url && !!settings?.secret_set;
  const deliveries = settings?.deliveries ?? [];

  if (!settings) {
    return (
      <div className={SECTION_CLASS}>
        {loadError != null ? (
          <div role="alert" className="space-y-2 text-xs">
            <p className="text-red-600 dark:text-red-400">
              {t('integrations.webhook.errors.load', {
                message: describeWebhookError(t, loadError),
              })}
            </p>
            <button
              type="button"
              onClick={() => load()}
              className={BUTTON_CLASS}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              {t('fileCheck.retry')}
            </button>
          </div>
        ) : (
          <p className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('integrations.webhook.loading')}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="webhook-settings">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 text-white">
          <Webhook className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            {t('integrations.webhook.title')}
          </h3>
          <p className="text-xs leading-snug text-slate-500 dark:text-slate-400">
            {t('integrations.webhook.intro')}
          </p>
        </div>
      </div>

      {/* URL and on/off */}
      {/* noValidate: the API checks the URL and says why it refuses one. */}
      <form onSubmit={handleSave} noValidate className={SECTION_CLASS}>
        <div className="bento-form-group">
          <label htmlFor={urlId} className="bento-form-label">
            {t('integrations.webhook.url')}
          </label>
          <input
            id={urlId}
            type="url"
            inputMode="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setDirty(true);
            }}
            placeholder="https://crm.example.com/hooks/idp"
            maxLength={2048}
            autoComplete="off"
            spellCheck={false}
            aria-describedby={urlHintId}
            aria-invalid={httpsWarning}
            className="bento-form-input"
          />
          <p
            id={urlHintId}
            className={
              httpsWarning
                ? 'bento-form-hint !text-amber-700 dark:!text-amber-400'
                : 'bento-form-hint'
            }
          >
            {httpsWarning
              ? t('integrations.webhook.urlNotHttps')
              : t('integrations.webhook.urlHint')}
          </p>
        </div>

        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p
              id={enabledId}
              className="text-sm font-medium text-slate-700 dark:text-slate-200"
            >
              {t('integrations.webhook.enabled')}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t('integrations.webhook.enabledHint')}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-labelledby={enabledId}
            onClick={() => {
              setEnabled((v) => !v);
              setDirty(true);
            }}
            className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 ${
              enabled ? 'bg-indigo-600' : 'bg-slate-300 dark:bg-slate-600'
            }`}
          >
            <span
              aria-hidden="true"
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                enabled ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>
        {enabled && !settings.secret_set && (
          <p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            {t('integrations.webhook.needsSecret')}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={saving || !dirty}
            className={PRIMARY_CLASS}
          >
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {saving ? t('common.saving') : t('integrations.webhook.save')}
          </button>
          <p
            role="status"
            className="text-xs text-slate-500 dark:text-slate-400"
          >
            {dirty
              ? t('integrations.webhook.unsaved')
              : savedAt
                ? t('integrations.webhook.saved', {
                    time: savedAt.toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    }),
                  })
                : ''}
          </p>
        </div>
        {saveError != null && (
          <p
            role="alert"
            className="break-words rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
          >
            {t('integrations.webhook.errors.save', {
              message: describeWebhookError(t, saveError),
            })}
          </p>
        )}
      </form>

      {/* Signing secret */}
      <div className={SECTION_CLASS}>
        <div className="flex flex-wrap items-center gap-2">
          <KeyRound
            className="h-4 w-4 text-slate-500 dark:text-slate-400"
            aria-hidden="true"
          />
          <h4 className="text-sm font-medium text-slate-700 dark:text-slate-200">
            {t('integrations.webhook.secret.title')}
          </h4>
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
              settings.secret_set
                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                : 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-300'
            }`}
          >
            {settings.secret_set
              ? t('integrations.webhook.secret.set')
              : t('integrations.webhook.secret.notSet')}
          </span>
        </div>

        {secret ? (
          <div
            role="alert"
            className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-700/50 dark:bg-amber-900/20"
          >
            <p className="flex items-start gap-1.5 text-xs font-semibold text-amber-800 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
              {t('integrations.webhook.secret.once')}
            </p>
            <label htmlFor={secretId} className="sr-only">
              {t('integrations.webhook.secret.title')}
            </label>
            <div className="flex gap-2">
              <input
                id={secretId}
                ref={secretInputRef}
                readOnly
                value={secret}
                onFocus={(e) => e.currentTarget.select()}
                spellCheck={false}
                autoComplete="off"
                className="bento-form-input min-w-0 flex-1 font-mono text-xs"
              />
              <button
                type="button"
                onClick={async () =>
                  setSecretCopy(await copyText(secret, secretInputRef.current))
                }
                className={PRIMARY_CLASS}
              >
                {secretCopy === 'copied' ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                {secretCopy === 'copied'
                  ? t('common.copied')
                  : t('common.copy')}
              </button>
            </div>
            {secretCopy === 'manual' && (
              <p className="text-xs text-amber-800 dark:text-amber-300">
                {t('integrations.webhook.secret.copyManual')}
              </p>
            )}
            <button
              type="button"
              onClick={dismissSecret}
              className={BUTTON_CLASS}
            >
              {t('integrations.webhook.secret.done')}
            </button>
          </div>
        ) : confirmReplace ? (
          <div
            role="alertdialog"
            aria-label={t('integrations.webhook.secret.replaceTitle')}
            className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-700/50 dark:bg-amber-900/20 dark:text-amber-200"
          >
            <p>{t('integrations.webhook.secret.confirmReplace')}</p>
            <div className="flex gap-2">
              <button
                ref={replaceRef}
                type="button"
                onClick={handleGenerate}
                disabled={secretBusy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-1 disabled:opacity-50"
              >
                {secretBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {t('integrations.webhook.secret.replace')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmReplace(false);
                  setTimeout(() => generateRef.current?.focus(), 0);
                }}
                disabled={secretBusy}
                className={BUTTON_CLASS}
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            <button
              ref={generateRef}
              type="button"
              onClick={handleGenerate}
              disabled={secretBusy}
              className={BUTTON_CLASS}
            >
              {secretBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <KeyRound className="h-3.5 w-3.5" />
              )}
              {settings.secret_set
                ? t('integrations.webhook.secret.regenerate')
                : t('integrations.webhook.secret.generate')}
            </button>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t('integrations.webhook.secret.hint')}
            </p>
          </div>
        )}
        {secretError != null && (
          <p
            role="alert"
            className="break-words text-xs text-red-600 dark:text-red-400"
          >
            {t('integrations.webhook.errors.secret', {
              message: describeWebhookError(t, secretError),
            })}
          </p>
        )}
      </div>

      {/* Test event */}
      <div className={SECTION_CLASS}>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={sendTest}
            disabled={!canTest || testing}
            className={BUTTON_CLASS}
          >
            {testing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
            {testing
              ? t('integrations.webhook.test.sending')
              : t('integrations.webhook.test.send')}
          </button>
          <p role="status" className="min-w-0 flex-1 text-xs">
            {testResult ? (
              testResult.status === 'delivered' ? (
                <span className="text-green-700 dark:text-green-400">
                  {testResult.http_status !== null
                    ? t('integrations.webhook.test.delivered', {
                        status: testResult.http_status,
                      })
                    : t('integrations.webhook.test.deliveredNoStatus')}
                </span>
              ) : (
                <span className="text-red-600 dark:text-red-400">
                  {[
                    testResult.http_status !== null
                      ? t('integrations.webhook.test.failedStatus', {
                          status: testResult.http_status,
                        })
                      : t('integrations.webhook.test.failed'),
                    testResult.error,
                  ]
                    .filter(Boolean)
                    .join(' – ')}
                </span>
              )
            ) : testError != null ? (
              <span className="break-words text-red-600 dark:text-red-400">
                {describeWebhookError(t, testError)}
              </span>
            ) : (
              <span className="text-slate-500 dark:text-slate-400">
                {canTest
                  ? t('integrations.webhook.test.hint')
                  : t('integrations.webhook.test.needsSetup')}
              </span>
            )}
          </p>
        </div>
        {testResult?.delivery_id && (
          <p className="font-mono text-[10px] text-slate-400">
            {t('integrations.webhook.test.deliveryId', {
              id: testResult.delivery_id,
            })}
          </p>
        )}
      </div>

      {/* Deliveries */}
      <DeliveriesSection
        deliveries={deliveries}
        loading={loading}
        loadError={loadError}
        onRefresh={() => load()}
      />

      {/* How the receiver checks a delivery */}
      <div className={SECTION_CLASS} data-testid="webhook-verify">
        <h4 className="text-sm font-medium text-slate-700 dark:text-slate-200">
          {t('integrations.webhook.verify.title')}
        </h4>
        <p className="text-xs leading-snug text-slate-600 dark:text-slate-300">
          {t('integrations.webhook.verify.intro')}
        </p>
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-slate-900 px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-100">
          {[
            'X-DocAI-Event: file_check.completed',
            'X-DocAI-Delivery: <delivery id>',
            'X-DocAI-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>',
          ].join('\n')}
        </pre>
        <p className="text-xs leading-snug text-slate-600 dark:text-slate-300">
          {t('integrations.webhook.verify.steps')}
        </p>
        <details className="text-xs">
          <summary className="cursor-pointer select-none font-medium text-slate-600 dark:text-slate-300">
            {t('integrations.webhook.verify.example')}
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-100">
            {WEBHOOK_VERIFY_SNIPPET}
          </pre>
        </details>
      </div>
    </div>
  );
}
