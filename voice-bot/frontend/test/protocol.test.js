// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    MessageType,
    base64ToBytes,
    SUBPROTOCOL,
    buildSocketUrl,
    bytesLEToInt16,
    callLanguage,
    httpUrlOnSocketHost,
    bytesToBase64,
    encodeMediaMessage,
    int16ToBytesLE,
    parseServerMessage,
    resolveSocketUrl,
} from '../src/lib/protocol.js';

test('message names match backend/JsonSerializer.py MessageType', () => {
    assert.deepEqual(
        { ...MessageType },
        { MEDIA: 'media', INTERRUPTION: 'interruption', USER_TRANSCRIPT: 'user_transcript', BOT_TRANSCRIPT: 'bot_transcript' },
    );
});

test('mic frame is exactly {"event":"media","data":base64(PCM16 little-endian)}', () => {
    const samples = Int16Array.from([0, 1, -1, 32767, -32768, 256]);
    const text = encodeMediaMessage(samples);
    const msg = JSON.parse(text);
    assert.deepEqual(Object.keys(msg), ['event', 'data']);
    assert.equal(msg.event, 'media');
    // Same bytes as Python struct.pack('<6h', 0, 1, -1, 32767, -32768, 256)
    const expected = Buffer.from([0, 0, 1, 0, 255, 255, 255, 127, 0, 128, 0, 1]).toString('base64');
    assert.equal(msg.data, expected);
    assert.equal(text, `{"event":"media","data":"${expected}"}`);
});

test('base64 helpers match Node Buffer for large payloads', () => {
    const bytes = new Uint8Array(100003);
    for (let i = 0; i < bytes.length; i++) bytes[i] = (i * 7919) & 255;
    const b64 = bytesToBase64(bytes);
    assert.equal(b64, Buffer.from(bytes).toString('base64'));
    assert.deepEqual(base64ToBytes(b64), bytes);
});

test('PCM16 LE bytes round-trip, odd trailing byte ignored, unaligned views copied', () => {
    const samples = Int16Array.from([12345, -12345, 0, -1]);
    const bytes = int16ToBytesLE(samples);
    assert.deepEqual(Array.from(bytesLEToInt16(bytes)), Array.from(samples));
    const odd = new Uint8Array([1, 0, 2, 0, 9]);
    assert.deepEqual(Array.from(bytesLEToInt16(odd)), [1, 2]);
    const backing = new Uint8Array(5);
    backing.set([1, 0, 2, 0], 1);
    assert.deepEqual(Array.from(bytesLEToInt16(backing.subarray(1))), [1, 2]);
});

test('bot audio frame decodes to the same samples', () => {
    const pcm = Buffer.from(Int16Array.from([100, -200, 300]).buffer).toString('base64');
    const m = parseServerMessage(JSON.stringify({ event: 'media', data: pcm }));
    assert.equal(m.kind, 'media');
    assert.deepEqual(Array.from(m.samples), [100, -200, 300]);
    assert.equal(parseServerMessage('{"event":"media","data":"%%%"}').kind, 'invalid');
    assert.equal(parseServerMessage('{"event":"media","data":null}').kind, 'invalid');
});

test('interruption frame (JsonSerializer) and stop/clear aliases', () => {
    assert.deepEqual(parseServerMessage('{"event": "interruption", "data": null}'), { kind: 'interruption' });
    assert.equal(parseServerMessage('{"event":"stop"}').kind, 'interruption');
    assert.equal(parseServerMessage('{"event":"clear"}').kind, 'interruption');
});

test('captions from the Mumbai backend: one message per turn, text at top level', () => {
    assert.deepEqual(parseServerMessage('{"event":"user_transcript","text":"Sneha Kulkarni ki file mein kya baaki hai?"}'), {
        kind: 'caption',
        role: 'user',
        text: 'Sneha Kulkarni ki file mein kya baaki hai?',
        final: true,
        turn: true,
        interrupted: false,
    });
    assert.deepEqual(parseServerMessage('{"event":"bot_transcript","text":"Aapki file mein","interrupted":true}'), {
        kind: 'caption',
        role: 'bot',
        text: 'Aapki file mein',
        final: true,
        turn: true,
        interrupted: true,
    });
});

test('captions in other shapes: data string/object, RTVI parts, transcript messages', () => {
    const legacy = parseServerMessage('{"event":"user_transcript","data":"Sneha Kulkarni ki file"}');
    assert.equal(legacy.text, 'Sneha Kulkarni ki file');
    assert.equal(legacy.turn, true);
    const interim = parseServerMessage('{"event":"user_transcript","data":{"text":"Sneha","final":false}}');
    assert.equal(interim.final, false);
    assert.equal(interim.turn, false, 'segments with a final flag join the open bubble');
    assert.equal(parseServerMessage('{"event":"bot_transcript","data":{"text":"  Namaste!  "}}').text, 'Namaste!');
    const rtvi = parseServerMessage('{"label":"rtvi-ai","type":"user-transcription","data":{"text":"hi","final":false}}');
    assert.equal(rtvi.final, false);
    const rtviBot = parseServerMessage('{"label":"rtvi-ai","type":"bot-transcription","data":{"text":"Hello"}}');
    assert.equal(rtviBot.role, 'bot');
    assert.equal(rtviBot.turn, false, 'RTVI bot text comes per sentence');
    assert.equal(parseServerMessage('{"event":"transcript","data":{"role":"assistant","content":"Ji"}}').role, 'bot');
    assert.equal(parseServerMessage('{"event":"transcript","data":{"role":"user","content":"Haan"}}').role, 'user');
    assert.equal(parseServerMessage('{"event":"transcript","data":{"role":"system","content":"x"}}').kind, 'ignored');
    assert.equal(parseServerMessage('{"event":"bot_transcript","text":"   "}').kind, 'ignored');
    assert.equal(
        parseServerMessage('{"event":"bot_transcript","text":"<thinking>check file</thinking>Aapki file mein"}').text,
        'Aapki file mein',
    );
    assert.equal(parseServerMessage(JSON.stringify({ event: 'bot_transcript', text: 'x'.repeat(9000) })).text.length, 4000);
});

