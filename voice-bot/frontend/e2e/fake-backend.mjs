// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// One local server laid out like the voice CloudFront distribution:
//   GET  /*               the built app (dist/), unknown paths -> index.html (CloudFront 403/404 rule)
//   GET  /config.json     the test's runtime config
//   WS   /ws              a fake bot speaking the backend protocol (serializers.py BrowserJsonSerializer):
//                         subprotocols ["voicebot.v1", "auth.<token>"], close 4001 for a bad token
//   POST /calls/outbound  the outbound-call API (main.py), Bearer token
// It records what the browser sent so the test can check it.

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { WebSocketServer } from 'ws';
import { goertzel, tonePcm16 } from './wav.mjs';

const TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.json': 'application/json',
    '.webmanifest': 'application/manifest+json',
    '.txt': 'text/plain',
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function newStats() {
    return {
        connections: [],
        frames: 0,
        badFrames: [],
        frameBytes: new Set(),
        micSamples: [],
        micStartedAt: 0,
        mutedWindowRms: null,
        scriptDone: false,
        interruptSentAt: 0,
        serverClosedAt: 0,
        clientClose: null,
        outbound: [],
    };
}

function rms(samples) {
    if (!samples.length) return 0;
    let s = 0;
    for (const v of samples) s += v * v;
    return Math.sqrt(s / samples.length);
}

/** Frequency check of the last second of caller audio, as the bot would hear it at 16 kHz. */
export function micAnalysis(stats) {
    const tail = stats.micSamples.slice(-16000).map((v) => v / 32768);
    const p = (f) => goertzel(tail, f, 16000);
    const elapsed = (Date.now() - stats.micStartedAt) / 1000;
    return {
        seconds: stats.micSamples.length / 16000,
        samplesPerSecond: Math.round(stats.micSamples.length / Math.max(0.001, elapsed)),
        p1000: p(1000),
        p333: p(333.33),
        p3000: p(3000),
        rms: rms(tail),
    };
}

export async function startServer({ distDir, config, expectedToken, scenario }) {
    const state = { config, expectedToken, scenario, stats: newStats(), sockets: new Set() };

    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        if (url.pathname === '/calls/outbound') return outbound(req, res, state);
        if (url.pathname === '/config.json' && state.config !== null) {
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(state.config));
            return;
        }
        let path = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, '');
        if (path === '/' || path === '') path = '/index.html';
        try {
            const body = await readFile(join(distDir, path));
            res.writeHead(200, { 'Content-Type': TYPES[extname(path)] || 'application/octet-stream' }).end(body);
        } catch {
            // CloudFront answers 403/404 with index.html and status 200.
            const body = await readFile(join(distDir, 'index.html'));
            res.writeHead(200, { 'Content-Type': TYPES['.html'] }).end(body);
        }
    });

    const wss = new WebSocketServer({
        noServer: true,
        handleProtocols: (protocols) => (protocols.has('voicebot.v1') ? 'voicebot.v1' : false),
    });
    server.on('upgrade', (req, socket, head) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        if (url.pathname !== '/ws') {
            socket.destroy();
            return;
        }
        wss.handleUpgrade(req, socket, head, (ws) => onCall(ws, req, state));
    });

    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address();
    return {
        port,
        origin: `http://127.0.0.1:${port}`,
        state,
        reset(next) {
            Object.assign(state, next, { stats: newStats() });
        },
        async close() {
            for (const ws of state.sockets) ws.terminate();
            wss.close();
            await new Promise((resolve) => server.close(resolve));
        },
    };
}

async function outbound(req, res, state) {
    let body = '';
    for await (const chunk of req) body += chunk;
    const auth = req.headers.authorization || '';
    const entry = { method: req.method, auth: auth === `Bearer ${state.expectedToken}`, body: null };
    try {
        entry.body = JSON.parse(body || 'null');
    } catch {
        entry.body = 'invalid';
    }
    state.stats.outbound.push(entry);
    const send = (status, json) => res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(json));
    if (req.method !== 'POST') return send(405, { detail: 'POST only' });
    if (!entry.auth) return send(401, { detail: 'sign in first' });
    // First attempt: refused by a guard rail; second: placed.
    if (state.stats.outbound.length === 1) return send(409, { detail: 'outside the calling window (10:00-19:00 IST)' });
    return send(202, { provider: 'plivo', request_id: 'req-7f3a9c21b8d4' });
}

