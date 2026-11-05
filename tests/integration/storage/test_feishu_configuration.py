import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from harness.api.app import create_memory_app
from harness.api.dependencies import Identity, require_identity
from harness.core.errors import ConflictError, NotFoundError
from harness.feishu.configuration import ConfigureConnection, FeishuConnectionControl
from harness.feishu.manager import supervise
from harness.feishu.models import FeishuSettings
from harness.feishu.repository import FeishuRepository
from harness.feishu.storage import BindingRow, ConnectionRow, DeliveryRow, IncomingRow
from harness.studio.mcp_credential_store import McpCredentialCipher
from tests.integration.storage.test_feishu_channel import setup

NOW = datetime.now(UTC)
LEGACY = FeishuSettings(
    enabled=True,
    app_id="cli_test",
    app_secret=SecretStr("private-test-secret"),
    vision_model_route="deepseek-v4-flash",
    receiver_label="测试接收器",
)


def control(database, response=None):
    transport = httpx.MockTransport(
        lambda _: (
            response
            or httpx.Response(200, json={"code": 0, "tenant_access_token": "private-test-token"})
        )
    )
    return FeishuConnectionControl(
        database[1], McpCredentialCipher(SecretStr("cipher-key")), LEGACY, transport=transport
    )


def request(revision, **changes):
    return ConfigureConnection(expectedRevision=revision, appId="cli_test", **changes)


async def bind(database, user="alice"):
    async with database[1].begin() as session:
        session.add(
            BindingRow(
                binding_id="binding-" + user,
                app_id="cli_test",
                tenant_key="ft",
                open_id="ou-" + user,
                tenant_id="tenant",
                user_id=user,
                notifications=True,
                created_at=NOW,
            )
        )


@pytest.mark.asyncio
async def test_import_encrypts_preserves_identity_and_is_idempotent(database):
    c = control(database)
    await bind(database)
    before = await c.get("tenant")
    assert before.source == "environment" and before.revision == 0
    first = await c.import_legacy("tenant", "admin")
    second = await c.import_legacy("tenant", "admin")
    assert first == second and first.revision == 1
    assert "private-test-secret" not in first.model_dump_json()
    runtime = await c.resolve("tenant")
    assert runtime.app_secret.get_secret_value() == "private-test-secret"
    assert runtime.vision_model_route == "deepseek-v4-flash"
    async with database[1]() as session:
        row = await session.get(ConnectionRow, "primary")
        assert row and "private-test-secret" not in row.ciphertext
        binding = await session.get(BindingRow, "binding-alice")
        assert binding and binding.revoked_at is None
    assert not (await c.resolve("other-tenant")).enabled
    with pytest.raises(NotFoundError):
        await c.get("other-tenant")


@pytest.mark.asyncio
async def test_disconnect_and_blank_secret_reconnect_preserve_binding(database):
    c = control(database)
    await bind(database)
    await c.import_legacy("tenant", "admin")
    disabled = await c.disconnect("tenant", "admin", 1)
    assert disabled.revision == 2 and not disabled.enabled
    await c.configure("tenant", "admin", request(2))
    restored = await c.resolve("tenant")
    assert restored.enabled and restored.configuration_revision == 3
    assert restored.app_secret.get_secret_value() == "private-test-secret"
    async with database[1]() as session:
        binding = await session.get(BindingRow, "binding-alice")
        assert binding and binding.revoked_at is None
    with pytest.raises(ConflictError):
        await c.configure(
            "tenant",
            "admin",
            ConfigureConnection(
                expectedRevision=3, appId="cli_another", appSecret="another-secret"
            ),
        )


