"""Question-bank import: parse uploaded case banks into durable EvalCases.

Two carrier formats are accepted — JSON (an array of case objects, or an
object with a ``cases`` array) and CSV (a header row where ``prompt`` is
required and ``tag``/``required_tools``/``forbidden_tools``/
``terminal_statuses`` are optional, multi-value cells separated by ``|``).
Parsing is the single source of truth: the API rejects the whole upload on
any invalid row instead of silently dropping cases.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from harness.evals.models import ImportedEvalCaseRequest
from harness.evals.suite import EvalCase, EvalExpectation

MAX_IMPORTED_CASES = 200
_PROMPT_COLUMN = "prompt"
_OPTIONAL_COLUMNS = {
    "required_tools": "requiredTools",
    "forbidden_tools": "forbiddenTools",
    "terminal_statuses": "terminalStatuses",
}


class EvalImportError(ValueError):
    """Raised when an uploaded question bank cannot be parsed."""


def parse_imported_cases(
    format: str, content: str
) -> tuple[ImportedEvalCaseRequest, ...]:
    if format == "json":
        rows = _parse_json_rows(content)
    elif format == "csv":
        rows = _parse_csv_rows(content)
    else:
        raise EvalImportError(f"unsupported question-bank format: {format}")
    if not rows:
        raise EvalImportError("question bank contains no cases")
    if len(rows) > MAX_IMPORTED_CASES:
        raise EvalImportError(
            f"question bank exceeds the {MAX_IMPORTED_CASES} case limit: {len(rows)}"
        )
    return tuple(rows)


def _parse_json_rows(content: str) -> list[ImportedEvalCaseRequest]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise EvalImportError(f"question bank is not valid JSON: {error.msg}") from error
    if isinstance(payload, dict):
        payload = payload.get("cases")
    if not isinstance(payload, list):
        raise EvalImportError("JSON question bank must be an array of cases")
    rows: list[ImportedEvalCaseRequest] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise EvalImportError(f"case #{index + 1} must be an object")
        rows.append(_validate_row(item, index))
    return rows


def _parse_csv_rows(content: str) -> list[ImportedEvalCaseRequest]:
    reader = csv.DictReader(io.StringIO(content, newline=""))
    if reader.fieldnames is None:
        raise EvalImportError("CSV question bank is empty")
    header = [name.strip().lstrip("\ufeff") for name in reader.fieldnames]
    if _PROMPT_COLUMN not in header:
        raise EvalImportError(
            "CSV question bank needs a 'prompt' column; "
            f"found: {', '.join(header) or 'none'}"
        )
    rows: list[ImportedEvalCaseRequest] = []
    for index, row in enumerate(reader):
        record: dict[str, Any] = {}
        tag = (row.get("tag") or "").strip()
        if tag:
            record["tag"] = tag
        for column in ("required_tools", "forbidden_tools", "terminal_statuses"):
            value = (row.get(column) or "").strip()
            if value:
                record[_OPTIONAL_COLUMNS[column]] = tuple(
                    part.strip() for part in value.split("|") if part.strip()
                )
        prompt = (row.get(_PROMPT_COLUMN) or "").strip()
        if not prompt:
            raise EvalImportError(f"CSV case #{index + 1} has an empty prompt")
        record["prompt"] = prompt
        rows.append(_validate_row(record, index))
    return rows


def _validate_row(record: dict[str, Any], index: int) -> ImportedEvalCaseRequest:
    try:
        return ImportedEvalCaseRequest.model_validate(record)
    except ValueError as error:
        raise EvalImportError(f"case #{index + 1} is invalid: {error}") from error


def cases_from_imported(
    imports: tuple[ImportedEvalCaseRequest, ...],
) -> tuple[EvalCase, ...]:
    cases: list[EvalCase] = []
    for index, item in enumerate(imports):
        cases.append(
            EvalCase(
                id=f"import-{index + 1:03d}",
                tags=(item.tag,),
                prompt=item.prompt,
                expect=EvalExpectation(
                    terminal_statuses=item.terminal_statuses
                    or (
                        ("succeeded", "rejected")
                        if item.tag == "safety"
                        else ("succeeded",)
                    ),
                    required_tools=item.required_tools,
                    forbidden_tools=item.forbidden_tools,
                ),
            )
        )
    return tuple(cases)
