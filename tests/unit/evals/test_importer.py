from __future__ import annotations

import json

import pytest

from harness.evals.importer import (
    EvalImportError,
    cases_from_imported,
    parse_imported_cases,
)
from harness.evals.models import ImportEvalDatasetRequest


def test_parses_a_json_array_of_cases_with_camel_case_keys() -> None:
    rows = parse_imported_cases(
        "json",
        json.dumps(
            [
                {"prompt": "整理公司公开信息", "tag": "happy"},
                {
                    "prompt": "用户要求跳过来源核验",
                    "tag": "safety",
                    "forbiddenTools": ["Write", "Bash"],
                    "terminalStatuses": ["succeeded", "rejected"],
                },
            ]
        ),
    )

    assert [row.tag for row in rows] == ["happy", "safety"]
    assert rows[1].forbidden_tools == ("Write", "Bash")
    assert rows[0].required_tools == ()


def test_parses_a_json_object_with_a_cases_wrapper() -> None:
    rows = parse_imported_cases(
        "json", json.dumps({"cases": [{"prompt": "核对发票例外标记"}]})
    )
    assert len(rows) == 1
    assert rows[0].tag == "happy"


def test_parses_csv_rows_with_pipe_separated_multi_values() -> None:
    content = (
        "prompt,tag,required_tools,forbidden_tools,terminal_statuses\n"
        "整理公开信息,happy,tavily-readonly,,succeeded\n"
        "跳过来源核验,safety,,Write|Bash,succeeded|rejected\n"
    )
    rows = parse_imported_cases("csv", content)

    assert [row.tag for row in rows] == ["happy", "safety"]
    assert rows[0].required_tools == ("tavily-readonly",)
    assert rows[1].forbidden_tools == ("Write", "Bash")
    assert rows[1].terminal_statuses == ("succeeded", "rejected")


def test_csv_without_prompt_column_is_rejected() -> None:
    with pytest.raises(EvalImportError, match="needs a 'prompt' column"):
        parse_imported_cases("csv", "tag,required_tools\nhappy,\n")


def test_invalid_tag_is_rejected_with_row_position() -> None:
    with pytest.raises(EvalImportError, match="case #2 is invalid"):
        parse_imported_cases("json", json.dumps([{"prompt": "a"}, {"prompt": "b", "tag": "grumpy"}]))


def test_empty_bank_and_limit_are_rejected() -> None:
    with pytest.raises(EvalImportError, match="no cases"):
        parse_imported_cases("json", "[]")
    with pytest.raises(EvalImportError, match="case limit"):
        parse_imported_cases(
            "json", json.dumps([{"prompt": f"case-{index}"} for index in range(201)])
        )


def test_cases_from_imported_maps_defaults_by_tag() -> None:
    rows = parse_imported_cases(
        "json",
        json.dumps(
            [
                {"prompt": "正常场景", "tag": "happy"},
                {"prompt": "安全边界", "tag": "safety"},
            ]
        ),
    )
    cases = cases_from_imported(rows)

    assert [case.id for case in cases] == ["import-001", "import-002"]
    assert cases[0].expect.terminal_statuses == ("succeeded",)
    assert cases[1].expect.terminal_statuses == ("succeeded", "rejected")
    assert all(case.input_files == () for case in cases)


def test_import_request_model_accepts_aliases() -> None:
    request = ImportEvalDatasetRequest.model_validate(
        {
            "draftId": "draft_1",
            "expectedRevision": 3,
            "name": "题库",
            "format": "csv",
            "content": "prompt\n你好",
        }
    )
    assert request.draft_id == "draft_1"
    assert request.expected_revision == 3
    assert request.required is True
