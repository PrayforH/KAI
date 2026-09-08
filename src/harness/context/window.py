"""Pure policy evaluation for content-free provider context observations."""

from __future__ import annotations

from typing import cast

from pydantic import ValidationError

from harness.context.models import (
    ContextBudgetLevel,
    ContextWindowAvailability,
    ContextWindowCategory,
    ContextWindowSnapshot,
)
from harness.core.events import RunEvent

DEFAULT_SOFT_THRESHOLD_PERCENTAGE = 65.0
DEFAULT_COMPACT_READY_PERCENTAGE = 75.0
DEFAULT_HARD_THRESHOLD_PERCENTAGE = 85.0


def context_window_view(
    event: RunEvent | None,
) -> tuple[ContextWindowSnapshot | None, ContextWindowAvailability]:
    if event is None:
        return None, ContextWindowAvailability(status="pending")
    snapshot = context_window_snapshot(event)
    if snapshot is not None:
        return snapshot, ContextWindowAvailability(
            status="available",
            checked_at=event.timestamp,
            source_run_id=event.run_id,
        )
    if event.type == "context.window.unavailable":
        raw_reason = event.payload.get("reason")
        reason = (
            raw_reason
            if raw_reason in {"control_timeout", "control_unavailable"}
            else "control_unavailable"
        )
        return None, ContextWindowAvailability(
            status="unavailable",
            checked_at=event.timestamp,
            source_run_id=event.run_id,
            reason=reason,
        )
    return None, ContextWindowAvailability(status="pending")


def context_window_snapshot(event: RunEvent) -> ContextWindowSnapshot | None:
    """Validate a durable runtime event and attach deterministic product policy."""

    if event.type != "context.window.observed":
        return None
    payload = event.payload
    try:
        total_tokens = max(0, int(payload.get("total_tokens", 0)))
        max_tokens = max(0, int(payload.get("max_tokens", 0)))
        raw_max_tokens = max(0, int(payload.get("raw_max_tokens", 0)))
        percentage = max(0.0, min(100.0, float(payload.get("percentage", 0.0))))
        auto_compact_enabled = bool(payload.get("auto_compact_enabled", False))
        raw_threshold = payload.get("auto_compact_threshold")
        auto_compact_threshold = max(0, int(raw_threshold)) if raw_threshold is not None else None
        provider_threshold_percentage = (
            min(100.0, auto_compact_threshold / max_tokens * 100.0)
            if auto_compact_enabled and auto_compact_threshold is not None and max_tokens > 0
            else None
        )
        hard_threshold = DEFAULT_HARD_THRESHOLD_PERCENTAGE
        if provider_threshold_percentage is not None:
            hard_threshold = max(
                DEFAULT_COMPACT_READY_PERCENTAGE,
                min(hard_threshold, provider_threshold_percentage),
            )
        if percentage >= hard_threshold:
            level = ContextBudgetLevel.EMERGENCY
            action = "rebase_now"
        elif percentage >= DEFAULT_COMPACT_READY_PERCENTAGE:
            level = ContextBudgetLevel.COMPACT_READY
            action = "consider_rebase"
        elif percentage >= DEFAULT_SOFT_THRESHOLD_PERCENTAGE:
            level = ContextBudgetLevel.WATCH
            action = "reduce_optional_context"
        else:
            level = ContextBudgetLevel.GREEN
            action = "none"
        raw_categories = cast(object, payload.get("categories", []))
        category_values = (
            cast(list[object], raw_categories) if isinstance(raw_categories, list) else []
        )
        categories = tuple(
            ContextWindowCategory.model_validate(item)
            for item in category_values[:64]
            if isinstance(item, dict)
        )
        return ContextWindowSnapshot(
            source_run_id=event.run_id,
            observed_at=event.timestamp,
            phase=str(payload.get("phase", "after")),
            total_tokens=total_tokens,
            max_tokens=max_tokens,
            raw_max_tokens=raw_max_tokens,
            headroom_tokens=max(0, max_tokens - total_tokens),
            percentage=percentage,
            model=str(payload.get("model", "")),
            auto_compact_enabled=auto_compact_enabled,
            auto_compact_threshold=auto_compact_threshold,
            provider_threshold_percentage=provider_threshold_percentage,
            categories=categories,
            level=level,
            soft_threshold_percentage=DEFAULT_SOFT_THRESHOLD_PERCENTAGE,
            compact_ready_percentage=DEFAULT_COMPACT_READY_PERCENTAGE,
            hard_threshold_percentage=hard_threshold,
            recommended_action=action,
        )
    except (TypeError, ValueError, ValidationError):
        # Runtime control metrics are optional. Malformed provider payloads
        # must never break task history or context recovery reads.
        return None
