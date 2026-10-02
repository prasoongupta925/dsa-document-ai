// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NO_CALL_ERRORS, closeOutcome } from '../src/lib/outcome.js';

test('backend close codes map to clear outcomes (main.py /ws)', () => {
    assert.deepEqual(closeOutcome(4001, 'Sign in again', { opened: true }), { error: 'errAuth' }, 'token refused after accept');
    assert.deepEqual(closeOutcome(1013, 'Busy, try again', { opened: true }), { error: 'errBusy' });
    assert.deepEqual(closeOutcome(1008, 'Unsupported pipeline', { opened: true }), { error: 'errRejected', detail: 'Unsupported pipeline' });
    assert.equal(closeOutcome(4003, '', {}).error, 'errRejected');
    assert.deepEqual(closeOutcome(1006, '', { opened: false }), { error: 'errConnect' }, 'handshake failed');
    assert.deepEqual(closeOutcome(1011, '', { opened: true }), { error: 'errServer' });
    assert.deepEqual(closeOutcome(1000, '', { opened: true }), { reason: 'assistant', detail: '' });
    assert.deepEqual(closeOutcome(1006, '', { opened: true, assistantEnded: true, endedBy: 'time_cap' }), {
        reason: 'assistant',
        detail: 'time_cap',
    });
    assert.deepEqual(closeOutcome(1006, '', { opened: true }), { error: 'errDropped' });
    assert.ok(NO_CALL_ERRORS.includes('errAuth') && !NO_CALL_ERRORS.includes('errDropped'));
});
