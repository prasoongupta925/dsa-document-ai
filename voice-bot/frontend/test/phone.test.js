// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { maskNumber, normalizeIndianNumber, outboundOutcome, shortLanguage } from '../src/lib/phone.js';

test('Indian numbers are normalised to +91XXXXXXXXXX', () => {
    for (const input of ['9876543210', '98765 43210', '098765-43210', '91' + '9876543210', '+91 98765 43210', '+91-9876543210']) {
        assert.equal(normalizeIndianNumber(input), '+91' + '9876543210', input);
    }
    for (const input of ['', '12345', '+1 415 555 0100', '+92 9876543210', 'abc', '98765432101']) {
        assert.equal(normalizeIndianNumber(input), null, input);
    }
});

test('masking and language codes', () => {
    assert.equal(maskNumber('+91' + '9876543210'), '+91 98XXXXX210');
    assert.equal(maskNumber('12'), '');
    assert.equal(shortLanguage('mr-IN'), 'mr');
    assert.equal(shortLanguage(''), 'hi');
});

test('outbound API answers map to messages', () => {
    assert.deepEqual(outboundOutcome(202, { provider: 'plivo', request_id: 'abcdef1234567890' }), {
        key: 'ringOk',
        ok: true,
        vars: { provider: 'Plivo', ref: 'abcdef123456' },
    });
    assert.deepEqual(outboundOutcome(409, { detail: 'outside the calling window (10:00-19:00 IST)' }), {
        key: 'ringRefused',
        vars: { reason: 'outside the calling window (10:00-19:00 IST)' },
    });
    assert.equal(outboundOutcome(401, { detail: 'sign in first' }).key, 'errAuth');
    assert.equal(outboundOutcome(200, null).key, 'ringOff', 'CloudFront index.html instead of the API');
    assert.equal(outboundOutcome(404, {}).key, 'ringOff');
    assert.equal(outboundOutcome(502, { detail: 'x' }).key, 'ringFailed');
});
