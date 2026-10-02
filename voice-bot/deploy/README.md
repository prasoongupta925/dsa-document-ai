# Voice hosting: one small server behind CloudFront (ap-south-1)

**What it is.** This folder puts **DSA Document AI - Voice** online: the loan-file voice assistant of
Varunika Loan Partners ("Varunika Loans"). A telecaller or another member of the DSA team opens a web page on a phone
or laptop, signs in with their **Document AI login**, picks English, हिन्दी or मराठी and talks. The bot says that it
is an AI assistant and that the call is recorded, checks the loan file in Document AI (missing documents, indicative
eligibility, the WhatsApp/SMS reminder text) and answers aloud. After the call, the recording goes to the Document AI
project "Telecaller QA – Sample calls". Customers reach the same bot by phone (Plivo, Exotel) or WhatsApp once those
keys are set; the bot checks a caller (caller ID or date of birth) before it shares anything about a file. Everything runs in Mumbai (ap-south-1), including every model call, on one small server that is switched on
only for demos:

```
phone / laptop browser ── HTTPS + WebSocket ──► CloudFront (Indian edges) ──► EC2 t4g.small :8080 (Mumbai, Elastic IP)
Plivo / Exotel media streams ── wss /phone/* ──►        "                      │  Pipecat bot (deploy/app/serve.py → backend/main.py)
Meta WhatsApp webhooks ── https /whatsapp ────►         "                      ├─ Amazon Transcribe streaming (ap-south-1)
WhatsApp call audio (WebRTC, UDP) ──────────────────────────────────────────► │  Bedrock, in-Region: Kimi K2.5 → gpt-oss-120b
                                                                              ├─ Amazon Polly, Kajal voice (ap-south-1)
                                                                              └─ IDP API (SigV4): file check, eligibility, uploads
```

The sample's own `infra/` folder (CDK: Fargate, network load balancer, NAT gateway) and `backend/Dockerfile` **are not used** by this hosting. They are kept unchanged for reference.

**What it costs** (details and sources under "Cost" below; ap-south-1 on-demand, before GST, ₹88 = US$1):

| State | US$ | ₹ |
|---|---|---|
| Server running, per day | about $0.42 | about ₹37 |
| Server stopped, per month (Elastic IP + disk) | about $4.7 | about ₹420 |
| AI services during a call, per minute (Transcribe, Polly, Kimi K2.5) | about $0.03–0.045 | about ₹2.6–4 |

Everything is billed by AWS (credits apply): no AWS Marketplace model, no third-party API key.

## Commands

You need the AWS CLI v2 with admin credentials (`AWS_PROFILE` or the default credential chain), `python3`, `curl`, and Node 22 / npm for
the web app build (`~/.local/node22` is picked up by itself). The region is always ap-south-1. Run the commands from
the voice folder of the repo; every script has `--help`:

```bash
cd voice-bot                            # from the repo root

deploy/deploy.sh                        # first deploy (~12 min), or later: code + stack settings (~3 min)
deploy/status.sh                        # URL, server state, running release, which secrets are set (names only)
deploy/update.sh                        # ship new code only (~1 min; backend only: --skip-frontend-build)
deploy/update.sh --restart              # restart after deploy/secrets.sh (~30 s)
deploy/stop.sh                          # pause between demos: the URL shows a "paused" page (~1 min)
deploy/start.sh                         # resume before a demo (~2 min)
deploy/secrets.sh list                  # phone / WhatsApp keys: list | set NAME | generate NAME | delete NAME | exotel-urls
deploy/destroy.sh                       # remove everything (asks for the stack name; --keep-secrets keeps provider keys)
bash deploy/tests/validate.sh           # check this folder (read-only; --offline skips the AWS and index calls, ~5 min)
```

`deploy.sh`, `update.sh`, `stop.sh`, `start.sh`, `secrets.sh set|generate|delete` and `destroy.sh` change things in
AWS; `status.sh`, `secrets.sh list|exotel-urls`, `deploy.sh --package-only` and `validate.sh` only read.

