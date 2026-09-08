from datetime import UTC, datetime

import pytest

from harness.context.models import ContextBudgetLevel
from harness.context.window import context_window_snapshot, context_window_view
from harness.core.events import RunEvent


def _event(percentage: float, *, threshold: int | None = 175_000) -> RunEvent:
    return RunEvent(
        event_id=f"event-{percentage}",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        sequence=7,
        type="context.window.observed",
        timestamp=datetime(2026, 8, 9, tzinfo=UTC),
        payload={
            "phase": "after",
            "total_tokens": int(percentage * 1_800),
            "max_tokens": 180_000,
            "raw_max_tokens": 200_000,
            "percentage": percentage,
            "model": "claude-sonnet",
            "auto_compact_enabled": True,
            "auto_compact_threshold": threshold,
            "categories": [{"name": "Messages", "tokens": 100_000}],
        },
    )


@pytest.mark.parametrize(
    ("percentage", "level", "action"),
    [
        (64.9, ContextBudgetLevel.GREEN, "none"),
        (65.0, ContextBudgetLevel.WATCH, "reduce_optional_context"),
        (75.0, ContextBudgetLevel.COMPACT_READY, "consider_rebase"),
        (85.0, ContextBudgetLevel.EMERGENCY, "rebase_now"),
    ],
)
def test_context_window_policy_has_monotonic_product_thresholds(
    percentage: float,
    level: ContextBudgetLevel,
    action: str,
) -> None:
    snapshot = context_window_snapshot(_event(percentage))

    assert snapshot is not None
    assert snapshot.level is level
    assert snapshot.recommended_action == action
    assert snapshot.headroom_tokens == max(0, 180_000 - int(percentage * 1_800))
    assert snapshot.soft_threshold_percentage == 65
    assert snapshot.compact_ready_percentage == 75
    assert snapshot.hard_threshold_percentage == 85
    assert snapshot.provider_threshold_percentage == pytest.approx(97.2222, rel=1e-4)


def test_provider_auto_compact_threshold_tightens_the_hard_boundary() -> None:
    snapshot = context_window_snapshot(_event(80.0, threshold=144_000))

    assert snapshot is not None
    assert snapshot.provider_threshold_percentage == 80
    assert snapshot.hard_threshold_percentage == 80
    assert snapshot.level is ContextBudgetLevel.EMERGENCY


def test_malformed_optional_window_event_fails_open() -> None:
    malformed = _event(70).model_copy(update={"payload": {"percentage": "not-a-number"}})

    assert context_window_snapshot(malformed) is None
    assert context_window_snapshot(malformed.model_copy(update={"type": "run.succeeded"})) is None


def test_context_window_view_distinguishes_pending_available_and_unavailable() -> None:
    observed = _event(70)
    unavailable = observed.model_copy(
        update={
            "type": "context.window.unavailable",
            "payload": {"phase": "after", "reason": "control_timeout"},
        }
    )

    pending_window, pending = context_window_view(None)
    available_window, available = context_window_view(observed)
    unavailable_window, unsupported = context_window_view(unavailable)

    assert pending_window is None
    assert pending.status == "pending"
    assert available_window is not None
    assert available.status == "available"
    assert available.source_run_id == observed.run_id
    assert unavailable_window is None
    assert unsupported.status == "unavailable"
    assert unsupported.reason == "control_timeout"
