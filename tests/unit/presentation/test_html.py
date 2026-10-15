"""A frame must refer to one bounded, authoritative file from its owning run."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from harness.core.events import RunEvent
from harness.presentation.projection import presentations_for_events
from harness.presentation.schema import MAX_HTML_BYTES, validate_block

CONTENT = b"<!doctype html><html><body>fixture</body></html>"
BLOCK = {
    "version": 1,
    "kind": "html",
    "fallback_text": "An interactive fixture.",
    "data": {"artifact_id": "artifact_html"},
}
ARTIFACT = {
    "artifact_id": "artifact_html",
    "tenant_id": "tenant-a",
    "run_id": "run-1",
    "name": "tool.html",
    "media_type": "text/html",
    "status": "ready",
    "object_key": "fixture",
    "sha256": hashlib.sha256(CONTENT).hexdigest(),
    "size_bytes": len(CONTENT),
}


def event(kind: str, sequence: int, payload: dict[str, Any]) -> RunEvent:
    return RunEvent(
        event_id=f"e-{sequence}",
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
        timestamp=datetime(2026, 10, 12, tzinfo=UTC),
        type=kind,
        sequence=sequence,
        payload=payload,
    )


def events(artifact: dict[str, Any] | None = None) -> list[RunEvent]:
    answer = "```harness-ui\n" + json.dumps(BLOCK) + "\n```"
    return [
        event("message.delta", 1, {"text": answer}),
        event("artifact.ready", 2, artifact or ARTIFACT),
        event("message.completed", 3, {}),
    ]


def test_joins_the_model_reference_to_the_owning_artifact_and_freezes_its_hash():
    result = presentations_for_events(events())
    assert len(result) == 1
    p = result[0]
    assert p.data == {
        "artifact_id": "artifact_html",
        "name": "tool.html",
        "sha256": ARTIFACT["sha256"],
        "size_bytes": len(CONTENT),
    }
    assert p.source_refs[-1] == {"run_id": "run-1", "sequence": 2, "origin": "artifact"}
    assert p.snapshot_hash and p.decision["rule_version"] == "presentation-policy/8"
    assert presentations_for_events(events())[0] == p


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "other"},
        {"tenant_id": "other"},
        {"status": "pending"},
        {"media_type": "text/plain"},
        {"sha256": None},
        {"size_bytes": MAX_HTML_BYTES + 1},
    ],
)
def test_rejects_unready_foreign_unbounded_or_unhashed_files(change: dict[str, Any]):
    assert presentations_for_events(events({**ARTIFACT, **change})) == []


def test_never_uses_a_tool_result_as_authoritative_artifact_metadata():
    e = events()
    e[1] = event("tool.result", 2, {"content": json.dumps(ARTIFACT)})
    assert presentations_for_events(e) == []


def test_ignores_an_artifact_event_from_another_session():
    e = events()
    e[1] = e[1].model_copy(update={"session_id": "other"})
    assert presentations_for_events(e) == []


def test_rejects_conflicting_metadata_for_the_same_artifact():
    e = events()
    e.append(event("artifact.ready", 4, {**ARTIFACT, "sha256": "f" * 64}))
    assert presentations_for_events(e) == []


def test_accepts_the_observed_flat_reference_only_with_authoritative_ready_metadata():
    flat = {k: v for k, v in BLOCK.items() if k != "data"}
    flat["artifact_id"] = "artifact_html"
    e = events()
    e[0] = event("message.delta", 1, {"text": "```harness-ui\n" + json.dumps(flat) + "\n```"})
    p = presentations_for_events(e)[0]
    assert p.data["artifact_id"] == "artifact_html"
    assert p.data["sha256"] == ARTIFACT["sha256"]
    assert presentations_for_events([e[0], e[2]]) == []


@pytest.mark.parametrize(
    "extra",
    [
        {"data": {"artifact_id": "artifact_html"}},
        {"data": {"artifact_id": "other"}},
        {"html": "<script>bad</script>"},
        {"sha256": "f" * 64},
    ],
)
def test_flat_reference_does_not_allow_duplicate_references_or_claimed_content(
    extra: dict[str, Any],
):
    flat = {k: v for k, v in BLOCK.items() if k != "data"}
    flat.update(artifact_id="artifact_html", **extra)
    assert (
        validate_block(
            flat,
            run_id="run-1",
            message_id="m",
            index=0,
            origin="model",
            reason_code="model_selected",
        )
        is None
    )


@pytest.mark.parametrize(
    "data,config",
    [
        ({"artifact_id": "https://example.com/tool.html"}, {}),
        ({"artifact_id": "artifact_html", "html": "<script>bad</script>"}, {}),
        ({"artifact_id": "artifact_html", "sha256": "f" * 64}, {}),
        ({"artifact_id": "artifact_html"}, {"height": True}),
        ({"artifact_id": "artifact_html"}, {"height": 901}),
        ({"artifact_id": "artifact_html"}, {"sandbox": "allow-same-origin"}),
    ],
)
def test_the_model_cannot_author_markup_metadata_urls_or_permissions(
    data: dict[str, Any], config: dict[str, Any]
) -> None:
    assert (
        validate_block(
            {**BLOCK, "data": data, "config": config},
            run_id="run-1",
            message_id="assistant-run-1",
            index=0,
            origin="model",
            reason_code="model_selected",
        )
        is None
    )
