// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Runtime configuration, read from config.json next to index.html (no rebuild per environment).
//
// {
//   "region": "ap-south-1",
//   "userPoolId": "ap-south-1_XXXXXXXXX",          the Document AI (IDP) user pool: same logins
//   "userPoolClientId": "xxxxxxxxxxxxxxxxxxxxxxxxxx",  an app client of that pool with USER_SRP_AUTH
//   "websocketUrl": "/ws",                           or "wss://<voice CloudFront>/ws"
//   "idpAppUrl": "https://<Document AI CloudFront>",  link back to the IDP app (optional)
//   "qaProjectId": "",                               Telecaller QA project id (optional, for the link)
//   "pipeline": "transcribe-polly",                  sent as ?pipeline= ("" = not sent)
//   "defaultLanguage": "hi-IN",                      en-IN | hi-IN | mr-IN
//   "maxCallSeconds": 600,                           call cap (= the bot's CALL_MAX_SECONDS); the browser
//                                                    hangs up itself 30 s later, after the bot's goodbye
//   "tokenTransport": "subprotocol",                 or "query" (?token=) for the original sample backend
//   "outboundCalls": false,                          true shows "Ring a phone" (POST /calls/outbound)
//   "outboundUrl": ""                                default: /calls/outbound on the WebSocket's host
// }
//
// Also accepted: the legacy aws-exports.json shape ({amplify: {Auth: {Cognito}}, websocket: {apiUrl}})
// and the IDP runtime-config.json names (cognitoProps.userPoolWebClientId).

export const LANGUAGE_CODES = Object.freeze(['en-IN', 'hi-IN', 'mr-IN']);

export const DEFAULTS = Object.freeze({
    region: 'ap-south-1',
    websocketUrl: '/ws',
    idpAppUrl: '',
    qaProjectId: '',
    pipeline: 'transcribe-polly',
    defaultLanguage: 'hi-IN',
    maxCallSeconds: 600,
    tokenTransport: 'subprotocol',
    outboundCalls: false,
    outboundUrl: '',
});

export const PROVIDERS = Object.freeze(['plivo', 'exotel']);

