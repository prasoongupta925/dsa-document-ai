#!/usr/bin/env bash
# Tests for deploy/instance/release.sh with stub systemctl / uv / curl / journalctl (no root, no network).
#   bash deploy/tests/test_release.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RELEASE_SH="$HERE/../instance/release.sh"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
pass=0; failn=0
ok() { pass=$((pass + 1)); echo "ok   $*"; }
bad() { failn=$((failn + 1)); echo "FAIL $*"; }

mkdir -p "$W/stubs" "$W/base" "$W/units"
cat > "$W/stubs/systemctl" <<'EOF'
#!/bin/bash
echo "systemctl $*" >> "$STUB_LOG"
EOF
cat > "$W/stubs/journalctl" <<'EOF'
#!/bin/bash
echo "(journal of $*)"
EOF
# health: the release whose RELEASE.json says "bad" never answers
cat > "$W/stubs/curl" <<'EOF'
#!/bin/bash
grep -q '"bad": true' "$DOCAI_BASE/current/RELEASE.json" 2>/dev/null && exit 22
exit 0
EOF
cat > "$W/stubs/uv" <<'EOF'
#!/bin/bash
echo "uv $*" >> "$STUB_LOG"
case "$1" in
  venv) dir="${!#}"; mkdir -p "$dir/bin"; ln -sf "$(command -v python3)" "$dir/bin/python";;
  pip) for a in "$@"; do case "$a" in *requirements.txt) grep -q FAIL_INSTALL "$a" && exit 1;; esac; done;;
esac
exit 0
EOF
chmod +x "$W/stubs/"*

make_zip() {  # make_zip ID [bad] [fail-install] [no-main] [unsafe]
  local id=$1; shift
  python3 - "$W/$id.zip" "$id" "$HERE/../instance/docai-voice.service" "$@" <<'EOF'
import json, sys, zipfile
out, rid, unit, flags = sys.argv[1], sys.argv[2], sys.argv[3], set(sys.argv[4:])
with zipfile.ZipFile(out, "w") as z:
    meta = {"id": rid, "built_at": "now"}
    if "bad" in flags:
        meta["bad"] = True
    z.writestr("RELEASE.json", json.dumps(meta, indent=1))
    if "no-main" not in flags:
        z.writestr("backend/main.py", "app = None\n")
    z.writestr("backend/requirements.txt", "FAIL_INSTALL\n" if "fail-install" in flags else "fastapi\n")
    z.writestr("deploy/app/serve.py", "print('serve')\n")
    z.writestr("deploy/app/requirements.txt", "uvicorn\n")
    z.writestr("deploy/instance/docai-voice.service", open(unit).read())
    z.writestr("frontend/index.html", "<html></html>")
    if "unsafe" in flags:
        z.writestr("../evil.txt", "x")
EOF
}

run() {  # run ZIP [ENV=VAL...] -> exit code in $rc, output in $W/out
  local zip=$1; shift
  set +e
  env "$@" PATH="$W/stubs:$PATH" STUB_LOG="$W/stub.log" DOCAI_BASE="$W/base" DOCAI_UNIT_DIR="$W/units" \
    DOCAI_CURL="$W/stubs/curl" DOCAI_UV="$W/stubs/uv" DOCAI_SYSTEMCTL="$W/stubs/systemctl" \
    DOCAI_JOURNALCTL="$W/stubs/journalctl" DOCAI_OWNER="$(id -u):$(id -g)" DOCAI_HEALTH_TIMEOUT=2 \
    UV_CACHE_DIR="$W/uvcache" UV_PYTHON_INSTALL_DIR="$W/python" bash "$RELEASE_SH" "$zip" > "$W/out" 2>&1
  rc=$?
  set -e
}
current() { basename "$(readlink -f "$W/base/current")"; }

make_zip r1; run "$W/r1.zip"
[ "$rc" = 0 ] && [ "$(current)" = r1 ] && ok "first install switches to r1" || { bad "first install"; cat "$W/out"; }
[ -f "$W/units/docai-voice.service" ] && grep -q "daemon-reload" "$W/stub.log" && grep -q "restart docai-voice" "$W/stub.log" \
  && ok "unit installed, daemon-reload and restart" || bad "unit/daemon-reload/restart"
[ -x "$W/base/releases/r1/.venv/bin/python" ] && ok "per-release venv" || bad "venv missing"
[ "$(stat -c %a "$W/base/releases/r1/backend/main.py")" = 644 ] && ok "code is read-only for others (644)" || bad "permissions"

: > "$W/stub.log"
make_zip r2; run "$W/r2.zip"
[ "$rc" = 0 ] && [ "$(current)" = r2 ] && [ -d "$W/base/releases/r1" ] && ok "second release switches, r1 kept" || bad "second release"
grep -q "daemon-reload" "$W/stub.log" && bad "unit unchanged but daemon-reload ran" || ok "no daemon-reload when the unit did not change"

make_zip r3 bad; run "$W/r3.zip"
[ "$rc" = 1 ] && [ "$(current)" = r2 ] && ok "unhealthy r3 rolled back to r2 (exit 1)" || { bad "rollback"; cat "$W/out"; }
[ ! -d "$W/base/releases/r3" ] && ok "failed release folder removed" || bad "r3 folder left behind"
grep -q "journal of -u docai-voice --since @[0-9]" "$W/out" && ok "prints the service log of this start only on failure" \
  || bad "no journal output limited to this start"

make_zip r4 fail-install; run "$W/r4.zip"
[ "$rc" = 1 ] && [ "$(current)" = r2 ] && [ ! -d "$W/base/releases/r4" ] && ok "pip failure leaves r2 running" || bad "pip failure"

make_zip r5 no-main; run "$W/r5.zip"
[ "$rc" = 1 ] && [ "$(current)" = r2 ] && grep -q "has no backend/main.py" "$W/out" && ok "incomplete zip refused" || bad "incomplete zip"

make_zip r6 unsafe; run "$W/r6.zip"
[ "$rc" = 1 ] && [ ! -e "$W/evil.txt" ] && [ ! -e "$W/base/evil.txt" ] && grep -q "unsafe path" "$W/out" && ok "zip with ../ refused" || bad "unsafe zip"

: > "$W/stub.log"; run "$W/r2.zip"
[ "$rc" = 0 ] && grep -q "already active: nothing to do" "$W/out" && ! grep -q restart "$W/stub.log" && ok "same release again is a no-op" || bad "no-op"
run "$W/r2.zip" FORCE=1
[ "$rc" = 0 ] && grep -q "restart docai-voice" "$W/stub.log" && ok "FORCE=1 restarts the active release" || bad "force restart"

for i in 7 8 9 10; do make_zip "r$i"; sleep 1; run "$W/r$i.zip"; done
left=$(ls -1 "$W/base/releases" | sort | tr '\n' ' ')
[ "$(current)" = r10 ] && [ "$(ls -1 "$W/base/releases" | wc -l)" -le 4 ] && [ -d "$W/base/releases/r9" ] \
  && ok "keeps the newest releases only ($left)" || bad "retention: $left"

echo "release.sh: $pass passed, $failn failed"
[ "$failn" = 0 ]
