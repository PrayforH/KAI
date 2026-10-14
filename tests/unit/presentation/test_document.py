"""Final bundle syntax is checked without running any authored JavaScript."""

import asyncio
import shutil

import pytest

from harness.presentation.document import validate_html_document


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js parser unavailable")
async def test_checks_scripts_and_modules_without_executing_them() -> None:
    await validate_html_document(
        b"<script>while(true){}; process.exit(99)</script>"
        b"<script type='module'>export const x = 1;</script>"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js parser unavailable")
async def test_catches_the_live_image_template_quoting_failure() -> None:
    with pytest.raises(ValueError, match="JavaScript.*failed|JavaScript.*语法检查失败"):
        await validate_html_document(b"<script>var x = '<em style='color:red'>'; </script>")


@pytest.mark.asyncio
async def test_keeps_json_data_and_static_html_independent_of_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("harness.presentation.document.shutil.which", lambda _: None)
    await validate_html_document(
        b'<script type="application/ld+json">{"description":"not JS"}</script><p>static</p>'
    )
    with pytest.raises(ValueError, match="Node.js"):
        await validate_html_document(b"<script>const x = 1</script>")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content, message",
    [
        (b'<script src="https://example.com/x.js"></script>', "自包含"),
        (b"<script>let x=1", "未闭合"),
        (b"\xff", "UTF-8"),
    ],
)
async def test_rejects_unusable_inline_documents(content: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        await validate_html_document(content)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_stops_the_parser_process_on_timeout_or_cancellation(
    monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    from unittest.mock import AsyncMock, Mock

    process = Mock(returncode=None)
    process.communicate = AsyncMock(side_effect=failure())
    process.wait = AsyncMock()
    monkeypatch.setattr("harness.presentation.document.shutil.which", lambda _: "/node")
    monkeypatch.setattr(
        "harness.presentation.document.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    expected = asyncio.CancelledError if failure is asyncio.CancelledError else ValueError
    with pytest.raises(expected):
        await validate_html_document(b"<script>const x = 1</script>")
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
