// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// End-to-end smoke test of the built app (dist/) in headless Chrome with a fake microphone.
//   npm run build && npm run test:e2e
// Chrome: $CHROME_PATH, else /usr/bin/google-chrome, else Playwright's cached Chromium.
// Screenshots go to e2e/.out/ (also to $SCREENSHOT_DIR when set).
//
// No AWS and no real login: the test seeds an Amplify session with unsigned test tokens that only
// the fake bot accepts, and blocks every request that leaves 127.0.0.1.

import { existsSync, mkdirSync, copyFileSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { startServer, micAnalysis } from './fake-backend.mjs';
import { toneWav } from './wav.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(ROOT, 'dist');
const OUT = join(ROOT, 'e2e', '.out');
const POOL_ID = 'ap-south-1_TestPool01';
const CLIENT_ID = 'e2etestclient0123456789ab';
const USER = 'asha.verma'; // made-up demo login (same as the seeded Document AI users)

const BASE_CONFIG = {
    region: 'ap-south-1',
    userPoolId: POOL_ID,
    userPoolClientId: CLIENT_ID,
    websocketUrl: '/ws',
    idpAppUrl: 'https://d111111abcdef8.cloudfront.net',
    qaProjectId: 'proj-telecaller-qa',
    pipeline: 'transcribe-polly',
    defaultLanguage: 'en-IN',
    maxCallSeconds: 600,
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
let currentStep = '';
// Console lines that a step causes on purpose (Chrome logs every 4xx answer as an error).
const EXPECTED_CONSOLE = [{ step: 'Ring a phone', text: 'status of 409' }];

function findChrome() {
    const candidates = [process.env.CHROME_PATH, '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
    const cache = join(homedir(), '.cache', 'ms-playwright');
    if (existsSync(cache)) {
        for (const dir of readdirSync(cache).filter((d) => d.startsWith('chromium-')).sort().reverse()) {
            candidates.push(join(cache, dir, 'chrome-linux64', 'chrome'));
        }
    }
    return candidates.find((p) => p && existsSync(p));
}

function jwt(payload) {
    const part = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
    return `${part({ alg: 'RS256', kid: 'e2e' })}.${part(payload)}.ZTJlLXNpZ25hdHVyZQ`;
}

function session() {
    const now = Math.floor(Date.now() / 1000);
    const iss = `https://cognito-idp.ap-south-1.amazonaws.com/${POOL_ID}`;
    const idToken = jwt({ sub: 'e2e-sub-1', 'cognito:username': USER, aud: CLIENT_ID, iss, token_use: 'id', iat: now, exp: now + 3600 });
    const accessToken = jwt({ sub: 'e2e-sub-1', username: USER, client_id: CLIENT_ID, iss, token_use: 'access', iat: now, exp: now + 3600 });
    const prefix = `CognitoIdentityServiceProvider.${CLIENT_ID}`;
    return {
        idToken,
        storage: {
            [`${prefix}.LastAuthUser`]: USER,
            [`${prefix}.${USER}.idToken`]: idToken,
            [`${prefix}.${USER}.accessToken`]: accessToken,
            [`${prefix}.${USER}.refreshToken`]: 'e2e-refresh-token',
            [`${prefix}.${USER}.clockDrift`]: '0',
            [`${prefix}.${USER}.signInDetails`]: JSON.stringify({ loginId: USER, authFlowType: 'USER_SRP_AUTH' }),
        },
    };
}

/** Runs one scenario; `fn(details)` may record measurements, saved with that step in results.json. */
async function step(name, fn) {
    currentStep = name;
    const started = Date.now();
    const details = {};
    const extra = () => (Object.keys(details).length ? { details } : {});
    try {
        await fn(details);
        results.push({ name, ok: true, ms: Date.now() - started, ...extra() });
        console.log(`ok   ${name}`);
    } catch (error) {
        results.push({ name, ok: false, ms: Date.now() - started, error: error.message, ...extra() });
        console.log(`FAIL ${name}\n     ${error.message.split('\n').join('\n     ')}`);
    }
}

async function main() {
    if (!existsSync(join(DIST, 'index.html'))) throw new Error('dist/ is missing: run npm run build first');
    const chrome = findChrome();
    if (!chrome) {
        console.log('SKIPPED: no Chrome/Chromium found (set CHROME_PATH)');
        return 0;
    }
    mkdirSync(OUT, { recursive: true });
    const wavPath = join(OUT, 'mic-1khz-48k.wav');
    writeFileSync(wavPath, toneWav({ rate: 48000, freq: 1000, seconds: 4 }));

    const seed = session();
    const server = await startServer({ distDir: DIST, config: BASE_CONFIG, expectedToken: seed.idToken, scenario: 'main' });
    const browser = await chromium.launch({
        executablePath: chrome,
        headless: true,
        args: [
            '--use-fake-ui-for-media-stream',
            '--use-fake-device-for-media-stream',
            `--use-file-for-fake-audio-capture=${wavPath}`,
            '--autoplay-policy=no-user-gesture-required',
        ],
    });

    const shots = [];
    const problems = [];
    const blocked = [];

    async function newPage({ mobile = true, seeded = true, locale = 'en-IN' } = {}) {
        const context = await browser.newContext({
            viewport: mobile ? { width: 390, height: 844 } : { width: 1366, height: 900 },
            deviceScaleFactor: 2,
            isMobile: mobile,
            hasTouch: mobile,
            locale,
        });
        await context.grantPermissions(['microphone', 'clipboard-read', 'clipboard-write'], { origin: server.origin });
        await context.route(/^https?:\/\/(?!127\.0\.0\.1[:/])/, (route) => {
            blocked.push(route.request().url());
            return route.abort();
        });
        if (seeded) {
            await context.addInitScript((items) => {
                for (const [k, v] of Object.entries(items)) window.localStorage.setItem(k, v);
            }, seed.storage);
        }
        const page = await context.newPage();
        page.on('pageerror', (error) => problems.push(`[${currentStep}] pageerror: ${error.message}`));
        page.on('console', (msg) => {
            if (msg.type() !== 'error') return;
            const text = msg.text();
            if (EXPECTED_CONSOLE.some((e) => currentStep.includes(e.step) && text.includes(e.text))) return;
            problems.push(`[${currentStep}] console: ${text}`);
        });
        return { context, page };
    }

    async function shot(page, name) {
        const path = join(OUT, `${name}.png`);
        await page.screenshot({ path });
        shots.push(path);
    }

    // ------------------------------------------------------------------ 1. sign-in screen
    await step('sign-in screen: Document AI login, username or email, no sign-up, recording notice', async () => {
        server.reset({ config: BASE_CONFIG, scenario: 'main' });
        const { context, page } = await newPage({ seeded: false });
        await page.goto(server.origin + '/');
        await page.getByText('DSA Document AI').first().waitFor({ timeout: 15000 });
        await page.getByLabel('Username or email').waitFor();
        await page.getByText('AI assistant · calls are recorded').waitFor();
        assert.equal(await page.getByText('Create Account').count(), 0, 'self sign-up must be hidden');
        await shot(page, '01-sign-in-mobile');
        await context.close();
    });

    // ------------------------------------------------------------------ 2. not configured
    await step('missing config.json (CloudFront serves index.html): clear "not configured" screen', async () => {
        server.reset({ config: null, scenario: 'main' });
        const { context, page } = await newPage({ seeded: false });
        await page.goto(server.origin + '/');
        await page.getByText('Voice app is not configured').waitFor({ timeout: 15000 });
        await page.getByText('config.json was not found next to index.html').waitFor();
        await shot(page, '02-not-configured');
        await context.close();
    });

    // ------------------------------------------------------------------ 3. the main call (Hindi, mobile)
    await step('Hindi call on a phone: mic -> 16 kHz frames, bot voice plays, captions, cards, barge-in, mute, end', async (details) => {
        server.reset({ config: BASE_CONFIG, scenario: 'main' });
        const stats = server.state.stats;
        const { context, page } = await newPage({ locale: 'hi-IN' });
        await page.goto(server.origin + '/');
        await page.locator('.call-btn').waitFor({ timeout: 15000 });
        await page.getByRole('radio', { name: 'हिन्दी' }).click();
        await page.getByText('AI सहायक · यह कॉल रिकॉर्ड की जा रही है').waitFor();
        await page.getByText('AI assistant · this call is recorded').waitFor();
        assert.equal(await page.locator('html').getAttribute('lang'), 'hi');
        await shot(page, '03-before-call-hindi');

        await page.locator('.call-btn').click();
        await page.locator('.shell.phase-live').waitFor({ timeout: 15000 });
        // Record (in the page) when the "assistant speaking" state ends, to time the barge-in.
        await page.evaluate(() => {
            window.__speakingOffAt = [];
            let speaking = !!document.querySelector('.shell.bot-speaking');
            new MutationObserver(() => {
                const now = !!document.querySelector('.shell.bot-speaking');
                if (speaking && !now) window.__speakingOffAt.push(Date.now());
                speaking = now;
            }).observe(document.getElementById('root'), { attributes: true, subtree: true, attributeFilter: ['class'] });
        });
        await page.locator('.shell.bot-speaking').waitFor({ timeout: 8000 }); // greeting audio is playing
        await page.locator('.timer').waitFor();
        await page.locator('.cap-bot', { hasText: 'Varunika Loan Partners' }).waitFor({ timeout: 8000 });

        // Mid-call: lookups and result cards in the timeline
        await page.locator('.cap-user', { hasText: 'Sneha Kulkarni ki file' }).waitFor({ timeout: 15000 });
        await page.locator('.verdict.not-ready', { hasText: 'अधूरी' }).waitFor({ timeout: 8000 });
        await page.locator('.result-list li', { hasText: 'Salary slip for June 2026' }).waitFor();
        await page.locator('.result-big', { hasText: 'Demo Bank से 4.5 lakh' }).waitFor({ timeout: 8000 });
        await page.locator('.reminder-text', { hasText: 'Form 16' }).waitFor({ timeout: 8000 });
        await shot(page, '04-live-call-cards');

        // Barge-in: ~2 s of bot audio is queued in the browser when the interruption arrives; it must
        // stop at once (the "speaking" state then ends after its 350 ms hang-over), not play out.
        while (!stats.interruptSentAt) await sleep(20);
        let stopMs = -1;
        for (let i = 0; i < 60 && stopMs < 0; i++) {
            const offs = await page.evaluate(() => window.__speakingOffAt);
            const first = offs.find((t) => t >= stats.interruptSentAt - 5);
            if (first) stopMs = first - stats.interruptSentAt;
            else await sleep(50);
        }
        // Not cleared, the queued audio would play ~2.1 s more (~2.45 s with the 350 ms hang-over);
        // cleared, only the hang-over remains (~0.36 s measured; the limit leaves room for a busy machine).
        assert.ok(stopMs >= 0 && stopMs < 1200, `bot audio kept playing ${stopMs} ms after the barge-in`);
        await page.locator('.cap-flag', { hasText: 'बीच में रोका' }).waitFor({ timeout: 5000 });
        while (!stats.scriptDone) await sleep(50);
        await page.locator('.cap-bot', { hasText: 'aapka din shubh ho' }).waitFor({ timeout: 5000 });

        // Copy the reminder
        await page.locator('.result-card .small-btn').click();
        await page.locator('.result-card .small-btn', { hasText: 'कॉपी हो गया' }).waitFor({ timeout: 3000 });
        const clip = await page.evaluate(() => navigator.clipboard.readText());
        assert.match(clip, /Form 16/);

        // Mute sends silence (the stream stays open)
        await page.locator('.tool-btn', { hasText: 'म्यूट' }).click();
        server.state.markMuted();
        await page.getByText('माइक्रोफ़ोन म्यूट है').waitFor();
        await sleep(800);
        await page.locator('.tool-btn', { hasText: 'अनम्यूट' }).click();

        // Scroll the captions to the end and capture the whole conversation
        await page.locator('.captions-body').evaluate((el) => (el.scrollTop = el.scrollHeight));
        await sleep(700); // smooth scrolling
        await shot(page, '05-live-call-end-of-script');

        const mic = micAnalysis(stats);
        await page.locator('.call-btn.end').click();
        await page.locator('.end-card').waitFor({ timeout: 5000 });
        await page.getByText('कॉल खत्म हुई').waitFor();
        await page.locator('.call-id code', { hasText: '4f1e9a07b3d2' }).waitFor();
        await page.getByText('Telecaller QA में रिकॉर्डिंग का नाम _4f1e9a.wav पर खत्म होता है।').waitFor();
        const qaHref = await page.locator('.end-card a').getAttribute('href');
        assert.equal(qaHref, 'https://d111111abcdef8.cloudfront.net/projects/proj-telecaller-qa');
        await shot(page, '06-after-call');
        await sleep(300);

        // What the bot received
        const c = stats.connections[0];
        assert.equal(stats.connections.length, 1);
        assert.equal(c.path, '/ws');
        assert.equal(c.tokenVia, 'subprotocol', 'ID token must travel as a subprotocol');
        assert.equal(c.tokenOk, true);
        assert.equal(c.chosen, 'voicebot.v1');
        assert.deepEqual(c.offered, ['voicebot.v1', 'auth.<token>']);
        assert.deepEqual(c.query, { language: 'hi-IN', pipeline: 'transcribe-polly' }, 'no token in the URL');
        assert.deepEqual(stats.badFrames, []);
        assert.deepEqual([...stats.frameBytes], [3200], 'every frame is 100 ms of 16 kHz PCM16');
        assert.ok(mic.seconds > 4, `only ${mic.seconds}s of caller audio`);
        assert.ok(Math.abs(mic.samplesPerSecond - 16000) < 1600, `caller audio rate ${mic.samplesPerSecond}/s`);
        assert.ok(mic.p1000 > 10 * mic.p333 && mic.p1000 > 10 * mic.p3000, `1 kHz test tone not dominant: ${JSON.stringify(mic)}`);
        assert.equal(stats.mutedWindowRms, 0, 'muted frames must be silent');
        assert.deepEqual(stats.clientClose, { code: 1000, reason: 'caller hung up' });
        Object.assign(details, { mic, bargeInStopMs: stopMs });
        await context.close();
    });

    // ------------------------------------------------------------------ 4. desktop layout, English
    await step('desktop: two columns, English hints, live call', async () => {
        server.reset({ config: BASE_CONFIG, scenario: 'main' });
        const { context, page } = await newPage({ mobile: false });
        await page.goto(server.origin + '/');
        await page.getByRole('radio', { name: 'English' }).click();
        await page.getByText("What is still missing in Sneha Kulkarni's file?").waitFor({ timeout: 15000 });
        await shot(page, '07-desktop-idle-english');
        await page.locator('.call-btn').click();
        await page.locator('.verdict.not-ready', { hasText: 'NOT READY' }).waitFor({ timeout: 20000 });
        await page.locator('.reminder-text').waitFor({ timeout: 8000 });
        await shot(page, '08-desktop-live-english');
        await page.locator('.call-btn.end').click();
        await page.locator('.end-card').waitFor({ timeout: 5000 });
        await context.close();
    });

    // ------------------------------------------------------------------ 5. the bot ends the call
    await step('the bot ends the call (time cap): its last words play out, then "assistant ended the call"', async (details) => {
        server.reset({ config: BASE_CONFIG, scenario: 'server-ends' });
        const { context, page } = await newPage();
        await page.goto(server.origin + '/');
        await page.getByRole('radio', { name: 'English' }).click();
        await page.locator('.call-btn').waitFor({ timeout: 15000 });
        await page.evaluate(() => {
            window.__seen = { ending: false, endCardAt: 0, speakingAtEnd: false };
            new MutationObserver(() => {
                const shell = document.querySelector('.shell');
                if (shell && shell.classList.contains('phase-ending')) {
                    window.__seen.ending = true;
                    if (shell.classList.contains('bot-speaking')) window.__seen.speakingAtEnd = true;
                }
                if (!window.__seen.endCardAt && document.querySelector('.end-card')) window.__seen.endCardAt = Date.now();
            }).observe(document.getElementById('root'), { attributes: true, childList: true, subtree: true });
        });
        await page.locator('.call-btn').click();
        await page.locator('.end-card').waitFor({ timeout: 15000 });
        await page.getByText('The assistant ended the call').waitFor();
        await page.getByText('time limit reached').waitFor();
        const seen = await page.evaluate(() => window.__seen);
        const drainMs = seen.endCardAt - server.state.stats.serverClosedAt;
        // 0.6 s of the goodbye was still queued when the bot hung up: it plays (plus a 0.25 s tail for
        // the device's output buffer) before the call ends, instead of being cut off by the close.
        assert.ok(seen.ending && seen.speakingAtEnd, `no "ending" phase with the bot speaking: ${JSON.stringify(seen)}`);
        assert.ok(drainMs >= 450 && drainMs < 2000, `the call ended ${drainMs} ms after the bot hung up`);
        details.drainMs = drainMs;
        await context.close();
    });

    // ------------------------------------------------------------------ 6. language switch by the bot
    await step('the bot switches to Marathi: the screen follows (मराठी)', async () => {
        server.reset({ config: BASE_CONFIG, scenario: 'language' });
        const { context, page } = await newPage();
        await page.goto(server.origin + '/');
        await page.getByRole('radio', { name: 'हिन्दी' }).click();
        await page.locator('.call-btn').click();
        await page.getByText('AI सहाय्यक · हा कॉल रेकॉर्ड केला जात आहे').waitFor({ timeout: 15000 });
        await page.locator('.lang-chip', { hasText: 'मराठी' }).waitFor(); // compact phone layout during the call
        assert.equal(await page.locator('.lang-picker button[lang="mr"]').getAttribute('aria-checked'), 'true');
        await page.locator('.cap-bot', { hasText: 'मराठीत' }).waitFor({ timeout: 5000 });
        await shot(page, '09-marathi-after-switch');
        await page.locator('.call-btn.end').click();
        await context.close();
    });

    // ------------------------------------------------------------------ 7. expired / foreign token
    await step('token refused (close 4001 after accept): "sign in again", no post-call card', async () => {
        server.reset({ config: BASE_CONFIG, scenario: 'main', expectedToken: 'some-other-token' });
        const { context, page } = await newPage();
        await page.goto(server.origin + '/');
        await page.getByRole('radio', { name: 'English' }).click();
        await page.locator('.call-btn').click();
        await page.getByText('Your login has expired. Sign out and sign in again.').waitFor({ timeout: 15000 });
        assert.equal(await page.locator('.end-card').count(), 0);
        await shot(page, '10-login-expired');
        await context.close();
        server.reset({ expectedToken: seed.idToken });
    });

    // ------------------------------------------------------------------ 8. busy
    await step('all call slots busy (close 1013): clear message', async () => {
        server.reset({ config: BASE_CONFIG, scenario: 'busy' });
        const { context, page } = await newPage();
        await page.goto(server.origin + '/');
        await page.getByRole('radio', { name: 'English' }).click();
        await page.locator('.call-btn').click();
        await page.getByText('The assistant is busy with other calls').waitFor({ timeout: 15000 });
        await context.close();
    });

    // ------------------------------------------------------------------ 9. ?token= fallback
    await step('tokenTransport "query" (original sample backend): ?token= and no subprotocol', async () => {
        server.reset({ config: { ...BASE_CONFIG, tokenTransport: 'query' }, scenario: 'server-ends' });
        const { context, page } = await newPage();
        await page.goto(server.origin + '/');
        await page.locator('.call-btn').click();
        await page.locator('.end-card').waitFor({ timeout: 15000 });
        const c = server.state.stats.connections[0];
        assert.equal(c.tokenVia, 'query');
        assert.equal(c.tokenOk, true);
        assert.deepEqual(c.offered, []);
        await context.close();
    });

    // ------------------------------------------------------------------ 10. outbound "Ring a phone"
    await step('"Ring a phone" (outboundCalls): guard-rail refusal, then the call is placed', async () => {
        server.reset({ config: { ...BASE_CONFIG, outboundCalls: true }, scenario: 'main' });
        const { context, page } = await newPage();
        await page.goto(server.origin + '/');
        await page.getByRole('radio', { name: 'English' }).click();
        await page.locator('.outbound summary').click();
        await page.getByLabel("Applicant's full name").fill('Sneha Kulkarni');
        await page.getByLabel('Phone number').fill('98765 43210');
        await page.getByRole('button', { name: 'Ring now' }).click();
        await page.getByText('Not placed: outside the calling window (10:00-19:00 IST)').waitFor({ timeout: 10000 });
        await page.getByRole('button', { name: 'Ring now' }).click();
        await page.getByText('Ringing +91 98XXXXX210 via Plivo. Reference req-7f3a9c21').waitFor({ timeout: 10000 });
        await page.locator('.outbound').scrollIntoViewIfNeeded();
        await shot(page, '11-ring-a-phone');
        const calls = server.state.stats.outbound;
        assert.equal(calls.length, 2);
        assert.ok(calls.every((x) => x.method === 'POST' && x.auth));
        assert.deepEqual(calls[1].body, { to: '+91' + '9876543210', applicant: 'Sneha Kulkarni', language: 'en', provider: '' });
        await context.close();
    });

    await browser.close();
    await server.close();

    await step('no page errors, no CSP violations, nothing left 127.0.0.1', async () => {
        assert.deepEqual(problems, []);
        assert.deepEqual(blocked, []);
    });

    if (process.env.SCREENSHOT_DIR) {
        mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
        for (const p of shots) copyFileSync(p, join(process.env.SCREENSHOT_DIR, p.split('/').pop()));
    }
    const failed = results.filter((r) => !r.ok);
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ chrome, results, shots }, null, 2));
    console.log(`\n${results.length - failed.length}/${results.length} passed · Chrome: ${chrome} · screenshots: ${OUT}`);
    return failed.length ? 1 : 0;
}

main()
    .then((code) => process.exit(code))
    .catch((error) => {
        console.error(error);
        process.exit(1);
    });
