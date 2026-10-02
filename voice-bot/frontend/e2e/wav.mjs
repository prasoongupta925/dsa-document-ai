// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Test signals: the fake microphone WAV and the fake bot voice (PCM16 mono).

/** 16-bit mono WAV of a gated tone (bursts survive Chrome's noise suppression). */
export function toneWav({ rate = 48000, freq = 1000, seconds = 4, amp = 0.5, onMs = 220, offMs = 80 }) {
    const n = Math.round(rate * seconds);
    const pcm = Buffer.alloc(n * 2);
    const period = Math.round((rate * (onMs + offMs)) / 1000);
    const on = Math.round((rate * onMs) / 1000);
    const fade = Math.round(rate * 0.005);
    for (let i = 0; i < n; i++) {
        const k = i % period;
        let g = k < on ? 1 : 0;
        if (k < fade) g = k / fade;
        else if (k >= on - fade && k < on) g = (on - k) / fade;
        const v = Math.round(amp * g * Math.sin((2 * Math.PI * freq * i) / rate) * 32767);
        pcm.writeInt16LE(v, i * 2);
    }
    const header = Buffer.alloc(44);
    header.write('RIFF', 0);
    header.writeUInt32LE(36 + pcm.length, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20); // PCM
    header.writeUInt16LE(1, 22); // mono
    header.writeUInt32LE(rate, 24);
    header.writeUInt32LE(rate * 2, 28);
    header.writeUInt16LE(2, 32);
    header.writeUInt16LE(16, 34);
    header.write('data', 36);
    header.writeUInt32LE(pcm.length, 40);
    return Buffer.concat([header, pcm]);
}

/** PCM16 LE bytes of a sine at 16 kHz. */
export function tonePcm16({ rate = 16000, freq = 440, seconds = 1, amp = 0.3 }) {
    const n = Math.round(rate * seconds);
    const buf = Buffer.alloc(n * 2);
    for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(amp * Math.sin((2 * Math.PI * freq * i) / rate) * 32767), i * 2);
    return buf;
}

/** Power of one frequency (Goertzel), normalised by length. */
export function goertzel(samples, freq, rate) {
    const w = (2 * Math.PI * freq) / rate;
    const c = 2 * Math.cos(w);
    let s1 = 0;
    let s2 = 0;
    for (let i = 0; i < samples.length; i++) {
        const s0 = samples[i] + c * s1 - s2;
        s2 = s1;
        s1 = s0;
    }
    return (s1 * s1 + s2 * s2 - c * s1 * s2) / (samples.length * samples.length);
}
