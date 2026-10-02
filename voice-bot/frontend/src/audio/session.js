// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// One voice call: microphone -> sd-capture worklet -> WebSocket (JsonSerializer protocol)
// and WebSocket -> sd-playback worklet -> speaker. No React in here.

import workletUrl from './worklet.js?worker&url';
import { WIRE_SAMPLE_RATE, int16ToFloat, levelToMeter } from './dsp.js';
import {
    AUTH_PROTOCOL_PREFIX,
    SUBPROTOCOL,
    buildSocketUrl,
    encodeMediaMessage,
    parseServerMessage,
} from '../lib/protocol.js';
import { closeOutcome } from '../lib/outcome.js';

const CHUNK_MS = 100; // 1600 samples / 3200 bytes per media message
const MAX_BUFFERED_BYTES = 1 << 20; // stop queueing audio if the network stalls (~25 s)
// The bot closes the socket right after sending its last audio, so the end of its goodbye is still
// in the jitter buffer: let it play out (normally ~0.1 s plus a 0.25 s tail), at most this long.
const DRAIN_MAX_MS = 2500;
// The bot has the same time cap and says goodbye when it is reached. The browser hangs up itself
// only this much later, so that goodbye is heard (it is the fallback for a bot that never ends).
const LIMIT_GRACE_S = 30;

/** Returns an i18n error key if this browser/page cannot run a call, else null. */
export function checkSupport() {
    if (typeof window === 'undefined') return 'errUnsupported';
    if (!window.isSecureContext) return 'errInsecure';
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') return 'errUnsupported';
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || typeof window.AudioWorkletNode === 'undefined' || typeof window.WebSocket === 'undefined') {
        return 'errUnsupported';
    }
    return null;
}

/** 'granted' | 'denied' | 'prompt' | 'unknown' (the Permissions API is missing in some browsers). */
export async function microphonePermission() {
    try {
        const status = await navigator.permissions.query({ name: 'microphone' });
        return status.state;
    } catch {
        return 'unknown';
    }
}

function micErrorKey(error) {
    switch (error && error.name) {
        case 'NotAllowedError':
        case 'PermissionDeniedError':
        case 'SecurityError':
            return 'errMicDenied';
        case 'NotFoundError':
        case 'DevicesNotFoundError':
        case 'OverconstrainedError':
            return 'errMicMissing';
        case 'NotReadableError':
        case 'TrackStartError':
        case 'AbortError':
            return 'errMicBusy';
        default:
            return 'errUnsupported';
    }
}

class CallError extends Error {
    constructor(key, detail = '') {
        super(key);
        this.key = key;
        this.detail = detail;
    }
}

async function openMicrophone() {
    const constraints = {
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
    };
    try {
        return await navigator.mediaDevices.getUserMedia(constraints);
    } catch (error) {
        if (error && error.name === 'OverconstrainedError') {
            try {
                return await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
            } catch (retryError) {
                throw new CallError(micErrorKey(retryError));
            }
        }
        throw new CallError(micErrorKey(error));
    }
}

/**
 * @typedef {object} SessionOptions
 * @property {string} websocketUrl   from config.json ("/ws" or "wss://host/ws")
 * @property {string} language       en-IN | hi-IN | mr-IN
 * @property {string} [pipeline]     sent as ?pipeline= when not empty
 * @property {'subprotocol'|'query'} [tokenTransport]  how the ID token reaches the bot
 * @property {() => Promise<string|null>} getToken  Cognito ID token
 * @property {number} maxCallSeconds
 * @property {string} pageHref
 * @property {object} on  callbacks: phase(p: starting|connecting|live|ending), caption({role,text,final,turn,interrupted}), interrupt(),
 *                        event(message) for tool/result/language/callStarted/callEnded/recording,
 *                        speaking(bool), levels({mic,bot}), audioSuspended(bool),
 *                        end({reason|error, detail, seconds})
 */
export class VoiceSession {
    /** @param {SessionOptions} options */
    constructor(options) {
        this.options = options;
        this.ended = false;
        this.opened = false;
        this.draining = null; // the outcome, while the bot's last words play out after it hung up
        this.muted = false;
        this.stopReason = null;
        this.assistantEnded = false;
        this.startedAt = 0;
        this.levels = { mic: 0, bot: 0 };
        this.timers = [];
        this.onVisibility = () => this._handleVisibility();
    }

    _emit(name, ...args) {
        const fn = this.options.on && this.options.on[name];
        if (!fn) return;
        try {
            fn(...args);
        } catch (error) {
            console.error(`voice session callback ${name} failed`, error);
        }
    }