`deploy.sh` prints the URL, for example `https://d1234abcd.cloudfront.net`. Users sign in there with the **same username or email and password as the Document AI app**. The voice app has its own client in the IDP's user pool, so no new users are needed.

Options for `deploy.sh`:
- `--whatsapp on|off`: opens the WebRTC UDP range for WhatsApp calls.
- `--india-only on|off`: turns on the CloudFront geo block.
- `--refresh-ami`: rebuilds the server on the newest Amazon Linux 2023.
- `--package-only`: builds the zip only and changes nothing in AWS.
- `--keep-failed`: keeps a failed server for debugging.
- `--instance-type t4g.medium|t4g.large`, `--volume-gb 8..12`, `--stack NAME` (default `docai-voice`).

## Demo day

1. **Before:** run `deploy/start.sh` (about 2 minutes). Skip it if `deploy/status.sh` says the server is running.
2. Open the URL on the phone (Chrome or Safari), sign in with the Document AI login and allow the microphone.
3. Pick English, हिन्दी or मराठी and tap Call. The bot first says that it is an AI assistant and that the call is recorded.
4. Ask about a seeded applicant, for example "Rahul Vijay Deshmukh ki file ready hai?", then about the loan amount, then for the reminder text. The screen shows the file check, the indicative eligibility and the WhatsApp/SMS text as cards.
5. Hang up. The recording (caller on the left channel, bot on the right) goes to the Document AI project "Telecaller QA – Sample calls", where the IDP transcribes and reviews it. The server keeps no copy.
6. **After:** run `deploy/stop.sh`. The URL then shows the paused page, and the stopped server costs about $4.7 a month.

Phone calls (Plivo, Exotel) and WhatsApp calls join the same bot once their keys are set (see "Phone and WhatsApp: switch on later" below).

## What the stack creates (`template.yaml`)

| Resource | Details |
|---|---|
| EC2 `t4g.small` | Graviton, Amazon Linux 2023 arm64 (public SSM parameter), default VPC, public subnet, Elastic IP. IMDSv2 only (hop limit 1). Encrypted 12 GB gp3. CPU credits "unlimited": extra cost ($0.04 per vCPU-hour) only if the 24-hour average stays above 20 % per vCPU. |
| Security group | **In:** TCP 8080 only from the CloudFront origin-facing prefix list (`pl-9aa247f3`). With `--whatsapp on`, also UDP 32768–60999 from anywhere for WebRTC media. **Out:** TCP 443 and 8443 (Transcribe streaming); everything with `--whatsapp on`. No SSH; admin access is SSM only. |
| CloudFront | HTTPS with the default certificate. `PriceClass_200`, which includes the Indian edges. The app, `/ws`, `/phone/*`, `/whatsapp` and `/calls/*` are never cached and get every viewer header and WebSocket. `/assets/*` (hashed Vite files) is cached at the edge. A custom `X-Origin-Verify` header carries a secret. While the server is stopped, `/offline.html` (a "paused" page) is served from S3. |
| Cognito app client | `docai-voice-web` in the **IDP's** user pool. SRP sign-in and refresh only, no client secret, tokens valid 60 min, refresh token 7 days. |
| IAM role (server) | See "Instance role" below. |
| S3 code bucket | Release zips and the paused page. Private, TLS only, SSE-S3. **Every object expires after 7 days.** |
| SSM parameters | `/docai-voice/stack-env` (non-secret settings as JSON) and `/docai-voice/stack-extra-env` (template parameter `ExtraEnvJson`). |

