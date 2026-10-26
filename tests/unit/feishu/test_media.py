from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from harness.core.errors import NotFoundError
from harness.dictation.service import DictationService, DictationSettings
from harness.feishu.media import (
    DownloadedResource,
    FeishuMedia,
    FeishuResourceClient,
    MediaRejectedError,
)
from harness.feishu.models import Actor, FeishuSettings, Incoming, MessageResource
from tests.unit.test_input_artifacts import build_service


def event(kind="file", **kwargs):
    return Incoming(
        actor=Actor(app_id="app", tenant_key="tenant", open_id="user"),
        key="message:om_test",
        kind="message",
        message_id="om_test",
        resources=[MessageResource(kind=kind, key="file_test", **kwargs)],
    )


def client(handler, maximum=1024):
    async def wrapped(request):
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "test-token", "expire": 7200}
            )
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.host == "open.feishu.cn"
        return handler(request)

    return FeishuResourceClient(
        FeishuSettings(max_resource_bytes=maximum), transport=httpx.MockTransport(wrapped)
    )


@pytest.mark.asyncio
async def test_downloader_uses_message_resource_identity_and_preserves_json_files():
    def response(request):
        assert request.url.path.endswith("/messages/om_test/resources/file_test")
        assert request.url.params["type"] == "file"
        return httpx.Response(
            200,
            content=b'{"hello": "world"}',
            headers={
                "Content-Type": "application/json",
                "Content-Disposition": 'attachment; filename="data.json"',
            },
        )

    result = await client(response).download("om_test", event().resources[0])
    assert result.name == "data.json" and result.media_type == "application/json"
    assert result.content == b'{"hello": "world"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (403, MediaRejectedError),
        (302, ConnectionError),
        (429, ConnectionError),
        (503, ConnectionError),
    ],
)
async def test_denied_redirects_and_failures_never_expose_external_response(status, expected):
    downloader = client(
        lambda _: httpx.Response(
            status, text="private external body", headers={"Location": "https://evil.test/steal"}
        )
    )
    with pytest.raises(expected) as error:
        await downloader.download("om_test", event().resources[0])
    assert "private" not in str(error.value) and "evil" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1025"}])
async def test_download_size_limit_applies_with_and_without_content_length(headers):
    downloader = client(lambda _: httpx.Response(200, content=b"x" * 1025, headers=headers))
    with pytest.raises(MediaRejectedError, match="上限"):
        await downloader.download("om_test", event().resources[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "file"])
async def test_image_storage_uses_verified_mime_and_retries_without_duplicate_uploads(kind):
    image = BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="PNG")
    downloader = SimpleNamespace(
        download=AsyncMock(
            return_value=DownloadedResource(
                image.getvalue(), "../picture.png", "application/octet-stream"
            )
        )
    )
    inputs = build_service(max_file_bytes=4096)
    resolver = AsyncMock(return_value="configured-vision")
    media = FeishuMedia(downloader, inputs, DictationService(DictationSettings()),
                        vision_route=resolver)
    incoming = event(kind)
    first = await media.prepare("tenant", "user", incoming)
    second = await media.prepare("tenant", "user", incoming)
    assert first.artifacts[0].media_type == "image/png"
    assert first.artifacts[0].name == ("飞书图片.png" if kind == "image" else "picture.png")
    assert first.model_route == "configured-vision"
    resolver.assert_awaited_with("tenant")
    assert first.artifacts[0].input_artifact_id == second.artifacts[0].input_artifact_id
    with pytest.raises(NotFoundError):
        await inputs.download(
            tenant_id="tenant",
            user_id="other",
            input_artifact_id=first.artifacts[0].input_artifact_id,
        )


@pytest.mark.asyncio
async def test_voice_transcription_is_submitted_with_original_audio_and_owned_storage(monkeypatch):
    from harness.feishu import media as module

    monkeypatch.setattr(module, "decode_voice", AsyncMock(return_value=b"valid-wav-test"))
    dictation = DictationService(DictationSettings(enabled=True))
    monkeypatch.setattr(dictation, "transcribe", AsyncMock(return_value="记住语音代号。"))
    downloader = SimpleNamespace(
        download=AsyncMock(
            return_value=DownloadedResource(b"OggS-test", "", "application/octet-stream")
        )
    )
    media = FeishuMedia(downloader, build_service(max_file_bytes=4096), dictation)
    result = await media.prepare("tenant", "user", event("audio", duration_ms=2000))
    assert "记住语音代号。" in result.text
    assert result.artifacts[0].media_type == "audio/ogg"
    _, original = await media.inputs.download(
        tenant_id="tenant", user_id="user", input_artifact_id=result.artifacts[0].input_artifact_id
    )
    assert original == b"OggS-test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings,duration",
    [
        (DictationSettings(), 1000),
        (DictationSettings(enabled=True, max_session_seconds=30), 31_000),
    ],
)
async def test_disabled_or_long_voice_is_rejected_before_resource_download(settings, duration):
    downloader = SimpleNamespace(download=AsyncMock())
    media = FeishuMedia(downloader, build_service(), DictationService(settings))
    with pytest.raises(MediaRejectedError):
        await media.prepare("tenant", "user", event("audio", duration_ms=duration))
    downloader.download.assert_not_awaited()
