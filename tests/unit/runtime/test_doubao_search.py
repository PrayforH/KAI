import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from harness.runtime import doubao_search
from harness.runtime.web_tools import PublicWebClient, WebAccessError


def payload(request):
    body = json.loads(request.content)
    if request.url.path.endswith("/global_search"):
        return {
            "Result": {
                "ErrorCode": 0,
                "Documents": [
                    {
                        "Title": "Global",
                        "Url": "https://example.org/global",
                        "Snippet": [
                            {"Type": "text", "Text": "Global content"},
                            {
                                "Type": "image",
                                "Image": {
                                    "ImageUrl": "https://images.example.org/global.jpg",
                                    "Alt": "global image",
                                },
                            },
                        ],
                    }
                ],
            }
        }
    if body["SearchType"] == "image":
        return {
            "Result": {
                "ImageResults": [
                    {
                        "Title": "Picture",
                        "Url": "https://example.org/picture",
                        "Image": {
                            "Url": "https://images.example.org/picture.jpg",
                            "Features": {"Description": "a scene"},
                        },
                    }
                ]
            }
        }
    return {
        "Result": {
            "WebResults": [
                {
                    "Title": "Domestic",
                    "Url": "https://example.org/domestic",
                    "Content": "正文",
                    "InlineImages": [{"ImageUrl": "https://images.example.org/domestic.jpg"}],
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_four_channels_sources_images_and_no_key_leak():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload(request))

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with patch.object(doubao_search.httpx, "AsyncClient", return_value=client):
        result = await PublicWebClient(AsyncMock(return_value="shared-secret"), "doubao").search(
            "上海 外滩"
        )
    assert len(requests) == 4
    assert all(r.headers["Authorization"] == "Bearer shared-secret" for r in requests)
    assert result["channels"] == dict.fromkeys(
        ["cn_web", "global_web", "cn_image", "global_image"], "ok"
    )
    assert [r["index"] for r in result["results"]] == ["cn", "global"]
    assert len(result["images"]) == 3
    assert all(i["source_url"].startswith("https://example.org/") for i in result["images"])
    assert result["trust"] == "untrusted" and not result["partial"]
    assert "shared-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_images_disabled_makes_only_web_requests_and_omits_inline_images():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload(request))

    with patch.object(
        doubao_search.httpx,
        "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ):
        result = await doubao_search.search("test", key="k", include_images=False, max_results=1)
    assert len(requests) == 2 and all(r["SearchType"] == "web" for r in requests)
    assert result["images"] == [] and result["images_status"] == "not_requested"
    assert len(result["results"]) == 1 and result["results"][0]["images"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        {"ResponseMetadata": {"Error": {"Code": "bad", "Message": "secret-echo"}}},
        {"Result": {"ErrorCode": 9, "ErrorMsg": "secret-echo"}},
    ],
)
async def test_http_200_business_errors_are_errors_without_upstream_text(error):
    with patch.object(
        doubao_search.httpx,
        "AsyncClient",
        return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=error))
        ),
    ):
        with pytest.raises(WebAccessError) as caught:
            await doubao_search.search("test", key="k")
    assert "secret-echo" not in str(caught.value)


@pytest.mark.asyncio
async def test_one_failure_is_explicit_and_other_channels_survive():
    def respond(request):
        if request.url.path.endswith("/global_search"):
            return httpx.Response(503)
        return httpx.Response(200, json=payload(request))

    with patch.object(
        doubao_search.httpx,
        "AsyncClient",
        return_value=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ):
        result = await doubao_search.search("test", key="k", time_range="week")
    assert result["partial"] and result["results"] and result["images"]
    assert result["channels"]["global_web"] == "failed"
    assert any("未应用" in notice for notice in result["notices"])


def test_unsafe_images_and_sources_never_returned():
    result = {
        "ImageResults": [
            {"Url": "http://127.0.0.1/a", "Image": {"Url": "https://example.org/image.jpg"}},
            {"Url": "https://example.org/a", "Image": {"Url": "http://example.org/image.jpg"}},
            {"Url": "https://example.org/a", "Image": {"Url": "https://169.254.169.254/image.jpg"}},
        ]
    }
    assert doubao_search._parse(
        result, global_index=False, image_search=True, include_images=True
    ) == ([], [])


@pytest.mark.asyncio
async def test_invalid_and_sensitive_query_rejected_before_network():
    with patch.object(doubao_search.httpx, "AsyncClient") as client:
        for query in ["x" * 101, "password=secret"]:
            with pytest.raises(WebAccessError):
                await doubao_search.search(query, key="k")
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"not json", json.dumps({"Result": []}).encode(), b"x" * 2_000_001]
)
async def test_invalid_or_oversized_upstream_is_not_returned(body):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    with patch.object(doubao_search.httpx, "AsyncClient", return_value=client):
        with pytest.raises(WebAccessError):
            await doubao_search.search("test", key="k")
