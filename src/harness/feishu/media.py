"""Bounded private-message resources, shared input storage and voice transcription."""

import asyncio
import mimetypes
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx
from PIL import Image, UnidentifiedImageError

from harness.application.input_artifacts import InputArtifactService
from harness.core.models import InputArtifact
from harness.dictation.service import DictationError, DictationService, validate_wav
from harness.feishu.models import FeishuSettings, Incoming, MessageResource, PreparedMessage


class MediaRejectedError(Exception):
    """Only fixed, user-facing messages; never external response bodies."""


@dataclass(frozen=True)
class DownloadedResource:
    content: bytes
    name: str
    media_type: str
    transcript: str = ""


class ResourceDownloader(Protocol):
    async def download(self, message_id: str, resource: MessageResource) -> DownloadedResource: ...


def safe_name(name: str, fallback: str) -> str:
    name = Path(name.replace("\\", "/")).name
    return re.sub(r"[\x00-\x1f\x7f]", "_", name).strip(" .")[:120] or fallback


class FeishuResourceClient:
    """Stream the documented resource API; SDK get() buffers unbounded responses."""

    def __init__(
        self, settings: FeishuSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings, self.transport = settings, transport
        self._token, self._expires = "", 0.0

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token and self._expires > time.monotonic():
            return self._token
        response = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.settings.app_id,
                "app_secret": self.settings.app_secret.get_secret_value(),
            },
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("tenant_access_token")
        if payload.get("code") != 0 or not isinstance(token, str) or not token:
            raise MediaRejectedError("飞书附件认证失败，请管理员检查应用配置。")
        self._token = token
        self._expires = time.monotonic() + max(0, min(int(payload.get("expire", 0)), 7200) - 60)
        return token

    async def download(self, message_id: str, resource: MessageResource) -> DownloadedResource:
        try:
            async with (
                asyncio.timeout(45),
                httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    timeout=15,
                    follow_redirects=False,
                ) as client,
            ):
                token = await self._access_token(client)
                url = (
                    "https://open.feishu.cn/open-apis/im/v1/messages/"
                    + quote(message_id, safe="")
                    + "/resources/"
                    + quote(resource.key, safe="")
                )
                async with client.stream(
                    "GET",
                    url,
                    params={"type": "image" if resource.kind == "image" else "file"},
                    headers={"Authorization": "Bearer " + token},
                ) as response:
                    if response.status_code == 401:
                        self._token = ""
                        raise ConnectionError("Feishu resource authentication expired")
                    if response.status_code in {400, 403, 404}:
                        raise MediaRejectedError(
                            "飞书附件无法读取：请检查应用的资源读取权限，或重新发送已失效的附件。"
                        )
                    response.raise_for_status()
                    maximum = self.settings.max_resource_bytes
                    length = response.headers.get("Content-Length")
                    if length and int(length) > maximum:
                        raise MediaRejectedError(
                            f"飞书附件超过 {maximum // (1024 * 1024)} MB 上限。"
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(content) + len(chunk) > maximum:
                            raise MediaRejectedError(
                                f"飞书附件超过 {maximum // (1024 * 1024)} MB 上限。"
                            )
                        content.extend(chunk)
                    if not content:
                        raise MediaRejectedError("飞书附件为空，请重新发送。")
                    disposition = Message()
                    disposition["content-disposition"] = response.headers.get(
                        "Content-Disposition", ""
                    )
                    name = resource.name or disposition.get_filename() or ""
                    media_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                    return DownloadedResource(bytes(content), name, media_type)
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            raise ConnectionError("Feishu resource download unavailable") from error


async def decode_voice(content: bytes, maximum_seconds: int) -> bytes:
    """Decode local bytes only; playlists cannot open remote/network resources."""
    with tempfile.TemporaryDirectory(prefix="kai-feishu-voice-") as directory:
        source, target = Path(directory) / "voice.bin", Path(directory) / "voice.wav"
        source.write_bytes(content)
        source.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "1",
            "-format_whitelist",
            "ogg,wav,mp3,aac,flac,matroska,webm,mov",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-vn",
            "-t",
            str(maximum_seconds + 1),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(25):
                await process.wait()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode or not target.exists():
            raise MediaRejectedError("无法解码这条语音，请重新录制或改发文字。")
        return target.read_bytes()


class FeishuMedia:
    def __init__(
        self,
        downloader: ResourceDownloader,
        inputs: InputArtifactService,
        dictation: DictationService,
        *,
        vision_route: Callable[[str], Awaitable[str]] | None = None,
        channel_label: str = "飞书",
        channel_key: str = "feishu",
    ) -> None:
        self.downloader, self.inputs, self.dictation = downloader, inputs, dictation
        self.vision_route = vision_route
        self.channel_label, self.channel_key = channel_label, channel_key

    async def prepare(self, tenant_id: str, user_id: str, incoming: Incoming) -> PreparedMessage:
        artifacts: list[InputArtifact] = []
        model_route = None
        texts = [incoming.text] if incoming.text else []
        for index, resource in enumerate(incoming.resources, 1):
            if resource.kind == "audio":
                if not self.dictation.settings.enabled:
                    raise MediaRejectedError("语音识别尚未启用，请管理员配置后重试，或改发文字。")
                if resource.duration_ms > self.dictation.settings.max_session_seconds * 1000:
                    raise MediaRejectedError(
                        f"语音超过 {self.dictation.settings.max_session_seconds} 秒上限，"
                        "请分段发送。"
                    )
            downloaded = await self.downloader.download(incoming.message_id, resource)
            content = downloaded.content
            media_type = downloaded.media_type or "application/octet-stream"
            name = safe_name(downloaded.name, self.channel_label + "文件")
            if media_type in {"application/octet-stream", "binary/octet-stream"}:
                media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            kind = resource.kind
            if kind == "file" and media_type.startswith("image/"):
                kind = "image"
            elif kind == "file" and media_type.startswith("audio/"):
                kind = "audio"
            if kind == "image":
                if self.vision_route is not None:
                    model_route = await self.vision_route(tenant_id)
                try:
                    with Image.open(BytesIO(content)) as image:
                        if image.width * image.height > 40_000_000:
                            raise MediaRejectedError("图片分辨率过大，请缩小后重发。")
                        image.verify()
                        format_name = (image.format or "").lower()
                        if format_name not in {"png", "jpeg", "webp", "gif"}:
                            raise MediaRejectedError("图片格式暂不支持，请改发 PNG、JPEG 或 WebP。")
                    media_type = "image/" + format_name
                    if resource.kind == "image":
                        suffix = f"-{index}" if len(incoming.resources) > 1 else ""
                        name = (
                            self.channel_label
                            + "图片"
                            + suffix
                            + "."
                            + ("jpg" if format_name == "jpeg" else format_name)
                        )
                except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
                    raise MediaRejectedError("无法读取这张图片，请重新发送。") from error
                if not incoming.text and not texts:
                    texts.append(
                        f"请查看{self.channel_label}图片，说明主要内容，并准确提取其中的文字。"
                    )
            elif kind == "audio":
                if not self.dictation.settings.enabled:
                    raise MediaRejectedError("语音识别尚未启用，请管理员配置后重试，或改发文字。")
                name = safe_name(downloaded.name, self.channel_label + "语音.opus")
                if content.startswith(b"OggS"):
                    media_type = "audio/ogg"
                elif content.startswith(b"RIFF"):
                    media_type = "audio/wav"
                elif not media_type.startswith("audio/"):
                    media_type = "audio/opus"
                wav = await decode_voice(content, self.dictation.settings.max_session_seconds)
                transcript = downloaded.transcript.strip()
                if transcript:
                    if len(transcript) > 12000:
                        raise MediaRejectedError("语音转写内容过长，请分段发送。")
                    validate_wav(wav, self.dictation.settings.max_session_seconds)
                else:
                    try:
                        transcript = await self.dictation.transcribe(wav)
                    except DictationError as error:
                        # Validation failures are definitive; upstream ASR outages retry.
                        try:
                            validate_wav(wav, self.dictation.settings.max_session_seconds)
                        except DictationError as invalid:
                            raise MediaRejectedError(str(invalid)) from invalid
                        raise ConnectionError("Feishu voice transcription unavailable") from error
                if not transcript:
                    raise MediaRejectedError("这条语音未识别出文字，请重新录制或改发文字。")
                texts.append(self.channel_label + "语音转写：\n" + transcript)
            else:
                if not incoming.text and not texts:
                    texts.append(f"请阅读{self.channel_label}文件，概括内容和关键信息。")
            artifact = await self.inputs.upload(
                tenant_id=tenant_id,
                user_id=user_id,
                name=name,
                media_type=media_type,
                content=content,
                idempotency_key=f"{self.channel_key}:{incoming.actor.app_id}:{incoming.message_id}:{resource.key}",
            )
            artifacts.append(artifact)
        return PreparedMessage(
            text="\n\n".join(texts), artifacts=artifacts, model_route=model_route
        )
