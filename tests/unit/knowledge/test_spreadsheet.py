"""Spreadsheet parsing for the knowledge document viewer."""

from __future__ import annotations

import io

from openpyxl import Workbook

from harness.knowledge.spreadsheet import is_spreadsheet, parse_spreadsheet


def test_detects_spreadsheet_suffixes() -> None:
    assert is_spreadsheet("分类表.xlsx")
    assert is_spreadsheet("分类表.XLS")
    assert is_spreadsheet("data.csv")
    assert not is_spreadsheet("起诉书.pdf")
    assert not is_spreadsheet("笔记.md")


def test_parses_first_sheet_into_a_grid() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "分类"
    sheet.append(["行业", "表现", "监管部门"])
    sheet.append(["民间投融资", "投资理财名义", "金融办"])
    sheet.append(["养老服务", "预付费返利", "民政部门"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    grid = parse_spreadsheet("分类表.xlsx", buffer.getvalue())
    assert grid is not None
    assert grid.sheet == "分类"
    assert grid.rows[0] == ("行业", "表现", "监管部门")
    assert grid.rows[1] == ("民间投融资", "投资理财名义", "金融办")
    assert len(grid.rows) == 3
    assert grid.truncated is False


def test_trims_trailing_empty_rows_and_marks_truncation() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for index in range(5):
        sheet.append([f"r{index}"])
    sheet.append([None])
    buffer = io.BytesIO()
    workbook.save(buffer)

    grid = parse_spreadsheet("t.xlsx", buffer.getvalue(), max_rows=3)
    assert grid is not None
    assert len(grid.rows) == 3
    assert grid.truncated is True


def test_unparseable_bytes_return_none() -> None:
    assert parse_spreadsheet("broken.xlsx", b"not a workbook") is None
