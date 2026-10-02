#!/usr/bin/env bash
# Build and deploy DSA Document AI - Voice to ap-south-1 (one t4g.small behind CloudFront).
#
#   deploy/deploy.sh                       # first deploy (~12 min) or update: code + settings (~3 min)
#   deploy/deploy.sh --skip-frontend-build # reuse frontend/dist as it is
#   deploy/deploy.sh --whatsapp on         # open the WebRTC UDP range for WhatsApp Business Calling (off: --whatsapp off)
#   deploy/deploy.sh --refresh-ami         # move to the newest Amazon Linux 2023 (new server, ~8 min)
#   deploy/deploy.sh --package-only        # build the release zip and show the settings; no AWS changes
#
# Other options: --india-only on|off (CloudFront geo allow-list), --instance-type t4g.medium, --volume-gb 8..12,
#   --keep-failed (no rollback: keep a failed server for inspection), --stack NAME (default docai-voice).
# First run: creates bucket, CloudFront, Cognito app client, role and settings; uploads the code; then the server.
# Later runs: upload the new code, update the stack, install the code on the running server (SSM, no SSH).
# Needs: AWS CLI v2 (AWS_PROFILE or the default credential chain), python3, curl, Node 22 / npm unless --skip-frontend-build.
source "$(dirname "$0")/lib.sh"

SKIP_FE=""; WHATSAPP=""; INDIA=""; ITYPE=""; VOLGB=""; REFRESH_AMI=""; PACKAGE_ONLY=""; KEEP_FAILED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-frontend-build) SKIP_FE=1; shift;;
    --whatsapp) case "${2:-}" in on) WHATSAPP=true;; off) WHATSAPP=false;; *) die "--whatsapp on|off";; esac; shift 2;;
    --india-only) case "${2:-}" in on) INDIA=true;; off) INDIA=false;; *) die "--india-only on|off";; esac; shift 2;;
    --instance-type) ITYPE="${2:?}"; shift 2;;
    --volume-gb) VOLGB="${2:?}"; shift 2;;
    --refresh-ami) REFRESH_AMI=1; shift;;
    --package-only) PACKAGE_ONLY=1; shift;;
    --keep-failed) KEEP_FAILED=1; shift;;
    --stack) STACK="${2:?}"; shift 2;;
    -h|--help) usage "$0"; exit 0;;
    *) die "unknown option $1 (see --help)";;
  esac
done
export KEEP_FAILED
need aws python3 curl

ACCOUNT=$(account_id) || die "the AWS CLI cannot sign in with profile $AWS_PROFILE (aws configure --profile $AWS_PROFILE)"
say "profile $AWS_PROFILE, region ap-south-1, stack $STACK, account $ACCOUNT"
discover_idp
say "IDP API $IDP_API_URL (id $IDP_API_ID), user pool $USER_POOL_ID${IDP_APP_URL:+, app $IDP_APP_URL}"

build_frontend "$SKIP_FE"
RID=$(new_release_id)
ZIP="$WORK/$RID.zip"
say "packaging release $RID (web app from ${FRONTEND_OUT#"$VOICE_ROOT/"})"
package_release "$ZIP" "$RID" "$FRONTEND_OUT" | sed 's/^/       /'

STATUS=$(stack_status)
if [ -n "$PACKAGE_ONLY" ]; then
  OUTDIR="${PACKAGE_OUT:-$DEPLOY_DIR/dist}"
  mkdir -p "$OUTDIR" && cp "$ZIP" "$OUTDIR/$RID.zip"
  say "package only: $OUTDIR/$RID.zip (stack status: $STATUS); nothing was changed in AWS"
  exit 0
fi

case "$STATUS" in
  *_IN_PROGRESS) die "stack $STACK is busy ($STATUS); try again when it is done";;
  ROLLBACK_COMPLETE|ROLLBACK_FAILED)
    warn "stack $STACK is $STATUS (its first create failed): deleting it before creating it again"
    aws cloudformation delete-stack --stack-name "$STACK"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK"
    STATUS=NONE;;
esac

COMMON=(IdpApiUrl="$IDP_API_URL" IdpApiId="$IDP_API_ID" UserPoolId="$USER_POOL_ID" IdpAppUrl="$IDP_APP_URL")
[ -n "$WHATSAPP" ] && COMMON+=(EnableWhatsApp="$WHATSAPP")
[ -n "$INDIA" ] && COMMON+=(RestrictViewersToIndia="$INDIA")
[ -n "$ITYPE" ] && COMMON+=(InstanceType="$ITYPE")
[ -n "$VOLGB" ] && COMMON+=(VolumeSize="$VOLGB")

if [ "$STATUS" = NONE ]; then
  discover_network "${ITYPE:-t4g.small}"
  PL=$(cloudfront_prefix_list)
  say "first deploy, step 1 of 2: bucket, CloudFront, login client, role, settings (CloudFront takes ~4-6 min)"
  say "network: $VPC_ID / $SUBNET_ID, CloudFront prefix list $PL"
  CFN_SECRET_PARAMS="OriginVerifySecret=$(origin_secret)"
  export CFN_SECRET_PARAMS
  cfn_deploy "${COMMON[@]}" VpcId="$VPC_ID" SubnetId="$SUBNET_ID" CloudFrontPrefixListId="$PL" CreateInstance=false
  unset CFN_SECRET_PARAMS
fi

load_outputs
upload_release "$ZIP" "$RID"
upload_offline_page
say "uploaded s3://$BUCKET/releases/$RID.zip"

BEFORE="$IID"
AMI_PIN=""
if [ -n "$BEFORE" ] && [ -z "$REFRESH_AMI" ]; then AMI_PIN=$(instance_ami "$BEFORE"); fi
[ -n "$REFRESH_AMI" ] && say "--refresh-ami: the server is rebuilt on the newest Amazon Linux 2023 if there is one"
if [ -z "$BEFORE" ]; then
  say "creating the server: first boot installs uv, Python 3.12, the requirements and $RID (~6-8 min)"
else
  say "updating the stack"
fi
cfn_deploy "${COMMON[@]}" CreateInstance=true AmiIdOverride="$AMI_PIN"
load_outputs
[ -n "$IID" ] || die "the stack has no server (InstanceId output missing)"

if [ "$BEFORE" = "$IID" ]; then
  STATE=$(instance_state "$IID")
  if [ "$STATE" = running ]; then
    wait_ssm_online "$IID"
    rollout_release "$IID" "$RID" || die "release $RID failed on the server; the previous release keeps running"
  else
    warn "the server is $STATE: deploy/start.sh starts it and installs $RID"
  fi
else
  say "new server $IID installed $RID at first boot"
fi

say "checking $URL through CloudFront"
if wait_http "$URL/health" 300 && wait_http "$URL/release.json" 120 "$RID"; then
  say "live: $RID"
else
  [ "$(instance_state "$IID")" = running ] && warn "$URL does not answer yet: deploy/status.sh shows the state"
fi
cat <<EOF

  Voice app : $URL   (log in with the same username/email and password as the Document AI app)
  Server    : $IID ($(instance_state "$IID")), Elastic IP $OUT_ElasticIp
  Release   : $RID
  Next      : deploy/update.sh (code only, ~1 min) · deploy/stop.sh / deploy/start.sh · deploy/secrets.sh (phone, WhatsApp)

EOF
print_costs
