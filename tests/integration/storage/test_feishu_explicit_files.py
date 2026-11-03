from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from harness.assistant.file_delivery import ChannelFileRow, ChannelFiles
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Artifact, ArtifactStatus
from harness.feishu.file_transport import FeishuFileTransport
from harness.feishu.storage import BindingRow
from harness.studio.mcp_credential_store import McpCredentialCipher
from tests.integration.storage.test_feishu_channel import bind, setup


async def configured(database):
    service, _, _ = setup(database)
    binding_id = await bind(service.repository)
    first = Artifact(
        artifact_id="html",
        run_id="old-web-run",
        tenant_id="tenant-a",
        name="report.html",
        media_type="text/html",
        status=ArtifactStatus.READY,
        object_key="private",
    )
    neighbor = first.model_copy(update={"artifact_id": "neighbor"})
    artifacts = SimpleNamespace(
        get=AsyncMock(return_value=first),
        list_for_run=AsyncMock(return_value=[first, neighbor]),
        download=AsyncMock(return_value=(first, b"<html>report</html>")),
    )
    service.conversation.runs.get.return_value = SimpleNamespace(session_id="old-session")
    service.conversation.agui.get_thread_record_for_session.return_value = SimpleNamespace(
        thread_id="personal",
    )
    transport = FeishuFileTransport(service)
    transport.upload = AsyncMock(return_value={"file_key": "private-upload"})
    transport.send = AsyncMock(return_value="om-confirmed")
    service.files = ChannelFiles(
        database[1], artifacts, McpCredentialCipher(SecretStr("test")), transport
    )
    return service, artifacts, transport, binding_id


@pytest.mark.asyncio
async def test_explicit_historical_html_sends_only_selected_file_and_keeps_receipt(database):
    service, _, transport, _ = await configured(database)
    receipt = await service.queue_artifact(
        "tenant-a", "user-a", "html", "send-1", thread_id="personal"
    )
    assert receipt["status"] == "pending"
    assert await service.files.deliver_once()
    assert await service.files.deliver_once()
    repeated = await service.queue_artifact("tenant-a", "user-a", "html", "send-1")
    assert repeated["deliveryId"] == receipt["deliveryId"]
    assert repeated["status"] == "sent" and repeated["messageId"] == "om-confirmed"
    assert not await service.files.deliver_once()
    async with database[1]() as session:
        assert await session.scalar(select(func.count()).select_from(ChannelFileRow)) == 1
    transport.upload.assert_awaited_once()
    transport.send.assert_awaited_once()
    assert (await service.file_receipt("tenant-a", "user-a", receipt["deliveryId"]))[
        "status"
    ] == "sent"
    with pytest.raises(NotFoundError):
        await service.file_receipt("tenant-a", "other-user", receipt["deliveryId"])


@pytest.mark.asyncio
async def test_owner_thread_and_idempotency_cannot_be_retargeted(database):
    service, artifacts, _, _ = await configured(database)
    with pytest.raises(PermissionDeniedError):
        await service.queue_artifact("tenant-a", "user-a", "html", "one", thread_id="other-thread")
    await service.queue_artifact("tenant-a", "user-a", "html", "one", thread_id="personal")
    artifacts.get.return_value = artifacts.get.return_value.model_copy(
        update={"artifact_id": "neighbor"}
    )
    with pytest.raises(ConflictError):
        await service.queue_artifact("tenant-a", "user-a", "neighbor", "one")
    service.conversation.agui.get_thread_record_for_session.side_effect = PermissionDeniedError(
        "denied"
    )
    with pytest.raises(PermissionDeniedError):
        await service.queue_artifact("tenant-a", "other-user", "html", "new")


@pytest.mark.asyncio
async def test_binding_revoked_after_upload_prevents_external_send(database):
    service, _, transport, binding_id = await configured(database)
    receipt = await service.queue_artifact("tenant-a", "user-a", "html", "one")
    await service.files.deliver_once()
    async with database[1].begin() as session:
        binding = await session.get(BindingRow, binding_id)
        binding.revoked_at = datetime.now(UTC)
    await service.files.deliver_once()
    assert (await service.file_receipt("tenant-a", "user-a", receipt["deliveryId"]))[
        "status"
    ] == "skipped"
    transport.send.assert_not_awaited()
