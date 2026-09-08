from importlib import import_module
from typing import Any

import pytest
import sqlalchemy as sa


def test_agent_catalog_projection_backfills_before_tightening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("migrations.versions.0028_agent_catalog_projection")
    added: list[str] = []
    altered: list[str] = []
    dropped: list[str] = []
    statements: list[str] = []

    def add_column(_table: str, column: sa.Column[Any]) -> None:
        added.append(str(column.name))

    def alter_column(_table: str, column: str, **_kwargs: object) -> None:
        altered.append(column)

    def drop_column(_table: str, column: str) -> None:
        dropped.append(column)

    monkeypatch.setattr(migration.op, "add_column", add_column)
    monkeypatch.setattr(migration.op, "alter_column", alter_column)
    monkeypatch.setattr(migration.op, "drop_column", drop_column)
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    migration.downgrade()

    assert added == [
        "status",
        "manifest_hash",
        "package_hash",
        "created_at",
        "catalog_manifest",
    ]
    sql = " ".join(statements)
    assert "payload::jsonb -> 'snapshot' -> 'manifest'" in sql
    assert "catalog_manifest" in sql
    assert altered == ["status", "manifest_hash", "created_at", "catalog_manifest"]
    assert dropped == [
        "catalog_manifest",
        "created_at",
        "package_hash",
        "manifest_hash",
        "status",
    ]
