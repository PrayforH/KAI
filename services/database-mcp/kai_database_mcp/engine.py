"""Bounded reads, with a separate process and a fresh read-only transaction per call."""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import multiprocessing
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

DIALECTS = {"sqlite": "sqlite", "mysql": "mysql", "postgresql": "postgres", "dameng": "oracle"}
FUNCTIONS = frozenset(
    """
EXISTS COUNT SUM AVG MIN MAX COALESCE NULLIF IF CASE CAST TRY_CAST EXTRACT DATE DATE_ADD
DATE_SUB DATE_DIFF DATEDIFF TIMESTAMP TIMESTAMPDIFF TIMESTAMP_DIFF DATE_TRUNC TIMESTAMP_TRUNC
STRFTIME YEAR MONTH DAY DAY_OF_MONTH DAYOFMONTH DAYOFWEEK WEEK HOUR MINUTE SECOND NOW
CURRENT_DATE CURRENT_TIMESTAMP CURRENT_TIME TO_CHAR TO_DATE TO_TIMESTAMP DATE_FORMAT ABS
ROUND FLOOR CEIL CEILING MOD POWER SQRT SIGN LOWER UPPER TRIM LTRIM RTRIM LENGTH CHAR_LENGTH
CHARACTER_LENGTH CONCAT CONCAT_WS SUBSTRING SUBSTR REPLACE LEFT RIGHT INSTR POSITION
ROW_NUMBER RANK DENSE_RANK LAG LEAD FIRST_VALUE LAST_VALUE NTH_VALUE NTILE PERCENT_RANK
CUME_DIST STDDEV STDDEV_POP STDDEV_SAMP VARIANCE VAR_POP VAR_SAMP GREATEST LEAST ARRAY_AGG
STRING_AGG GROUP_CONCAT IIF IFNULL NVL
""".split()
)


class QueryRejectedError(ValueError):
    pass


# Preserve the original import name for service clients.
QueryRejected = QueryRejectedError


def validate_sql(sql: str, engine: str) -> str:
    if engine not in DIALECTS or not sql.strip() or len(sql.encode()) > 65536:
        raise QueryRejected("Unsupported engine or SQL length (maximum 64 KiB)")
    # Do not allow executable MySQL comments, hints, or comment-based parser differences.
    if any(token in sql for token in ("/*", "--", "#")):
        raise QueryRejected("SQL comments are not accepted")
    try:
        statements = sqlglot.parse(sql, read=DIALECTS[engine])
    except sqlglot.errors.ParseError:
        raise QueryRejected("SQL could not be parsed; use one SELECT statement") from None
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise QueryRejected("Only one SELECT/CTE/set query is allowed")
    tree = statements[0]
    forbidden = {
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "Command",
        "Copy",
        "Into",
        "Lock",
        "Transaction",
        "Set",
        "Use",
        "Grant",
        "Revoke",
        "Execute",
        "Pragma",
        "Attach",
        "Detach",
        "LoadData",
        "NextValueFor",
        "SessionParameter",
        "PropertyEQ",
    }
    for node in tree.walk():
        if type(node).__name__ in forbidden:
            raise QueryRejected("Writes, locks, session changes and external access are forbidden")
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if name.upper() not in FUNCTIONS:
                raise QueryRejected("SQL function is outside the read-only allowlist")
            if isinstance(node.parent, exp.Dot):
                raise QueryRejected("Qualified/custom SQL functions are not allowed")
    return sql.rstrip().rstrip(";")


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)  # Financial decimals retain exact precision.
    if isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"byte_length": len(value), "omitted": True}
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def _password(connection: dict) -> str:
    if "_password" in connection:
        return connection["_password"]
    name = connection.get("password_env")
    if not name or name not in os.environ:
        raise QueryRejected("Configured database secret is unavailable")
    return os.environ[name]


