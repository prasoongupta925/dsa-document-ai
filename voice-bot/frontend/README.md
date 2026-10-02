# DSA Document AI — Voice (web app)

The browser side of the all-India voice assistant. A telecaller or a customer signs in with their
**Document AI login**, picks **English / हिन्दी / मराठी** and taps **Call**. Speech goes to the voice bot in
Mumbai (Amazon Transcribe → Bedrock → Amazon Polly Kajal). The screen shows live captions of both sides, the
bot's lookups (file check, indicative eligibility, WhatsApp reminder) as cards, and the notice
**"AI assistant · this call is recorded"**. After the call, the recording goes to *Telecaller QA* in Document AI.

Works on mobile browsers (Android Chrome, iPhone Safari) and on desktop. Needs HTTPS (or `localhost`) for the
microphone.

## Run it

```bash
npm ci                       # ~2 min (112 s measured, warm npm cache)
cp public/config.example.json public/config.json   # then fill in the values (see below)
npm run dev                  # http://localhost:3000
npm run build                # -> dist/  (~25 s; longer on a busy machine)
```

## Runtime configuration: `config.json`

The app reads `config.json` next to `index.html` when it starts, so one build serves every environment.
`dist/` contains no `config.json`: the deploy writes it. On the voice server (`../deploy`), `deploy/app/serve.py`
generates it at run time from `config.example.json` and the stack's settings, so nothing is written by hand
there. The legacy `aws-exports.json` shape, which the sample's CDK stack writes, is still read as a fallback.

| Key | Example | Notes |
|---|---|---|
| `region` | `ap-south-1` | Must match the user pool. |
| `userPoolId` | `ap-south-1_EXAMPLE01` | The **Document AI** user pool (`UserIdentity…`), so the same logins work. |
| `userPoolClientId` | `1exampleclientid0abcdefgh2` | An app client of that pool with `ALLOW_USER_SRP_AUTH`. The IDP's web client already allows it. The bot must accept this client id as the token audience. |
| `websocketUrl` | `/ws` | A path on the same CloudFront, or `wss://host/ws`. |
| `idpAppUrl` | `https://<your-app-url>` | Link back to Document AI (optional). |
| `qaProjectId` | `proj_DemoCalls_QaSample01` | Telecaller QA project, for the "Open Telecaller QA" link after a call (optional). |
| `pipeline` | `transcribe-polly` | Sent as `?pipeline=`; `""` = not sent. |
| `defaultLanguage` | `hi-IN` | `en-IN`, `hi-IN` or `mr-IN`. The user's last choice is remembered. |
| `maxCallSeconds` | `600` | The call cap, as the bot's `CALL_MAX_SECONDS`. The bot says goodbye at the cap; the browser hangs up itself only 30 s later (fallback), so that goodbye is heard. |
| `tokenTransport` | `subprotocol` | `query` sends `?token=` instead, for the original sample backend. |
| `outboundCalls` | `false` | `true` shows **Ring a phone** (POST `/calls/outbound`: Plivo or Exotel rings the applicant). |
| `outboundUrl` | `""` | Default: `/calls/outbound` on the WebSocket's host. |

The app checks the values at start-up and shows "Voice app is not configured" with the exact problem instead
of a broken page. Placeholders such as `ap-south-1_XXXXXXXXX` are rejected.

Write it from the Document AI stack (read-only CloudFormation call) or from environment variables:

```bash
VOICE_QA_PROJECT_ID=proj_... node scripts/write-config.mjs --idp-stack IDP-V2-Application --profile <profile> --out dist/config.json
VOICE_USER_POOL_ID=... VOICE_USER_POOL_CLIENT_ID=... node scripts/write-config.mjs --out dist/config.json
```

In CDK, `s3_deployment.Source.json_data("config.json", {...})` next to `Source.asset("../frontend/dist")` does
the same job.

## Deploy notes (CloudFront in front of the bot)

