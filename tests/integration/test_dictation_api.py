import io
import wave

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.config import Settings
from harness.dictation.service import DictationService, DictationSettings

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


def audio(seconds: int = 1) -> bytes:
    result = io.BytesIO()
    with wave.open(result, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(bytes(32_000 * seconds))
    return result.getvalue()


@pytest.mark.asyncio
async def test_feature_is_disabled_until_configured() -> None:
    app = create_memory_app()
    app.state.dictation = DictationService(DictationSettings(enabled=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        caps = await client.get("/v1/dictation/capabilities", headers=HEADERS)
        assert caps.status_code == 200
        assert caps.json()["enabled"] is False
        response = await client.post(
            "/v1/dictation/transcribe",
            headers=HEADERS,
            files={"file": ("audio.wav", audio(), "audio/wav")},
        )
        assert response.status_code == 503


@pytest.mark.asyncio
async def test_audio_upload_and_text_refinement_are_separate_and_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_memory_app()
    audio_calls: list[str] = []
    text_calls: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        audio_calls.append(request.url.path)
        return httpx.Response(200, json={"text": "那个今天开会"})

    app.state.dictation = DictationService(
        DictationSettings(enabled=True), transport=httpx.MockTransport(respond)
    )

    async def complete(tenant_id: str, route: str, **_kwargs: object) -> str:
        text_calls.append((tenant_id, route))
        return "今天开会。"

    monkeypatch.setattr(app.state.container.model_configurations, "complete_text", complete)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/dictation/transcribe", headers=HEADERS,
            files={"file": ("audio.wav", audio(), "audio/wav")},
        )
        assert response.status_code == 200
        refined = await client.post(
            "/v1/dictation/refine", headers=HEADERS,
            json={"draft": response.json()["text"], "model_route": "my-text-model"},
        )
        assert refined.status_code == 200
        assert refined.json() == {
            "text": "今天开会。", "draft": "那个今天开会", "status": "refined",
        }
        assert audio_calls == ["/v1/audio/transcriptions"]
        assert text_calls == [("tenant-a", "my-text-model")]


@pytest.mark.asyncio
async def test_requests_require_authentication_when_service_token_is_configured() -> None:
    app = create_memory_app(settings=Settings(api_bearer_token="test-auth-token"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/dictation/capabilities")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_whole_recording_limit_and_batch_capability() -> None:
    app = create_memory_app()
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"text": "整段语音"})

    app.state.dictation = DictationService(
        DictationSettings(enabled=True, max_session_seconds=120),
        transport=httpx.MockTransport(respond),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        caps = await client.get("/v1/dictation/capabilities", headers=HEADERS)
        assert caps.json()["interaction"] == "record_then_transcribe"
        for seconds, expected_status in [(21, 200), (120, 200), (121, 413)]:
            response = await client.post(
                "/v1/dictation/transcribe", headers=HEADERS,
                files={"file": ("audio.wav", audio(seconds), "audio/wav")},
            )
            assert response.status_code == expected_status
    assert calls == ["/v1/audio/transcriptions"] * 2
