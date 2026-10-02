"""Log Retention Enforcer Lambda

Runs daily (EventBridge schedule) and once on every deploy (CDK Trigger):

1. Every CloudWatch log group (or only those under LOG_GROUP_PREFIXES) whose
   retention is missing ("never expire") or longer than RETENTION_DAYS gets
   the largest allowed retention <= RETENTION_DAYS. This covers log groups
   that AWS services create on their own (Lambda, AgentCore, CodeBuild, ...).
   Retention is never raised.
2. ECR repositories named ECR_REPOSITORY_PREFIX* without a lifecycle policy get
   one that expires UNTAGGED images after 1 day. Tagged images are live Lambda
   / AgentCore code and are never expired; an existing policy is never
   overwritten.

The handler NEVER raises (a failing deploy-time Trigger would fail the
deploy); errors are reported in the result.

Environment:
    RETENTION_DAYS (default 7), LOG_GROUP_PREFIXES (comma list, '' = all
    log groups), ECR_REPOSITORY_PREFIX (default 'cdk-').
"""
import json
import os

import boto3

ALLOWED_RETENTION = [
    1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096,
    1827, 2192, 2557, 2922, 3288, 3653,
]

UNTAGGED_EXPIRY_POLICY = {
    'rules': [
        {
            'rulePriority': 1,
            'description': 'Expire untagged images 1 day after push (tagged images are kept)',
            'selection': {
                'tagStatus': 'untagged',
                'countType': 'sinceImagePushed',
                'countUnit': 'days',
                'countNumber': 1,
            },
            'action': {'type': 'expire'},
        }
    ]
}

MAX_ERRORS = 50


def target_retention(days) -> int:
    """Largest CloudWatch Logs retention value <= days (minimum 1)."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = ALLOWED_RETENTION[0]
    allowed = [value for value in ALLOWED_RETENTION if value <= days]
    return allowed[-1] if allowed else ALLOWED_RETENTION[0]


def parse_prefixes(raw) -> list:
    if not raw:
        return []
    return [p.strip() for p in str(raw).split(',') if p.strip()]


def _add_error(result: dict, text: str) -> None:
    result['errors_total'] = result.get('errors_total', 0) + 1
    if len(result['errors']) < MAX_ERRORS:
        result['errors'].append(text)


def enforce_log_retention(logs, days, prefixes=None) -> dict:
    target = target_retention(days)
    result = {
        'target_days': target,
        'checked': 0,
        'updated': 0,
        'already_ok': 0,
        'errors': [],
        'errors_total': 0,
    }
    seen = set()
    for prefix in (prefixes or [None]):
        kwargs = {'logGroupNamePrefix': prefix} if prefix else {}
        while True:
            try:
                response = logs.describe_log_groups(**kwargs)
            except Exception as e:
                _add_error(result, f'describe_log_groups failed ({type(e).__name__})')
                break
            for group in response.get('logGroups', []) or []:
                name = group.get('logGroupName')
                if not name or name in seen:
                    continue
                seen.add(name)
                result['checked'] += 1
                current = group.get('retentionInDays')
                if current is not None and current <= target:
                    result['already_ok'] += 1
                    continue
                try:
                    logs.put_retention_policy(logGroupName=name, retentionInDays=target)
                    result['updated'] += 1
                except Exception as e:
                    _add_error(result, f'put_retention_policy failed for {name} ({type(e).__name__})')
            token = response.get('nextToken')
            if not token:
                break
            kwargs['nextToken'] = token
    return result


def _is_policy_not_found(exc: Exception) -> bool:
    if type(exc).__name__ == 'LifecyclePolicyNotFoundException':
        return True
    code = (getattr(exc, 'response', None) or {}).get('Error', {}).get('Code')
    return code == 'LifecyclePolicyNotFoundException'


def enforce_ecr_untagged(ecr, prefix) -> dict:
    result = {
        'repositories': 0,
        'policies_added': 0,
        'policies_kept': 0,
        'errors': [],
        'errors_total': 0,
    }
    prefix = prefix or ''
    kwargs = {}
    while True:
        try:
            response = ecr.describe_repositories(**kwargs)
        except Exception as e:
            _add_error(result, f'describe_repositories failed ({type(e).__name__})')
            break
        for repo in response.get('repositories', []) or []:
            name = repo.get('repositoryName') or ''
            if not name.startswith(prefix):
                continue
            result['repositories'] += 1
            try:
                ecr.get_lifecycle_policy(repositoryName=name)
                result['policies_kept'] += 1  # never overwrite an existing policy
                continue
            except Exception as e:
                if not _is_policy_not_found(e):
                    _add_error(result, f'get_lifecycle_policy failed for {name} ({type(e).__name__})')
                    continue
            try:
                ecr.put_lifecycle_policy(
                    repositoryName=name,
                    lifecyclePolicyText=json.dumps(UNTAGGED_EXPIRY_POLICY),
                )
                result['policies_added'] += 1
            except Exception as e:
                _add_error(result, f'put_lifecycle_policy failed for {name} ({type(e).__name__})')
        token = response.get('nextToken')
        if not token:
            break
        kwargs['nextToken'] = token
    return result


def handler(event, context):
    result = {'errors': []}
    try:
        days = os.environ.get('RETENTION_DAYS', '7')
        prefixes = parse_prefixes(os.environ.get('LOG_GROUP_PREFIXES', ''))
        ecr_prefix = os.environ.get('ECR_REPOSITORY_PREFIX', 'cdk-')
        region = os.environ.get('AWS_REGION')

        try:
            logs = boto3.client('logs', region_name=region)
            result['logs'] = enforce_log_retention(logs, days, prefixes)
        except Exception as e:
            result['errors'].append(f'logs: {type(e).__name__}')

        try:
            ecr = boto3.client('ecr', region_name=region)
            result['ecr'] = enforce_ecr_untagged(ecr, ecr_prefix)
        except Exception as e:
            result['errors'].append(f'ecr: {type(e).__name__}')
    except Exception as e:  # never fail a deploy-time Trigger
        result['errors'].append(f'handler: {type(e).__name__}')

    try:
        print(json.dumps({'log_retention': result}, default=str))
    except Exception:
        pass
    return result
