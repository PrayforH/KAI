"""Broadcast admission, explicit states, deterministic replay and summary recovery."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.group_delivery import (
    GROUP_FANOUT_LIMIT,
    DeliveryState,
    GroupSummaryState,
    has_broadcast,
    hydrate_delivery_batch,
    reconcile_group_summaries,
    strip_broadcast,
)
from harness.assistant.models import SpecialistStatus
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import RunStatus
from tests.unit.assistant.test_group_chat import _service, _specialist
from tests.unit.assistant.test_group_delegate import setup_delegate


@pytest.mark.parametrize(
    "text",
    [
        "@所有人 做事",
        "@everyone 做事",
        "＠ｅｖｅｒｙｏｎｅ 做事",
        "@ 所有人 做事",
        "@　EVERYONE 做事",
        "＠ 所 有 人 做事",
    ],
)
def test_broadcast_variants(text):
    assert has_broadcast(text)
    assert strip_broadcast(text) == "做事"


@pytest.mark.parametrize("text", ["mail@everyone.com", "@everyoneElse", "@所有人员", "everyone"])
def test_non_broadcast(text):
    assert not has_broadcast(text)


async def setup_broadcast(count=2):
    specialists = [_specialist(f"specialist_{i}", f"专家{i}") for i in range(count)]
    service, repo, agui = _service(specialists)
    group = await service.create_assistant_group("t", "u", name="项目")
    for specialist in specialists:
        await service.add_assistant_group_member(
            "t", "u", group.group_id, specialist_id=specialist.specialist_id
        )
    return service, repo, agui, group


@pytest.mark.asyncio
async def test_broadcast_active_only_and_duplicate_tokens_replay():
    service, repo, agui, group = await setup_broadcast(3)
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="specialist_2")
    repo._specialists["specialist_1"] = repo._specialists["specialist_1"].model_copy(
        update={"status": SpecialistStatus.PAUSED}
    )
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 @everyone @专家0 请回复", client_message_id="once"
    )
    assert agui.create_run_with_result.await_count == 2
    assert {r.specialist_id for r in first.replies if r.run_id} == {None, "specialist_0"}
    second = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@everyone 请回复", client_message_id="once"
    )
    assert second.message.message_id == first.message.message_id
    assert agui.create_run_with_result.await_count == 2
    assert any("paused" in n for n in first.notices)
    batch = next(iter(repo.batches.values()))
    assert batch.summary_state == "pending"
    assert [t.order for t in batch.targets] == [0, 1]


@pytest.mark.asyncio
async def test_broadcast_limit_rejects_entire_batch():
    service, repo, agui, group = await setup_broadcast(GROUP_FANOUT_LIMIT)
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事"
    )
    agui.create_run_with_result.assert_not_awaited()
    assert any("整批未派发" in n and str(GROUP_FANOUT_LIMIT) in n for n in result.notices)
    assert next(iter(repo.batches.values())).targets == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_primary", [True, False])
async def test_zero_specialists_receipt(with_primary):
    service, _, agui, group = await setup_broadcast(0)
    if not with_primary:
        await service.remove_assistant_group_member("t", "u", group.group_id, member_id="primary")
    result = await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人")
    assert agui.create_run_with_result.await_count == int(with_primary)
    assert any("退化" in n if with_primary else "没有可接话成员" in n for n in result.notices)


@pytest.mark.asyncio
async def test_partial_rejection_and_unknown_are_distinct():
    service, repo, _, group = await setup_broadcast(2)
    original_create = service._agui.create_run_with_result.side_effect

    async def create(**kwargs):
        member = kwargs["group_context"]["memberId"]
        if member == "specialist_0":
            raise ConflictError("disabled")
        if member == "specialist_1":
            raise TimeoutError("network")
        return await original_create(**kwargs)

    service._agui.create_run_with_result.side_effect = create
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事"
    )
    batch = next(iter(repo.batches.values()))
    assert [t.state for t in batch.targets] == [
        DeliveryState.RUNNING,
        DeliveryState.FAILED,
        DeliveryState.UNKNOWN,
    ]
    assert "已接收 1/3" in result.notices[-1]
    assert "未确认 1" in result.notices[-1]


@pytest.mark.asyncio
async def test_delegate_contract_reuses_batch_and_order():
    service, group, posted, run, session, *_ = await setup_delegate()
    result = await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    repeat = await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    assert result == repeat
    assert result["targetOrder"] == 0
    assert result["targetMemberId"] == "specialist_pm"
    assert len(service.repository.batches) == 1
    batch = next(iter(service.repository.batches.values()))
    assert result["deliveryBatchId"] == batch.batch_id
    assert batch.trigger_message_id == posted.message.message_id
    assert len(batch.targets) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,state",
    [
        (RunStatus.QUEUED, "pending"),
        (RunStatus.PROVISIONING, "running"),
        (RunStatus.CANCELLING, "running"),
        (RunStatus.REJECTED, "failed"),
        (RunStatus.TIMED_OUT, "failed"),
        (RunStatus.RUNNING, "running"),
        (RunStatus.SUCCEEDED, "succeeded"),
        (RunStatus.FAILED, "failed"),
        (RunStatus.WAITING_APPROVAL, "waiting"),
        (RunStatus.CANCELLED, "stopped"),
    ],
)
async def test_read_batch_states(status, state):
    service, repo, _, group = await setup_broadcast(1)
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事"
    )
    service._runs.get.return_value = SimpleNamespace(status=status)
    service._hydrate_group_message = AsyncMock(side_effect=lambda _, m, **kwargs: m)
    batch = await service.get_assistant_group_delivery(
        "t", "u", group.group_id, posted.message.message_id
    )
    assert all(t.state.value == state for t in batch.targets)
    assert batch.targets[1].result.delivery_order == 1


@pytest.mark.asyncio
async def test_summary_waits_then_recovers_once_after_all_terminal():
    service, repo, agui, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 做事")
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.RUNNING, run_id="fake")
    await reconcile_group_summaries(service)
    assert agui.create_run_with_result.await_count == 2
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.return_value = []
    # Reconstructing a service uses only persisted intent, no process memory.
    fresh = service.with_repository(repo)
    await reconcile_group_summaries(fresh)
    await reconcile_group_summaries(fresh)
    assert agui.create_run_with_result.await_count == 3
    batch = next(iter(repo.batches.values()))
    assert batch.summary_state == "queued"
    assert (
        agui.create_run_with_result.await_args.kwargs["group_context"]["deliveryKind"]
        == "group_summary"
    )


@pytest.mark.asyncio
async def test_unknown_summary_expires_without_claiming_failure():
    service, repo, agui, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 做事")
    batch = next(iter(repo.batches.values()))
    repo.batches[batch.batch_id] = batch.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(hours=25)}
    )
    service._runs.get.side_effect = TimeoutError("unavailable")
    await reconcile_group_summaries(service)
    assert next(iter(repo.batches.values())).summary_state == "queued"
    body = agui.create_run_with_result.await_args.kwargs["request"].messages[0].content
    assert '"state": "unknown"' in body
    assert '"expired": true' in body


@pytest.mark.asyncio
async def test_non_member_is_denied_before_run_or_artifact_lookup():
    service, repo, agui, group = await setup_broadcast(0)
    with pytest.raises(PermissionDeniedError):
        await service.get_assistant_group_delivery("t", "intruder", group.group_id, "known")
    agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{"limit": 101}, {"offset": -1}, {"since": datetime(2026, 1, 1)}]
)
async def test_artifact_invalid_page_or_time_rejected(kwargs):
    from harness.core.errors import InvalidRequestError

    service, _, _, group = await setup_broadcast(0)
    with pytest.raises(InvalidRequestError):
        await service.list_assistant_group_artifacts("t", "u", group.group_id, **kwargs)


@pytest.mark.asyncio
async def test_primary_removed_prevents_summary_and_does_not_reinsert_seat():
    service, repo, agui, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 做事")
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="primary")
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.return_value = []
    await reconcile_group_summaries(service)
    assert next(iter(repo.batches.values())).summary_state == "unavailable"
    assert agui.create_run_with_result.await_count == 2


@pytest.mark.asyncio
async def test_read_event_failure_remains_unknown_not_negative():
    service, _, _, group = await setup_broadcast(1)
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事"
    )
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.side_effect = TimeoutError("event store unavailable")
    batch = await service.get_assistant_group_delivery(
        "t", "u", group.group_id, result.message.message_id
    )
    assert all(t.state is DeliveryState.UNKNOWN for t in batch.targets)
    assert all(t.result is None for t in batch.targets)


@pytest.mark.asyncio
async def test_no_primary_still_receives_broadcast_with_explicit_no_summary_notice():
    service, repo, agui, group = await setup_broadcast(1)
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="primary")
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事"
    )
    assert agui.create_run_with_result.await_count == 1
    assert any("无法自动汇总" in n for n in result.notices)
    assert next(iter(repo.batches.values())).summary_state == "not_required"


@pytest.mark.asyncio
async def test_broadcast_primary_cannot_delegate_same_task_again():
    service, group, _, run, session, *_ = await setup_delegate()
    context = {**run.input["group_context"], "deliveryKind": "group_broadcast"}
    run = run.model_copy(update={"input": {**run.input, "group_context": context}})
    service._runs.get.return_value = run
    with pytest.raises(ConflictError, match="不要重复转交"):
        await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_registry_uncertainty_does_not_block_other_members():
    service, repo, agui, group = await setup_broadcast(2)
    original = repo.get_specialist

    async def lookup(tenant, user, specialist_id):
        if specialist_id == "specialist_0":
            raise TimeoutError("registry unavailable")
        return await original(tenant, user, specialist_id)

    repo.get_specialist = lookup
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事"
    )
    assert agui.create_run_with_result.await_count == 2
    assert any("状态未确认，本次未派发" in n for n in result.notices)
    batch = next(iter(repo.batches.values()))
    assert any("状态未确认" in n for n in batch.notices)


@pytest.mark.asyncio
async def test_definitive_summary_rejection_is_visible_and_not_retried():
    service, repo, agui, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 做事")
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.return_value = []
    agui.create_run_with_result.side_effect = ConflictError("主助理尚未配置")
    await reconcile_group_summaries(service)
    await reconcile_group_summaries(service)
    batch = next(iter(repo.batches.values()))
    assert batch.summary_state == "unavailable"
    assert any("主助理汇总未启动" in n for n in batch.notices)
    assert agui.create_run_with_result.await_count == 3


@pytest.mark.asyncio
async def test_empty_broadcast_result_does_not_repeat_original_work_on_read():
    service, _, agui, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 做事")
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.return_value = []
    await service.list_assistant_group_messages("t", "u", group.group_id)
    await service.list_assistant_group_messages("t", "u", group.group_id)
    assert agui.create_run_with_result.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("read outage"), NotFoundError("missing")])
async def test_summary_row_read_failure_preserves_targets_as_unknown(error):
    service, repo, _, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 检查")
    batch = next(iter(repo.batches.values())).model_copy(
        update={"summary_state": GroupSummaryState.QUEUED, "summary_message_id": "missing-summary"}
    )
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.return_value = []
    original = repo.get_group_message

    async def lookup(tenant, user, group_id, message_id):
        if message_id == batch.summary_message_id:
            raise error
        return await original(tenant, user, group_id, message_id)

    repo.get_group_message = lookup
    hydrated = await hydrate_delivery_batch(service, "t", "u", batch)
    assert all(t.state is DeliveryState.SUCCEEDED for t in hydrated.targets)
    assert hydrated.summary.status == "unknown"
    assert hydrated.summary.run_id is None
    assert hydrated.summary_state == "queued"


@pytest.mark.asyncio
async def test_summary_permission_denial_is_not_hidden_as_unknown():
    service, repo, _, group = await setup_broadcast(0)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 检查")
    batch = next(iter(repo.batches.values())).model_copy(
        update={"summary_state": GroupSummaryState.QUEUED, "summary_message_id": "summary"}
    )
    original = repo.get_group_message

    async def lookup(tenant, user, group_id, message_id):
        if message_id == "summary":
            raise PermissionDeniedError("membership revoked")
        return await original(tenant, user, group_id, message_id)

    repo.get_group_message = lookup
    with pytest.raises(PermissionDeniedError):
        await hydrate_delivery_batch(service, "t", "u", batch)


@pytest.mark.asyncio
async def test_delivery_fields_include_reply_sources_outside_full_timeline_window():
    service, repo, _, group = await setup_broadcast(0)
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 检查"
    )
    messages = [
        posted.replies[0].model_copy(update={"message_id": f"reply-{i}"}) for i in range(300)
    ]
    repo.group_delivery_message_fields = AsyncMock(return_value={})
    await service._with_group_delivery_fields("t", "u", group.group_id, messages)
    assert repo.group_delivery_message_fields.await_args.args[-1] == [posted.message.message_id]


@pytest.mark.asyncio
async def test_summary_event_read_failure_preserves_summary_identity():
    service, repo, _, group = await setup_broadcast(1)
    await service.post_assistant_group_message("t", "u", group.group_id, text="@所有人 检查")
    service._runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake")
    service._events.list_after.return_value = []
    await reconcile_group_summaries(service)
    batch = next(iter(repo.batches.values()))
    original = service._hydrate_group_message

    async def hydrate(tenant, message, **kwargs):
        if message.message_id == batch.summary_message_id:
            raise TimeoutError("events unavailable")
        return await original(tenant, message, **kwargs)

    service._hydrate_group_message = hydrate
    hydrated = await hydrate_delivery_batch(service, "t", "u", batch)
    assert hydrated.summary.status == "unknown"
    assert hydrated.summary.run_id is not None
    assert all(t.state is DeliveryState.SUCCEEDED for t in hydrated.targets)
