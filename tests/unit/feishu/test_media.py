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
    file_type_for,
)
from harness.feishu.models import (
    Actor,
    FeishuSettings,
    Incoming,
    MessageResource,
    file_message,
)
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
async def test_combined_caption_images_file_and_voice_reach_one_prepared_turn(monkeypatch):
    from harness.feishu import media as module
    from harness.feishu.models import MessageResource

    image = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
    downloader = SimpleNamespace(download=AsyncMock(side_effect=[
        DownloadedResource(image.getvalue(), "", "image/png"),
        DownloadedResource(image.getvalue(), "", "image/png"),
        DownloadedResource(b"file contents", "facts.txt", "text/plain"),
        DownloadedResource(b"OggS-test", "voice.opus", "audio/ogg"),
    ]))
    monkeypatch.setattr(module, "decode_voice", AsyncMock(return_value=b"wav"))
    dictation = DictationService(DictationSettings(enabled=True))
    monkeypatch.setattr(dictation, "transcribe", AsyncMock(return_value="语音补充内容"))
    media = FeishuMedia(downloader, build_service(max_file_bytes=4096), dictation,
                        vision_route=AsyncMock(return_value="deepseek-v4-flash"))
    incoming = event("image").model_copy(update={
        "text": "[图片 1][图片 2]\n比较两张图片，结合附件和语音回答。",
        "resources": [MessageResource(kind="image", key="img_one"),
                      MessageResource(kind="image", key="img_two"),
                      MessageResource(kind="file", key="file_one", name="facts.txt"),
                      MessageResource(kind="audio", key="file_voice")],
    })
    result = await media.prepare("tenant", "user", incoming)
    assert result.text == incoming.text + "\n\n飞书语音转写：\n语音补充内容"
    assert result.model_route == "deepseek-v4-flash"
    assert [a.name for a in result.artifacts] == [
        "飞书图片-1.png", "飞书图片-2.png", "facts.txt", "voice.opus",
    ]
    assert len({a.input_artifact_id for a in result.artifacts}) == 4


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


def upload_client(handler, maximum=1024):
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


@pytest.mark.parametrize(
    ("media_type", "name", "expected"),
    [
        ("application/pdf", "report.pdf", "pdf"),
        ("application/octet-stream", "表格.xlsx", "xls"),
        ("application/octet-stream", "notes.docx", "doc"),
        ("application/octet-stream", "no-extension", "stream"),
        ("text/html", "page.html", "stream"),
    ],
)
def test_file_type_follows_the_declared_type_then_the_extension(
    media_type: str, name: str, expected: str
):
    assert file_type_for(media_type, name) == expected


@pytest.mark.asyncio
async def test_file_upload_returns_the_platform_key_and_sends_its_fields():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        assert request.url.path == "/open-apis/im/v1/files"
        body = request.read()
        seen["has_type"] = b'name="file_type"' in body
        seen["has_name"] = b'name="file_name"' in body
        seen["has_payload"] = b"payload-bytes" in body
        return httpx.Response(200, json={"code": 0, "data": {"file_key": "file_v2_abc"}})

    key = await upload_client(handler).upload_file("报告.pdf", "application/pdf", b"payload-bytes")
    assert key == "file_v2_abc"
    assert seen == {
        "path": "/open-apis/im/v1/files",
        "has_type": True,
        "has_name": True,
        "has_payload": True,
    }


@pytest.mark.asyncio
async def test_file_upload_classifies_definitive_rejections_and_outages():
    # A refusal is not retried; an outage is.
    for status, expected in ((403, MediaRejectedError), (500, ConnectionError)):
        client_ = upload_client(lambda request, status=status: httpx.Response(status, json={}))
        with pytest.raises(expected):
            await client_.upload_file("a.pdf", "application/pdf", b"x")


@pytest.mark.asyncio
async def test_file_upload_refuses_empty_and_oversized_payloads_before_the_network():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"code": 0, "data": {"file_key": "k"}})

    client_ = upload_client(handler, maximum=1024)
    with pytest.raises(MediaRejectedError):
        await client_.upload_file("empty.pdf", "application/pdf", b"")
    with pytest.raises(MediaRejectedError):
        await client_.upload_file("big.pdf", "application/pdf", b"x" * 1025)
    assert calls == []


@pytest.mark.asyncio
async def test_file_upload_rejects_a_response_without_a_key():
    client_ = upload_client(
        lambda request: httpx.Response(200, json={"code": 0, "data": {}})
    )
    with pytest.raises(MediaRejectedError):
        await client_.upload_file("a.pdf", "application/pdf", b"x")


def test_file_message_binds_an_uploaded_key():
    message = file_message("file_v2_abc")
    assert message.msg_type == "file"
    assert message.content == {"file_key": "file_v2_abc"}
    with pytest.raises(ValueError):
        file_message("  ")
