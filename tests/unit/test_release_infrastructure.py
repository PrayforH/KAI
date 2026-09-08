from harness.release_infrastructure import (
    REQUIRED_ENVIRONMENT_VARIABLES,
    RegistryProbe,
    ReleaseEnvironmentSnapshot,
    ReleaseInfrastructureSnapshot,
    ReleaseRunnerSnapshot,
    audit_release_infrastructure,
)


def _ready_snapshot() -> ReleaseInfrastructureSnapshot:
    environments = tuple(
        ReleaseEnvironmentSnapshot(
            name=name,
            variables=tuple(sorted(REQUIRED_ENVIRONMENT_VARIABLES)),
            secrets=("HARNESS_API_BEARER_TOKEN",),
            protectionRules=("required_reviewers",) if name == "production" else (),
        )
        for name in ("test", "canary", "production")
    )
    return ReleaseInfrastructureSnapshot(
        repository="PrayforH/agent-studio",
        repositoryVariables={
            "HARNESS_RELEASE_RUNNER": "trusted-release",
            "HARNESS_RELEASE_REGISTRY": "harbor.shdata.com:5000",
            "HARNESS_RELEASE_NAMESPACE": "agent-studio/amd64",
        },
        repositorySecrets=(
            "HARNESS_RELEASE_REGISTRY_USERNAME",
            "HARNESS_RELEASE_REGISTRY_PASSWORD",
        ),
        environments=environments,
        runners=(
            ReleaseRunnerSnapshot(
                name="bridge-1",
                status="online",
                labels=("self-hosted", "linux", "harness-deploy", "trusted-release"),
            ),
        ),
        registry=RegistryProbe(
            url="https://harbor.shdata.com:5000/v2/",
            tlsVerified=True,
            statusCode=401,
        ),
    )


def test_complete_release_infrastructure_is_ready() -> None:
    audit = audit_release_infrastructure(_ready_snapshot())

    assert audit.ready is True
    assert all(check.passed for check in audit.checks)


def test_missing_infrastructure_reports_every_independent_gap() -> None:
    snapshot = _ready_snapshot().model_copy(
        update={
            "repository_variables": {
                "HARNESS_RELEASE_REGISTRY": "http://harbor.shdata.com:5000"
            },
            "repository_secrets": (),
            "environments": (),
            "runners": (),
            "registry": RegistryProbe(
                url="https://harbor.shdata.com:5000/v2/",
                tlsVerified=False,
                error="ConnectError",
            ),
        }
    )

    audit = audit_release_infrastructure(snapshot)
    failed_ids = {check.check_id for check in audit.checks if not check.passed}

    assert audit.ready is False
    assert "repository.release-runner-variable" in failed_ids
    assert "repository.registry-variable" in failed_ids
    assert "repository.registry-secret-names" in failed_ids
    assert "environment.production.exists" in failed_ids
    assert "runner.release" in failed_ids
    assert "runner.deployment" in failed_ids
    assert "registry.tls" in failed_ids


def test_busy_online_runner_remains_eligible() -> None:
    snapshot = _ready_snapshot()
    snapshot = snapshot.model_copy(
        update={"runners": (snapshot.runners[0].model_copy(update={"busy": True}),)}
    )

    audit = audit_release_infrastructure(snapshot)

    assert audit.ready is True


def test_release_label_on_a_non_linux_runner_is_not_eligible() -> None:
    snapshot = _ready_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "runners": (
                ReleaseRunnerSnapshot(
                    name="windows-build",
                    status="online",
                    labels=("self-hosted", "windows", "trusted-release"),
                ),
                ReleaseRunnerSnapshot(
                    name="linux-deploy",
                    status="online",
                    labels=("self-hosted", "linux", "harness-deploy"),
                ),
            )
        }
    )

    audit = audit_release_infrastructure(snapshot)
    release_check = next(
        check for check in audit.checks if check.check_id == "runner.release"
    )

    assert release_check.passed is False
