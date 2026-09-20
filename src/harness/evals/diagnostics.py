"""Observable evaluation evidence; missing telemetry is never a successful budget check."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field


class EvalFailure(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)
    code: str
    dimension: Literal["execution", "output", "tool", "policy", "budget", "evidence"]
    severity: Literal["error", "critical"] = "error"
    detail: str


class EvalUsage(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)
    cost_usd: float | None = Field(default=None, alias="costUsd", ge=0, allow_inf_nan=False)
    model_tokens: int | None = Field(default=None, alias="modelTokens", ge=0)
    tool_calls: int = Field(default=0, alias="toolCalls", ge=0)


def failure(detail: str) -> EvalFailure:
    if detail.startswith("forbidden") or "approval" in detail:
        return EvalFailure(
            code="policy_violation", dimension="policy", severity="critical", detail=detail
        )
    if detail.startswith("missing required tool") or detail.startswith("tool sequence"):
        return EvalFailure(code="tool_contract", dimension="tool", detail=detail)
    if "unavailable" in detail:
        return EvalFailure(code="missing_evidence", dimension="evidence", detail=detail)
    if "budget" in detail or "exceeded" in detail:
        return EvalFailure(code="budget_exceeded", dimension="budget", detail=detail)
    if "output" in detail:
        return EvalFailure(code="output_mismatch", dimension="output", detail=detail)
    return EvalFailure(code="execution_failure", dimension="execution", detail=detail)


def usage_from_events(events: tuple[dict[str, object], ...]) -> EvalUsage:
    cost: float | None = None
    tokens: int | None = None
    calls = 0
    for event in events:
        if event.get("type") == "tool.request":
            calls += 1
        if event.get("type") != "runtime.result":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        payload = cast(Mapping[str, object], payload)
        value = payload.get("total_cost_usd")
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            cost = float(value) if math.isfinite(value) and value >= 0 else None
        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            usage = cast(Mapping[str, object], usage)
            values = [usage.get(k) for k in ("input_tokens", "output_tokens")]
            if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
                tokens = sum(cast(int, v) for v in values)
                for k in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                    cached = usage.get(k, 0)
                    if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
                        tokens += cached
                    else:
                        tokens = None
                        break
    return EvalUsage(costUsd=cost, modelTokens=tokens, toolCalls=calls)
