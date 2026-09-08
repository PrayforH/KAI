"""OpenAI-compatible embeddings; credentials and input never enter error messages."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import SecretStr


class MemoryEmbedder(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class OpenAIEmbeddingClient:
    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        model: str,
        *,
        dimensions: int = 1024,
        timeout: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self._dimensions = dimensions
        self._timeout = timeout
        self._transport = transport

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts or len(texts) > 32 or any(not t or len(t) > 6000 for t in texts):
            raise ValueError("invalid embedding batch")
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            trust_env=False,
        ) as client:
            response = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                json={"model": self.model, "input": list(texts), "encoding_format": "float"},
            )
        if response.status_code != 200:
            raise ValueError(f"embedding provider returned HTTP {response.status_code}")
        data = response.json().get("data", [])
        if len(data) != len(texts) or {row.get("index") for row in data} != set(range(len(texts))):
            raise ValueError("embedding response indexes are invalid")
        vectors = tuple(
            tuple(float(v) for v in row["embedding"])
            for row in sorted(data, key=lambda row: row["index"])
        )
        if any(
            len(v) != self._dimensions
            or not all(math.isfinite(x) for x in v)
            or sum(x * x for x in v) <= 0
            for v in vectors
        ):
            raise ValueError("embedding dimensions or values are invalid")
        return vectors