@pytest.mark.asyncio
async def test_failed_auth_and_concurrent_writes_do_not_replace_configuration(database):
    c = control(database)
    await c.import_legacy("tenant", "admin")
    invalid = control(database, httpx.Response(200, json={"code": 1, "msg": "private-data"}))
    with pytest.raises(ConflictError, match="无法验证"):
        await invalid.configure("tenant", "admin", request(1, appSecret="wrong"))
    assert (await c.get("tenant")).revision == 1
    results = await asyncio.gather(
        c.configure("tenant", "admin", request(1)),
        c.configure("tenant", "admin", request(1)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    assert (await c.get("tenant")).revision == 2


@pytest.mark.asyncio
async def test_api_admin_boundary_and_validation_never_echo_secret(database):
    app = create_memory_app()
    c = control(database)
    app.state.container = replace(app.state.container, feishu_connections=c)
    identity = Identity("tenant", "admin", frozenset({"owner"}))
    app.dependency_overrides[require_identity] = lambda: identity
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.put(
            "/v1/studio/assistant/feishu/connection",
            json={
                "expectedRevision": 0,
                "appId": "cli_test",
                "appSecret": {"secret": "do-not-echo"},
            },
        )
        assert invalid.status_code == 422 and "do-not-echo" not in invalid.text
        identity = Identity("tenant", "member", frozenset({"member"}))
        denied = await client.put(
            "/v1/studio/assistant/feishu/connection",
            json={
                "expectedRevision": 0,
                "appId": "cli_test",
            },
        )
        assert denied.status_code == 403
        view = await client.get("/v1/studio/assistant/feishu/connection")
        assert view.status_code == 200 and not view.json()["canManage"]
        identity = Identity("tenant", "admin", frozenset({"owner"}))
        saved = await client.put(
            "/v1/studio/assistant/feishu/connection",
            json={
                "expectedRevision": 0,
                "appId": "cli_test",
            },
        )
        assert saved.status_code == 200 and saved.json()["canManage"]
        assert "private-test-secret" not in saved.text and "ciphertext" not in saved.text


@pytest.mark.asyncio
async def test_diagnostics_distinguish_live_socket_from_process_and_isolate_user(database):
    c = control(database)
    await c.import_legacy("tenant", "admin")
    service, _, _ = setup(database)
    service.repository = FeishuRepository(database[1], "cli_test")
    service.settings = await c.resolve("tenant")
    await bind(database)
    await bind(database, "bob")
    # Receiver liveness must use the actual clock: this module can run after
    # more than the 45-second TTL in the complete channel regression suite.
    await service.repository.heartbeat(
        "receiver", "connecting", datetime.now(UTC), configuration_revision=1
    )
    async with database[1].begin() as session:
        session.add(
            IncomingRow(
                incoming_id="media-bob",
                app_id="cli_test",
                actor_key="bob",
                event_key="bob-media",
                payload={"prepared": {"artifacts": [{"id": "private-bob"}]}},
                binding_id="binding-bob",
                status="done",
                attempts=0,
                created_at=NOW,
                next_attempt_at=NOW,
            )
        )
    checks = {v.key: v for v in (await c.diagnose("tenant", "alice", service)).checks}
    assert checks["credentials"].status == "pass"
    assert checks["receiver"].status == "pending" and checks["events"].status == "pending"
    assert checks["attachments"].status == "pending"
    await service.repository.heartbeat(
        "receiver", "awaiting_event", datetime.now(UTC), configuration_revision=1
    )
    checks = {v.key: v for v in (await c.diagnose("tenant", "alice", service)).checks}
    assert checks["receiver"].status == "pass"
    key = await c.send_test("tenant", "alice", service)
    assert (await c.test_status("tenant", "alice", service, key))["status"] == "pending"
    with pytest.raises(NotFoundError):
        await c.test_status("tenant", "bob", service, key)
    async with database[1]() as session:
        row = await session.scalar(select(DeliveryRow).where(DeliveryRow.dedup_key == key))
        assert row and row.binding_id == "binding-alice"
        assert await session.scalar(select(func.count()).select_from(DeliveryRow)) == 1


@pytest.mark.asyncio
async def test_supervisor_applies_saved_revision_without_overlapping_children(
    database, monkeypatch
):
    c = control(database)
    await c.import_legacy("tenant", "admin")
    children = []
    payloads = []

    async def spawn(*args, **kwargs):
        assert "private-test-secret" not in str(args)
        assert "HARNESS_FEISHU_APP_SECRET" not in kwargs["env"]
        assert not children or children[-1].returncode is not None
        child = SimpleNamespace(returncode=None)
        child.terminate = Mock(side_effect=lambda: setattr(child, "returncode", 0))
        child.wait = AsyncMock(return_value=0)
        child.stdin = SimpleNamespace(
            write=lambda b: payloads.append(json.loads(b)), drain=AsyncMock(), close=Mock()
        )
        children.append(child)
        return child

    monkeypatch.delenv("HARNESS_FEISHU_APP_SECRET", raising=False)
    monkeypatch.setattr("harness.feishu.manager.asyncio.create_subprocess_exec", spawn)
    stop = asyncio.Event()
    task = asyncio.create_task(supervise(c, stop, poll_seconds=0.02))

    async def wait_for(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.02)

    try:
        await wait_for(lambda: len(children) == 1)
        assert payloads[0]["app_secret"] == "private-test-secret"
        await c.configure("tenant", "admin", request(1))
        await wait_for(lambda: len(children) == 2)
        assert payloads[1]["configuration_revision"] == 2
        await c.disconnect("tenant", "admin", 2)
        await wait_for(lambda: children[-1].returncode is not None)
        assert len(children) == 2
    finally:
        stop.set()
        await task
