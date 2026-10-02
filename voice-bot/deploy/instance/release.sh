#!/bin/bash
# Install one release of the voice bot on this server and switch the service to it. Runs as root:
# from user-data on the first boot, and from deploy/update.sh / deploy/deploy.sh through SSM Run Command.
#
#   release.sh RELEASE.zip            # a zip made by deploy/lib.sh (backend/, frontend/, deploy/, RELEASE.json)
#
# Steps: unpack into /opt/docai-voice/releases/<id> -> own venv (uv, Python 3.12) -> requirements
# -> NLTK data -> systemd unit -> switch "current" -> restart -> wait for http://127.0.0.1:8080/health.
# If the new release never answers /health, the previous one is put back and restarted, and this exits 1.
# Keeps the newest 3 releases. Re-running with the active release only restarts it when FORCE=1.
set -euo pipefail

BASE="${DOCAI_BASE:-/opt/docai-voice}"
UNIT_DIR="${DOCAI_UNIT_DIR:-/etc/systemd/system}"
SERVICE=docai-voice
HEALTH_URL="${DOCAI_HEALTH_URL:-http://127.0.0.1:8080/health}"
HEALTH_TIMEOUT="${DOCAI_HEALTH_TIMEOUT:-300}"
KEEP="${DOCAI_KEEP_RELEASES:-3}"
SYSTEMCTL="${DOCAI_SYSTEMCTL:-systemctl}"
JOURNALCTL="${DOCAI_JOURNALCTL:-journalctl}"
CURL="${DOCAI_CURL:-curl}"
UV="${DOCAI_UV:-uv}"
OWNER="${DOCAI_OWNER:-root:root}"
PYVER=3.12
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$BASE/python}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/var/cache/docai-voice/uv}"
export UV_PYTHON_PREFERENCE=only-managed UV_NO_PROGRESS=1 UV_PYTHON_DOWNLOADS=automatic
export PATH="/usr/local/bin:$PATH" HOME="${HOME:-/root}"   # SSM Run Command may start scripts without HOME

T0=$(date +%s)
log() { printf '[release %3ss] %s\n' "$(( $(date +%s) - T0 ))" "$*"; }
die() { log "ERROR: $*"; exit 1; }

zip="${1:?usage: release.sh RELEASE.zip}"
[ -f "$zip" ] || die "no such file: $zip"
command -v "$UV" >/dev/null || die "uv is not installed (the first-boot user-data installs it)"

# Release id from RELEASE.json; refuse zips with absolute paths or '..'.
id=$(python3 - "$zip" <<'EOF'
import json, re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for name in z.namelist():
    if name.startswith("/") or ".." in name.split("/"):
        sys.exit(f"unsafe path in zip: {name}")
rid = json.loads(z.read("RELEASE.json"))["id"]
if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", rid):
    sys.exit("bad release id")
print(rid)
EOF
) || die "not a voice release zip: $zip"

mkdir -p "$BASE/releases"
dest="$BASE/releases/$id"
prev=$(readlink -f "$BASE/current" 2>/dev/null || true)
[ -n "$prev" ] && [ ! -d "$prev" ] && prev=""

healthy() {
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if "$CURL" -fsS -m 5 -o /dev/null "$HEALTH_URL" 2>/dev/null; then return 0; fi
    sleep 2
  done
  return 1
}

if [ "$prev" = "$(readlink -f "$dest" 2>/dev/null || echo none)" ]; then
  if [ -z "${FORCE:-}" ]; then log "release $id is already active: nothing to do"; exit 0; fi
  log "release $id is already active: restarting it"
  "$SYSTEMCTL" restart "$SERVICE"
  healthy || die "$SERVICE does not answer $HEALTH_URL"
  log "release $id is up"; exit 0
fi

log "unpacking $id"
rm -rf "$dest"
mkdir -p "$dest"
python3 -m zipfile -e "$zip" "$dest/"
for f in backend/main.py backend/requirements.txt deploy/app/serve.py deploy/app/requirements.txt \
         deploy/instance/$SERVICE.service RELEASE.json; do
  [ -f "$dest/$f" ] || { rm -rf "$dest"; die "release $id has no $f"; }
