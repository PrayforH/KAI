import hashlib
import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).parents[2]
    / "agents/ontology-data-agent/skills/ontology-data-analysis/scripts/sqlite_readonly.py"
)
spec = importlib.util.spec_from_file_location("ontology_sqlite", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "data.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE orders(id INTEGER PRIMARY KEY, amount INTEGER);"
            "INSERT INTO orders VALUES(1,100),(2,200);"
            "CREATE TABLE refunds(id INTEGER PRIMARY KEY, order_id INTEGER, amount INTEGER);"
            "INSERT INTO refunds VALUES(1,1,10),(2,1,20);"
        )
    return path


def test_totals_and_original_unchanged(database: Path) -> None:
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = module.query(
        database, "SELECT SUM(amount), SUM(amount)-(SELECT SUM(amount) FROM refunds) FROM orders"
    )
    assert result["rows"] == [[300, 270]]
    assert not result["truncated"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET amount=0",
        "DROP TABLE orders",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA writable_schema=ON",
        "PRAGMA query_only=OFF",
        "SELECT load_extension('bad')",
        "SELECT 1; SELECT 2",
        "VACUUM INTO 'copy.sqlite'",
        "WITH x AS (SELECT 1) DELETE FROM orders",
    ],
)
def test_rejects_mutations_and_multiple_statements(database: Path, sql: str) -> None:
    before = database.read_bytes()
    with pytest.raises(sqlite3.Error):
        module.query(database, sql)
    assert database.read_bytes() == before


def test_truncation_explicit(database: Path) -> None:
    result = module.query(database, "SELECT * FROM orders ORDER BY id", max_rows=1)
    assert result["rows"] == [[1, 100]]
    assert result["truncated"]


def test_metadata(database: Path) -> None:
    assert module.query(database, "PRAGMA table_info('orders')")["returned_rows"] == 2
    assert (
        module.query(database, "SELECT name FROM sqlite_schema WHERE type='table'")["returned_rows"]
        == 2
    )


def test_timeout(database: Path) -> None:
    with pytest.raises((sqlite3.OperationalError, TimeoutError)):
        module.query(
            database,
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n",
            timeout=0.01,
        )


def test_python310_connection_without_setlimit(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = sqlite3.connect

    class CompatibleConnection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.connection = connect(*args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            if name in {"setlimit", "enable_load_extension"}:
                raise AttributeError(name)
            return getattr(self.connection, name)

    monkeypatch.setattr(module.sqlite3, "connect", CompatibleConnection)
    assert module.query(database, "SELECT SUM(amount) FROM orders")["rows"] == [[300]]
    with pytest.raises(sqlite3.Error):
        module.query(database, "DELETE FROM orders")
