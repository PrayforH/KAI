from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.core.models import (
    ExecutionIdentity,
    ProcessedInput,
    ProcessingStatus,
    Run,
    RunStatus,
    Session,
    ThreadFile,
    ThreadFileKind,
    UserMemory,
)
from harness.runtime.base import RuntimeContext
from harness.sandbox.base import SandboxIsolation

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def test_execution_identity_is_immutable_and_contains_run_scope() -> None:
    identity = ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-a",
        project_id="project-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="research-agent",
        agent_version="1.2.0",
    )

    assert identity.model_dump() == {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "agent_owner_user_id": None,
        "team_ids": (),
        "project_id": "project-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_name": "research-agent",
        "agent_version": "1.2.0",
        "connection_mode": "caller_owned",
    }
    with pytest.raises(ValidationError):
        identity.run_id = "another-run"  # type: ignore[misc]


def test_memory_and_processed_input_capture_version_and_lineage() -> None:
    memory = UserMemory(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="research-agent",
        content="Prefer concise Chinese reports.",
        version=3,
        updated_at=NOW,
    )
    source = ThreadFile(
        file_id="file-source",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        kind=ThreadFileKind.ORIGINAL,
        name="report.pdf",
        media_type="application/pdf",
        path="inputs/original/report.pdf",
        created_at=NOW,
        input_artifact_id="input-a",
    )
    derived = ThreadFile(
        file_id="file-derived",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        kind=ThreadFileKind.DERIVED,
        name="report.md",
        media_type="text/markdown",
        path="inputs/processed/report/report.md",
        created_at=NOW,
        parent_file_id=source.file_id,
    )
    processed = ProcessedInput(
        source_file_id=source.file_id,
        status=ProcessingStatus.PROCESSED,
        derived_file_ids=(derived.file_id,),
        processor="pdf",
    )

    assert memory.version == 3
    assert derived.parent_file_id == source.file_id
    assert processed.derived_file_ids == (derived.file_id,)


def test_runtime_context_derives_identity_and_hides_sensitive_runtime_state(
    tmp_path: Path,
) -> None:
    session = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="research-agent",
        agent_version="1.2.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-a",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.RUNNING,
        idempotency_key="idem-a",
        created_at=NOW,
        updated_at=NOW,
    )

    def transport_factory(_options: object) -> object:
        return object()

    context = RuntimeContext(
        run=run,
        session=session,
        workspace=tmp_path,
        memory_projection="secret preference",
        processed_input_paths=("inputs/processed/report/report.md",),
        runtime_transport_factory=transport_factory,
    )

    assert context.identity is not None
    assert context.identity.run_id == run.run_id
    assert context.identity.project_id == session.agent_name
    assert context.runtime_transport_factory is transport_factory
    dumped = context.model_dump(mode="json")
    assert "memory_projection" not in dumped
    assert "runtime_transport_factory" not in dumped
    assert "secret preference" not in str(dumped)


def test_runtime_context_rejects_unknown_sandbox_isolation(tmp_path: Path) -> None:
    session = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="research-agent",
        agent_version="1.2.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-a",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.RUNNING,
        idempotency_key="idem-a",
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(ValidationError):
        RuntimeContext(
            run=run,
            session=session,
            workspace=tmp_path,
            sandbox_isolation="virtual-machine",  # type: ignore[arg-type]
        )

    context = RuntimeContext(run=run, session=session, workspace=tmp_path)
    assert context.sandbox_isolation is SandboxIsolation.WORKSPACE
