#!/usr/bin/env bash
# deploy/app/serve.py in front of the REAL backend/main.py and the built frontend (frontend/dist), over real
# sockets, with the settings the stack writes. No AWS calls: SSM is skipped, no tool runs, no token is valid.
#   1. the stack's settings (browser channel)
#   2. phone (Plivo, Exotel) and WhatsApp switched on with made-up keys: their routes through serve.py, the
#      Plivo signature over the CloudFront URL, and no key, token or caller number in the log
#   PYTHON=<venv with backend/requirements.txt>/bin/python bash deploy/tests/test_real_backend.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"   # voice-bot/
PY="${PYTHON:-python3}"
FRONT="${FRONTEND_OUT:-$ROOT/frontend/dist}"
(cd "$ROOT/backend" && AWS_REGION=ap-south-1 "$PY" -W ignore -c "import main, websockets; assert callable(main.main)" >/dev/null 2>&1) \
  || { echo "skipped: $PY cannot import backend/main.py (install backend/requirements.txt in a venv)"; exit 0; }
[ -f "$FRONT/index.html" ] || { echo "skipped: no built frontend in $FRONT (npm run build)"; exit 0; }
W=$(mktemp -d); SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
LAN=$(hostname -I 2>/dev/null | awk '{print $1}'); HOSTNAME_CF=dtest0123456.cloudfront.net
pass=0; fail=0; PID=""; PORT=""
# The LLM settings exactly as the stack writes them (template.yaml, StackEnvParameter), one NAME=VALUE per line.
mapfile -t STACK_LLM < <(python3 - "$HERE" <<'EOF'
import json, re, sys
sys.path.insert(0, sys.argv[1])
import yaml
from check_template import CfnLoader, to_long_form
t = to_long_form(yaml.load(open(sys.argv[1] + "/../template.yaml").read(), Loader=CfnLoader))
text = t["Resources"]["StackEnvParameter"]["Properties"]["Value"]["Fn::Sub"][0]
env = json.loads(re.sub(r"\$\{[^}]+\}", "X", text))
for name in ("LLM_MODEL_CHAIN", "LLM_MUMBAI_ONLY"):
    print(f"{name}={env[name]}")
EOF
)
[ "${#STACK_LLM[@]}" = 2 ] || { echo "FAIL cannot read the LLM settings from template.yaml"; exit 1; }
CHAIN=$(printf '%s\n' "${STACK_LLM[@]}" | sed -n 's/^LLM_MODEL_CHAIN=//p')
ok() { pass=$((pass + 1)); echo "ok   $*"; }
bad() { fail=$((fail + 1)); echo "FAIL $*"; }

