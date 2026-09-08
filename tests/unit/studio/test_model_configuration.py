import json
from datetime import UTC, datetime
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from harness.core.errors import ConflictError
from harness.core.models import ModelCompatibility
from harness.runtime.cc_switch import CcSwitchClaudeConfig
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.mcp_credential_store import (
    InMemoryMcpCredentialRepository,
    McpCredentialCipher,
    McpCredentialService,
)
from harness.studio.model_configuration import (
    BindAgentModelRequest,
    ConfigureModelRequest,
    GeneratedVideoReference,
    GenerateImageRequest,
    GenerateVideoRequest,
    ModelConfigurationService,
)
from harness.studio.repositories import InMemoryAgentDraftRepository


def image_bytes(*, size: tuple[int, int] = (256, 256), format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color="white").save(output, format=format)
    return output.getvalue()


def service(
    *,
    environment: str = "test",
    client: httpx.AsyncClient | None = None,
    server_routes: tuple[CcSwitchClaudeConfig, ...] = (),
) -> tuple[ModelConfigurationService, CapabilityCatalogService, InMemoryMcpCredentialRepository]:
    catalogs = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
        clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
    )
    credentials = InMemoryMcpCredentialRepository()
    credential_service = McpCredentialService(
        credentials,
        McpCredentialCipher(SecretStr("test-encryption-key")),
    )
    return (
        ModelConfigurationService(
            catalogs,
            credential_service,
            environment=environment,
            server_routes=server_routes,
            http_client=client,
        ),
        catalogs,
        credentials,
    )


def request(**updates: object) -> ConfigureModelRequest:
    values: dict[str, object] = {
        "expectedRevision": 1,
        "label": "视觉主模型",
        "modelType": "vision",
        "provider": "Example AI",
        "model": "example-vision-1",
        "baseUrl": "https://models.example.test/v1",
        "apiFormat": "openai_compatible",
        "authScheme": "bearer",
        "apiKey": "secret-value-never-returned",
        "enabled": True,
    }
    values.update(updates)
    return ConfigureModelRequest.model_validate(values)


@pytest.mark.asyncio
async def test_configure_encrypts_key_and_redacts_runtime_catalog() -> None:
    models, catalogs, credentials = service()

    configured = await models.configure("tenant-a", "admin-a", "vision-primary", request())

    view = next(item for item in configured.models if item.route_id == "vision-primary")
    assert view.credential_configured is True
    assert "secret-value-never-returned" not in view.model_dump_json()
    stored = await credentials.get("tenant-a", "tenant:model-control-plane", "vision-primary")
    assert stored is not None
    assert "secret-value-never-returned" not in stored.ciphertext
    runtime_catalog = await catalogs.get_for_user("tenant-a", "member-a")
    route = next(
        item for item in runtime_catalog.catalog.model_routes if item.route_id == "vision-primary"
    )
    assert route.base_url is None
    assert route.model_type == "vision"


@pytest.mark.asyncio
async def test_agent_binding_changes_runtime_route_without_manifest_edit() -> None:
    models, _catalogs, _credentials = service()
    configured = await models.configure("tenant-a", "admin-a", "vision-primary", request())
    bound = await models.bind_agent(
        "tenant-a",
        "admin-a",
        "helper-agent",
        BindAgentModelRequest(
            expectedRevision=configured.revision,
            routeId="vision-primary",
        ),
    )

    resolved = await models.resolve_runtime("tenant-a", "helper-agent", "manifest-route")

    assert bound.agent_model_bindings == {"helper-agent": "vision-primary"}
    assert resolved is not None
    assert resolved.route_id == "vision-primary"
    assert resolved.model == "example-vision-1"
    assert resolved.base_url == "https://models.example.test/v1"
    assert resolved.credential.get_secret_value() == "secret-value-never-returned"


