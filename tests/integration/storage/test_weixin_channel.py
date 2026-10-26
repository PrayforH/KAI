import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from harness.api.app import create_memory_app
from harness.api.dependencies import Identity, require_identity
from harness.core.errors import ConflictError, PermissionDeniedError
from harness.studio.mcp_credential_store import McpCredentialCipher
from harness.weixin.client import WeixinClient, WeixinError, trusted_base
from harness.weixin.service import WeixinService, now
from harness.weixin.storage import WeixinConnectionRow as Connection
from harness.weixin.storage import WeixinMessageRow as Message


def setup(database):
    client = SimpleNamespace(
        qr=AsyncMock(
            return_value={
                "qrcode": "private-qr",
                "qrcode_img_content": "https://weixin.qq.com/qr-test",
            }
        ),
        qr_status=AsyncMock(
            return_value={
                "status": "confirmed",
                "bot_token": "private-token",
                "ilink_bot_id": "bot-one",
                "ilink_user_id": "peer-one",
                "baseurl": "https://ilinkai.weixin.qq.com",
            }
        ),
        updates=AsyncMock(),
        send=AsyncMock(),
    )
    run = SimpleNamespace(
        run_id="run-shared", status=SimpleNamespace(is_terminal=True, value="succeeded")
    )
    conversation = SimpleNamespace(
        submit=AsyncMock(return_value=run),
        runs=SimpleNamespace(get=AsyncMock(return_value=run)),
        answer=AsyncMock(return_value="共享会话回答"),
    )
    auth = SimpleNamespace(
        profile=AsyncMock(
            return_value=(SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
        )
    )
    return WeixinService(
        database[1], McpCredentialCipher(SecretStr("test-key")), conversation, auth, client
    ), client


async def bind(service, user="alice"):
    await service.start("tenant", user)
    await service.login_once("tenant", user)
    async with service.sessions() as s:
        row = await s.get(Connection, ("tenant", user))
        return row.generation, service.open(row, "credentials", row.ciphertext)


def event(message_id=1, **changes):
    return dict(
        {
            "message_id": message_id,
            "message_type": 1,
            "from_user_id": "peer-one",
            "context_token": "private-context",
            "item_list": [{"type": 1, "text_item": {"text": "请记住 WX-TEST"}}],
        },
        **changes,
    )


@pytest.mark.asyncio
async def test_qr_is_owner_scoped_and_all_credentials_are_encrypted(database):
    c, client = setup(database)
    status = await c.start("tenant", "alice")
    assert status["qrSvg"].startswith("<?xml") and status["loginStatus"] == "wait"
    await c.start("tenant", "alice")
    assert client.qr.await_count == 1  # Repeated clicks do not invalidate a pending scan.
    assert not (await c.status("tenant", "bob"))["bound"]
    await c.login_once("tenant", "alice")
    status = await c.status("tenant", "alice")
    assert status["enabled"] and status["bound"] and status["qrSvg"] is None
    assert "private-token" not in json.dumps(status, default=str)
    async with c.sessions() as s:
        row = await s.get(Connection, ("tenant", "alice"))
        assert "private-token" not in row.ciphertext and not row.login_ciphertext
        assert c.open(row, "credentials", row.ciphertext)["peer"] == "peer-one"


@pytest.mark.asyncio
async def test_pairing_code_redirect_and_expiry_are_explicit(database):
    c, client = setup(database)
    await c.start("tenant", "alice")
    client.qr_status.return_value = {"status": "scaned_but_redirect", "redirect_host": "127.0.0.1"}
    await c.login_once("tenant", "alice")
    assert (await c.status("tenant", "alice"))["loginStatus"] == "failed"
    await c.start("tenant", "alice")
    client.qr_status.return_value = {"status": "need_verifycode"}
    await c.login_once("tenant", "alice")
    status = await c.status("tenant", "alice")
    with pytest.raises(ConflictError):
        await c.verify("tenant", "bob", status["loginId"], "123456")
    await c.verify("tenant", "alice", status["loginId"], "123456")
    await c.login_once("tenant", "alice")
    assert client.qr_status.call_args.args[2] == "123456"
    async with c.sessions.begin() as s:
        row = await s.get(Connection, ("tenant", "alice"))
        row.login_expires_at = now() - timedelta(seconds=1)
    await c.login_once("tenant", "alice")
    assert (await c.status("tenant", "alice"))["loginStatus"] == "expired"


@pytest.mark.asyncio
async def test_duplicate_bot_cannot_bind_another_kai_owner(database):
    c, _ = setup(database)
    await bind(c)
    await bind(c, "bob")
    assert not (await c.status("tenant", "bob"))["bound"]
    assert (await c.status("tenant", "alice"))["enabled"]


@pytest.mark.asyncio
async def test_cursor_and_inbox_commit_together_filter_other_people_and_deduplicate(database):
    c, _ = setup(database)
    generation, credentials = await bind(c)
    result = {
        "get_updates_buf": "private-cursor",
        "msgs": [
            event(),
            event(),
            event(2, from_user_id="stranger"),
            event(3, group_id="group"),
            event(4, message_type=2),
        ],
    }
    await c.ingest("tenant", "alice", generation, credentials, result)
    await c.ingest("tenant", "alice", generation, credentials, result)
    async with c.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Message)) == 1
        row = await s.get(Connection, ("tenant", "alice"))
        assert "private-cursor" not in row.cursor_ciphertext
        assert c.open(row, "cursor", row.cursor_ciphertext)["cursor"] == "private-cursor"
    assert (await c.status("tenant", "alice"))["online"]
    assert not (await c.status("other-tenant", "alice"))["bound"]


