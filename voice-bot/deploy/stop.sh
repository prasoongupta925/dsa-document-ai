#!/usr/bin/env bash
# Stop the voice server between demos. Calls in progress end. The URL then shows a "paused" page.
#
#   deploy/stop.sh
#
# While stopped you pay only for the Elastic IP and the 12 GB disk (about $4.7 a month); deploy/start.sh resumes.
source "$(dirname "$0")/lib.sh"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="${2:?}"; shift 2;;
    -h|--help) usage "$0"; exit 0;;
    *) die "unknown option $1 (see --help)";;
  esac
done
need aws python3

load_outputs
[ -n "$IID" ] || die "stack $STACK has no server"
upload_offline_page || warn "could not refresh the paused page"
STATE=$(instance_state "$IID")
case "$STATE" in
  stopped) say "the server is already stopped";;
  running|pending) say "stopping $IID"; aws ec2 stop-instances --instance-ids "$IID" >/dev/null
                   aws ec2 wait instance-stopped --instance-ids "$IID"; say "stopped";;
  stopping) aws ec2 wait instance-stopped --instance-ids "$IID"; say "stopped";;
  *) die "the server is $STATE";;
esac
say "$URL now shows the paused page; deploy/start.sh brings it back"
print_costs
