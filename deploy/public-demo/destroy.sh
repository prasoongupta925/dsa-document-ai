#!/usr/bin/env bash
# Remove the public demo: empty its bucket, then delete the stack (bucket + CloudFront).
#   AWS_PROFILE=<profile> ./destroy.sh [stack name] [region]
set -euo pipefail
STACK=${1:-dsa-docai-public-demo}
REGION=${2:-${AWS_REGION:-ap-south-1}}
BUCKET=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
VERSIONS=$(mktemp)
trap 'rm -f "$VERSIONS"' EXIT
aws s3 rm "s3://$BUCKET" --recursive --region "$REGION" --only-show-errors
# versions left by overwrites (the bucket keeps none for long, but delete-stack needs it empty)
aws s3api list-object-versions --bucket "$BUCKET" --region "$REGION" --output json \
  --query '{Objects: [Versions, DeleteMarkers][][].{Key: Key, VersionId: VersionId}}' > "$VERSIONS" || true
if grep -q '"Key"' "$VERSIONS" 2>/dev/null; then
  aws s3api delete-objects --bucket "$BUCKET" --region "$REGION" --delete "file://$VERSIONS" >/dev/null
fi
aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK"
echo "Deleted $STACK"