**Instance role**, least privilege:
- **Speech:** `transcribe:StartStreamTranscription(+WebSocket)` and `polly:SynthesizeSpeech`, only when the request's region is ap-south-1.
- **Bedrock:** `InvokeModel` and `InvokeModelWithResponseStream` on exactly two models, the LLM chain: `moonshotai.kimi-k2.5` and `openai.gpt-oss-120b-1:0`, as ap-south-1 foundation models (invoked in-Region). `tests/check_template.py` fails if this list and `LLM_MODEL_CHAIN` ever differ.
- **Explicit DENY** (it holds even if someone later attaches a broader policy to the role):
  - **every model ARN that is not an ap-south-1 foundation model** (`NotResource: arn:aws:bedrock:ap-south-1::foundation-model/*`). That covers:
    - the `global.`, `apac.` and `in.` inference profiles. `in.` profiles also route to Hyderabad (ap-south-2);
    - other Regions' models;
    - application inference profiles and prompt routers;
    - provisioned, custom and imported models;
    - Marketplace endpoints and Bedrock system tools;
  - Marketplace providers, which are billed to the card, not to credits: anthropic, cohere, twelvelabs, stability, writer, luma, `openai.gpt-5*` and `openai.gpt-6*`. This covers model and inference-profile ARNs, the default prompt router `anthropic.*` and Bedrock Marketplace model endpoints, for every `bedrock:InvokeModel*` action (Converse uses the same actions);
  - Nova Sonic (`amazon.nova-sonic*`, `amazon.nova-2-sonic*`). IAM has no separate bidirectional-stream action: Nova Sonic's stream is authorised as `bedrock:InvokeModel`, so the deny names the models;
  - the newer entry points this server never uses: Bedrock's OpenAI-compatible projects API (`bedrock-mantle:*`) and Bedrock API keys (`bedrock:CallWithBearerToken`);
  - `aws-marketplace:Subscribe`.
- **IDP API:** `execute-api:Invoke` on the IDP HTTP API. The bot calls six routes: `GET /projects`, `POST …/file-check`, `POST …/eligibility/calculate`, `GET …/eligibility/inputs`, `POST …/documents` and `PUT …/documents/<id>/status`. Explicit DENY on:
  - every `DELETE` and `PATCH`, and creating projects;
  - applicant erase, lender login, eligibility-input edits;
  - webhook/integration settings, prompts, project agents, SageMaker;
  - jobs that run models or batches: file Q&A (`file-check/ask`), graph rebuilds, dataset queries, document re-analysis.

  Not denied, because IAM's `*` would also catch the upload's `PUT …/documents/<id>/status`: editing a project (`PUT /projects/<id>`).
- **Code:** `s3:GetObject` on `releases/*` of the code bucket.
- **Parameters:** `ssm:GetParameter(s)/GetParametersByPath` on `/docai-voice/*`, with an explicit DENY on every other parameter. `AmazonSSMManagedInstanceCore` alone would allow `GetParameter` on all of them.
- `AmazonSSMManagedInstanceCore`, for Session Manager and Run Command.

## How a release gets onto the server

1. `deploy.sh` or `update.sh` builds the web app (`npm ci && npm run build` in `frontend/`). The output goes to `frontend/dist`, or `frontend/build`, whichever is newer.
2. It zips:
   - `backend/`, without `.env*`, tests, `__pycache__`, recordings or `Dockerfile`;
   - the built web app as `frontend/`;
   - `deploy/app/` and `deploy/instance/`;
   - `RELEASE.json`.
3. The zip goes to `s3://<bucket>/releases/<id>.zip` and is also copied to `releases/latest.zip`.
4. On the **first boot** of a new server, user-data:
   - installs gcc, git and cfn-bootstrap;
   - sets journald so that no entry gets older than 7 days (12-hour files, each deleted once its newest entry is 6 days old), and turns off core dumps;
   - creates the `voicebot` user;
   - installs **uv 0.11.16** (SHA-256 pinned) and **Python 3.12** (`uv python install`);
   - downloads `latest.zip` and runs its `deploy/instance/release.sh`;
   - signals CloudFormation.