function onCall(ws, req, state) {
    const { stats } = state;
    state.sockets.add(ws);
    const url = new URL(req.url, 'http://127.0.0.1');
    const offered = String(req.headers['sec-websocket-protocol'] || '')
        .split(',')
        .map((p) => p.trim())
        .filter(Boolean);
    const authProto = offered.find((p) => p.startsWith('auth.'));
    const token = authProto ? authProto.slice(5) : url.searchParams.get('token') || '';
    const connection = {
        path: url.pathname,
        query: Object.fromEntries(url.searchParams),
        offered: offered.map((p) => (p.startsWith('auth.') ? 'auth.<token>' : p)),
        chosen: ws.protocol,
        tokenVia: authProto ? 'subprotocol' : url.searchParams.has('token') ? 'query' : 'none',
        tokenOk: token === state.expectedToken,
        origin: req.headers.origin,
    };
    stats.connections.push(connection);

    let closed = false;
    let mutedFrom = 0;
    ws.on('close', (code, reason) => {
        closed = true;
        state.sockets.delete(ws);
        stats.clientClose = { code, reason: reason.toString() };
    });
    ws.on('message', (data, isBinary) => {
        stats.frames++;
        if (isBinary) {
            stats.badFrames.push('binary');
            return;
        }
        let msg;
        try {
            msg = JSON.parse(data.toString());
        } catch {
            stats.badFrames.push('not json');
            return;
        }
        const keys = Object.keys(msg).join(',');
        if (keys !== 'event,data' || msg.event !== 'media' || typeof msg.data !== 'string') {
            stats.badFrames.push(keys);
            return;
        }
        const bytes = Buffer.from(msg.data, 'base64');
        stats.frameBytes.add(bytes.length);
        if (!stats.micStartedAt) stats.micStartedAt = Date.now();
        for (let i = 0; i + 1 < bytes.length; i += 2) stats.micSamples.push(bytes.readInt16LE(i));
        if (mutedFrom && stats.mutedWindowRms === null && stats.micSamples.length - mutedFrom >= 8000) {
            stats.mutedWindowRms = rms(stats.micSamples.slice(mutedFrom + 3200, mutedFrom + 8000));
        }
    });

    const send = (obj) => {
        if (!closed) ws.send(JSON.stringify(obj));
    };
    // 40 ms chunks like pipecat's output transport (audio_out_10ms_chunks=4). Pipecat paces them in
    // real time; the fake sends them twice as fast (pace 20 ms) so a backlog builds up in the browser.
    const speak = async (seconds, freq, pace = 20) => {
        const pcm = tonePcm16({ freq, seconds });
        for (let i = 0; i < pcm.length && !closed; i += 1280) {
            send({ event: 'media', data: pcm.subarray(i, i + 1280).toString('base64') });
            if (pace) await sleep(pace);
        }
    };
    const waitMic = async (seconds, timeoutMs = 10000) => {
        const until = Date.now() + timeoutMs;
        while (!closed && stats.micSamples.length < seconds * 16000 && Date.now() < until) await sleep(50);
    };

    state.markMuted = () => {
        mutedFrom = stats.micSamples.length;
    };

    (async () => {
        if (!connection.tokenOk) {
            ws.close(4001, 'Sign in again'); // main.py accepts first, then refuses the token
            return;
        }
        if (state.scenario === 'busy') {
            ws.close(1013, 'Busy, try again');
            return;
        }
        send({ event: 'call_started', call_id: '4f1e9a07b3d2', language: 'hi' });
        await speak(1.0, 440);
        send({
            event: 'bot_transcript',
            text: 'Namaste! Main Varunika Loan Partners ki AI assistant hoon. Yeh call record ho rahi hai.',
            interrupted: false,
        });
        if (state.scenario === 'server-ends') {
            await sleep(400);
            send({ event: 'call_ended', reason: 'time_cap' });
            // The goodbye, then the hang-up. Pipecat closes right after sending the last chunk, so the
            // end of the goodbye is still in the browser's jitter buffer: here 0.6 s of it.
            await speak(0.6, 330, 0);
            stats.serverClosedAt = Date.now();
            ws.close(1000);
            return;
        }
        if (state.scenario === 'language') {
            await sleep(300);
            send({ event: 'tool', name: 'switch_language', status: 'started' });
            send({ event: 'language', language: 'mr' });
            send({ event: 'bot_transcript', text: 'नक्की, आता मराठीत बोलूया.', interrupted: false });
            stats.scriptDone = true;
            return;
        }
        await waitMic(1.5);
        send({ event: 'user_transcript', text: 'Sneha Kulkarni ki file mein kya baaki hai?' });
        send({ event: 'tool', name: 'file_status', status: 'started' });
        await sleep(350);
        send({
            event: 'file_status',
            verdict: 'NOT READY',
            missing: ['Salary slip for June 2026', 'Bank statement for March to May 2026', 'Form 16 for FY 2025-26'],
            mismatches: 1,
        });
        await speak(0.8, 523);
        send({
            event: 'bot_transcript',
            text: 'Sneha ji, June 2026 ki salary slip, March se May ka bank statement aur Form 16 baaki hai.',
            interrupted: false,
        });
        send({ event: 'user_transcript', text: 'Kitna loan mil sakta hai?' });
        send({ event: 'tool', name: 'eligibility', status: 'started' });
        await sleep(250);
        send({ event: 'eligibility', best_lender: 'Demo Bank', amount: '4.5 lakh' });
        await speak(0.6, 494);
        send({
            event: 'bot_transcript',
            text: 'Demo Bank se lagbhag saadhe chaar lakh tak, yeh sirf anumaan hai; faisla lender karega.',
            interrupted: false,
        });
        send({ event: 'user_transcript', text: 'WhatsApp reminder bhej do.' });
        send({ event: 'tool', name: 'reminder', status: 'started' });
        await sleep(250);
        send({
            event: 'reminder',
            template: 'T1',
            language: 'hi',
            channel: 'whatsapp',
            text:
                'नमस्ते Sneha, Varunika Loan Partners की ओर से। आपके loan आवेदन के लिए ये documents बाकी हैं: ' +
                'June 2026 की salary slip; March–May 2026 का bank statement; FY 2025-26 का Form 16। ' +
                'यहाँ अपलोड करें: {upload_link}',
            placeholders: ['upload_link'],
        });
        // A long answer arrives fast (backlog in the browser), then the caller barges in.
        await speak(2.5, 392, 0);
        await sleep(400);
        stats.interruptSentAt = Date.now();
        send({ event: 'interruption', data: null });
        send({ event: 'bot_transcript', text: 'Main reminder ka draft team ko bhej rahi hoon aur', interrupted: true });
        await sleep(1500); // the bot listens while the caller speaks
        send({ event: 'user_transcript', text: 'Theek hai, dhanyavaad.' });
        await speak(0.4, 440);
        send({ event: 'bot_transcript', text: 'Dhanyavaad Sneha ji, aapka din shubh ho.', interrupted: false });
        stats.scriptDone = true;
    })().catch((error) => {
        stats.badFrames.push(`script error ${error.message}`);
    });
}
