import { createFileRoute } from '@tanstack/react-router';
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Power, PowerOff, Clock, RefreshCw, Server } from 'lucide-react';
import { useAwsClient } from '../hooks/useAwsClient';

export const Route = createFileRoute('/settings')({
  component: SettingsPage,
});

const AMAZON_SOFTWARE_LICENSE = `Amazon Software License

1. Definitions

"Licensor" means any person or entity that distributes its Work under this license.
"Software" means the original work of authorship made available under this license.
"Work" means the Software and any additions to or derivative works of the Software that are made available under this license.
The terms "reproduce," "reproduction," "derivative works," and "distribution" have the meaning as provided under U.S. copyright law; provided, however, that for the purposes of this license, derivative works shall not include works that remain separable from, or merely link (or bind by name) to the interfaces of, the Work.
Works, including the Software, are "made available" under this license by including in or with the Work either (a) a copyright notice referencing the applicability of this license to the Work, or (b) a copy of this license.

2. License Grants

2.1 Copyright Grant. Subject to the terms and conditions of this license, each Licensor grants to you a perpetual, worldwide, non-exclusive, royalty-free, copyright license to reproduce, prepare derivative works of, publicly display, publicly perform, sublicense and distribute its Work and any resulting derivative works in any form.

2.2 Patent Grant. Subject to the terms and conditions of this license, each Licensor grants to you a perpetual, worldwide, non-exclusive, royalty-free patent license to make, have made, use, sell, offer for sale, import, and otherwise transfer its Work, in whole or in part. The foregoing license applies only to the patent claims licensable by Licensor that would be infringed by Licensor's Work (or portion thereof) individually and excluding any combinations with any other materials or technology.

3. Limitations

3.1 Redistribution. You may reproduce or distribute the Work only if (a) you do so under this license, (b) you include a complete copy of this license with your distribution, and (c) you retain without modification any copyright, patent, trademark, or attribution notices that are present in the Work.

3.2 Derivative Works. You may specify that additional or different terms apply to the use, reproduction, and distribution of your derivative works of the Work ("Your Terms") only if (a) Your Terms provide that the use limitation in Section 3.3 applies to your derivative works, and (b) you identify the specific derivative works that are subject to Your Terms. Notwithstanding Your Terms, this license (including the redistribution requirements in Section 3.1) will continue to apply to the Work itself.

3.3 Use Limitation. The Work and any derivative works thereof only may be used or intended for use with the web services, computing platforms or applications provided by Amazon.com, Inc. or its affiliates, including Amazon Web Services, Inc.

3.4 Patent Claims. If you bring or threaten to bring a patent claim against any Licensor (including any claim, cross-claim or counterclaim in a lawsuit) to enforce any patents that you allege are infringed by any Work, then your rights under this license from such Licensor (including the grants in Sections 2.1 and 2.2) will terminate immediately.

3.5 Trademarks. This license does not grant any rights to use any Licensor's or its affiliates' names, logos, or trademarks, except as necessary to reproduce the notices described in this license.

3.6 Termination. If you violate any term of this license, then your rights under this license (including the grants in Sections 2.1 and 2.2) will terminate immediately.

4. Disclaimer of Warranty.

THE WORK IS PROVIDED "AS IS" WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED, INCLUDING WARRANTIES OR CONDITIONS OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE OR NON-INFRINGEMENT. YOU BEAR THE RISK OF UNDERTAKING ANY ACTIVITIES UNDER THIS LICENSE. SOME STATES' CONSUMER LAWS DO NOT ALLOW EXCLUSION OF AN IMPLIED WARRANTY, SO THIS DISCLAIMER MAY NOT APPLY TO YOU.

5. Limitation of Liability.

EXCEPT AS PROHIBITED BY APPLICABLE LAW, IN NO EVENT AND UNDER NO LEGAL THEORY, WHETHER IN TORT (INCLUDING NEGLIGENCE), CONTRACT, OR OTHERWISE SHALL ANY LICENSOR BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES ARISING OUT OF OR RELATED TO THIS LICENSE, THE USE OR INABILITY TO USE THE WORK (INCLUDING BUT NOT LIMITED TO LOSS OF GOODWILL, BUSINESS INTERRUPTION, LOST PROFITS OR DATA, COMPUTER FAILURE OR MALFUNCTION, OR ANY OTHER COMMERCIAL DAMAGES OR LOSSES), EVEN IF THE LICENSOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

Effective Date - April 18, 2008 (c) 2008 Amazon.com, Inc. or its affiliates. All rights reserved.`;

type SettingsSection = 'sagemaker' | 'license';

interface EndpointStatus {
  endpoint_name: string;
  status: string;
  current_instance_count: number;
  desired_instance_count: number;
}

interface ScaleInSettings {
  evaluation_periods: number;
}

