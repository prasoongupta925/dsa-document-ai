# Public demo hosting (read-only, no login)

Hosts a **read-only public demo** of the web app on its own CloudFront URL. It is a static build of
`packages/frontend` made with `VITE_PUBLIC_DEMO=1`:

- no sign-in: a fixed demo user, "Asha Verma" (a made-up persona);
- every API call is answered in the browser from snapshots of the real app running on **synthetic data**
  (`packages/frontend/src/demo/`); the file check, "Ask about this file", the lender check and the chat agents
  replay real recorded answers;
- uploads, edits, deletes, erasing and the CRM webhook test are switched off, and a banner says so.

The company in the demo, Varunika Loan Partners, is fictional. A normal build (`VITE_PUBLIC_DEMO` unset) is not
affected: the demo code and its snapshot are only loaded when the flag is `1`.

## Build and publish

```bash
pnpm install
cd packages/frontend
VITE_PUBLIC_DEMO=1 pnpm exec vite build            # writes dist/packages/frontend at the repo root
cd ../..
AWS_PROFILE=<profile> deploy/public-demo/deploy.sh dist/packages/frontend [stack name] [region]
# optional: fill the voice bot video link of the build
VOICE_VIDEO_URL=https://... AWS_PROFILE=<profile> deploy/public-demo/deploy.sh dist/packages/frontend
# remove everything (empties the bucket, deletes the stack)
AWS_PROFILE=<profile> deploy/public-demo/destroy.sh [stack name] [region]
```

Defaults: stack `dsa-docai-public-demo`, region `ap-south-1`. Nothing account-specific is hard-coded: the bucket
and distribution come from the stack's outputs.

`deploy.sh` creates or updates the stack, then uploads the build:

- hashed assets cached for a year, `index.html` never cached and uploaded last;
- **files over 10,000,000 bytes are uploaded pre-gzipped** with `Content-Encoding: gzip`. CloudFront compresses
  only objects up to 10 MB, and the main JS bundle is about 10.4 MB;
- old files removed, then the CDN invalidated.

## What the stack creates (`template.yaml`)

AWS-billed services only (S3, CloudFront), no domain, nothing from Marketplace:

- a private S3 bucket: all public access blocked, SSE-S3, TLS-only bucket policy, old object versions and
  unfinished uploads removed after 1 day;
- a CloudFront distribution with Origin Access Control: default root object `index.html`, SPA fallback to
  `index.html` for deep links, HTTPS only (plain HTTP is redirected), `PriceClass_200`, security headers
  (HSTS, `nosniff`, `DENY` framing, a CSP with `connect-src 'self'`, so the page cannot call any backend).

Cost: a few MB in S3 plus CloudFront requests and transfer, a few rupees a month at demo traffic. The site has no
expiry of its own; run `destroy.sh` when the demo is over.
