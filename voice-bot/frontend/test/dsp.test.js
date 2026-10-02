// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    Chunker,
    PlaybackQueue,
    Resampler,
    WIRE_SAMPLE_RATE,
    designLowpass,
    floatToInt16,
    int16ToFloat,
    levelToMeter,
    rms,
} from '../src/audio/dsp.js';

function tone(freq, rate, seconds, amp = 0.5) {
    const n = Math.round(rate * seconds);
    const x = new Float32Array(n);
    for (let i = 0; i < n; i++) x[i] = amp * Math.sin((2 * Math.PI * freq * i) / rate);
    return x;
}

/** Amplitude of the `freq` component (single-bin DFT over the steady part). */
function amplitudeAt(x, freq, rate, skip = 200) {
    let re = 0;
    let im = 0;
    let n = 0;
    for (let i = skip; i < x.length - skip; i++, n++) {
        re += x[i] * Math.cos((2 * Math.PI * freq * i) / rate);
        im += x[i] * Math.sin((2 * Math.PI * freq * i) / rate);
    }
    return (2 * Math.hypot(re, im)) / n;
}

function runInBlocks(resampler, x, sizes) {
    const out = [];
    let i = 0;
    let k = 0;
    while (i < x.length) {
        const size = sizes[k++ % sizes.length];
        const y = resampler.process(x.subarray(i, i + size));
        out.push(...y);
        i += size;
    }
    return Float32Array.from(out);
}

test('wire rate is 16 kHz (backend SAMPLE_RATE)', () => {
    assert.equal(WIRE_SAMPLE_RATE, 16000);
});

test('48 kHz -> 16 kHz keeps a 1 kHz tone at 1 kHz with unity gain', () => {
    const x = tone(1000, 48000, 1);
    const y = runInBlocks(new Resampler(48000, 16000), x, [128]);
    assert.ok(Math.abs(y.length - 16000) <= 2, `length ${y.length}`);
    const a1k = amplitudeAt(y, 1000, 16000);
    assert.ok(Math.abs(a1k - 0.5) < 0.01, `1 kHz amplitude ${a1k}`);
    // A mislabelled rate would move the tone (e.g. to 3 kHz); nothing must be there.
    assert.ok(amplitudeAt(y, 3000, 16000) < 0.005);
});

test('44.1 kHz -> 16 kHz: right length and pitch', () => {
    const x = tone(1000, 44100, 1);
    const y = runInBlocks(new Resampler(44100, 16000), x, [128]);
    assert.ok(Math.abs(y.length - 16000) <= 2, `length ${y.length}`);
    assert.ok(Math.abs(amplitudeAt(y, 1000, 16000) - 0.5) < 0.01);
});

test('anti-alias filter: tones above 8 kHz do not fold into the speech band', () => {
    for (const freq of [9000, 11000, 15000]) {
        const y = runInBlocks(new Resampler(48000, 16000), tone(freq, 48000, 1), [128]);
        const alias = 16000 - freq; // where an unfiltered tone would land
        const amp = amplitudeAt(y, alias, 16000);
        assert.ok(amp < 0.5 * 10 ** (-40 / 20), `${freq} Hz leaked ${amp} at ${alias} Hz`);
    }
    // The speech band stays: 3.4 kHz passes almost untouched.
    const y = runInBlocks(new Resampler(48000, 16000), tone(3400, 48000, 1), [128]);
    assert.ok(amplitudeAt(y, 3400, 16000) > 0.48);
});

test('output does not depend on block sizes (exact integer phase)', () => {
    const x = tone(440, 44100, 0.5, 0.8);
    const whole = runInBlocks(new Resampler(44100, 16000), x, [x.length]);
    const odd = runInBlocks(new Resampler(44100, 16000), x, [1, 7, 128, 333, 2, 4096]);
    assert.equal(odd.length, whole.length);
    assert.deepEqual(odd, whole);
});

test('16 kHz -> 16 kHz is a pass-through and 16 kHz -> 48 kHz upsamples', () => {
    const x = tone(1000, 16000, 0.25);
    assert.deepEqual(Array.from(new Resampler(16000, 16000).process(x)), Array.from(x));
    const up = runInBlocks(new Resampler(16000, 48000), tone(1000, 16000, 1), [100]);
    assert.ok(Math.abs(up.length - 48000) <= 3);
    assert.ok(Math.abs(amplitudeAt(up, 1000, 48000) - 0.5) < 0.02);
});

test('low-pass taps are symmetric with unity DC gain', () => {
    const taps = designLowpass(63, 7000 / 48000);
    assert.equal(taps.length, 63);
    const sum = taps.reduce((a, b) => a + b, 0);
    assert.ok(Math.abs(sum - 1) < 1e-6);
    for (let i = 0; i < taps.length; i++) assert.ok(Math.abs(taps[i] - taps[taps.length - 1 - i]) < 1e-7);
});

test('PCM16 conversion clamps, rounds and round-trips', () => {
    const pcm = floatToInt16(Float32Array.from([-1, -1.5, 1, 2, 0, 0.5, -0.5, NaN]));
    assert.deepEqual(Array.from(pcm), [-32768, -32768, 32767, 32767, 0, 16384, -16384, 0]);
    const x = tone(700, 16000, 0.1, 0.9);
    const back = int16ToFloat(floatToInt16(x));
    // encode x32767 / decode /32768 (standard asymmetric PCM16 scaling): error <= 1.5 LSB
    for (let i = 0; i < x.length; i++) assert.ok(Math.abs(back[i] - x[i]) <= 1.5 / 32768 + 1e-9);
});

