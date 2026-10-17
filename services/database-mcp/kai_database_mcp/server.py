"""Streamable HTTP endpoint. Credentials stay in server-owned config/environment."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from .engine import DIALECTS, QueryRejected, bounded_execute, metadata_sql, validate_sql


class TokenVerifier:
    def __init__(self, tokens, resource=None):
        self.tokens = tokens
        self.resource = resource

    async def verify_token(self, token: str):
        digest = hashlib.sha256(token.encode()).hexdigest()
        for record in self.tokens:
            if hmac.compare_digest(digest, record["sha256"]):
                return AccessToken(
                    token=token,
                    client_id=digest[:16],
                    resource=self.resource,
                    scopes=[
                        "db:read",
                        *(
                            ["db:connect"]
                            if record.get("allow_dynamic_connections") is True
                            else []
                        ),
                        *["source:" + s for s in record["sources"]],
                    ],
                )
        return None


def load_config(path: Path):
    config = json.loads(path.read_text())
    sources = config["sources"]
    ids = [s["id"] for s in sources]
    if len(ids) != len(set(ids)) or not config.get("tokens"):
        raise ValueError("Unique data sources and authentication tokens are required")
    for source in sources:
        if source["engine"] not in DIALECTS:
            raise ValueError("Unsupported engine")
        if any(k in source["connection"] for k in ("password", "_password")):
            raise ValueError("Use a password_env secret reference")
    for token in config["tokens"]:
        if len(token["sha256"]) != 64 or not set(token["sources"]).issubset(ids):
            raise ValueError("Invalid token grants")
    return config


def create_server(config: dict, public_url: str):
    from urllib.parse import urlsplit

    host = urlsplit(public_url).netloc
    mcp = FastMCP(
        "KAI Database",
        instructions=(
            "Read-only database exploration. Use a configured source_id, or "
            "call connect_database with user-authorized host, username and "
            "password. Reuse the returned temporary source_id for metadata and"
            " queries. Never echo passwords or save them in documents."
        ),
        stateless_http=True,
        json_response=True,
        token_verifier=TokenVerifier(config["tokens"], public_url),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(public_url),
            resource_server_url=AnyHttpUrl(public_url),
            required_scopes=["db:read"],
            validate_token_resource=True,
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[],
        ),
    )
    sources = {s["id"]: s for s in config["sources"]}
    temporary_sources = {}
    ttl_seconds = 3600
    semaphore = asyncio.Semaphore(4)

    def prune():
        now = time.monotonic()
        for source_id in list(temporary_sources):
            if temporary_sources[source_id]["expires_at"] <= now:
                del temporary_sources[source_id]

    annotation = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    def permitted(source_id):
        token = get_access_token()
        prune()
        entry = temporary_sources.get(source_id)
        if (
            token is not None
            and entry
            and entry["owner"] == token.client_id
            and "db:connect" in token.scopes
        ):
            entry["expires_at"] = time.monotonic() + ttl_seconds
            return entry["source"]
        if token is None or "source:" + source_id not in token.scopes or source_id not in sources:
            raise ValueError("Data source is unavailable, expired or not authorized")
        return sources[source_id]

    async def run(source, sql, params=(), timeout=15, max_rows=200):
        async with semaphore:
            return await asyncio.to_thread(bounded_execute, source, sql, params, timeout, max_rows)

    async def metadata(source, operation, schema=None, table=None):
        if schema is None:
            schema = source.get("default_schema")
        if schema is None:
            schema = (
                source["connection"].get("database")
                if source["engine"] == "mysql"
                else source["connection"].get("user", "").upper()
                if source["engine"] == "dameng"
                else "public"
            )
        sql, params = metadata_sql(source["engine"], operation, schema, table)
        result = await run(source, sql, params, max_rows=2000)
        if source["engine"] == "sqlite" and operation == "primary_keys" and result.get("ok"):
            pk_index = result["columns"].index("pk")
            result["rows"] = sorted(
                (row for row in result["rows"] if row[pk_index]), key=lambda row: row[pk_index]
            )
            result["returned_rows"] = len(result["rows"])
        return result

    @mcp.tool(annotations=annotation)
    async def list_sources() -> dict:
        """List only authorized data-source IDs and engines; never returns connection secrets."""
        token = get_access_token()
        if token is None:
            raise ValueError("Authentication required")
        prune()
        visible = [s for s in sources.values() if "source:" + s["id"] in token.scopes]
        visible += [
            e["source"]
            for e in temporary_sources.values()
            if e["owner"] == token.client_id and "db:connect" in token.scopes
        ]
        return {
            "sources": [
                {
                    "id": s["id"],
                    "label": s.get("label", s["id"]),
                    "engine": s["engine"],
                    "readonly": True,
                }
                for s in visible
            ],
            "username_password_supported": "db:connect" in token.scopes,
        }

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
        )
    )
    async def connect_database(
        engine: Literal["mysql", "postgresql", "dameng"],
        host: str,
        username: str,
        password: str,
        port: int | None = None,
        database: str | None = None,
        schema: str | None = None,
        sslmode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] = "prefer",
    ) -> dict:
        """Connect using user-authorized host, port, database/schema, username and password.
        Tests a read-only connection and returns a temporary source_id for all other tools.
        Credentials stay in MCP process memory, expire after one hour of inactivity or
        restart, and are never returned. Do not put passwords in SQL/files/output. SQLite
        uses configured file source IDs.
        """
        token = get_access_token()
        if token is None or "db:connect" not in token.scopes:
            raise ValueError("Username/password connection is not authorized for this caller")
        if (
            not host
            or len(host) > 253
            or any(c in host for c in "/\\@?# \t\r\n")
            or not username
            or len(username) > 128
            or len(password) > 4096
        ):
            raise ValueError("Invalid connection parameters")
        port = (
            port
            if port is not None
            else {"mysql": 3306, "postgresql": 5432, "dameng": 5236}[engine]
        )
        if not 1 <= port <= 65535 or (engine != "dameng" and not database):
            raise ValueError("A valid port and database name are required")
        prune()
        if sum(e["owner"] == token.client_id for e in temporary_sources.values()) >= 32:
            raise ValueError("Temporary connection limit reached; close an unused source first")
        source_id = "connection-" + secrets.token_hex(16)
        source = {
            "id": source_id,
            "label": f"{engine} {host}:{port} / {schema or database or username}",
            "engine": engine,
            "connection_origin": "user",
            "default_schema": schema,
            "connection": {
                "host": host,
                "port": port,
                "user": username,
                "_password": password,
                "database": database or "",
                "sslmode": sslmode,
            },
        }
        result = await run(source, "SELECT 1 FROM DUAL" if engine == "dameng" else "SELECT 1")
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error"),
                "error_type": result.get("error_type"),
                "error_code": result.get("error_code"),
                "message": (
                    "Connection failed. Check host/port, database, username/password, "
                    "TLS and network access."
                ),
            }
        temporary_sources[source_id] = {
            "source": source,
            "owner": token.client_id,
            "expires_at": time.monotonic() + ttl_seconds,
        }
        return {
            "ok": True,
            "source_id": source_id,
            "engine": engine,
            "readonly": True,
            "schema": schema,
            "expires_after_idle_seconds": ttl_seconds,
            "temporary": True,
        }

    @mcp.tool(annotations=annotation)
    async def close_connection(source_id: str) -> dict:
        """Forget a temporary username/password connection and discard its credentials.
        Configured sources cannot be removed.
        """
        source = permitted(source_id)
        if source.get("connection_origin") != "user":
            raise ValueError("Only temporary connections can be closed")
        temporary_sources.pop(source_id, None)
        return {"ok": True, "source_id": source_id, "closed": True}

    @mcp.tool(annotations=annotation)
    async def test_connection(source_id: str) -> dict:
        """Open a read-only connection and perform a bounded SELECT; accepts IDs returned by
        connect_database or list_sources.
        """
        source = permitted(source_id)
        return await run(
            source, "SELECT 1 FROM DUAL" if source["engine"] == "dameng" else "SELECT 1"
        )

    @mcp.tool(annotations=annotation)
    async def list_schemas(source_id: str) -> dict:
        """List schemas visible to the configured database role."""
        return await metadata(permitted(source_id), "schemas")

    @mcp.tool(annotations=annotation)
    async def list_tables(source_id: str, schema: str | None = None) -> dict:
        """List tables/views within a schema. Truncated results are explicitly marked."""
        return await metadata(permitted(source_id), "tables", schema)

    @mcp.tool(annotations=annotation)
    async def describe_table(source_id: str, table: str, schema: str | None = None) -> dict:
        """Read columns, primary keys and foreign keys. Relationships are schema declarations,
        not proof of data cardinality.
        """
        source = permitted(source_id)
        return {
            kind: await metadata(source, kind, schema, table)
            for kind in ("columns", "primary_keys", "foreign_keys")
        }

    @mcp.tool(annotations=annotation)
    async def query(
        source_id: str, sql: str, timeout_seconds: int = 15, max_rows: int = 200
    ) -> dict:
        """Execute one read-only SELECT/CTE. No writes, procedures, locks, custom functions or
        comments. Query text may be retained in platform audit; never include secrets.
        max_rows<=2000, timeout<=60s.
        """
        source = permitted(source_id)
        try:
            validated = validate_sql(sql, source["engine"])
            # Bound rows at the database, in addition to result byte and process time limits.
            if source["engine"] == "dameng":
                bounded = f"SELECT * FROM ({validated}) kai_bounded WHERE ROWNUM <= {max_rows + 1}"
            else:
                bounded = f"SELECT * FROM ({validated}) AS kai_bounded LIMIT {max_rows + 1}"
            return await run(source, bounded, timeout=timeout_seconds, max_rows=max_rows)
        except QueryRejected as exc:
            return {"ok": False, "error": "query_rejected", "message": str(exc)}

    return mcp


def main():
    config = load_config(Path(os.environ["KAI_DB_CONFIG"]))
    public_url = os.environ.get("KAI_DB_PUBLIC_URL", "http://127.0.0.1:18865/mcp")
    server = create_server(config, public_url)
    import uvicorn

    uvicorn.run(
        server.streamable_http_app(),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "18865")),
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
