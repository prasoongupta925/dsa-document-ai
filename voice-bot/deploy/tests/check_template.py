#!/usr/bin/env python3
"""Offline checks for deploy/template.yaml that `aws cloudformation validate-template` does not do.

  python3 deploy/tests/check_template.py [template.yaml]

1. YAML parse with the CloudFormation short tags (!Ref, !Sub, !GetAtt, ...).
2. Every resource property against the CloudFormation registry schema of its type: unknown property names,
   missing required properties, literal values outside an enum. Schemas are cached in
   $SCHEMA_CACHE (default ~/.cache/docai-voice/cfn-schemas); a missing one is fetched with the read-only
   `aws cloudformation describe-type` call.
3. Every Ref / GetAtt / ${...} in Sub points at a parameter, resource or pseudo parameter; every Condition exists.
4. The EC2 user-data script (Fn::Sub with dummy values) passes `bash -n`.
5. Mumbai only: LLM_MODEL_CHAIN has no global./apac./in. profile, the role may invoke exactly those models as
   ap-south-1 foundation models, and a Deny with NotResource refuses every other model ARN.
Exit code 0 = no problems.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TEMPLATE = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "template.yaml"
CACHE = Path(os.environ.get("SCHEMA_CACHE", Path.home() / ".cache/docai-voice/cfn-schemas"))
PSEUDO = {"AWS::AccountId", "AWS::Region", "AWS::StackName", "AWS::StackId", "AWS::Partition",
          "AWS::URLSuffix", "AWS::NoValue", "AWS::NotificationARNs"}
# GetAtt names served by CloudFormation itself that some registry schemas do not list as properties
LEGACY_ATTRS = {"AWS::S3::Bucket": {"Arn", "RegionalDomainName", "DomainName", "WebsiteURL", "DualStackDomainName"},
                "AWS::EC2::EIP": {"PublicIp", "AllocationId"},
                "AWS::EC2::SecurityGroup": {"GroupId", "VpcId"},
                "AWS::CloudFront::Distribution": {"DomainName", "Id"},
                "AWS::CloudFront::OriginAccessControl": {"Id"},
                "AWS::IAM::Role": {"Arn", "RoleId"}}

# Registry schemas of some types (CloudFront) list valid values only in prose: the ones this template uses.
PROSE_ENUMS = {
    "OriginProtocolPolicy": {"http-only", "match-viewer", "https-only"},
    "ViewerProtocolPolicy": {"allow-all", "redirect-to-https", "https-only"},
    "PriceClass": {"PriceClass_100", "PriceClass_200", "PriceClass_All", "None"},
    "HttpVersion": {"http1.1", "http2", "http3", "http2and3"},
    "RestrictionType": {"blacklist", "whitelist", "none"},
    "AllowedMethods": {"GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"},
    "CachedMethods": {"GET", "HEAD", "OPTIONS"},
    "SigningBehavior": {"never", "always", "no-override"},
    "SigningProtocol": {"sigv4"},
    "OriginAccessControlOriginType": {"s3", "mediastore", "lambda", "mediapackagev2"},
    "SSEAlgorithm": {"aws:kms", "AES256", "aws:kms:dsse"},
    "ObjectOwnership": {"ObjectWriter", "BucketOwnerPreferred", "BucketOwnerEnforced"},
}

problems: list[str] = []


class Tag:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def __repr__(self):
        return f"!{self.name} {self.value!r}"


def _construct(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return Tag(suffix, value)


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor("!", _construct)


def to_long_form(x):
    """!Ref a -> {'Ref': a}, !GetAtt a.b -> {'Fn::GetAtt': [a, b]}, other !X -> {'Fn::X': ...}."""
    if isinstance(x, Tag):
        v = to_long_form(x.value)
        if x.name == "Ref":
            return {"Ref": v}
        if x.name == "GetAtt" and isinstance(v, str):
            return {"Fn::GetAtt": v.split(".", 1)}
        if x.name == "Condition":
            return {"Condition": v}
        return {f"Fn::{x.name}": v}
    if isinstance(x, dict):
        return {k: to_long_form(v) for k, v in x.items()}
    if isinstance(x, list):
        return [to_long_form(v) for v in x]
    return x


def schema_for(type_name: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (type_name.replace("::", "__") + ".json")
    if not f.exists():
        out = subprocess.run(["aws", "cloudformation", "describe-type", "--type", "RESOURCE", "--type-name",
                              type_name, "--query", "Schema", "--output", "text", "--region", "ap-south-1"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise SystemExit(f"cannot fetch schema for {type_name}: {out.stderr.strip()}")
        f.write_text(out.stdout)
    return json.loads(f.read_text())


def resolve(schema: dict, node: dict) -> dict:
    seen = 0
    while "$ref" in node and seen < 20:
        ref = node["$ref"]
        assert ref.startswith("#/"), ref
        cur = schema
        for part in ref[2:].split("/"):
            cur = cur[part]
        node = {**cur, **{k: v for k, v in node.items() if k != "$ref"}}
        seen += 1
    return node


INTRINSIC = re.compile(r"^(Ref|Fn::\w+|Condition)$")


def is_intrinsic(v) -> bool:
    return isinstance(v, dict) and len(v) == 1 and INTRINSIC.match(next(iter(v)))


def check_value(schema, node, value, path):
    node = resolve(schema, node)
    if is_intrinsic(value):
        key = next(iter(value))
        if key == "Fn::If":      # check both branches
            _, a, b = value[key]
            for branch in (a, b):
                if branch != {"Ref": "AWS::NoValue"}:
                    check_value(schema, node, branch, path)
        return
    for combo in ("oneOf", "anyOf"):
        if combo in node and isinstance(value, dict):
            options = [resolve(schema, o) for o in node[combo]]
            merged = {}
            for o in options:
                merged.update(o.get("properties", {}))
            if merged:
                node = {"type": "object", "properties": merged, "additionalProperties": False}
    types = node.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    if isinstance(value, dict) and ("object" in types or "properties" in node):
        props = node.get("properties", {})
        patterned = node.get("patternProperties", {})
        for k, v in value.items():
            if k in PROSE_ENUMS:
                for item in (v if isinstance(v, list) else [v]):
                    if isinstance(item, str) and item not in PROSE_ENUMS[k]:
                        problems.append(f"{path}.{k}: {item!r} not in {sorted(PROSE_ENUMS[k])}")
            if k in props:
                check_value(schema, props[k], v, f"{path}.{k}")
            elif any(re.match(p, k) for p in patterned):
                pass
            elif node.get("additionalProperties") is False or props:
                if not node.get("additionalProperties", False) is True:
                    problems.append(f"{path}: unknown property {k!r} (allowed: {', '.join(sorted(props))})")
        for req in node.get("required", []):
            if req not in value:
                problems.append(f"{path}: missing required property {req!r}")
    elif isinstance(value, list) and ("array" in types or "items" in node):
        for i, item in enumerate(value):
            if "items" in node:
                check_value(schema, node["items"], item, f"{path}[{i}]")
    elif "enum" in node and isinstance(value, (str, int, bool)) and not is_intrinsic(value):
        allowed = node["enum"]
        if value not in allowed and str(value) not in [str(a) for a in allowed]:
            problems.append(f"{path}: {value!r} not in {allowed}")


def walk_refs(x, path, names, resources, conditions):
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "Ref":
                if v not in names:
                    problems.append(f"{path}: Ref to unknown {v!r}")
            elif k == "Fn::GetAtt":
                res, attr = v if isinstance(v, list) else v.split(".", 1)
                check_getatt(res, attr, path, resources)
            elif k == "Fn::Sub":
                text, mapping = (v, {}) if isinstance(v, str) else (v[0], v[1])
                for m in re.findall(r"\$\{([^}!][^}]*)\}", text):
                    if m in mapping:
                        continue
                    if "." in m and m.split(".", 1)[0] in resources:
                        check_getatt(*m.split(".", 1), path, resources)
                    elif m not in names:
                        problems.append(f"{path}: ${{{m}}} in Sub is unknown")
                walk_refs(mapping, path, names, resources, conditions)
            elif k == "Fn::FindInMap":
                check_find_in_map(v, path)
                walk_refs(v, path, names, resources, conditions)
            elif k in ("Fn::If",):
                if v[0] not in conditions:
                    problems.append(f"{path}: Fn::If on unknown condition {v[0]!r}")
                walk_refs(v[1:], path, names, resources, conditions)
            elif k == "Condition" and isinstance(v, str):
                if v not in conditions:
                    problems.append(f"{path}: unknown condition {v!r}")
            else:
                walk_refs(v, f"{path}.{k}", names, resources, conditions)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            walk_refs(v, f"{path}[{i}]", names, resources, conditions)


MAPPINGS: dict = {}
PARAMS: dict = {}


def check_find_in_map(v, path):
    name, key, attr = v
    if name not in MAPPINGS:
        problems.append(f"{path}: FindInMap on unknown mapping {name!r}")
        return
    keys = [key] if isinstance(key, str) else []
    if isinstance(key, dict) and "Ref" in key and key["Ref"] in PARAMS:
        keys = [str(a) for a in PARAMS[key["Ref"]].get("AllowedValues", [])]
        if not keys:
            problems.append(f"{path}: FindInMap key {key['Ref']} has no AllowedValues to check against")
    for k in keys:
        if k not in MAPPINGS[name] or (isinstance(attr, str) and attr not in MAPPINGS[name][k]):
            problems.append(f"{path}: mapping {name} has no {k}.{attr}")


def check_json_parameters(resources):
    """SSM parameters whose value is a JSON object (Fn::Sub): the rendered text must parse."""
    for name, r in resources.items():
        if r["Type"] != "AWS::SSM::Parameter":
            continue
        value = r["Properties"].get("Value")
        sub = value.get("Fn::Sub") if isinstance(value, dict) else None
        if sub is None:
            continue
        text = sub if isinstance(sub, str) else sub[0]
        rendered = re.sub(r"\$\{[^}]+\}", "X", text)
        if rendered.lstrip().startswith("{"):
            try:
                json.loads(rendered)
            except ValueError as e:
                problems.append(f"Resources.{name}.Value: not valid JSON after substitution: {e}")


CROSS_REGION_PROFILE = re.compile(r"^(global|apac|in|us|eu|jp|au|ca|us-gov)\.")
MUMBAI_MODEL_ARN = re.compile(
    r"^arn:\$\{AWS::Partition\}:bedrock:\$\{AWS::Region\}::foundation-model/(?P<id>[a-z0-9][a-z0-9.:-]*)$")
MUMBAI_ONLY_DENY = "arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/*"


def _items(x):
    return x if isinstance(x, list) else [x]


def _branches(x):
    """Every value an intrinsic-free list item can take (both sides of Fn::If)."""
    if isinstance(x, dict) and "Fn::If" in x:
        return [v for branch in x["Fn::If"][1:] for item in _items(branch) for v in _branches(item)]
    return [x]


def check_mumbai_only(resources):
    """Mumbai only: the server invokes exactly the in-Region foundation models of its LLM chain, an explicit Deny
    refuses every other model ARN (global./apac./in. inference profiles, other Regions), and the chain the app
    gets names no cross-Region profile."""
    where = "Resources.StackEnvParameter.Value"
    sub = resources.get("StackEnvParameter", {}).get("Properties", {}).get("Value", {}).get("Fn::Sub")
    text = sub if isinstance(sub, str) else (sub or [""])[0]
    try:
        env = json.loads(re.sub(r"\$\{[^}]+\}", "X", text))
    except ValueError:
        env = {}
    chain = [m.strip() for m in str(env.get("LLM_MODEL_CHAIN", "")).split(",") if m.strip()]
    if not chain:
        problems.append(f"{where}: no LLM_MODEL_CHAIN")
    for model in chain:
        if CROSS_REGION_PROFILE.match(model):
            problems.append(f"{where}: LLM_MODEL_CHAIN has the cross-Region profile {model!r} (Mumbai only)")
    if env.get("LLM_MUMBAI_ONLY") != "true":
        problems.append(f"{where}: LLM_MUMBAI_ONLY must be \"true\"")

    role = resources.get("InstanceRole", {}).get("Properties", {})
    for arn in role.get("ManagedPolicyArns", []):
        name = arn.get("Fn::Sub", arn) if isinstance(arn, dict) else arn
        if not str(name).endswith(":policy/AmazonSSMManagedInstanceCore"):
            problems.append(f"Resources.InstanceRole: managed policy {name!r} (only AmazonSSMManagedInstanceCore)")
    statements = [s for p in role.get("Policies", []) for s in _items(p["PolicyDocument"]["Statement"])]
    allowed, deny_found = set(), False
    for s in statements:
        actions = [a for a in _items(s.get("Action", [])) if isinstance(a, str)]
        if not any(a == "*" or a.startswith("bedrock") for a in actions):
            continue
        sid = s.get("Sid", "?")
        if s["Effect"] == "Allow":
            if "NotResource" in s or "NotAction" in s:
                problems.append(f"InstanceRole {sid}: a Bedrock Allow with NotResource/NotAction")
            for res in (v for item in _items(s.get("Resource", [])) for v in _branches(item)):
                arn = res.get("Fn::Sub") if isinstance(res, dict) else res
                m = MUMBAI_MODEL_ARN.match(arn) if isinstance(arn, str) else None
                if m is None:
                    problems.append(f"InstanceRole {sid}: allows {arn!r}; only ap-south-1 foundation models may be")
                else:
                    allowed.add(m["id"])
        elif ("bedrock:InvokeModel*" in actions or "bedrock:*" in actions) and \
                _items(s.get("NotResource", [])) == [{"Fn::Sub": MUMBAI_ONLY_DENY}]:
            deny_found = True
    if allowed != set(chain):
        problems.append(f"InstanceRole: the models it may invoke {sorted(allowed)} differ from LLM_MODEL_CHAIN {chain}")
    if not deny_found:
        problems.append(f"InstanceRole: no Deny on bedrock:InvokeModel* with NotResource {MUMBAI_ONLY_DENY}")
    print(f"Mumbai only: chain {','.join(chain)}; role invokes exactly these, everything else denied"
          if allowed == set(chain) and deny_found else "")


def check_getatt(res, attr, path, resources):
    if res not in resources:
        problems.append(f"{path}: GetAtt on unknown resource {res!r}")
        return
    rtype = resources[res]["Type"]
    s = schema_for(rtype)
    readonly = {p.split("/")[-1] for p in s.get("readOnlyProperties", [])}
    if attr not in readonly and attr not in s.get("properties", {}) and attr not in LEGACY_ATTRS.get(rtype, set()):
        problems.append(f"{path}: {rtype} has no attribute {attr!r} (read-only: {sorted(readonly)})")


def check_user_data(resources):
    for name, r in resources.items():
        ud = r.get("Properties", {}).get("UserData")
        if not ud:
            continue
        inner = ud.get("Fn::Base64", ud)
        sub = inner.get("Fn::Sub") if isinstance(inner, dict) else inner
        text = sub if isinstance(sub, str) else sub[0]
        script = re.sub(r"\$\{(?!!)[^}]+\}", "DUMMY", text).replace("${!", "${")
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(script)
        out = subprocess.run(["bash", "-n", f.name], capture_output=True, text=True)
        os.unlink(f.name)
        if out.returncode != 0:
            problems.append(f"Resources.{name}.UserData: bash -n failed: {out.stderr.strip()}")
        if re.search(r"\$\{(?!AWS::|!)(?![A-Z][A-Za-z0-9]*[}.])", text):
            problems.append(f"Resources.{name}.UserData: '${{' that Fn::Sub would treat as a variable")
        print(f"user-data of {name}: {len(script.splitlines())} lines, bash -n ok" if out.returncode == 0 else "")


def main():
    raw = yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)
    t = to_long_form(raw)
    params = t.get("Parameters", {})
    resources = t.get("Resources", {})
    conditions = t.get("Conditions", {})
    names = set(params) | set(resources) | PSEUDO
    MAPPINGS.update(t.get("Mappings", {}))
    PARAMS.update(params)
    for name, p in params.items():
        if "Default" in p and "AllowedValues" in p and str(p["Default"]) not in [str(a) for a in p["AllowedValues"]]:
            problems.append(f"Parameters.{name}: default not in AllowedValues")
        if "Default" in p and "AllowedPattern" in p and p.get("Type") == "String":
            if not re.fullmatch(p["AllowedPattern"], str(p["Default"])):
                problems.append(f"Parameters.{name}: default {p['Default']!r} does not match AllowedPattern")
        if name not in json.dumps(t.get("Resources")) + json.dumps(t.get("Conditions")) + json.dumps(t.get("Outputs")):
            problems.append(f"Parameters.{name}: never used")
    for name, r in resources.items():
        rtype = r["Type"]
        s = schema_for(rtype)
        props = r.get("Properties", {})
        root = {"type": "object", "properties": s["properties"], "required": s.get("required", []),
                "additionalProperties": False}
        check_value(s, root, props, f"Resources.{name}.Properties")
        if "Condition" in r and r["Condition"] not in conditions:
            problems.append(f"Resources.{name}: unknown condition {r['Condition']!r}")
        for dep in ([r["DependsOn"]] if isinstance(r.get("DependsOn"), str) else r.get("DependsOn", [])):
            if dep not in resources:
                problems.append(f"Resources.{name}: DependsOn unknown {dep!r}")
    walk_refs(resources, "Resources", names, resources, conditions)
    walk_refs(t.get("Outputs", {}), "Outputs", names, resources, conditions)
    walk_refs(conditions, "Conditions", names, resources, conditions)
    for name, o in t.get("Outputs", {}).items():
        if "Condition" in o and o["Condition"] not in conditions:
            problems.append(f"Outputs.{name}: unknown condition")
        ref = o.get("Value")
        cond_res = {k for k, r in resources.items() if "Condition" in r}
        if is_intrinsic(ref) and next(iter(ref)) == "Ref" and ref["Ref"] in cond_res and "Condition" not in o:
            problems.append(f"Outputs.{name}: refers to conditional resource {ref['Ref']} without a Condition")
    check_user_data(resources)
    check_json_parameters(resources)
    check_mumbai_only(resources)
    size = TEMPLATE.stat().st_size
    if size > 51200:
        problems.append(f"template is {size} bytes: `aws cloudformation deploy` needs --s3-bucket above 51,200")
    print(f"{TEMPLATE.name}: {len(params)} parameters, {len(resources)} resources, {size} bytes")
    if problems:
        print("\n".join(f"PROBLEM: {p}" for p in problems))
        sys.exit(1)
    print("template checks: OK")


if __name__ == "__main__":
    main()
