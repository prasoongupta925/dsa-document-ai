#!/usr/bin/env bash
# Fast teardown of the lean stack (ap-south-1): stacks deleted in parallel waves, then the resources the
# stacks retain on purpose (buckets, tables, user pool, KMS keys, SSM parameters, log groups).
#   deploy/lean/destroy.sh            # everything except the CDK bootstrap (kept: $0, makes the next deploy faster)
#   deploy/lean/destroy.sh --all      # also the CodeBuild project and the CDK bootstrap
# Only stacks whose relations are real CloudFormation exports have to wait for each other
# (Mcp -> Agent); every other link is an SSM parameter, so most stacks go at the same time.
set -uo pipefail
export AWS_REGION="${AWS_REGION:-ap-south-1}" AWS_DEFAULT_REGION="${AWS_REGION:-ap-south-1}"
R=$AWS_REGION
ALL=false; [ "${1:-}" = "--all" ] && ALL=true
ACCT=$(aws sts get-caller-identity --query Account --output text)
T0=$(date +%s)
say() { printf '%4ss  %s\n' "$(( $(date +%s) - T0 ))" "$*"; }
PROBLEMS="$(mktemp)"; trap 'rm -f "$PROBLEMS"' EXIT
problem() { say "PROBLEM: $*"; echo "$*" >>"$PROBLEMS"; }
exists() { aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1; }

wave() {  # delete the given stacks together and wait for all of them
  local s started=()
  for s in "$@"; do exists "$s" && aws cloudformation delete-stack --stack-name "$s" && started+=("$s"); done
  [ ${#started[@]} -eq 0 ] && return 0
  say "deleting ${started[*]}"
  for s in "${started[@]}"; do
    ( if aws cloudformation wait stack-delete-complete --stack-name "$s" 2>/dev/null; then say "deleted $s"; else
        problem "stack $s not deleted: $(aws cloudformation describe-stack-events --stack-name "$s" \
          --query "StackEvents[?ResourceStatus=='DELETE_FAILED'] | [0].[LogicalResourceId,ResourceStatusReason]" --output text)"; fi ) &
  done
  wait
}
empty_bucket() {  # $1 bucket [$2 region]: every object version and delete marker (versioned buckets too)
  local b=$1 r=${2:-$AWS_REGION} kind batch
  aws s3 rm "s3://$b" --recursive --region "$r" --only-show-errors 2>/dev/null
  for kind in Versions DeleteMarkers; do
    while batch=$(aws s3api list-object-versions --bucket "$b" --region "$r" --max-items 1000 --output json \
            --query "{Objects: $kind[].{Key: Key, VersionId: VersionId}, Quiet: \`true\`}" 2>/dev/null) \
          && echo "$batch" | grep -q '"Key"'; do
      aws s3api delete-objects --bucket "$b" --region "$r" --delete "$batch" >/dev/null || { problem "could not empty $b"; break; }
    done
  done
}

# Remember the stacks' customer-managed KMS keys before the stacks go (they are retained, $1/month each).
KMS_KEYS=$(for st in $(aws cloudformation list-stacks --query "StackSummaries[?StackStatus!='DELETE_COMPLETE' && (starts_with(StackName,'IDP-V2') || starts_with(StackName,'IDPV2'))].StackName" --output text); do
  aws cloudformation describe-stack-resources --stack-name "$st" --query "StackResources[?ResourceType=='AWS::KMS::Key'].PhysicalResourceId" --output text 2>/dev/null
done | tr '\t' '\n' | sed '/^$/d' | sort -u)
say "customer KMS keys to retire after the stacks: $(echo $KMS_KEYS | wc -w)"

say "emptying buckets"
for b in $(aws s3 ls | awk '{print $3}' | grep -E '^idp-v2-.*|^idpv2-.*'); do empty_bucket "$b" & done
for b in $(aws s3api list-directory-buckets --query 'Buckets[].Name' --output text 2>/dev/null); do
  case "$b" in lancedb-*|idp-v2-*) aws s3 rm "s3://$b" --recursive --only-show-errors & ;; esac
done
wait

wave IDP-V2-Retention IDP-V2-Application IDP-V2-Workflow IDP-V2-Webcrawler IDP-V2-Webhook IDP-V2-Worker \
     IDP-V2-LanceService IDP-V2-Ocr IDP-V2-Bda IDP-V2-Transcribe IDP-V2-Agent IDP-V2-Websocket IDP-V2-Event
# The optional CloudFront WAF lives in us-east-1; it can go once IDP-V2-Application (its importer) is gone.
AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 wave IDPV2ApplicationFrontendwaf5A2F87E0
wave IDP-V2-Mcp
wave IDP-V2-Storage
# Leftovers of the old full edition, if any (VPC/NAT and Neptune stacks).
wave IDP-V2-Neptune
wave IDP-V2-Vpc

say "removing retained resources"
for b in $(aws s3 ls | awk '{print $3}' | grep -E '^idp-v2-.*|^idpv2-.*'); do
  ( empty_bucket "$b"; if aws s3 rb "s3://$b" --force >/dev/null 2>&1; then say "deleted bucket $b"; else problem "bucket $b not deleted"; fi ) &
done
for b in $(aws s3api list-directory-buckets --query 'Buckets[].Name' --output text 2>/dev/null); do
  case "$b" in lancedb-*|idp-v2-*) ( aws s3 rm "s3://$b" --recursive --only-show-errors; aws s3api delete-bucket --bucket "$b" && say "deleted $b" ) & ;; esac
