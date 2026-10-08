import io
import json
import wave

import httpx
import pytest

from harness.dictation.service import (
    DictationError,
    DictationService,
    DictationSettings,
    refinement_preserves_facts,
    validate_wav,
)


def wav_audio(seconds: int = 1, rate: int = 16_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(bytes(rate * seconds * 2))
    return output.getvalue()


def test_reject_wrong_sample_rate_truncated_or_oversized_audio() -> None:
    assert validate_wav(wav_audio(), 20) == 1
    for audio in (wav_audio(rate=44_100), wav_audio()[:-10], b"not audio", wav_audio(2)):
        with pytest.raises(DictationError):
            validate_wav(audio, 1)


@pytest.mark.asyncio
async def test_vad_endpoint_uses_trailing_silence_and_asr_uses_single_engine() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert b'dictation.wav' in request.content
        if request.url.path == "/vad":
            return httpx.Response(200, json={"segments": [{"start": 0.1, "end": 0.3}]})
        assert request.url.host == "sensevoice"
        assert b'auto' in request.content
        return httpx.Response(200, json={"text": "<|zh|>请不要修改金额 1200 元。"})

    service = DictationService(
        DictationSettings(enabled=True, vad_url="http://vad", sensevoice_url="http://sensevoice"),
        transport=httpx.MockTransport(respond),
    )
    result = await service.detect(wav_audio())
    assert result.speech and result.endpoint
    assert await service.transcribe(wav_audio()) == "请不要修改金额 1200 元。"
    assert calls == ["http://vad/vad", "http://sensevoice/v1/audio/transcriptions"]


@pytest.mark.asyncio
async def test_qwen_engine_and_silence_or_invalid_vad_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/vad":
            return httpx.Response(200, json={"segments": []})
        assert request.url.host == "qwen"
        assert b'Qwen3-ASR-1.7B' in request.content
        return httpx.Response(200, json={"text": "香港航空的航点"})

    service = DictationService(
        DictationSettings(engine="qwen3", qwen3_url="http://qwen", vad_url="http://vad"),
        transport=httpx.MockTransport(respond),
    )
    result = await service.detect(wav_audio())
    assert not result.speech and not result.endpoint
    assert await service.transcribe(wav_audio()) == "香港航空的航点"


@pytest.mark.asyncio
async def test_refinement_sends_only_text_and_never_retranscribes() -> None:
    def forbid_audio(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Refinement must not call VAD or ASR")

    service = DictationService(DictationSettings(), transport=httpx.MockTransport(forbid_audio))

    async def complete(_system: str, user: str) -> str:
        assert json.loads(user) == {"draft": "那个请不要修改金额 1200 元"}
        return "请不要修改金额 1200 元。"

    result = await service.refine("那个请不要修改金额 1200 元", complete)
    assert result.status == "refined"
    assert result.draft == "那个请不要修改金额 1200 元"
    assert result.text == "请不要修改金额 1200 元。"


@pytest.mark.asyncio
async def test_failed_or_destructive_refinement_returns_original() -> None:
    service = DictationService(DictationSettings())
    draft = "请不要修改金额 1200 元"

    async def broken(_system: str, _user: str) -> str:
        raise httpx.ConnectError("unavailable")

    async def destructive(_system: str, _user: str) -> str:
        return "请修改金额 2000 元。"

    for complete in (broken, destructive):
        result = await service.refine(draft, complete)
        assert result.text == draft and result.status == "fallback"
    assert not refinement_preserves_facts(draft, "请修改金额 1200 元。")


@pytest.mark.asyncio
async def test_upstream_errors_do_not_expose_service_details() -> None:
    service = DictationService(
        DictationSettings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="private diagnostics")),
    )
    with pytest.raises(DictationError, match="语音服务暂时不可用"):
        await service.transcribe(wav_audio())
