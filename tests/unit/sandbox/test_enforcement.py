"""Sandbox enforcement fact derivation."""

from harness.sandbox.base import (
    SandboxEnforcement,
    SandboxIsolation,
    sandbox_enforcement,
)


def test_workspace_isolation_reports_none() -> None:
    assert (
        sandbox_enforcement("local", SandboxIsolation.WORKSPACE)
        is SandboxEnforcement.NONE
    )


def test_kubernetes_reports_full() -> None:
    assert (
        sandbox_enforcement("kubernetes", SandboxIsolation.CONTAINER)
        is SandboxEnforcement.FULL
    )


def test_provider_backends_report_delegated() -> None:
    for provider in ("cubesandbox", "daytona", "e2b"):
        assert (
            sandbox_enforcement(provider, SandboxIsolation.CONTAINER)
            is SandboxEnforcement.DELEGATED
        )


def test_deferred_suffix_maps_to_backend() -> None:
    assert (
        sandbox_enforcement("cubesandbox-deferred", SandboxIsolation.CONTAINER)
        is SandboxEnforcement.DELEGATED
    )
    assert (
        sandbox_enforcement("kubernetes-deferred", SandboxIsolation.CONTAINER)
        is SandboxEnforcement.FULL
    )


def test_unknown_container_provider_fails_closed_to_none() -> None:
    assert (
        sandbox_enforcement("mystery-provider", SandboxIsolation.CONTAINER)
        is SandboxEnforcement.NONE
    )
