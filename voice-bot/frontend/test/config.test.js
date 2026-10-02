// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULTS, loadConfig, normalizeConfig } from '../src/lib/config.js';

const PAGE = 'https://d2voice.cloudfront.net/';
const GOOD = {
    region: 'ap-south-1',
    userPoolId: 'ap-south-1_EXAMPLE01',
    userPoolClientId: '1exampleclientid0abcdefgh2',
    websocketUrl: '/ws',
    idpAppUrl: 'https://d111111abcdef8.cloudfront.net/',
    qaProjectId: 'proj_123',
};

test('flat config.json: values, defaults and the Telecaller QA link', () => {
    const { config, errors } = normalizeConfig(GOOD, { pageHref: PAGE });
    assert.deepEqual(errors, []);
    assert.equal(config.region, 'ap-south-1');
    assert.equal(config.userPoolId, GOOD.userPoolId);
    assert.equal(config.userPoolClientId, GOOD.userPoolClientId);
    assert.equal(config.websocketUrl, '/ws');
    assert.equal(config.idpAppUrl, 'https://d111111abcdef8.cloudfront.net');
    assert.equal(config.qaProjectUrl, 'https://d111111abcdef8.cloudfront.net/projects/proj_123');
    assert.equal(config.pipeline, DEFAULTS.pipeline);
    assert.equal(config.defaultLanguage, 'hi-IN');
    assert.equal(config.maxCallSeconds, 600);
    assert.ok(Object.isFrozen(config));
});

test('legacy aws-exports.json shape (what infra writes today) is accepted', () => {
    const legacy = {
        amplify: {
            Auth: {
                Cognito: {
                    userPoolClientId: '1exampleclientid0abcdefgh2',
                    userPoolId: 'ap-south-1_EXAMPLE01',
                    identityPoolId: 'ap-south-1:00000000-0000-4000-8000-000000000001',
                    region: 'ap-south-1',
                },
            },
        },
        websocket: { apiUrl: 'wss://d2voice.cloudfront.net/ws' },
    };
    const { config, errors } = normalizeConfig(legacy, { pageHref: PAGE });
    assert.deepEqual(errors, []);
    assert.equal(config.websocketUrl, 'wss://d2voice.cloudfront.net/ws');
    assert.equal(config.userPoolClientId, '1exampleclientid0abcdefgh2');
});

test('IDP runtime-config.json names (cognitoProps.userPoolWebClientId) are accepted', () => {
    const { config, errors } = normalizeConfig(
        { cognitoProps: { region: 'ap-south-1', userPoolId: 'ap-south-1_EXAMPLE01', userPoolWebClientId: '1exampleclientid0abcdefgh2' } },
        { pageHref: PAGE },
    );
    assert.deepEqual(errors, []);
    assert.equal(config.websocketUrl, '/ws', 'defaults to the same CloudFront /ws path');
});

test('placeholders and the shipped example are rejected (nothing half-configured goes live)', () => {
    const sampleAwsExports = { amplify: { Auth: { Cognito: { userPoolClientId: 'userPoolClientId', userPoolId: 'userPoolId' } } } };
    assert.equal(normalizeConfig(sampleAwsExports).config, null);
    assert.equal(normalizeConfig({ userPoolId: 'YOUR_COGNITO_USER_POOL_ID', userPoolClientId: 'abc12345abc' }).config, null);
    const example = JSON.parse(readFileSync(new URL('../public/config.example.json', import.meta.url), 'utf8'));
    const result = normalizeConfig(example, { pageHref: PAGE });
    assert.equal(result.config, null);
    assert.ok(result.errors.some((e) => e.startsWith('userPoolId')));
    assert.ok(result.errors.some((e) => e.startsWith('userPoolClientId')));
    assert.equal(normalizeConfig(null).config, null);
    assert.equal(normalizeConfig([]).config, null);
});

test('socket URL rules: wss on https pages, ws only for localhost', () => {
    assert.ok(normalizeConfig({ ...GOOD, websocketUrl: 'ws://voice.example.in/ws' }, { pageHref: PAGE }).errors.length);
    assert.deepEqual(normalizeConfig({ ...GOOD, websocketUrl: 'ws://localhost:8080/ws' }, { pageHref: PAGE }).errors, []);
    assert.deepEqual(normalizeConfig({ ...GOOD, websocketUrl: 'ws://10.0.0.5:8080/ws' }, { pageHref: 'http://10.0.0.5:3000/' }).errors, []);
    assert.ok(normalizeConfig({ ...GOOD, websocketUrl: 'ftp://x/ws' }, { pageHref: PAGE }).errors.length);
});

test('region must match the pool; non-Mumbai region warns', () => {
    assert.ok(normalizeConfig({ ...GOOD, region: 'us-east-1' }, { pageHref: PAGE }).errors.some((e) => e.includes('does not match')));
    const other = normalizeConfig({ ...GOOD, region: undefined, userPoolId: 'us-east-1_5ONWYOkFW' }, { pageHref: PAGE });
    assert.equal(other.config.region, 'us-east-1');
    assert.ok(other.warnings.some((w) => w.includes('not Mumbai')));
});