test('Chunker emits fixed 100 ms chunks (1600 samples) and zeros when muted', () => {
    const chunks = [];
    const chunker = new Chunker(1600, (c) => chunks.push(c));
    chunker.push(new Float32Array(4000).fill(0.25));
    assert.equal(chunks.length, 2);
    assert.ok(chunks.every((c) => c instanceof Int16Array && c.length === 1600));
    assert.equal(chunks[0][0], Math.round(0.25 * 32767));
    assert.notEqual(chunks[0].buffer, chunks[1].buffer, 'each chunk owns its buffer (transferable)');
    chunker.push(new Float32Array(800).fill(0.25), true); // completes chunk 3 with muted samples
    assert.equal(chunks.length, 3);
    assert.equal(chunks[2][0], Math.round(0.25 * 32767));
    assert.equal(chunks[2][1599], 0);
});

test('PlaybackQueue waits for the prebuffer, then plays, then runs dry', () => {
    const q = new PlaybackQueue(48000, { prebufferMs: 80 }); // 1280 source samples
    const out = new Float32Array(128);
    q.push(new Float32Array(1000).fill(0.5));
    assert.equal(q.render(out), 0);
    assert.ok(out.every((v) => v === 0));
    q.push(new Float32Array(600).fill(0.5));
    assert.equal(q.render(out), 128);
    assert.ok(out.every((v) => Math.abs(v - 0.5) < 1e-6));
    let real = 0;
    for (let i = 0; i < 60; i++) real += q.render(out);
    assert.ok(q.bufferedSamples < 2);
    assert.equal(q.playing, false, 'ran dry -> prebuffer again');
    assert.ok(real > 0);
});

test('PlaybackQueue converts 16 kHz to the device rate (48 kHz, 44.1 kHz)', () => {
    for (const rate of [48000, 44100]) {
        const q = new PlaybackQueue(rate, { prebufferMs: 0 });
        q.push(tone(1000, 16000, 1));
        const out = new Float32Array(128);
        const collected = [];
        for (;;) {
            const n = q.render(out);
            if (n === 0) break;
            collected.push(...out.subarray(0, n));
        }
        const y = Float32Array.from(collected);
        assert.ok(Math.abs(y.length - rate) <= 4, `${rate}: ${y.length} frames`);
        assert.ok(Math.abs(amplitudeAt(y, 1000, rate) - 0.5) < 0.02, `${rate}: amplitude`);
    }
});

test('PlaybackQueue.clear() drops queued audio (barge-in)', () => {
    const q = new PlaybackQueue(48000, { prebufferMs: 20 });
    q.push(new Float32Array(16000).fill(0.3));
    const out = new Float32Array(128);
    assert.equal(q.render(out), 128);
    q.clear();
    assert.equal(q.bufferedSamples, 0);
    assert.equal(q.render(out), 0);
    assert.ok(out.every((v) => v === 0));
});

test('PlaybackQueue.drain() plays a goodbye tail shorter than the prebuffer (the bot hung up)', () => {
    const q = new PlaybackQueue(48000, { prebufferMs: 80 }); // 1280 source samples
    const out = new Float32Array(128);
    q.push(new Float32Array(800).fill(0.4)); // the last 50 ms of the goodbye
    assert.equal(q.render(out), 0, 'below the prebuffer: waits for more audio');
    assert.equal(q.empty, false);
    q.drain(); // the socket closed: no more audio will come
    let real = 0;
    for (let i = 0; i < 40 && !q.empty; i++) {
        const n = q.render(out);
        assert.ok(out.subarray(0, n).every((v) => Math.abs(v - 0.4) < 1e-6));
        real += n;
    }
    assert.ok(q.empty);
    // 799 source steps at 3 output frames each: all of the 50 ms is played, nothing is invented.
    assert.equal(real, 799 * 3);
    assert.equal(q.render(out), 0);
});

test('PlaybackQueue never grows past maxSeconds (drops the oldest audio)', () => {
    const q = new PlaybackQueue(48000, { maxSeconds: 1 }); // capacity: 16384 samples
    for (let i = 1; i <= 6; i++) q.push(new Float32Array(16000).fill(i / 10));
    assert.equal(q.bufferedSamples, 16384);
    assert.equal(q.dropped, 6 * 16000 - 16384);
    const out = new Float32Array(128);
    assert.equal(q.render(out), 128);
    assert.ok(Math.abs(out[0] - 0.5) < 1e-6, `oldest kept sample is from push 5, got ${out[0]}`);
});

test('meter mapping', () => {
    assert.equal(levelToMeter(0), 0);
    assert.equal(levelToMeter(1), 1);
    assert.equal(levelToMeter(0.001), 0);
    const mid = levelToMeter(0.02);
    assert.ok(mid > 0.3 && mid < 0.6, `${mid}`);
    assert.ok(Math.abs(rms(new Float32Array([0.5, -0.5, 0.5, -0.5])) - 0.5) < 1e-9);
    assert.equal(rms(new Float32Array(0)), 0);
});
