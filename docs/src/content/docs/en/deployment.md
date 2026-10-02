---
title: "Quick Deploy Guide"
description: "One-click Deployment with CloudShell + CodeBuild"
---

## Overview

Deploy the entire IDP pipeline automatically by running a single script (`deploy.sh`) in AWS CloudShell. The script creates a CodeBuild project via CloudFormation, and CodeBuild deploys all 12 CDK stacks sequentially.

```
Run deploy.sh (CloudShell)
  → Create CloudFormation Stack
    → Provision CodeBuild Project
      → CDK Bootstrap
        → Deploy all stacks in parallel (concurrency=4)
          → Create Cognito admin user
```

---

## Prerequisites

### Region Selection

Deployment is recommended in the following regions:

| Region | Notes |
|--------|-------|
| **us-east-1** (N. Virginia) | All models supported |
| **us-west-2** (Oregon) | All models supported |
| **ap-south-1** (Mumbai) | Supported with the per-Region defaults below. No AgentCore Web Search tool and no voice (Nova Sonic) |

### Deploying Outside us-east-1

Region-specific settings are chosen per deploy Region
(`packages/common/constructs/src/core/region-config.ts`). Each one can be
overridden with CDK context, which `deploy.sh`/`destroy.sh` pass through with
`--context KEY=VALUE` (repeatable).

