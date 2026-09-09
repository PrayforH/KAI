"""HTTP client for the WeKnora REST API.

The client owns authentication (JWT login with re-login on expiry) and the
response envelope handling: WeKnora endpoints return either a bare JSON body
or a ``{"success": ..., "data": ...}`` envelope with structured errors.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import httpx

from harness.knowledge.weknora.configuration import WeknoraSettings


def _dict_list(value: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - remote JSON
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in cast(list[Any], value) if isinstance(item, dict)]


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
                password.get_secret_value()
                if hasattr(password, "get_secret_value")
                else str(password)
            )
            response = await self._client.post(
                "/auth/login",
                json={"email": self._settings.email, "password": secret},
            )
            payload = cast(dict[str, Any], self._unwrap(response))
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
            values = cast(dict[str, Any], payload)
            error = values.get("error")
            if values.get("success") is False or error:
                message = "weknora request rejected"
                if isinstance(error, dict):
                    error_values = cast(dict[str, Any], error)
                    message = str(error_values.get("message") or message)
                raise WeknoraError(message, status_code=response.status_code)
        return cast(Any, payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        reauthenticated: bool = False,
        raw: bool = False,
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
                raw=raw,
            )
        payload = self._unwrap(response)
        if not raw and isinstance(payload, dict):
            values = cast(dict[str, Any], payload)
            if "data" in values:
                return values["data"]
        return cast(Any, payload)

    async def get_data(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        """GET a path; ``raw=True`` keeps the envelope so callers can read
        pagination metadata (total/page) alongside the rows."""
        return await self._request("GET", path, params=params, raw=raw)

    async def post_data(self, path: str, *, json: Any = None) -> Any:
        return await self._request("POST", path, json=json)

    # --- knowledge bases -------------------------------------------------

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str,
        indexing_strategy: dict[str, bool],
        embedding_model: str = "",
        summary_model_id: str = "",
        wiki_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            # WeKnora binds the create request flat (not nested under "config");
            # a nested embedding_model_id is silently dropped, which leaves the
            # base without an embedding model and stalls document parsing.
            "indexing_strategy": indexing_strategy,
        }
        if embedding_model:
            body["embedding_model_id"] = embedding_model
        if summary_model_id:
            body["summary_model_id"] = summary_model_id
        if wiki_config:
            body["wiki_config"] = wiki_config
        payload = await self.post_data("/knowledge-bases", json=body)
        if not isinstance(payload, dict) or not cast(dict[str, Any], payload).get("id"):
            raise WeknoraError("weknora knowledge base creation returned no id")
        return cast(dict[str, Any], payload)

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
            return _dict_list(payload)
        return []

    # --- documents --------------------------------------------------------

    async def list_documents(
        self,
        base_id: str,
        *,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """WeKnora paginates documents (20 per page); fetch every page."""
        documents: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = await self.get_data(
                f"/knowledge-bases/{base_id}/knowledge",
                params={"page": page, "page_size": page_size},
                raw=True,
            )
            if isinstance(payload, list):
                documents.extend(_dict_list(payload))
                break
            values = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
            rows = _dict_list(values.get("data") or values.get("items") or [])
            documents.extend(rows)
            total = values.get("total")
            if not rows or (isinstance(total, int) and len(documents) >= total):
                break
        return documents

    async def get_document(self, document_id: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledge/{document_id}")
        if not isinstance(payload, dict):
            raise WeknoraError(f"weknora document not found: {document_id}")
        return cast(dict[str, Any], payload)

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
        if not isinstance(payload, dict) or not cast(dict[str, Any], payload).get("id"):
            raise WeknoraError("weknora document upload returned no id")
        return cast(dict[str, Any], payload)

    async def create_manual_document(
        self,
        base_id: str,
        *,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        payload = await self.post_data(
            f"/knowledge-bases/{base_id}/knowledge/manual",
            # WeKnora keeps manual documents as drafts unless published; a draft
            # is never parsed, so it would never yield chunks or citations.
            json={"title": title, "content": content, "status": "publish"},
        )
        if not isinstance(payload, dict) or not cast(dict[str, Any], payload).get("id"):
            raise WeknoraError("weknora manual document creation returned no id")
        return cast(dict[str, Any], payload)

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
            total: Any = None
            if isinstance(payload, dict):
                values = cast(dict[str, Any], payload)
                rows = _dict_list(values.get("data") or [])
                total = values.get("total")
            elif isinstance(payload, list):
                rows = _dict_list(payload)
            else:
                rows = []
            chunks.extend(rows)
            if not rows or (isinstance(total, int) and len(chunks) >= total):
                break
        return chunks

    async def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        payload = await self.get_data(f"/chunks/by-id/{chunk_id}")
        if isinstance(payload, dict):
            values = cast(dict[str, Any], payload)
            inner = values.get("data")
            if isinstance(inner, dict):
                return cast(dict[str, Any], inner)
            return values
        return None

    # --- wiki -------------------------------------------------------------

    async def list_wiki_pages(
        self,
        base_id: str,
        *,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """WeKnora paginates wiki pages (20 per page); fetch every page."""
        pages: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = await self.get_data(
                f"/knowledgebase/{base_id}/wiki/pages",
                params={"page": page, "page_size": page_size},
                raw=True,
            )
            if isinstance(payload, list):
                pages.extend(_dict_list(payload))
                break
            values = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
            rows = _dict_list(values.get("pages") or values.get("data") or [])
            pages.extend(rows)
            total = values.get("total")
            if not rows or (isinstance(total, int) and len(pages) >= total):
                break
        return pages

    async def get_wiki_page(self, base_id: str, slug: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/pages/{slug}")
        if isinstance(payload, dict):
            values = cast(dict[str, Any], payload)
            inner = values.get("data")
            if isinstance(inner, dict):
                return cast(dict[str, Any], inner)
            return values
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
            values = cast(dict[str, Any], payload)
            return _dict_list(
                values.get("pages") or values.get("data") or values.get("results") or []
            )
        return _dict_list(payload)

    async def get_wiki_graph(self, base_id: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/graph")
        if isinstance(payload, dict):
            values = cast(dict[str, Any], payload)
            inner = values.get("data")
            if isinstance(inner, dict):
                return cast(dict[str, Any], inner)
            return values
        raise WeknoraError("weknora wiki graph returned an unexpected payload")

    async def get_wiki_stats(self, base_id: str) -> dict[str, Any]:
        payload = await self.get_data(f"/knowledgebase/{base_id}/wiki/stats")
        if isinstance(payload, dict):
            values = cast(dict[str, Any], payload)
            inner = values.get("data")
            if isinstance(inner, dict):
                return cast(dict[str, Any], inner)
            return values
        raise WeknoraError("weknora wiki stats returned an unexpected payload")