start_serve() {  # start_serve LOG [NAME=VALUE...]: serve.py with the stack's settings plus the given ones
  local log=$1; shift
  PORT=$(( 20000 + RANDOM % 20000 ))
  env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY DOCAI_SKIP_SSM=1 ORIGIN_VERIFY="$SECRET" \
    AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1 AWS_EC2_METADATA_DISABLED=true \
    COGNITO_USER_POOL_ID=ap-south-1_EXAMPLE01 COGNITO_APP_CLIENT_IDS=4abcdefghijklmnopqrstuvw12 \
    USER_POOL_ID=ap-south-1_EXAMPLE01 APP_CLIENT_ID=4abcdefghijklmnopqrstuvw12 \
    IDP_API_URL=https://abcdefghij.execute-api.ap-south-1.amazonaws.com IDP_APP_URL=https://didp.cloudfront.net \
    PUBLIC_HOST=$HOSTNAME_CF ALLOWED_ORIGINS=https://$HOSTNAME_CF MAX_CONCURRENT_CALLS=4 LOG_JSON=true LOG_LEVEL=INFO \
    "${STACK_LLM[@]}" PORT=$PORT FRONTEND_DIR="$FRONT" RECORDINGS_DIR="$W/rec" TMPDIR="$W" RELEASE_FILE="$W/RELEASE.json" "$@" \
    "$PY" -W ignore "$HERE/../app/serve.py" > "$log" 2>&1 &
  PID=$!
  for _ in $(seq 1 150); do curl -fsS -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null && return 0; sleep 0.2; done
  return 1
}
stop_serve() {  # SIGTERM, as systemd does; returns 1 if it had to be killed
  [ -n "$PID" ] || return 0
  kill -TERM "$PID" 2>/dev/null
  for _ in $(seq 1 50); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
  local rc=0
  if kill -0 "$PID" 2>/dev/null; then rc=1; kill -KILL "$PID" 2>/dev/null; fi
  PID=""
  return $rc
}
trap 'stop_serve; rm -rf "$W"' EXIT
echo '{"id": "real-backend-test", "built_at": "now"}' > "$W/RELEASE.json"
H=(-H "X-Origin-Verify: $SECRET")
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# ------------------------------------------------------------------------------ 1. the stack's settings
start_serve "$W/log"
curl -s "http://127.0.0.1:$PORT/health" | grep -q '"browser":true' && ok "real backend /health (browser channel on)" || bad "health"
if [ -n "$LAN" ]; then
  [ "$(code "http://$LAN:$PORT/health")" = 403 ] && ok "without the CloudFront header: 403" || bad "guard"
  [ "$(code "${H[@]}" "http://$LAN:$PORT/")" = 200 ] && ok "web app index.html" || bad "index"
  worklet=$(ls "$FRONT/assets" | grep -m1 '\.js$')
  ctype=$(curl -s -o /dev/null -w '%{content_type}' "${H[@]}" "http://$LAN:$PORT/assets/$worklet")
  case "$ctype" in text/javascript*) ok "assets served as JavaScript";; *) bad "asset type $ctype";; esac
  curl -s "${H[@]}" "http://$LAN:$PORT/config.json" > "$W/config.json"
  python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); assert c["websocketUrl"]=="wss://'$HOSTNAME_CF'/ws" and c["userPoolClientId"]=="4abcdefghijklmnopqrstuvw12" and c["outboundCalls"] is False' "$W/config.json" \
    && ok "config.json for this stack (no 'Ring a phone' without phone keys)" || bad "config.json"
  NODE=$(command -v node || ls "$HOME"/.local/node*/bin/node 2>/dev/null | head -n1)
  if [ -n "$NODE" ] && [ -f "$ROOT/frontend/src/lib/config.js" ]; then
    cat > "$W/check.mjs" <<EOF
import { normalizeConfig } from '$ROOT/frontend/src/lib/config.js';
import { readFileSync } from 'node:fs';
const r = normalizeConfig(JSON.parse(readFileSync(process.argv[2], 'utf8')), { pageHref: 'https://$HOSTNAME_CF/' });
if (r.errors.length || r.warnings.length) { console.log(JSON.stringify(r)); process.exit(1); }
EOF
    "$NODE" "$W/check.mjs" "$W/config.json" && ok "the frontend's own config parser accepts it (no errors, no warnings)" || bad "frontend parser"
  fi
  [ "$(code -X POST "${H[@]}" "http://$LAN:$PORT/phone/plivo/answer")" = 404 ] && ok "phone routes off without keys" || bad "phone off"
  "$PY" - "$LAN" "$PORT" "$SECRET" "$HOSTNAME_CF" <<'EOF' && ok "browser WebSocket reaches the backend's sign-in check (4001)" || bad "websocket"
import asyncio, sys, websockets
host, port, secret, cf = sys.argv[1:5]
async def main():
    headers = {"X-Origin-Verify": secret, "Origin": f"https://{cf}"}
    async with websockets.connect(f"ws://{host}:{port}/ws?language=hindi", additional_headers=headers,
                                  subprotocols=["voicebot.v1", "auth.not-a-token"]) as ws:
        try:
            await asyncio.wait_for(ws.recv(), 10)
        except websockets.exceptions.ConnectionClosed as e:
            assert e.rcvd and e.rcvd.code == 4001, e
            return
    raise SystemExit("not closed")
asyncio.run(main())
EOF
fi
ready=$(grep -m1 'voice bot ready' "$W/log")
case "$ready" in *"$CHAIN"*) ok "the backend keeps the stack's whole Mumbai chain ($CHAIN)";; *) bad "startup models: ${ready:0:200}";; esac
grep -q -i 'cross-Region\|outside ap-south-1' "$W/log" && bad "the backend notes a cross-Region model" || ok "no cross-Region model note in the log"
grep -q '"logger": "docai.serve"' "$W/log" && ok "access lines go through the backend's JSON logging" || bad "log format"
grep -q "$SECRET" "$W/log" && bad "origin secret in the log" || ok "no origin secret in the log"
stop_serve && ok "stops on SIGTERM" || bad "did not stop on SIGTERM"
[ "$fail" = 0 ] || { echo "--- log:"; tail -n 20 "$W/log" | cut -c1-240; }

