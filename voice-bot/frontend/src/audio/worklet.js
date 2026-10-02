// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// AudioWorklet processors (audio thread). Loaded with audioWorklet.addModule(); Vite bundles
// this file and dsp.js into one script (imported with `?worker&url`).
//
//   sd-capture : microphone (device rate) -> 16 kHz PCM16 chunks -> main thread (-> WebSocket)
//   sd-playback: 16 kHz samples from the main thread -> jitter buffer -> speaker (device rate)
//                messages in: audio, clear (barge-in), drain (the bot hung up), stop
//                messages out: level, speaking, drained (the last words have been played)

import { Chunker, PlaybackQueue, Resampler, WIRE_SAMPLE_RATE } from './dsp.js';

const LEVEL_HZ = 30; // meter updates per second

class SdCaptureProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const o = (options && options.processorOptions) || {};
        this.resampler = new Resampler(sampleRate, o.targetRate || WIRE_SAMPLE_RATE);
        this.chunker = new Chunker(o.chunkSamples || 1600, (pcm) => {
            this.port.postMessage({ type: 'chunk', pcm: pcm.buffer }, [pcm.buffer]);
        });
        this.muted = false;
        this.active = true;
        this.levelSum = 0;
        this.levelCount = 0;
        this.levelEvery = Math.round(sampleRate / LEVEL_HZ);
        this.port.onmessage = (event) => {
            const m = event.data || {};
            if (m.type === 'mute') this.muted = !!m.value;
            else if (m.type === 'stop') this.active = false;
        };
    }

    process(inputs) {
        if (!this.active) return false;
        const channel = inputs[0] && inputs[0][0];
        if (channel && channel.length) {
            for (let i = 0; i < channel.length; i++) this.levelSum += channel[i] * channel[i];
            this.levelCount += channel.length;
            if (this.levelCount >= this.levelEvery) {
                const level = this.muted ? 0 : Math.sqrt(this.levelSum / this.levelCount);
                this.port.postMessage({ type: 'level', value: level });
                this.levelSum = 0;
                this.levelCount = 0;
            }
            this.chunker.push(this.resampler.process(channel), this.muted);
        }
        return true; // the output stays silent; it only keeps the node pulled by the graph
    }
}

class SdPlaybackProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const o = (options && options.processorOptions) || {};
        this.queue = new PlaybackQueue(sampleRate, {
            sourceRate: o.sourceRate || WIRE_SAMPLE_RATE,
            prebufferMs: o.prebufferMs ?? 80,
        });
        this.speaking = false;
        this.silentFrames = 0;
        this.hangoverFrames = Math.round(sampleRate * ((o.hangoverMs ?? 350) / 1000));
        this.levelSum = 0;
        this.levelCount = 0;
        this.levelEvery = Math.round(sampleRate / LEVEL_HZ);
        this.active = true;
        // End of call: play out the queue, then report "drained" after this much silence, so the
        // device's own output buffer (more on Bluetooth) is heard before the context closes.
        this.draining = false;
        this.drainSilentFrames = 0;
        this.drainTailFrames = Math.round(sampleRate * ((o.drainTailMs ?? 250) / 1000));
        this.port.onmessage = (event) => {
            const m = event.data || {};
            if (m.type === 'audio' && m.samples) this.queue.push(new Float32Array(m.samples));
            else if (m.type === 'clear') this.queue.clear();
            else if (m.type === 'drain') {
                this.queue.drain();
                this.draining = true;
            } else if (m.type === 'stop') this.active = false;
        };
    }

    process(_inputs, outputs) {
        if (!this.active) return false;
        const out = outputs[0] && outputs[0][0];
        if (!out) return true;
        const real = this.queue.render(out);
        for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);

        for (let i = 0; i < out.length; i++) this.levelSum += out[i] * out[i];
        this.levelCount += out.length;
        if (this.levelCount >= this.levelEvery) {
            this.port.postMessage({ type: 'level', value: Math.sqrt(this.levelSum / this.levelCount) });
            this.levelSum = 0;
            this.levelCount = 0;
        }

        if (real > 0) {
            this.silentFrames = 0;
            if (!this.speaking) {
                this.speaking = true;
                this.port.postMessage({ type: 'speaking', value: true });
            }
        } else if (this.speaking) {
            this.silentFrames += out.length;
            if (this.silentFrames >= this.hangoverFrames) {
                this.speaking = false;
                this.port.postMessage({ type: 'speaking', value: false });
            }
        }

        if (this.draining) {
            this.drainSilentFrames = real > 0 ? 0 : this.drainSilentFrames + out.length;
            // Already quiet when the call ended: report at once; otherwise after the tail.
            if (this.queue.empty && (!this.speaking || this.drainSilentFrames >= this.drainTailFrames)) {
                this.draining = false;
                this.port.postMessage({ type: 'drained' });
            }
        }
        return true;
    }
}

registerProcessor('sd-capture', SdCaptureProcessor);
registerProcessor('sd-playback', SdPlaybackProcessor);