def connect(source: dict, timeout: int):
    engine = source["engine"]
    c = source["connection"]
    if engine == "sqlite":
        root = Path(os.environ.get("KAI_DB_SQLITE_ROOT", "/data/sqlite")).resolve(strict=True)
        path = Path(c["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise QueryRejected("SQLite file is outside the configured data directory")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 5))
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        if hasattr(db, "enable_load_extension"):
            db.enable_load_extension(False)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        started = time.monotonic()
        db.set_progress_handler(lambda: int(time.monotonic() - started > timeout), 1000)
        allowed = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE,
        }

        def authorize(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_PRAGMA:
                return (
                    sqlite3.SQLITE_OK
                    if first in {"table_info", "foreign_key_list", "index_list", "index_info"}
                    else sqlite3.SQLITE_DENY
                )
            if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in {
                "load_extension",
                "readfile",
                "writefile",
                "fts3_tokenizer",
            }:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

        db.set_authorizer(authorize)
        return db
    if source.get("read_only_role") is not True and source.get("connection_origin") != "user":
        raise QueryRejected("A dedicated read-only database account must be configured")
    args = dict(host=c["host"], port=int(c["port"]), user=c["user"], password=_password(c))
    if engine == "postgresql":
        import psycopg

        return psycopg.connect(
            **args,
            dbname=c["database"],
            connect_timeout=min(timeout, 10),
            options=(
                f"-c default_transaction_read_only=on -c statement_timeout="
                f"{timeout * 1000} -c lock_timeout=2000 -c "
                f"search_path=pg_catalog,public"
            ),
            sslmode=c.get("sslmode", "verify-full"),
            **({"sslrootcert": c["sslrootcert"]} if c.get("sslrootcert") else {}),
        )
    if engine == "mysql":
        import pymysql

        ssl = {"ca": c["ssl_ca"], "check_hostname": True} if c.get("ssl_ca") else None
        db = pymysql.connect(
            **args,
            database=c["database"],
            connect_timeout=min(timeout, 10),
            read_timeout=timeout,
            write_timeout=timeout,
            autocommit=False,
            local_infile=False,
            ssl=ssl,
            cursorclass=pymysql.cursors.SSCursor,
        )
        try:
            with db.cursor() as cursor:
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}")
                cursor.execute("START TRANSACTION READ ONLY")
            return db
        except Exception:
            db.close()
            raise
    if engine == "dameng":
        import dmPython

        return dmPython.connect(
            server=args["host"],
            port=args["port"],
            user=args["user"],
            password=args["password"],
            autoCommit=False,
            access_mode=dmPython.DSQL_MODE_READ_ONLY,
            login_timeout=min(timeout, 10) * 1000,
            connection_timeout=timeout,
            **({"ssl_path": c["ssl_path"]} if c.get("ssl_path") else {}),
        )
    raise QueryRejected("Unsupported database engine")


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[\w$]+", value, re.UNICODE) or len(value) > 128:
        raise QueryRejected("Invalid schema/table identifier")
    return value


def metadata_sql(engine: str, operation: str, schema: str | None, table: str | None):
    """Trusted catalog queries; values use the driver's bind parameters."""
    if engine == "sqlite":
        if operation == "schemas":
            return "SELECT 'main' AS schema_name", ()
        if operation == "tables":
            return (
                (
                    "SELECT name AS table_name, type AS table_type FROM sqlite_schema "
                    "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER"
                    " BY name"
                ),
                (),
            )
        name = _identifier(table or "")
        if operation == "foreign_keys":
            return f'PRAGMA foreign_key_list("{name}")', ()
        # table_info includes the primary-key ordinal (pk); callers can distinguish composite keys.
        return f'PRAGMA table_info("{name}")', ()
    placeholder = "?" if engine == "dameng" else "%s"
    if engine == "dameng":
        if operation == "schemas":
            return "SELECT DISTINCT OWNER AS SCHEMA_NAME FROM ALL_TABLES ORDER BY OWNER", ()
        if operation == "tables":
            return "SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER=? ORDER BY TABLE_NAME", (
                schema or "",
            )
        if operation == "primary_keys":
            return (
                (
                    "SELECT cc.COLUMN_NAME,cc.POSITION FROM ALL_CONSTRAINTS c JOIN "
                    "ALL_CONS_COLUMNS cc ON c.OWNER=cc.OWNER AND "
                    "c.CONSTRAINT_NAME=cc.CONSTRAINT_NAME WHERE c.CONSTRAINT_TYPE='P' "
                    "AND c.OWNER=? AND c.TABLE_NAME=? ORDER BY cc.POSITION"
                ),
                (schema or "", table or ""),
            )
        if operation == "foreign_keys":
            return (
                (
                    "SELECT c.CONSTRAINT_NAME,cc.COLUMN_NAME,rc.OWNER AS "
                    "TARGET_SCHEMA,rc.TABLE_NAME AS TARGET_TABLE,rcc.COLUMN_NAME AS "
                    "TARGET_COLUMN,cc.POSITION FROM ALL_CONSTRAINTS c JOIN "
                    "ALL_CONS_COLUMNS cc ON c.OWNER=cc.OWNER AND "
                    "c.CONSTRAINT_NAME=cc.CONSTRAINT_NAME JOIN ALL_CONSTRAINTS rc ON "
                    "c.R_OWNER=rc.OWNER AND c.R_CONSTRAINT_NAME=rc.CONSTRAINT_NAME "
                    "JOIN ALL_CONS_COLUMNS rcc ON rc.OWNER=rcc.OWNER AND "
                    "rc.CONSTRAINT_NAME=rcc.CONSTRAINT_NAME AND "
                    "cc.POSITION=rcc.POSITION WHERE c.CONSTRAINT_TYPE='R' AND "
                    "c.OWNER=? AND c.TABLE_NAME=? ORDER BY "
                    "c.CONSTRAINT_NAME,cc.POSITION"
                ),
                (schema or "", table or ""),
            )
        return (
            (
                "SELECT COLUMN_NAME,DATA_TYPE,DATA_LENGTH,DATA_PRECISION,DATA_SCAL"
                "E,NULLABLE FROM ALL_TAB_COLUMNS WHERE OWNER=? AND TABLE_NAME=? "
                "ORDER BY COLUMN_ID"
            ),
            (schema or "", table or ""),
        )
    if operation == "schemas":
        return "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name", ()
    if operation == "tables":
        return (
            (
                f"SELECT table_name,table_type FROM information_schema.tables "
                f"WHERE table_schema={placeholder} ORDER BY table_name"
            ),
            (schema or "public",),
        )
    if operation == "primary_keys" and engine == "postgresql":
        # information_schema.table_constraints hides constraints from roles with
        # SELECT-only privileges. pg_catalog exposes the declared key safely.
        return (
            (
                "SELECT a.attname AS column_name,k.ordinal_position FROM "
                "pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON "
                "t.oid=i.indrelid JOIN pg_catalog.pg_namespace n ON "
                "n.oid=t.relnamespace CROSS JOIN LATERAL unnest(i.indkey) WITH "
                "ORDINALITY k(attnum,ordinal_position) JOIN "
                "pg_catalog.pg_attribute a ON a.attrelid=t.oid AND "
                "a.attnum=k.attnum WHERE i.indisprimary AND n.nspname=%s AND "
                "t.relname=%s ORDER BY k.ordinal_position"
            ),
            (schema or "public", table or ""),
        )
    if operation == "primary_keys":
        return (
            (
                f"SELECT k.column_name,k.ordinal_position FROM "
                f"information_schema.table_constraints c JOIN "
                f"information_schema.key_column_usage k ON "
                f"c.constraint_catalog=k.constraint_catalog AND "
                f"c.constraint_schema=k.constraint_schema AND "
                f"c.constraint_name=k.constraint_name AND "
                f"c.table_name=k.table_name WHERE c.constraint_type='PRIMARY KEY' "
                f"AND c.table_schema={placeholder} AND c.table_name={placeholder} "
                f"ORDER BY k.ordinal_position"
            ),
            (schema or "public", table or ""),
        )
    if operation == "foreign_keys" and engine == "mysql":
        return (
            (
                "SELECT constraint_name,column_name,referenced_table_schema AS "
                "target_schema,referenced_table_name AS "
                "target_table,referenced_column_name AS "
                "target_column,ordinal_position FROM "
                "information_schema.key_column_usage WHERE table_schema=%s AND "
                "table_name=%s AND referenced_table_name IS NOT NULL ORDER BY "
                "constraint_name,ordinal_position"
            ),
            (schema or "", table or ""),
        )
    if operation == "foreign_keys":
        return (
            (
                "SELECT c.conname,a.attname AS column_name,tn.nspname AS "
                "target_schema,t.relname AS target_table,ta.attname AS "
                "target_column,k.n AS ordinal_position FROM "
                "pg_catalog.pg_constraint c JOIN pg_catalog.pg_class f ON "
                "c.conrelid=f.oid JOIN pg_catalog.pg_namespace fn ON "
                "f.relnamespace=fn.oid JOIN pg_catalog.pg_class t ON "
                "c.confrelid=t.oid JOIN pg_catalog.pg_namespace tn ON "
                "t.relnamespace=tn.oid CROSS JOIN LATERAL "
                "generate_subscripts(c.conkey,1) k(n) JOIN pg_catalog.pg_attribute"
                " a ON a.attrelid=f.oid AND a.attnum=c.conkey[k.n] JOIN "
                "pg_catalog.pg_attribute ta ON ta.attrelid=t.oid AND "
                "ta.attnum=c.confkey[k.n] WHERE c.contype='f' AND fn.nspname=%s "
                "AND f.relname=%s ORDER BY c.conname,k.n"
            ),
            (schema or "public", table or ""),
        )
    return (
        (
            f"SELECT column_name,data_type,is_nullable,column_default FROM "
            f"information_schema.columns WHERE table_schema={placeholder} AND "
            f"table_name={placeholder} ORDER BY ordinal_position"
        ),
        (schema or "public", table or ""),
    )


def execute(source: dict, sql: str, params: list | tuple, timeout: int, max_rows: int) -> dict:
    db = connect(source, timeout)
    try:
        cursor = db.cursor(name="kai_readonly") if source["engine"] == "postgresql" else db.cursor()
        try:
            cursor.execute(sql, params) if params else cursor.execute(sql)
            if cursor.description is None:
                raise QueryRejected("Statement returned no result set")
            columns = [col[0] for col in cursor.description]
            rows = []
            size = 0
            truncated = False
            for _ in range(max_rows + 1):
                row = cursor.fetchone()
                if row is None:
                    break
                values = [_normalize(v) for v in row]
                size += len(json.dumps(values, ensure_ascii=False, default=str).encode())
                if len(rows) == max_rows or size > 1024 * 1024:
                    truncated = True
                    break
                rows.append(values)
            return {
                "source_id": source["id"],
                "engine": source["engine"],
                "columns": columns,
                "rows": rows,
                "returned_rows": len(rows),
                "truncated": truncated,
                "readonly": True,
                "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            }
        finally:
            cursor.close()
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _child(pipe, source, sql, params, timeout, max_rows):
    try:
        if sys.platform == "linux":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        pipe.send({"ok": True, **execute(source, sql, params, timeout, max_rows)})
    except QueryRejected as exc:
        pipe.send({"ok": False, "error": "query_rejected", "message": str(exc)})
    except Exception as exc:
        code_match = re.search(r"\bCODE\s*:\s*(-?\d+)", str(exc))
        error_code = next((a for a in exc.args if isinstance(a, int)), None)
        if error_code is None and code_match:
            error_code = int(code_match.group(1))
        # Driver messages can echo SQL, values, hosts and passwords. Return only a class name.
        pipe.send(
            {
                "ok": False,
                "error": "database_error",
                "error_type": type(exc).__name__,
                "error_code": error_code,
                "message": "Check connection, read-only role, SQL dialect and query budget.",
            }
        )
    finally:
        pipe.close()


def bounded_execute(source: dict, sql: str, params=(), timeout=15, max_rows=200) -> dict:
    if not 1 <= timeout <= 60 or not 1 <= max_rows <= 2000:
        raise QueryRejected("timeout must be 1..60 seconds; max_rows must be 1..2000")
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child, args=(child, source, sql, params, timeout, max_rows), daemon=True
    )
    started = time.monotonic()
    try:
        process.start()
        child.close()
        if parent.poll(timeout + 2):
            try:
                result = parent.recv()
            except EOFError:
                result = {"ok": False, "error": "worker_failed"}
        else:
            result = {
                "ok": False,
                "error": "query_timeout",
                "message": "Database connection worker was terminated.",
            }
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
            process.close()