@pytest.mark.asyncio
async def test_text_completion_uses_the_managed_openai_route() -> None:
    captured: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "精炼后的任务标题"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _catalogs, _credentials = service(client=client)
        await models.configure("tenant-a", "admin-a", "title-flash", request())
        text = await models.complete_text(
            "tenant-a",
            "title-flash",
            system_prompt="只输出标题",
            user_prompt="生成报告",
            max_tokens=48,
        )

    assert text == "精炼后的任务标题"
    assert captured[0].url == "https://models.example.test/v1/chat/completions"
    assert captured[0].headers["authorization"] == "Bearer secret-value-never-returned"
    assert json.loads(captured[0].content)["messages"][0] == {
        "role": "system",
        "content": "只输出标题",
    }


@pytest.mark.asyncio
async def test_image_generation_uses_dedicated_endpoint_and_never_chat_route() -> None:
    captured: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        return httpx.Response(
            200,
            json={"data": [{"url": "https://cdn.example.test/image.png"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _catalogs, _credentials = service(client=client)
        configured = await models.configure(
            "tenant-a",
            "admin-a",
            "image-primary",
            request(
                label="图像生成",
                modelType="image_generation",
                model="image-1",
                apiFormat="openai_images",
            ),
        )
        result = await models.generate_image(
            "tenant-a",
            "image-primary",
            GenerateImageRequest(prompt="A safe product illustration"),
        )

    image = next(item for item in configured.models if item.route_id == "image-primary")
    assert image.capabilities == ("image_generation",)
    assert image.deletable is True
    assert result.images[0].url == "https://cdn.example.test/image.png"
    assert captured[0].url.path.endswith("/v1/images/generations")
    assert captured[0].headers["authorization"] == "Bearer secret-value-never-returned"


@pytest.mark.asyncio
async def test_video_generation_supports_private_unauthenticated_async_lifecycle() -> None:
    captured: list[httpx.Request] = []
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"video-bytes"
    first_image = image_bytes()
    second_image = image_bytes(format="JPEG")

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        if incoming.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": []})
        if incoming.method == "POST":
            return httpx.Response(
                200,
                json={"id": "provider-video-1", "status": "queued", "progress": 0},
            )
        if incoming.url.path.endswith("/content"):
            return httpx.Response(
                200,
                content=mp4,
                headers={"content-type": "video/mp4", "x-request-id": "video-request-1"},
            )
        if incoming.method == "DELETE":
            return httpx.Response(
                200,
                json={"id": "provider-video-1", "deleted": True},
            )
        return httpx.Response(
            200,
            json={
                "id": "provider-video-1",
                "status": "completed",
                "progress": 100,
                "inference_time_s": 128.4,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _catalogs, credentials = service(environment="production", client=client)
        configured = await models.configure(
            "tenant-a",
            "admin-a",
            "minimax-h3-video",
            request(
                label="MiniMax H3 视频",
                modelType="video_generation",
                model="/model",
                baseUrl="http://172.20.109.229:18000/v1",
                apiFormat="openai_videos",
                authScheme="none",
                apiKey=None,
            ),
        )
        connection = await models.test("tenant-a", "minimax-h3-video")
        created = await models.create_video_job(
            "tenant-a",
            "user-a",
            "minimax-h3-video",
            GenerateVideoRequest(
                prompt="一只猫在草地上奔跑",
                mode="ref2va",
                aspectRatio="16:9",
                seconds=5,
                seed=7,
                negativePrompt="画面抖动、文字水印",
                inputArtifactIds=("input_artifact_a", "input_artifact_b"),
            ),
            references=(
                GeneratedVideoReference(
                    name="first.png", media_type="image/png", content=first_image
                ),
                GeneratedVideoReference(
                    name="second.jpg", media_type="image/jpeg", content=second_image
                ),
            ),
        )
        status_result = await models.get_video_job(
            "tenant-a", "user-a", "minimax-h3-video", created.job_id
        )
        result = await models.download_video(
            "tenant-a", "user-a", "minimax-h3-video", created.job_id
        )
        cancelled = await models.cancel_video_job(
            "tenant-a", "user-a", "minimax-h3-video", created.job_id
        )

    video = next(item for item in configured.models if item.route_id == "minimax-h3-video")
    assert video.capabilities == ("video_generation",)
    assert video.credential_configured is True
    assert (
        await credentials.get("tenant-a", "tenant:model-control-plane", "minimax-h3-video") is None
    )
    assert connection.ok is True
    assert captured[0].method == "GET"
    assert captured[0].url == "http://172.20.109.229:18000/v1/models"
    assert "authorization" not in captured[0].headers
    assert captured[1].url == "http://172.20.109.229:18000/v1/videos"
    assert b'name="prompt"' in captured[1].content
    assert b'name="aspect_ratio"' in captured[1].content
    assert b'name="seconds"' in captured[1].content
    assert b'name="seed"' in captured[1].content
    assert b'name="negative_prompt"' in captured[1].content
    assert b'name="extra_params"' in captured[1].content
    assert b'{"task":"ref2va"}' in captured[1].content
    assert "画面抖动、文字水印".encode() in captured[1].content
    assert captured[1].content.count(b'name="input_references"') == 2
    assert b'filename="first.png"' in captured[1].content
    assert b'filename="second.jpg"' in captured[1].content
    assert first_image in captured[1].content
    assert second_image in captured[1].content
    assert created.status == "queued"
    assert status_result.status == "completed"
    assert status_result.progress == 100
    assert status_result.inference_time_seconds == 128.4
    assert result.content == mp4
    assert result.request_id == "video-request-1"
    assert cancelled.status == "cancelled"
    assert captured[2].url.path == "/v1/videos/provider-video-1"
    assert captured[3].url.path == "/v1/videos/provider-video-1/content"
    assert captured[4].method == "DELETE"
    assert await models.resolve_runtime("tenant-a", "helper-agent", "minimax-h3-video") is None


@pytest.mark.asyncio
async def test_video_generation_uses_single_reference_field_for_one_image() -> None:
    captured: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        return httpx.Response(
            200,
            json={"id": "provider-video-1", "status": "queued", "progress": 0},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _catalogs, _credentials = service(environment="production", client=client)
        await models.configure(
            "tenant-a",
            "admin-a",
            "minimax-h3-video",
            request(
                modelType="video_generation",
                model="/model",
                baseUrl="http://172.20.109.229:18000/v1",
                apiFormat="openai_videos",
                authScheme="none",
                apiKey=None,
            ),
        )
        await models.create_video_job(
            "tenant-a",
            "user-a",
            "minimax-h3-video",
            GenerateVideoRequest(
                prompt="让图片动起来",
                mode="ref2va",
                inputArtifactIds=("input_artifact_a",),
            ),
            references=(
                GeneratedVideoReference(
                    name="reference.webp",
                    media_type="image/webp",
                    content=image_bytes(format="WEBP"),
                ),
            ),
        )

    assert b'name="input_reference"' in captured[0].content
    assert b'name="input_references"' not in captured[0].content
    assert b'{"task":"ref2va"}' in captured[0].content


def test_ref2va_requires_an_image_and_auto_mode_keeps_two_image_limit() -> None:
    with pytest.raises(ValueError, match="Ref2VA requires at least one reference image"):
        GenerateVideoRequest(prompt="缺少参考图", mode="ref2va")

    with pytest.raises(ValueError, match="at most two reference images"):
        GenerateVideoRequest(
            prompt="自动模式图片过多",
            inputArtifactIds=(
                "input_artifact_a",
                "input_artifact_b",
                "input_artifact_c",
            ),
        )

    request = GenerateVideoRequest(
        prompt="九张参考图",
        mode="ref2va",
        inputArtifactIds=tuple(f"input_artifact_{index}" for index in range(9)),
    )
    assert len(request.input_artifact_ids) == 9


@pytest.mark.asyncio
async def test_video_generation_rejects_extreme_reference_aspect_ratio() -> None:
    models, _catalogs, _credentials = service(environment="production")
    await models.configure(
        "tenant-a",
        "admin-a",
        "minimax-h3-video",
        request(
            modelType="video_generation",
            model="/model",
            baseUrl="http://172.20.109.229:18000/v1",
            apiFormat="openai_videos",
            authScheme="none",
            apiKey=None,
        ),
    )

    with pytest.raises(ConflictError, match="aspect ratio must be between 1:4 and 4:1"):
        await models.create_video_job(
            "tenant-a",
            "user-a",
            "minimax-h3-video",
            GenerateVideoRequest(
                prompt="让长图动起来",
                inputArtifactIds=("input_artifact_a",),
            ),
            references=(
                GeneratedVideoReference(
                    name="extreme.png",
                    media_type="image/png",
                    content=image_bytes(size=(1280, 256)),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_unauthenticated_video_route_rejects_api_key_storage() -> None:
    models, _catalogs, _credentials = service(environment="production")

    with pytest.raises(ConflictError, match="cannot be stored"):
        await models.configure(
            "tenant-a",
            "admin-a",
            "minimax-h3-video",
            request(
                modelType="video_generation",
                model="/model",
                baseUrl="http://172.20.109.229:18000/v1",
                apiFormat="openai_videos",
                authScheme="none",
            ),
        )


@pytest.mark.asyncio
async def test_delete_permanently_removes_workspace_model_and_credential() -> None:
    models, _catalogs, credentials = service()
    configured = await models.configure("tenant-a", "admin-a", "minimax-h3", request())

    deleted = await models.delete("tenant-a", "admin-a", "minimax-h3", configured.revision)

    assert "minimax-h3" not in {item.route_id for item in deleted.models}
    assert await credentials.get("tenant-a", "tenant:model-control-plane", "minimax-h3") is None


@pytest.mark.asyncio
async def test_delete_of_platform_model_route_persists() -> None:
    models, _catalogs, _credentials = service()

    deleted = await models.delete("tenant-a", "admin-a", "deepseek-v4-flash", 1)
    repeated = await models.list("tenant-a")

    assert "deepseek-v4-flash" not in {item.route_id for item in deleted.models}
    assert repeated == deleted


@pytest.mark.asyncio
async def test_delete_rejects_model_still_bound_to_an_agent() -> None:
    models, _catalogs, _credentials = service()
    configured = await models.configure("tenant-a", "admin-a", "minimax-h3", request())
    bound = await models.bind_agent(
        "tenant-a",
        "admin-a",
        "helper-agent",
        BindAgentModelRequest(
            expectedRevision=configured.revision,
            routeId="minimax-h3",
        ),
    )

    with pytest.raises(ConflictError, match="agent:helper-agent"):
        await models.delete("tenant-a", "admin-a", "minimax-h3", bound.revision)


@pytest.mark.asyncio
async def test_production_allows_private_models_but_rejects_unsafe_endpoints() -> None:
    models, _catalogs, _credentials = service(environment="production")

    configured = await models.configure(
        "tenant-a",
        "admin-a",
        "private-model",
        request(baseUrl="http://172.20.109.112:31300/v1"),
    )
    assert (
        next(item for item in configured.models if item.route_id == "private-model").base_url
        == "http://172.20.109.112:31300/v1"
    )

    with pytest.raises(ConflictError, match="require HTTPS"):
        await models.configure(
            "tenant-a",
            "admin-a",
            "public-http-model",
            request(
                expectedRevision=configured.revision,
                baseUrl="http://8.8.8.8/v1",
            ),
        )

    with pytest.raises(ConflictError, match="loopback or link-local"):
        await models.configure(
            "tenant-a",
            "admin-a",
            "loopback-model",
            request(
                expectedRevision=configured.revision,
                baseUrl="https://127.0.0.1/v1",
            ),
        )


@pytest.mark.asyncio
async def test_server_route_is_imported_testable_and_does_not_expose_secret() -> None:
    captured: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})

    route = CcSwitchClaudeConfig(
        route_id="minimax-m3",
        base_url="https://api.minimaxi.com/anthropic",
        model="MiniMax-M3",
        provider="anthropic",
        credential=SecretStr("server-only-secret"),
        auth_scheme="x-api-key",
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use", "vision"}),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _catalogs, credentials = service(
            environment="production", client=client, server_routes=(route,)
        )
        listed = await models.list("tenant-a")
        result = await models.test("tenant-a", "minimax-m3")

    view = next(item for item in listed.models if item.route_id == "minimax-m3")
    assert view.base_url == "https://api.minimaxi.com/anthropic/v1"
    assert view.model_type == "vision"
    assert view.credential_configured is True
    assert view.deletable is True
    assert "server-only-secret" not in listed.model_dump_json()
    stored = await credentials.get("tenant-a", "tenant:model-control-plane", "minimax-m3")
    assert stored is not None
    assert result.ok is True
    assert captured[0].url == "https://api.minimaxi.com/anthropic/v1/messages"
    assert captured[0].headers["x-api-key"] == "server-only-secret"
    assert captured[0].headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_trusted_server_route_may_use_private_http_but_workspace_override_cannot() -> None:
    route = CcSwitchClaudeConfig(
        route_id="glm-5-3-flash",
        base_url="http://172.20.109.174:4000",
        model="glm-5.3-flash",
        provider="new-api",
        credential=SecretStr("server-only-secret"),
        auth_scheme="bearer",
    )
    models, _catalogs, _credentials = service(environment="production", server_routes=(route,))

    resolved = await models.resolve_runtime("tenant-a", "helper-agent", "glm-5-3-flash")

    assert resolved is not None
    assert resolved.base_url == "http://172.20.109.174:4000"


@pytest.mark.asyncio
async def test_imported_server_model_runs_without_server_fallback_after_import() -> None:
    route = CcSwitchClaudeConfig(
        route_id="minimax-m3",
        base_url="https://api.minimaxi.com/anthropic",
        model="MiniMax-M3",
        provider="anthropic",
        credential=SecretStr("server-only-secret"),
        auth_scheme="x-api-key",
        capabilities=frozenset({"streaming", "tool_use", "vision"}),
    )
    models, catalogs, credentials = service(server_routes=(route,))
    imported = await models.list("tenant-a")
    database_only = ModelConfigurationService(
        catalogs,
        McpCredentialService(
            credentials,
            McpCredentialCipher(SecretStr("test-encryption-key")),
        ),
    )

    listed = await database_only.list("tenant-a")
    resolved = await database_only.resolve_runtime("tenant-a", "helper-agent", "minimax-m3")

    view = next(item for item in listed.models if item.route_id == "minimax-m3")
    assert listed == imported
    assert view.base_url == "https://api.minimaxi.com/anthropic/v1"
    assert view.model == "MiniMax-M3"
    assert resolved is not None
    assert resolved.base_url == "https://api.minimaxi.com/anthropic"
    assert resolved.credential.get_secret_value() == "server-only-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format", ["openai_compatible", "anthropic_compatible"])
async def test_authoring_completion_forwards_image_bytes(api_format: str) -> None:
    captured: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        return httpx.Response(200, json={"choices": [{"message": {"content": "red"}}],
                                        "content": [{"type": "text", "text": "red"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _, _ = service(client=client)
        await models.configure("tenant-a", "admin-a", "vision", request(apiFormat=api_format))
        text = await models.complete_text(
            "tenant-a", "vision", system_prompt="describe", user_prompt="color?",
            images=(("image/png", image_bytes()),),
        )
    assert text == "red"
    content = json.loads(captured[0].content)["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "color?"}
    if api_format == "openai_compatible":
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    else:
        assert content[1]["source"]["media_type"] == "image/png"
        assert content[1]["source"]["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["https://models.example.test/api/anthropic", "https://models.example.test/api/anthropic/v1"])
async def test_managed_anthropic_sdk_base_uses_one_v1_segment(base_url: str) -> None:
    calls: list[httpx.Request] = []
    def handler(incoming: httpx.Request) -> httpx.Response:
        calls.append(incoming)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "red square"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models, _, _ = service(client=client)
        await models.configure("tenant-a", "admin-a", "vision", request(
            apiFormat="anthropic_compatible", baseUrl=base_url))
        assert await models.complete_text("tenant-a", "vision", system_prompt="describe",
            user_prompt="color?", images=(("image/png", image_bytes()),)) == "red square"
    assert str(calls[0].url) == "https://models.example.test/api/anthropic/v1/messages"
