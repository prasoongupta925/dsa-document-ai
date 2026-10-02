// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Pure DSP helpers shared by the AudioWorklet (audio thread) and the unit tests (Node).
// No DOM or Web Audio globals in this file.
//
// The browser AudioContext runs at the device rate (usually 48 kHz or 44.1 kHz). Forcing
// `new AudioContext({ sampleRate: 16000 })` breaks the microphone on Firefox and is unreliable on
// iOS Safari, so we keep the native rate and resample here:
//   microphone (native rate) -> low-pass + resample -> 16 kHz PCM16 -> backend
//   backend 16 kHz PCM16 -> resample -> speaker (native rate)

/** Sample rate on the wire. Must match SAMPLE_RATE in backend/main.py (JsonSerializer, Transcribe, Polly). */
export const WIRE_SAMPLE_RATE = 16000;

function gcd(a, b) {
    while (b) [a, b] = [b, a % b];
    return a;
}

function checkRate(rate, name) {
    if (!Number.isFinite(rate) || rate <= 0) throw new RangeError(`${name} must be a positive number`);
    return Math.round(rate);
}

/** Float32 [-1, 1] -> Int16 PCM, clamped, NaN -> 0. */
export function floatToInt16(input, out = new Int16Array(input.length)) {
    for (let i = 0; i < input.length; i++) {
        let s = input[i];
        if (s !== s) s = 0; // NaN
        else if (s > 1) s = 1;
        else if (s < -1) s = -1;
        out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
    }
    return out;
}

/** Int16 PCM -> Float32 [-1, 1). */
export function int16ToFloat(input, out = new Float32Array(input.length)) {
    for (let i = 0; i < input.length; i++) out[i] = input[i] / 32768;
    return out;
}

/** Root-mean-square level of a block. */
export function rms(block) {
    if (!block.length) return 0;
    let sum = 0;
    for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
    return Math.sqrt(sum / block.length);
}

/** Maps an RMS level to 0..1 for the on-screen meter (-60 dBFS -> 0, -12 dBFS -> 1). */
export function levelToMeter(level) {
    if (!(level > 0)) return 0;
    const db = 20 * Math.log10(level);
    return Math.min(1, Math.max(0, (db + 60) / 48));
}

/**
 * Linear-phase windowed-sinc low-pass (Hamming window), unity gain at DC.
 * @param {number} numTaps odd number of taps
 * @param {number} cutoff -6 dB point in cycles per sample (0 .. 0.5)
 */
export function designLowpass(numTaps, cutoff) {
    if (numTaps % 2 === 0) numTaps += 1;
    const taps = new Float32Array(numTaps);
    const m = (numTaps - 1) / 2;
    let sum = 0;
    for (let n = 0; n < numTaps; n++) {
        const k = n - m;
        const sinc = k === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * k) / (Math.PI * k);
        const w = 0.54 - 0.46 * Math.cos((2 * Math.PI * n) / (numTaps - 1));
        taps[n] = sinc * w;
        sum += taps[n];
    }
    for (let n = 0; n < numTaps; n++) taps[n] /= sum;
    return taps;
}

/** Streaming FIR filter (keeps history between blocks). */
export class FirFilter {
    constructor(taps) {
        this.taps = taps;
        this.history = new Float32Array(taps.length - 1);
        this.work = new Float32Array(0);
        this.out = new Float32Array(0);
    }

    process(input) {
        const h = this.taps;
        const nt = h.length;
        const hl = nt - 1;
        const len = input.length;
        if (this.work.length < hl + len) this.work = new Float32Array(hl + len);
        if (this.out.length < len) this.out = new Float32Array(len);
        const work = this.work;
        const out = this.out;
        work.set(this.history, 0);
        work.set(input, hl);
        for (let i = 0; i < len; i++) {
            let acc = 0;
            for (let k = 0; k < nt; k++) acc += h[k] * work[i + k]; // taps are symmetric
            out[i] = acc;
        }
        this.history.set(work.subarray(len, len + hl));
        return out.subarray(0, len);
    }
}

/**
 * Streaming resampler with exact integer phase (output is identical whatever the block sizes).
 * Down-sampling applies an anti-alias low-pass first; interpolation is linear.
 * `process()` returns a view into an internal buffer: consume it before the next call.
 */
export class Resampler {
    constructor(inRate, outRate, { taps } = {}) {
        inRate = checkRate(inRate, 'inRate');
        outRate = checkRate(outRate, 'outRate');
        const g = gcd(inRate, outRate);
        this.inRate = inRate;
        this.outRate = outRate;
        this.num = inRate / g; // phase step, in 1/den input samples
        this.den = outRate / g;
        this.invDen = 1 / this.den;
        this.identity = inRate === outRate;
        if (inRate > outRate) {
            const ratio = inRate / outRate;
            const count = taps ?? (2 * Math.round(10.5 * ratio) + 1); // 63 taps for 48k -> 16k
            // -6 dB at 0.4375 x the output rate (7 kHz for 16 kHz): passband to ~5.8 kHz, stopband from ~8.2 kHz.
            this.filter = new FirFilter(designLowpass(count, (0.4375 * outRate) / inRate));
        } else {
            this.filter = null;
        }
        this.phase = this.den; // position of the next output sample (v[1] = first input sample)
        this.last = 0; // v[0]: last (filtered) input sample of the previous block
        this.out = new Float32Array(0);
    }

