import base64
import json
import os
from io import BytesIO
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from harness.dictation.service import DictationService, DictationSettings
from harness.feishu.media import MediaRejectedError
from harness.feishu.models import MessageResource
from harness.weixin.media import (
    WeixinMedia,
    WeixinMediaSettings,
    WeixinResourceClient,
    cdn_url,
    decrypt,
)
from tests.unit.test_input_artifacts import build_service

KEY = b"1234567890abcdef"


def encrypt(value):
    pad = padding.PKCS7(128).padder()
    data = pad.update(value) + pad.finalize()
    cipher = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
    return cipher.update(data) + cipher.finalize()


def media(key=None):
    return {
        "encrypt_query_param": "private-download-parameter",
        "aes_key": key or base64.b64encode(KEY).decode(),
        "encrypt_type": 1,
    }


@pytest.mark.parametrize(
    "key", [base64.b64encode(KEY).decode(), base64.b64encode(KEY.hex().encode()).decode()]
)
def test_real_aes_decryption_accepts_both_documented_key_encodings(key):
    assert decrypt(encrypt(b"file text"), {"media": media(key)}, "file") == b"file text"
    assert (
        decrypt(encrypt(b"image"), {"aeskey": KEY.hex(), "media": {"aes_key": "wrong"}}, "image")
        == b"image"
    )
    assert decrypt(b"raw-image", {"media": {}}, "image") == b"raw-image"


@pytest.mark.parametrize(
    "url",
    [
        "http://novac2c.cdn.weixin.qq.com/c2c/download",
        "https://127.0.0.1/secret",
        "https://novac2c.cdn.weixin.qq.com.evil.test/file",
        "https://x@novac2c.cdn.weixin.qq.com/file",
        "https://novac2c.cdn.weixin.qq.com:8443/file",
    ],
)
def test_untrusted_cdn_urls_are_rejected_without_reflecting_them(url):
    with pytest.raises(MediaRejectedError) as e:
        cdn_url({"full_url": url})
    assert url not in str(e.value)


@pytest.mark.asyncio
async def test_downloads_only_cdn_bytes_and_never_forwards_bot_credentials():
    def handler(request):
        assert request.url.host == "novac2c.cdn.weixin.qq.com"
        assert request.url.params["encrypted_query_param"] == "private-download-parameter"
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=encrypt(b"content"))

    client = WeixinResourceClient(
        {"0": {"media": media()}}, WeixinMediaSettings(), 120, httpx.MockTransport(handler)
    )
    result = await client.download(
        "msg", MessageResource(kind="file", key="0", name="../facts.txt")
    )
    assert (
        result.content == b"content"
        and result.name == "facts.txt"
        and result.media_type == "text/plain"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * 1041),
        httpx.Response(200, headers={"Content-Length": "1041"}),
    ],
)
async def test_stream_limit_is_enforced_with_or_without_content_length(response):
    client = WeixinResourceClient(
        {"0": {"media": media()}},
        WeixinMediaSettings(max_resource_bytes=1024),
        120,
        httpx.MockTransport(lambda _: response),
    )
    with pytest.raises(MediaRejectedError, match="大小上限"):
        await client.download("msg", MessageResource(kind="file", key="0"))


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_private_download_errors_are_not_exposed():
    client = WeixinResourceClient(
        {"0": {"media": media()}},
        WeixinMediaSettings(),
        120,
        httpx.MockTransport(
            lambda _: httpx.Response(
                302, text="secret body", headers={"Location": "http://127.0.0.1"}
            )
        ),
    )
    with pytest.raises(ConnectionError) as e:
        await client.download("msg", MessageResource(kind="file", key="0"))
    assert "secret" not in str(e.value) and "127.0.0.1" not in str(e.value)


