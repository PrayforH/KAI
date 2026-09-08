import pytest

from harness.policy.models import PolicyContext, PolicyDecision
from harness.policy.profiles import (
    UnknownPolicyProfileError,
    default_policy_profiles,
)
from harness.sandbox.base import SandboxIsolation


def _decision(
    profile: str,
    tool: str,
    *,
    isolation: SandboxIsolation = SandboxIsolation.WORKSPACE,
) -> PolicyDecision:
    engine = default_policy_profiles().resolve(profile)
    return engine.evaluate(
        PolicyContext(
            tenant_id="tenant-a",
            agent_name="invoice-reviewer",
            tool_name=tool,
            sandbox_isolation=isolation,
        )
    ).decision


def test_read_only_profile_allows_evidence_tools_and_denies_mutation() -> None:
    assert _decision("production-read-only", "Read") is PolicyDecision.ALLOW
    assert _decision("production-read-only", "mcp__tavily__tavily_search") is PolicyDecision.ALLOW
    assert (
        _decision("production-read-only", "mcp__novel-search__sag_search")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__novel-search__sag_explain_search")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__novel-search__sag_get_event")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__novel-search__sag_get_document")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__novel-search__sag_list_chunks")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__knowledge-search__sag_explain_search")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__knowledge-search__sag_get_document")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__knowledge-search__sag_search")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__knowledge-search__sag_get_event")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__knowledge-search__sag_list_chunks")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-read-only", "mcp__novel-search__sag_ingest_document")
        is PolicyDecision.DENY
    )
    assert (
        _decision("production-read-only", "mcp__knowledge-search__sag_ingest_document")
        is PolicyDecision.DENY
    )
    assert _decision("production-read-only", "Write") is PolicyDecision.DENY
    assert _decision("production-read-only", "Bash") is PolicyDecision.DENY
    assert (
        _decision("production-read-only", "mcp__harness-memory__propose_memory")
        is PolicyDecision.DENY
    )


def test_standard_profile_uses_trusted_sandbox_facts() -> None:
    assert _decision("production-standard", "Write") is PolicyDecision.ALLOW
    assert (
        _decision(
            "production-standard",
            "Write",
            isolation=SandboxIsolation.CONTAINER,
        )
        is PolicyDecision.ALLOW
    )
    assert _decision("production-standard", "Bash") is PolicyDecision.ALLOW
    assert (
        _decision("production-standard", "mcp__novel-search__sag_get_document")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-standard", "mcp__novel-search__sag_list_chunks")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-standard", "mcp__knowledge-search__sag_explain_search")
        is PolicyDecision.ALLOW
    )
    assert (
        _decision(
            "production-standard",
            "mcp__sentiment_query_mcp__search_risk_subjects",
        )
        is PolicyDecision.ALLOW
    )
    assert (
        _decision("production-standard", "mcp__sentiment_query_mcp__unknown")
        is PolicyDecision.DENY
    )


def test_orchestrator_profile_allows_explicit_delegation() -> None:
    assert _decision("production-orchestrator", "Task") is PolicyDecision.ALLOW
    assert _decision("production-orchestrator", "Agent") is PolicyDecision.ALLOW


def test_local_standard_remains_a_backward_compatible_alias() -> None:
    profiles = default_policy_profiles()

    assert profiles.resolve("local-standard") is profiles.resolve("production-standard")


def test_unknown_policy_profile_fails_closed() -> None:
    with pytest.raises(UnknownPolicyProfileError, match="unknown permission policy"):
        default_policy_profiles().resolve("manifest-self-grant")
