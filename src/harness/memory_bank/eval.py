from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from harness.memory_bank.service import MemoryBankService


@dataclass(frozen=True)
class MemoryRecallEvalCase:
    name: str
    tenant_id: str
    user_id: str
    agent_name: str
    query: str
    expected_entry_ids: frozenset[str]
    forbidden_entry_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MemoryRecallEvalResult:
    name: str
    recalled_entry_ids: tuple[str, ...]
    precision: float
    recall: float
    passed: bool


class MemoryRecallEvalRunner:
    """Deterministic recall evaluator used as a release gate for memory changes."""

    def __init__(self, service: MemoryBankService) -> None:
        self._service = service

    async def run(
        self, cases: Sequence[MemoryRecallEvalCase]
    ) -> tuple[MemoryRecallEvalResult, ...]:
        results: list[MemoryRecallEvalResult] = []
        for case in cases:
            hits = await self._service.search(
                case.tenant_id,
                case.user_id,
                case.agent_name,
                case.query,
                limit=50,
            )
            recalled = tuple(hit.entry.entry_id for hit in hits)
            recalled_set = frozenset(recalled)
            correct = recalled_set & case.expected_entry_ids
            precision = len(correct) / len(recalled_set) if recalled_set else 1.0
            recall = len(correct) / len(case.expected_entry_ids) if case.expected_entry_ids else 1.0
            passed = (
                case.expected_entry_ids <= recalled_set
                and not (case.forbidden_entry_ids & recalled_set)
                and (not recalled_set if not case.expected_entry_ids else True)
            )
            results.append(
                MemoryRecallEvalResult(
                    name=case.name,
                    recalled_entry_ids=recalled,
                    precision=precision,
                    recall=recall,
                    passed=passed,
                )
            )
        return tuple(results)
