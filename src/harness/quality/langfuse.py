from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import SecretStr

from harness.quality.models import DatasetProjection, QualityScore


class QualityExporter(Protocol):
    async def export_score(self, score: QualityScore) -> None: ...
    async def export_dataset(self, dataset: DatasetProjection) -> None: ...


class DisabledQualityExporter:
    async def export_score(self, score: QualityScore) -> None:
        del score

    async def export_dataset(self, dataset: DatasetProjection) -> None:
        del dataset


class FakeQualityExporter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.scores: list[QualityScore] = []
        self.datasets: list[DatasetProjection] = []

    async def export_score(self, score: QualityScore) -> None:
        if self.fail:
            raise RuntimeError("quality exporter unavailable")
        self.scores.append(score)

    async def export_dataset(self, dataset: DatasetProjection) -> None:
        if self.fail:
            raise RuntimeError("quality exporter unavailable")
        self.datasets.append(dataset)


class LangfuseQualityExporter:
    def __init__(
        self,
        *,
        base_url: str,
        public_key: str,
        secret_key: SecretStr,
        timeout: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(public_key, secret_key.get_secret_value())
        self._timeout = timeout
        self._transport = transport

    async def export_score(self, score: QualityScore) -> None:
        if score.trace_id is None or score.value is None:
            return
        payload = {
            "id": score.score_id,
            # Run scores target their trace. Langfuse rejects requests that also
            # target a session; the trace already carries the session relation.
            "traceId": score.trace_id,
            "name": score.name,
            "value": score.value,
            "dataType": "NUMERIC",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, auth=self._auth, transport=self._transport
        ) as client:
            response = await client.post(f"{self._base_url}/api/public/scores", json=payload)
            response.raise_for_status()

    async def export_dataset(self, dataset: DatasetProjection) -> None:
        payload = {
            "name": f"{dataset.dataset_id}-v{dataset.dataset_version}",
            "description": dataset.name,
            "metadata": {
                "agentName": dataset.agent_name,
                "caseCount": dataset.case_count,
                "contentHash": dataset.content_hash,
            },
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, auth=self._auth, transport=self._transport
        ) as client:
            response = await client.post(f"{self._base_url}/api/public/v2/datasets", json=payload)
            response.raise_for_status()
