"""Tests for the log retention enforcer. No AWS calls: logs/ECR clients are fakes.

Usage (from packages/infra/src/functions/retention):
    python -m pytest -q log-retention-enforcer/test_retention_enforcer.py
"""
import importlib.util
import json
import os

import pytest

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-south-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_enforcer():
    spec = importlib.util.spec_from_file_location(
        'retention_log_enforcer_index', os.path.join(HERE, 'index.py')
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


enforcer = _load_enforcer()


class FakeLogs:
    def __init__(self, groups, page_size=2, fail_on=()):
        # groups: {name: retentionInDays or None}
        self.groups = dict(groups)
        self.page_size = page_size
        self.fail_on = set(fail_on)
        self.puts = []
        self.describe_calls = []

    def describe_log_groups(self, logGroupNamePrefix=None, nextToken=None):
        self.describe_calls.append(logGroupNamePrefix)
        names = sorted(n for n in self.groups if not logGroupNamePrefix or n.startswith(logGroupNamePrefix))
        start = int(nextToken or 0)
        page = names[start:start + self.page_size]
        response = {'logGroups': []}
        for name in page:
            group = {'logGroupName': name, 'storedBytes': 0}
            if self.groups[name] is not None:
                group['retentionInDays'] = self.groups[name]
            response['logGroups'].append(group)
        if start + self.page_size < len(names):
            response['nextToken'] = str(start + self.page_size)
        return response

    def put_retention_policy(self, logGroupName, retentionInDays):
        if logGroupName in self.fail_on:
            raise RuntimeError('AccessDenied')
        self.puts.append((logGroupName, retentionInDays))
        self.groups[logGroupName] = retentionInDays


class LifecyclePolicyNotFoundException(Exception):
    pass


class FakeEcr:
    def __init__(self, repos, policies):
        self.repos = list(repos)
        self.policies = dict(policies)
        self.puts = []

    def describe_repositories(self, nextToken=None):
        start = int(nextToken or 0)
        page = self.repos[start:start + 2]
        response = {'repositories': [{'repositoryName': r} for r in page]}
        if start + 2 < len(self.repos):
            response['nextToken'] = str(start + 2)
        return response

    def get_lifecycle_policy(self, repositoryName):
        if repositoryName not in self.policies:
            raise LifecyclePolicyNotFoundException(repositoryName)
        return {'lifecyclePolicyText': self.policies[repositoryName]}

    def put_lifecycle_policy(self, repositoryName, lifecyclePolicyText):
        self.puts.append((repositoryName, json.loads(lifecyclePolicyText)))
        self.policies[repositoryName] = lifecyclePolicyText


class BrokenClient:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError('service unavailable')
        return fail


# ---------------------------------------------------------------------------


@pytest.mark.parametrize('days,expected', [
    (7, 7), (10, 7), (0, 1), (-3, 1), (1, 1), (2, 1), (14, 14), (29, 14),
    (365, 365), (10_000, 3653), ('7', 7), (None, 1),
])
def test_target_retention(days, expected):
    assert enforcer.target_retention(days) == expected


def test_groups_are_lowered_never_raised():
    logs = FakeLogs({
        '/aws/lambda/idp-v2-a': None,
        '/aws/lambda/idp-v2-b': 30,
        '/aws/codebuild/idp-v2': 3653,
        '/aws/bedrock-agentcore/runtimes/x': 1,
        '/aws/vendedlogs/states/y': 5,
        '/ecs/backend': 7,
    })
    result = enforcer.enforce_log_retention(logs, 7)

    assert sorted(logs.puts) == [
        ('/aws/codebuild/idp-v2', 7),
        ('/aws/lambda/idp-v2-a', 7),
        ('/aws/lambda/idp-v2-b', 7),
    ]
    assert logs.groups['/aws/bedrock-agentcore/runtimes/x'] == 1
    assert logs.groups['/aws/vendedlogs/states/y'] == 5
    assert logs.groups['/ecs/backend'] == 7
    assert result['checked'] == 6
    assert result['updated'] == 3
    assert result['already_ok'] == 3
    assert result['target_days'] == 7
    assert logs.describe_calls[0] is None  # no prefix = every group


def test_prefix_filtering():
    logs = FakeLogs({
        '/aws/lambda/idp-v2-a': None,
        '/aws/lambda/other-app': None,
        '/aws/codebuild/idp-v2': None,
        '/custom/elsewhere': None,
    })
    prefixes = enforcer.parse_prefixes(' /aws/lambda/idp-v2 , /aws/codebuild/ ,, ')
    assert prefixes == ['/aws/lambda/idp-v2', '/aws/codebuild/']
    result = enforcer.enforce_log_retention(logs, 7, prefixes)
    assert sorted(n for n, _ in logs.puts) == ['/aws/codebuild/idp-v2', '/aws/lambda/idp-v2-a']
    assert logs.groups['/aws/lambda/other-app'] is None
    assert result['updated'] == 2


def test_put_failure_is_reported_not_raised():
    logs = FakeLogs({'/aws/lambda/a': None, '/aws/lambda/b': None}, fail_on={'/aws/lambda/a'})
    result = enforcer.enforce_log_retention(logs, 7)
    assert logs.puts == [('/aws/lambda/b', 7)]
    assert result['updated'] == 1
    assert len(result['errors']) == 1 and 'RuntimeError' in result['errors'][0]


def test_ecr_untagged_policy_added_only_where_missing():
    ecr = FakeEcr(
        repos=['cdk-hnb659fds-container-assets-000000000000-ap-south-1', 'cdk-with-policy', 'my-other-repo'],
        policies={'cdk-with-policy': '{"rules": []}'},
    )
    result = enforcer.enforce_ecr_untagged(ecr, 'cdk-')

    assert [name for name, _ in ecr.puts] == ['cdk-hnb659fds-container-assets-000000000000-ap-south-1']
    policy = ecr.puts[0][1]
    assert len(policy['rules']) == 1
    rule = policy['rules'][0]
    assert rule['selection']['tagStatus'] == 'untagged'  # tagged images are never expired
    assert rule['selection']['countType'] == 'sinceImagePushed'
    assert rule['selection']['countNumber'] == 1
    assert rule['action'] == {'type': 'expire'}
    assert ecr.policies['cdk-with-policy'] == '{"rules": []}'  # existing policy untouched
    assert 'my-other-repo' not in ecr.policies
    assert result == {
        'repositories': 2, 'policies_added': 1, 'policies_kept': 1,
        'errors': [], 'errors_total': 0,
    }


def test_ecr_client_error_code_is_recognised():
    class ClientErrorLike(Exception):
        def __init__(self):
            super().__init__('not found')
            self.response = {'Error': {'Code': 'LifecyclePolicyNotFoundException'}}

    class Ecr(FakeEcr):
        def get_lifecycle_policy(self, repositoryName):
            raise ClientErrorLike()

    ecr = Ecr(repos=['cdk-x'], policies={})
    result = enforcer.enforce_ecr_untagged(ecr, 'cdk-')
    assert result['policies_added'] == 1


def test_ecr_other_errors_do_not_put_a_policy():
    class Ecr(FakeEcr):
        def get_lifecycle_policy(self, repositoryName):
            raise RuntimeError('AccessDenied')

    ecr = Ecr(repos=['cdk-x'], policies={})
    result = enforcer.enforce_ecr_untagged(ecr, 'cdk-')
    assert ecr.puts == []
    assert result['policies_added'] == 0 and len(result['errors']) == 1


def test_broken_clients_report_errors():
    logs_result = enforcer.enforce_log_retention(BrokenClient(), 7)
    ecr_result = enforcer.enforce_ecr_untagged(BrokenClient(), 'cdk-')
    assert logs_result['errors'] and ecr_result['errors']


def test_handler_never_raises_when_clients_error(monkeypatch):
    monkeypatch.setenv('RETENTION_DAYS', '7')
    monkeypatch.setenv('LOG_GROUP_PREFIXES', '')
    monkeypatch.setenv('ECR_REPOSITORY_PREFIX', 'cdk-')
    monkeypatch.setattr(enforcer.boto3, 'client', lambda *a, **kw: BrokenClient())
    result = enforcer.handler({}, None)
    assert result['logs']['errors']
    assert result['ecr']['errors']


def test_handler_never_raises_when_client_creation_fails(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError('no credentials')

    monkeypatch.setattr(enforcer.boto3, 'client', explode)
    result = enforcer.handler({'RequestType': 'Create'}, None)
    assert result['errors'] == ['logs: RuntimeError', 'ecr: RuntimeError']


def test_handler_uses_env(monkeypatch):
    logs = FakeLogs({'/aws/lambda/idp-v2-a': 400, '/aws/lambda/other': None})
    ecr = FakeEcr(repos=['cdk-a'], policies={})
    monkeypatch.setenv('RETENTION_DAYS', '14')
    monkeypatch.setenv('LOG_GROUP_PREFIXES', '/aws/lambda/idp-v2')
    monkeypatch.setenv('ECR_REPOSITORY_PREFIX', 'cdk-')
    monkeypatch.setattr(enforcer.boto3, 'client', lambda name, **kw: logs if name == 'logs' else ecr)
    result = enforcer.handler({}, None)
    assert logs.puts == [('/aws/lambda/idp-v2-a', 14)]
    assert result['logs']['target_days'] == 14
    assert result['ecr']['policies_added'] == 1
    assert result['errors'] == []
