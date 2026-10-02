#!/usr/bin/env bash
# Shared helpers for deploy/*.sh (sourced, not run). Mumbai only; uses AWS_PROFILE or the default credential chain.
set -euo pipefail

if [ -n "${AWS_REGION:-}" ] && [ "$AWS_REGION" != ap-south-1 ]; then
  echo "This stack runs in ap-south-1 (Mumbai) only; AWS_REGION is $AWS_REGION." >&2; exit 2
fi
export AWS_REGION=ap-south-1 AWS_DEFAULT_REGION=ap-south-1 AWS_PAGER=""

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"   # voice-bot/
STACK="${STACK_NAME:-docai-voice}"
IDP_STACK="${IDP_STACK:-IDP-V2-Application}"
SSM_PREFIX=/docai-voice
SERVICE=docai-voice
T0=$(date +%s)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/docai-voice.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

say() { printf '%5ss  %s\n' "$(( $(date +%s) - T0 ))" "$*"; }
warn() { printf '%5ss  WARNING: %s\n' "$(( $(date +%s) - T0 ))" "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { local c; for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "needs '$c' on PATH"; done; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$1"; }

# One AWS CLI call that rides out short network drops: 4 tries over ~1 min.
aws_retry() {
  local n
  for n in 1 2 3; do aws "$@" 2>/dev/null && return 0; sleep $(( n * 8 )); done
  aws "$@"
}

account_id() { aws_retry sts get-caller-identity --query Account --output text; }

# ------------------------------------------------------------------------------------------- stack
stack_status() {
  local out
  if out=$(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text 2>&1); then
    echo "$out"; return 0
  fi
  case "$out" in *"does not exist"*) echo NONE;; *) die "cannot read stack $STACK: $out";; esac
}

# Sets OUT_<OutputKey> for every stack output (empty when the output is absent).
load_outputs() {
  local json
  json=$(aws_retry cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].Outputs' --output json)
  OUT_Url="" OUT_DistributionDomainName="" OUT_DistributionId="" OUT_CodeBucketName="" OUT_InstanceId=""
  OUT_ElasticIp="" OUT_UserPoolClientId="" OUT_UserPoolId="" OUT_OriginDomainName=""
  eval "$(python3 -c '
import json, re, shlex, sys
for o in json.loads(sys.argv[1] or "[]") or []:
    if re.fullmatch(r"[A-Za-z0-9]+", o["OutputKey"]):
        print("OUT_%s=%s" % (o["OutputKey"], shlex.quote(o["OutputValue"])))
' "$json")"
  URL="$OUT_Url" BUCKET="$OUT_CodeBucketName" IID="$OUT_InstanceId"
}

instance_state() { aws_retry ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].State.Name' --output text; }
instance_ami() { aws_retry ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].ImageId' --output text; }