done

log "python $PYVER venv and requirements (uv)"
"$UV" python find "$PYVER" >/dev/null 2>&1 || "$UV" python install "$PYVER"
"$UV" venv --quiet --python "$PYVER" "$dest/.venv"
if ! "$UV" pip install --quiet --python "$dest/.venv/bin/python" --compile-bytecode \
      -r "$dest/backend/requirements.txt" -r "$dest/deploy/app/requirements.txt"; then
  rm -rf "$dest"; die "pip install failed for $id (the running release is untouched)"
fi
"$dest/.venv/bin/python" -m compileall -q "$dest/backend" "$dest/deploy/app" >/dev/null 2>&1 || true

# Pipecat downloads NLTK's punkt_tab tokenizer at import time; fetch it once into a shared read-only folder.
if "$dest/.venv/bin/python" -c "import nltk" 2>/dev/null && [ ! -d "$BASE/nltk_data/tokenizers/punkt_tab" ]; then
  "$dest/.venv/bin/python" -c "import nltk, sys; sys.exit(0 if nltk.download('punkt_tab', download_dir=sys.argv[1], quiet=True) else 1)" \
    "$BASE/nltk_data" || log "warning: NLTK punkt_tab download failed; the service will fetch it on first start"
fi

chown -R "$OWNER" "$dest" 2>/dev/null || true
chmod -R u=rwX,go=rX "$dest"
[ -d "$BASE/nltk_data" ] && chmod -R u=rwX,go=rX "$BASE/nltk_data"

unit_new="$dest/deploy/instance/$SERVICE.service"
unit_old=""
if ! cmp -s "$unit_new" "$UNIT_DIR/$SERVICE.service"; then
  if [ -f "$UNIT_DIR/$SERVICE.service" ]; then unit_old=$(mktemp); cp "$UNIT_DIR/$SERVICE.service" "$unit_old"; fi
  install -m 0644 "$unit_new" "$UNIT_DIR/$SERVICE.service"
  "$SYSTEMCTL" daemon-reload
fi

switch_to() {  # atomic symlink swap, then restart
  ln -sfn "$1" "$BASE/current.new"
  mv -Tf "$BASE/current.new" "$BASE/current"
  "$SYSTEMCTL" enable "$SERVICE" >/dev/null 2>&1 || true
  "$SYSTEMCTL" restart "$SERVICE"
}

log "switching to $id"
switched_at=$(date +%s)
switch_to "$dest"
if ! healthy; then
  log "release $id did not answer $HEALTH_URL within ${HEALTH_TIMEOUT}s; its log lines:"
  # Only this start's lines: SSM keeps Run Command output for 30 days, and older lines may be about calls.
  "$JOURNALCTL" -u "$SERVICE" --since "@$switched_at" -n 60 --no-pager 2>/dev/null || true
  if [ -n "$prev" ]; then
    if [ -n "$unit_old" ]; then install -m 0644 "$unit_old" "$UNIT_DIR/$SERVICE.service"; "$SYSTEMCTL" daemon-reload; fi
    log "rolling back to $(basename "$prev")"
    switch_to "$prev"
    healthy && log "rolled back: $(basename "$prev") is up" || log "the previous release does not answer either"
    rm -rf "$dest"
  fi
  rm -f "$unit_old"
  die "release $id failed its health check"
fi
rm -f "$unit_old"

# Keep the newest $KEEP releases (and always the active and the previous one).
active=$(readlink -f "$BASE/current")
n=0
for d in $(ls -1dt "$BASE"/releases/*/ 2>/dev/null); do
  d=${d%/}
  n=$((n + 1))
  [ "$n" -le "$KEEP" ] && continue
  [ "$d" = "$active" ] || [ "$d" = "$prev" ] && continue
  rm -rf "$d"
done
"$UV" cache prune --quiet >/dev/null 2>&1 || true
log "release $id is up"