@pytest.mark.asyncio
async def test_shared_conversation_idempotent_resume_and_real_reply_context(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [event()]})
    c.conversation.submit.side_effect = ConflictError("another channel is busy")
    assert not await c.process_once("tenant", "alice")
    c.conversation.submit.side_effect = None
    c.conversation.submit.return_value.status.is_terminal = False
    assert await c.process_once("tenant", "alice")
    async with c.sessions() as s:
        assert (await s.scalar(select(Message))).run_id == "run-shared"
    c.conversation.submit.return_value.status.is_terminal = True
    assert await c.process_once("tenant", "alice")
    assert client.send.await_count == 1
    assert client.send.call_args.args[1] == "private-context"
    assert client.send.call_args.args[-1] == "共享会话回答"
    assert c.conversation.submit.call_args.args[:2] == ("tenant", "alice")
    assert c.conversation.submit.call_args.args[2].startswith("weixin-chat-")
    assert not await c.process_once("tenant", "alice")
    checks = (await c.diagnose("tenant", "alice"))["checks"]
    assert all(x["status"] == "pass" for x in checks)


@pytest.mark.asyncio
async def test_disconnect_rejects_inflight_poll_and_retains_runs_and_feishu(database):
    c, _ = setup(database)
    generation, credentials = await bind(c)
    await c.disconnect("tenant", "alice")
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [event()]})
    assert not await c.process_once("tenant", "alice")
    assert not (await c.status("tenant", "alice"))["enabled"]
    async with c.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Message)) == 0


@pytest.mark.asyncio
async def test_delivery_timeout_does_not_auto_duplicate_or_claim_success(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [event()]})
    client.send.side_effect = WeixinError()
    assert await c.process_once("tenant", "alice")
    assert not await c.process_once("tenant", "alice")
    assert client.send.await_count == 1
    status = await c.status("tenant", "alice")
    assert status["failedMessages"] == 1 and not status["lastReplyAt"]


@pytest.mark.asyncio
async def test_disabled_account_never_starts_a_model_or_sends_private_result(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [event()]})
    c.auth.profile.return_value[0].disabled = True
    with pytest.raises(PermissionDeniedError):
        await c.start("tenant", "alice")
    assert await c.process_once("tenant", "alice")
    assert not c.conversation.submit.called and not client.send.called


@pytest.mark.asyncio
async def test_non_text_is_explicit_without_inventing_an_attachment_analysis(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {"msgs": [event(item_list=[{"type": 2, "image_item": {}}])]},
    )
    assert await c.process_once("tenant", "alice")
    assert not c.conversation.submit.called
    assert "附件处理尚未启用" in client.send.call_args.args[-1]


@pytest.mark.asyncio
async def test_api_keeps_qr_and_status_private_to_signed_in_owner(database):
    c, _ = setup(database)
    app = create_memory_app()
    app.state.container = replace(app.state.container, weixin=c)
    identity = Identity("tenant", "alice", frozenset({"member"}))
    app.dependency_overrides[require_identity] = lambda: identity
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/v1/studio/assistant/weixin/login")).status_code == 200
        identity = Identity(
            "tenant",
            "alice",
            frozenset({"member"}),
            permissions=frozenset({"tasks:read", "tasks:write"}),
        )
        api_key_view = await client.get("/v1/studio/assistant/weixin/status")
        assert "qrSvg" not in api_key_view.json() and "loginId" not in api_key_view.json()
        assert (await client.post("/v1/studio/assistant/weixin/login")).status_code == 403
        await c.login_once("tenant", "alice")
        identity = Identity("tenant", "bob", frozenset({"member"}))
        status = (await client.get("/v1/studio/assistant/weixin/status")).json()
        assert not status["bound"] and "qrSvg" not in status
        await client.post("/v1/studio/assistant/weixin/disconnect")
        assert (await c.status("tenant", "alice"))["enabled"]
        invalid = await client.post(
            "/v1/studio/assistant/weixin/verify",
            json={"loginId": "x", "code": {"secret": "do-not-reflect"}},
        )
        assert invalid.status_code == 422 and "do-not-reflect" not in invalid.text