5. `release.sh`, on first boot and on every update (through SSM Run Command):
   - unpacks to `/opt/docai-voice/releases/<id>`;
   - creates that release's own venv (`uv venv --python 3.12`);
   - installs `backend/requirements.txt` together with `deploy/app/requirements.txt`;
   - downloads NLTK `punkt_tab` once;
   - installs the systemd unit;
   - switches the `current` symlink and restarts;
   - waits for `http://127.0.0.1:8080/health`.

   If the new release doesn't answer, it puts the previous one back. It keeps the 3 newest releases.
6. systemd runs `deploy/app/serve.py` as the unprivileged user `voicebot` (unit `docai-voice`). The code is read-only for the service. It can write only to `/var/lib/docai-voice` and its own `/tmp`. Every start of the unit first rotates and vacuums the journal, and every start and stop deletes leftover recordings and temp files.

The first deploy runs in two steps:
1. The stack without the server: bucket, CloudFront, client, role, settings.
2. The code upload, then the server.

That way the server never boots before its code exists.

Later deploys pin the server's current AMI, so a new Amazon Linux release never silently replaces the server. Use `--refresh-ami` when you want it.

## Contract with `backend/` and `frontend/`

`deploy/app/serve.py` starts the backend the way it starts itself: `backend/main.py`'s `main()` (settings, JSON logging, uvicorn). A small hook on `uvicorn.Config` puts three layers around the app the backend hands to uvicorn: access log, origin check, web app. It also forces `proxy_headers` off, so `X-Forwarded-For` is never trusted. The backend code is not changed. Without a `main()`, it serves `main:app` or `create_app()` (`APP_IMPORT` picks another module).

**Environment the backend gets.** It's in place before `main.py` is imported. The names are the ones `backend/config.py` reads; the stack writes them to `/docai-voice/stack-env`:

| Variable | Value |
|---|---|
| `AWS_REGION`, `AWS_DEFAULT_REGION` | `ap-south-1` (the unit sets them too; the backend refuses any other region) |
| `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_IDS` (also `USER_POOL_ID`, `APP_CLIENT_ID`) | The IDP's pool and the voice app client. The backend checks ID tokens against them. |
| `IDP_API_URL` | IDP backend HTTP API (SigV4, `execute-api`) |
| `IDP_APP_URL` | IDP web app, for the "back to Document AI" link in `config.json` |
| `PUBLIC_HOST` | The CloudFront domain. Plivo signature URLs and the `wss://` URLs in Plivo/Exotel answers use it. |
| `ALLOWED_ORIGINS` | `https://<CloudFront domain>`: the only browser origin allowed on `/ws` and `/calls/outbound` |
| `LLM_MODEL_CHAIN`, `LLM_MUMBAI_ONLY` | `moonshotai.kimi-k2.5,openai.gpt-oss-120b-1:0` and `true`. Both are in-Region model ids, invoked in ap-south-1. Whatever the chain says, the server role refuses every inference profile (`global.`, `apac.`, `in.`), so a wrong chain fails instead of leaving Mumbai. |
| `MAX_CONCURRENT_CALLS` | 4 on t4g.small, 8 on t4g.medium, 16 on t4g.large |
| `LOG_LEVEL`, `LOG_JSON` | `INFO`, `true` (one JSON object per line in journald) |
| `RECORDINGS_DIR`, `TMPDIR`, `HOME` | From the unit: `/var/lib/docai-voice/{recordings,tmp}` and `/var/lib/docai-voice`. Files left there are deleted at every service start and stop. |
| `NLTK_DATA`, `NUMBA_CACHE_DIR` | From the unit: pre-filled tokenizer data and a writable cache |
| Anything in `/docai-voice/secrets/*` | Same name as the variable (`deploy/secrets.sh`): provider keys, `OUTBOUND_ALLOWED_TO`, `TELECALLER_NUMBER` |
| Anything in `ExtraEnvJson` | Non-secret extras, e.g. `{"DSA_NAME": "...", "QA_PROJECT_ID": "...", "DSA_PHONE": "..."}` |

