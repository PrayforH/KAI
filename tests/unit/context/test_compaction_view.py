from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import InMemoryEventRepository
from harness.context.compaction_view import SUMMARY_PREFIX, _snapshot, compaction_detail
from harness.core.events import RunEvent


def event(run_id: str, seq: int, kind: str, **payload: object) -> RunEvent:
    return RunEvent(
        event_id=f"{run_id}-{seq}",
        tenant_id="tenant",
        session_id="session",
        run_id=run_id,
        sequence=seq,
        type=kind,
        timestamp=datetime(2026, 9, 28, tzinfo=UTC) + timedelta(seconds=seq),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_missing_checkpoint_is_not_presented_as_successful_history() -> None:
    repo = InMemoryEventRepository()
    await repo.append(event("run", 1, "context.compacted", runtime="deepagents"))
    detail = await compaction_detail(repo, "tenant", "session", "run")
    assert detail.status == "unavailable"
    assert detail.summary is None


@pytest.mark.asyncio
async def test_multiple_compactions_show_final_checkpoint_and_exclude_current_run_from_before() -> (
    None
):
    repo = InMemoryEventRepository()
    await repo.append(
        event(
            "run",
            1,
            "context.history.checkpoint",
            schema_version=1,
            messages=[{"role": "user", "content": "not previous run"}],
        )
    )
    await repo.append(event("run", 2, "context.compacted", runtime="deepagents"))
    await repo.append(event("run", 3, "context.compacted", runtime="deepagents"))
    await repo.append(
        event(
            "run",
            4,
            "context.history.checkpoint",
            schema_version=1,
            messages=[{"role": "user", "content": SUMMARY_PREFIX + " final"}],
        )
    )
    detail = await compaction_detail(repo, "tenant", "session", "run")
    assert detail.status == "available"
    assert detail.compaction_count == 2
    assert detail.before is None
    assert detail.summary.content.endswith(" final")


def test_large_inspection_is_bounded_and_explicitly_truncated() -> None:
    snapshot = _snapshot(
        event("run", 1, "checkpoint"),
        [
            {"role": "user", "content": "x" * 150_000},
            {"role": "assistant", "content": "y" * 150_000},
            {"role": "user", "content": "extra"},
        ],
    )
    assert snapshot.message_count == 3
    assert snapshot.characters == 300_005
    assert snapshot.truncated
    assert sum(len(message.content) for message in snapshot.messages) == 240_000


@pytest.mark.asyncio
async def test_recent_events_are_tenant_scoped_ordered_and_bounded() -> None:
    repo = InMemoryEventRepository()
    for seq in range(1, 5):
        await repo.append(event("run", seq, "context.compacted"))
    await repo.append(
        event("other", 1, "context.compacted").model_copy(update={"tenant_id": "other"})
    )
    latest = await repo.recent_for_session_types(
        "tenant", "session", ("context.compacted",), limit=2
    )
    assert [item.sequence for item in latest] == [4, 3]
    older = await repo.recent_for_session_types(
        "tenant", "session", ("context.compacted",), before=latest[1]
    )
    assert [item.sequence for item in older] == [2, 1]
