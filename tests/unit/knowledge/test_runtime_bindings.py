"""The run-level knowledge selection rules every runtime shares."""

from harness.knowledge.models import KnowledgeResultTrust, KnowledgeSnapshotBinding
from harness.knowledge.runtime import (
    knowledge_bindings_for_run,
    knowledge_mode_for_run,
    knowledge_query_tool_name,
    knowledge_result_trust,
)
from harness.policy.models import ContextTrust


def _binding(reference: str, trust: KnowledgeResultTrust) -> KnowledgeSnapshotBinding:
    return KnowledgeSnapshotBinding(
        knowledgeBaseReference=reference,
        sourceReference=reference,
        snapshotId=f"snap-{reference}",
        trust=trust,
    )


def test_run_override_wins_over_session_bindings() -> None:
    session = ({"knowledgeBaseReference": "pinned", "sourceReference": "pinned",
                "snapshotId": "snap-pinned", "trust": "sensitive"},)
    override = [_binding("override", KnowledgeResultTrust.UNTRUSTED)]

    bindings = knowledge_bindings_for_run(
        {
            "knowledge_binding_override": [
                item.model_dump(mode="json", by_alias=True) for item in override
            ]
        },
        session,
    )

    assert [item.knowledge_base_reference for item in bindings] == ["override"]
    assert knowledge_result_trust(bindings) is ContextTrust.UNTRUSTED


def test_session_bindings_apply_when_no_override_and_set_the_trust_floor() -> None:
    bindings = knowledge_bindings_for_run(
        {}, [{"knowledgeBaseReference": "aipolicy", "sourceReference": "aipolicy",
              "snapshotId": "snap-1", "trust": "sensitive"}]
    )
    assert [item.knowledge_base_reference for item in bindings] == ["aipolicy"]
    assert knowledge_result_trust(bindings).value == "sensitive"
    assert knowledge_result_trust(
        [_binding("x", KnowledgeResultTrust.UNTRUSTED)]
    ).value == "untrusted"


def test_mode_defaults_to_rag_and_only_accepts_the_two_modes() -> None:
    assert knowledge_mode_for_run({}) == "rag"
    assert knowledge_mode_for_run({"knowledge_mode": "wiki"}) == "wiki"
    assert knowledge_mode_for_run({"knowledge_mode": "bogus"}) == "rag"


def test_tool_name_is_the_one_the_policy_rules_allow() -> None:
    assert knowledge_query_tool_name() == "mcp__harness-knowledge__query_knowledge_sources"
    assert knowledge_query_tool_name(wiki_mode=True) == "mcp__harness-knowledge__search_wiki_pages"