Precedence: variables the process already has, then secrets, then extra-env, then stack-env. Values are never written to disk or logged.

**Paths `serve.py` answers itself.** Everything else goes to the backend:

| Path | Response |
|---|---|
| `/`, `/index.html`, any built file | Static files. `/assets/*` gets `immutable`, `index.html` gets `no-cache`. |
| `/config.json`, `/aws-exports.json`, `/runtime-config.json` | Generated at run time from the build's own `config.json` or `config.example.json`. Its known keys are filled in: region, user pool id, app client id, `wss://<domain>/ws`, IDP link. The canonical keys `region`, `userPoolId`, `userPoolClientId`, `websocketUrl` and `idpAppUrl`, and the sample's `amplify.Auth.Cognito` / `websocket.apiUrl`, are always present. `outboundCalls` and `providers` follow the phone secrets with the backend's own rules, so "Ring a phone" appears only when Plivo or Exotel is configured (`OUTBOUND_CALLS_UI=true\|false` overrides). `defaultLanguage`, `maxCallSeconds` and `qaProjectId` follow `DEFAULT_LANGUAGE`, `CALL_MAX_SECONDS` and `QA_PROJECT_ID`. There is no identity pool, so browsers never get AWS credentials. The frontend's own parser (`frontend/src/lib/config.js`) accepts the result with no errors or warnings (`tests/test_real_backend.sh`). |
| `/release.json` | `{id, built_at}` of the running release |
| Unknown `GET` with `Accept: text/html` | `index.html` if the backend has no such route (client-side routes) |

**Other rules:**
- **Origin check:** a request without the CloudFront `X-Origin-Verify` header gets 403, or WebSocket close 1008. The exception is 127.0.0.1 with no `X-Forwarded-For` (CloudFront always adds one). The header is stripped before the backend sees it.
- **Health check:** `GET /health` on the backend must answer 200. `release.sh` and the scripts use it.
- **Logs:** journald keeps them 7 days: `journalctl -u docai-voice`.
  - The access log has one line per request, with no query string. `/phone/*` paths are cut after three segments, so signed tokens and URL keys never reach the log.
  - uvicorn's own access log is off, because it would print `/ws?token=<Cognito JWT>`.
  - uvicorn still logs every WebSocket handshake with its path (`"WebSocket /phone/exotel/ws/<key>?…" [accepted]`). A filter on uvicorn's own loggers cuts those paths the same way and hides token-like query values. It is a logger filter, so it stays in place when the backend replaces the logging handlers. `tests/test_real_backend.sh` switches phones and WhatsApp on with made-up keys and checks that no key, token, secret or caller number reaches the log.
  - Lines go through the backend's JSON logging, which also masks tokens, PAN, Aadhaar, phone numbers and e-mails.

## Phone and WhatsApp: switch on later

All three channels are built and tested with fake provider messages, and **off** until their keys exist. Keys live
only in SSM Parameter Store as SecureStrings named `/docai-voice/secrets/<NAME>`; the server reads each one at
start-up as the environment variable `<NAME>`. Put them there with `deploy/secrets.sh` (it asks for the value with
hidden input; never on the command line, never in git), then run `deploy/update.sh --restart`.

