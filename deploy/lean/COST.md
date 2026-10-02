# Lean edition: cost and deploy time (ap-south-1, Mumbai)

The lean edition runs the same app (same UI, pipeline, agents and file check). It drops the six
resources that bill every hour whether anyone uses the app or not:

| Removed (always on) | What replaced it | Idle cost removed |
|---|---|---|
| NAT gateway + its public IPv4 (VPC stack) | nothing: no Lambda runs in a VPC any more | ~$1.4/day |
| Neptune Serverless (knowledge graph) | graph switched off (graph view stays empty) | ~$2.9/day |
| ElastiCache Serverless (WebSocket connections, list cache) | one DynamoDB on-demand table; list cache off | ~$0.3-3/day |
| ECS Fargate backend (1 vCPU, 2 GB) + load balancer + VPC link | same backend container on Lambda (Lambda Web Adapter) | ~$1.6/day |
| CloudFront WAF (us-east-1 stack) | off by default, `--waf` turns it back on | ~$0.3/day |

Measured on our test account in September: the full edition idles at about **$7.6/day (~Rs 20,000/month)**.

## What still costs money when nobody uses it

| Item | Price (Mumbai) | Per month |
|---|---|---|
| 2 customer-managed KMS keys (webhook secrets, data) | $1 per key | $2 |
| S3 + DynamoDB storage of 7 days of demo files | $0.025/GB, DynamoDB 25 GB free | < $0.5 |
| CloudWatch Logs (7-day retention) | $0.67/GB ingested | < $0.5 |
| Cognito (up to 10,000 monthly users), CloudFront (1 TB, 10 M requests), Lambda (1 M requests, 400,000 GB-s) | free tier, always free | $0 |

**Idle: about $3/month (~Rs 260), i.e. ~$0.10/day, 98-99% less than the full edition.**

## Pay per use (only when the team works)

| Service | Mumbai price | Typical use |
|---|---|---|
| Amazon Nova 2 Lite (global profile) | $0.35 per 1 M input tokens, $2.95 per 1 M output | reading a page, Ask answers; descriptions, summaries and facts until the model switch |
| gpt-oss-120b (runs in Mumbai) | $0.18 per 1 M input tokens, $0.71 per 1 M output | document facts, after the model switch |
| Gemma 3 12B (runs in Mumbai) | $0.11 per 1 M input tokens, $0.34 per 1 M output | page descriptions and summaries, after the model switch |
| Lambda (arm64) | $0.0000133334 per GB-second, $0.20 per 1 M requests | pipeline steps, backend API |
| API Gateway HTTP API | $1.05 per 1 M requests | every screen action |
| API Gateway WebSocket | $1 per 1 M messages, $0.25 per 1 M connection-minutes | live progress in the UI |
| DynamoDB on-demand | $0.71 per 1 M writes, $0.1425 per 1 M reads | projects, documents, facts |
| Step Functions (standard) | $0.0285 per 1,000 state transitions (4,000 free) | one run per document, ~40 transitions |
| Amazon Transcribe (batch) | $0.006 per audio minute ($0.0001 per second) | call QA |
| AgentCore Runtime | $0.0895 per vCPU-hour, $0.00945 per GB-hour, only while an answer is being produced | chat agents |

Flex tier (same model, half price, requests may wait longer): Nova 2 Lite $0.175 / $1.475,
gpt-oss-120b $0.09 / $0.355, Gemma 3 12B $0.06 / $0.17 per 1 M input / output tokens.

The app's cost meter shows the facts-step cost of every document and the cost of every Ask answer.
Page reading, descriptions and summaries are not in it (81% of the document cost in the seed run
below); CloudWatch (AWS/Bedrock token metrics) and Cost Explorer show the full spend.

### Measured: seed run on the live stack (1 Oct 2026)

