#!/usr/bin/env bash
# Start the voice server before a demo (about 2 minutes until the app answers).
#
#   deploy/start.sh
#
# The app starts by itself at boot (systemd). If a newer release was uploaded while the server was off
# (deploy.sh / update.sh on a stopped server), it is installed now.
source "$(dirname "$0")/lib.sh"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="${2:?}"; shift 2;;
    -h|--help) usage "$0"; exit 0;;
    *) die "unknown option $1 (see --help)";;
  esac
done
need aws python3 curl

load_outputs
[ -n "$IID" ] || die "stack $STACK has no server: run deploy/deploy.sh"
STATE=$(instance_state "$IID")
case "$STATE" in
  running) say "the server is already running";;
  stopping) say "the server is stopping; waiting"; aws ec2 wait instance-stopped --instance-ids "$IID"
            aws ec2 start-instances --instance-ids "$IID" >/dev/null; aws ec2 wait instance-running --instance-ids "$IID";;
  stopped) say "starting $IID"; aws ec2 start-instances --instance-ids "$IID" >/dev/null
           aws ec2 wait instance-running --instance-ids "$IID";;
  pending) aws ec2 wait instance-running --instance-ids "$IID";;
  *) die "the server is $STATE";;
esac

say "waiting for the app through CloudFront"
wait_http "$URL/health" 300 || die "$URL/health does not answer after 5 minutes: deploy/status.sh"

LATEST=$(latest_release_id)
RUNNING=$(running_release_id)
if [ -n "$LATEST" ] && [ "$LATEST" != "$RUNNING" ] \
   && aws s3api head-object --bucket "$BUCKET" --key "releases/$LATEST.zip" >/dev/null 2>&1; then
  say "a newer release was uploaded while the server was off: $LATEST (running ${RUNNING:-unknown})"
  wait_ssm_online "$IID"
  rollout_release "$IID" "$LATEST" || warn "could not install $LATEST; ${RUNNING:-the old release} keeps running"
fi
upload_offline_page || true
say "ready: $URL (release $(running_release_id))"
