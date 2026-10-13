import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from harness.runtime.web_tools import (
    PageText,
    PublicNetworkBackend,
    PublicWebClient,
    WebAccessError,
    check_public_query,
    is_public_address,
    resolve_public,
    validate_public_url,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
        "2002:0808:0808::1",
    ],
)
def test_private_and_transition_addresses_denied(address):
    assert not is_public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://localhost/x",
        "https://a.internal/",
        "https://user:pass@example.com",
        "https://example.com:8000",
        "https://example.com\\@127.0.0.1",
        "https://example.com/?api_key=secret",
    ],
)
def test_unsafe_urls_denied(url):
    with pytest.raises(WebAccessError):
        validate_public_url(url)


def test_public_urls_encoded_and_fragment_removed():
    assert (
        validate_public_url("https://example.com/中文#section")
        == "https://example.com/%E4%B8%AD%E6%96%87"
    )
    assert is_public_address("8.8.8.8")
    assert is_public_address("2606:4700:4700::1111")
    assert check_public_query("Python asyncio official documentation")
    with pytest.raises(WebAccessError):
        check_public_query("contact alice@example.com")


@pytest.mark.asyncio
async def test_mixed_dns_results_fail_closed():
    answers = [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("127.0.0.1", 443))]
    with patch("asyncio.BaseEventLoop.getaddrinfo", AsyncMock(return_value=answers)):
        with pytest.raises(WebAccessError):
            await resolve_public("example.com", 443)


@pytest.mark.asyncio
async def test_connect_uses_checked_literal_ip_not_second_dns_lookup():
    backend = AsyncMock()
    with (
        patch("harness.runtime.web_tools.resolve_public", AsyncMock(return_value=["8.8.8.8"])),
        patch("httpcore.AnyIOBackend", return_value=backend),
    ):
        await PublicNetworkBackend().connect_tcp("example.com", 443, timeout=3)
    assert backend.connect_tcp.call_args.args == ("8.8.8.8", 443)


def test_html_extracts_text_without_active_content():
    parser = PageText()
    parser.feed(
        "<html><head><title>Official</title><script>steal()</script></head><body><p>Facts</p>"
        "<form>secret form</form><div hidden>hidden</div></body></html>"
    )
    assert parser.text() == "Official\nFacts"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,payload",
    [
        (
            "minimax",
            {
                "base_resp": {"status_code": 0},
                "organic": [
                    {
                        "link": "https://example.com",
                        "title": "Result",
                        "snippet": "External instructions remain data",
                    },
                    None,
                    {"link": 5},
                ],
            },
        ),
        (
            "tavily",
            {
                "results": [
                    {
                        "url": "https://example.com",
                        "title": "Result",
                        "content": "External instructions remain data",
                    }
                ]
            },
        ),
    ],
)
async def test_search_provider_mapping_and_untrusted_results(provider, payload):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client):
        result = await PublicWebClient(AsyncMock(return_value="test-key"), provider).search(
            "public query"
        )
    assert result["trust"] == "untrusted"
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://example.com/"
    assert seen[0].headers["Authorization"] == "Bearer test-key"
    assert "test-key" not in str(result)


@pytest.mark.asyncio
async def test_search_missing_credentials_is_explicit():
    with pytest.raises(WebAccessError, match="尚未配置"):
        await PublicWebClient().search("public query")


@pytest.mark.asyncio
async def test_fetch_blocks_private_destination_before_http():
    with pytest.raises(WebAccessError, match="非公网"):
        await PublicWebClient().fetch("http://127.0.0.1/")


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("from_environment", [False, True])
async def test_fetch_reads_through_configured_proxy(monkeypatch, scheme, from_environment):
    import httpcore

    proxy = "http://proxy.example:3128"
    monkeypatch.setenv("HARNESS_WEB_TOOLS_PROXY", proxy if from_environment else "")
    content = b"<html><title>Public source</title><p>Verified text</p></html>"
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
        + str(len(content)).encode()
        + b"\r\n\r\n"
        + content
    )
    responses = ([b"HTTP/1.1 200 Connection established\r\n\r\n"] if scheme == "https" else []) + [
        response
    ]
    backend = httpcore.AsyncMockBackend(responses)
    connect = AsyncMock(wraps=backend.connect_tcp)
    monkeypatch.setattr(backend, "connect_tcp", connect)
    real_pool = httpcore.AsyncConnectionPool

    def pool_with_mock_network(**kwargs):
        return real_pool(**kwargs, network_backend=backend)

    # Keep the real connection pool and proxy protocol; replace only network I/O.
    with patch("harness.runtime.web_tools.httpcore.AsyncConnectionPool", pool_with_mock_network):
        result = await PublicWebClient(proxy="" if from_environment else proxy).fetch(
            f"{scheme}://example.com/"
        )
    assert result["title"] == "Public source"
    assert "Verified text" in result["content"]
    assert result["trust"] == "untrusted"
    assert connect.call_args.kwargs["host"] == "proxy.example"
    assert connect.call_args.kwargs["port"] == 3128