# cfn_deploy KEY=VALUE...  Parameters not given keep their current value (CloudFormation "use previous value").
# Secret parameters go in the environment variable CFN_SECRET_PARAMS ("Key=Value", newline-separated), never in argv.
cfn_deploy() {
  local pf="$WORK/parameters.json" rc=0
  (umask 077; python3 -c '
import json, os, sys
params = sys.argv[1:] + [p for p in os.environ.get("CFN_SECRET_PARAMS", "").splitlines() if p]
print(json.dumps(params))' "$@" > "$pf")
  aws cloudformation deploy --stack-name "$STACK" --template-file "$DEPLOY_DIR/template.yaml" \
    --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset --parameter-overrides "file://$pf" \
    --tags Project=docai-voice ${KEEP_FAILED:+--disable-rollback} || rc=$?
  rm -f "$pf"
  if [ "$rc" != 0 ]; then show_failure; die "CloudFormation deploy of $STACK failed"; fi
}

show_failure() {
  echo "--- failed stack events:" >&2
  aws cloudformation describe-stack-events --stack-name "$STACK" --output text \
    --query 'StackEvents[?contains(ResourceStatus, `FAILED`)] | [0:8].[LogicalResourceId, ResourceStatusReason]' >&2 || true
  local iid
  iid=$(aws cloudformation describe-stack-events --stack-name "$STACK" --output text \
    --query "StackEvents[?LogicalResourceId=='Instance' && starts_with(PhysicalResourceId, 'i-')] | [0].PhysicalResourceId" 2>/dev/null || true)
  if [ -n "$iid" ] && [ "$iid" != None ]; then
    echo "--- first-boot log of $iid (EC2 console output, kept about an hour after termination):" >&2
    aws ec2 get-console-output --instance-id "$iid" --latest --query Output --output text 2>/dev/null \
      | grep -a -E 'docai-voice|\[release|ERROR|Error|error' | tail -n 40 >&2 || true
  fi
}

# ------------------------------------------------------------------------------------------- discovery (read-only)
# IDP_API_URL, IDP_API_ID, USER_POOL_ID, IDP_APP_URL from the IDP stack outputs (env values win).
discover_idp() {
  local json found
  json=$(aws_retry cloudformation describe-stacks --stack-name "$IDP_STACK" --query 'Stacks[0].Outputs' --output json) \
    || die "IDP stack $IDP_STACK not found in ap-south-1 (set IDP_STACK=...)"
  found=$(python3 -c '
import json, re, shlex, sys
outs = {o["OutputKey"]: o["OutputValue"] for o in json.loads(sys.argv[1] or "[]") or []}
pick = lambda test: next((v for k, v in sorted(outs.items()) if test(k)), "")
url = pick(lambda k: "BackendUrl" in k)
pool = pick(lambda k: "UserPoolId" in k)
front = pick(lambda k: "FrontendDistributionDomainName" in k)
print("_URL=%s _POOL=%s _FRONT=%s" % (shlex.quote(url), shlex.quote(pool), shlex.quote(front)))
' "$json")
  local _URL _POOL _FRONT; eval "$found"
  IDP_API_URL="${IDP_API_URL:-$_URL}"
  USER_POOL_ID="${USER_POOL_ID:-$_POOL}"
  IDP_APP_URL="${IDP_APP_URL:-${_FRONT:+https://$_FRONT}}"
  if [ -z "$USER_POOL_ID" ]; then
    USER_POOL_ID=$(aws_retry cognito-idp list-user-pools --max-results 60 --output text \
      --query "UserPools[?starts_with(Name, 'UserIdentity')] | [0].Id")
    [ "$USER_POOL_ID" = None ] && USER_POOL_ID=""
  fi
  [[ "$IDP_API_URL" =~ ^https://([a-z0-9]{10})\.execute-api\.ap-south-1\.amazonaws\.com/?$ ]] \
    || die "IDP backend URL not found or not in ap-south-1 (got '${IDP_API_URL}'); set IDP_API_URL=..."
  IDP_API_ID="${BASH_REMATCH[1]}"
  [[ "$USER_POOL_ID" =~ ^ap-south-1_[0-9A-Za-z]+$ ]] || die "IDP user pool not found; set USER_POOL_ID=..."
}

# VPC_ID / SUBNET_ID: the default VPC and its first default subnet in an AZ that offers $1 (env values win).
discover_network() {
  local itype=$1 azs line
  VPC_ID="${VPC_ID:-$(aws_retry ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)}"
  [ -n "$VPC_ID" ] && [ "$VPC_ID" != None ] || die "no default VPC in ap-south-1 (create one: aws ec2 create-default-vpc)"
  # CloudFront reaches the server by its public DNS name (ec2-<ip>.ap-south-1.compute.amazonaws.com)
  [ "$(aws_retry ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsHostnames \
       --query EnableDnsHostnames.Value --output text)" = True ] \
    || die "$VPC_ID has DNS hostnames off: aws ec2 modify-vpc-attribute --vpc-id $VPC_ID --enable-dns-hostnames"
  if [ -z "${SUBNET_ID:-}" ]; then
    azs=" $(aws_retry ec2 describe-instance-type-offerings --location-type availability-zone \
      --filters "Name=instance-type,Values=$itype" --query 'InstanceTypeOfferings[].Location' --output text | tr '\t' ' ') "
    SUBNET_ID=""
    while read -r line; do
      set -- $line
      case "$azs" in *" $1 "*) SUBNET_ID=$2; break;; esac
    done < <(aws_retry ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" Name=default-for-az,Values=true \
               --query 'Subnets[].[AvailabilityZone,SubnetId]' --output text | sort)
    [ -n "$SUBNET_ID" ] || die "no default subnet of $VPC_ID is in an AZ that offers $itype"
  fi
}

cloudfront_prefix_list() {
  local id
  id=$(aws_retry ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
    --query 'PrefixLists[0].PrefixListId' --output text)
  [[ "$id" =~ ^pl-[0-9a-f]+$ ]] || die "CloudFront origin-facing prefix list not found"
  echo "$id"
}

# ------------------------------------------------------------------------------------------- SSM parameters
# ssm_put_secure NAME VALUE_FILE DESCRIPTION [overwrite]   (the value never appears on a command line)
ssm_put_secure() {
  local f="$WORK/put.json" rc=0
  (umask 077; python3 -c '
import json, sys
name, value_file, desc, overwrite = sys.argv[1:5]
value = open(value_file, encoding="utf-8").read().rstrip("\r\n")
print(json.dumps({"Name": name, "Value": value, "Type": "SecureString", "Description": desc,
                  "Overwrite": overwrite == "overwrite", "Tier": "Standard"}))
' "$1" "$2" "$3" "${4:-}" > "$f")
  aws ssm put-parameter --cli-input-json "file://$f" >/dev/null || rc=$?
  rm -f "$f"
  return "$rc"
}

# The CloudFront -> app shared secret: read it, or create it once (43 random URL-safe characters).
origin_secret() {
  local v name="$SSM_PREFIX/secrets/ORIGIN_VERIFY"
  if v=$(aws ssm get-parameter --name "$name" --with-decryption --query Parameter.Value --output text 2>/dev/null) && [ -n "$v" ]; then
    printf '%s' "$v"; return 0
  fi
  (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$WORK/origin")
  ssm_put_secure "$name" "$WORK/origin" "CloudFront X-Origin-Verify header value (managed by deploy.sh)" >/dev/null \
    || die "cannot write $name"
  cat "$WORK/origin"; rm -f "$WORK/origin"
}

# ------------------------------------------------------------------------------------------- build + package
# Builds voice-bot/frontend; sets FRONTEND_OUT. --skip: reuse the last build.
build_frontend() {
  local skip=${1:-} fe="$VOICE_ROOT/frontend" npm_bin
  if [ -z "$skip" ]; then
    npm_bin=$(command -v npm || true)
    [ -z "$npm_bin" ] && [ -x "$HOME/.local/node22/bin/npm" ] && export PATH="$HOME/.local/node22/bin:$PATH" && npm_bin=npm
    [ -n "$npm_bin" ] || die "npm not found (install Node 22, or use --skip-frontend-build with an existing build)"
    say "building the web app (npm)"
    if [ -f "$fe/package-lock.json" ]; then
      (cd "$fe" && npm ci --no-audit --no-fund --loglevel=error >/dev/null) || die "npm ci failed in $fe"
    else
      (cd "$fe" && npm install --no-audit --no-fund --loglevel=error >/dev/null) || die "npm install failed in $fe"
    fi
    (cd "$fe" && npm run build --silent >/dev/null) || die "npm run build failed in $fe"
  fi
  FRONTEND_OUT="${FRONTEND_OUT:-}"
  if [ -z "$FRONTEND_OUT" ]; then
    local d newest=""
    for d in "$fe/dist" "$fe/build"; do
      [ -f "$d/index.html" ] || continue
      if [ -z "$newest" ] || [ "$d/index.html" -nt "$newest/index.html" ]; then newest=$d; fi
    done
    FRONTEND_OUT=$newest
  fi
  [ -n "$FRONTEND_OUT" ] && [ -f "$FRONTEND_OUT/index.html" ] || die "no built web app in $fe/dist or $fe/build"
}

new_release_id() {
  local sha
  sha=$(git -C "$VOICE_ROOT" rev-parse --short=7 HEAD 2>/dev/null || echo nogit)
  if [ -n "$(git -C "$VOICE_ROOT" status --porcelain -- . 2>/dev/null)" ]; then sha="$sha-dirty"; fi
  printf '%s-%s' "$(date -u +%Y%m%dT%H%M%SZ)" "$sha"
}

# package_release OUT.zip RELEASE_ID FRONTEND_DIR -> backend/, frontend/ (built), deploy/app, deploy/instance, RELEASE.json
package_release() {
  VOICE_ROOT="$VOICE_ROOT" python3 - "$@" <<'EOF'
import fnmatch, json, os, subprocess, sys, time, zipfile
out, rid, front = sys.argv[1:4]
root = os.environ["VOICE_ROOT"]
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "tests", "test", "recordings", ".git"}
SKIP_FILES = [".env", ".env.*", "*.pyc", "*.pyo", "*.wav", "*.mp3", "*.log", ".DS_Store", "Dockerfile",
              ".dockerignore", "test_*.py", "*_test.py", "conftest.py", "pytest.ini", "requirements-dev.txt",
              "*.pem", "*.key"]
count = {}

def add_tree(z, src, prefix, skip_dirs=SKIP_DIRS, skip_files=SKIP_FILES):
    if not os.path.isdir(src):
        sys.exit(f"missing folder: {src}")
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or any(fnmatch.fnmatch(name, p) for p in skip_files):
                continue
            z.write(full, os.path.join(prefix, os.path.relpath(full, src)))
            count[prefix] = count.get(prefix, 0) + 1

for need in ("backend/main.py", "backend/requirements.txt"):
    if not os.path.isfile(os.path.join(root, need)):
        sys.exit(f"missing {need}")
if not os.path.isfile(os.path.join(front, "index.html")):
    sys.exit(f"no index.html in {front}")
sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
meta = {"id": rid, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_sha": sha or None,
        "frontend": os.path.relpath(front, root)}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    add_tree(z, os.path.join(root, "backend"), "backend")
    add_tree(z, os.path.join(root, "deploy", "app"), "deploy/app")
    add_tree(z, os.path.join(root, "deploy", "instance"), "deploy/instance")
    add_tree(z, front, "frontend", skip_dirs={".git"}, skip_files=[".env", ".env.*", ".DS_Store", "*.map"])
    z.writestr("RELEASE.json", json.dumps(meta, indent=1) + "\n")
size = os.path.getsize(out)
print(f"{os.path.basename(out)}: {size / 1e6:.1f} MB, files: " + ", ".join(f"{k} {v}" for k, v in count.items()))
if size > 80e6:
    sys.exit("release zip is over 80 MB: something large got in")
EOF
}

upload_release() {  # upload_release ZIP RELEASE_ID
  aws s3 cp --only-show-errors "$1" "s3://$BUCKET/releases/$2.zip"
  aws s3 cp --only-show-errors "s3://$BUCKET/releases/$2.zip" "s3://$BUCKET/releases/latest.zip"
  printf '{"id": "%s", "key": "releases/%s.zip"}\n' "$2" "$2" \
    | aws s3 cp --only-show-errors --content-type application/json - "s3://$BUCKET/releases/latest.json"
}

upload_offline_page() {
  aws s3 cp --only-show-errors --content-type 'text/html; charset=utf-8' --cache-control 'max-age=60' \
    "$DEPLOY_DIR/offline.html" "s3://$BUCKET/public/offline.html"
}

latest_release_id() {
  aws s3 cp --only-show-errors "s3://$BUCKET/releases/latest.json" - 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])' 2>/dev/null || true
}

running_release_id() {
  curl -fsS -m 10 "$URL/release.json" 2>/dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin).get("id") or "")' 2>/dev/null || true
}

# ------------------------------------------------------------------------------------------- server commands (SSM)
wait_ssm_online() {  # wait_ssm_online INSTANCE_ID [SECONDS]
  local end=$(( $(date +%s) + ${2:-240} )) st
  while [ "$(date +%s)" -lt "$end" ]; do
    st=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$1" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)
    [ "$st" = Online ] && return 0
    sleep 5
  done
  die "the SSM agent of $1 is not online (is the server running?)"
}

# ssm_run INSTANCE_ID COMMENT TIMEOUT_SECONDS < script   -> prints the output; returns 0 only on Success
ssm_run() {
  local iid=$1 comment=$2 timeout=$3 pf="$WORK/ssm.json" cid st end
  python3 -c 'import json, sys; print(json.dumps({"commands": [sys.stdin.read()], "executionTimeout": [sys.argv[1]]}))' \
    "$timeout" > "$pf"
  cid=$(aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript --comment "$comment" \
        --parameters "file://$pf" --timeout-seconds 120 --query Command.CommandId --output text)
  end=$(( $(date +%s) + timeout + 60 ))
  while :; do
    sleep 5
    st=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query Status --output text 2>/dev/null || echo Pending)
    case "$st" in Success|Failed|Cancelled|TimedOut) break;; esac
    [ "$(date +%s)" -lt "$end" ] || { st=TimedOut; break; }
  done
  aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query StandardOutputContent --output text 2>/dev/null \
    | tail -n 30 | sed 's/^/    /'
  if [ "$st" != Success ]; then
    aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query StandardErrorContent --output text 2>/dev/null \
      | tail -n 30 | sed 's/^/    ! /' >&2
    warn "server command '$comment' ended with $st"
    return 1
  fi
}

