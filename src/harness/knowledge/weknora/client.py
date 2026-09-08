"""HTTP client for the WeKnora REST API.

The client owns authentication (JWT login with re-login on expiry) and the
response envelope handling: WeKnora endpoints return either a bare JSON body
or a ``{"success": ..., "data": ...}`` envelope with structured errors.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from pydantic import SecretStr

from harness.knowledge.weknora.configuration import WeknoraSettings


class WeknoraError(RuntimeError):
    """A WeKnora API call failed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WeknoraClient:
    def __init__(self, settings: WeknoraSettings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._login_lock = asyncio.Lock()
        base = settings.base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{base}/api/v1",
            timeout=httpx.Timeout(settings.timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _authenticate(self) -> str:
        async with self._login_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            password = self._settings.password
            secret = (
                password.get_secret_value() if isinstance(password, SecretStr) else str(password)
            )
            response = await self._client.post(
                "/auth/login",
                json={"email": self._settings.email, "password": secret},
            )
            payload = self._unwrap(response)
            token = payload.get("token")
            if not isinstance(token, str) or not token:
                raise WeknoraError("weknora login response did not contain a token")
            self._token = token
            # Conservative validity window; a 401 triggers a fresh login anyway.
            self._token_expires_at = time.monotonic() + 1_800
            return token

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        if response.status_code == 401:
            raise WeknoraError("weknora request unauthorized", status_code=401)
        if response.status_code >= 400:
            raise WeknoraError(
                f"weknora request failed: HTTP {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise WeknoraError("weknora returned a non-JSON response") from error
        if isinstance(payload, dict):
            error = payload.get("error")
            if payload.get("success") is False or error:
                message = "weknora request rejected"
                if isinstance(error, dict):
                    message = str(error.get("message") or message)
                raise WeknoraError(message, status_code=response.status_code)
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        reauthenticated: bool = False,
    ) -> Any:
        token = await self._authenticate()
        response = await self._client.request(
            method,
            path,
            json=json,
            params=params,
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401 and not reauthenticated:
            self._token = None
            return await self._request(
                method,
                path,
                json=json,
                params=params,
                files=files,
                reauthenticated=True,
            )
        payload = self._unwrap(response)
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def get_data(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post_data(self, path: str, *, json: Any = None) -> Any:
        return await self._request("POST", path, json=json)

    # --- knowledge bases -------------------------------------------------

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str,
        indexing_strategy: dict[str, bool],
    ) -> dict[str, Any]:
        payload = await self.post_data(
            "/knowledge-bases",
            json={
                "name": name,
                "description": description,
                "config": {
                    "indexing_strategy": indexing_strategy,
                    "embedding_model_id": self._settings.embedding_model,
                },
            },
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise WeknoraError("weknora knowledge base creation returned no id")
        return payload

    async def delete_knowledge_base(self, base_id: str) -> None:
        await self._request("DELETE", f"/knowledge-bases/{base_id}")

    async def hybrid_search(
        self,
        base_id: str,
        query: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = await self.post_data(
            f"/knowledge-bases/{base_id}/hybrid-search",
            json={"query_text": query, "match_count": limit},
        )
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    # --- documents --------------------------------------------------------

    async def list_documents(self, base_id: str) -> list[dict[str, Any]]:
        payload = await self.get_data(f"/knowledge-bases/{base_id}/knowledge")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("items") or []
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    async def get_document(self, document_id: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledge/{document_id}")
        if not isinstance(payload, dict):
            raise WeknoraError(f"weknora document not found: {document_id}")
        return payload

    async def upload_document(
        self,
        base_id: str,
        *,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            f"/knowledge-bases/{base_id}/knowledge/file",
            files={"file": (filename, content)},
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise WeknoraError("weknora document upload returned no id")
        return payload

    async def create_manual_document(
        self,
        base_id: str,
        *,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        payload = await self.post_data(
            f"/knowledge-bases/{base_id}/knowledge/manual",
            json={"title": title, "content": content},
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise WeknoraError("weknora manual document creation returned no id")
        return payload

    async def delete_document(self, document_id: str) -> None:
        await self._request("DELETE", f"/knowledge/{document_id}")

    async def reparse_document(self, document_id: str) -> None:
        await self.post_data(f"/knowledge/{document_id}/reparse")

    # --- chunks -----------------------------------------------------------

    async def list_chunks(
        self,
        document_id: str,
        *,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = await self.get_data(
                f"/chunks/{document_id}",
                params={"page": page, "page_size": page_size},
            )
            rows: list[Any]
            if isinstance(payload, dict):
                rows = payload.get("data") or []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []
            chunks.extend(item for item in rows if isinstance(item, dict))
            total = payload.get("total") if isinstance(payload, dict) else None
            if not rows or (isinstance(total, int) and len(chunks) >= total):
                break
        return chunks

    async def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        payload = await self.get_data(f"/chunks/by-id/{chunk_id}")
        if isinstance(payload, dict):
            inner = payload.get("data")
            if isinstance(inner, dict):
                return inner
            return payload
        return None

    # --- wiki -------------------------------------------------------------

    async def list_wiki_pages(self, base_id: str) -> list[dict[str, Any]]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/pages")
        if isinstance(payload, dict):
            rows = payload.get("pages") or payload.get("data") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        return [item for item in rows if isinstance(item, dict)]

    async def get_wiki_page(self, base_id: str, slug: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/pages/{slug}")
        if isinstance(payload, dict):
            inner = payload.get("data")
            if isinstance(inner, dict):
                return inner
            return payload
        raise WeknoraError(f"weknora wiki page not found: {slug}")

    async def search_wiki_pages(
        self,
        base_id: str,
        query: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = await self.get_data(
            f"/knowledgebase/{base_id}/wiki/search",
            params={"q": query, "limit": limit},
        )
        if isinstance(payload, dict):
            rows = payload.get("pages") or payload.get("data") or payload.get("results") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        return [item for item in rows if isinstance(item, dict)]

    async def get_wiki_graph(self, base_id: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/graph")
        if isinstance(payload, dict):
            inner = payload.get("data")
            if isinstance(inner, dict):
                return inner
            return payload
        raise WeknoraError("weknora wiki graph returned an unexpected payload")

    async def get_wiki_stats(self, base_id: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/stats")
        if isinstance(payload, dict):
            inner = payload.get("data")
            if isinstance(inner, dict):
                return inner
            return payload
        raise WeknoraError("weknora wiki stats returned an unexpected payload")