- Build output is **`dist/`** (the sample used `build/`). `../deploy/deploy.sh` and `update.sh` build and ship it.
- `/ws` must forward `Sec-WebSocket-Protocol` (the sample's policy does): the ID token travels there.
- For **Ring a phone**, route `/calls/*` to the bot: POST allowed, caching disabled, viewer headers forwarded
  (`Authorization`).
- `index.html` carries a Content-Security-Policy. Connections are allowed only to the page's own host, any
  `wss:` host, `https://*.amazonaws.com` (Cognito), and `ws://localhost` / `ws://127.0.0.1` for tests.

## WebSocket protocol

Same as the bot's serializer (`backend/serializers.py`, formerly `JsonSerializer.py`). Text frames, one JSON
object each:

```
connect   new WebSocket(<websocketUrl>?language=hi-IN&pipeline=transcribe-polly, ["voicebot.v1", "auth.<Cognito ID token>"])
browser → {"event":"media","data":"<base64 PCM16 LE, 16 kHz mono, 100 ms = 3200 bytes>"}     hang up = close (1000)
bot     → {"event":"media","data":"<base64 PCM16 16 kHz>"}   {"event":"interruption","data":null}
          {"event":"user_transcript"|"bot_transcript","text":"...","interrupted":false}
          {"event":"tool","name":"file_status","status":"started"}
          {"event":"file_status","verdict":"NOT READY","missing":[...],"mismatches":1}
          {"event":"eligibility","best_lender":"...","amount":"4.5 lakh"}
          {"event":"reminder","channel":"whatsapp","language":"hi","text":"...","placeholders":[...]}
          {"event":"language","language":"mr"}   {"event":"call_started","call_id":"..."}   {"event":"call_ended","reason":"time_cap"}
close codes  4001 sign in again · 1013 busy · 1008 refused · 1011 bot error · 1000 normal end
```

The token is sent as a subprotocol, so it never appears in a URL or a load-balancer log. Parsing lives in
`src/lib/protocol.js`.

## Audio

- One `AudioContext` at the device rate, created inside the tap (iOS unlocks audio only there).
- Microphone (echo cancellation, noise suppression, auto gain) → AudioWorklet `sd-capture`: anti-alias filter,
  exact resampling to 16 kHz, 100 ms PCM16 frames. Mute sends silence, so the stream stays open.
- Bot voice → AudioWorklet `sd-playback`: 80 ms jitter buffer, resampling to the device rate. An
  `interruption` drops queued audio at once (barge-in).
- When the bot hangs up (normal close), the end of its goodbye is still in the jitter buffer (Pipecat
  closes right after sending the last chunk). The microphone stops at once, the status shows "Ending the
  call…", the rest plays out plus a 0.25 s tail for the device's output buffer, then the call ends (at
  most 2.5 s). Tapping End during that time ends it at once.
- The screen stays awake during a call (Wake Lock). If iOS pauses audio, a "Tap to resume audio" button appears.
- The DSP code (`src/audio/dsp.js`) is plain JS shared by the worklet and the unit tests.

## Tests

```bash
npm run lint        # ESLint 10 + react-hooks
npm test            # 54 unit tests (node:test): DSP, protocol, captions, config, strings, phone, close codes
python test/conformance/test_json_serializer.py   # needs a Python with pipecat-ai 1.3.0
npm run build && npm run test:e2e                 # headless Chrome, fake microphone, fake bot
```

- **Conformance:** runs the bot's real serializer against the browser's real encoder and parser: audio both
  ways and every UI event.
- **End to end** (`e2e/smoke.mjs`): 11 scenarios against a local server laid out like CloudFront.
  - Sign-in screen; missing config; Hindi call on a phone; desktop layout; bot ends the call; bot switches to
    Marathi; token refused (4001); busy (1013); `?token=` fallback; Ring a phone.
  - In the Hindi call it checks that the microphone really reaches the bot as 16 kHz PCM16. A 1 kHz test tone
    arrives at 1 kHz, about 16,000 samples per second, in 3200-byte frames.
  - Barge-in stops queued audio (measured: about 355 ms, of which 350 ms is the "speaking" hang-over). Mute
    sends silence.
  - When the bot hangs up with 0.6 s of its goodbye still queued, that audio plays out before the call ends
    (measured: 0.91 s from the close to the end card), instead of being cut off.
  - It also fails on any page error, CSP violation or request leaving 127.0.0.1.
  - Screenshots go to `e2e/.out/` (`SCREENSHOT_DIR=... npm run test:e2e` copies them).

## Privacy

- **Kept in the browser:** the Amplify session and the chosen language (`localStorage`).
- **Not kept:** captions and cards are in memory only and cleared at the next call. Phone numbers typed into
  Ring a phone are not stored and are shown masked.
- **No analytics.** The console never prints tokens or transcripts.