@pytest.mark.asyncio
async def test_redirect_to_private_network_is_checked_again():
    import httpcore

    class Stream(httpcore.AsyncNetworkStream):
        async def read(self, max_bytes, timeout=None):
            return (
                b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )

        async def write(self, buffer, timeout=None):
            pass

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    connect = AsyncMock(side_effect=[Stream(), WebAccessError("blocked private redirect")])
    with patch.object(PublicNetworkBackend, "connect_tcp", connect):
        with pytest.raises(WebAccessError, match="private redirect"):
            await PublicWebClient().fetch("http://example.com/")
    assert connect.call_count == 2
    assert connect.call_args.kwargs["host"] == "169.254.169.254"


@pytest.mark.asyncio
async def test_search_does_not_expose_provider_error_details():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="provider secret diagnostic")
        )
    )
    with patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client):
        with pytest.raises(WebAccessError) as error:
            await PublicWebClient(AsyncMock(return_value="test-key")).search("public query")
    assert "401" in str(error.value)
    assert "secret diagnostic" not in str(error.value)


def test_platform_switch_denies_builtin_networking():
    from harness.runtime.tools import ToolResolutionError, ToolResolver

    with pytest.raises(ToolResolutionError, match="关闭"):
        ToolResolver(web_enabled=False).web_server({"WebSearch"}, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("oversized", [False, True])
async def test_fetch_decodes_compressed_pages_with_expanded_size_limit(encoding, oversized):
    import gzip
    import zlib

    content = (
        b"x" * 2_000_001
        if oversized
        else b"<html><title>Public source</title><p>Verified text</p></html>"
    )
    packed = gzip.compress(content) if encoding == "gzip" else zlib.compress(content)
    wire = (
        f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Encoding: {encoding}"
        f"\r\nContent-Length: {len(packed)}\r\n\r\n"
    ).encode() + packed

    class Stream:
        def __init__(self):
            self.data = wire

        async def read(self, max_bytes, timeout=None):
            data, self.data = self.data[:max_bytes], self.data[max_bytes:]
            return data

        async def write(self, buffer, timeout=None):
            pass

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    with patch.object(PublicNetworkBackend, "connect_tcp", AsyncMock(return_value=Stream())):
        if oversized:
            with pytest.raises(WebAccessError, match="2 MB"):
                await PublicWebClient().fetch("http://example.com/")
        else:
            result = await PublicWebClient().fetch("http://example.com/")
            assert result["title"] == "Public source"
            assert "Verified text" in result["content"]
            assert result["trust"] == "untrusted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [("search", {"query": "public query"}), ("fetch", {"url": "https://example.com/"})],
)
async def test_web_failure_survives_sdk_mcp_result_serialization(name, arguments):
    from mcp.types import CallToolRequest

    from harness.runtime.web_tools import create_web_mcp_server

    client = PublicWebClient()
    client.fetch = AsyncMock(side_effect=WebAccessError("网页读取超时或连接失败"))
    server = create_web_mcp_server({"WebSearch", "WebFetch"}, client)["instance"]
    request = CallToolRequest(method="tools/call", params={"name": name, "arguments": arguments})
    result = await server.request_handlers[CallToolRequest](request)
    assert result.root.isError is True
    assert result.root.content[0].text


@pytest.mark.asyncio
async def test_tavily_images_keep_their_page_source_and_reject_private_urls():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["include_images"] and body["include_image_descriptions"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/page",
                        "title": "Museum",
                        "images": [
                            {"url": "https://example.com/photo.jpg", "description": "Exterior"},
                            "http://127.0.0.1/private.jpg",
                            "https://example.com:8443/private.jpg",
                        ],
                    }
                ],
                "images": ["https://example.org/search-image.jpg"],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client):
        result = await PublicWebClient(AsyncMock(return_value="test-key"), "tavily").search(
            "museum photos", include_images=True
        )
    assert result["results"][0]["images"] == [
        {
            "url": "https://example.com/photo.jpg",
            "description": "Exterior",
            "source_url": "https://example.com/page",
        }
    ]
    assert "source_url" not in result["images"][0]  # cannot invent attribution
    assert "未下载" in result["images_notice"]


@pytest.mark.asyncio
async def test_minimax_reports_missing_image_support_without_losing_text():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"organic": [{"link": "https://example.com/", "title": "Public source"}]}
            )
        )
    )
    with patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client):
        result = await PublicWebClient(AsyncMock(return_value="test-key"), "minimax").search(
            "museum photos", include_images=True
        )
    assert len(result["results"]) == 1
    assert result["images_status"] == "unsupported"
    assert result["images"] == []


