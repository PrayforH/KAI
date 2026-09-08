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