    /** Call this synchronously inside the tap/click handler: iOS unlocks audio only in the gesture. */
    start() {
        const unsupported = checkSupport();
        if (unsupported) {
            this._finish({ error: unsupported });
            return;
        }
        const AC = window.AudioContext || window.webkitAudioContext;
        try {
            this.ctx = new AC({ latencyHint: 'interactive' });
        } catch (error) {
            this._finish({ error: 'errAudio', detail: error && error.message });
            return;
        }
        this.ctx.resume().catch(() => {});
        try {
            // Safari 16.4+: tell iOS this is a two-way call (keeps the loudspeaker and echo cancelling).
            if (navigator.audioSession) navigator.audioSession.type = 'play-and-record';
        } catch {
            /* not supported */
        }
        this._emit('phase', 'starting');
        this._run().catch((error) => {
            if (this.ended) return;
            if (error instanceof CallError) this._finish({ error: error.key, detail: error.detail });
            else this._finish({ error: 'errAudio', detail: (error && error.message) || String(error) });
        });
    }

    async _run() {
        const ctx = this.ctx;
        this.stream = await openMicrophone();
        if (this.ended) return this._cleanup();

        await ctx.audioWorklet.addModule(workletUrl);
        if (this.ended) return this._cleanup();

        this.capture = new AudioWorkletNode(ctx, 'sd-capture', {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            outputChannelCount: [1],
            channelCount: 1,
            channelCountMode: 'explicit',
            channelInterpretation: 'speakers',
            processorOptions: {
                targetRate: WIRE_SAMPLE_RATE,
                chunkSamples: Math.round((WIRE_SAMPLE_RATE * CHUNK_MS) / 1000),
            },
        });
        this.playback = new AudioWorkletNode(ctx, 'sd-playback', {
            numberOfInputs: 0,
            numberOfOutputs: 1,
            outputChannelCount: [1],
            processorOptions: { sourceRate: WIRE_SAMPLE_RATE, prebufferMs: 80 },
        });
        this.source = ctx.createMediaStreamSource(this.stream);
        this.source.connect(this.capture);
        this.capture.connect(ctx.destination); // silent output: keeps the capture node running
        this.playback.connect(ctx.destination);
        this.capture.port.onmessage = (event) => this._handleCapture(event.data);
        this.playback.port.onmessage = (event) => this._handlePlayback(event.data);
        if (this.muted) this.capture.port.postMessage({ type: 'mute', value: true });
        ctx.onstatechange = () => {
            const state = ctx.state;
            this._emit('audioSuspended', !this.ended && state !== 'running' && state !== 'closed');
        };
        for (const track of this.stream.getAudioTracks()) {
            track.addEventListener('ended', () => {
                if (!this.ended && !this.draining) this._finish({ error: 'errMicBusy' });
            });
        }

        this._emit('phase', 'connecting');
        let token;
        try {
            token = await this.options.getToken();
        } catch {
            token = null;
        }
        if (this.ended) return this._cleanup();
        if (!token) throw new CallError('errAuth');

        const viaQuery = this.options.tokenTransport === 'query';
        if (!viaQuery && !/^[A-Za-z0-9._-]+$/.test(token)) throw new CallError('errAuth');
        const url = buildSocketUrl(
            this.options.websocketUrl,
            { language: this.options.language, pipeline: this.options.pipeline, ...(viaQuery ? { token } : {}) },
            this.options.pageHref,
        );
        // Token as a subprotocol: it never appears in the URL (load-balancer and proxy logs).
        const ws = viaQuery ? new WebSocket(url) : new WebSocket(url, [SUBPROTOCOL, AUTH_PROTOCOL_PREFIX + token]);
        ws.binaryType = 'arraybuffer';
        this.ws = ws;
        ws.onopen = () => this._handleOpen();
        ws.onmessage = (event) => this._handleMessage(event.data);
        ws.onclose = (event) => this._handleClose(event);
        ws.onerror = () => {}; // the close event that follows carries the details
    }

    _handleOpen() {
        if (this.ended) return;
        this.opened = true;
        this.startedAt = performance.now();
        this._emit('phase', 'live');
        const limit = (Math.max(60, Number(this.options.maxCallSeconds) || 600) + LIMIT_GRACE_S) * 1000;
        this.timers.push(setTimeout(() => this.stop('limit'), limit));
        document.addEventListener('visibilitychange', this.onVisibility);
        this._acquireWakeLock();
        if (this.ctx && this.ctx.state !== 'running') this.ctx.resume().catch(() => {});
    }

    _handleCapture(message) {
        if (!message) return;
        if (message.type === 'chunk') {
            const ws = this.ws;
            if (ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < MAX_BUFFERED_BYTES) {
                ws.send(encodeMediaMessage(new Int16Array(message.pcm)));
            }
        } else if (message.type === 'level') {
            this.levels = { ...this.levels, mic: levelToMeter(message.value) };
            this._emit('levels', this.levels);
        }
    }

    _handlePlayback(message) {
        if (!message) return;
        if (message.type === 'level') {
            this.levels = { ...this.levels, bot: levelToMeter(message.value) };
            this._emit('levels', this.levels);
        } else if (message.type === 'speaking') {
            this._emit('speaking', !!message.value);
        } else if (message.type === 'drained' && this.draining) {
            this._finish(this.draining);
        }
    }