@pytest.mark.asyncio
async def test_combined_caption_images_file_and_voice_use_owned_shared_inputs(monkeypatch):
    from harness.feishu import media as shared

    image = BytesIO()
    Image.new("RGB", (8, 8), "green").save(image, format="PNG")
    bodies = [image.getvalue(), b"project WXFILE-79 budget 68320", b"RIFF-voice"]
    calls = 0

    def handler(_):
        nonlocal calls
        value = bodies[calls % len(bodies)]
        calls += 1
        return httpx.Response(200, content=encrypt(value))

    dictation = DictationService(DictationSettings(enabled=True))
    monkeypatch.setattr(shared, "decode_voice", AsyncMock(return_value=b"valid-wave"))
    monkeypatch.setattr(dictation, "transcribe", AsyncMock(return_value="记住微信语音代号海豚六八"))
    inputs = build_service(max_file_bytes=4096)
    prepared = WeixinMedia(
        inputs,
        dictation,
        vision_route=AsyncMock(return_value="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    payload = json.dumps(
        [
            {"type": 2, "image_item": {"media": media()}},
            {"type": 4, "file_item": {"media": media(), "file_name": "facts.txt"}},
            {"type": 3, "voice_item": {"media": media(), "playtime": 2000}},
        ]
    )
    result = await prepared.prepare("tenant", "alice", "msg", "请结合图片、文件和语音回答", payload)
    assert len(result.artifacts) == 3 and result.model_route == "deepseek-v4-flash"
    assert (
        result.text.startswith("请结合图片、文件和语音回答")
        and "微信语音转写：\n记住微信语音代号海豚六八" in result.text
    )
    assert [a.name for a in result.artifacts] == ["微信图片-1.png", "facts.txt", "微信语音.wav"]
    again = await prepared.prepare("tenant", "alice", "msg", "请结合图片、文件和语音回答", payload)
    assert [a.input_artifact_id for a in again.artifacts] == [
        a.input_artifact_id for a in result.artifacts
    ]
    from harness.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await inputs.download(
            tenant_id="tenant",
            user_id="bob",
            input_artifact_id=result.artifacts[0].input_artifact_id,
        )


@pytest.mark.asyncio
async def test_silk_decoder_real_round_trip_when_installed():
    if not os.getenv("KAI_WEIXIN_SILK_MODULE"):
        pytest.skip("Pinned codec is exercised by release smoke verification")
    import asyncio

    from harness.weixin.media import decode_silk

    code = (
        "const {encode}=require(process.env.KAI_WEIXIN_SILK_MODULE);"
        "encode(Buffer.alloc(6400),16000).then(x=>process.stdout.write(Buffer.from(x.data)));"
    )
    process = await asyncio.create_subprocess_exec(
        "node", "-e", code, stdout=asyncio.subprocess.PIPE
    )
    silk, _ = await process.communicate()
    wav = await decode_silk(silk, 120)
    assert wav.startswith(b"RIFF") and len(wav) > 44
    with pytest.raises(MediaRejectedError):
        await decode_silk(b"\x02#!SILK_V3bad", 120)


@pytest.mark.asyncio
async def test_platform_voice_transcript_is_preserved_without_second_asr(monkeypatch):
    import wave

    from harness.feishu import media as shared

    wav = BytesIO()
    with wave.open(wav, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0" * 3200)
    value = wav.getvalue()
    dictation = DictationService(DictationSettings(enabled=True))
    monkeypatch.setattr(
        dictation, "transcribe", AsyncMock(return_value="wrong secondary transcript")
    )
    monkeypatch.setattr(shared, "decode_voice", AsyncMock(return_value=value))
    prepared = WeixinMedia(
        build_service(max_file_bytes=8192),
        dictation,
        vision_route=AsyncMock(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=encrypt(value))),
    )
    result = await prepared.prepare(
        "tenant",
        "alice",
        "voice",
        "",
        json.dumps([{"type": 3, "voice_item": {"media": media(), "text": "请记住海豚68"}}]),
    )
    assert result.text == "微信语音转写：\n请记住海豚68"
    assert not dictation.transcribe.called
    assert result.artifacts[0].media_type == "audio/wav"
