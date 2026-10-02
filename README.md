<p align="center">
  <img src="packages/frontend/public/logo.png" alt="DSA Document AI logo" width="96">
</p>

# DSA Document AI

An AI document assistant for Indian loan DSAs (direct selling agents). It checks a loan file
before it goes to a lender, fills the eligibility sheet from the documents and the credit
report, and works out what each lender can offer. It runs serverless on AWS in Mumbai
(ap-south-1) and costs about $3 a month when nobody uses it.

**Live demo (no login, read-only, synthetic data):** {DEMO_URL}

Built by Prasoon Gupta.

> The company in the demo is fictional (see [The demo company](#the-demo-company)). Every
> applicant, document and call in it is synthetic.

## What it does

A DSA's telecallers collect a customer's salary slips, bank statements, PAN, Aadhaar and credit
report, then "log in" the file with a bank or NBFC. A file with a missing month, a PAN typo or a
gross salary typed as net comes back days later. This app catches that before the file leaves
the office:

- **READY / NOT READY file check.** Amazon Bedrock reads every page; fixed rules decide. The
  check compares the file with the lender's checklist (documents, months, statement period) and
  cross-checks PAN, name, employer, declared vs. slip vs. bank-credited salary and declared EMIs
  vs. bank debits. Each finding names the document it comes from; anything the rules cannot
  verify is marked for a person to review.
- **Credit report auto-fill.** The score, enquiries and tradelines of a CIBIL-style credit report,
  with salary, bonus, rent and other income from the other documents, fill the eligibility inputs.
  Every value shows its source document, and the app lists what is still missing.
- **Eligibility per lender, with the FOIR grid.** FOIR-based eligibility for each lender, using
  the DSA's own spreadsheet formulas as code: FOIR by net-salary slab and company category, the
  EMIs to obligate or close, tenure limits and serviceable pincodes. It shows the reasons when a
  lender says no, and warns before a NOT READY file is logged in. The lender policies shipped here
  are SAMPLE data: replace them with your lenders' grids.
- **Branch finder.** The nearest branches of each eligible lender for the applicant's pincode,
  from public data (India Post pincode directory, RBI bank branch list) and the DSA's own lists
  uploaded as CSV.
- **EMI and balance-transfer calculator.**
- **Chat agents.** Built-in agents on Amazon Bedrock AgentCore: a file checker, a customer-facing
  loan assistant (English, Hindi, Marathi), a call QA reviewer and a document-reminder writer for
  WhatsApp and SMS follow-ups. The agents call the file-check and eligibility engine as MCP tools,
  and "Ask about this file" answers from the file's own documents only.
- **Call QA.** Recorded telecaller calls (Indian English, Hindi, Marathi, Hinglish) are
  transcribed with Amazon Transcribe and scored against a review rubric.
- **CRM webhook.** The verdict can be pushed to a CRM through a signed webhook
  (HMAC-SHA256; headers `X-DocAI-Event`, `X-DocAI-Delivery`, `X-DocAI-Signature`).
- **Voice bot (separate service).** An Indian-language voice bot (Amazon Transcribe streaming,
  an LLM on Amazon Bedrock, Amazon Polly) reads the file through this app's API, and its
  recordings go to Call QA. It lives in a separate repository and is not part of this code.
- **Nothing kept longer than 7 days.** Documents, extracted facts, chat sessions, uploads and
  logs are deleted automatically.

## Architecture on AWS

The app is serverless and pay-per-use, and its resources (storage, pipeline, API, agents) are in
ap-south-1 (Mumbai). There is no VPC or NAT gateway, no always-on database and no container
cluster. A few model calls leave the region: search embeddings run in us-east-1 and reranking in
ap-northeast-1 (Mumbai has no Amazon embedding or rerank model), and Nova 2 Lite runs through
Bedrock's global cross-region inference profile. The per-region settings are in
[`packages/common/constructs/src/core/region-config.ts`](packages/common/constructs/src/core/region-config.ts).

```
Browser ─► CloudFront + S3 (React web app), Amazon Cognito sign-in
        ─► API Gateway HTTP API (IAM auth) ─► FastAPI backend on AWS Lambda ─► DynamoDB, S3

Upload ─► S3 ─► EventBridge / SQS ─► Step Functions pipeline
          ├─ OCR: PaddleOCR on Lambda (or a SageMaker endpoint that scales to zero)
          ├─ page reading, summaries and document facts on Amazon Bedrock
          ├─ facts grounded against the printed text ─► DynamoDB
          └─ embeddings ─► LanceDB on S3 (hybrid search)

Chat ─► Amazon Bedrock AgentCore Runtime (Strands agents)
        ─► AgentCore Gateway (MCP): file check + eligibility engine (Lambda), search, artifacts
Call recordings ─► Amazon Transcribe ─► call QA
Live progress ─► API Gateway WebSocket (connections in DynamoDB)
CRM webhook ─► queue ─► signed delivery (secrets encrypted with a dedicated KMS key)
Retention ─► daily sweepers + S3 lifecycle rules + log retention, 7 days
```

- **AWS CDK, 15 stacks:** Storage, Event, Ocr, Bda, Transcribe, Workflow, Websocket, Worker,
  Mcp, Agent, LanceService, Webhook, Webcrawler, Application, Retention. AWS CodeBuild deploys
  them (see [Deploy](#deploy)).
- **Models (Amazon Bedrock, AWS-sold models only):** Amazon Nova 2 Lite reads pages and answers
  "Ask about this file"; OpenAI gpt-oss-120b extracts document facts; Google Gemma 3 12B writes
  page descriptions and summaries; Amazon Nova multimodal embeddings power search; chat uses GLM-5
  by default, with Nova 2 Lite and DeepSeek V3.2 selectable. The roles are set in
  [`packages/infra/src/models.json`](packages/infra/src/models.json).
- **"AI reads, rules decide":** the model extracts; a deterministic engine
  ([`packages/lambda/file-check-mcp`](packages/lambda/file-check-mcp)) gives the verdict and the
  eligibility numbers, so the same extracted facts always get the same verdict.

## Cost

Measured and priced for ap-south-1; details in [`deploy/lean/COST.md`](deploy/lean/COST.md).

- **Idle:** about $3 a month, mostly two customer-managed KMS keys. Cognito, CloudFront and
  Lambda stay in the always-free tier for a small team.
- **Per document:** $0.013 measured with Nova 2 Lite for every step; about $0.009 estimated with
  the cheaper models configured now, and about $0.0045 on the Bedrock Flex tier.
- **Example month for one DSA branch** (500 loan files of 7 documents, 2,000 questions, 300 call
  minutes): about Rs 4,300-4,700.

## Deploy

You need an AWS account, the AWS CLI v2 with admin credentials (`AWS_PROFILE` or the default
credential chain) and Amazon Bedrock access in ap-south-1 to the models in `models.json`. The
build runs in AWS CodeBuild and clones the repository from GitHub, so push your branch first (a
fork: set `REPO_URL`).

```sh
git clone https://github.com/prasoongupta925/dsa-document-ai.git
cd dsa-document-ai

ADMIN_USER_EMAIL=you@example.com deploy/lean/deploy.sh   # first deploy: about 40 minutes
deploy/lean/deploy.sh --admin-email you@example.com --hotswap  # code-only update
deploy/lean/update-frontend.sh                           # web app only, about 2 minutes
deploy/lean/destroy.sh                                   # remove everything (--all: also CodeBuild and the CDK bootstrap)
```

The admin user gets a temporary password by email. [`deploy/lean/README.md`](deploy/lean/README.md)
covers the build cache, the optional CloudFront WAF and accounts that stop CodeBuild builds after
45 minutes (`deploy.sh` then chains builds on its own).

Run the tests locally (Python 3.13 with uv, Node 22 with pnpm):

```sh
pnpm install
cd packages/backend && uv run pytest tests/ -q                                  # backend: 1,039 tests
cd packages/lambda/file-check-mcp && uv run --with pytest pytest -q             # file check and eligibility engine: 212
cd packages/infra/src/functions/step-functions/document-facts && uv run --with pytest pytest -q   # document facts: 279
cd packages/frontend && pnpm vitest run && pnpm tsc --noEmit -p tsconfig.app.json   # web app: 349
```

## How it was built

The coding agent was **Claude Code**, working in a terminal with the AWS CLI (a dedicated IAM
user with an explicit deny on Marketplace-billed models) and the **Agent Toolkit for AWS**
(`aws configure agent-toolkit`), which connects the agent to the AWS MCP Server and AWS skills
for documentation, pricing and service guidance. With it, the work went:

1. **Start from the AWS sample.** [`sample-aws-idp-pipeline`](https://github.com/aws-samples/sample-aws-idp-pipeline)
   provides the document pipeline, search, agents and web app.
2. **Re-architect for a small business.** A serverless-only "lean" edition: no VPC/NAT, graph
   database, cache cluster or Fargate. Idle cost went from about $7.6 a day to about $3 a month.
3. **Build the DSA features.** The rules engine, the eligibility calculator with the FOIR grid,
   credit report auto-fill, the branch finder, the calculators, the agents and their MCP tools,
   call QA and the CRM webhook, each with tests.
4. **Ship and measure.** Deploys with AWS CDK through CodeBuild, cost per document measured from
   CloudWatch, Mumbai model prices from the AWS Price List API.

## The demo company

The demo uses fictional names. **Varunika Loan Partners** is a loan DSA near Mumbai (Thane), with
the consumer brand **Varunika Loans**. **CallVarta CRM** is a fictional telecalling CRM. The
handler and reviewer personas (Asha Verma, Rohan Iyer), the applicants, documents and calls are
made up. Lender names appear only in SAMPLE policy and branch data. Nothing here is a lender's
real policy, and no lender has reviewed it.

Reference data: the India Post pincode directory (Government Open Data License - India) and the
RBI bank branch list via [razorpay/ifsc](https://github.com/razorpay/ifsc) (public domain); see
[`packages/backend/app/data/branches/README.md`](packages/backend/app/data/branches/README.md).

## Repository layout

| Path | What |
|---|---|
| `packages/frontend` | React web app (Vite, TanStack Router, Tailwind) |
| `packages/backend` | FastAPI backend (runs on Lambda) |
| `packages/lambda/file-check-mcp` | File check, eligibility and loan tools (MCP tools for the agents) |
| `packages/infra` | AWS CDK stacks, Step Functions steps, built-in agent prompts |
| `packages/agents` | Chat agents (Strands Agents on AgentCore Runtime) |
| `deploy/lean` | Deploy, update and destroy scripts; cost notes |
| `docs`, `README.upstream.md` | Documentation of the AWS sample this builds on |

## License

Built on the AWS sample [sample-aws-idp-pipeline](https://github.com/aws-samples/sample-aws-idp-pipeline)
(Amazon Software License). This repository keeps the sample's [LICENSE](LICENSE): the Amazon
Software License, which allows use only with services provided by Amazon Web Services. The
sample's own README is kept as [README.upstream.md](README.upstream.md).

Not legal or credit advice, and not a compliance claim.