    _handleMessage(data) {
        if (this.ended || this.draining) return;
        const message = parseServerMessage(data);
        switch (message.kind) {
            case 'media':
                if (this.playback && message.samples.length) {
                    const samples = int16ToFloat(message.samples);
                    this.playback.port.postMessage({ type: 'audio', samples: samples.buffer }, [samples.buffer]);
                }
                break;
            case 'interruption':
                if (this.playback) this.playback.port.postMessage({ type: 'clear' });
                this._emit('interrupt');
                break;
            case 'caption':
                this._emit('caption', message);
                break;
            case 'tool':
            case 'result':
            case 'language':
            case 'callStarted':
            case 'recording':
                this._emit('event', message);
                break;
            case 'callEnded':
                this.assistantEnded = true;
                this.endedBy = message.reason;
                this._emit('event', message);
                break;
            case 'end':
                this.assistantEnded = true;
                break;
            case 'error':
                this.serverError = message.message;
                break;
            default:
                break;
        }
    }

    _handleClose(event) {
        if (this.ended || this.draining) return;
        const outcome = closeOutcome(event.code, event.reason, this);
        if (outcome.reason === 'assistant' && this.playback) this._drain(outcome);
        else this._finish(outcome);
    }

    /**
     * The bot hung up (normal close after its goodbye). The microphone stops now; the speaker plays
     * what is left of the goodbye, then the call ends ("drained", or after DRAIN_MAX_MS).
     */
    _drain(outcome) {
        this.draining = outcome; // the call lasts until its last word has been heard
        if (this.capture) this.capture.port.postMessage({ type: 'stop' });
        if (this.stream) for (const track of this.stream.getTracks()) track.stop();
        this.playback.port.postMessage({ type: 'drain' });
        this.timers.push(setTimeout(() => this.draining && this._finish(this.draining), DRAIN_MAX_MS));
        this._emit('phase', 'ending');
    }

    _handleVisibility() {
        if (this.ended || document.visibilityState !== 'visible') return;
        if (this.ctx && this.ctx.state !== 'running') this.ctx.resume().catch(() => {});
        if (!this.wakeLock || this.wakeLock.released) this._acquireWakeLock();
    }

    async _acquireWakeLock() {
        try {
            if (navigator.wakeLock && document.visibilityState === 'visible') {
                this.wakeLock = await navigator.wakeLock.request('screen');
                if (this.ended) this.wakeLock.release().catch(() => {});
            }
        } catch {
            /* the screen may sleep: not fatal */
        }
    }

    /** Resume audio after iOS interrupted it (must be called from a tap). */
    resumeAudio() {
        if (this.ctx && this.ctx.state !== 'closed') this.ctx.resume().catch(() => {});
    }

    setMuted(muted) {
        this.muted = !!muted;
        if (this.capture) this.capture.port.postMessage({ type: 'mute', value: this.muted });
    }

    get seconds() {
        return this.opened ? (performance.now() - this.startedAt) / 1000 : 0;
    }

    /** Ends the call: 'user' (End button), 'limit' (max duration) or 'unmount'. */
    stop(reason = 'user') {
        if (this.ended) return;
        if (this.draining) {
            this._finish(this.draining); // the bot had already hung up: keep its outcome
            return;
        }
        this.stopReason = reason;
        const ws = this.ws;
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
            try {
                ws.close(1000, reason === 'limit' ? 'time limit' : 'caller hung up');
            } catch {
                /* already closing */
            }
        }
        this._finish({ reason });
    }

    _finish(result) {
        if (this.ended) return;
        const seconds = this.seconds;
        this.ended = true;
        this.draining = null;
        this._cleanup();
        this._emit('end', { ...result, seconds });
    }

    _cleanup() {
        for (const timer of this.timers) clearTimeout(timer);
        this.timers = [];
        document.removeEventListener('visibilitychange', this.onVisibility);
        if (this.wakeLock && !this.wakeLock.released) this.wakeLock.release().catch(() => {});
        this.wakeLock = null;
        const ws = this.ws;
        if (ws) {
            ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
            if (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN) {
                try {
                    ws.close(1000, 'caller hung up');
                } catch {
                    /* ignore */
                }
            }
        }
        for (const node of [this.capture, this.playback]) {
            if (!node) continue;
            try {
                node.port.postMessage({ type: 'stop' });
                node.port.onmessage = null;
                node.disconnect();
            } catch {
                /* ignore */
            }
        }
        if (this.source) {
            try {
                this.source.disconnect();
            } catch {
                /* ignore */
            }
        }
        if (this.stream) for (const track of this.stream.getTracks()) track.stop();
        if (this.ctx && this.ctx.state !== 'closed') {
            this.ctx.onstatechange = null;
            this.ctx.close().catch(() => {});
        }
        this.capture = this.playback = this.source = this.stream = null;
    }
}
