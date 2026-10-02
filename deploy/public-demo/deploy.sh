#!/usr/bin/env bash
# Publish the read-only public demo of DSA Document AI (no login, synthetic data) on its own CloudFront URL.
#
#   AWS_PROFILE=<profile> ./deploy.sh <dist dir> [stack name] [region]
#
# <dist dir> is the web app built with VITE_PUBLIC_DEMO=1 (every API call is answered in the browser from
# snapshots; nothing calls a backend). Creates or updates the stack in template.yaml (private S3 bucket +
# CloudFront with Origin Access Control), uploads the build (hashed assets cached for a year, index.html never
# cached) and invalidates the CDN. Only AWS-billed services: S3 and CloudFront. No domain, nothing from Marketplace.
# Optional: VOICE_VIDEO_URL=https://... fills the voice bot video link of the build ({VOICE_VIDEO_URL}).
set -euo pipefail

DIST=${1:?usage: deploy.sh <dist dir> [stack name] [region]}
STACK=${2:-dsa-docai-public-demo}
REGION=${3:-${AWS_REGION:-ap-south-1}}
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$DIST/index.html" ] || { echo "no index.html in $DIST" >&2; exit 1; }

aws cloudformation deploy --region "$REGION" --stack-name "$STACK" --template-file "$HERE/template.yaml" \
  --tags app=dsa-document-ai-public-demo --no-fail-on-empty-changeset
out() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BUCKET=$(out BucketName)
DIST_ID=$(out DistributionId)
URL=$(out DemoUrl)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -R "$DIST"/. "$STAGE"/
if [ -n "${VOICE_VIDEO_URL:-}" ]; then
  python3 - "$STAGE" "$VOICE_VIDEO_URL" <<'PY'
import pathlib, sys
root, url = pathlib.Path(sys.argv[1]), sys.argv[2]
for p in root.rglob("*.js"):
    t = p.read_text(encoding="utf-8")
    if "{VOICE_VIDEO_URL}" in t:
        p.write_text(t.replace("{VOICE_VIDEO_URL}", url), encoding="utf-8")
        print(f"voice video link set in {p.name}")
PY
fi

# 0) CloudFront compresses only objects up to 10,000,000 bytes, and the main JS bundle is larger
#    (about 10.4 MB). Files over that limit are gzipped here, under the same name, and uploaded with
#    Content-Encoding: gzip, so browsers still get them compressed. They are left out of the syncs below.
BIG_LIMIT=10000000
BIG=()
EXCLUDE_BIG=()
while IFS= read -r -d '' f; do
  rel=${f#"$STAGE"/}
  gzip -9 -n -c "$f" > "$f.gz" && mv "$f.gz" "$f"
  BIG+=("$rel")
  EXCLUDE_BIG+=(--exclude "$rel")
  echo "pre-gzipped $rel ($(wc -c < "$f") bytes gzipped)"
done < <(find "$STAGE" -type f -size +"${BIG_LIMIT}"c -print0)

content_type() {
  case "$1" in
    *.js | *.mjs) echo "application/javascript" ;;
    *.css) echo "text/css" ;;
    *.json | *.map) echo "application/json" ;;
    *.html) echo "text/html; charset=utf-8" ;;
    *.svg) echo "image/svg+xml" ;;
    *.wasm) echo "application/wasm" ;;
    *) echo "application/octet-stream" ;;
  esac
}

# 1) hashed assets (new names on every build): cached for a year
for rel in ${BIG[@]+"${BIG[@]}"}; do
  case "$rel" in
    assets/*) cache="public,max-age=31536000,immutable" ;;
    *) cache="public,max-age=3600" ;;
  esac
  aws s3 cp "$STAGE/$rel" "s3://$BUCKET/$rel" --region "$REGION" --only-show-errors \
    --content-encoding gzip --content-type "$(content_type "$rel")" --cache-control "$cache"
done
aws s3 sync "$STAGE/assets" "s3://$BUCKET/assets" --region "$REGION" --only-show-errors \
  --cache-control "public,max-age=31536000,immutable" \
  ${EXCLUDE_BIG[@]+"${EXCLUDE_BIG[@]/#assets\//}"}
# 2) the other static files (logo, favicon): an hour
aws s3 sync "$STAGE" "s3://$BUCKET" --region "$REGION" --only-show-errors \
  --exclude "assets/*" --exclude "index.html" --cache-control "public,max-age=3600" \
  ${EXCLUDE_BIG[@]+"${EXCLUDE_BIG[@]}"}
# 3) index.html last, never cached, so a visitor never gets a page whose assets are not there yet
aws s3 cp "$STAGE/index.html" "s3://$BUCKET/index.html" --region "$REGION" --only-show-errors \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"
# 4) remove files of older builds (the pre-gzipped files are excluded, so they are never re-uploaded plain)
aws s3 sync "$STAGE" "s3://$BUCKET" --region "$REGION" --only-show-errors --delete --size-only \
  --exclude "index.html" ${EXCLUDE_BIG[@]+"${EXCLUDE_BIG[@]}"}

INV=$(aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" --query Invalidation.Id --output text)
aws cloudfront wait invalidation-completed --distribution-id "$DIST_ID" --id "$INV"
echo "Public demo: $URL"
