"""Bounded single-statement SQLite reads. No third-party dependencies."""

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path


READ_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info", "index_xinfo"}
ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
FORBIDDEN_FUNCTIONS = {"load_extension", "readfile", "writefile", "fts3_tokenizer"}


def authorize(action, first, second, database, trigger):
    if action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_OK if (first or "").lower() in READ_PRAGMAS else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in FORBIDDEN_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in ALLOWED else sqlite3.SQLITE_DENY


def query(database: Path, sql: str, *, timeout=60.0, max_rows=200):
    if not 0 < timeout <= 600 or not 1 <= max_rows <= 10000:
        raise ValueError("timeout must be in (0,600]; max_rows must be in [1,10000]")
    if len(sql.encode()) > 256 * 1024:
        raise ValueError("SQL exceeds 256 KiB")
    database = database.resolve(strict=True)
    started = time.monotonic()
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 5))
    try:
        # Some Python builds omit extension loading entirely.
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        # setlimit is available only in Python 3.11+. The deployed sandbox also
        # supports 3.10; query permissions and timeout do not depend on it.
        if hasattr(connection, "setlimit"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 256 * 1024)
        connection.set_authorizer(authorize)
        connection.set_progress_handler(lambda: int(time.monotonic() - started >= timeout), 1000)
        cursor = connection.execute(sql)
        if cursor.description is None:
            raise ValueError("query did not return a result set")
        rows = []
        byte_count = 0
        truncated = False
        for row in cursor:
            if time.monotonic() - started >= timeout:
                raise TimeoutError("query timed out")
            values = [{"blob_hex": v.hex()} if isinstance(v, bytes) else v for v in row]
            byte_count += len(json.dumps(values, ensure_ascii=False).encode())
            if len(rows) >= max_rows or byte_count > 8 * 1024 * 1024:
                truncated = True
                break
            rows.append(values)
        return {"columns": [c[0] for c in cursor.description], "rows": rows,
                "returned_rows": len(rows), "truncated": truncated,
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(), "readonly": True}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sql-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-rows", type=int, default=200)
    args = parser.parse_args()
    # Never overwrite any input or existing artifact, including a symlink target.
    if args.output.exists() or args.output.is_symlink():
        parser.error("output already exists; choose a new result filename")
    try:
        result = query(args.database, args.sql_file.read_text(encoding="utf-8"),
                       timeout=args.timeout, max_rows=args.max_rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "output": str(args.output),
                          "returned_rows": result["returned_rows"], "truncated": result["truncated"]}))
    except (OSError, ValueError, sqlite3.Error, TimeoutError) as exc:
        # Do not echo arbitrary SQL or database contents into error logs.
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "hint": "Check single read-only statement, input path, query budget and output path."}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
