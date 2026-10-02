// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { useState } from 'react';
import { fetchAuthSession } from 'aws-amplify/auth';
import { t } from '../lib/i18n.js';
import { PROVIDER_NAMES, maskNumber, normalizeIndianNumber, outboundOutcome, shortLanguage } from '../lib/phone.js';
import { httpUrlOnSocketHost } from '../lib/protocol.js';

/**
 * "Ring a phone": asks the voice backend (POST /calls/outbound) to have Plivo or Exotel call the
 * applicant; the bot talks when they answer. The backend enforces the consent list, the calling
 * window and the daily attempt cap. Shown only when config.json sets "outboundCalls": true.
 */
export default function OutboundPanel({ config, lang }) {
    const [applicant, setApplicant] = useState('');
    const [phone, setPhone] = useState('');
    const [provider, setProvider] = useState('');
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState(null); // { key, vars, ok }

    const submit = async (event) => {
        event.preventDefault();
        const name = applicant.trim().replace(/\s+/g, ' ');
        const to = normalizeIndianNumber(phone);
        if (name.split(' ').length < 2) return setMessage({ key: 'nameMissing' });
        if (!to) return setMessage({ key: 'phoneInvalid' });
        setBusy(true);
        setMessage({ key: 'ringing', info: true });
        try {
            const token = (await fetchAuthSession()).tokens?.idToken?.toString();
            if (!token) {
                setMessage({ key: 'errAuth' });
                return;
            }
            const url = config.outboundUrl
                ? new URL(config.outboundUrl, window.location.href).toString()
                : httpUrlOnSocketHost(config.websocketUrl, '/calls/outbound', window.location.href);
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({ to, applicant: name, language: shortLanguage(lang), provider }),
                cache: 'no-store',
                credentials: 'omit',
            });
            let data = null;
            try {
                data = JSON.parse(await response.text());
            } catch {
                data = null;
            }
            const outcome = outboundOutcome(response.status, data);
            if (outcome.ok) outcome.vars.number = maskNumber(to);
            setMessage(outcome);
        } catch {
            setMessage({ key: 'ringFailed' });
        } finally {
            setBusy(false);
        }
    };

    return (
        <details className="outbound">
            <summary>{t(lang, 'outboundTitle')}</summary>
            <form onSubmit={submit} noValidate>
                <p className="muted small">{t(lang, 'outboundHelp')}</p>
                <label>
                    <span>{t(lang, 'applicantLabel')}</span>
                    <input
                        value={applicant}
                        onChange={(e) => setApplicant(e.target.value)}
                        autoComplete="off"
                        placeholder="Sneha Kulkarni"
                        maxLength={80}
                    />
                </label>
                <label>
                    <span>{t(lang, 'phoneLabel')}</span>
                    <input
                        type="tel"
                        inputMode="tel"
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                        autoComplete="off"
                        placeholder="+91 98XXXXXXXX"
                        maxLength={20}
                    />
                </label>
                <label>
                    <span>{t(lang, 'providerLabel')}</span>
                    <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                        <option value="">{t(lang, 'providerAuto')}</option>
                        {config.providers.map((p) => (
                            <option key={p} value={p}>
                                {PROVIDER_NAMES[p]}
                            </option>
                        ))}
                    </select>
                </label>
                <button type="submit" className="ring-btn" disabled={busy}>
                    {busy ? t(lang, 'ringing') : t(lang, 'ringNow')}
                </button>
                {message ? (
                    <p className={`outbound-msg${message.ok ? ' ok' : message.info ? '' : ' bad'}`} role="status">
                        {t(lang, message.key, message.vars)}
                    </p>
                ) : null}
            </form>
        </details>
    );
}