| SSM parameter `/docai-voice/secrets/…` | Channel | Value | Where it comes from |
|---|---|---|---|
| `PHONE_URL_KEY` | Plivo, Exotel | 40 random characters (the backend needs 32 or more) | `deploy/secrets.sh generate PHONE_URL_KEY` |
| `PLIVO_AUTH_ID` | Plivo | Auth ID (`MA…`) | Plivo console, Overview |
| `PLIVO_AUTH_TOKEN` | Plivo | Auth Token | Plivo console, Overview |
| `PLIVO_NUMBER` | Plivo | the rented number, E.164 (`+9122…`) | Plivo console, Phone Numbers |
| `EXOTEL_SID` | Exotel | account SID | Exotel dashboard, API settings |
| `EXOTEL_API_KEY` | Exotel | API key | Exotel dashboard, API settings |
| `EXOTEL_API_TOKEN` | Exotel | API token | Exotel dashboard, API settings |
| `EXOTEL_EXOPHONE` | Exotel | the ExoPhone used as caller ID | Exotel dashboard, ExoPhones |
| `OUTBOUND_ALLOWED_TO` | outbound calls | the only numbers that may be rung, `+91…`, comma-separated (team phones with signed consent) | you |
| `TELECALLER_NUMBER` | Plivo (optional) | the line "transfer to a human" dials, `+91…` | you |
| `WHATSAPP_TOKEN` | WhatsApp | permanent (system user) access token | Meta Business settings, System users |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp | phone number id (digits) | Meta app, WhatsApp, API setup |
| `WHATSAPP_APP_SECRET` | WhatsApp | app secret (checks the webhook signature) | Meta app, App settings, Basic |
| `WHATSAPP_WEBHOOK_VERIFICATION_TOKEN` | WhatsApp | any random string, also typed into Meta | `deploy/secrets.sh generate WHATSAPP_WEBHOOK_VERIFICATION_TOKEN` |

Outbound calls ("Ring a phone" in the web app, or `POST /calls/outbound`) also obey `CALL_WINDOW` (default
10:00-19:00 India time), `CALL_DAYS` (default mon-sat) and `OUTBOUND_MAX_ATTEMPTS_PER_DAY` (default 3 per number).
These and any other non-secret setting of `backend/.env.example` (for example `DSA_PHONE`, `QA_PROJECT_ID`) can be
set the same way: `deploy/secrets.sh set NAME`.

**Plivo** (Indian numbers; inbound and outbound):
1. `deploy/secrets.sh generate PHONE_URL_KEY`
2. `deploy/secrets.sh set PLIVO_AUTH_ID`, then `set PLIVO_AUTH_TOKEN` and `set PLIVO_NUMBER`.
3. `deploy/secrets.sh set OUTBOUND_ALLOWED_TO`, and optionally `set TELECALLER_NUMBER`.
4. `deploy/update.sh --restart`. `https://<url>/health` then shows `"plivo": true` and `"outbound_plivo": true`, and the web app shows "Ring a phone".
5. In the Plivo console, create an XML application with the Answer URL `https://<url>/phone/plivo/answer` and the Hangup URL `https://<url>/phone/plivo/status`, both POST, and link the number to it. Every Plivo request is checked against its `X-Plivo-Signature-V3` signature.

**Exotel** (ExoPhones; inbound and outbound):
1. `deploy/secrets.sh generate PHONE_URL_KEY` (skip it if Plivo already set one).
2. `deploy/secrets.sh set EXOTEL_SID`, then `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_EXOPHONE` and `OUTBOUND_ALLOWED_TO`.
3. `deploy/update.sh --restart`. `/health` then shows `"exotel": true` and `"outbound_exotel": true`.
4. `deploy/secrets.sh exotel-urls` prints the two URLs for Exotel. In App Bazaar, give the ExoPhone a flow with a **Voicebot** applet whose URL is the printed `wss://<url>/phone/exotel/ws/<key>?sample-rate=16000`, followed by a **Connect** applet to the telecaller line (when the caller says "agent", the bot ends the stream and the flow continues there). The status callback URL is optional for inbound calls; outbound calls set it themselves.

   Exotel does not sign its streams, so the `<key>` in these URLs is the only check: keep them private. It is derived from `PHONE_URL_KEY` (HMAC), so the URLs never reveal the key that seals call tokens. A new `PHONE_URL_KEY` changes the URLs: paste them again.

