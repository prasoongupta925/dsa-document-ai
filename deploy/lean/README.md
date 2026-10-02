# Lean edition (serverless only)

Same app as the full edition: same UI, document pipeline, agents, file check and eligibility page.
What changes is only what it runs on: no VPC/NAT, no Neptune, no ElastiCache, no Fargate/ALB and,
by default, no WAF. Everything left is pay per use, so the stack costs about $3 a month when idle
(see COST.md).

## Fresh deploy (the normal path)

1. Push the branch. The build clones it from GitHub (`--repo`, default
   `https://github.com/prasoongupta925/dsa-document-ai.git`, branch `main`), not from this working copy.
   Deploying your own fork: `REPO_URL=https://github.com/<you>/dsa-document-ai.git`.
2. Run `ADMIN_USER_EMAIL=you@example.com deploy/lean/deploy.sh`. It creates the CodeBuild project the first time, bootstraps CDK when the region
   has no bootstrap yet and deploys the 15 stacks. The admin user (`ADMIN_USER_EMAIL`) gets its temporary
   password by email.
3. Sign in with the admin user, create users in the Cognito user pool for your team, and upload your own
   (or synthetic) loan files. Use synthetic data for demos.

Some AWS accounts (new ones in particular) stop every CodeBuild build after 45 minutes, and a fresh deploy can
need more than one build.
`deploy.sh` handles that itself. When the cap stops a build, it waits until no stack is mid-operation and starts
the next build with the same settings; stacks that are already deployed are no-ops there. It starts at most
4 builds (`MAX_BUILDS=6 deploy/lean/deploy.sh` for more). For each build it prints the result, the minutes, the
error lines of the log and the stack states, and it exits 0 only when the last build SUCCEEDED. If it stops
early, run it again: finished stacks are skipped.

`deploy.sh` will not start while `deploy/lean/destroy.sh` is running, because the last steps of a destroy delete
the idp-v2 tables, buckets and parameters. It also will not start while another build of the deploy project runs.

## Commands (region `ap-south-1`; set `AWS_PROFILE` to pick a profile)

| What | Command | Needs |
|---|---|---|
| First deploy / full update | `deploy/lean/deploy.sh` | branch pushed to GitHub |
| Code-only update (Lambda, Step Functions, container images) | `deploy/lean/deploy.sh --hotswap` | same |
| Some stacks only | `deploy/lean/deploy.sh --stacks "IDP-V2-Application IDP-V2-Workflow"` | same |
| Web app only | `deploy/lean/update-frontend.sh` (`--no-build` publishes the bundle already built) | local Node 22 + pnpm |
| Destroy everything (keeps the CDK bootstrap) | `deploy/lean/destroy.sh` | - |
| Destroy including CodeBuild project + bootstrap | `deploy/lean/destroy.sh --all` | - |
| CloudFront WAF back on | `deploy/lean/deploy.sh --waf`, on every later deploy too | us-east-1 bootstrap (done by the build) |

A deploy without `--waf` detaches the WAF from CloudFront, but its us-east-1 stack stays (and bills) until
`destroy.sh` removes it.

## Build cache

About 18 of the 19 minutes of CDK bundling in a build go to cargo compiling the three Rust Lambdas (paddle-ocr,
toka, lancedb-service) in the cargo-lambda Docker image. The build keeps cargo's `target/` folders in S3 and puts
them back before `cdk synth`, so cargo compiles only what changed. cargo decides what is stale, as on a laptop.

- Bucket `sample-aws-idp-pipeline-build-cache-<account>-<region>`. `deploy.sh` creates it the first time (public
  access blocked, SSE-S3, HTTPS only) and checks its rule on every run: each object is deleted 7 days after it was
  last written. It holds compiled code, no app data: an estimated 1-2 GB (the first build prints the real sizes),
  a few cents a month. `destroy.sh`, `--all` included, leaves it; without builds it is empty within 7 days, and
  `aws s3 rb s3://<bucket> --force` removes it.
- There is one object per crate. A change to the crate's `Cargo.toml`/`Cargo.lock`, the cargo-lambda image or the
  CPU type gives a new key, so that crate starts clean. When the crate folder is the same git tree as in the cached
  build, its compiled code is reused too; otherwise only that crate is compiled again.
- The build saves the cache right after `cdk synth`, then deploys that synthesized assembly (`cdk deploy --app`), so
  a build the 45-minute cap stops still leaves the cache for the next one.
- Expected: synth ~18 min down to ~2 min, plus ~1 min to restore and save, so a full update takes ~27 min instead
  of ~42. Only the first build after the cache is set up (or after a change above) is cold; in a fresh deploy the
  later builds get ~15 more minutes for CloudFormation. `deploy.sh` prints each build's `rust cache:` and `took` lines.
- `deploy.sh --no-cache` compiles the Rust Lambdas from scratch and replaces the cache. `CACHE_BUCKET= deploy/lean/deploy.sh`
  runs without any cache.
- Not cached, on purpose: `cdk.out` (CDK bundles the Rust and Node.js assets again on every synth because they use
  output hashes; the Python layers it would reuse take ~30 s and would freeze their unpinned pip packages), the pnpm
  store and the uv cache (~30 s together). The CodeBuild project cache stays LOCAL: a build stopped by the cap never
  uploads an S3-mode project cache, and S3 mode would drop the local Docker layer cache.

## What behaves differently from the full edition

- Knowledge graph: switched off. The graph service still answers (empty results), so search, chat and
  the pipeline work unchanged; the graph view is empty.
- Backend: the same FastAPI container runs on Lambda behind the same HTTP API (30 s per request, as before).
- Live updates: WebSocket connections are tracked in a DynamoDB table instead of Valkey.
- Session/agent list cache: off (DynamoDB answers directly).
