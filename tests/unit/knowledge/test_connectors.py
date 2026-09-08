from __future__ import annotations

import httpx
import pytest

from harness.knowledge.connectors import (
    KnowledgeConnectorError,
    WebKnowledgeConnector,
)
from harness.knowledge.models import WebKnowledgeConfig


def web_config(url: str, *, max_bytes: int = 1_024) -> WebKnowledgeConfig:
    return WebKnowledgeConfig.model_validate(
        {
            "url": url,
            "title": "Reviewed page",
            "maxBytes": max_bytes,
        }
    )


@pytest.mark.asyncio
async def test_web_connector_stops_reading_when_stream_exceeds_limit() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * 2_048,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = WebKnowledgeConnector(client=client)
        with pytest.raises(KnowledgeConnectorError, match="byte limit"):
            await connector.sync(
                web_config("https://1.1.1.1/page"),
                {},
            )


@pytest.mark.asyncio
async def test_web_connector_revalidates_every_redirect_target() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://127.0.0.1/internal"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = WebKnowledgeConnector(client=client)
        with pytest.raises(KnowledgeConnectorError, match="private network"):
            await connector.sync(
                web_config("https://1.1.1.1/page"),
                {},
            )


@pytest.mark.asyncio
async def test_web_connector_extracts_text_and_preserves_final_citation_url() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"revision-2"',
            },
            content=(
                b"<html><head><title>Policy</title><style>hidden</style></head>"
                b"<body><h1>Leave</h1><p>Annual leave is 15 days.</p>"
                b"<script>ignore()</script></body></html>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = WebKnowledgeConnector(client=client)
        result = await connector.sync(
            web_config("https://1.1.1.1/policy", max_bytes=4_096),
            {},
        )

    assert result.documents[0].content == "Policy\nLeave\nAnnual leave is 15 days."
    assert result.documents[0].source_uri == "https://1.1.1.1/policy"
    assert result.checkpoint["etag"] == '"revision-2"'