19 demo PDFs (21 pages) and 1 recorded call: 84 Nova 2 Lite calls, 240,757 input + 53,041 output
tokens = **$0.24, about $0.013 (Rs 1.1) per document** (CloudWatch AWS/Bedrock, 07:40-07:50 UTC).
The facts step was $0.045 of it (19 `idp-v2-document-facts` log lines); search embeddings in
us-east-1 added $0.005. Each document took ~40 Step Functions transitions and ~20 Lambda GB-seconds.

### Example month for one DSA branch (~Rs 88 per USD)

500 loan files x 7 documents (3,500 documents), 2,000 Ask questions, 300 minutes of recorded calls:

| Part | Now (Nova 2 Lite for everything) | After the model switch |
|---|---|---|
| Reading + facts, 3,500 documents | $0.0127 each (measured): ~Rs 3,900 | ~$0.009 each (est.): ~Rs 2,800 |
| 2,000 Ask answers (~Rs 0.3 each, Nova 2 Lite in both) | ~Rs 600 | ~Rs 600 |
| 300 call minutes (Transcribe, $0.006/min) | ~Rs 160 | ~Rs 160 |
| Lambda, Step Functions, API Gateway, DynamoDB, S3 | ~Rs 500-900 | ~Rs 500-900 |
| Idle base (KMS, logs, storage) | ~Rs 260 | ~Rs 260 |
| **Total** | **~Rs 5,400-5,800 per month** | **~Rs 4,300-4,700 per month** |

After the model switch: facts on gpt-oss-120b, page descriptions and summaries on Gemma 3 12B,
page reading and Ask still on Nova 2 Lite. The estimate prices the seed run's tokens at the Mumbai
rates above; for facts it uses our 6-document test, where gpt-oss-120b cost 0.54x Nova 2 Lite
(its reasoning tokens included). Page reading is then close to 80% of the document cost. If these
pipeline calls run on the Flex tier, a document costs about $0.0045 (est.): ~Rs 1,400 for the
documents and ~Rs 2,900-3,300 for the month.

The demo PDFs are mostly one page. Real files have multi-page bank statements and Form-16s, and the
reading cost grows with pages, so expect the document line to be roughly 2-3x higher (est.).

The same month on the full edition costs ~Rs 20,000 before any use.

## Deploy and destroy time

| Action | Full edition | Lean edition |
|---|---|---|
| First deploy | 60-80 min, several 45-min CodeBuild runs | `deploy/lean/deploy.sh`: fewer, faster stacks (no Neptune, NAT, ElastiCache or Fargate); if the 45-min cap stops a build, the script starts the next one itself |
| Code-only update | full CloudFormation update, 30+ min | `deploy.sh --hotswap` (Lambda/Step Functions code in place) |
| Web app only | same as a code update | `update-frontend.sh`, ~1-2 min, no build server |
| Destroy | ~60 min (Neptune, NAT, VPC ENIs, WAF) | `destroy.sh`, stacks in 3 parallel waves |

The lean stack has no VPC, so there are no Lambda network interfaces to wait for on delete, which was
the slowest part of every past teardown. Measured on 1 Oct 2026 (`deploy.sh` output): the build that
finished the fresh lean deploy took 43 min, about 20 min building the Lambda assets and 22 min of
stacks; an earlier build, ended by a since-fixed error, had already created 4 of the 15 stacks. That
is close to the 45-min cap, so a fresh deploy can need a second build, which `deploy.sh` starts itself.
Lean destroy time is not measured yet.

Prices: AWS Price List API for ap-south-1: EC2/Lambda/DynamoDB/API Gateway/S3/KMS/CloudWatch on
30 Sep 2026; checked 1 Oct 2026: Bedrock (AmazonBedrock offer published 30 Sep 2026, the AWS-sold
offer, not Marketplace), Transcribe (`APS3-TranscribeAudio`) and Step Functions (`APS3-StateTransition`),
both published 11 Sep 2026. AWS pricing page for AgentCore. Usage: CloudWatch (AWS/Bedrock,
AWS/Lambda), Step Functions execution history and the facts log lines of the 1 Oct seed run.