| Context key | ap-south-1 default | Purpose |
|-------------|--------------------|---------|
| `lancedbExpressAzId` | `aps1-az1` (`aps1-az3` also works) | Availability Zone ID of the S3 Express One Zone bucket for LanceDB. Must be a zone ID of the deploy Region; synth fails otherwise. Required for Regions without a default (us-east-1 uses `use1-az4`) |
| `embeddingRegion` | `us-east-1` | Region for Nova multimodal embeddings, which ap-south-1 does not offer. Chunk text is sent there for embedding; nothing is stored there |
| `rerankRegion` | `ap-northeast-1` | Region for Amazon Rerank. If rerank fails, search falls back to the hybrid order |
| `voiceModelRegion` | `ap-south-1` | Region for Nova Sonic. It is not offered in ap-south-1, so voice is off unless you set, for example, `ap-northeast-1` |
| `enableWebSearch` | `false` | Create the AgentCore Web Search gateway target. The default is `true` only in us-east-1, eu-west-1 and ap-northeast-1, where the tool is offered |
| `retentionDays` | `7` | Maximum age of client data, logs and queues |
| `securityLogRetentionDays` | `retentionDays` (`7`) | Retention for security logs (access, audit and flow logs). Read by `getSecurityLogRetentionDays()` in `retention-config.ts`; no log group uses it yet, so it changes nothing today. See [Retention and security logs](#retention-and-security-logs) |

Also note:

- Named S3 buckets end in `-<account>-<region>` (for example `idp-v2-document-storage-<account>-ap-south-1`), so names cannot collide across Regions.
- The CloudFront WAF stack is always deployed to us-east-1. The CodeBuild deploy bootstraps CDK in us-east-1 as well as in the deploy Region.
- `deploy.sh` and `destroy.sh` download their CodeBuild template from the repository given with `--repo-url` (at `--version`), so a fork uses its own templates.

```bash
./deploy.sh --admin-email user@example.com \
  --repo-url https://github.com/<owner>/sample-aws-idp-pipeline.git \
  --context lancedbExpressAzId=aps1-az3
```

### Retention and security logs

- Nothing is kept longer than `retentionDays` (default 7): documents and
  their analysis, chat sessions, artifacts, queues and every CloudWatch log
  group (the RetentionStack sweeper and log-retention enforcer apply it).
- The backend DynamoDB table has TTL on the attribute `expires_at` (epoch
  seconds). The file-check Ask usage ledger (`PROJ#<project>` /
  `FCASK#<timestamp>#<id>`: one item per question with the model, tokens and
  cost, never the question or the answer) sets it to the call time plus
  `retentionDays`, so those items delete themselves.
  Items without `expires_at` never expire. TTL deletion is asynchronous
  (usually within a few days of expiry); `GET .../file-check/usage` counts
  only the last 7 days whatever TTL has removed. The CRM webhook delivery log
  (`WHDLV#<timestamp>#<id>`, below) expires the same way.
- Production in India: the Digital Personal Data Protection Rules, 2025,
  Rule 6(1)(e), require a Data Fiduciary to keep the logs used to detect,
  investigate and remediate unauthorised access, and the personal data they
  hold, for one year unless another law requires otherwise. The demo keeps
  7 days. Before production, set `--context securityLogRetentionDays=365`,
  apply `toLogRetention(getSecurityLogRetentionDays(this))` to the security
  log groups (API access logs, CloudFront/S3 access logs) and
  exempt those groups from the log-retention enforcer, which otherwise caps
  them back at `retentionDays`. Client data keeps `retentionDays`.

### CRM webhook

A project can push its loan-file verdict to a CRM (for example CallVarta CRM)
each time a document finishes analysis. Stack `IDP-V2-Webhook` holds the
delivery Lambda `idp-v2-webhook-delivery` (outside the VPC, so it has no path
to private resources); deploy it with the rest (`--all`) or before
`IDP-V2-Workflow` and `IDP-V2-Application`, which read its ARN from SSM.

- Backend API, per project (`/projects/{id}/integrations`):
  `PUT /webhook` `{"url": "https://crm.example.com/hooks/idp", "enabled": true}`
  (https only, at most 2048 characters, no credentials, public hosts only;
  `enabled` needs a URL and a secret), `POST /webhook/secret` (a new signing
  secret, shown only in this response), `POST /webhook/test` (a signed `test`
  event with empty `results`, also while disabled) and `GET /webhook`
  (settings and the last 20 deliveries).
- When a document completes, the workflow finalizer invokes the Lambda
  asynchronously if the webhook is enabled; a webhook problem never fails the
  workflow. The Lambda runs the file check (default checklist) and POSTs
  `{event: "file_check.completed", delivery_id, project_id, document_id, at,
  results: [{applicant, verdict, summary, missing, checklist_id}]}` for the
  applicant(s) of that document (every applicant when the check cannot tell;
  one entry with `applicant: null` when there is none).
- Headers: `X-DocAI-Event`, `X-DocAI-Delivery` (retries reuse it:
  deduplicate on it) and `X-DocAI-Signature: t=<unix>,v1=<hex>`, the
  HMAC-SHA256 of `<t>.<raw body>` keyed with the secret. The receiver verifies
  it over the raw body in constant time, rejects timestamps more than 5
  minutes off and answers 2xx. 5xx and network errors are retried (3 attempts,
  5 s timeout each); 4xx and redirects are not.
- At send time the URL is checked again and every address the host resolves to
  must be public; the connection goes to those addresses only.
- Delivery log items (`PROJ#<project>` / `WHDLV#<timestamp>#<id>`: status, HTTP
  status, error and applicant names, never the payload or the secret) expire
  after `retentionDays`. The URL and secret are project settings (the project's
  `META` item) and are deleted with the project.

---

## Deployment Steps

### Step 1. Open CloudShell

Click the CloudShell icon at the top of the AWS Console, or search for "CloudShell" in the search bar.

![CloudShell](../assets/quick-deploy-cloudshell.png)

### Step 2. Run the Deploy Script

```bash
git clone https://github.com/aws-samples/sample-aws-idp-pipeline.git
cd sample-aws-idp-pipeline
chmod +x ./deploy.sh
./deploy.sh
```

### Step 3. Enter Admin Email

When prompted, enter the email address for the admin account. A Cognito user will be created with this email.

```
===========================================================================
  Sample AWS IDP Pipeline - Automated Deployment
---------------------------------------------------------------------------
  Deploys the full IDP pipeline via CodeBuild.

  Stacks: Storage, Event, Bda, Ocr, Transcribe, Workflow,
          Websocket, Worker, Mcp, Agent, Application
===========================================================================

Enter admin user email address: your-email@example.com
```

### Step 4. Confirm and Start Deployment

Review the configuration and enter `y` to start deployment.

```
Configuration:
--------------
Admin Email: your-email@example.com
Repository:  https://github.com/aws-samples/sample-aws-idp-pipeline.git
Version:     main
Stack Name:  sample-aws-idp-pipeline-codebuild

Do you want to proceed with deployment? (y/N): y
```

The following steps will execute automatically:

1. Download and validate CloudFormation template
2. Create CodeBuild project (CloudFormation)
3. Start CodeBuild build
4. CDK Bootstrap (first time only)
5. Deploy 12 stacks sequentially/in parallel
6. Create Cognito admin user

---

## Monitoring Deployment

### From CloudShell

The script displays CodeBuild build progress in real-time.

```
Starting CodeBuild: sample-aws-idp-pipeline-deploy ...
Build ID: sample-aws-idp-pipeline-deploy:xxxxxxxx

You can monitor progress in the AWS Console:
  CodeBuild > Build projects > sample-aws-idp-pipeline-deploy

Phase: BUILD
```

### From CodeBuild Console

For detailed logs, check the CodeBuild project directly in the AWS Console.

> **AWS Console** > **CodeBuild** > **Build projects** > **sample-aws-idp-pipeline-deploy**

![CodeBuild Console](../assets/quick-deploy-codebuild.png)

### CodeBuild Build Phases

| Phase | Description | Estimated Time |
|-------|-------------|----------------|
| INSTALL | Node.js 22, Python 3.13, pnpm, CDK, Docker QEMU setup | 2-3 min |
| PRE_BUILD | Source clone, dependency install (`pnpm install`) | 3-5 min |
| BUILD | Lint, Compile, Test, Bundle + CDK deploy (12 stacks) | 30-50 min |
| POST_BUILD | Create Cognito admin user, output URLs | 1 min |

---

## After Deployment

When deployment completes successfully, the following information is displayed.

```
===========================================================================
  Deployment Successful
===========================================================================

  Application URL: https://dxxxxxxxxxx.cloudfront.net

  Login Credentials:
     Email:              your-email@example.com
     Temporary Password: TempPass123!

  Next Steps:
     1. Access the application using the URL above
     2. Log in with the credentials
     3. Change your password when prompted

  To destroy all resources:
     aws cloudformation delete-stack --stack-name sample-aws-idp-pipeline-codebuild

===========================================================================
```

### Access and Login

1. Navigate to the **Application URL**
2. Your username is the portion before `@` in your email (e.g., `your-email@example.com` → `your-email`)
3. Log in with the temporary password `TempPass123!`
4. You will be prompted to change your password on first login

![Login Screen](../assets/quick-deploy-login.png)

---

## Advanced Options

### Command-line Options

```bash
./deploy.sh [OPTIONS]

Options:
  --admin-email EMAIL   Admin email (skip interactive prompt)
  --repo-url URL        Repository URL (default: github.com/aws-samples/...)
  --version VERSION     Branch or tag to deploy (default: main)
  --stack-name NAME     CloudFormation stack name (default: sample-aws-idp-pipeline-codebuild)
  --stacks STACKS       Deploy specific CDK stacks only
  --template-url-base URL  Raw base URL of the CodeBuild template (default: derived from --repo-url)
  --context KEY=VALUE   CDK context for the build, repeatable (see "Deploying Outside us-east-1")
  --info                Show deployed application URL
  --help                Show help message
```

### Check Deployment URL

```bash
./deploy.sh --info
```

### Deploy a Specific Version

```bash
./deploy.sh --admin-email user@example.com --version v1.0.0
```

---

## Cleanup

### Run destroy.sh

To delete all deployed resources, run the `destroy.sh` script. Similar to deployment, it uses CodeBuild to delete all 12 stacks in reverse order.

```bash
cd sample-aws-idp-pipeline
chmod +x ./destroy.sh
./destroy.sh
```

```
===========================================================================
  Sample AWS IDP Pipeline - Automated Destroy
---------------------------------------------------------------------------
  Destroys all IDP pipeline resources via CodeBuild.

  Stacks: Application, Agent, Mcp, Worker, Websocket, Workflow,
          Transcribe, Bda, Ocr, Event, Storage
===========================================================================

WARNING: This will permanently delete all IDP pipeline resources.

Do you want to proceed with destroy? (y/N): y
```

Once the destroy completes, the destroy CodeBuild stack is automatically cleaned up.

### If Deletion Fails

Some resources may fail to delete (e.g., S3 buckets with remaining data, ENIs still in use, etc.). In this case:

1. Go to **AWS Console** > **CloudFormation** and check for stacks in `DELETE_FAILED` status
2. Review the **Events** tab of the failed stack to identify the cause
3. Manually delete the problematic resources, then retry deleting the stack from CloudFormation

:::note
The `CDKToolkit` stack is preserved for future deployments. To fully remove it, run `aws cloudformation delete-stack --stack-name CDKToolkit`.
:::

---

## Troubleshooting

### CloudShell Session Timeout

CloudShell sessions terminate after 20 minutes of inactivity. Once CodeBuild starts, the build continues even if the CloudShell session ends. Check progress in the CodeBuild Console.

### CodeBuild Build Failure

Check the build logs:

```bash
# View recent build logs
aws logs tail /aws/codebuild/sample-aws-idp-pipeline-deploy --since 10m
```

### Common Failure Causes

| Cause | Solution |
|-------|----------|
| Bedrock model access not enabled | Enable required models in Bedrock Console |
| Service quota exceeded | Request quota increase via AWS Support |
| CDK Bootstrap failed | `aws cloudformation delete-stack --stack-name CDKToolkit` then redeploy |

---

## Deployment Architecture

```
CloudShell
  │
  ├─ deploy.sh
  │   ├─ Download CloudFormation Template (deploy-codebuild.yml)
  │   ├─ Create CloudFormation Stack
  │   │   └─ CodeBuild Project (IAM Role: PowerUserAccess + IAM)
  │   └─ Start CodeBuild Build
  │
  └─ CodeBuild (BUILD_GENERAL1_LARGE, amazonlinux 5.0)
      ├─ INSTALL:    Node.js 22, Python 3.13, pnpm, CDK, Docker QEMU (ARM64)
      ├─ PRE_BUILD:  git clone → pnpm install
      ├─ BUILD:      lint + test + bundle → CDK deploy (12 stacks)
      └─ POST_BUILD: Cognito admin user creation
```
