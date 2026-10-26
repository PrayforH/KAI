"""Bounded CDN decryption and shared assistant input preparation."""

import asyncio
import base64
import json
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from harness.application.input_artifacts import InputArtifactService
from harness.dictation.service import DictationService
from harness.feishu.media import DownloadedResource, FeishuMedia, MediaRejectedError, safe_name
from harness.feishu.models import Actor, Incoming, MessageResource, PreparedMessage


class WeixinMediaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_WEIXIN_", extra="ignore")
    max_resource_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_message_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    vision_model_route: str = "deepseek-v4-flash"


def cdn_url(media: dict[str, Any]) -> str:
    value = media.get("full_url")
    if not value:
        param = media.get("encrypt_query_param")
        if not isinstance(param, str) or not param or len(param) > 32000:
            raise MediaRejectedError("微信附件缺少下载信息，请重新发送。")
        value = "https://novac2c.cdn.weixin.qq.com/c2c/download?" + urlencode(
            {"encrypted_query_param": param}
        )
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".cdn.weixin.qq.com")
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.fragment
            or len(value) > 40000
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise MediaRejectedError("微信附件地址无效，请重新发送。") from None
    return value


def decrypt(content: bytes, item: dict[str, Any], kind: str) -> bytes:
    media = item.get("media", {})
    try:
        if kind == "image" and item.get("aeskey"):
            key = bytes.fromhex(item["aeskey"])
        elif media.get("aes_key"):
            key = base64.b64decode(media["aes_key"], validate=True)
            if len(key) == 32:
                key = bytes.fromhex(key.decode("ascii"))
        elif kind == "image":
            return content
        else:
            raise ValueError
        if len(key) != 16 or media.get("encrypt_type", 1) != 1:
            raise ValueError
        decoder = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        plain = decoder.update(content) + decoder.finalize()
        unpad = padding.PKCS7(128).unpadder()
        return unpad.update(plain) + unpad.finalize()
    except (ValueError, TypeError, UnicodeError):
        raise MediaRejectedError("微信附件解密失败，请重新发送。") from None


async def decode_silk(content: bytes, maximum_seconds: int) -> bytes:
    if not content.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
        return content
    process = await asyncio.create_subprocess_exec(
        "node",
        "--max-old-space-size=128",
        str(Path(__file__).with_name("silk_decode.mjs")),
        str(maximum_seconds),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(20):
            wav, _ = await process.communicate(content)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if (
        process.returncode
        or not wav.startswith(b"RIFF")
        or len(wav) > maximum_seconds * 32000 + 4096
    ):
        raise MediaRejectedError("无法解码微信语音，或语音超过时长上限，请重新录制或分段发送。")
    return wav


class WeixinResourceClient:
    def __init__(
        self,
        items: dict[str, dict[str, Any]],
        settings: WeixinMediaSettings,
        maximum_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.items, self.settings, self.maximum_seconds, self.transport = (
            items,
            settings,
            maximum_seconds,
            transport,
        )
        self.total = 0

    async def download(self, message_id: str, resource: MessageResource) -> DownloadedResource:
        item = self.items[resource.key]
        media = item.get("media")
        if not isinstance(media, dict):
            raise MediaRejectedError("微信附件缺少下载信息，请重新发送。")
        url = cdn_url(cast(dict[str, Any], media))
        try:
            async with (
                asyncio.timeout(45),
                httpx.AsyncClient(
                    transport=self.transport, trust_env=False, follow_redirects=False, timeout=15
                ) as client,
            ):
                async with client.stream("GET", url) as response:
                    if response.status_code in (400, 403, 404, 410):
                        raise MediaRejectedError("微信附件已失效或无法读取，请重新发送。")
                    response.raise_for_status()
                    maximum = self.settings.max_resource_bytes + 16
                    length = response.headers.get("Content-Length")
                    if length and int(length) > maximum:
                        raise MediaRejectedError("微信附件超过大小上限，请缩小或拆分后发送。")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(64 * 1024):
                        if (
                            len(content) + len(chunk) > maximum
                            or self.total + len(content) + len(chunk)
                            > self.settings.max_message_bytes + 160
                        ):
                            raise MediaRejectedError("微信附件超过大小上限，请缩小或拆分后发送。")
                        content.extend(chunk)
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise ConnectionError("Weixin media download unavailable") from None
        self.total += len(content)
        plain = decrypt(bytes(content), item, resource.kind)
        if not plain or len(plain) > self.settings.max_resource_bytes:
            raise MediaRejectedError("微信附件为空或超过大小上限，请重新发送。")
        name = safe_name(resource.name, "微信文件")
        document_types = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        media_type = (
            document_types.get(Path(name).suffix.lower())
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        if resource.kind == "audio":
            plain = await decode_silk(plain, self.maximum_seconds)
            name = "微信语音.wav" if plain.startswith(b"RIFF") else "微信语音.opus"
            media_type = "audio/wav" if plain.startswith(b"RIFF") else "audio/opus"
        transcript = item.get("text", "") if resource.kind == "audio" else ""
        return DownloadedResource(
            plain, name, media_type, transcript if isinstance(transcript, str) else ""
        )


class WeixinMedia:
    def __init__(
        self,
        inputs: InputArtifactService,
        dictation: DictationService,
        *,
        vision_route: Callable[[str], Awaitable[str]],
        settings: WeixinMediaSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.inputs, self.dictation, self.vision_route = inputs, dictation, vision_route
        self.settings, self.transport = settings or WeixinMediaSettings(), transport

    async def prepare(
        self, tenant: str, user: str, key: str, text: str, payload: str
    ) -> PreparedMessage:
        items = json.loads(payload)
        resources: list[MessageResource] = []
        downloads: dict[str, dict[str, Any]] = {}
        try:
            for index, part in enumerate(items):
                kind = {2: "image", 3: "audio", 4: "file"}.get(part.get("type"))
                field = {2: "image_item", 3: "voice_item", 4: "file_item"}.get(part.get("type"))
                if not kind or not field or not isinstance(part.get(field), dict):
                    raise MediaRejectedError("这类微信附件暂不支持，请发送图片、文件或语音。")
                item = part[field]
                resource = MessageResource(
                    kind=cast(Literal["image", "audio", "file"], kind),
                    key=str(index),
                    name=safe_name(str(item.get("file_name", "")), "") if kind == "file" else "",
                    duration_ms=int(item.get("playtime", 0) or 0) if kind == "audio" else 0,
                )
                resources.append(resource)
                downloads[resource.key] = item
            incoming = Incoming(
                actor=Actor(app_id="weixin", tenant_key=tenant, open_id=user),
                key=key,
                kind="message",
                text=text,
                message_id=key,
                resources=resources,
            )
            media = FeishuMedia(
                WeixinResourceClient(
                    downloads,
                    self.settings,
                    self.dictation.settings.max_session_seconds,
                    self.transport,
                ),
                self.inputs,
                self.dictation,
                vision_route=self.vision_route,
                channel_label="微信",
                channel_key="weixin",
            )
            return await media.prepare(tenant, user, incoming)
        except (ValidationError, ValueError, TypeError):
            raise MediaRejectedError("微信消息内容过长或附件信息无效，请拆分后重新发送。") from None
