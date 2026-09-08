"""Read-only audit of the infrastructure required to publish a release."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import httpx
from pydantic import Field

from harness.release import ReleaseModel

REQUIRED_ENVIRONMENTS = ("test", "canary", "production")
REQUIRED_ENVIRONMENT_VARIABLES = frozenset(
    {
        "HARNESS_DEPLOY_ENV_FILE",
        "HARNESS_RELEASE_STATE_ROOT",
        "HARNESS_BASE_URL",
        "HARNESS_WEB_URL",
        "HARNESS_TENANT_ID",
        "HARNESS_SMOKE_USER_ID",
        "HARNESS_SMOKE_AGENT_NAME",
        "HARNESS_SMOKE_AGENT_VERSION",
        "HARNESS_EXECUTION_PROFILE",
    }
)
REQUIRED_ENVIRONMENT_SECRETS = frozenset({"HARNESS_API_BEARER_TOKEN"})
REQUIRED_REPOSITORY_SECRETS = frozenset(
    {"HARNESS_RELEASE_REGISTRY_USERNAME", "HARNESS_RELEASE_REGISTRY_PASSWORD"}
)
DEPLOY_RUNNER_LABELS = frozenset({"self-hosted", "linux", "harness-deploy"})


class ReleaseEnvironmentSnapshot(ReleaseModel):
    name: str
    variables: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()
    protection_rules: tuple[str, ...] = Field(default=(), alias="protectionRules")


class ReleaseRunnerSnapshot(ReleaseModel):
    name: str
    status: str
    busy: bool = False
    labels: tuple[str, ...] = ()


class RegistryProbe(ReleaseModel):
    url: str
    tls_verified: bool = Field(alias="tlsVerified")
    status_code: int | None = Field(default=None, alias="statusCode")
    error: str | None = None


class ReleaseInfrastructureSnapshot(ReleaseModel):
    repository: str
    repository_variables: dict[str, str] = Field(alias="repositoryVariables")
    repository_secrets: tuple[str, ...] = Field(alias="repositorySecrets")
    environments: tuple[ReleaseEnvironmentSnapshot, ...]
    runners: tuple[ReleaseRunnerSnapshot, ...]
    registry: RegistryProbe


class InfrastructureCheck(ReleaseModel):
    check_id: str = Field(alias="checkId")
    passed: bool
    detail: str


class ReleaseInfrastructureAudit(ReleaseModel):
    repository: str
    ready: bool
    checks: tuple[InfrastructureCheck, ...]


def _check(check_id: str, passed: bool, detail: str) -> InfrastructureCheck:
    return InfrastructureCheck(checkId=check_id, passed=passed, detail=detail)


def audit_release_infrastructure(
    snapshot: ReleaseInfrastructureSnapshot,
    *,
    expected_registry: str = "harbor.shdata.com:5000",
    expected_namespace: str = "agent-studio/amd64",
) -> ReleaseInfrastructureAudit:
    """Return every readiness decision without exposing any secret value."""

    checks: list[InfrastructureCheck] = []
    variables = snapshot.repository_variables
    release_runner_label = variables.get("HARNESS_RELEASE_RUNNER", "").strip()
    checks.append(
        _check(
            "repository.release-runner-variable",
            bool(release_runner_label),
            "HARNESS_RELEASE_RUNNER is configured"
            if release_runner_label
            else "HARNESS_RELEASE_RUNNER is missing",
        )
    )
    actual_registry = variables.get("HARNESS_RELEASE_REGISTRY")
    checks.append(
        _check(
            "repository.registry-variable",
            actual_registry == expected_registry,
            f"HARNESS_RELEASE_REGISTRY must equal {expected_registry}",
        )
    )
    actual_namespace = variables.get("HARNESS_RELEASE_NAMESPACE")
    checks.append(
        _check(
            "repository.namespace-variable",
            actual_namespace == expected_namespace,
            f"HARNESS_RELEASE_NAMESPACE must equal {expected_namespace}",
        )
    )
    missing_repository_secrets = sorted(
        REQUIRED_REPOSITORY_SECRETS - set(snapshot.repository_secrets)
    )
    checks.append(
        _check(
            "repository.registry-secret-names",
            not missing_repository_secrets,
            "required Harbor robot secret names are present"
            if not missing_repository_secrets
            else f"missing secret names: {', '.join(missing_repository_secrets)}",
        )
    )

    environments = {environment.name: environment for environment in snapshot.environments}
    for name in REQUIRED_ENVIRONMENTS:
        environment = environments.get(name)
        checks.append(
            _check(
                f"environment.{name}.exists",
                environment is not None,
                f"GitHub environment {name} exists"
                if environment is not None
                else f"GitHub environment {name} is missing",
            )
        )
        if environment is None:
            continue
        missing_variables = sorted(
            REQUIRED_ENVIRONMENT_VARIABLES - set(environment.variables)
        )
        checks.append(
            _check(
                f"environment.{name}.variables",
                not missing_variables,
                "required environment variable names are present"
                if not missing_variables
                else f"missing variable names: {', '.join(missing_variables)}",
            )
        )
        missing_secrets = sorted(REQUIRED_ENVIRONMENT_SECRETS - set(environment.secrets))
        checks.append(
            _check(
                f"environment.{name}.secret-names",
                not missing_secrets,
                "required environment secret names are present"
                if not missing_secrets
                else f"missing secret names: {', '.join(missing_secrets)}",
            )
        )
        if name == "production":
            protected = "required_reviewers" in environment.protection_rules
            checks.append(
                _check(
                    "environment.production.reviewers",
                    protected,
                    "production requires reviewer approval"
                    if protected
                    else "production has no required-reviewer protection rule",
                )
            )

    online_runners = [runner for runner in snapshot.runners if runner.status == "online"]
    release_runner_ready = bool(release_runner_label) and any(
        {"self-hosted", "linux", release_runner_label} <= set(runner.labels)
        for runner in online_runners
    )
    checks.append(
        _check(
            "runner.release",
            release_runner_ready,
            "an online Linux self-hosted runner has the configured release label"
            if release_runner_ready
            else (
                "no online Linux self-hosted runner has the configured release label"
            ),
        )
    )
    deploy_runner_ready = any(
        DEPLOY_RUNNER_LABELS <= set(runner.labels) for runner in online_runners
    )
    checks.append(
        _check(
            "runner.deployment",
            deploy_runner_ready,
            "an online runner has self-hosted, linux and harness-deploy labels"
            if deploy_runner_ready
            else "no online runner has self-hosted, linux and harness-deploy labels",
        )
    )

    registry_url = f"https://{expected_registry}/v2/"
    registry_ready = (
        snapshot.registry.url == registry_url
        and snapshot.registry.tls_verified
        and snapshot.registry.status_code in {200, 401, 403}
    )
    checks.append(
        _check(
            "registry.tls",
            registry_ready,
            f"TLS-verified registry API is reachable at {registry_url}"
            if registry_ready
            else f"TLS-verified registry API is not reachable at {registry_url}",
        )
    )
    return ReleaseInfrastructureAudit(
        repository=snapshot.repository,
        ready=all(check.passed for check in checks),
        checks=tuple(checks),
    )


def _gh_api(path: str) -> dict[str, object]:
    process = subprocess.run(
        ["gh", "api", path],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        message = (
            process.stderr.strip().splitlines()[-1]
            if process.stderr.strip()
            else "unknown error"
        )
        raise RuntimeError(f"GitHub API request failed for {path}: {message}")
    result = cast(object, json.loads(process.stdout))
    if not isinstance(result, dict):
        raise RuntimeError(f"GitHub API returned a non-object for {path}")
    return cast(dict[str, object], result)


def _object_items(value: object, *, field: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise RuntimeError(f"GitHub API field {field} is not a list")
    return [
        cast(dict[str, object], item)
        for item in cast(list[object], value)
        if isinstance(item, dict)
    ]


def _named_items(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    raw_items = payload.get(key, [])
    return _object_items(raw_items, field=key)


def _names(payload: dict[str, object], key: str) -> tuple[str, ...]:
    names = [item.get("name") for item in _named_items(payload, key)]
    return tuple(sorted(name for name in names if isinstance(name, str)))


def probe_registry(registry: str, *, timeout_seconds: float = 5.0) -> RegistryProbe:
    """Probe the anonymous v2 endpoint; 401 is a healthy authenticated registry."""

    url = f"https://{registry}/v2/"
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout_seconds,
            trust_env=False,
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return RegistryProbe(
            url=url,
            tlsVerified=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return RegistryProbe(url=url, tlsVerified=True, statusCode=response.status_code)


def collect_release_infrastructure(
    repository: str,
    *,
    registry: str = "harbor.shdata.com:5000",
) -> ReleaseInfrastructureSnapshot:
    """Collect names and status through read-only GitHub and anonymous registry calls."""

    repo_variables_payload = _gh_api(f"repos/{repository}/actions/variables?per_page=100")
    repository_variables = {
        name: value
        for item in _named_items(repo_variables_payload, "variables")
        if isinstance((name := item.get("name")), str)
        and isinstance((value := item.get("value")), str)
    }
    repository_secrets = _names(
        _gh_api(f"repos/{repository}/actions/secrets?per_page=100"), "secrets"
    )
    environment_items = _named_items(
        _gh_api(f"repos/{repository}/environments?per_page=100"), "environments"
    )
    environments: list[ReleaseEnvironmentSnapshot] = []
    for item in environment_items:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        protection_rules = _object_items(
            item.get("protection_rules", []), field="protection_rules"
        )
        rule_names = tuple(
            sorted(
                rule_type
                for rule in protection_rules
                if isinstance((rule_type := rule.get("type")), str)
            )
        )
        variables = _names(
            _gh_api(f"repos/{repository}/environments/{name}/variables?per_page=100"),
            "variables",
        )
        secrets = _names(
            _gh_api(f"repos/{repository}/environments/{name}/secrets?per_page=100"),
            "secrets",
        )
        environments.append(
            ReleaseEnvironmentSnapshot(
                name=name,
                variables=variables,
                secrets=secrets,
                protectionRules=rule_names,
            )
        )

    runners: list[ReleaseRunnerSnapshot] = []
    for item in _named_items(
        _gh_api(f"repos/{repository}/actions/runners?per_page=100"), "runners"
    ):
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        label_items = _object_items(item.get("labels", []), field="labels")
        labels = tuple(
            sorted(
                label_name
                for label in label_items
                if isinstance((label_name := label.get("name")), str)
            )
        )
        runners.append(
            ReleaseRunnerSnapshot(
                name=name,
                status=status,
                busy=item.get("busy") is True,
                labels=labels,
            )
        )

    return ReleaseInfrastructureSnapshot(
        repository=repository,
        repositoryVariables=repository_variables,
        repositorySecrets=repository_secrets,
        environments=tuple(environments),
        runners=tuple(runners),
        registry=probe_registry(registry),
    )


def load_infrastructure_fixture(path: Path) -> ReleaseInfrastructureSnapshot:
    return ReleaseInfrastructureSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
