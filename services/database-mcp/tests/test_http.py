import hashlib
import json
import sqlite3

from kai_database_mcp.server import create_server
from starlette.testclient import TestClient


def test_mcp_transport_auth_scope_and_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("KAI_DB_SQLITE_ROOT", str(tmp_path))
    path = tmp_path / "demo.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE orders(id INT,amount INT);INSERT INTO orders VALUES(1,100),(2,200);"
    )
    db.close()
    config = {
        "tokens": [
            {"sha256": hashlib.sha256(b"owner").hexdigest(), "sources": ["demo"]},
            {"sha256": hashlib.sha256(b"limited").hexdigest(), "sources": []},
        ],
        "sources": [{"id": "demo", "engine": "sqlite", "connection": {"path": str(path)}}],
    }
    app = create_server(config, "http://testserver/mcp").streamable_http_app()
    with TestClient(app) as client:

        def call(method, params=None, token="owner"):
            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            if token:
                headers["Authorization"] = "Bearer " + token
            return client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            )

        assert call("tools/list", token=None).status_code == 401
        assert call("tools/list", token="wrong").status_code == 401
        init = call(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert init.status_code == 200
        assert len(call("tools/list").json()["result"]["tools"]) == 8

        def tool(name, args=None, token="owner"):
            r = call("tools/call", {"name": name, "arguments": args or {}}, token).json()["result"]
            return r

        visible = tool("list_sources")
        assert "demo" in json.dumps(visible) and "connection" not in json.dumps(visible)
        assert "demo" not in json.dumps(tool("list_sources", token="limited"))
        assert tool("query", {"source_id": "demo", "sql": "SELECT * FROM orders"}, token="limited")[
            "isError"
        ]
        success = tool("query", {"source_id": "demo", "sql": "SELECT SUM(amount) FROM orders"})
        assert "300" in json.dumps(success) and not success.get("isError")
        denied = tool("query", {"source_id": "demo", "sql": "DELETE FROM orders"})
        assert "query_rejected" in json.dumps(denied)


def test_username_password_connect_is_scoped_temporary_and_redacted(monkeypatch):
    from kai_database_mcp import server

    clock = [100.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    calls = []

    def execute(source, sql, params=(), timeout=15, max_rows=200):
        calls.append((source, sql, params))
        return {"ok": True, "rows": [[1]], "columns": ["x"]}

    monkeypatch.setattr(server, "bounded_execute", execute)
    config = {
        "sources": [],
        "tokens": [
            {
                "sha256": hashlib.sha256(t.encode()).hexdigest(),
                "sources": [],
                "allow_dynamic_connections": t != "limited",
            }
            for t in ("owner", "other", "limited")
        ],
    }
    with TestClient(create_server(config, "http://testserver/mcp").streamable_http_app()) as client:

        def tool(name, args=None, token="owner"):
            r = client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer " + token,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args or {}},
                },
            )
            return r.json()["result"]

        args = dict(
            engine="dameng",
            host="db.internal",
            port=15236,
            username="FAO_RO",
            password="never-echo-this",
            schema="FAO_SENTIMENT",
        )
        assert tool("connect_database", args, "limited")["isError"]
        result = tool("connect_database", args)
        assert "never-echo-this" not in json.dumps(result)
        assert not result.get("isError"), result
        source_id = (result.get("structuredContent") or json.loads(result["content"][0]["text"]))[
            "source_id"
        ]
        assert calls[0][0]["connection"]["_password"] == args["password"]
        assert calls[0][1] == "SELECT 1 FROM DUAL"
        assert source_id in json.dumps(tool("list_sources"))
        assert source_id not in json.dumps(tool("list_sources", token="other"))
        assert "never-echo-this" not in json.dumps(tool("list_sources"))
        assert tool("test_connection", {"source_id": source_id}, "other")["isError"]
        assert not tool("list_tables", {"source_id": source_id}).get("isError")
        assert calls[-1][2] == ("FAO_SENTIMENT",)
        assert "query_rejected" in json.dumps(
            tool("query", {"source_id": source_id, "sql": "DELETE FROM orders"})
        )
        clock[0] += 3601
        assert tool("test_connection", {"source_id": source_id})["isError"]
        source_id = json.loads(tool("connect_database", args)["content"][0]["text"])["source_id"]
        assert not tool("close_connection", {"source_id": source_id}).get("isError")
        assert tool("test_connection", {"source_id": source_id})["isError"]


def test_failed_connection_does_not_register_source_or_return_driver_secrets(monkeypatch):
    from kai_database_mcp import server

    monkeypatch.setattr(
        server,
        "bounded_execute",
        lambda *a: {"ok": False, "error": "database_error", "message": "secret-from-driver"},
    )
    config = {
        "sources": [],
        "tokens": [
            {
                "sha256": hashlib.sha256(b"owner").hexdigest(),
                "sources": [],
                "allow_dynamic_connections": True,
            }
        ],
    }
    with TestClient(create_server(config, "http://testserver/mcp").streamable_http_app()) as client:
        r = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer owner",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "connect_database",
                    "arguments": dict(
                        engine="postgresql",
                        host="db.internal",
                        username="reader",
                        password="secret-from-driver",
                        database="demo",
                    ),
                },
            },
        )
        assert not json.loads(r.json()["result"]["content"][0]["text"])["ok"]
        assert "secret-from-driver" not in r.text
