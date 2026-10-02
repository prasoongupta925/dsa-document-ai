// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Helpers for the "Ring a phone" form (backend POST /calls/outbound). Numbers are never stored.

export const PROVIDER_NAMES = Object.freeze({ plivo: 'Plivo', exotel: 'Exotel' });

/** "98765 43210", "098765...", "91 98765...", "+91-98765..." -> "+91" + "9876543210" (null if not Indian). */
export function normalizeIndianNumber(value) {
    const raw = String(value || '').trim();
    const digits = raw.replace(/\D/g, '');
    if (raw.startsWith('+')) return /^91\d{10}$/.test(digits) ? `+${digits}` : null;
    if (/^\d{10}$/.test(digits)) return `+91${digits}`;
    if (/^0\d{10}$/.test(digits)) return `+91${digits.slice(1)}`;
    if (/^91\d{10}$/.test(digits)) return `+${digits}`;
    return null;
}

/** "+91" + "9876543210" -> "+91 98XXXXX210" for on-screen confirmations. */
export function maskNumber(e164) {
    if (!/^\+91\d{10}$/.test(e164 || '')) return '';
    return `+91 ${e164.slice(3, 5)}XXXXX${e164.slice(-3)}`;
}

/** "hi-IN" -> "hi" (the outbound API takes short codes). */
export function shortLanguage(code) {
    return String(code || 'hi').slice(0, 2).toLowerCase();
}

/**
 * Maps the outbound API answer to a UI message key.
 * @returns {{key: string, vars?: object, ok?: boolean}}
 */
export function outboundOutcome(status, data) {
    if (!data || typeof data !== 'object') return { key: 'ringOff' };
    if (status === 202 || status === 200) {
        const provider = PROVIDER_NAMES[data.provider] || String(data.provider || '').slice(0, 20);
        return { key: 'ringOk', ok: true, vars: { provider, ref: String(data.request_id || '').slice(0, 12) } };
    }
    if (status === 401) return { key: 'errAuth' };
    if (status === 409 || status === 400) return { key: 'ringRefused', vars: { reason: String(data.detail || '').slice(0, 200) } };
    if (status === 403 || status === 404 || status === 405) return { key: 'ringOff' };
    return { key: 'ringFailed' };
}