const POOL_ID = /^([a-z]{2}(?:-[a-z]+)+-\d+)_[A-Za-z0-9]+$/;
const CLIENT_ID = /^[A-Za-z0-9]{8,128}$/;
const PLACEHOLDER = /^(your|replace|change|todo|xxx|<|\$\{)|^userPool(Client)?Id$|^placeholder|_x{5,}$/i;
const PLACEHOLDER_HOST = /replace|your[-_]|placeholder|example\.(com|invalid)/i;
const LOCAL_HOST = /^(localhost|127\.0\.0\.1|\[::1\])$/;

function pick(...values) {
    for (const v of values) {
        if (typeof v === 'string' && v.trim() !== '') return v.trim();
    }
    return undefined;
}

function flatten(raw) {
    const cognito = raw?.amplify?.Auth?.Cognito ?? raw?.Auth?.Cognito ?? {};
    const props = raw?.cognitoProps ?? raw?.cognito ?? {};
    return {
        region: pick(raw.region, props.region, cognito.region),
        userPoolId: pick(raw.userPoolId, props.userPoolId, cognito.userPoolId),
        userPoolClientId: pick(
            raw.userPoolClientId,
            raw.appClientId,
            raw.clientId,
            raw.userPoolWebClientId,
            props.userPoolClientId,
            props.userPoolWebClientId,
            props.appClientId,
            cognito.userPoolClientId,
        ),
        websocketUrl: pick(raw.websocketUrl, raw.wsUrl, raw?.websocket?.apiUrl, raw?.websocket?.url),
        idpAppUrl: pick(raw.idpAppUrl, raw.idpUrl, raw?.links?.idpApp),
        qaProjectId: pick(raw.qaProjectId, raw?.links?.qaProjectId),
        qaProjectUrl: pick(raw.qaProjectUrl, raw?.links?.qaProject),
        pipeline: raw.pipeline === '' || raw.pipeline === null ? '' : pick(raw.pipeline),
        defaultLanguage: pick(raw.defaultLanguage, raw.language),
        maxCallSeconds: raw.maxCallSeconds,
        examples: raw.examples,
        tokenTransport: pick(raw.tokenTransport),
        outboundCalls: raw.outboundCalls ?? raw?.outbound?.enabled,
        outboundUrl: pick(raw.outboundUrl, raw?.outbound?.url),
        providers: raw.providers ?? raw?.outbound?.providers,
    };
}

/** Optional https link: a bad value only drops the link (warning), it never blocks calls. */
function checkHttpUrl(value, name, warnings) {
    if (!value) return '';
    try {
        const url = new URL(value);
        if (PLACEHOLDER_HOST.test(url.hostname)) {
            warnings.push(`${name} ignored: it still holds a placeholder`);
            return '';
        }
        if (url.protocol === 'https:' || (url.protocol === 'http:' && LOCAL_HOST.test(url.hostname))) {
            return url.toString().replace(/\/+$/, '');
        }
        warnings.push(`${name} ignored: it must be an https:// address`);
    } catch {
        warnings.push(`${name} ignored: not a valid URL`);
    }
    return '';
}

function checkSocketUrl(value, pageHref, errors) {
    let url;
    try {
        url = new URL(value, pageHref || 'https://localhost/');
    } catch {
        errors.push('websocketUrl is not a valid URL');
        return value;
    }
    const scheme = url.protocol.replace(/:$/, '');
    const pageSecure = !pageHref || pageHref.startsWith('https:');
    if (!['ws', 'wss', 'http', 'https'].includes(scheme)) {
        errors.push('websocketUrl must start with wss:// (or be a path such as /ws)');
    } else if (pageSecure && (scheme === 'ws' || scheme === 'http') && !LOCAL_HOST.test(url.hostname)) {
        errors.push('websocketUrl must use wss:// on an https page');
    }
    return value;
}

function cleanExamples(examples) {
    if (!examples || typeof examples !== 'object') return undefined;
    const out = {};
    for (const code of LANGUAGE_CODES) {
        const list = examples[code];
        if (Array.isArray(list)) {
            const lines = list.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim()).slice(0, 6);
            if (lines.length) out[code] = lines;
        }
    }
    return Object.keys(out).length ? out : undefined;
}

/**
 * Validates and normalises a raw config object.
 * @returns {{config: object|null, errors: string[], warnings: string[]}}
 */