# ------------------------------------------------------------------------------ 2. phone and WhatsApp on
if [ -n "$LAN" ]; then
  KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(30))')
  PLIVO_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  WA_VERIFY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  WA_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  WRONG_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(30))')
  # Exotel's URLs carry a key derived from PHONE_URL_KEY (backend/config.py exotel_url_key), never the key itself
  EXO_KEY=$(cd "$ROOT/backend" && python3 -c 'import sys; from config import exotel_url_key; print(exotel_url_key(sys.argv[1]))' "$KEY")
  failed_before=$fail
  if start_serve "$W/log2" PHONE_URL_KEY="$KEY" PLIVO_AUTH_ID=MAFAKEAUTHID00000000 PLIVO_AUTH_TOKEN="$PLIVO_TOKEN" \
       PLIVO_NUMBER=+91""2240000000 EXOTEL_SID=fakesid EXOTEL_API_KEY=fakekey EXOTEL_API_TOKEN=faketoken \
       EXOTEL_EXOPHONE=02240000001 WHATSAPP_TOKEN=fake-wa-token WHATSAPP_PHONE_NUMBER_ID=100000000000001 \
       WHATSAPP_APP_SECRET="$WA_SECRET" WHATSAPP_WEBHOOK_VERIFICATION_TOKEN="$WA_VERIFY" OUTBOUND_ALLOWED_TO=+91""9800000001; then
    ROOT="$ROOT" "$PY" - "$LAN" "$PORT" "$SECRET" "$HOSTNAME_CF" "$EXO_KEY" "$PLIVO_TOKEN" "$WA_VERIFY" "$WRONG_KEY" "$W/sealed" "$KEY" \
      > "$W/phase2.out" 2>&1 <<'EOF'
import asyncio, json, os, sys, urllib.error, urllib.parse, urllib.request
import websockets
sys.path.insert(0, os.path.join(os.environ["ROOT"], "backend"))
from security import plivo_v3_signature   # the backend's own Plivo V3 signer

host, port, secret, cf, key, plivo_token, wa_verify, wrong_key, sealed_file, phone_url_key = sys.argv[1:11]
base = f"http://{host}:{port}"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}

