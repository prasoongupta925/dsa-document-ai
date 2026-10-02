#!/usr/bin/env bash
# Every check for the deploy/ folder. Read-only: the only AWS calls are validate-template and describe-type.
#
#   bash deploy/tests/validate.sh             # all checks
#   bash deploy/tests/validate.sh --offline   # skip the AWS calls (uses cached schemas if there are any)
#   PYTHON=/path/to/venv/bin/python bash deploy/tests/validate.sh   # a Python with fastapi + httpx for test_serve.py
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$(cd "$HERE/.." && pwd)"
OFFLINE=""; [ "${1:-}" = "--offline" ] && OFFLINE=1
export AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1
failed=0
step() { printf '\n== %s\n' "$*"; }
result() { if [ "$1" = 0 ]; then echo "   ok"; else echo "   FAILED"; failed=$((failed + 1)); fi; }

step "bash -n on every script"
rc=0
for f in "$DEPLOY"/*.sh "$DEPLOY"/instance/*.sh "$DEPLOY"/tests/*.sh; do bash -n "$f" || { echo "   $f"; rc=1; }; done
result $rc

if command -v shellcheck >/dev/null; then
  step "shellcheck"
  shellcheck -S warning "$DEPLOY"/*.sh "$DEPLOY"/instance/*.sh; result $?
fi

step "template: YAML parse, registry schemas, references, user-data bash -n"
if [ -n "$OFFLINE" ] && [ ! -d "${SCHEMA_CACHE:-$HOME/.cache/docai-voice/cfn-schemas}" ]; then
  echo "   skipped (offline and no schema cache)"
else
  python3 "$HERE/check_template.py" "$DEPLOY/template.yaml"; result $?
fi

if [ -z "$OFFLINE" ]; then
  step "aws cloudformation validate-template (read-only)"
  aws cloudformation validate-template --template-body "file://$DEPLOY/template.yaml" \
    --query 'join(`, `, [join(` `, Capabilities), to_string(length(Parameters))])' --output text; result $?
fi

if [ -z "$OFFLINE" ]; then
  step "server role: IAM policy simulator (read-only), Mumbai-only models"
  python3 "$HERE/simulate_iam.py" | tail -n 1; result "${PIPESTATUS[0]}"
fi

if [ -z "$OFFLINE" ] && command -v uv >/dev/null; then
  step "requirements: prebuilt wheels for Linux aarch64 + Python 3.12 (index metadata only, nothing installed)"
  lock=$(mktemp -d)
  uv pip compile --quiet --python-version 3.12 --python-platform aarch64-manylinux_2_34 --only-binary :all: \
    "$DEPLOY/../backend/requirements.txt" "$DEPLOY/app/requirements.txt" -o "$lock/resolved.txt"; rc=$?
  [ "$rc" = 0 ] && echo "   $(grep -c '==' "$lock/resolved.txt") packages, all with aarch64 wheels"
  rm -rf "$lock"; result $rc
fi

if command -v systemd-analyze >/dev/null; then
  step "systemd-analyze verify (unit copied with local paths)"
  tmpu=$(mktemp -d)
  sed -e 's#/opt/docai-voice/current/.venv/bin/python#/usr/bin/python3#' -e 's#^User=voicebot#User=daemon#' \
      -e 's#^Group=voicebot#Group=daemon#' "$DEPLOY/instance/docai-voice.service" > "$tmpu/docai-voice.service"
  out=$(systemd-analyze verify "$tmpu/docai-voice.service" 2>&1 | grep "docai-voice.service" || true)
  rm -rf "$tmpu"
  [ -z "$out" ] || echo "$out"
  result "$([ -z "$out" ] && echo 0 || echo 1)"
fi

step "deploy/app/serve.py unit tests"
PY="${PYTHON:-python3}"
if "$PY" -c "import fastapi, httpx" 2>/dev/null; then
  out=$(cd "$DEPLOY/.." && "$PY" -W ignore -m unittest discover -s deploy/tests -p 'test_serve.py' 2>&1); rc=$?
  printf '%s\n' "$out" | tail -n 3; result $rc
else
  echo "   skipped: $PY has no fastapi/httpx (set PYTHON=<venv>/bin/python, e.g. the backend venv)"
fi

step "deploy/app/serve.py live (uvicorn, real sockets, stand-in backend)"
if "$PY" -c "import fastapi, uvicorn, websockets" 2>/dev/null; then
  PYTHON="$PY" bash "$HERE/test_serve_live.sh" | tail -n 1; result "${PIPESTATUS[0]}"
else
  echo "   skipped: $PY has no fastapi/uvicorn/websockets"
fi

step "deploy/app/serve.py in front of the real backend/main.py and frontend/dist"
PYTHON="$PY" bash "$HERE/test_real_backend.sh" | tail -n 1; result "${PIPESTATUS[0]}"

step "deploy/instance/release.sh with stub systemctl/uv/curl"
bash "$HERE/test_release.sh" | tail -n 1; result "${PIPESTATUS[0]}"

step "release zip: layout and exclusions"
(
  set -e
  source "$DEPLOY/lib.sh"
  fe="$WORK/fe"; mkdir -p "$fe/assets"; echo '<html></html>' > "$fe/index.html"; echo 'x' > "$fe/assets/a-1.js"
  echo 'SECRET=1' > "$fe/.env"
  package_release "$WORK/r.zip" "test-0001" "$fe" >/dev/null
  python3 - "$WORK/r.zip" "$VOICE_ROOT" <<'EOF'
import os, sys, zipfile
names = set(zipfile.ZipFile(sys.argv[1]).namelist())
must = {"RELEASE.json", "backend/main.py", "backend/requirements.txt", "deploy/app/serve.py",
        "deploy/app/requirements.txt", "deploy/instance/release.sh", "deploy/instance/docai-voice.service",
        "frontend/index.html", "frontend/assets/a-1.js"}
missing = must - names
bad = [n for n in names if os.path.basename(n).startswith(".env") or "__pycache__" in n or n.endswith((".pyc", ".wav"))
       or "/tests/" in n or os.path.basename(n).startswith("test_") or n.endswith("Dockerfile")]
if os.path.exists(os.path.join(sys.argv[2], "backend", ".env")) and "backend/.env" in names:
    bad.append("backend/.env")
print(f"   {len(names)} files; missing: {sorted(missing) or 'none'}; must not be there: {sorted(bad) or 'none'}")
sys.exit(1 if missing or bad else 0)
EOF
); result $?

printf '\n%s\n' "$([ "$failed" = 0 ] && echo "ALL CHECKS PASSED" || echo "$failed CHECK(S) FAILED")"
[ "$failed" = 0 ]
