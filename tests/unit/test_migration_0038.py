# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownParameterType=false, reportMissingParameterType=false
"""Revision 0038 must describe its schema independently of today's ORM.

Revision 0001 replays the live ``Base.metadata``, so a revision that also read
the current model would silently change meaning whenever the model changes.
These tests pin the opposite: 0038's definition is inline, guarded, and agrees
with the ORM only because both are correct today.
"""

import ast
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy import Table

from harness.storage.models import RunExecutionCommandRow

MIGRATION = "migrations/versions/0038_run_execution_commands.py"


class FakeInspector:
    def __init__(self, present: bool) -> None:
        self._present = present

    def has_table(self, name: str) -> bool:
        return self._present


class Recorder:
    def __init__(self, *, table_present: bool) -> None:
        self.tables: list[tuple[str, tuple[object, ...]]] = []
        self.indexes: list[tuple[object, ...]] = []
        self.dropped: list[str] = []
        self._table_present = table_present

    def upgrade(self) -> None:
        migration = import_module("migrations.versions.0038_run_execution_commands")
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(migration.sa, "inspect", lambda bind: FakeInspector(self._table_present))
            patch.setattr(migration.op, "get_bind", lambda: object())
            patch.setattr(
                migration.op,
                "create_table",
                lambda name, *args, **kwargs: self.tables.append((name, args)),
            )
            patch.setattr(
                migration.op,
                "create_index",
                lambda *args, **kwargs: self.indexes.append(args),
            )
            patch.setattr(
                migration.op, "drop_table", lambda name: self.dropped.append(name)
            )
            migration.upgrade()
            migration.downgrade()


def test_upgrade_declares_every_column_the_model_declares() -> None:
    recorder = Recorder(table_present=False)

    recorder.upgrade()

    assert len(recorder.tables) == 1
    name, args = recorder.tables[0]
    assert name == "run_execution_commands"
    declared = {
        (column.name, str(column.type), column.nullable)
        for column in args
        if isinstance(column, sa.Column)
    }
    assert declared == {
        (column.name, str(column.type), column.nullable)
        for column in RunExecutionCommandRow.__table__.columns
    }
    assert any(
        isinstance(item, sa.PrimaryKeyConstraint) for item in args
    ), "the command id must remain the primary key"


def test_upgrade_creates_every_index_the_model_declares() -> None:
    recorder = Recorder(table_present=False)

    recorder.upgrade()

    created_indexes = {args[0] for args in recorder.indexes}
    declared_indexes = cast(Table, RunExecutionCommandRow.__table__).indexes
    assert created_indexes == {index.name for index in declared_indexes}


def test_upgrade_is_a_no_op_when_revision_0001_already_replayed_the_table() -> None:
    """A brand-new database already has the table by the time 0038 runs."""

    recorder = Recorder(table_present=True)

    recorder.upgrade()

    assert recorder.tables == []
    assert recorder.indexes == []
    assert recorder.dropped == ["run_execution_commands"]


def test_the_revision_never_reads_the_live_orm_metadata() -> None:
    tree = ast.parse(Path(MIGRATION).read_text(encoding="utf-8"))

    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "harness.storage.models" not in imported
    attributes = {
        f"{node.value.id}.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    assert "Base.metadata" not in attributes
