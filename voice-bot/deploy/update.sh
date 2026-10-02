#!/usr/bin/env bash
# Ship new code to the running voice server in about a minute (no CloudFormation change, no SSH).
#
#   deploy/update.sh                        # build the web app, zip, upload, install on the server, check
#   deploy/update.sh --skip-frontend-build  # backend-only change: reuse frontend/dist
#   deploy/update.sh --restart              # only restart the service (e.g. after deploy/secrets.sh set ...)
#
# The server installs the release with deploy/instance/release.sh (own venv, health check, automatic rollback).
# Settings that live in the stack (IDP URL, user pool, toggles) change with deploy/deploy.sh instead.
source "$(dirname "$0")/lib.sh"

SKIP_FE=""; RESTART_ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-frontend-build) SKIP_FE=1; shift;;
    --restart) RESTART_ONLY=1; shift;;
    --stack) STACK="${2:?}"; shift 2;;
    -h|--help) usage "$0"; exit 0;;
    *) die "unknown option $1 (see --help)";;
  esac
done
need aws python3 curl

[ "$(stack_status)" != NONE ] || die "stack $STACK does not exist: run deploy/deploy.sh first"
load_outputs
[ -n "$IID" ] || die "stack $STACK has no server yet: run deploy/deploy.sh"
STATE=$(instance_state "$IID")
[ "$STATE" = running ] || die "the server is $STATE: run deploy/start.sh first"
wait_ssm_online "$IID" 60

if [ -n "$RESTART_ONLY" ]; then
  restart_service "$IID" || die "the service did not come back (see the log above)"
  wait_http "$URL/health" 120 || die "$URL/health does not answer through CloudFront"
  say "restarted; $URL is up"
  exit 0
fi

build_frontend "$SKIP_FE"
RID=$(new_release_id)
ZIP="$WORK/$RID.zip"
say "packaging release $RID"
package_release "$ZIP" "$RID" "$FRONTEND_OUT" | sed 's/^/       /'
upload_release "$ZIP" "$RID"
rollout_release "$IID" "$RID" || die "release $RID failed on the server; the previous release keeps running"
wait_http "$URL/release.json" 120 "$RID" || die "$URL does not report $RID yet"
say "live: $RID at $URL"
