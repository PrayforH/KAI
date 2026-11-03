import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.artifact_delivery import assistant_file_overlay
from harness.core.models import Artifact, ArtifactStatus, Run, RunStatus, Session


@pytest.mark.asyncio
async def test_historical_file_tool_lists_routes_and_returns_actual_receipts_without_credentials():
    now = datetime.now(UTC)
    run = Run(
        run_id="current",
        tenant_id="t",
        session_id="s",
        status=RunStatus.RUNNING,
        idempotency_key="one",
        created_at=now,
        updated_at=now,
    )
    session = Session(
        session_id="s",
        tenant_id="t",
        user_id="u",
        agent_name="a",
        agent_version="1",
        created_at=now,
    )
    runs = SimpleNamespace(
        get=AsyncMock(return_value=run), list_for_sessions=AsyncMock(return_value=[run])
    )
    agui = SimpleNamespace(
        get_thread_record_for_session=AsyncMock(
            return_value=SimpleNamespace(
                thread_id="main",
                session_ids=["s"],
            )
        )
    )
    file = Artifact(
        artifact_id="html",
        run_id="current",
        tenant_id="t",
        name="report.html",
        media_type="text/html",
        status=ArtifactStatus.READY,
        object_key="SECRET_STORE_KEY",
    )
    artifacts = SimpleNamespace(
        list_for_runs=AsyncMock(return_value=[file]), get=AsyncMock(return_value=file)
    )
    feishu = SimpleNamespace(
        authorize=AsyncMock(),
        status=AsyncMock(return_value=SimpleNamespace(bound=True, enabled=True)),
        queue_artifact=AsyncMock(return_value={"deliveryId": "file-1", "status": "pending"}),
        file_receipt=AsyncMock(return_value={"artifactId": "html", "status": "sent"}),
    )
    overlay = await assistant_file_overlay(
        artifacts, runs, agui, AsyncMock(return_value=feishu), run, session
    )
    handler = overlay.tools[0].handler
    listed = json.loads((await handler({"action": "list"}))["content"][0]["text"])
    assert listed["files"][0]["artifactId"] == "html"
    assert listed["destination"]["bound"] and "SECRET" not in str(listed)
    sent = await handler({"action": "send", "artifact_id": "html", "request_key": "send-1"})
    assert '"pending"' in sent["content"][0]["text"]
    feishu.queue_artifact.assert_awaited_once_with("t", "u", "html", "send-1", thread_id="main")
    assert (
        '"sent"'
        in (await handler({"action": "status", "delivery_id": "file-1"}))["content"][0]["text"]
    )
    runs.get.return_value = run.model_copy(update={"status": RunStatus.SUCCEEDED})
    assert (await handler({"action": "send", "artifact_id": "html", "request_key": "two"}))[
        "isError"
    ]
    feishu.queue_artifact.assert_awaited_once()
