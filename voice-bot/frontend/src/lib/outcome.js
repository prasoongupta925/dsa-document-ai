// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Maps a WebSocket close to the call outcome. The backend accepts first and then checks the token,
 * so 4001 can arrive after "open". Codes: 4001 sign in again, 4003 origin not allowed,
 * 1013 busy (all call slots taken), 1008 policy (e.g. unsupported pipeline), 1011 server error.
 */
export function closeOutcome(code, reason, state) {
    const { opened = false, assistantEnded = false, endedBy = '', serverError = '' } = state || {};
    if (code === 4001) return { error: 'errAuth' };
    if (code === 1013) return { error: 'errBusy' };
    if (code === 4003) return { error: 'errRejected', detail: reason || 'this website is not on the allowed list' };
    if (code === 1008) return { error: 'errRejected', detail: reason || serverError || 'policy' };
    if (!opened) return { error: 'errConnect' };
    if (code === 1011) return { error: 'errServer' };
    if (assistantEnded || code === 1000 || code === 1001 || code === 1005) return { reason: 'assistant', detail: endedBy };
    return { error: 'errDropped' };
}

/** Outcomes after which no post-call card is shown (no conversation took place). */
export const NO_CALL_ERRORS = Object.freeze([
    'errAuth',
    'errBusy',
    'errRejected',
    'errConnect',
    'errMicDenied',
    'errMicMissing',
    'errMicBusy',
    'errInsecure',
    'errUnsupported',
    'errAudio',
]);