# rollout_release INSTANCE_ID RELEASE_ID [force]   -> the server downloads releases/<id>.zip and runs its release.sh
rollout_release() {
  [[ "$2" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad release id $2"
  say "installing $2 on $1 (SSM Run Command)"
  ssm_run "$1" "docai-voice release $2" 900 <<EOF
set -euo pipefail
tmp=\$(mktemp -d)
trap 'rm -rf "\$tmp"' EXIT
aws s3 cp --only-show-errors "s3://$BUCKET/releases/$2.zip" "\$tmp/release.zip" --region ap-south-1
python3 -c 'import sys, zipfile; open(sys.argv[2], "wb").write(zipfile.ZipFile(sys.argv[1]).read("deploy/instance/release.sh"))' "\$tmp/release.zip" "\$tmp/release.sh"
${3:+FORCE=1 }bash "\$tmp/release.sh" "\$tmp/release.zip"
EOF
}

restart_service() {  # restart_service INSTANCE_ID
  say "restarting $SERVICE on $1"
  ssm_run "$1" "docai-voice restart" 300 <<'EOF'
set -uo pipefail
started=$(date +%s)
systemctl restart docai-voice
for i in $(seq 1 120); do
  curl -fsS -m 3 -o /dev/null http://127.0.0.1:8080/health && { echo "docai-voice answers /health"; exit 0; }
  sleep 2
done
# only this start's lines (SSM keeps command output for 30 days; older lines may be about calls)
journalctl -u docai-voice --since "@$started" -n 40 --no-pager
exit 1
EOF
}

# wait_http URL SECONDS [TEXT]   -> 0 once URL answers 2xx (and its body contains TEXT)
wait_http() {
  local url=$1 end=$(( $(date +%s) + $2 )) want=${3:-} body
  while [ "$(date +%s)" -lt "$end" ]; do
    if body=$(curl -fsS -m 10 "$url" 2>/dev/null); then
      if [ -z "$want" ] || printf '%s' "$body" | grep -q -- "$want"; then return 0; fi
    fi
    sleep 5
  done
  return 1
}

print_costs() {
  cat <<'EOF'
Hosting cost (ap-south-1, on-demand, before GST; usage of Transcribe/Polly/Bedrock is extra, per call minute):
  running : t4g.small $0.0112/h + public IPv4 $0.005/h + 12 GB gp3  -> about $0.42/day (~Rs 37), $12.9/month 24x7
  stopped : Elastic IP $0.005/h + 12 GB gp3 $0.0912/GB-month      -> about $4.7/month (~Rs 420)
  CloudFront, S3, SSM: within the free tier for a demo
EOF
}
