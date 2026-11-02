from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from harness.assistant.file_delivery import ChannelFileRow, ChannelFiles
from harness.core.models import Artifact, ArtifactStatus
from harness.feishu.media import MediaPermissionError, MediaRejectedError
from harness.studio.mcp_credential_store import McpCredentialCipher


def setup(database, *, retry_send=True):
    artifact = Artifact(
        artifact_id="a",
        run_id="run",
        tenant_id="tenant",
        name="报告.txt",
        media_type="text/plain",
        status=ArtifactStatus.READY,
        object_key="key",
    )
    artifacts = SimpleNamespace(
        list_for_run=AsyncMock(return_value=[artifact]),
        get=AsyncMock(return_value=artifact),
        download=AsyncMock(return_value=(artifact, b"report")),
    )
    transport = SimpleNamespace(
        channel="feishu",
        retry_send=retry_send,
        route_prefix="app:",
        target=AsyncMock(return_value="owner"),
        upload=AsyncMock(return_value={"file_key": "private-key"}),
        send=AsyncMock(),
    )
    worker = ChannelFiles(
        database[1], artifacts, McpCredentialCipher(SecretStr("test-key")), transport
    )
    return worker, artifacts, transport


async def enqueue(worker):
    async with worker.sessions.begin() as session:
        await worker.enqueue(
            session,
            tenant_id="tenant",
            user_id="alice",
            source_id="msg",
            route="app:binding",
            run_id="run",
        )


async def row(worker):
    async with worker.sessions() as session:
        return await session.scalar(select(ChannelFileRow))


@pytest.mark.asyncio
async def test_durable_upload_and_send_are_idempotent_and_do_not_rerun(database):
    worker, artifacts, transport = setup(database)
    transport.send.return_value = "om-file-receipt"
    await enqueue(worker)
    await enqueue(worker)
    assert await worker.deliver_once()
    stored = await row(worker)
    assert stored.status == "ready" and "private-key" not in stored.ciphertext
    # Restart after upload: the saved encrypted reference is reused.
    restarted = ChannelFiles(worker.sessions, artifacts, worker.cipher, transport)
    assert await restarted.deliver_once()
    assert (await row(worker)).status == "sent"
    assert (await row(worker)).message_id == "om-file-receipt"
    assert not await restarted.deliver_once()
    transport.upload.assert_awaited_once()
    transport.send.assert_awaited_once_with(
        "owner", {"file_key": "private-key"}, stored.delivery_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("after_upload", [False, True])
async def test_revocation_prevents_upload_or_send(database, after_upload):
    worker, _, transport = setup(database)
    await enqueue(worker)
    if after_upload:
        await worker.deliver_once()
    transport.target.return_value = None
    await worker.deliver_once()
    assert (await row(worker)).status == "skipped"
    assert transport.upload.await_count == int(after_upload)
    transport.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_send,expected", [(True, "sent"), (False, "uncertain")])
async def test_interrupted_send_respects_platform_dedup_contract(database, retry_send, expected):
    worker, _, transport = setup(database, retry_send=retry_send)
    await enqueue(worker)
    await worker.deliver_once()
    async with worker.sessions.begin() as session:
        stored = await session.scalar(select(ChannelFileRow))
        stored.status = "sending"
        stored.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await worker.deliver_once()
    assert (await row(worker)).status == expected
    assert transport.send.await_count == int(retry_send)


@pytest.mark.asyncio
async def test_upload_failure_retries_without_plaintext_or_duplicate_send(database):
    worker, _, transport = setup(database)
    await enqueue(worker)
    transport.upload.side_effect = ConnectionError("private-token")
    instant = datetime.now(UTC)
    await worker.deliver_once(instant)
    stored = await row(worker)
    assert stored.status == "pending" and stored.error_code == "UploadUnavailable"
    assert "private-token" not in str(stored.__dict__)
    assert not await worker.deliver_once(instant)
    transport.upload.side_effect = None
    await worker.deliver_once(instant + timedelta(seconds=5))
    await worker.deliver_once(instant + timedelta(seconds=5))
    assert (await row(worker)).status == "sent"
    transport.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_file_and_cross_run_artifact_are_not_sent(database):
    worker, artifacts, transport = setup(database)
    await enqueue(worker)
    artifacts.get.return_value = artifacts.get.return_value.model_copy(update={"run_id": "other"})
    await worker.deliver_once()
    assert (await row(worker)).error_code == "FileRejected"
    transport.upload.assert_not_awaited()
    transport.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_definite_rejection_does_not_loop_forever(database):
    worker, _, transport = setup(database)
    await enqueue(worker)
    transport.upload.side_effect = MediaRejectedError("too big")
    await worker.deliver_once()
    assert (await row(worker)).status == "failed"
    assert not await worker.deliver_once()


@pytest.mark.asyncio
async def test_permission_block_waits_for_explicit_owner_retry(database):
    worker, _, transport = setup(database)
    await enqueue(worker)
    transport.upload.side_effect = MediaPermissionError("upload permission required")
    await worker.deliver_once()
    assert (await row(worker)).status == "blocked"
    assert not await worker.deliver_once()
    assert await worker.retry_failed("tenant", "bob") == 0
    assert await worker.retry_failed("tenant", "alice") == 1
    transport.upload.side_effect = None
    await worker.deliver_once()
    await worker.deliver_once()
    assert (await row(worker)).status == "sent"
    # An unknown Weixin receipt must never be blindly sent again via this action.
    async with worker.sessions.begin() as session:
        stored = await session.scalar(select(ChannelFileRow))
        stored.status = "uncertain"
    assert await worker.retry_failed("tenant", "alice") == 0
