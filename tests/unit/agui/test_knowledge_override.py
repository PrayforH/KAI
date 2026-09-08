"""Per-thread knowledge base selection override from the composer."""

from __future__ import annotations

from typing import Any

import pytest

from harness.agui.service import _knowledge_references_override
from harness.core.errors import ConflictError


def request_with_forwarded(forwarded: Any) -> Any:  # noqa: ANN401 - minimal stub
    return type("Request", (), {"forwarded_props": forwarded})()


def test_reads_and_deduplicates_references() -> None:
    request = request_with_forwarded(
        {"knowledgeReferences": ["case-library", "policy-2026", "case-library"]}
    )
    assert _knowledge_references_override(request) == ["case-library", "policy-2026"]


def test_ignores_missing_or_empty_override() -> None:
    assert _knowledge_references_override(request_with_forwarded({})) is None
    assert _knowledge_references_override(request_with_forwarded(None)) is None
    empty = request_with_forwarded({"knowledgeReferences": []})
    assert _knowledge_references_override(empty) is None


def test_rejects_malformed_references() -> None:
    for value in ["case-library", ["Case-Library"], ["9bad"], ["a" * 200], [1]]:
        with pytest.raises(ConflictError, match="knowledge reference override is invalid"):
            _knowledge_references_override(
                request_with_forwarded({"knowledgeReferences": value})
            )