function SettingsPage() {
  const { t } = useTranslation();
  const { fetchApi } = useAwsClient();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>('sagemaker');

  // SageMaker state
  const [endpointStatus, setEndpointStatus] = useState<EndpointStatus | null>(
    null,
  );
  const [scaleInSettings, setScaleInSettings] =
    useState<ScaleInSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingMinutes, setEditingMinutes] = useState<number | null>(null);

  const fetchSageMakerStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [status, settings] = await Promise.all([
        fetchApi<EndpointStatus>('sagemaker/status'),
        fetchApi<ScaleInSettings>('sagemaker/settings'),
      ]);
      setEndpointStatus(status);
      setScaleInSettings(settings);
      setEditingMinutes(settings.evaluation_periods);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch status');
    } finally {
      setLoading(false);
    }
  }, [fetchApi]);

  useEffect(() => {
    if (activeSection === 'sagemaker') {
      fetchSageMakerStatus();
    }
  }, [activeSection, fetchSageMakerStatus]);

  const handleStartEndpoint = async () => {
    setActionLoading(true);
    try {
      await fetchApi('sagemaker/start', { method: 'POST' });
      await fetchSageMakerStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start endpoint');
    } finally {
      setActionLoading(false);
    }
  };

  const handleStopEndpoint = async () => {
    setActionLoading(true);
    try {
      await fetchApi('sagemaker/stop', { method: 'POST' });
      await fetchSageMakerStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop endpoint');
    } finally {
      setActionLoading(false);
    }
  };

  const handleSaveSettings = async () => {
    if (
      editingMinutes === null ||
      editingMinutes === scaleInSettings?.evaluation_periods
    )
      return;
    setActionLoading(true);
    try {
      const result = await fetchApi<ScaleInSettings>('sagemaker/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ evaluation_periods: editingMinutes }),
      });
      setScaleInSettings(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setActionLoading(false);
    }
  };

  const menuItems: {
    key: SettingsSection;
    label: string;
    icon: React.ReactNode;
  }[] = [
    {
      key: 'sagemaker',
      label: t('settings.sagemaker', 'SageMaker'),
      icon: <Server className="w-5 h-5" />,
    },
    {
      key: 'license',
      label: t('settings.license'),
      icon: (
        <svg
          className="w-5 h-5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="16" y1="13" x2="8" y2="13" />
          <line x1="16" y1="17" x2="8" y2="17" />
          <polyline points="10 9 9 9 8 9" />
        </svg>
      ),
    },
  ];

  return (
    <div className="min-h-screen bento-page" style={{ paddingTop: '0.5rem' }}>
      <div className="flex gap-6" style={{ minHeight: 'calc(100vh - 120px)' }}>
        {/* Side Menu */}
        <div className="flex flex-col w-56 flex-shrink-0">
          <div style={{ padding: '0.5rem 0 1.25rem' }}>
            <div className="bento-hero-label" style={{ marginBottom: '1rem' }}>
              <span className="bento-hero-accent" />
              <span>{t('nav.settings')}</span>
            </div>
            <h1
              className="bento-hero-title"
              style={{ fontSize: '2rem', margin: 0 }}
            >
              {t('settings.heroTitle')}
            </h1>
          </div>
          <nav className="flex flex-col gap-1">
            {menuItems.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setActiveSection(item.key)}
                className={`flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium transition-all text-left ${
                  activeSection === item.key
                    ? 'bg-[var(--color-accent-light)] text-[var(--color-accent)]'
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]'
                }`}
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </div>

        {/* Content */}
        <div className="flex-1 pt-1">
          {activeSection === 'sagemaker' && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3
                  className="text-base font-semibold"
                  style={{ color: 'var(--color-text-primary)' }}
                >
                  {t('settings.sagemakerTitle', 'OCR Endpoint Management')}
                </h3>
                <button
                  onClick={fetchSageMakerStatus}
                  disabled={loading}
                  className="bento-btn-cancel flex items-center gap-1.5"
                  style={{ padding: '0.375rem 0.75rem' }}
                >
                  <RefreshCw
                    className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`}
                  />
                  <span>{t('common.refresh', 'Refresh')}</span>
                </button>
              </div>

              {error && (
                <div
                  className="mb-4 p-3 rounded-lg text-sm"
                  style={{
                    background: 'rgba(239, 68, 68, 0.1)',
                    color: '#ef4444',
                    border: '1px solid rgba(239, 68, 68, 0.2)',
                  }}
                >
                  {error}
                </div>
              )}

              {/* Endpoint Status */}
              <div
                className="rounded-lg p-5 mb-4"
                style={{
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        endpointStatus?.current_instance_count &&
                        endpointStatus.current_instance_count > 0
                          ? 'bg-green-500'
                          : endpointStatus?.status === 'Updating'
                            ? 'bg-yellow-500 animate-pulse'
                            : 'bg-slate-400'
                      }`}
                    />
                    <span
                      className="text-sm"
                      style={{ color: 'var(--color-text-secondary)' }}
                    >
                      {endpointStatus?.endpoint_name || 'Loading...'}
                    </span>
                  </div>
                  <span
                    className="text-xs px-2 py-0.5 rounded"
                    style={{
                      background:
                        endpointStatus?.status === 'InService'
                          ? 'var(--color-accent-light)'
                          : endpointStatus?.status === 'Updating'
                            ? 'rgba(234, 179, 8, 0.15)'
                            : 'var(--color-bg-tertiary)',
                      color:
                        endpointStatus?.status === 'InService'
                          ? 'var(--color-accent)'
                          : endpointStatus?.status === 'Updating'
                            ? '#ca8a04'
                            : 'var(--color-text-muted)',
                    }}
                  >
                    {endpointStatus?.status || '-'}
                  </span>
                </div>

                <div className="flex gap-3 mb-5">
                  <div
                    className="flex-1 p-3 rounded-lg text-center"
                    style={{ background: 'var(--color-bg-tertiary)' }}
                  >
                    <div
                      className="text-xs mb-1"
                      style={{ color: 'var(--color-text-muted)' }}
                    >
                      {t('settings.currentInstances', 'Current Instances')}
                    </div>
                    <div
                      className="text-xl font-semibold"
                      style={{ color: 'var(--color-text-primary)' }}
                    >
                      {endpointStatus?.current_instance_count ?? '-'}
                    </div>
                  </div>
                  <div
                    className="flex-1 p-3 rounded-lg text-center"
                    style={{ background: 'var(--color-bg-tertiary)' }}
                  >
                    <div
                      className="text-xs mb-1"
                      style={{ color: 'var(--color-text-muted)' }}
                    >
                      {t('settings.desiredInstances', 'Desired Instances')}
                    </div>
                    <div
                      className="text-xl font-semibold"
                      style={{ color: 'var(--color-text-primary)' }}
                    >
                      {endpointStatus?.desired_instance_count ?? '-'}
                    </div>
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={handleStartEndpoint}
                    disabled={
                      actionLoading || endpointStatus?.status === 'Updating'
                    }
                    className="bento-btn-save flex-1 flex items-center justify-center gap-1.5"
                  >
                    <Power className="w-3.5 h-3.5" />
                    {t('settings.startEndpoint', 'Start')}
                  </button>
                  <button
                    onClick={handleStopEndpoint}
                    disabled={
                      actionLoading || endpointStatus?.status === 'Updating'
                    }
                    className="bento-btn-cancel flex-1 flex items-center justify-center gap-1.5"
                  >
                    <PowerOff className="w-3.5 h-3.5" />
                    {t('settings.stopEndpoint', 'Stop')}
                  </button>
                </div>
              </div>

              {/* Scale-in Settings */}
              <div
                className="rounded-lg p-5"
                style={{
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <Clock
                    className="w-4 h-4"
                    style={{ color: 'var(--color-text-muted)' }}
                  />
                  <h4
                    className="text-sm font-medium"
                    style={{ color: 'var(--color-text-primary)' }}
                  >
                    {t('settings.scaleInSettings', 'Auto Scale-in Settings')}
                  </h4>
                </div>

                <p
                  className="text-xs mb-4"
                  style={{ color: 'var(--color-text-muted)' }}
                >
                  {t(
                    'settings.scaleInDescription',
                    'Endpoint will automatically stop after this period of inactivity',
                  )}
                </p>

                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={editingMinutes ?? ''}
                    onChange={(e) =>
                      setEditingMinutes(parseInt(e.target.value) || 1)
                    }
                    className="w-16 px-2 py-1.5 rounded text-sm text-center"
                    style={{
                      background: 'var(--color-bg-tertiary)',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-text-primary)',
                    }}
                  />
                  <span
                    className="text-sm"
                    style={{ color: 'var(--color-text-secondary)' }}
                  >
                    {t('settings.minutes', 'minutes')}
                  </span>
                  <button
                    onClick={handleSaveSettings}
                    disabled={
                      actionLoading ||
                      editingMinutes === null ||
                      editingMinutes === scaleInSettings?.evaluation_periods
                    }
                    className="bento-btn-save ml-auto"
                  >
                    {t('common.save', 'Save')}
                  </button>
                </div>
              </div>
            </div>
          )}

          {activeSection === 'license' && (
            <div>
              <h3
                className="text-base font-semibold mb-4"
                style={{ color: 'var(--color-text-primary)' }}
              >
                {t('settings.licenseTitle')}
              </h3>
              <pre
                className="text-sm leading-relaxed whitespace-pre-wrap rounded-lg p-6 overflow-auto"
                style={{
                  color: 'var(--color-text-secondary)',
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border)',
                  maxHeight: 'calc(100vh - 180px)',
                  fontFamily: 'inherit',
                }}
              >
                {AMAZON_SOFTWARE_LICENSE}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
