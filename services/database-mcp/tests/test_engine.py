import hashlib
import sqlite3

import pytest
from kai_database_mcp.engine import (
    QueryRejected,
    bounded_execute,
    execute,
    metadata_sql,
    validate_sql,
)
from kai_database_mcp.server import TokenVerifier


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("KAI_DB_SQLITE_ROOT", str(tmp_path))
    path = tmp_path / "orders.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY,amount NUMERIC); "
        "CREATE TABLE refunds(id INTEGER PRIMARY KEY,order_id INTEGER "
        "REFERENCES orders(id),amount NUMERIC); INSERT INTO orders "
        "VALUES(1,100),(2,200); INSERT INTO refunds "
        "VALUES(1,1,10),(2,1,20);"
    )
    db.close()
    return {"id": "demo", "engine": "sqlite", "connection": {"path": str(path)}}


@pytest.mark.parametrize("engine", ["sqlite", "mysql", "postgresql", "dameng"])
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT * INTO x FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "SELECT load_extension('x')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT dblink_exec('x','DELETE FROM t')",
        "SELECT GET_LOCK('x',10)",
        "SELECT nextval('x')",
        "SELECT SYS.EVAL('x')",
        "SELECT 1 /*! UNION SELECT 2 */",
        "ATTACH DATABASE 'x' AS other",
        "SELECT @x := 1",
        "SELECT SLEEP(10)",
    ],
)
def test_reject_dangerous_sql(engine, sql):
    with pytest.raises(QueryRejected):
        validate_sql(sql, engine)


@pytest.mark.parametrize("engine", ["sqlite", "mysql", "postgresql", "dameng"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(amount) AS total FROM orders",
        (
            "WITH r AS (SELECT order_id,SUM(amount) AS refund FROM refunds "
            "GROUP BY order_id) SELECT SUM(o.amount-COALESCE(r.refund,0)) FROM"
            " orders o LEFT JOIN r ON r.order_id=o.id"
        ),
        "SELECT id,ROW_NUMBER() OVER(ORDER BY id) FROM orders",
        (
            "SELECT COUNT(*) FROM refunds r WHERE NOT EXISTS (SELECT 1 FROM "
            "orders o WHERE o.id=r.order_id)"
        ),
    ],
)
def test_accept_analysis(engine, sql):
    assert validate_sql(sql, engine)


def test_query_and_source_unchanged(source):
    path = source["connection"]["path"]
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    result = bounded_execute(
        source,
        "SELECT (SELECT SUM(amount) FROM orders)-(SELECT SUM(amount) FROM refunds)",
        timeout=5,
    )
    assert result["ok"] and result["rows"] == [[270]] and result["readonly"]
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before


def test_sqlite_database_write_denied_even_without_parser(source):
    with pytest.raises(sqlite3.DatabaseError):
        execute(source, "DELETE FROM orders", (), 5, 10)


def test_bounds_and_schema(source):
    assert bounded_execute(source, "SELECT * FROM orders", max_rows=1)["truncated"]
    sql, params = metadata_sql("sqlite", "foreign_keys", None, "refunds")
    result = bounded_execute(source, sql, params)
    assert result["rows"][0][2:5] == ["orders", "order_id", "id"]
    with pytest.raises(QueryRejected):
        metadata_sql("sqlite", "columns", None, 'x");DROP TABLE x;--')


def test_path_escape(source, tmp_path, monkeypatch):
    root = tmp_path / "confined"
    root.mkdir()
    monkeypatch.setenv("KAI_DB_SQLITE_ROOT", str(root))
    assert bounded_execute(source, "SELECT 1")["error"] == "query_rejected"


def test_timeout(source):
    sql = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n "
        "WHERE x<1000000000) SELECT SUM(x) FROM n"
    )
    result = bounded_execute(source, sql, timeout=1)
    assert not result["ok"] and result["elapsed_seconds"] < 5


@pytest.mark.asyncio
async def test_tokens_scoped_and_invalid_rejected():
    verifier = TokenVerifier(
        [{"sha256": hashlib.sha256(b"secret").hexdigest(), "sources": ["one"]}]
    )
    assert await verifier.verify_token("wrong") is None
    token = await verifier.verify_token("secret")
    assert token.scopes == ["db:read", "source:one"]


def test_catalog_inputs_are_parameters():
    for engine in ["mysql", "postgresql", "dameng"]:
        for kind in ["tables", "columns", "primary_keys", "foreign_keys"]:
            sql, params = metadata_sql(engine, kind, "x';DROP TABLE t;--", "orders")
            assert "x';DROP" not in sql and params[0] == "x';DROP TABLE t;--"
