"""Tenant model connections managed from the Studio control plane.

The capability catalog stores non-secret routing metadata. API keys reuse the
encrypted credential repository under a tenant-owned principal and are never
included in API responses, logs, manifests, or task input.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, Literal, cast
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import Field, SecretStr, model_validator

from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import ModelCompatibility, Run, RunStatus
from harness.core.ports import RunRepository, SessionRepository
from harness.runtime.cc_switch import CcSwitchClaudeConfig
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.mcp_credential_store import (
    McpCredentialService,
    StoredMcpCredential,
)
from harness.studio.models import (
    CapabilityCatalogRecord,
    ModelRouteCapability,
    ReplaceCapabilityCatalogRequest,
    StudioModel,
    UpsertCatalogResourceRequest,
)

_MODEL_CREDENTIAL_OWNER = "tenant:model-control-plane"
_MODEL_IMPORT_ACTOR = "system:model-control-plane-import"
_API_KEY = "api_key"


class ConfigureModelRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    label: str = Field(min_length=1, max_length=160)
    model_type: Literal["chat", "vision", "image_generation", "video_generation"] = Field(
        alias="modelType"
    )
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=300)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    api_format: Literal[
        "anthropic_compatible",
        "openai_compatible",
        "openai_images",
        "openai_videos",
        "minimax_videos",
        "minimax_videos_v2",
    ] = Field(alias="apiFormat")
    auth_scheme: Literal["bearer", "x-api-key", "none"] = Field(alias="authScheme")
    api_key: SecretStr | None = Field(default=None, alias="apiKey", min_length=1, max_length=16_384)
    enabled: bool = True


class ModelConfiguration(StudioModel):
    route_id: str = Field(alias="routeId")
    label: str
    model_type: Literal["chat", "vision", "image_generation", "video_generation"] = Field(
        alias="modelType"
    )
    provider: str
    model: str
    base_url: str | None = Field(alias="baseUrl")
    api_format: Literal[
        "anthropic_compatible",
        "openai_compatible",
        "openai_images",
        "openai_videos",
        "minimax_videos",
        "minimax_videos_v2",
    ] = Field(alias="apiFormat")
    auth_scheme: Literal["bearer", "x-api-key", "none"] = Field(alias="authScheme")
    capabilities: tuple[str, ...]
    enabled: bool
    credential_configured: bool = Field(alias="credentialConfigured")
    deletable: bool
    version: int


class ModelConfigurationList(StudioModel):
    revision: int
    models: tuple[ModelConfiguration, ...]
    agent_model_bindings: dict[str, str] = Field(alias="agentModelBindings")


class BindAgentModelRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    route_id: str = Field(alias="routeId", pattern=r"^[a-z][a-z0-9-]*$")


class ModelConnectionTestResult(StudioModel):
    ok: bool
    latency_ms: int = Field(alias="latencyMs", ge=0)
    message: str


class GenerateImageRequest(StudioModel):
    prompt: str = Field(min_length=1, max_length=8_000)
    size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024"
    quality: Literal["standard", "high"] = "standard"
    count: int = Field(default=1, ge=1, le=4)


class GeneratedImage(StudioModel):
    url: str | None = None
    b64_json: str | None = Field(default=None, alias="b64Json")
    revised_prompt: str | None = Field(default=None, alias="revisedPrompt")


class GenerateImageResult(StudioModel):
    model: str
    images: tuple[GeneratedImage, ...]


class VideoTaskContext(StudioModel):
    thread_id: str = Field(alias="threadId", min_length=1, max_length=128)
    agent_name: str = Field(alias="agentName", min_length=1, max_length=128)
    agent_version: str = Field(alias="agentVersion", min_length=1, max_length=128)
    agent_owner_user_id: str | None = Field(default=None, alias="agentOwnerUserId")
    space_id: str | None = Field(default=None, alias="spaceId")


class GenerateVideoRequest(StudioModel):
    task: VideoTaskContext | None = None
    prompt: str = Field(min_length=1, max_length=8_000)
    mode: Literal["auto", "ref2va"] = "auto"
    aspect_ratio: Literal["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"] = Field(
        default="16:9", alias="aspectRatio"
    )
    seconds: int = Field(default=5, ge=4, le=15)
    resolution: Literal["480P", "768P", "1080P", "2K"] = "768P"
    seed: int | None = Field(default=None, ge=0)
    negative_prompt: str | None = Field(default=None, alias="negativePrompt", max_length=4_000)
    input_artifact_ids: tuple[str, ...] = Field(default=(), alias="inputArtifactIds", max_length=9)

    @model_validator(mode="after")
    def validate_input_artifacts(self) -> GenerateVideoRequest:
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("duplicate video reference image")
        if any(
            len(item) > 128 or not item.startswith("input_artifact_")
            for item in self.input_artifact_ids
        ):
            raise ValueError("invalid input artifact ID")
        if self.mode == "ref2va" and not self.input_artifact_ids:
            raise ValueError("Ref2VA requires at least one reference image")
        if self.mode == "auto" and len(self.input_artifact_ids) > 2:
            raise ValueError("automatic video mode accepts at most two reference images")
        return self


class VideoGenerationJob(StudioModel):
    job_id: str = Field(alias="jobId")
    status: Literal["queued", "in_progress", "completed", "failed", "cancelled"]
    progress: int = Field(default=0, ge=0, le=100)
    error: str | None = None
    inference_time_seconds: float | None = Field(default=None, alias="inferenceTimeSeconds")
    video_url: str | None = Field(default=None, alias="videoUrl")
    cancellation_supported: bool = Field(default=True, alias="cancellationSupported")


class VideoGenerationQuota(StudioModel):
    daily_total: int = Field(alias="dailyTotal", ge=0)
    daily_used: int = Field(alias="dailyUsed", ge=0)
    daily_remaining: int = Field(alias="dailyRemaining", ge=0)
    weekly_total: int = Field(alias="weeklyTotal", ge=0)
    weekly_used: int = Field(alias="weeklyUsed", ge=0)
    weekly_remaining: int = Field(alias="weeklyRemaining", ge=0)


@dataclass(frozen=True, slots=True)
class GeneratedVideo:
    content: bytes
    media_type: str
    request_id: str | None
    inference_time_seconds: str | None


@dataclass(frozen=True, slots=True)
class GeneratedVideoReference:
    name: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class _ManagedVideoJob:
    tenant_id: str
    user_id: str
    route_id: str
    provider_job_id: str
    created_at: datetime


_MAX_GENERATED_VIDEO_BYTES = 128 * 1024 * 1024
_MAX_VIDEO_REFERENCE_BYTES = 60 * 1024 * 1024
_MAX_VIDEO_REFERENCE_IMAGE_BYTES = 30 * 1024 * 1024
_MIN_VIDEO_REFERENCE_DIMENSION = 256
_MAX_VIDEO_REFERENCE_DIMENSION = 5_760
_MIN_VIDEO_REFERENCE_ASPECT_RATIO = 1 / 4
_MAX_VIDEO_REFERENCE_ASPECT_RATIO = 4


class ModelConfigurationService:
    def __init__(
        self,
        catalogs: CapabilityCatalogService,
        credentials: McpCredentialService,
        *,
        environment: str = "local",
        server_routes: Iterable[CcSwitchClaudeConfig] = (),
        http_client: httpx.AsyncClient | None = None,
        runs: RunRepository | None = None,
        sessions: SessionRepository | None = None,
    ) -> None:
        self._catalogs = catalogs
        self._credentials = credentials
        self._environment = environment
        self._server_routes = {
            route.route_id: route for route in server_routes if route.route_id is not None
        }
        self._http_client = http_client
        self._video_jobs: dict[str, _ManagedVideoJob] = {}
        self._runs = runs
        self._sessions = sessions
        self._video_monitors: dict[str, asyncio.Task[None]] = {}

    async def list(self, tenant_id: str) -> ModelConfigurationList:
        record = await self._import_server_models(tenant_id)
        stored = {
            item.reference
            for item in await self._credentials.repository.list_for_user(
                tenant_id, _MODEL_CREDENTIAL_OWNER
            )
        }
        return ModelConfigurationList(
            revision=record.revision,
            models=tuple(
                ModelConfiguration(
                    routeId=route.route_id,
                    label=route.label,
                    modelType=route.model_type,
                    provider=route.provider,
                    model=route.models[0],
                    baseUrl=route.base_url,
                    apiFormat=route.api_format,
                    authScheme=route.auth_scheme,
                    capabilities=route.capabilities,
                    enabled=route.enabled,
                    credentialConfigured=(route.auth_scheme == "none" or route.route_id in stored),
                    deletable=True,
                    version=route.version,
                )
                for route in record.catalog.model_routes
            ),
            agentModelBindings=dict(record.catalog.agent_model_bindings),
        )

    async def configure(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        request: ConfigureModelRequest,
    ) -> ModelConfigurationList:
        record = await self._import_server_models(tenant_id)
        if record.revision != request.expected_revision:
            raise ConflictError("model catalog revision changed; reload before saving")
        self._validate_endpoint(request.base_url)
        current = next(
            (route for route in record.catalog.model_routes if route.route_id == route_id),
            None,
        )
        credential = await self._credentials.repository.get(
            tenant_id, _MODEL_CREDENTIAL_OWNER, route_id
        )
        if request.auth_scheme == "none" and request.api_key is not None:
            raise ConflictError("API keys cannot be stored for an unauthenticated model")
        if request.auth_scheme != "none" and request.api_key is None and credential is None:
            raise ConflictError("an API key is required when creating a model connection")
        capabilities = {
            "chat": ("streaming", "tool_use"),
            "vision": ("streaming", "tool_use", "vision"),
            "image_generation": ("image_generation",),
            "video_generation": ("video_generation",),
        }[request.model_type]
        route = ModelRouteCapability(
            routeId=route_id,
            label=request.label.strip(),
            modelType=request.model_type,
            provider=request.provider.strip(),
            models=(request.model.strip(),),
            baseUrl=request.base_url.rstrip("/"),
            apiFormat=request.api_format,
            authScheme=request.auth_scheme,
            capabilities=capabilities,
            credentialManaged=True,
            credentialReference=None,
            version=(current.version + 1 if current is not None else 1),
            enabled=request.enabled,
        )
        await self._catalogs.upsert(
            tenant_id=tenant_id,
            user_id=user_id,
            resource_type="modelRoute",
            resource_id=route_id,
            request=UpsertCatalogResourceRequest(
                expectedRevision=record.revision,
                resource=route,
            ),
        )
        if request.api_key is not None:
            await self._store_secret(tenant_id, user_id, route_id, request.api_key)
        elif request.auth_scheme == "none" and credential is not None:
            await self._credentials.repository.delete(tenant_id, _MODEL_CREDENTIAL_OWNER, route_id)
        return await self.list(tenant_id)

    async def disable(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        expected_revision: int,
    ) -> ModelConfigurationList:
        await self._catalogs.disable(
            tenant_id=tenant_id,
            user_id=user_id,
            resource_type="modelRoute",
            resource_id=route_id,
            expected_revision=expected_revision,
        )
        return await self.list(tenant_id)

    async def delete(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        expected_revision: int,
    ) -> ModelConfigurationList:
        """Permanently remove a user-created route and its stored credential."""

        await self._catalogs.delete_model(
            tenant_id=tenant_id,
            user_id=user_id,
            resource_id=route_id,
            expected_revision=expected_revision,
        )
        await self._credentials.repository.delete(tenant_id, _MODEL_CREDENTIAL_OWNER, route_id)
        if self._credentials.audit is not None:
            await self._credentials.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.model_configuration.delete",
                resource_type="model_route",
                resource_id=route_id,
                details={},
            )
        return await self.list(tenant_id)

    async def bind_agent(
        self,
        tenant_id: str,
        user_id: str,
        agent_name: str,
        request: BindAgentModelRequest,
    ) -> ModelConfigurationList:
        record = await self._import_server_models(tenant_id)
        route = next(
            (item for item in record.catalog.model_routes if item.route_id == request.route_id),
            None,
        )
        if route is None or not route.enabled or route.model_type not in {"chat", "vision"}:
            raise ConflictError("Agent defaults require an enabled chat or vision model")
        bindings = dict(record.catalog.agent_model_bindings)
        bindings[agent_name] = request.route_id
        await self._catalogs.replace(
            tenant_id=tenant_id,
            user_id=user_id,
            request=ReplaceCapabilityCatalogRequest(
                expectedRevision=request.expected_revision,
                catalog=record.catalog.model_copy(update={"agent_model_bindings": bindings}),
            ),
        )
        return await self.list(tenant_id)

    async def resolve_runtime(
        self,
        tenant_id: str,
        agent_name: str,
        requested_route_id: str,
        *,
        apply_agent_binding: bool = True,
        required_api_format: Literal["anthropic_compatible", "openai_compatible", "openai_images"]
        | None = None,
    ) -> CcSwitchClaudeConfig | None:
        record = await self._import_server_models(tenant_id)
        bound_route_id = (
            record.catalog.agent_model_bindings.get(agent_name) if apply_agent_binding else None
        )
        bound_route = next(
            (item for item in record.catalog.model_routes if item.route_id == bound_route_id),
            None,
        )
        route_id = (
            bound_route_id
            if bound_route_id is not None
            and bound_route is not None
            and (required_api_format is None or bound_route.api_format == required_api_format)
            else requested_route_id
        )
        route = next(
            (item for item in record.catalog.model_routes if item.route_id == route_id), None
        )
        if route is None or not route.enabled:
            return None
        if required_api_format is not None and route.api_format != required_api_format:
            return None
        if route.base_url is None or route.model_type not in {"chat", "vision"}:
            return None
        if route.auth_scheme == "none":
            return None
        secret = await self._secret(tenant_id, route_id)
        if secret is None:
            return None
        return CcSwitchClaudeConfig(
            route_id=route.route_id,
            base_url=self._runtime_sdk_base_url(route),
            model=route.models[0],
            provider=("anthropic" if route.auth_scheme == "x-api-key" else "new-api"),
            credential=secret,
            auth_scheme=route.auth_scheme,
            api_format=route.api_format,
            compatibility=ModelCompatibility.FULL,
            capabilities=frozenset(route.capabilities),
        )

    async def complete_text(
        self,
        tenant_id: str,
        route_id: str,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 256,
        images: tuple[tuple[str, bytes], ...] = (),
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Complete control-plane text, optionally forwarding provider deltas."""

        route = await self._route(tenant_id, route_id)
        if not route.enabled or route.model_type not in {"chat", "vision"}:
            raise ConflictError("the selected route is not an enabled text model")
        if route.api_format not in {"anthropic_compatible", "openai_compatible"}:
            raise ConflictError("the selected route does not support text completion")
        if images and "vision" not in route.capabilities:
            raise ConflictError("当前模型不支持看图，请配置可用的视觉模型")
        user_content: object = user_prompt
        if images:
            if route.api_format == "openai_compatible":
                user_content = [
                    {"type": "text", "text": user_prompt},
                    *(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"
                            },
                        }
                        for mime, data in images
                    ),
                ]
            else:
                user_content = [
                    {"type": "text", "text": user_prompt},
                    *(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": base64.b64encode(data).decode(),
                            },
                        }
                        for mime, data in images
                    ),
                ]
        secret = await self._credential_for_route(tenant_id, route)
        if route.api_format == "openai_compatible":
            path = "chat/completions"
            payload: dict[str, object] = {
                "model": route.models[0],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
        else:
            path = "messages"
            payload = {
                "model": route.models[0],
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
                "max_tokens": max_tokens,
                "temperature": 0,
                "thinking": {"type": "disabled"},
            }
        try:
            if on_delta is not None:
                return await self._stream_completion(route, secret, path, payload, on_delta)
            response = await self._post(route, secret, path, payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ConflictError(
                f"model provider rejected the completion (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("model provider completion failed") from None
        text = self._completion_text(response.json(), route.api_format)
        if not text:
            raise ConflictError("model provider returned no visible completion text")
        return text

    async def _stream_completion(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        path: str,
        payload: dict[str, object],
        on_delta: Callable[[str], Awaitable[None]],
    ) -> str:
        assert route.base_url is not None
        base = route.base_url.rstrip("/")
        if route.api_format == "anthropic_compatible" and not base.endswith("/v1"):
            base += "/v1"
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=10),
            follow_redirects=False,
        )
        chunks: list[str] = []
        finished = False
        try:
            async with client.stream(
                "POST",
                f"{base}/{path}",
                headers=self._headers(route, secret),
                json={**payload, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    try:
                        event = json.loads(data)
                    except ValueError:
                        raise ConflictError("模型返回了无效的流式响应") from None
                    if not isinstance(event, dict):
                        raise ConflictError("模型返回了无效的流式响应")
                    event = cast(dict[str, Any], event)
                    if event.get("error") or event.get("type") == "error":
                        raise ConflictError("模型流式输出失败，请重试")
                    text = ""
                    if route.api_format == "openai_compatible":
                        choices = event.get("choices")
                        if choices:
                            if not isinstance(choices, list) or not isinstance(choices[0], dict):
                                raise ConflictError("模型返回了无效的流式响应")
                            choice = cast(dict[str, Any], choices[0])
                            delta = choice.get("delta")
                            if delta is not None and not isinstance(delta, dict):
                                raise ConflictError("模型返回了无效的流式响应")
                            text = cast(dict[str, Any], delta or {}).get("content", "")
                            if choice.get("finish_reason") == "length":
                                raise ConflictError("模型输出超出长度限制，请缩小修改范围")
                            finished = finished or choice.get("finish_reason") is not None
                    elif event.get("type") == "content_block_delta":
                        text = event.get("delta", {}).get("text", "")
                    elif event.get("type") == "message_delta":
                        if event.get("delta", {}).get("stop_reason") == "max_tokens":
                            raise ConflictError("模型输出超出长度限制，请缩小修改范围")
                    elif event.get("type") == "message_stop":
                        finished = True
                    if isinstance(text, str) and text:
                        chunks.append(text)
                        await on_delta(text)
            if not finished or not chunks:
                raise ConflictError("模型流式输出中断或未返回正文，请重试")
            return "".join(chunks)
        finally:
            if self._http_client is None:
                await client.aclose()

    @staticmethod
    def _completion_text(payload: object, api_format: str) -> str:
        if not isinstance(payload, dict):
            return ""
        values = cast(dict[str, object], payload)
        if api_format == "openai_compatible":
            choices = values.get("choices")
            if not isinstance(choices, list) or not choices:
                return ""
            first = cast(list[object], choices)[0]
            if not isinstance(first, dict):
                return ""
            message = cast(dict[str, object], first).get("message")
            if not isinstance(message, dict):
                return ""
            content = cast(dict[str, object], message).get("content")
            if isinstance(content, str):
                return content.strip()
            return ""

        content = values.get("content")
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        return "\n".join(
            text.strip()
            for item in cast(list[object], content)
            if isinstance(item, dict)
            and isinstance((text := cast(dict[str, object], item).get("text")), str)
            and text.strip()
        )

    async def test(self, tenant_id: str, route_id: str) -> ModelConnectionTestResult:
        from time import monotonic

        route = await self._route(tenant_id, route_id)
        secret = await self._credential_for_route(tenant_id, route)
        started = monotonic()
        payload: dict[str, object]
        if route.model_type == "video_generation":
            if route.api_format == "minimax_videos_v2":
                raw = self._minimax_h3_response(
                    await self._get(route, secret, "query/video_generation?page_num=1&page_size=1")
                )
                if not isinstance(raw.get("items"), list):
                    raise ConflictError("MiniMax H3 未返回有效的任务列表。")
                return ModelConnectionTestResult(
                    ok=True,
                    latencyMs=int((monotonic() - started) * 1000),
                    message="连接成功，H3 服务已就绪。",
                )
            if route.api_format == "minimax_videos":
                quota = await self.video_quota(tenant_id, route_id)
                return ModelConnectionTestResult(
                    ok=True,
                    latencyMs=int((monotonic() - started) * 1000),
                    message=(
                        f"连接成功，今日视频额度剩余 "
                        f"{quota.daily_remaining}/{quota.daily_total} 条。"
                    ),
                )
            try:
                response = await self._get(route, secret, "models")
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise ConflictError(
                    f"model provider rejected the test request (HTTP {error.response.status_code})"
                ) from None
            except httpx.HTTPError:
                raise ConflictError("model provider connection failed") from None
            return ModelConnectionTestResult(
                ok=True,
                latencyMs=int((monotonic() - started) * 1000),
                message="连接成功，视频服务已就绪。",
            )
        if route.model_type == "image_generation":
            payload = {
                "model": route.models[0],
                "prompt": "A small green circle on a white background",
                "size": "1024x1024",
                "n": 1,
            }
            path = "images/generations"
        elif route.api_format == "openai_compatible":
            payload = {
                "model": route.models[0],
                "messages": [{"role": "user", "content": "Reply OK"}],
                "max_tokens": 2,
            }
            path = "chat/completions"
        else:
            payload = {
                "model": route.models[0],
                "messages": [{"role": "user", "content": "Reply OK"}],
                "max_tokens": 2,
            }
            path = "messages"
        try:
            response = await self._post(route, secret, path, payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ConflictError(
                f"model provider rejected the test request (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("model provider connection failed") from None
        return ModelConnectionTestResult(
            ok=True,
            latencyMs=int((monotonic() - started) * 1000),
            message="连接成功，服务已返回有效响应。",
        )

    async def generate_image(
        self, tenant_id: str, route_id: str, request: GenerateImageRequest
    ) -> GenerateImageResult:
        route = await self._route(tenant_id, route_id)
        if route.model_type != "image_generation" or not route.enabled:
            raise ConflictError("the selected route is not an enabled image generation model")
        secret = await self._credential_for_route(tenant_id, route)
        try:
            response = await self._post(
                route,
                secret,
                "images/generations",
                {
                    "model": route.models[0],
                    "prompt": request.prompt,
                    "size": request.size,
                    "quality": request.quality,
                    "n": request.count,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ConflictError(
                f"image provider rejected the request (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("image provider connection failed") from None
        raw = cast(object, response.json())
        if not isinstance(raw, dict):
            raise ConflictError("image provider returned an invalid response")
        raw_values = cast(dict[object, object], raw)
        data = raw_values.get("data")
        if not isinstance(data, list):
            raise ConflictError("image provider returned an invalid response")
        images: list[GeneratedImage] = []
        for item in cast(list[object], data):
            if not isinstance(item, dict):
                continue
            values = cast(dict[str, object], item)
            raw_url = values.get("url")
            raw_b64 = values.get("b64_json")
            raw_revised_prompt = values.get("revised_prompt")
            images.append(
                GeneratedImage(
                    url=raw_url if isinstance(raw_url, str) else None,
                    b64Json=raw_b64 if isinstance(raw_b64, str) else None,
                    revisedPrompt=(
                        raw_revised_prompt if isinstance(raw_revised_prompt, str) else None
                    ),
                )
            )
        if not images:
            raise ConflictError("image provider returned no images")
        return GenerateImageResult(model=route.models[0], images=tuple(images))

    async def create_video_job(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        request: GenerateVideoRequest,
        *,
        references: tuple[GeneratedVideoReference, ...] = (),
        task_session_id: str | None = None,
    ) -> VideoGenerationJob:
        route = await self._route(tenant_id, route_id)
        if route.model_type != "video_generation" or not route.enabled:
            raise ConflictError("the selected route is not an enabled video generation model")
        secret = await self._credential_for_route(tenant_id, route)
        form, reference_files = self._video_request_form(route, request, references)
        if route.api_format == "minimax_videos_v2":
            view = await self._create_minimax_h3_video(
                tenant_id, user_id, route, secret, request, references
            )
            await self._record_video_task(view, route, request, task_session_id)
            return view
        if route.api_format == "minimax_videos":
            view = await self._create_minimax_video(
                tenant_id, user_id, route, secret, request, references
            )
            await self._record_video_task(view, route, request, task_session_id)
            return view
        try:
            response = await self._post_multipart(
                route,
                secret,
                "videos",
                form,
                reference_files,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ConflictError(
                f"video provider rejected the request (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("video provider connection failed") from None
        raw = self._video_provider_response(response)
        provider_job_id = raw.get("id")
        if (
            not isinstance(provider_job_id, str)
            or not provider_job_id
            or len(provider_job_id) > 256
        ):
            raise ConflictError("video provider returned an invalid job identifier")
        job_id = f"video_job_{uuid4().hex}"
        self._video_jobs[job_id] = _ManagedVideoJob(
            tenant_id=tenant_id,
            user_id=user_id,
            route_id=route_id,
            provider_job_id=provider_job_id,
            created_at=datetime.now(UTC),
        )
        self._prune_video_jobs()
        view = self._video_job_view(job_id, raw)
        await self._record_video_task(view, route, request, task_session_id)
        return view

    async def get_video_job(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        job_id: str,
    ) -> VideoGenerationJob:
        job = await self._video_job(tenant_id, user_id, route_id, job_id)
        route = await self._route(tenant_id, route_id)
        secret = await self._credential_for_route(tenant_id, route)
        if route.api_format in {"minimax_videos", "minimax_videos_v2"}:
            view = await self._get_minimax_video(route, secret, job_id, job.provider_job_id)
            await self._update_video_task(tenant_id, view)
            return view
        try:
            response = await self._get(
                route, secret, f"videos/{quote(job.provider_job_id, safe='')}"
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                self._video_jobs.pop(job_id, None)
                raise NotFoundError(f"video generation job not found: {job_id}") from None
            raise ConflictError(
                f"video provider rejected the status request (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("video provider connection failed") from None
        view = self._video_job_view(job_id, self._video_provider_response(response))
        await self._update_video_task(tenant_id, view)
        return view

    async def cancel_video_job(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        job_id: str,
    ) -> VideoGenerationJob:
        job = await self._video_job(tenant_id, user_id, route_id, job_id)
        route = await self._route(tenant_id, route_id)
        secret = await self._credential_for_route(tenant_id, route)
        if route.api_format in {"minimax_videos", "minimax_videos_v2"}:
            # Stop waiting without deleting the remote record. H3 can only cancel
            # queued tasks; running tasks remain available for status queries.
            return VideoGenerationJob(jobId=job_id, status="cancelled", cancellationSupported=False)
        try:
            response = await self._delete(
                route, secret, f"videos/{quote(job.provider_job_id, safe='')}"
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                self._video_jobs.pop(job_id, None)
                raise NotFoundError(f"video generation job not found: {job_id}") from None
            raise ConflictError(
                f"video provider rejected cancellation (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("video provider connection failed") from None
        self._video_jobs.pop(job_id, None)
        view = VideoGenerationJob(jobId=job_id, status="cancelled", progress=0)
        await self._update_video_task(tenant_id, view)
        return view

    async def download_video(
        self,
        tenant_id: str,
        user_id: str,
        route_id: str,
        job_id: str,
    ) -> GeneratedVideo:
        job = await self._video_job(tenant_id, user_id, route_id, job_id)
        route = await self._route(tenant_id, route_id)
        secret = await self._credential_for_route(tenant_id, route)
        if route.api_format in {"minimax_videos", "minimax_videos_v2"}:
            result = await self._get_minimax_video(route, secret, job_id, job.provider_job_id)
            if result.status != "completed" or result.video_url is None:
                raise ConflictError("视频尚未生成完成。")
            # Only explicit downloads use the authenticated content endpoint.
            # Playback keeps the default provider URL. Never forward the model
            # credential to the CDN or accept a download URL from the caller.
            client = self._http_client or httpx.AsyncClient(timeout=60.0, follow_redirects=False)
            try:
                async with client.stream("GET", result.video_url) as response:
                    response.raise_for_status()
                    media_type = response.headers.get("content-type", "").partition(";")[0]
                    if media_type.strip() != "video/mp4":
                        raise ConflictError("MiniMax 未返回有效的 MP4 文件。")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > _MAX_GENERATED_VIDEO_BYTES:
                            raise ConflictError("视频文件超过 128 MiB 下载限制。")
                        content.extend(chunk)
            except httpx.HTTPError:
                raise ConflictError("视频下载暂时不可用，请稍后重试。") from None
            finally:
                if self._http_client is None:
                    await client.aclose()
            if len(content) < 12 or bytes(content[4:8]) != b"ftyp":
                raise ConflictError("MiniMax 未返回有效的 MP4 文件。")
            return GeneratedVideo(
                content=bytes(content),
                media_type="video/mp4",
                request_id=None,
                inference_time_seconds=None,
            )
        try:
            response = await self._get(
                route,
                secret,
                f"videos/{quote(job.provider_job_id, safe='')}/content",
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ConflictError(
                f"video provider rejected the download (HTTP {error.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise ConflictError("video provider connection failed") from None
        media_type = response.headers.get("content-type", "").partition(";")[0].strip()
        if (
            media_type != "video/mp4"
            or len(response.content) < 12
            or response.content[4:8] != b"ftyp"
        ):
            raise ConflictError("video provider returned an invalid MP4 response")
        if len(response.content) > _MAX_GENERATED_VIDEO_BYTES:
            raise ConflictError("video provider response exceeds the 128 MiB limit")
        return GeneratedVideo(
            content=response.content,
            media_type=media_type,
            request_id=response.headers.get("x-request-id"),
            inference_time_seconds=response.headers.get("x-inference-time-s"),
        )

    async def video_quota(self, tenant_id: str, route_id: str) -> VideoGenerationQuota:
        route = await self._route(tenant_id, route_id)
        if route.api_format != "minimax_videos" or not route.enabled:
            raise ConflictError("该模型不支持套餐视频额度查询。")
        secret = await self._credential_for_route(tenant_id, route)
        if secret is None or not secret.get_secret_value().startswith("sk-cp-"):
            raise ConflictError("视频额度查询需要 MiniMax 套餐 Key。")
        # The official API also serves the subscription account endpoint.
        # Use the configured API host; the website host can be unreachable
        # from deployment networks even when model generation works.
        try:
            raw = self._minimax_response(await self._get(route, secret, "token_plan/remains"))
        except httpx.HTTPError:
            raise ConflictError("MiniMax 视频额度暂时不可用，请稍后重试。") from None
        remains = raw.get("model_remains")
        if not isinstance(remains, list):
            raise ConflictError("MiniMax 未返回有效的视频额度。")
        video: dict[str, object] | None = None
        for item in cast(list[object], remains):
            if isinstance(item, dict):
                values = cast(dict[str, object], item)
                if values.get("model_name") == "video":
                    video = values
                    break
        if video is None:
            raise ConflictError("当前套餐未提供视频生成额度。")

        def count(name: str) -> int:
            assert video is not None
            value = video.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ConflictError("MiniMax 未返回有效的视频额度。")
            return value

        daily_total = count("current_interval_total_count")
        daily_used = count("current_interval_usage_count")
        weekly_total = count("current_weekly_total_count")
        weekly_used = count("current_weekly_usage_count")
        return VideoGenerationQuota(
            dailyTotal=daily_total,
            dailyUsed=daily_used,
            dailyRemaining=max(0, daily_total - daily_used),
            weeklyTotal=weekly_total,
            weeklyUsed=weekly_used,
            weeklyRemaining=max(0, weekly_total - weekly_used),
        )

    @staticmethod
    def _minimax_response(response: httpx.Response) -> dict[str, object]:
        try:
            response.raise_for_status()
            raw = cast(object, response.json())
        except (httpx.HTTPError, ValueError):
            raise ConflictError("MiniMax 视频服务请求失败，请稍后重试。") from None
        if not isinstance(raw, dict):
            raise ConflictError("MiniMax 视频服务返回了无效响应。")
        values = cast(dict[str, object], raw)
        base_resp = values.get("base_resp")
        if not isinstance(base_resp, dict):
            raise ConflictError("MiniMax 视频服务返回了无效响应。")
        code = cast(dict[str, object], base_resp).get("status_code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise ConflictError("MiniMax 视频服务返回了无效状态码。")
        if code != 0:
            messages = {
                1002: "MiniMax 请求过于频繁，请稍后重试。",
                1004: "MiniMax Key 无效，请检查模型配置。",
                1008: "MiniMax 视频额度不足。",
                2013: "MiniMax 视频参数无效。",
            }
            # Provider messages may echo credentials or inputs. Only publish
            # known messages and the numeric code, never the upstream body.
            suffix = f"（{code}）"
            raise ConflictError(messages.get(code, "MiniMax 视频请求未成功。") + suffix)
        return values

    async def _create_minimax_video(
        self,
        tenant_id: str,
        user_id: str,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        request: GenerateVideoRequest,
        references: tuple[GeneratedVideoReference, ...],
    ) -> VideoGenerationJob:
        if (
            request.resolution not in {"768P", "1080P"}
            or request.seconds not in {6, 10}
            or request.mode != "auto"
            or len(references) > 1
            or request.seed is not None
            or request.negative_prompt
            or request.aspect_ratio != "16:9"
        ):
            raise ConflictError("官方视频支持 6 或 10 秒，可添加一张首帧图片；不支持 H3 高级参数。")
        if request.resolution == "1080P" and request.seconds != 6:
            raise ConflictError("1080P 视频仅支持 6 秒，请调整时长或选择 768P。")
        # The provider accounts for plan allowances and extra credits. A plan-only
        # quota check must not block requests that can use the account's balance.
        payload: dict[str, object] = {
            "model": route.models[0],
            "prompt": request.prompt,
            "duration": request.seconds,
            "resolution": request.resolution,
        }
        if references:
            item = references[0]
            payload["first_frame_image"] = f"data:{item.media_type};base64," + base64.b64encode(
                item.content
            ).decode("ascii")
        # Submit once. Retrying a POST can consume additional credits.
        try:
            raw = self._minimax_response(
                await self._post(route, secret, "video_generation", payload)
            )
        except httpx.HTTPError:
            raise ConflictError("MiniMax 连接中断；请先核实额度，避免重复提交。") from None
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id.isdigit() or len(task_id) > 256:
            raise ConflictError("MiniMax 未返回有效的视频任务 ID。")
        job_id = f"video_job_{uuid4().hex}"
        self._video_jobs[job_id] = _ManagedVideoJob(
            tenant_id=tenant_id,
            user_id=user_id,
            route_id=route.route_id,
            provider_job_id=task_id,
            created_at=datetime.now(UTC),
        )
        self._prune_video_jobs()
        return VideoGenerationJob(jobId=job_id, status="queued", cancellationSupported=False)

    @staticmethod
    def _minimax_h3_response(response: httpx.Response) -> dict[str, object]:
        if response.is_error:
            messages = {
                401: "MiniMax H3 Key 无效或无权访问。",
                402: "MiniMax H3 余额不足。",
                429: "MiniMax H3 请求过于频繁。",
            }
            raise ConflictError(messages.get(response.status_code, "MiniMax H3 请求失败。"))
        raw = ModelConfigurationService._video_provider_response(response)
        if "error" in raw:
            raise ConflictError("MiniMax H3 请求失败。")
        return raw

    async def _create_minimax_h3_video(
        self,
        tenant_id: str,
        user_id: str,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        request: GenerateVideoRequest,
        references: tuple[GeneratedVideoReference, ...],
    ) -> VideoGenerationJob:
        model = route.models[0]
        resolutions = {"MiniMax-H3": {"768P", "2K"}, "MiniMax-H3-Max": {"480P", "768P"}}
        if model not in resolutions or request.resolution not in resolutions[model]:
            raise ConflictError("当前 H3 模型不支持所选分辨率。")
        if request.seconds < (5 if model == "MiniMax-H3-Max" else 4):
            raise ConflictError("H3 Max 支持 5–15 秒。")
        if len(request.prompt) > 7000 or request.seed is not None or request.negative_prompt:
            raise ConflictError("H3 提示词最多 7000 字，不支持随机种子或负向提示词。")
        content: list[dict[str, object]] = [{"type": "text", "text": request.prompt}]
        for index, reference in enumerate(references):
            with Image.open(BytesIO(reference.content)) as image:
                width, height = image.size
            if not 2 / 5 <= width / height <= 5 / 2:
                raise ConflictError("H3 参考图片比例需在 2:5 至 5:2 之间。")
            role = (
                "reference_image"
                if request.mode == "ref2va"
                else ("first_frame" if index == 0 else "last_frame")
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{reference.media_type};base64,"
                        + base64.b64encode(reference.content).decode("ascii")
                    },
                    "role": role,
                }
            )
        payload: dict[str, object] = {
            "model": model,
            "content": content,
            "duration": request.seconds,
            "resolution": request.resolution,
            "ratio": "adaptive" if references and request.mode == "auto" else request.aspect_ratio,
        }
        if len(json.dumps(payload).encode()) > 64 * 1024 * 1024:
            raise ConflictError("H3 请求超过 64 MiB，请减少参考素材。")
        try:
            raw = self._minimax_h3_response(
                await self._post(route, secret, "video_generation", payload)
            )
        except httpx.HTTPError:
            raise ConflictError("MiniMax H3 连接中断，请先核实任务状态，避免重复提交。") from None
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id.isdigit() or len(task_id) > 256:
            raise ConflictError("MiniMax H3 未返回有效的视频任务 ID。")
        job_id = f"video_job_{uuid4().hex}"
        self._video_jobs[job_id] = _ManagedVideoJob(
            tenant_id, user_id, route.route_id, task_id, datetime.now(UTC)
        )
        self._prune_video_jobs()
        return VideoGenerationJob(jobId=job_id, status="queued", cancellationSupported=False)

    async def _get_minimax_h3_video(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        job_id: str,
        task_id: str,
    ) -> VideoGenerationJob:
        try:
            raw = self._minimax_h3_response(
                await self._get(route, secret, f"query/video_generation/{quote(task_id, safe='')}")
            )
        except httpx.HTTPError:
            raise ConflictError("MiniMax H3 视频状态暂时不可用，请稍后继续查询。") from None
        task = raw.get("task")
        if not isinstance(task, dict):
            raise ConflictError("MiniMax H3 未返回有效任务。")
        task = cast(dict[str, object], task)
        statuses = {
            "queued": "queued",
            "running": "in_progress",
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }
        raw_status = task.get("status")
        status = statuses.get(raw_status) if isinstance(raw_status, str) else None
        if status is None:
            raise ConflictError("MiniMax H3 返回了未知的任务状态。")
        video_url = None
        if status == "completed":
            content = task.get("content")
            video_url = (
                cast(dict[str, object], content).get("url") if isinstance(content, dict) else None
            )
            if not isinstance(video_url, str):
                raise ConflictError("MiniMax H3 未返回视频链接。")
            parsed = urlsplit(video_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ConflictError("MiniMax H3 返回了无效的视频链接。")
        return VideoGenerationJob(
            jobId=job_id,
            status=cast(Any, status),
            progress=100 if status == "completed" else 0,
            videoUrl=video_url,
            cancellationSupported=False,
            error="MiniMax H3 未能完成视频生成。" if status == "failed" else None,
        )

    async def _get_minimax_video(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        job_id: str,
        task_id: str,
    ) -> VideoGenerationJob:
        if route.api_format == "minimax_videos_v2":
            return await self._get_minimax_h3_video(route, secret, job_id, task_id)
        try:
            raw = self._minimax_response(
                await self._get(
                    route, secret, f"query/video_generation?task_id={quote(task_id, safe='')}"
                )
            )
            raw_status = raw.get("status")
            statuses = {
                "Preparing": "queued",
                "Queueing": "queued",
                "Processing": "in_progress",
                "Success": "completed",
                "Fail": "failed",
                "Failed": "failed",
            }
            status = statuses.get(raw_status) if isinstance(raw_status, str) else None
            if status is None:
                raise ConflictError("MiniMax 返回了未知的视频任务状态。")
            video_url = None
            if status == "completed":
                file_id = raw.get("file_id")
                if not isinstance(file_id, str) or not file_id.isdigit():
                    raise ConflictError("MiniMax 未返回有效的视频文件 ID。")
                file = self._minimax_response(
                    await self._get(
                        route, secret, f"files/retrieve?file_id={quote(file_id, safe='')}"
                    )
                ).get("file")
                video_url = (
                    cast(dict[str, object], file).get("download_url")
                    if isinstance(file, dict)
                    else None
                )
                if not isinstance(video_url, str):
                    raise ConflictError("MiniMax 未返回视频链接。")
                parsed = urlsplit(video_url)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ConflictError("MiniMax 返回了无效的视频链接。")
            return VideoGenerationJob(
                jobId=job_id,
                status=cast(Any, status),
                progress=100 if status == "completed" else 0,
                videoUrl=video_url,
                cancellationSupported=False,
                error="MiniMax 未能完成视频生成。" if status == "failed" else None,
            )
        except httpx.HTTPError:
            raise ConflictError("MiniMax 视频状态暂时不可用，请稍后继续查询。") from None

    def _video_request_form(
        self,
        route: ModelRouteCapability,
        request: GenerateVideoRequest,
        references: tuple[GeneratedVideoReference, ...],
    ) -> tuple[dict[str, str], tuple[tuple[str, GeneratedVideoReference], ...]]:
        if len(references) != len(request.input_artifact_ids):
            raise ConflictError("video reference images could not be resolved")
        if any(not item.media_type.lower().startswith("image/") for item in references):
            raise ConflictError("video references must be image files")
        if sum(len(item.content) for item in references) > _MAX_VIDEO_REFERENCE_BYTES:
            raise ConflictError("video reference images exceed the 60 MiB total limit")
        for index, item in enumerate(references, start=1):
            if len(item.content) > _MAX_VIDEO_REFERENCE_IMAGE_BYTES:
                raise ConflictError(f"video reference image {index} exceeds the 30 MiB limit")
            try:
                with Image.open(BytesIO(item.content)) as image:
                    width, height = image.size
                    image.verify()
            except (OSError, UnidentifiedImageError, ValueError):
                raise ConflictError(
                    f"video reference image {index} is invalid or unsupported"
                ) from None
            if (
                min(width, height) < _MIN_VIDEO_REFERENCE_DIMENSION
                or max(width, height) > _MAX_VIDEO_REFERENCE_DIMENSION
            ):
                raise ConflictError(
                    f"video reference image {index} dimensions must be between 256 and 5760 pixels"
                )
            aspect_ratio = width / height
            if not (
                _MIN_VIDEO_REFERENCE_ASPECT_RATIO
                <= aspect_ratio
                <= _MAX_VIDEO_REFERENCE_ASPECT_RATIO
            ):
                raise ConflictError(
                    f"video reference image {index} aspect ratio must be between 1:4 and 4:1"
                )
        form: dict[str, str] = {
            "model": route.models[0],
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "seconds": str(request.seconds),
        }
        if request.seed is not None:
            form["seed"] = str(request.seed)
        if request.negative_prompt:
            form["negative_prompt"] = request.negative_prompt
        if request.mode == "ref2va":
            form["extra_params"] = json.dumps({"task": "ref2va"}, separators=(",", ":"))
        reference_files = tuple(
            (
                "input_reference" if len(references) == 1 else "input_references",
                item,
            )
            for item in references
        )
        return form, reference_files

    async def _video_job(
        self, tenant_id: str, user_id: str, route_id: str, job_id: str
    ) -> _ManagedVideoJob:
        job = self._video_jobs.get(job_id)
        if job is None and self._runs is not None and self._sessions is not None:
            run = await self._runs.get(tenant_id, job_id)
            session = await self._sessions.get(tenant_id, run.session_id)
            saved = cast(dict[str, Any] | None, run.input.get("video_generation"))
            if (
                session.user_id != user_id
                or not isinstance(saved, dict)
                or saved.get("routeId") != route_id
            ):
                raise NotFoundError(f"video generation job not found: {job_id}")
            provider_id = saved.get("providerJobId")
            if not isinstance(provider_id, str):
                raise NotFoundError(f"video generation job not found: {job_id}")
            job = _ManagedVideoJob(tenant_id, user_id, route_id, provider_id, run.created_at)
            self._video_jobs[job_id] = job
            if not run.status.is_terminal:
                self._monitor_video_task(job_id, job)
        if (
            job is None
            or job.tenant_id != tenant_id
            or job.user_id != user_id
            or job.route_id != route_id
        ):
            raise NotFoundError(f"video generation job not found: {job_id}")
        return job

    async def _record_video_task(
        self,
        view: VideoGenerationJob,
        route: ModelRouteCapability,
        request: GenerateVideoRequest,
        session_id: str | None,
    ) -> None:
        if session_id is None:
            return
        if self._runs is None:
            raise ConflictError("video task persistence is unavailable")
        job = self._video_jobs[view.job_id]
        now = datetime.now(UTC)
        await self._runs.add(
            Run(
                run_id=view.job_id,
                session_id=session_id,
                tenant_id=job.tenant_id,
                status=self._video_run_status(view),
                idempotency_key=view.job_id,
                created_at=now,
                updated_at=now,
                input={
                    "prompt": request.prompt,
                    "input_artifact_ids": list(request.input_artifact_ids),
                    "video_generation": {
                        "routeId": job.route_id,
                        "routeLabel": route.label,
                        "model": route.models[0],
                        "apiFormat": route.api_format,
                        "providerJobId": job.provider_job_id,
                        "request": request.model_dump(mode="json", by_alias=True, exclude={"task"}),
                        "job": view.model_dump(mode="json", by_alias=True),
                    },
                },
            )
        )
        if view.status in {"queued", "in_progress"}:
            self._monitor_video_task(view.job_id, job)

    @staticmethod
    def _video_run_status(view: VideoGenerationJob) -> RunStatus:
        return {
            "completed": RunStatus.SUCCEEDED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }.get(view.status, RunStatus.RUNNING)

    async def _update_video_task(self, tenant_id: str, view: VideoGenerationJob) -> None:
        if self._runs is None:
            return
        for _ in range(3):
            try:
                run = await self._runs.get(tenant_id, view.job_id)
            except NotFoundError:
                return
            saved = cast(dict[str, Any] | None, run.input.get("video_generation"))
            if not isinstance(saved, dict):
                return
            if run.status.is_terminal and view.status in {"queued", "in_progress"}:
                return
            payload = view.model_dump(mode="json", by_alias=True)
            if saved.get("job") == payload:
                return
            updated = run.model_copy(
                update={
                    "status": self._video_run_status(view),
                    "fencing_token": run.fencing_token + 1,
                    "updated_at": datetime.now(UTC),
                    "input": {**run.input, "video_generation": {**saved, "job": payload}},
                }
            )
            if await self._runs.compare_and_set(run.status, updated):
                return
        raise ConflictError("video task status changed concurrently; query again")

    def _monitor_video_task(self, job_id: str, job: _ManagedVideoJob) -> None:
        if job_id in self._video_monitors:
            return

        async def monitor() -> None:
            try:
                for _ in range(360):
                    await asyncio.sleep(5)
                    try:
                        view = await self.get_video_job(
                            job.tenant_id, job.user_id, job.route_id, job_id
                        )
                        if view.status not in {"queued", "in_progress"}:
                            return
                    except ConflictError:
                        continue
                    except NotFoundError:
                        return
            finally:
                self._video_monitors.pop(job_id, None)

        self._video_monitors[job_id] = asyncio.create_task(monitor())

    @staticmethod
    def _video_provider_response(response: httpx.Response) -> dict[str, object]:
        try:
            raw = cast(object, response.json())
        except ValueError:
            raise ConflictError("video provider returned an invalid response") from None
        if not isinstance(raw, dict):
            raise ConflictError("video provider returned an invalid response")
        return cast(dict[str, object], raw)

    @staticmethod
    def _video_job_view(job_id: str, raw: dict[str, object]) -> VideoGenerationJob:
        raw_status = raw.get("status")
        if raw_status not in {"queued", "in_progress", "completed", "failed"}:
            raise ConflictError("video provider returned an invalid job status")
        status = cast(Literal["queued", "in_progress", "completed", "failed"], raw_status)
        raw_progress = raw.get("progress", 0)
        progress = raw_progress if isinstance(raw_progress, int) else 0
        raw_error = raw.get("error")
        error_message: str | None = None
        if isinstance(raw_error, dict):
            message = cast(dict[object, object], raw_error).get("message")
            if isinstance(message, str):
                error_message = message
        raw_inference_time = raw.get("inference_time_s")
        inference_time = (
            float(raw_inference_time) if isinstance(raw_inference_time, int | float) else None
        )
        return VideoGenerationJob(
            jobId=job_id,
            status=status,
            progress=max(0, min(100, progress)),
            error=error_message,
            inferenceTimeSeconds=inference_time,
        )

    def _prune_video_jobs(self) -> None:
        if len(self._video_jobs) <= 512:
            return
        oldest = min(self._video_jobs, key=lambda key: self._video_jobs[key].created_at)
        self._video_jobs.pop(oldest, None)

    async def _route(self, tenant_id: str, route_id: str) -> ModelRouteCapability:
        record = await self._import_server_models(tenant_id)
        route = next(
            (item for item in record.catalog.model_routes if item.route_id == route_id), None
        )
        if route is None:
            raise NotFoundError(f"configured model not found: {route_id}")
        if route.base_url is None:
            raise NotFoundError(f"configured model not found: {route_id}")
        self._validate_endpoint(route.base_url)
        return route

    async def _store_secret(
        self, tenant_id: str, user_id: str, route_id: str, api_key: SecretStr
    ) -> None:
        now = datetime.now(UTC)
        await self._credentials.repository.upsert(
            StoredMcpCredential(
                tenant_id=tenant_id,
                owner_user_id=_MODEL_CREDENTIAL_OWNER,
                reference=route_id,
                revision=1,
                key_names=(_API_KEY,),
                ciphertext=self._credentials.cipher.encrypt(
                    tenant_id,
                    _MODEL_CREDENTIAL_OWNER,
                    route_id,
                    {_API_KEY: api_key},
                ),
                updated_by=user_id,
                updated_at=now,
            )
        )
        if self._credentials.audit is not None:
            await self._credentials.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.model_credential.configure",
                resource_type="model_credential",
                resource_id=route_id,
                details={"keys": [_API_KEY]},
            )

    async def _secret(self, tenant_id: str, route_id: str) -> SecretStr | None:
        stored = await self._credentials.repository.get(
            tenant_id, _MODEL_CREDENTIAL_OWNER, route_id
        )
        if stored is not None:
            return self._credentials.cipher.decrypt(stored).get(_API_KEY)
        return None

    async def _import_server_models(self, tenant_id: str) -> CapabilityCatalogRecord:
        """One-time import from deployment settings into the frontend control plane."""

        record = await self._catalogs.get(tenant_id)
        if not self._server_routes:
            return record
        stored = {
            item.reference
            for item in await self._credentials.repository.list_for_user(
                tenant_id, _MODEL_CREDENTIAL_OWNER
            )
        }
        changed = False
        imported_routes: list[ModelRouteCapability] = []
        for route in record.catalog.model_routes:
            server = self._server_routes.get(route.route_id)
            if server is None:
                imported_routes.append(route)
                continue
            if route.route_id not in stored:
                await self._store_secret(
                    tenant_id,
                    _MODEL_IMPORT_ACTOR,
                    route.route_id,
                    server.credential,
                )
            if route.base_url is None:
                route = route.model_copy(
                    update={
                        "models": (server.model,),
                        "capabilities": tuple(sorted(server.capabilities)),
                        "model_type": self._server_model_type(server),
                        "base_url": self._server_direct_base_url(server),
                        "api_format": "anthropic_compatible",
                        "auth_scheme": server.resolved_auth_scheme,
                        "version": route.version + 1,
                    }
                )
                changed = True
            imported_routes.append(route)
        if not changed:
            return record
        try:
            return await self._catalogs.replace(
                tenant_id=tenant_id,
                user_id=_MODEL_IMPORT_ACTOR,
                request=ReplaceCapabilityCatalogRequest(
                    expectedRevision=record.revision,
                    catalog=record.catalog.model_copy(
                        update={"model_routes": tuple(imported_routes)}
                    ),
                ),
            )
        except ConflictError:
            # Another API/worker process completed the same idempotent import.
            return await self._catalogs.get(tenant_id)

    @staticmethod
    def _server_model_type(
        route: CcSwitchClaudeConfig,
    ) -> Literal["chat", "vision", "image_generation", "video_generation"]:
        return "vision" if "vision" in route.capabilities else "chat"

    @staticmethod
    def _server_direct_base_url(
        route: CcSwitchClaudeConfig,
    ) -> str:
        base_url = route.base_url.rstrip("/")
        # Deployment settings are SDK base URLs; Anthropic-compatible SDKs
        # append /v1/messages themselves. The control-plane probe uses raw
        # HTTP, so expose the complete API base before appending /messages.
        if not base_url.endswith("/v1"):
            return f"{base_url}/v1"
        return base_url

    @staticmethod
    def _runtime_sdk_base_url(route: ModelRouteCapability) -> str:
        assert route.base_url is not None
        base_url = route.base_url.rstrip("/")
        # The model catalog stores the complete direct API base so connection
        # tests can call /messages. Claude Code expects the provider root and
        # appends /v1/messages itself. Passing the catalog URL through unchanged
        # therefore produces /v1/v1/messages and a misleading model-not-found
        # 404 for Anthropic-compatible routes.
        if route.api_format == "anthropic_compatible" and base_url.endswith("/v1"):
            return base_url[:-3]
        return base_url

    async def _require_secret(self, tenant_id: str, route_id: str) -> SecretStr:
        secret = await self._secret(tenant_id, route_id)
        if secret is None:
            raise ConflictError(f"credentials are not configured for model: {route_id}")
        return secret

    async def _credential_for_route(
        self, tenant_id: str, route: ModelRouteCapability
    ) -> SecretStr | None:
        if route.auth_scheme == "none":
            return None
        return await self._require_secret(tenant_id, route.route_id)

    @staticmethod
    def _headers(route: ModelRouteCapability, secret: SecretStr | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if route.api_format == "anthropic_compatible":
            headers["anthropic-version"] = "2023-06-01"
        if route.auth_scheme == "x-api-key":
            if secret is None:
                raise ConflictError(f"credentials are not configured for model: {route.route_id}")
            headers["x-api-key"] = secret.get_secret_value()
        elif route.auth_scheme == "bearer":
            if secret is None:
                raise ConflictError(f"credentials are not configured for model: {route.route_id}")
            headers["authorization"] = f"Bearer {secret.get_secret_value()}"
        return headers

    async def _post(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        path: str,
        payload: dict[str, object],
    ) -> httpx.Response:
        assert route.base_url is not None
        base_url = route.base_url.rstrip("/")
        if (
            route.api_format == "anthropic_compatible"
            and path == "messages"
            and not base_url.endswith("/v1")
        ):
            base_url += "/v1"
        headers = {"content-type": "application/json", **self._headers(route, secret)}
        client = self._http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=False)
        try:
            return await client.post(
                f"{base_url}/{path}",
                headers=headers,
                json=payload,
            )
        finally:
            if self._http_client is None:
                await client.aclose()

    async def _get(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        path: str,
        *,
        timeout: float = 15.0,
    ) -> httpx.Response:
        assert route.base_url is not None
        client = self._http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        try:
            return await client.get(
                f"{route.base_url.rstrip('/')}/{path}",
                headers=self._headers(route, secret),
            )
        finally:
            if self._http_client is None:
                await client.aclose()

    async def _delete(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        path: str,
    ) -> httpx.Response:
        assert route.base_url is not None
        client = self._http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=False)
        try:
            return await client.delete(
                f"{route.base_url.rstrip('/')}/{path}",
                headers=self._headers(route, secret),
            )
        finally:
            if self._http_client is None:
                await client.aclose()

    async def _post_multipart(
        self,
        route: ModelRouteCapability,
        secret: SecretStr | None,
        path: str,
        form: dict[str, str],
        reference_files: tuple[tuple[str, GeneratedVideoReference], ...] = (),
    ) -> httpx.Response:
        assert route.base_url is not None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(900.0, connect=15.0),
            follow_redirects=False,
        )
        try:
            files: list[tuple[str, tuple[str | None, str | bytes, str | None]]] = [
                (name, (None, value, None)) for name, value in form.items()
            ]
            files.extend(
                (
                    field,
                    (
                        reference.name.replace("\r", "_").replace("\n", "_")[:120]
                        or "reference-image",
                        reference.content,
                        reference.media_type,
                    ),
                )
                for field, reference in reference_files
            )
            return await client.post(
                f"{route.base_url.rstrip('/')}/{path}",
                headers=self._headers(route, secret),
                files=files,
            )
        finally:
            if self._http_client is None:
                await client.aclose()

    def _validate_endpoint(self, value: str) -> None:
        normalized = value.rstrip("/")
        if normalized in {
            self._server_direct_base_url(route) for route in self._server_routes.values()
        }:
            return
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            raise ConflictError("model Base URL must use HTTP or HTTPS")
        if parsed.hostname is None:
            raise ConflictError("model Base URL must include a hostname")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if self._environment == "production" and parsed.scheme != "https":
                raise ConflictError("production public model connections require HTTPS") from None
            return
        if self._environment != "production":
            return
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            raise ConflictError(
                "production model connections cannot target loopback or link-local addresses"
            )
        # Model catalog administration is permission-gated and is also the source
        # of truth for on-premise providers. RFC1918 endpoints therefore need to
        # remain configurable without a legacy deployment-environment exception.
        if address.is_private:
            return
        if parsed.scheme != "https":
            raise ConflictError("production public model connections require HTTPS")