test('bot lookups and their results become timeline events', () => {
    assert.deepEqual(parseServerMessage('{"event":"tool","name":"file_status","status":"started"}'), {
        kind: 'tool',
        name: 'file_status',
        status: 'started',
    });
    assert.deepEqual(
        parseServerMessage(
            JSON.stringify({ event: 'file_status', verdict: 'NOT READY', missing: ['June 2026 salary slip', 5, ''], mismatches: 1 }),
        ),
        { kind: 'result', card: { type: 'file_status', verdict: 'NOT READY', missing: ['June 2026 salary slip'], mismatches: 1 } },
    );
    assert.deepEqual(parseServerMessage('{"event":"eligibility","best_lender":"Demo Bank","amount":"4.5 lakh"}'), {
        kind: 'result',
        card: { type: 'eligibility', bestLender: 'Demo Bank', amount: '4.5 lakh' },
    });
    assert.deepEqual(parseServerMessage('{"event":"eligibility","best_lender":null,"amount":null}').card, {
        type: 'eligibility',
        bestLender: '',
        amount: '',
    });
    const reminder = parseServerMessage(
        JSON.stringify({
            event: 'reminder',
            template: 'T1',
            language: 'hi',
            channel: 'whatsapp',
            text: 'नमस्ते Sneha, बाकी documents: June 2026 salary slip',
            placeholders: ['upload_link'],
        }),
    );
    assert.equal(reminder.card.type, 'reminder');
    assert.equal(reminder.card.language, 'hi-IN');
    assert.deepEqual(reminder.card.placeholders, ['upload_link']);
    assert.deepEqual(
        parseServerMessage('{"event":"reminder","text":"x","placeholders":{"upload_link":"…"}}').card.placeholders,
        ['upload_link'],
    );
    assert.equal(parseServerMessage('{"event":"reminder","text":""}').kind, 'ignored');
});

test('language switch, call start/end and recording events', () => {
    assert.deepEqual(parseServerMessage('{"event":"language","language":"mr"}'), { kind: 'language', language: 'mr-IN' });
    assert.equal(parseServerMessage('{"event":"language","language":"ta"}').kind, 'ignored');
    assert.deepEqual(parseServerMessage('{"event":"call_started","call_id":"c-123","language":"hi"}'), {
        kind: 'callStarted',
        callId: 'c-123',
        language: 'hi-IN',
    });
    assert.deepEqual(parseServerMessage('{"event":"call_ended","reason":"time_cap"}'), { kind: 'callEnded', reason: 'time_cap' });
    assert.deepEqual(parseServerMessage('{"event":"recording","status":"uploaded","document_id":"d1"}'), {
        kind: 'recording',
        status: 'uploaded',
        documentId: 'd1',
    });
    assert.equal(callLanguage('Hindi'), 'hi-IN');
    assert.equal(callLanguage('en-IN'), 'en-IN');
    assert.equal(callLanguage(undefined), null);
});

test('end / error / unknown / invalid / binary', () => {
    assert.deepEqual(parseServerMessage('{"event":"end_call","data":{"reason":"done"}}'), { kind: 'end', reason: 'done' });
    assert.equal(SUBPROTOCOL, 'voicebot.v1');
    assert.deepEqual(parseServerMessage('{"event":"error","data":"boom"}'), { kind: 'error', message: 'boom' });
    assert.deepEqual(parseServerMessage('{"event":"metrics","data":{}}'), { kind: 'unknown', type: 'metrics' });
    assert.equal(parseServerMessage('not json').kind, 'invalid');
    assert.equal(parseServerMessage('[1,2]').kind, 'invalid');
    assert.equal(parseServerMessage(new ArrayBuffer(4)).kind, 'ignored');
});

test('connect URL: language and pipeline (token only in the query fallback)', () => {
    const url = buildSocketUrl(
        '/ws',
        { token: 'eyJ.abc-_.sig', language: 'hi-IN', pipeline: 'transcribe-polly' },
        'https://d123.cloudfront.net/index.html',
    );
    assert.equal(url, 'wss://d123.cloudfront.net/ws?token=eyJ.abc-_.sig&language=hi-IN&pipeline=transcribe-polly');
    assert.equal(
        buildSocketUrl('/ws', { token: 't', language: 'mr-IN', pipeline: '' }, 'http://localhost:3000/'),
        'ws://localhost:3000/ws?token=t&language=mr-IN',
    );
    assert.equal(
        buildSocketUrl('wss://voice.example.in/ws?x=1', { token: 't', language: 'en-IN' }, 'https://a.b/'),
        'wss://voice.example.in/ws?x=1&token=t&language=en-IN',
    );
    assert.throws(() => resolveSocketUrl('ftp://x/ws', 'https://a.b/'));
    assert.equal(httpUrlOnSocketHost('/ws', '/calls/outbound', 'https://d2.cloudfront.net/'), 'https://d2.cloudfront.net/calls/outbound');
    assert.equal(httpUrlOnSocketHost('ws://localhost:8080/ws?a=1', '/calls/outbound', 'http://localhost:3000/'), 'http://localhost:8080/calls/outbound');
});