**WhatsApp Business Calling** (customer-initiated calls are free in India):
1. `deploy/secrets.sh set WHATSAPP_TOKEN`, then `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, and `deploy/secrets.sh generate WHATSAPP_WEBHOOK_VERIFICATION_TOKEN`.
2. `deploy/deploy.sh --whatsapp on`. This opens the WebRTC UDP range (32768–60999) and the egress WebRTC needs, ships the code and restarts the service.
3. In the Meta app, set the WhatsApp webhook URL to `https://<url>/whatsapp` with the same verification token, subscribe to the `calls` field, and turn on calling for the number (WhatsApp Manager or the phone number settings API). `/health` shows `"whatsapp": true`. Every webhook is checked against its `X-Hub-Signature-256` (the app secret).

Without `--whatsapp on`, the security group blocks the WebRTC media, even when the WhatsApp secrets are set. `deploy/status.sh` warns about this. NAT discovery uses AWS's public STUN server in Mumbai (`stun:stun.kinesisvideo.ap-south-1.amazonaws.com:443`); set `WHATSAPP_STUN_URLS` to change it.

**Switch a channel off:** `deploy/secrets.sh delete NAME` for its keys, then `deploy/update.sh --restart` (for WhatsApp also `deploy/deploy.sh --whatsapp off`). Without its keys a channel is off: its routes answer 404 or 401, and "Ring a phone" refuses to dial.

## Cost

On-demand prices in ap-south-1 from the AWS Price List API (Sep 2026), before GST, ₹88 = US$1:

| State | What you pay for | US$ | ₹ |
|---|---|---|---|
| **Running, per day** | t4g.small $0.0112/h × 24, public IPv4 $0.005/h × 24, 12 GB gp3 at $0.0912/GB-month | **$0.42** | **₹37** |
| Running 24×7, per month | the same × 730 h | $12.9 | ₹1,140 |
| **Stopped, per month** | Elastic IP $3.65 + 12 GB gp3 $1.09. The EIP is billed whether or not it is attached. | **$4.7** | **₹420** |
| Demo pattern: stopped, plus 2 h × 10 demo days | $4.7 + 20 h × $0.0112 (the IP and disk are already in the $4.7) | ~$4.9 | ~₹435 |
| CloudFront, S3, SSM, CloudWatch | free tier, or a few cents. No load balancer, no NAT gateway, no CloudWatch Logs. | ~$0 | ~₹0 |

Usage on top, per call minute (estimate): ≈ $0.03–0.045 (≈ ₹2.6–4) for the AI services during the call.
- Transcribe streaming: the Price List API gives $0.0001667/s ($0.010/min) for Mumbai, but the public pricing page lists $0.024/min (tier 1). Budget with the higher one.
- Polly neural: $16 per 1M characters, ≈ $0.007 at half talk time.
- Kimi K2.5: $0.72 / $3.60 per 1M tokens, ≈ $0.012. The fallback, gpt-oss-120b, costs $0.18 / $0.71.
- After the call, the IDP's QA transcription (Transcribe batch): $0.006/min in the Price List API, up to $0.024/min on the pricing page.

The sample's own CDK stack (Fargate 2 vCPU/8 GB + NAT gateway + load balancer) would cost ≈ ₹13,600 a month before the first call.

## Notes and limits

- **Data residency:**
  - Transcribe, Polly, Kimi K2.5, gpt-oss-120b, the IDP and the server are all in ap-south-1. Both models are invoked in-Region, with no inference profile.
  - The brief also listed `global.amazon.nova-2-lite-v1:0` as a last fallback. It is left out because the project rule is Mumbai only: no `global.`, `apac.` or `in.` profiles. The server role refuses them explicitly.
  - If both models fail on a turn, the bot does not try a model outside Mumbai. It asks the caller to say it again; a second failure in the same call ends it with an apology and a promise that the team will call back (`backend/bot.py`).
  - CloudFront edges are global, but the WebSocket traffic of Indian users enters at Indian edges (`PriceClass_200`).
  - WhatsApp calls learn the server's public address from AWS's STUN server in Mumbai (Kinesis Video Streams); no audio goes through it.
  - Software comes from outside AWS only while it is installed: uv and Python from GitHub, Python packages from PyPI, NLTK's tokenizer data. No call data goes there.