@pytest.mark.asyncio
async def test_protocol_auth_redirection_and_upstream_failure_are_sanitized():
    observed = []

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json={"ret": 0, "msgs": []})

    client = WeixinClient(httpx.MockTransport(handle))
    await client.updates(
        {"base": "https://ilinkai.weixin.qq.com", "token": "private-token"}, "cursor"
    )
    assert observed[0].headers["Authorization"] == "Bearer private-token"
    assert json.loads(observed[0].content)["base_info"]["bot_agent"] == "KAI/1.0"
    for host in [
        "https://127.0.0.1",
        "https://evil.com",
        "https://ilinkai.weixin.qq.com@evil.com",
        "http://ilinkai.weixin.qq.com",
        "https://ilinkai.weixin.qq.com:8080",
    ]:
        with pytest.raises(WeixinError):
            trusted_base(host)
    client = WeixinClient(
        httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ret": -14, "errmsg": "private-upstream"})
        )
    )
    with pytest.raises(WeixinError, match="AuthorizationExpired") as error:
        await client.updates({"base": "https://ilinkai.weixin.qq.com", "token": "x"}, "")
    assert "private-upstream" not in str(error.value)


@pytest.mark.asyncio
async def test_media_metadata_is_encrypted_prepared_once_and_submitted_to_the_same_conversation(
    database,
):
    from harness.feishu.models import PreparedMessage

    c, client = setup(database)
    c.media = SimpleNamespace(
        prepare=AsyncMock(
            return_value=PreparedMessage(
                text="微信语音转写：测试内容", model_route="deepseek-v4-flash"
            )
        )
    )
    generation, credentials = await bind(c)
    payload = event(
        item_list=[
            {"type": 1, "text_item": {"text": "caption"}},
            {
                "type": 2,
                "image_item": {
                    "media": {
                        "aes_key": "private-aes-key",
                        "encrypt_query_param": "private-download",
                    }
                },
            },
        ]
    )
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [payload, payload]})
    async with c.sessions() as s:
        row = await s.scalar(select(Message))
        assert "private" not in row.media_ciphertext
        assert (
            "private-aes-key"
            in c.open(await s.get(Connection, ("tenant", "alice")), "media", row.media_ciphertext)[
                "items"
            ]
        )
    c.conversation.submit.side_effect = ConflictError("busy")
    assert not await c.process_once("tenant", "alice")
    c.conversation.submit.side_effect = None
    assert await c.process_once("tenant", "alice")
    assert c.media.prepare.await_count == 1
    assert c.conversation.submit.call_args.args[3] == "微信语音转写：测试内容"
    assert c.conversation.submit.call_args.kwargs["model_route"] == "deepseek-v4-flash"
    assert client.send.await_count == 1


@pytest.mark.asyncio
async def test_failed_media_has_bounded_retries_and_does_not_block_later_text(database):
    c, client = setup(database)
    c.media = SimpleNamespace(prepare=AsyncMock(side_effect=ConnectionError("private URL")))
    generation, credentials = await bind(c)
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {"msgs": [event(item_list=[{"type": 4, "file_item": {}}]), event(2)]},
    )
    assert not await c.process_once("tenant", "alice")
    assert not await c.process_once("tenant", "alice")
    assert await c.process_once("tenant", "alice")
    assert "暂时无法下载或识别" in client.send.call_args.args[-1]
    assert not c.conversation.submit.called
    assert await c.process_once("tenant", "alice")
    assert c.conversation.submit.await_count == 1


@pytest.mark.asyncio
async def test_disconnect_during_media_preparation_never_creates_a_run_or_sends(database):
    from harness.feishu.models import PreparedMessage

    c, client = setup(database)

    async def prepare(*args):
        await c.disconnect("tenant", "alice")
        return PreparedMessage(text="private attachment analysis")

    c.media = SimpleNamespace(prepare=AsyncMock(side_effect=prepare))
    generation, credentials = await bind(c)
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {"msgs": [event(item_list=[{"type": 2, "image_item": {}}])]},
    )
    assert not await c.process_once("tenant", "alice")
    assert not c.conversation.submit.called and not client.send.called