test('optional values are cleaned', () => {
    const { config, warnings } = normalizeConfig(
        {
            ...GOOD,
            maxCallSeconds: 99999,
            defaultLanguage: 'ta-IN',
            pipeline: '',
            qaProjectId: '../../etc',
            idpAppUrl: 'https://REPLACE_WITH_DOCUMENT_AI_DOMAIN',
            examples: { 'hi-IN': ['  Kya baaki hai?  ', 5, ''], 'xx-YY': ['no'] },
        },
        { pageHref: PAGE },
    );
    assert.equal(config.maxCallSeconds, 3600);
    assert.equal(config.defaultLanguage, 'hi-IN');
    assert.equal(config.pipeline, '');
    assert.equal(config.qaProjectId, '');
    assert.equal(config.idpAppUrl, '');
    assert.equal(config.qaProjectUrl, '');
    assert.deepEqual(config.examples, { 'hi-IN': ['Kya baaki hai?'] });
    assert.ok(warnings.length >= 3);
    assert.equal(normalizeConfig({ ...GOOD, maxCallSeconds: 5 }, { pageHref: PAGE }).config.maxCallSeconds, 60);
    const plainHttp = normalizeConfig({ ...GOOD, idpAppUrl: 'http://evil.example.org' }, { pageHref: PAGE });
    assert.equal(plainHttp.config.idpAppUrl, '', 'non-https link dropped, calls still work');
    assert.ok(plainHttp.warnings.some((w) => w.startsWith('idpAppUrl')));
});

function fakeFetch(routes) {
    return async (url) => {
        const path = new URL(url).pathname;
        const route = routes[path];
        if (route === 'network') throw new TypeError('Failed to fetch');
        if (route === undefined) return { ok: false, status: 404, text: async () => '' };
        return { ok: true, status: 200, text: async () => route };
    };
}

test('loadConfig: config.json first, CloudFront index.html fallback treated as missing', async () => {
    const ok = await loadConfig({ fetchImpl: fakeFetch({ '/config.json': JSON.stringify(GOOD) }), baseUrl: '/', pageHref: PAGE });
    assert.equal(ok.source, 'config.json');
    assert.ok(ok.config);

    const legacy = await loadConfig({
        fetchImpl: fakeFetch({
            '/config.json': '<!doctype html><html></html>',
            '/aws-exports.json': JSON.stringify({ amplify: { Auth: { Cognito: GOOD } }, websocket: { apiUrl: '/ws' } }),
        }),
        baseUrl: '/',
        pageHref: PAGE,
    });
    assert.equal(legacy.source, 'aws-exports.json');
    assert.ok(legacy.config);

    const missing = await loadConfig({ fetchImpl: fakeFetch({}), baseUrl: '/', pageHref: PAGE });
    assert.equal(missing.config, null);
    assert.match(missing.errors[0], /not found/);

    const broken = await loadConfig({ fetchImpl: fakeFetch({ '/config.json': '{oops' }), baseUrl: '/', pageHref: PAGE });
    assert.match(broken.errors[0], /not valid JSON/);

    const offline = await loadConfig({ fetchImpl: fakeFetch({ '/config.json': 'network' }), baseUrl: '/', pageHref: PAGE });
    assert.match(offline.errors[0], /network/);

    const sub = await loadConfig({
        fetchImpl: fakeFetch({ '/voice/config.json': JSON.stringify(GOOD) }),
        baseUrl: '/voice/',
        pageHref: 'https://x.cloudfront.net/voice/',
    });
    assert.ok(sub.config, 'served under a sub-path');
});

test('token transport and outbound calls', () => {
    const base = normalizeConfig(GOOD, { pageHref: PAGE }).config;
    assert.equal(base.tokenTransport, 'subprotocol');
    assert.equal(base.outboundCalls, false);
    assert.deepEqual([...base.providers], ['plivo', 'exotel']);
    const legacy = normalizeConfig({ ...GOOD, tokenTransport: 'query' }, { pageHref: PAGE }).config;
    assert.equal(legacy.tokenTransport, 'query');
    const odd = normalizeConfig({ ...GOOD, tokenTransport: 'cookie' }, { pageHref: PAGE });
    assert.equal(odd.config.tokenTransport, 'subprotocol');
    assert.ok(odd.warnings.some((w) => w.includes('tokenTransport')));
    const out = normalizeConfig(
        { ...GOOD, outboundCalls: true, outboundUrl: '/calls/outbound', providers: ['exotel', 'twilio'] },
        { pageHref: PAGE },
    ).config;
    assert.equal(out.outboundCalls, true);
    assert.equal(out.outboundUrl, '/calls/outbound');
    assert.deepEqual([...out.providers], ['exotel']);
    assert.equal(normalizeConfig({ ...GOOD, outboundUrl: '//evil.example.org/x' }, { pageHref: PAGE }).config.outboundUrl, '');
    assert.equal(
        normalizeConfig({ ...GOOD, outboundUrl: 'https://voice.callvarta.in/calls/outbound' }, { pageHref: PAGE }).config.outboundUrl,
        'https://voice.callvarta.in/calls/outbound',
    );
});