    process(input) {
        if (this.identity) return input;
        const x = this.filter ? this.filter.process(input) : input;
        const n = x.length;
        if (n === 0) return x.subarray(0, 0);
        const maxOut = Math.ceil((n * this.den) / this.num) + 2;
        if (this.out.length < maxOut) this.out = new Float32Array(maxOut);
        const out = this.out;
        const { num, den, invDen } = this;
        const last = this.last;
        let phase = this.phase;
        let count = 0;
        // Virtual array v = [last, x[0], ..., x[n-1]]; output at v-position phase/den.
        for (;;) {
            const i = Math.floor(phase / den);
            if (i >= n) break;
            const f = (phase - i * den) * invDen;
            const a = i === 0 ? last : x[i - 1];
            out[count++] = a + (x[i] - a) * f;
            phase += num;
        }
        this.phase = phase - n * den;
        this.last = x[n - 1];
        return out.subarray(0, count);
    }
}

/**
 * Accumulates 16 kHz float samples into fixed-size Int16 chunks (the wire frames).
 * `push()` calls `emit(Int16Array)` for every full chunk; the array is handed over (not reused).
 */
export class Chunker {
    constructor(chunkSamples, emit) {
        if (!(chunkSamples > 0)) throw new RangeError('chunkSamples must be > 0');
        this.size = chunkSamples;
        this.emit = emit;
        this.chunk = new Int16Array(chunkSamples);
        this.fill = 0;
    }

    push(samples, muted = false) {
        for (let i = 0; i < samples.length; i++) {
            let s = muted ? 0 : samples[i];
            if (s !== s) s = 0;
            else if (s > 1) s = 1;
            else if (s < -1) s = -1;
            this.chunk[this.fill++] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
            if (this.fill === this.size) {
                const full = this.chunk;
                this.chunk = new Int16Array(this.size);
                this.fill = 0;
                this.emit(full);
            }
        }
    }
}

function nextPow2(n) {
    let p = 1;
    while (p < n) p *= 2;
    return p;
}

/**
 * Jitter buffer for the assistant's voice: 16 kHz samples in, device-rate samples out.
 * Waits for `prebufferMs` of audio before it starts (and again after running dry), so network
 * jitter does not chop the voice. `clear()` drops everything (barge-in / interruption).
 * `drain()` is for the end of the call: no more audio will come, so whatever is left plays out
 * even if it is shorter than the prebuffer.
 */
export class PlaybackQueue {
    constructor(outRate, { sourceRate = WIRE_SAMPLE_RATE, prebufferMs = 80, maxSeconds = 180 } = {}) {
        outRate = checkRate(outRate, 'outRate');
        sourceRate = checkRate(sourceRate, 'sourceRate');
        const g = gcd(sourceRate, outRate);
        this.num = sourceRate / g; // source samples advanced per output sample = num / den
        this.den = outRate / g;
        this.invDen = 1 / this.den;
        this.sourceRate = sourceRate;
        this.maxCapacity = nextPow2(Math.ceil(sourceRate * maxSeconds));
        this.buf = new Float32Array(Math.min(nextPow2(sourceRate * 8), this.maxCapacity));
        this.mask = this.buf.length - 1;
        this.read = 0;
        this.count = 0;
        this.phase = 0;
        this.prebuffer = Math.max(2, Math.round((sourceRate * prebufferMs) / 1000));
        this.playing = false;
        this.draining = false;
        this.dropped = 0;
    }

    get bufferedSamples() {
        return this.count;
    }

    /** Nothing left to play (the interpolator needs two samples). */
    get empty() {
        return this.count < 2;
    }

    /** No more audio will arrive: play the rest without waiting for the prebuffer. */
    drain() {
        this.draining = true;
    }

    get bufferedMs() {
        return (this.count * 1000) / this.sourceRate;
    }

    clear() {
        this.read = 0;
        this.count = 0;
        this.phase = 0;
        this.playing = false;
    }

    _grow(needed) {
        let cap = this.buf.length;
        while (cap < needed && cap < this.maxCapacity) cap *= 2;
        if (cap === this.buf.length) return;
        const next = new Float32Array(cap);
        for (let i = 0; i < this.count; i++) next[i] = this.buf[(this.read + i) & this.mask];
        this.buf = next;
        this.mask = cap - 1;
        this.read = 0;
    }

    push(samples) {
        if (!samples.length) return;
        if (this.count + samples.length > this.buf.length) this._grow(this.count + samples.length);
        if (samples.length > this.buf.length) {
            this.dropped += samples.length - this.buf.length;
            samples = samples.subarray(samples.length - this.buf.length);
        }
        const n = samples.length;
        const overflow = this.count + n - this.buf.length;
        if (overflow > 0) {
            // Over maxSeconds behind: drop the oldest audio rather than grow without bound.
            this.read = (this.read + overflow) & this.mask;
            this.count -= overflow;
            this.dropped += overflow;
        }
        let w = (this.read + this.count) & this.mask;
        for (let i = 0; i < n; i++) {
            this.buf[w] = samples[i];
            w = (w + 1) & this.mask;
        }
        this.count += n;
    }

    /** Fills `out` (device rate). Returns how many frames came from the queue (0 = silence). */
    render(out) {
        if (!this.playing) {
            if (this.count >= this.prebuffer || (this.draining && this.count >= 2)) {
                this.playing = true;
            } else {
                out.fill(0);
                return 0;
            }
        }
        const { buf, mask, num, den, invDen } = this;
        let { read, count, phase } = this;
        let j = 0;
        for (; j < out.length; j++) {
            if (count < 2) break;
            const a = buf[read];
            const b = buf[(read + 1) & mask];
            out[j] = a + (b - a) * (phase * invDen);
            phase += num;
            while (phase >= den) {
                phase -= den;
                read = (read + 1) & mask;
                count--;
            }
        }
        if (j < out.length) {
            out.fill(0, j);
            this.playing = false; // ran dry: wait for the prebuffer again
        }
        this.read = read;
        this.count = count;
        this.phase = phase;
        return j;
    }
}