- **CloudFront to server:** browsers, Plivo, Exotel and Meta reach CloudFront over HTTPS. The hop from CloudFront to the server is plain HTTP on port 8080 across AWS's network. The security group admits only CloudFront's origin-facing addresses, and the app refuses requests without the secret header. CloudFront accepts only a publicly trusted certificate that matches the origin's name, and that name is AWS's `ec2-…compute.amazonaws.com`. Encrypting this hop too needs a domain of our own pointing at the Elastic IP, with a certificate on the server.
- **Long silences:** CloudFront closes a WebSocket after 10 minutes without a byte from the server. Calls end at `CALL_MAX_SECONDS` (600), and the bot speaks up when the caller has been silent for `USER_IDLE_SECONDS`, so no call gets near that limit.
- **Retention:**
  - The code bucket expires everything after 7 days.
  - journald: no entry gets older than 7 days. Retention works per file, so files rotate every 12 hours and are deleted once their newest entry is 6 days old. Every service start (boot, deploy, restart) also rotates and vacuums, so a server that was stopped for weeks drops its old logs as soon as it starts. Core dumps are off.
  - Leftover recordings and temp files are deleted at every service start and stop. The backend deletes each recording right after uploading it to the IDP, which keeps it 7 days.
  - While the server is stopped, its encrypted disk still holds the last days of logs (events and ids, no transcripts) until it starts again. `destroy.sh` deletes the disk.
  - **SSM Run Command keeps its command history for 30 days.** That's AWS's setting and can't be changed. Our commands contain only S3 keys and release ids. Their output holds install steps and, when a release fails, only the log lines of that start, never older lines that may be about calls.
- **Stopped server:**
  - The URL shows the paused page with status 503 and reloads every 30 s. Only CloudFront's 504 (no answer from the server) is mapped to it. While the service restarts (a few seconds during `update.sh`), CloudFront shows its own 502 page.
  - Phone calls and WhatsApp calls fail while the server is stopped.
  - Release zips older than 7 days are gone, but the server keeps its installed release on disk.
  - The paused page is in the same bucket, so it also expires 7 days after the last `stop.sh`. A server left stopped longer than that shows CloudFront's plain error page instead, until `start.sh` or `stop.sh` uploads the page again.
- **One server:** a deploy restarts the service and drops calls in progress. Deploy outside calling hours.
- **First-boot script changes:** the template's user-data runs only on a new server. When it changes, CloudFormation stops and starts the existing server (about 2 minutes offline), but the script does not run again. Release-level changes (`deploy/instance/*`, the systemd unit) ship with `update.sh` instead.
- **Rotating the CloudFront secret:**
  1. `deploy/secrets.sh` refuses to touch `ORIGIN_VERIFY`.
  2. Copy the current value to `ORIGIN_VERIFY_PREVIOUS` with the AWS CLI (the app accepts both).
  3. Put a new 43-character value in `ORIGIN_VERIFY`.
  4. Run `deploy/update.sh --restart`.
  5. Run `deploy/deploy.sh` with `CFN_SECRET_PARAMS=OriginVerifySecret=<new>` exported.
  6. Delete `ORIGIN_VERIFY_PREVIOUS`.
- **SSM-backed names:** the backend can also resolve `NAME_SSM=/path` itself. The server role may read only `/docai-voice/*`, so put such secrets there (`deploy/secrets.sh`), not under other paths like `/idp-v2/...`.
- **Logs and shell on the server:** `aws ssm start-session --target <instance id>` (needs the Session Manager plugin), then `journalctl -u docai-voice -f`. If a first boot fails, `deploy.sh` prints the instance's console output.
- **Not done here** (needs your go-ahead and AWS writes): actually running `deploy.sh`, buying a Plivo number, the Meta WhatsApp Business setup.