@pytest.mark.asyncio
async def test_tavily_news_keeps_images_and_dates_without_inventing_article_sources():
    import json

    seen = []
    payload = {
        "results": [
            {
                "url": "https://news.example.com/article",
                "title": "Person in news",
                "published_date": "2026-10-13",
                "content": "News",
                "images": [{"url": "https://cdn.example.com/article.jpg", "description": "Event"}],
            }
        ],
        "images": [
            "https://cdn.example.com/general.jpg",
            "http://10.0.0.1/private.jpg",
            {"url": "https://cdn.example.com/general.jpg"},
            {"url": "https://cdn.example.com/person.jpg", "description": "Person"},
        ],
    }

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client):
        result = await PublicWebClient(AsyncMock(return_value="test-key"), "tavily").search(
            "public person",
            topic="news",
            time_range="week",
            search_depth="advanced",
        )
    assert seen[0]["include_images"] and seen[0]["include_image_descriptions"]
    assert seen[0]["topic"] == "news" and seen[0]["time_range"] == "week"
    assert len(result["images"]) == 2
    assert "source_url" not in result["images"][0]
    source = result["results"][0]
    assert source["published_date"] == "2026-10-13"
    assert source["images"][0]["source_url"] == source["url"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["extract", "crawl", "map"])
async def test_tavily_retrieval_preserves_article_images_and_bounds_crawl(operation):
    import json

    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "results": ["https://example.com/child"]
                if operation == "map"
                else [
                    {
                        "url": "https://example.com/article",
                        "raw_content": "Article body",
                        "images": ["https://cdn.example.com/news.jpg"],
                    }
                ],
                "failed_results": [
                    {"url": "https://example.com/missing", "error": "private diagnostic"}
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with (
        patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client),
        patch("harness.runtime.web_tools.resolve_public", AsyncMock(return_value=["8.8.8.8"])),
    ):
        result = await PublicWebClient(AsyncMock(return_value="test-key"), "tavily").fetch(
            "https://example.com/article",
            operation=operation,
            query="public facts",
            limit=99,
        )
    path, body = seen[0]
    assert path == f"/{operation}"
    if operation != "extract":
        assert body["limit"] == 5 and body["max_depth"] == 1
        assert body["allow_external"] is False
    if operation == "map":
        assert result["links"] == ["https://example.com/child"]
    else:
        assert result["images"][0]["source_url"] == "https://example.com/article"
        assert result["results"][0]["content"] == "Article body"
        assert result["partial"] is True
    assert "private diagnostic" not in str(result)


@pytest.mark.asyncio
async def test_tavily_extraction_private_dns_blocked_before_provider_call():
    client = PublicWebClient(AsyncMock(return_value="test-key"), "tavily")
    with patch(
        "harness.runtime.web_tools.resolve_public", AsyncMock(side_effect=WebAccessError("非公网"))
    ):
        with pytest.raises(WebAccessError, match="非公网"):
            await client.fetch("https://example.com", operation="extract")
    client._api_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_tavily_partial_extraction_with_no_success_is_error():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"results": [], "failed_results": [{"error": "private diagnostic"}]},
            )
        )
    )
    with (
        patch("harness.runtime.web_tools.httpx.AsyncClient", return_value=client),
        patch("harness.runtime.web_tools.resolve_public", AsyncMock(return_value=["8.8.8.8"])),
    ):
        with pytest.raises(WebAccessError, match="未能提取"):
            await PublicWebClient(AsyncMock(return_value="key"), "tavily").fetch(
                "https://example.com",
                operation="extract",
            )


def test_observed_qcc_challenge_and_login_redirect_are_not_page_content():
    from harness.runtime.web_tools import reject_access_challenge

    with pytest.raises(WebAccessError, match="反爬验证"):
        reject_access_challenge(
            '{"l1":"var arg1=\'5de0458b12682f6eaa997dc0e6b37c445d01c0abc67661696f\';"}'
            'encoded-challenge',
            "https://www.qcc.com/firm/company.html",
            "https://www.qcc.com/firm/company.html",
        )
    with pytest.raises(WebAccessError, match="登录页"):
        reject_access_challenge(
            "登录以继续",
            "https://www.tianyancha.com/company/123",
            "https://www.tianyancha.com/login?from=/company/123",
        )
    reject_access_challenge(
        "本文解释 var arg1 和反爬验证的原理",
        "https://example.com/article",
        "https://example.com/article",
    )
    reject_access_challenge("登录说明", "https://example.com/login", "https://example.com/login")


@pytest.mark.asyncio
async def test_fetch_rejects_observed_hidden_textarea_qcc_challenge():
    content = (
        b'<textarea id="renderData" style="display:none">'
        b'{"l1":"var arg1=\'5de0458b12682f6eaa997dc0e6b37c445d01c0abc67661696f\';"}'
        b'</textarea><!doctype html><html><head>'
        b'<meta name="aliyun_waf_aa" content="challenge"><script>challenge()</script>'
        b'</head><body>encoded-challenge</body></html>'
    )
    wire = (
        f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
        f"Content-Length: {len(content)}\r\n\r\n"
    ).encode() + content

    class Stream:
        def __init__(self):
            self.data = wire

        async def read(self, max_bytes, timeout=None):
            data, self.data = self.data[:max_bytes], self.data[max_bytes:]
            return data

        async def write(self, buffer, timeout=None):
            pass

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    with patch.object(PublicNetworkBackend, "connect_tcp", AsyncMock(return_value=Stream())):
        with pytest.raises(WebAccessError, match="反爬验证"):
            await PublicWebClient().fetch("http://example.com/firm/company.html")
