"""Sandbox enforcement fact derivation."""

from harness.sandbox.base import (
    SandboxEnforcement,
    SandboxIsolation,
    provider_meets_enforcement_floor,
    sandbox_enforcement,
    sandbox_enforcement_rank,
    trust_enforcement_floor,
)
from harness.studio.catalog import default_capability_catalog


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


def test_enforcement_ranks_are_ordered() -> None:
    assert (
        sandbox_enforcement_rank(SandboxEnforcement.NONE)
        < sandbox_enforcement_rank(SandboxEnforcement.DELEGATED)
        < sandbox_enforcement_rank(SandboxEnforcement.FULL)
    )


def test_profile_floor_rejects_weaker_backends() -> None:
    # A profile that requires delegated isolation cannot run on the Worker's own
    # filesystem, and an unknown backend can never satisfy a floor.
    assert not provider_meets_enforcement_floor("local", SandboxEnforcement.DELEGATED)
    assert not provider_meets_enforcement_floor(
        "mystery-provider", SandboxEnforcement.DELEGATED
    )

    assert provider_meets_enforcement_floor("cubesandbox", SandboxEnforcement.DELEGATED)
    assert provider_meets_enforcement_floor("local", SandboxEnforcement.NONE)
    assert provider_meets_enforcement_floor("gvisor", SandboxEnforcement.FULL)
    assert provider_meets_enforcement_floor("kubernetes", SandboxEnforcement.FULL)
    assert not provider_meets_enforcement_floor("cubesandbox", SandboxEnforcement.FULL)


def test_default_profiles_declare_a_floor_their_provider_delivers() -> None:
    profiles = {item.profile_id: item for item in default_capability_catalog().execution_profiles}

    assert profiles["local-development"].minimum_enforcement is SandboxEnforcement.NONE
    assert profiles["isolated-default"].minimum_enforcement is SandboxEnforcement.NONE
    assert (
        profiles["e2b-public-egress"].minimum_enforcement
        is SandboxEnforcement.DELEGATED
    )
    assert profiles["gvisor-production"].minimum_enforcement is SandboxEnforcement.FULL
    assert (
        profiles["cubesandbox-private"].minimum_enforcement
        is SandboxEnforcement.DELEGATED
    )

    # Every shipped profile must be self-consistent: declaring a floor its own
    # provider cannot reach would make the profile unusable in production.
    for profile in profiles.values():
        assert provider_meets_enforcement_floor(
            profile.sandbox_provider, profile.minimum_enforcement
        ), f"{profile.profile_id} declares a floor its provider cannot deliver"


def test_trust_high_watermark_sets_the_session_floor() -> None:
    assert trust_enforcement_floor("safe") is SandboxEnforcement.NONE
    assert trust_enforcement_floor("sensitive") is SandboxEnforcement.DELEGATED
    assert trust_enforcement_floor("untrusted") is SandboxEnforcement.FULL


def test_an_unknown_trust_level_fails_closed_to_full() -> None:
    assert trust_enforcement_floor("whatever") is SandboxEnforcement.FULL


def test_a_session_floor_refuses_backends_below_it() -> None:
    floor = trust_enforcement_floor("untrusted")
    assert not provider_meets_enforcement_floor("daytona", floor)
    assert not provider_meets_enforcement_floor("opensandbox-deferred", floor)
    assert not provider_meets_enforcement_floor("local", floor)
    assert provider_meets_enforcement_floor("gvisor", floor)


def test_a_sensitive_session_still_accepts_a_container_backend() -> None:
    floor = trust_enforcement_floor("sensitive")
    assert provider_meets_enforcement_floor("opensandbox", floor)
    assert provider_meets_enforcement_floor("cubesandbox-deferred", floor)
    assert not provider_meets_enforcement_floor("local", floor)
