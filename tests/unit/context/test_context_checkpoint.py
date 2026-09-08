from datetime import UTC, datetime

import pytest

from harness.context.checkpoint import (
    ContextCheckpointService,
    TranscriptCheckpoint,
)
from harness.context.repositories import InMemoryContextRepository
from harness.context.service import ContextService
from harness.core.events import RunEvent
from harness.core.models import Run, RunStatus, Session
from harness.policy.models import ContextTrust

NOW = datetime(2026, 8, 9, tzinfo=UTC)


class StubTranscriptCheckpoints:
    async def checkpoint(
        self,
        tenant_id: str,
        project_id: str,
        sdk_session_id: str,
    ) -> TranscriptCheckpoint | None:
        assert (tenant_id, project_id, sdk_session_id) == (
            "tenant-a",
            "session-a",
            "sdk-session-a",
        )
        return TranscriptCheckpoint(
            sdk_session_id_hash=f"sha256:{'a' * 64}",
            transcript_checkpoint_hash=f"sha256:{'b' * 64}",
            entry_count=4,
        )


def _event(sequence: int, event_type: str, payload: dict[str, object]) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence}",
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        sequence=sequence,
        type=event_type,
        timestamp=NOW,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_run_checkpoint_projects_final_answer_and_durable_object_references() -> None:
    contexts = ContextService(
        InMemoryContextRepository(),
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-a",
    )
    await contexts.promote_trust(
        "tenant-a",
        "owner-a",
        "session-a",
        ContextTrust.UNTRUSTED,
    )
    service = ContextCheckpointService(contexts, StubTranscriptCheckpoints())
    session = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="owner-a",
        agent_name="assistant",
        agent_version="1.0.0",
        claude_session_id="sdk-session-a",
        created_at=NOW,
    )
    run = Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="run-a",
        created_at=NOW,
        updated_at=NOW,
    )
    events = [
        _event(3, "message.completed", {"message_id": "message-a"}),
        _event(
            4,
            "artifact.ready",
            {
                "artifact_id": "artifact-a",
                "name": "report.md",
                "media_type": "text/markdown",
                "sha256": "1" * 64,
            },
        ),
        _event(
            5,
            "workspace.archived",
            {"snapshot_id": "snapshot-a", "sha256": "2" * 64},
        ),
    ]

    digest = await service.checkpoint_run(
        session=session,
        run=run,
        events=events,
        final_response="Use API token=private-value and inspect report.md",
    )

    assert digest is not None
    assert digest.source.through_event_sequence == 5
    assert digest.trust_high_watermark is ContextTrust.UNTRUSTED
    assert digest.facts[0].source_refs == ("run:run-a:event:3",)
    assert "private-value" not in digest.facts[0].text
    assert digest.artifact_refs[0].ref == "artifact:artifact-a"
    assert digest.artifact_refs[0].content_hash == f"sha256:{'1' * 64}"
    assert digest.workspace_refs[0].ref == "workspace:snapshot-a"


@pytest.mark.asyncio
async def test_codex_checkpoint_builds_durable_recovery_without_claude_transcript() -> None:
    contexts = ContextService(
        InMemoryContextRepository(),
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-a",
    )
    service = ContextCheckpointService(contexts, StubTranscriptCheckpoints())
    session = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="owner-a",
        agent_name="assistant",
        agent_version="1.0.0",
        runtime_type="codex-app-server",
        runtime_thread_id="codex-thread-a",
        created_at=NOW,
    )
    run = Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="run-a",
        input={"prompt": "remember the selected company"},
        created_at=NOW,
        updated_at=NOW,
    )

    digest = await service.checkpoint_run(
        session=session,
        run=run,
        events=[_event(3, "message.completed", {"message_id": "message-a"})],
        final_response="Selected company is Example Corp.",
    )

    assert digest is not None
    assert digest.created_by.route_id == "event-projection-v1"
    assert digest.source.sdk_session_id_hash.startswith("sha256:")
    assert [entry.text for entry in digest.facts] == [
        "用户请求：remember the selected company",
        "助手结果：Selected company is Example Corp.",
    ]
    projection = await contexts.recovery_projection(
        "tenant-a", "owner-a", "session-a"
    )
    assert "remember the selected company" in projection
    assert "Selected company is Example Corp." in projection


@pytest.mark.asyncio
async def test_run_checkpoint_skips_session_without_sdk_resume_identity() -> None:
    contexts = ContextService(
        InMemoryContextRepository(),
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-a",
    )
    service = ContextCheckpointService(contexts, StubTranscriptCheckpoints())
    session = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="owner-a",
        agent_name="assistant",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="run-a",
        created_at=NOW,
        updated_at=NOW,
    )

    assert (
        await service.checkpoint_run(
            session=session,
            run=run,
            events=[],
            final_response="done",
        )
        is None
    )