export function normalizeConfig(raw, { pageHref } = {}) {
    const errors = [];
    const warnings = [];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        return { config: null, errors: ['config.json is not a JSON object'], warnings };
    }
    const f = flatten(raw);

    const userPoolId = f.userPoolId;
    if (!userPoolId || PLACEHOLDER.test(userPoolId) || !POOL_ID.test(userPoolId)) {
        errors.push('userPoolId is missing or not a Cognito user pool id (e.g. ap-south-1_AbC123xyz)');
    }
    const userPoolClientId = f.userPoolClientId;
    if (!userPoolClientId || PLACEHOLDER.test(userPoolClientId) || !CLIENT_ID.test(userPoolClientId)) {
        errors.push('userPoolClientId is missing or not a Cognito app client id');
    }
    const poolRegion = userPoolId && POOL_ID.test(userPoolId) ? userPoolId.match(POOL_ID)[1] : undefined;
    const region = f.region || poolRegion || DEFAULTS.region;
    if (poolRegion && region !== poolRegion) {
        errors.push(`region ${region} does not match the user pool region ${poolRegion}`);
    }
    if (region !== 'ap-south-1') warnings.push(`region is ${region}, not Mumbai (ap-south-1)`);

    const websocketUrl = checkSocketUrl(f.websocketUrl || DEFAULTS.websocketUrl, pageHref, errors);
    const idpAppUrl = checkHttpUrl(f.idpAppUrl, 'idpAppUrl', warnings);
    const qaProjectId = f.qaProjectId && /^[A-Za-z0-9_-]{1,128}$/.test(f.qaProjectId) ? f.qaProjectId : '';
    if (f.qaProjectId && !qaProjectId) warnings.push('qaProjectId ignored: unexpected characters');
    let qaProjectUrl = checkHttpUrl(f.qaProjectUrl, 'qaProjectUrl', warnings);
    if (!qaProjectUrl && idpAppUrl && qaProjectId) qaProjectUrl = `${idpAppUrl}/projects/${encodeURIComponent(qaProjectId)}`;

    const pipeline = f.pipeline === undefined ? DEFAULTS.pipeline : f.pipeline;
    const defaultLanguage = LANGUAGE_CODES.includes(f.defaultLanguage) ? f.defaultLanguage : DEFAULTS.defaultLanguage;
    if (f.defaultLanguage && f.defaultLanguage !== defaultLanguage) {
        warnings.push(`defaultLanguage ${f.defaultLanguage} is not one of ${LANGUAGE_CODES.join(', ')}`);
    }
    let maxCallSeconds = Number(f.maxCallSeconds ?? DEFAULTS.maxCallSeconds);
    if (!Number.isFinite(maxCallSeconds)) maxCallSeconds = DEFAULTS.maxCallSeconds;
    maxCallSeconds = Math.min(3600, Math.max(60, Math.round(maxCallSeconds)));

    let tokenTransport = f.tokenTransport || DEFAULTS.tokenTransport;
    if (tokenTransport !== 'subprotocol' && tokenTransport !== 'query') {
        warnings.push(`tokenTransport ${tokenTransport} is not "subprotocol" or "query"`);
        tokenTransport = DEFAULTS.tokenTransport;
    }
    const outboundCalls = f.outboundCalls === true || f.outboundCalls === 'true';
    let outboundUrl = '';
    if (f.outboundUrl) {
        if (/^\/(?!\/)/.test(f.outboundUrl)) outboundUrl = f.outboundUrl;
        else outboundUrl = checkHttpUrl(f.outboundUrl, 'outboundUrl', warnings);
    }
    const providers = Array.isArray(f.providers)
        ? PROVIDERS.filter((p) => f.providers.includes(p))
        : [...PROVIDERS];

    if (errors.length) return { config: null, errors, warnings };
    return {
        config: Object.freeze({
            region,
            userPoolId,
            userPoolClientId,
            websocketUrl,
            idpAppUrl,
            qaProjectId,
            qaProjectUrl,
            pipeline,
            defaultLanguage,
            maxCallSeconds,
            examples: cleanExamples(f.examples),
            tokenTransport,
            outboundCalls,
            outboundUrl,
            providers: Object.freeze(providers),
        }),
        errors,
        warnings,
    };
}

async function fetchJson(fetchImpl, url) {
    let response;
    try {
        response = await fetchImpl(url, { cache: 'no-store', credentials: 'same-origin' });
    } catch {
        return { status: 'network' };
    }
    if (!response.ok) return { status: 'missing' };
    const text = await response.text();
    // CloudFront answers unknown paths with index.html (200): that is "missing", not "broken".
    if (/^\s*</.test(text)) return { status: 'missing' };
    try {
        return { status: 'ok', json: JSON.parse(text) };
    } catch {
        return { status: 'invalid' };
    }
}

/**
 * Loads config.json (then the legacy aws-exports.json) from the app's base URL.
 * @returns {Promise<{config: object|null, errors: string[], warnings: string[], source: string}>}
 */
export async function loadConfig({ fetchImpl, baseUrl, pageHref }) {
    const base = new URL(baseUrl || '/', pageHref);
    for (const name of ['config.json', 'aws-exports.json']) {
        const url = new URL(name, base).toString();
        const got = await fetchJson(fetchImpl, url);
        if (got.status === 'ok') return { ...normalizeConfig(got.json, { pageHref }), source: name };
        if (got.status === 'invalid') return { config: null, errors: [`${name} is not valid JSON`], warnings: [], source: name };
        if (got.status === 'network') {
            return { config: null, errors: [`${name} could not be downloaded (network error)`], warnings: [], source: name };
        }
    }
    return {
        config: null,
        errors: ['config.json was not found next to index.html'],
        warnings: [],
        source: '',
    };
}
