#!/usr/bin/env bash
# Delete everything the voice stack created: server, disk, Elastic IP, CloudFront, login client, role, bucket,
# settings and (unless --keep-secrets) the provider secrets in SSM. Takes 5-15 minutes (CloudFront).
#
#   deploy/destroy.sh                 # asks you to type the stack name
#   deploy/destroy.sh --yes           # no question
#   deploy/destroy.sh --keep-secrets  # keep /docai-voice/secrets/* (Plivo, Exotel, WhatsApp keys)
#
# The IDP itself (Document AI) and its user pool are not touched; only the voice app's client is removed.
source "$(dirname "$0")/lib.sh"
YES=""; KEEP_SECRETS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift;;
    --keep-secrets) KEEP_SECRETS=1; shift;;
    --stack) STACK="${2:?}"; shift 2;;
    -h|--help) usage "$0"; exit 0;;
    *) die "unknown option $1 (see --help)";;
  esac
done
need aws python3

STATUS=$(stack_status)
say "stack $STACK: $STATUS"
if [ -z "$YES" ]; then
  printf 'This deletes the voice server, its URL and its data. Type the stack name (%s) to go on: ' "$STACK"
  read -r answer
  [ "$answer" = "$STACK" ] || die "cancelled"
fi

empty_bucket() {
  [ -n "${BUCKET:-}" ] || return 0
  aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || return 0
  say "emptying s3://$BUCKET"
  aws s3 rm "s3://$BUCKET" --recursive --only-show-errors
}

if [ "$STATUS" != NONE ]; then
  case "$STATUS" in *_IN_PROGRESS) say "waiting for the running stack operation"
    aws cloudformation wait stack-update-complete --stack-name "$STACK" 2>/dev/null \
      || aws cloudformation wait stack-create-complete --stack-name "$STACK" 2>/dev/null || true;; esac
  load_outputs
  empty_bucket
  say "deleting stack $STACK (CloudFront is disabled first; 5-15 min)"
  aws cloudformation delete-stack --stack-name "$STACK"
  if ! aws cloudformation wait stack-delete-complete --stack-name "$STACK"; then
    warn "delete did not finish; emptying the bucket again and retrying once"
    empty_bucket
    aws cloudformation delete-stack --stack-name "$STACK"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK" || { show_failure; die "stack $STACK was not deleted"; }
  fi
  say "stack deleted"
fi

names=$(aws ssm get-parameters-by-path --path "$SSM_PREFIX" --recursive --query 'Parameters[].Name' --output text 2>/dev/null \
  | tr '\t' '\n' | sed '/^$/d' || true)
if [ -n "$KEEP_SECRETS" ]; then
  names=$(printf '%s\n' "$names" | grep -v "^$SSM_PREFIX/secrets/" | grep -v '^$' || true)
  names=$(printf '%s\n%s\n' "$names" "$SSM_PREFIX/secrets/ORIGIN_VERIFY" | sed '/^$/d')   # tied to the old CloudFront
fi
if [ -n "$names" ]; then
  say "deleting SSM parameters: $(printf '%s ' $names)"
  printf '%s\n' $names | xargs -n 10 aws ssm delete-parameters --names >/dev/null 2>&1 || true
fi
say "done: nothing of the voice stack is left${KEEP_SECRETS:+ except the provider secrets under $SSM_PREFIX/secrets/}"
