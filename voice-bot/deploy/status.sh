#!/usr/bin/env bash
# Show the voice stack: URL, server state, running release, which provider secrets are set (names only).
#
#   deploy/status.sh
source "$(dirname "$0")/lib.sh"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="${2:?}"; shift 2;;
    -h|--help) usage "$0"; exit 0;;
    *) die "unknown option $1 (see --help)";;
  esac
done
need aws python3 curl

STATUS=$(stack_status)
echo "stack      : $STACK ($STATUS)"
[ "$STATUS" = NONE ] && exit 0
load_outputs
echo "url        : $URL"
echo "login      : IDP user pool $OUT_UserPoolId, voice app client $OUT_UserPoolClientId"
if [ -n "$IID" ]; then
  aws ec2 describe-instances --instance-ids "$IID" --output text \
    --query 'Reservations[0].Instances[0].[InstanceId, InstanceType, State.Name, ImageId, LaunchTime]' \
    | awk '{ printf "server     : %s %s %s (AMI %s, since %s)\n", $1, $2, $3, $4, $5 }'
else
  echo "server     : none (deploy/deploy.sh creates it)"
fi
if curl -fsS -m 10 -o /dev/null "$URL/health" 2>/dev/null; then
  echo "health     : OK, release $(running_release_id)"
else
  echo "health     : not answering"
fi
echo "latest zip : $(latest_release_id || true)"
names=$(aws ssm get-parameters-by-path --path "$SSM_PREFIX/secrets" --query 'Parameters[].Name' --output text 2>/dev/null \
  | tr '\t' '\n' | sed "s#^$SSM_PREFIX/secrets/##" | sort | tr '\n' ' ')
echo "secrets    : ${names:-none}"
WA=$(aws cloudformation describe-stacks --stack-name "$STACK" --output text \
  --query "Stacks[0].Parameters[?ParameterKey=='EnableWhatsApp'].ParameterValue | [0]" 2>/dev/null || true)
case " $names " in *" WHATSAPP_TOKEN "*)
  [ "$WA" = true ] || echo "WARNING    : WhatsApp secrets are set but the WebRTC ports are closed: deploy/deploy.sh --whatsapp on";;
esac
