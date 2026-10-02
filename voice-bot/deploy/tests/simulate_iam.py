#!/usr/bin/env python3
"""Checks the server role's permissions with IAM's policy simulator (read-only: simulate-custom-policy and
get-policy-version; nothing is created). The role's inline policy is rendered from template.yaml with this
account's id and evaluated together with AWS's AmazonSSMManagedInstanceCore, as on the real role.

  AWS_PROFILE=<profile> python3 deploy/tests/simulate_iam.py

The model cases use real inference-profile ids of ap-south-1 (aws bedrock list-inference-profiles): Mumbai only
means both chain models are allowed in-Region and every global./apac./in. profile is refused explicitly.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_template import CfnLoader, to_long_form  # noqa: E402

REGION = "ap-south-1"


def aws(*args):
    out = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(out.stderr.strip())
    return json.loads(out.stdout or "{}")


ACCOUNT = aws("sts", "get-caller-identity")["Account"]
API = "h4jtkssfbb"
BUCKET_ARN = "arn:aws:s3:::docai-voice-codebucket-test"
SUBS = {"AWS::Partition": "aws", "AWS::Region": REGION, "AWS::AccountId": ACCOUNT, "IdpApiId": API,
        "CodeBucket.Arn": BUCKET_ARN}


def render(x):
    if isinstance(x, dict):
        if "Fn::Sub" in x:
            return re.sub(r"\$\{([^}]+)\}", lambda m: SUBS[m.group(1)], x["Fn::Sub"])
        if "Fn::If" in x:
            raise SystemExit(f"the role policy has a condition ({x['Fn::If'][0]}): add it to this simulator")
        if "Ref" in x:
            return SUBS[x["Ref"]]
        return {k: render(v) for k, v in x.items()}
    if isinstance(x, list):
        return [render(v) for v in x]
    return x


template = to_long_form(yaml.load((Path(__file__).resolve().parents[1] / "template.yaml").read_text(), Loader=CfnLoader))
inline = render(template["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"])
managed_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
version = aws("iam", "get-policy", "--policy-arn", managed_arn)["Policy"]["DefaultVersionId"]
managed = aws("iam", "get-policy-version", "--policy-arn", managed_arn, "--version-id", version)["PolicyVersion"]["Document"]

FM = f"arn:aws:bedrock:{REGION}::foundation-model"
IP = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile"
EXEC = f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{API}/$default"
P = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter"
IN_MUMBAI = [{"ContextKeyName": "aws:RequestedRegion", "ContextKeyValues": [REGION], "ContextKeyType": "string"}]
ELSEWHERE = [{"ContextKeyName": "aws:RequestedRegion", "ContextKeyValues": ["us-east-1"], "ContextKeyType": "string"}]
CASES = [  # (action, resource, context, expected)
    # the chain, in-Region
    ("bedrock:InvokeModelWithResponseStream", f"{FM}/moonshotai.kimi-k2.5", None, "allowed"),
    ("bedrock:InvokeModel", f"{FM}/moonshotai.kimi-k2.5", None, "allowed"),
    ("bedrock:InvokeModel", f"{FM}/openai.gpt-oss-120b-1:0", None, "allowed"),
    ("bedrock:InvokeModelWithResponseStream", f"{FM}/openai.gpt-oss-120b-1:0", None, "allowed"),
    # Mumbai only: every cross-Region profile is refused, even for an AWS-sold model
    ("bedrock:InvokeModelWithResponseStream", f"{IP}/global.amazon.nova-2-lite-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModelWithResponseStream", f"{IP}/global.moonshotai.kimi-k3", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{IP}/apac.amazon.nova-lite-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{IP}/in.openai.gpt-5.6-terra", None, "explicitDeny"),
    ("bedrock:InvokeModelWithResponseStream", f"{IP}/in.anthropic.claude-haiku-4-5-20251001-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", "arn:aws:bedrock:ap-south-2::foundation-model/moonshotai.kimi-k2.5", None, "explicitDeny"),
    ("bedrock:InvokeModel", "arn:aws:bedrock:us-east-1::foundation-model/moonshotai.kimi-k2.5", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:application-inference-profile/abc123", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:provisioned-model/abc123", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:imported-model/abc123", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"arn:aws:bedrock::{ACCOUNT}:system-tool/web-search", None, "explicitDeny"),
    # in-Region models outside the chain: not allowed (no explicit deny needed)
    ("bedrock:InvokeModel", f"{FM}/amazon.nova-2-lite-v1:0", None, "implicitDeny"),
    ("bedrock:InvokeModel", f"{FM}/meta.llama3-70b-instruct-v1:0", None, "implicitDeny"),
    # card-billed (Marketplace) models: explicit deny, in-Region too
    ("bedrock:InvokeModel", f"{FM}/anthropic.claude-sonnet-4-5-20250929-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModelWithResponseStream", f"{IP}/global.anthropic.claude-sonnet-4-5-20250929-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{FM}/cohere.command-r-plus-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{FM}/openai.gpt-5-mini", None, "explicitDeny"),
    # Nova Sonic's bidirectional stream is authorised as bedrock:InvokeModel on the model
    ("bedrock:InvokeModel", "arn:aws:bedrock:ap-northeast-1::foundation-model/amazon.nova-2-sonic-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{FM}/amazon.nova-sonic-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{FM}/amazon.nova-2-sonic-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"{IP}/apac.amazon.nova-sonic-v1:0", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:marketplace/model-endpoint/all-access", None, "explicitDeny"),
    ("bedrock:InvokeModel", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:default-prompt-router/anthropic.claude:1", None, "explicitDeny"),
    ("bedrock-mantle:CreateInference", f"arn:aws:bedrock-mantle:{REGION}:{ACCOUNT}:project/default", None, "explicitDeny"),
    ("bedrock:CallWithBearerToken", "*", None, "explicitDeny"),
    ("aws-marketplace:Subscribe", "*", None, "explicitDeny"),
    ("transcribe:StartStreamTranscriptionWebSocket", "*", IN_MUMBAI, "allowed"),
    ("transcribe:StartStreamTranscriptionWebSocket", "*", ELSEWHERE, "implicitDeny"),
    ("polly:SynthesizeSpeech", "*", IN_MUMBAI, "allowed"),
    ("polly:SynthesizeSpeech", "*", ELSEWHERE, "implicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/GET/projects", None, "allowed"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/file-check", None, "allowed"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/eligibility/calculate", None, "allowed"),
    ("execute-api:Invoke", f"{EXEC}/GET/projects/p1/eligibility/inputs", None, "allowed"),   # caller check (DOB)
    ("execute-api:Invoke", f"{EXEC}/PUT/projects/p1/eligibility/inputs", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/documents", None, "allowed"),
    ("execute-api:Invoke", f"{EXEC}/PUT/projects/p1/documents/d1/status", None, "allowed"),
    ("execute-api:Invoke", f"{EXEC}/DELETE/projects/p1", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/applicants/erase", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/eligibility/login", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/PUT/projects/p1/integrations/webhook", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects", None, "explicitDeny"),                          # create project
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/agents", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/PUT/projects/p1/agents/a1", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/file-check/ask", None, "explicitDeny"),         # model cost
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/graph/rebuild", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/projects/p1/datasets/d1/query", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/documents/d1/workflows/w1/reanalyze", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/PATCH/chat/projects/p1/sessions/s1", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/PUT/prompts/voice-system", None, "explicitDeny"),
    ("execute-api:Invoke", f"{EXEC}/POST/sagemaker/start", None, "explicitDeny"),
    ("execute-api:Invoke", f"arn:aws:execute-api:{REGION}:{ACCOUNT}:otherapi01/$default/GET/projects", None, "implicitDeny"),
    ("s3:GetObject", f"{BUCKET_ARN}/releases/20261001T000000Z-abc.zip", None, "allowed"),
    ("s3:GetObject", f"{BUCKET_ARN}/public/offline.html", None, "implicitDeny"),
    ("s3:PutObject", f"{BUCKET_ARN}/releases/x.zip", None, "implicitDeny"),
    ("ssm:GetParametersByPath", f"{P}/docai-voice", None, "allowed"),
    ("ssm:GetParameter", f"{P}/docai-voice/secrets/PLIVO_AUTH_TOKEN", None, "allowed"),
    ("ssm:GetParameter", f"{P}/idp-v2/backend/url", None, "explicitDeny"),        # managed policy allows *, we deny
    ("ssm:GetParameters", f"arn:aws:ssm:us-east-1:{ACCOUNT}:parameter/docai-voice/x", None, "explicitDeny"),
    ("ssm:PutParameter", f"{P}/docai-voice/secrets/X", None, "implicitDeny"),
    ("ssm:UpdateInstanceInformation", "*", None, "allowed"),                        # Session Manager / Run Command
    ("secretsmanager:GetSecretValue", "*", None, "implicitDeny"),
    ("iam:PassRole", "*", None, "implicitDeny"),
]

def decide(case):
    action, resource, context, _ = case
    args = ["iam", "simulate-custom-policy", "--policy-input-list", json.dumps(inline), json.dumps(managed),
            "--action-names", action, "--resource-arns", resource]
    if context:
        args += ["--context-entries", json.dumps(context)]
    return aws(*args)["EvaluationResults"][0]["EvalDecision"]


from concurrent.futures import ThreadPoolExecutor  # noqa: E402

with ThreadPoolExecutor(max_workers=8) as pool:      # one CLI call per case; IAM is slow from India
    decisions = list(pool.map(decide, CASES))
fails = 0
for (action, resource, context, expected), got in zip(CASES, decisions):
    ok = got == expected
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {got:<13} {action} {resource.split(':', 5)[-1][:70]}"
          + (f" ({context[0]['ContextKeyValues'][0]})" if context else ""))
print(f"IAM simulation (Mumbai only): {len(CASES) - fails} as expected, {fails} not")
sys.exit(1 if fails else 0)
