"""助理资料库：值进加密存储、列表只回脱敏元数据、授权走 credential_ref。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from harness.assistant.models import Specialist, SpecialistStatus
from harness.assistant.storage import VaultItemRow
from harness.assistant.vault import VaultCategory, VaultDeleteRequest, VaultItemWrite
from harness.core.errors import ConflictError, NotFoundError


def _vault_row(**overrides) -> VaultItemRow:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    values = {
        "item_id": "vault_item_1",
        "tenant_id": "t",
        "user_id": "u",
        "category": "login",
        "title": "行情网站",
        "reference": "vault_abc",
        "last4": None,
        "has_value": True,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return VaultItemRow(**values)


def _specialist(config: dict | None = None) -> Specialist:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    return Specialist(
        specialistId="specialist_a",
        tenantId="t",
        userId="u",
        name="资料 Bot",
        slug="ziliau-bot",
        description="",
        agentName="agent",
        agentVersion="1.0",
        status=SpecialistStatus.READY,
        config=config or {},
        createdAt=now,
        updatedAt=now,
    )


def _service(rows=None, specialist=None):
    from harness.assistant.service import AssistantService

    repo, agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(7)]
    repo.list_vault_items.return_value = list(rows or [])
    repo.get_vault_item.side_effect = lambda tenant, user, item_id: (
        next(row for row in (rows or []) if row.item_id == item_id)
    )
    repo.create_vault_item.side_effect = lambda row: row
    repo.update_vault_item.side_effect = lambda tenant, user, item_id, **kwargs: _vault_row(
        item_id=item_id, revision=2, **{k: v for k, v in kwargs["changes"].items()}
    )
    repo.delete_vault_item.return_value = True
    repo.delete_credential_blob.return_value = True
    repo.record_handover.side_effect = lambda value: value
    repo.get_specialist.side_effect = NotFoundError("missing") if specialist is None else None
    if specialist is not None:
        repo.get_specialist.return_value = specialist

    def _apply(tenant, user, specialist_id, **kwargs):
        config = dict(kwargs.get("config") or {})
        for key in kwargs.get("config_remove") or ():
            config.pop(key, None)
        return _specialist(config)

    repo.update_specialist.side_effect = _apply
    auth.profile.return_value = (SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
    agui.get_binding.return_value = SimpleNamespace(session_id="session")
    service = AssistantService(
        repo, agents=agents, auth=auth, projects=projects, agui=agui, runs=runs, events=events
    )
    return service, repo


class _Cipher:
    def __init__(self) -> None:
        self.sealed: list[tuple[str, str, str, str]] = []

    def encrypt(self, tenant_id: str, user_id: str, reference: str, values) -> str:
        secret = values["secret"].get_secret_value()
        self.sealed.append((tenant_id, user_id, reference, secret))
        return f"cipher::{reference}::{secret[::-1]}"


@pytest.mark.asyncio
async def test_vault_item_encrypts_the_value_and_lists_only_metadata() -> None:
    service, repo = _service()
    cipher = _Cipher()
    service.set_cipher(cipher)  # type: ignore[arg-type]
    item = await service.create_vault_item(
        "t", "u", category=VaultCategory.LOGIN, title="行情网站", value="hunter2"
    )
    # 值进了加密存储，没有留在条目上。
    assert cipher.sealed[0][3] == "hunter2"
    assert repo.save_credential_blob.await_count == 1
    ciphertext = repo.save_credential_blob.await_args.args[3]
    assert "hunter2" not in ciphertext
    assert item.has_secret is True
    assert item.last4 is None
    # 列表投影里只有元数据：没有值、密文这类字段。
    assert "hunter2" not in item.model_dump_json()
    assert set(item.model_dump()) == {
        "item_id", "category", "title", "reference",
        "has_secret", "last4", "revision", "created_at", "updated_at",
    }


@pytest.mark.asyncio
async def test_vault_list_is_owner_scoped() -> None:
    service, repo = _service(rows=[_vault_row()])
    items = await service.vault_items("t", "other-user")
    assert len(items) == 1
    repo.list_vault_items.assert_awaited_once_with("t", "other-user")


@pytest.mark.asyncio
async def test_vault_delete_needs_the_title_confirmation() -> None:
    service, repo = _service(rows=[_vault_row()])
    with pytest.raises(ConflictError):
        await service.delete_vault_item("t", "u", "vault_item_1", confirm_title="别的标题")
    repo.delete_vault_item.assert_not_called()
    assert await service.delete_vault_item(
        "t", "u", "vault_item_1", confirm_title="行情网站"
    ) is True
    # 密文一起清掉，不留孤儿。
    repo.delete_credential_blob.assert_awaited_once_with("t", "u", "vault_abc")


@pytest.mark.asyncio
async def test_granting_writes_a_credential_ref_and_revoking_removes_it() -> None:
    service, repo = _service(rows=[_vault_row()], specialist=_specialist())
    await service.grant_vault_item(
        "t", "u", "specialist_a", item_id="vault_item_1", granted=True, expected_revision=1
    )
    config = repo.update_specialist.await_args.kwargs["config"]
    assert config["credential_ref:行情网站"] == "vault_abc"

    repo.update_specialist.reset_mock()
    service, repo = _service(
        rows=[_vault_row()],
        specialist=_specialist({"credential_ref:行情网站": "vault_abc"}),
    )
    await service.grant_vault_item(
        "t", "u", "specialist_a", item_id="vault_item_1", granted=False, expected_revision=1
    )
    # 撤销必须真正移除引用：合并语义不会删键，得显式移除。
    assert repo.update_specialist.await_args.kwargs["config_remove"] == ("credential_ref:行情网站",)
    config = repo.update_specialist.await_args.kwargs["config"]
    assert "credential_ref:行情网站" not in config


def test_card_category_has_no_cvv_field() -> None:
    """卡片只保存安全令牌与末四位：CVV 不是可填字段。"""
    with pytest.raises(ValidationError):
        VaultItemWrite.model_validate({
            "category": "card",
            "title": "某张卡",
            "cvv": "123",
        })
    body = VaultItemWrite.model_validate({
        "category": "card",
        "title": "某张卡",
        "last4": "4242",
    })
    assert body.last4 == "4242"
    assert body.value is None


def test_delete_request_requires_a_confirmation_title() -> None:
    body = VaultDeleteRequest.model_validate({"confirmTitle": "行情网站"})
    assert body.confirm_title == "行情网站"
    with pytest.raises(ValidationError):
        VaultDeleteRequest.model_validate({})


@pytest.mark.asyncio
async def test_deleting_a_vault_item_revokes_the_bot_grants() -> None:
    """删除即失效：已经授权给 Bot 的 credential_ref 也要收回。"""
    granted = _specialist({"credential_ref:行情网站": "vault_abc"})
    service, repo = _service(rows=[_vault_row()], specialist=granted)
    repo.list_specialists.return_value = [granted]
    deleted = await service.delete_vault_item(
        "t", "u", "vault_item_1", confirm_title="行情网站"
    )
    assert deleted is True
    repo.delete_credential_blob.assert_awaited_once_with("t", "u", "vault_abc")
    # 引用被显式移除：Bot 手里不再留着指向已删资料的引用。
    assert repo.update_specialist.await_args.kwargs["config_remove"] == ("credential_ref:行情网站",)


@pytest.mark.asyncio
async def test_revoking_a_grant_retries_after_a_concurrent_edit() -> None:
    """Bot 的资料同时被改过时，按最新 revision 重试，不把「删除即失效」做成半截。"""
    granted = _specialist({"credential_ref:行情网站": "vault_abc"})
    service, repo = _service(rows=[_vault_row()], specialist=granted)
    repo.list_specialists.return_value = [granted]
    calls: list[int] = []

    def _flaky(tenant, user, specialist_id, **kwargs):
        calls.append(kwargs["expected_revision"])
        if len(calls) == 1:
            raise ConflictError("Specialist changed; reload before applying this change")
        return _specialist({})

    repo.update_specialist.side_effect = _flaky
    repo.get_specialist.side_effect = None
    repo.get_specialist.return_value = _specialist(
        {"credential_ref:行情网站": "vault_abc", "title": "研究"}
    )
    await service.delete_vault_item("t", "u", "vault_item_1", confirm_title="行情网站")
    assert len(calls) == 2
