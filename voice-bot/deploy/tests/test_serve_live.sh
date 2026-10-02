#!/usr/bin/env bash
# Runs deploy/app/serve.py for real (uvicorn, real sockets) in front of a stand-in backend and checks it from
# 127.0.0.1 and from this machine's LAN address (a "remote" client). No AWS calls (DOCAI_SKIP_SSM=1).
#   PYTHON=<venv>/bin/python bash deploy/tests/test_serve_live.sh     # needs fastapi, uvicorn, websockets
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
"$PY" -c "import fastapi, uvicorn, websockets" 2>/dev/null || { echo "skipped: $PY lacks fastapi/uvicorn/websockets"; exit 0; }
W=$(mktemp -d); PORT=$(( 20000 + RANDOM % 20000 )); SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
LAN=$(hostname -I 2>/dev/null | awk '{print $1}')
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   $*"; }
bad() { fail=$((fail + 1)); echo "FAIL $*"; }

mkdir -p "$W/backend" "$W/frontend/assets"
cat > "$W/backend/main.py" <<'EOF'
# Starts like the real backend/main.py: create_app() + main() that builds uvicorn.Config itself.
import asyncio, os, sys
import uvicorn
from fastapi import FastAPI, WebSocket

def create_app():
    pool = os.environ["USER_POOL_ID"]          # settings must exist when the app is built
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok", "pool": pool}

    @app.get("/whoami")
    async def whoami(request: __import__("fastapi").Request):
        return {"client": request.client.host if request.client else None}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello " + websocket.query_params.get("language", ""))
        await websocket.close()

    return app

def main():
    config = uvicorn.Config(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
                            access_log=False, log_config=None, proxy_headers=True, forwarded_allow_ips="*")
    asyncio.run(uvicorn.Server(config).serve())
    return 0

if __name__ == "__main__":
    sys.exit(main())
EOF
echo '<html>voice</html>' > "$W/frontend/index.html"; echo 'x' > "$W/frontend/assets/i-1.js"
echo '{"id": "live-test", "built_at": "now"}' > "$W/RELEASE.json"

DOCAI_SKIP_SSM=1 ORIGIN_VERIFY="$SECRET" BACKEND_DIR="$W/backend" FRONTEND_DIR="$W/frontend" \
  RELEASE_FILE="$W/RELEASE.json" PORT=$PORT USER_POOL_ID=ap-south-1_TEST APP_CLIENT_ID=client1 \
  PUBLIC_HOST=dtest.cloudfront.net "$PY" -W ignore "$HERE/../app/serve.py" > "$W/log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$W"' EXIT
for _ in $(seq 1 50); do curl -fsS -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null && break; sleep 0.2; done

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
[ "$(code "http://127.0.0.1:$PORT/health")" = 200 ] && ok "127.0.0.1 /health without header" || bad "local health"
[ "$(code -H "X-Forwarded-For: 1.2.3.4" "http://127.0.0.1:$PORT/health")" = 403 ] && ok "127.0.0.1 with X-Forwarded-For needs the header" || bad "local XFF"
if [ -n "$LAN" ]; then
  [ "$(code "http://$LAN:$PORT/health")" = 403 ] && ok "remote client without header -> 403" || bad "remote without header"
  [ "$(code -H "X-Origin-Verify: wrong" "http://$LAN:$PORT/")" = 403 ] && ok "wrong secret -> 403" || bad "wrong secret"
  [ "$(code -H "X-Origin-Verify: $SECRET" "http://$LAN:$PORT/health")" = 200 ] && ok "remote with CloudFront header -> 200" || bad "remote with header"
  [ "$(code -H "X-Forwarded-For: 127.0.0.1" "http://$LAN:$PORT/health")" = 403 ] && ok "spoofed X-Forwarded-For: 127.0.0.1 -> 403" || bad "XFF spoof"
  who=$(curl -s -H "X-Origin-Verify: $SECRET" -H "X-Forwarded-For: 9.9.9.9" "http://$LAN:$PORT/whoami")
  echo "$who" | grep -q "\"$LAN\"" && ok "backend sees the TCP peer, not X-Forwarded-For (proxy_headers off)" || bad "proxy headers: $who"
  cfg=$(curl -s -H "X-Origin-Verify: $SECRET" "http://$LAN:$PORT/config.json")
  echo "$cfg" | grep -q '"websocketUrl": *"wss://dtest.cloudfront.net/ws"' && echo "$cfg" | grep -q '"userPoolClientId": *"client1"' \
    && ok "runtime config.json" || bad "config.json: $cfg"
  [ "$(curl -s -D - -o /dev/null -H "X-Origin-Verify: $SECRET" "http://$LAN:$PORT/assets/i-1.js" | grep -ci immutable)" = 1 ] \
    && ok "hashed asset cache header" || bad "asset cache header"
  "$PY" - "$LAN" "$PORT" "$SECRET" <<'EOF' && ok "WebSocket with header works, without header is refused" || bad "websocket"
import asyncio, sys
import websockets
host, port, secret = sys.argv[1], sys.argv[2], sys.argv[3]
async def main():
    url = f"ws://{host}:{port}/ws?token=SECRET.JWT.VALUE&language=hi-IN"
    async with websockets.connect(url, additional_headers={"X-Origin-Verify": secret}) as ws:
        assert await ws.recv() == "hello hi-IN"
    try:
        async with websockets.connect(url) as ws:
            await ws.recv()
        raise SystemExit("accepted without header")
    except websockets.exceptions.InvalidStatus as e:
        assert e.response.status_code == 403, e.response.status_code
asyncio.run(main())
EOF
else
  echo "no LAN address: remote-client checks skipped"
fi
grep -q "SECRET.JWT.VALUE" "$W/log" && bad "the /ws token reached the log" || ok "no token in the log"
grep -q "$SECRET" "$W/log" && bad "origin secret in the log" || ok "no origin secret in the log"
kill -TERM $PID; for _ in $(seq 1 50); do kill -0 $PID 2>/dev/null || break; sleep 0.2; done
kill -0 $PID 2>/dev/null && bad "did not stop on SIGTERM" || ok "stops cleanly on SIGTERM"
echo "--- serve.py log:"; sed 's/^/    /' "$W/log" | tail -n 12
echo "serve.py live: $pass passed, $fail failed"
[ "$fail" = 0 ]