def call(method, path, data=None, headers=None):
    req = urllib.request.Request(base + path, data=data, method=method, headers={"X-Origin-Verify": secret, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    return bool(cond)

results = []
st, body = call("GET", "/health")
ch = json.loads(body)["channels"] if st == 200 else {}
results.append(check("health: Plivo, Exotel, WhatsApp and outbound Plivo on",
                     all(ch.get(k) for k in ("plivo", "exotel", "whatsapp", "outbound_plivo"))))
st, body = call("GET", "/config.json")
cfg = json.loads(body) if st == 200 else {}
results.append(check("config.json shows 'Ring a phone' (providers plivo, exotel)",
                     cfg.get("outboundCalls") is True and set(cfg.get("providers", [])) == {"plivo", "exotel"}))
st, _ = call("POST", "/phone/plivo/answer", b"CallUUID=3f1c2b4a-0000-1111-2222-33334444555f", FORM)
results.append(check(f"unsigned Plivo answer -> 401 ({st})", st == 401))
params = {"CallUUID": "3f1c2b4a-0000-1111-2222-33334444555f", "Direction": "inbound", "From": "91" "9812345678",
          "To": "91" "2240000000", "CallStatus": "in-progress"}
nonce = "31415926535897932384"
sig = plivo_v3_signature("POST", f"https://{cf}/phone/plivo/answer", nonce, plivo_token, params)
st, xml = call("POST", "/phone/plivo/answer", urllib.parse.urlencode(params).encode(),
               {**FORM, "X-Plivo-Signature-V3": sig, "X-Plivo-Signature-V3-Nonce": nonce})
prefix = f"wss://{cf}/phone/plivo/ws/"
results.append(check("Plivo answer signed for the CloudFront URL -> <Stream> wss://<CloudFront>/phone/plivo/ws/...",
                     st == 200 and prefix in xml))
token = xml.split(prefix, 1)[1].split("<", 1)[0] if prefix in xml else ""
open(sealed_file, "w").write(token)

async def sockets():
    out = {}
    for name, path in (("exotel wrong key", f"/phone/exotel/ws/{wrong_key}?sample-rate=16000"),
                       ("exotel PHONE_URL_KEY itself", f"/phone/exotel/ws/{phone_url_key}?sample-rate=16000"),
                       ("exotel", f"/phone/exotel/ws/{key}?sample-rate=16000"),
                       ("plivo", f"/phone/plivo/ws/{token}")):
        try:
            async with websockets.connect(f"ws://{host}:{port}{path}", additional_headers={"X-Origin-Verify": secret}):
                out[name] = "accepted"          # closed right away: no start event, so no call is set up
        except websockets.exceptions.InvalidStatus as e:
            out[name] = e.response.status_code
    return out

ws = asyncio.run(sockets())
results.append(check(f"media streams: Exotel and Plivo accepted, a wrong Exotel key and PHONE_URL_KEY refused ({ws})",
                     ws == {"exotel wrong key": 403, "exotel PHONE_URL_KEY itself": 403, "exotel": "accepted",
                            "plivo": "accepted"}))
st, _ = call("POST", f"/phone/exotel/status/{key}", b"CallSid=c1&Status=completed&ConversationDuration=12", FORM)
results.append(check(f"Exotel status callback -> 200 ({st})", st == 200))
st, body = call("GET", "/whatsapp?" + urllib.parse.urlencode(
    {"hub.mode": "subscribe", "hub.verify_token": wa_verify, "hub.challenge": "4242"}))
results.append(check(f"WhatsApp webhook verification -> 200 with the challenge ({st})", st == 200 and body == "4242"))
st, _ = call("POST", "/whatsapp", b'{"object": "whatsapp_business_account"}',
             {"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=00"})
results.append(check(f"WhatsApp event with a bad signature -> 401 ({st})", st == 401))
st, _ = call("GET", "/phone/nothing-here", None, {"Accept": "text/html"})
results.append(check(f"unknown /phone/ path stays 404, not the web app ({st})", st == 404))
sys.exit(0 if all(results) else 1)
EOF
    rc=$?
    grep -E '^(ok|FAIL) ' "$W/phase2.out"
    pass=$((pass + $(grep -c '^ok ' "$W/phase2.out"))); n_bad=$(grep -c '^FAIL ' "$W/phase2.out")
    fail=$((fail + n_bad))
    if [ "$rc" != 0 ] && [ "$n_bad" = 0 ]; then bad "phase 2 checks crashed (exit $rc)"; tail -n 5 "$W/phase2.out"; fi
    sleep 0.5                                               # let the last log lines reach the file
    leaked=""
    for name in KEY EXO_KEY PLIVO_TOKEN WA_VERIFY WA_SECRET WRONG_KEY; do grep -q -- "${!name}" "$W/log2" && leaked="$leaked $name"; done
    sealed=$(cat "$W/sealed" 2>/dev/null)
    [ -n "$sealed" ] && grep -q -- "${sealed:0:40}" "$W/log2" && leaked="$leaked sealed-Plivo-token"
    grep -q "9812345678" "$W/log2" && leaked="$leaked caller-number"
    [ -z "$leaked" ] && ok "no URL key, token, secret or caller number in the log" || bad "in the log:$leaked"
    # serve.py's filter cuts the path after three segments ("…"); the backend's JSON logging then masks that
    # segment again ("[KEY]"). Either way only the first three segments are left.
    grep -qE '"WebSocket /phone/exotel/ws/(…|\[KEY\])' "$W/log2" && ok "uvicorn's WebSocket lines keep only the first three path segments" \
      || bad "uvicorn WebSocket line not redacted as expected"
  else
    bad "serve.py did not start with phone and WhatsApp settings"
  fi
  stop_serve || bad "did not stop on SIGTERM (phase 2)"
  [ "$fail" = "$failed_before" ] || { echo "--- log:"; tail -n 25 "$W/log2" | cut -c1-260; }
fi
echo "real backend: $pass passed, $fail failed"
[ "$fail" = 0 ]