done
for t in $(aws dynamodb list-tables --query 'TableNames[?starts_with(@,`IDP-V2`) || starts_with(@,`idp-v2`)]' --output text); do
  ( aws dynamodb update-table --table-name "$t" --no-deletion-protection-enabled >/dev/null 2>&1
    aws dynamodb delete-table --table-name "$t" >/dev/null && say "deleted table $t" ) &
done
for id in $(aws cognito-idp list-user-pools --max-results 50 --query "UserPools[?starts_with(Name,'UserIdentity')].Id" --output text); do
  ( d=$(aws cognito-idp describe-user-pool --user-pool-id "$id" --query 'UserPool.Domain' --output text)
    [ "$d" != "None" ] && aws cognito-idp delete-user-pool-domain --user-pool-id "$id" --domain "$d" >/dev/null 2>&1
    # update-user-pool resets every setting it is not given; Cognito then refuses the call unless the
    # pool's auto-verified attributes are passed back, so the deletion protection would stay on.
    av=$(aws cognito-idp describe-user-pool --user-pool-id "$id" --query 'UserPool.AutoVerifiedAttributes' --output text)
    aws cognito-idp update-user-pool --user-pool-id "$id" --deletion-protection INACTIVE \
      $( [ -n "$av" ] && [ "$av" != "None" ] && echo --auto-verified-attributes $av ) >/dev/null
    if aws cognito-idp delete-user-pool --user-pool-id "$id"; then say "deleted user pool $id"; else problem "user pool $id not deleted"; fi ) &
done
wait
for k in $KMS_KEYS; do  # customer keys the stacks created (retained on stack delete)
  [ "$(aws kms describe-key --key-id "$k" --query KeyMetadata.KeyState --output text 2>/dev/null)" = "Enabled" ] || continue
  aws kms schedule-key-deletion --key-id "$k" --pending-window-in-days 7 --query DeletionDate --output text | sed "s/^/KMS key scheduled for deletion on /"
done
# Customer keys no stack listed (their stack was already gone): list them for a person to check, never guess.
for k in $(aws kms list-keys --query 'Keys[].KeyId' --output text); do
  case " $KMS_KEYS " in *" $k "*) continue ;; esac
  m=$(aws kms describe-key --key-id "$k" --query 'KeyMetadata.[KeyManager,KeyState]' --output text 2>/dev/null)
  [ "$m" = "CUSTOMER	Enabled" ] && say "note: customer KMS key $k is still enabled and was not created by a stack that still existed; check it"
done
for p in $(aws ssm get-parameters-by-path --path /idp-v2 --recursive --query 'Parameters[].Name' --output text); do
  aws ssm delete-parameter --name "$p"
done
for lg in $(aws logs describe-log-groups --query 'logGroups[].logGroupName' --output text); do
  case "$lg" in *idp-v2*|*IDP-V2*|*IDPV2*|/aws/bedrock-agentcore/*|/aws/codebuild/sample-aws-idp*|/aws/vendedlogs/states/idp*)
    aws logs delete-log-group --log-group-name "$lg" 2>/dev/null ;; esac
done

# The WAF stack's cross-region export writer leaves a log group in us-east-1 with no expiry.
for lg in $(aws logs describe-log-groups --region us-east-1 --log-group-name-prefix /aws/lambda/IDPV2 --query 'logGroups[].logGroupName' --output text); do
  aws logs delete-log-group --region us-east-1 --log-group-name "$lg" && say "deleted us-east-1 log group $lg"
done
# The us-east-1 bootstrap only served the WAF stack: remove it when nothing else there uses it.
if [ -z "$(aws cloudformation list-stacks --region us-east-1 --query "StackSummaries[?StackStatus!='DELETE_COMPLETE' && StackName!='CDKToolkit'].StackName" --output text)" ] \
   && aws cloudformation describe-stacks --region us-east-1 --stack-name CDKToolkit >/dev/null 2>&1; then
  b="cdk-hnb659fds-assets-${ACCT}-us-east-1"
  empty_bucket "$b" us-east-1
  aws ecr delete-repository --region us-east-1 --repository-name "cdk-hnb659fds-container-assets-${ACCT}-us-east-1" --force >/dev/null 2>&1
  AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 wave CDKToolkit
  if aws s3 rb "s3://$b" --force --region us-east-1 >/dev/null 2>&1; then say "deleted the us-east-1 bootstrap bucket"; else problem "bucket $b not deleted"; fi
  aws ssm delete-parameter --region us-east-1 --name /cdk-bootstrap/hnb659fds/version >/dev/null 2>&1
fi

if $ALL; then
  wave sample-aws-idp-pipeline-codebuild sample-aws-idp-pipeline-destroy-codebuild
  b="cdk-hnb659fds-assets-${ACCT}-${R}"; empty_bucket "$b"
  aws ecr delete-repository --repository-name "cdk-hnb659fds-container-assets-${ACCT}-${R}" --force >/dev/null 2>&1
  wave CDKToolkit
  aws s3 rb "s3://$b" --force >/dev/null 2>&1 || problem "bucket $b not deleted"
  aws ssm delete-parameter --name /cdk-bootstrap/hnb659fds/version >/dev/null 2>&1
fi

say "left in $R:"
aws cloudformation list-stacks --query "StackSummaries[?StackStatus!='DELETE_COMPLETE' && (starts_with(StackName,'IDP') || starts_with(StackName,'CDK') || starts_with(StackName,'sample-aws'))].[StackName,StackStatus]" --output text
if [ -s "$PROBLEMS" ]; then
  say "finished in $(( ($(date +%s) - T0) / 60 )) min with $(wc -l <"$PROBLEMS") problem(s):"; sed 's/^/   - /' "$PROBLEMS"; exit 1
fi
say "done in $(( ($(date +%s) - T0) / 60 )) min, nothing left over"
