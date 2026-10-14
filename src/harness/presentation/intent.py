"""Bounded semantic selection for data-backed automatic UI, after results exist."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Sequence
from time import monotonic
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from harness.config import Settings
from harness.presentation.catalog import default_catalog
from harness.presentation.completion import UICompletionClient
from harness.presentation.schema import Presentation, validate_block

_LOGGER = logging.getLogger(__name__)

UI_INTENT_SYSTEM_PROMPT = "明确只要一个数值、是非结论或一句话用text。只分类，不回答问题。"


def _selection_prompt(kinds: Sequence[str]) -> str:
    """Advertise only data-backed capabilities, from the same renderer catalog."""
    catalog = default_catalog()
    choices = [
        f"{renderer.summary}用{kind}" for kind in kinds if (renderer := catalog.renderer(kind))
    ]
    return "；".join(choices) + "；" + UI_INTENT_SYSTEM_PROMPT


class UIIntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["text", "summary", "table", "chart", "comparison", "unknown"]


def _result_summary(presentation: Presentation) -> dict[str, Any]:
    """Describe information shape explicitly instead of asking a small model to infer it."""
    data = presentation.data
    if presentation.kind == "table":
        return {
            "rows": data["rows"][:4],
        }
    if presentation.kind == "summary":
        return {
            item["label"]: f"{item['value']} {item.get('unit', '')}".strip()
            for item in data["items"]
        }
    return data


def presentation_variants(presentation: Presentation) -> dict[str, Presentation]:
    """The classifier only chooses among complete, schema-validated actual data."""
    if presentation.actions:
        return {}
    if presentation.kind == "summary":
        # A snapshot has no series or competing options. Do not manufacture them.
        return {"summary": presentation}
    if presentation.kind not in {"table", "chart", "comparison"}:
        return {}
    if presentation.kind != "table":
        return {presentation.kind: presentation}
    variants = {"table": presentation}
    rows = cast(list[dict[str, Any]], presentation.data["rows"])
    columns = cast(list[dict[str, str]], presentation.data["columns"])
    keys = [c["key"] for c in columns]
    if not rows or not all(all(key in row for key in keys) for row in rows):
        return variants
    labels = {c["key"]: c.get("label") or c["key"] for c in columns}
    label_keys = [
        key
        for key in keys
        if key.lower() not in {"id", "uuid", "url", "identifier"}
        and all(isinstance(row[key], str) and row[key].strip() for row in rows)
        and len({row[key] for row in rows}) == len(rows)
    ]
    if not label_keys:
        return variants
    x_field = next(
        (
            key
            for key in label_keys
            if key.lower() in {"date", "month", "year", "time", "日期", "月份", "年份"}
        ),
        label_keys[0],
    )
    numeric = [
        key
        for key in keys
        if key.lower() not in {"id", "uuid", "identifier", "code", "编号", "序号"}
        and all(
            isinstance(row[key], int | float)
            and not isinstance(row[key], bool)
            and math.isfinite(row[key])
            for row in rows
        )
    ]
    blocks: dict[str, dict[str, Any]] = {}
    if numeric:
        times = all(re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", row[x_field]) for row in rows)
        blocks["chart"] = {
            "version": 1,
            "kind": "chart",
            "title": presentation.title,
            "fallback_text": presentation.fallback_text,
            "data": {"rows": rows},
            "config": {
                "chart_type": "line" if times else "bar",
                "x_field": x_field,
                "y_fields": numeric[:4],
                "y_labels": [labels[k] for k in numeric[:4]],
            },
        }
    if 2 <= len(rows) <= 8:
        dims = [key for key in keys if key != x_field]
        blocks["comparison"] = {
            "version": 1,
            "kind": "comparison",
            "title": presentation.title,
            "fallback_text": presentation.fallback_text,
            "data": {
                "dimensions": [{"key": k, "label": labels[k]} for k in dims],
                "items": [
                    {"label": row[x_field], "values": {k: row[k] for k in dims}} for row in rows
                ],
            },
        }
    for kind, block in blocks.items():
        validated = validate_block(
            block,
            run_id=presentation.run_id,
            message_id=presentation.message_id,
            index=0,
            origin=str(presentation.decision.get("origin", "shape")),
            reason_code="d1_selected",
        )
        if validated is not None:
            variants[kind] = validated.model_copy(
                update={
                    "presentation_id": presentation.presentation_id,
                    "source_refs": presentation.source_refs,
                    "source_text": presentation.source_text,
                    "snapshot_hash": hashlib.sha256(
                        json.dumps(
                            {
                                "data": validated.data,
                                "config": validated.config,
                                "actions": validated.actions,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                            allow_nan=False,
                        ).encode()
                    ).hexdigest(),
                }
            )
    return variants


class UIIntentSelector:
    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        *,
        timeout_seconds: float = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = UICompletionClient(
            base_url, api_key, model, timeout_seconds=timeout_seconds, transport=transport
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> UIIntentSelector | None:
        if not settings.ui_intent_model:
            return None
        if not settings.ui_intent_base_url or not settings.ui_intent_api_key.get_secret_value():
            raise ValueError("UI intent selection requires endpoint and key")
        return cls(
            settings.ui_intent_base_url,
            settings.ui_intent_api_key,
            settings.ui_intent_model,
            timeout_seconds=settings.ui_intent_timeout_seconds,
        )

    async def select(
        self,
        presentation: Presentation,
        *,
        request: str,
        allowed_kinds: Sequence[str],
        preferences: Sequence[dict[str, str]] = (),
        result_evidence: Sequence[dict[str, Any]] = (),
    ) -> Presentation | None:
        if presentation.actions:
            return presentation
        if (
            presentation.decision.get("origin") != "shape"
            and presentation.kind != "summary"
            and not (result_evidence and presentation.kind in {"table", "chart", "comparison"})
        ):
            return presentation
        variants = {
            k: v for k, v in presentation_variants(presentation).items() if k in allowed_kinds
        }
        choices = ["text", *variants]
        # Keep a model block's source so clients can replace its exact fence with
        # readable text even when selection fails or asks to suppress the card.
        text_fallback = (
            presentation.model_copy(
                update={"decision": {**presentation.decision, "display": "text"}}
            )
            if presentation.decision.get("origin") == "model"
            else None
        )
        fallback = (
            variants.get(presentation.kind) if presentation.kind != "summary" else text_fallback
        )
        if not variants:
            return None
        started = monotonic()
        try:
            content = await self._client.classify(
                _selection_prompt(choices),
                {
                    "request": request[:4000],
                    "available_kinds": choices,
                    "result_summary": _result_summary(presentation),
                    **({"result_evidence": list(result_evidence[:4])} if result_evidence else {}),
                    **({"preferences": list(preferences[:4])} if preferences else {}),
                },
                max_tokens=64,
                response_schema={
                    "type": "object",
                    "properties": {"kind": {"type": "string", "enum": [*choices, "unknown"]}},
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            )
            decision = UIIntentDecision.model_validate_json(content)
            if decision.kind == "unknown":
                return fallback
            if decision.kind not in choices:
                raise ValueError("selection is outside allowed data-backed kinds")
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            _LOGGER.info(
                "ui_intent fallback model=%s elapsed_ms=%d",
                self._client.model,
                (monotonic() - started) * 1000,
            )
            return fallback
        _LOGGER.info(
            "ui_intent kind=%s model=%s elapsed_ms=%d",
            decision.kind,
            self._client.model,
            (monotonic() - started) * 1000,
        )
        if decision.kind == "text":
            return (
                text_fallback.model_copy(
                    update={
                        "decision": {
                            **text_fallback.decision,
                            "reason_code": "d1_selected",
                            "selector_model": self._client.model,
                        }
                    }
                )
                if text_fallback is not None
                else None
            )
        selected = variants[decision.kind]
        return selected.model_copy(
            update={
                "decision": {
                    **selected.decision,
                    "reason_code": "d1_selected",
                    "selector_model": self._client.model,
                }
            }
        )
