import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  WebhookSecretResponse,
  WebhookSettings,
  WebhookTestResult,
  WebhookUpdateRequest,
} from '../types/integrations';
import {
  parseWebhookSecret,
  parseWebhookSettings,
  parseWebhookTestResult,
} from '../lib/integrations';

interface UseWebhookSettingsOptions {
  fetchApi: <T>(url: string, init?: RequestInit) => Promise<T>;
  projectId: string;
}

/**
 * The project's outgoing webhook (prefix projects/{id}/integrations). The
 * signing secret is only in memory while shown: POST .../webhook/secret
 * returns it once, and GET never does.
 */
export function useWebhookSettings({
  fetchApi,
  projectId,
}: UseWebhookSettingsOptions) {
  const [settings, setSettings] = useState<WebhookSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [secretBusy, setSecretBusy] = useState(false);
  const [secretError, setSecretError] = useState<unknown>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<WebhookTestResult | null>(null);
  const [testError, setTestError] = useState<unknown>(null);

  const base = `projects/${projectId}/integrations/webhook`;
  const loadSeq = useRef(0);
  const projectRef = useRef(projectId);

  useEffect(() => {
    if (projectRef.current === projectId) return;
    projectRef.current = projectId;
    loadSeq.current += 1;
    setSettings(null);
    setLoadError(null);
    setSaveError(null);
    setSavedAt(null);
    setSecret(null);
    setSecretError(null);
    setTestResult(null);
    setTestError(null);
  }, [projectId]);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setLoadError(null);
    try {
      const raw = await fetchApi<unknown>(base);
      if (seq !== loadSeq.current) return;
      setSettings(parseWebhookSettings(raw));
    } catch (err) {
      if (seq !== loadSeq.current) return;
      console.error('Failed to load webhook settings:', err);
      setLoadError(err);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [fetchApi, base]);

  /** PUT; a 400 (invalid URL) keeps the form and shows the API's detail. */
  const save = useCallback(
    async (update: WebhookUpdateRequest): Promise<boolean> => {
      setSaving(true);
      setSaveError(null);
      try {
        const raw = await fetchApi<unknown>(base, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(update),
        });
        loadSeq.current += 1;
        setSettings(parseWebhookSettings(raw));
        setSavedAt(new Date());
        return true;
      } catch (err) {
        console.error('Failed to save webhook settings:', err);
        setSaveError(err);
        return false;
      } finally {
        setSaving(false);
      }
    },
    [fetchApi, base],
  );

  /** Creates (or replaces) the signing secret; the response is its only copy. */
  const generateSecret = useCallback(async (): Promise<boolean> => {
    setSecretBusy(true);
    setSecretError(null);
    try {
      const raw = await fetchApi<WebhookSecretResponse>(`${base}/secret`, {
        method: 'POST',
      });
      setSecret(parseWebhookSecret(raw));
      setSettings((s) => (s ? { ...s, secret_set: true } : s));
      return true;
    } catch (err) {
      console.error('Failed to generate the webhook secret:', err);
      setSecretError(err);
      return false;
    } finally {
      setSecretBusy(false);
    }
  }, [fetchApi, base]);

  /** Forgets the secret shown once. */
  const dismissSecret = useCallback(() => setSecret(null), []);

  /** Sends a test event, then reloads the deliveries. */
  const sendTest = useCallback(async () => {
    setTesting(true);
    setTestError(null);
    setTestResult(null);
    try {
      const raw = await fetchApi<unknown>(`${base}/test`, { method: 'POST' });
      setTestResult(parseWebhookTestResult(raw));
    } catch (err) {
      console.error('Webhook test failed:', err);
      setTestError(err);
    } finally {
      setTesting(false);
    }
    load();
  }, [fetchApi, base, load]);

  return {
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
  };
}

export type WebhookSettingsState = ReturnType<typeof useWebhookSettings>;
