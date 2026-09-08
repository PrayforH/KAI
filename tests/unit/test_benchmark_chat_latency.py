# pyright: reportPrivateUsage=false

from pathlib import Path

import httpx
import pytest

from scripts.benchmark_chat_latency import (
    LatencySample,
    _headers,
    _nearest_rank,
    measure_once,
    summarize,
)


def _sample(run: int, value: float) -> LatencySample:
    return LatencySample(
        run=run,
        harness_run_id=f"run-{run}",
        response_headers_ms=value,
        first_event_ms=value + 1,
        run_started_ms=value + 2,
        first_text_ms=value + 3,
        total_ms=value + 4,
        event_count=5,
        text_characters=2,
    )


def test_nearest_rank_uses_observed_values() -> None:
    values = [50.0, 10.0, 40.0, 20.0, 30.0]

    assert _nearest_rank(values, 0.50) == 30.0
    assert _nearest_rank(values, 0.95) == 50.0
    assert _nearest_rank(values, 0.99) == 50.0


def test_summarize_reports_each_latency_milestone() -> None:
    result = summarize([_sample(1, 10), _sample(2, 20), _sample(3, 30)])

    assert result["response_headers_ms"] == {
        "min": 10.0,
        "p50": 20.0,
        "p95": 30.0,
        "p99": 30.0,
        "max": 30.0,
        "mean": 20.0,
    }
    assert result["first_text_ms"]["p50"] == 23.0
    assert result["total_ms"]["p50"] == 24.0


def test_headers_use_explicit_release_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_API_BEARER_TOKEN", "release-token")

    headers = _headers(Path("not-read.env"), tenant_id="release-tenant", user_id="smoke-user")

    assert headers == {
        "X-Tenant-ID": "release-tenant",
        "X-User-ID": "smoke-user",
        "Authorization": "Bearer release-token",
    }


def test_measure_once_requires_successful_terminal_and_acceptance_marker() -> None:
    body = "\n".join(
        (
            'data: {"type":"RUN_STARTED"}',
            'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"RELEASE_"}',
            'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"GATE_OK"}',
            'data: {"type":"RUN_FINISHED"}',
            "",
        )
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"X-Harness-Run-ID": "run-server"},
            text=body,
            request=request,
        )
    )
    with httpx.Client(base_url="http://test", transport=transport) as client:
        sample = measure_once(
            client,
            run_number=1,
            agent_name="echo-agent",
            agent_version="0.4.1",
            thread_id="thread-release",
            prompt="return the marker",
            timeout=10,
            required_text="RELEASE_GATE_OK",
        )

    assert sample.harness_run_id == "run-server"
    assert sample.text_characters == len("RELEASE_GATE_OK")


def test_measure_once_fails_closed_when_marker_is_missing() -> None:
    body = "\n".join(
        (
            'data: {"type":"RUN_STARTED"}',
            'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"wrong"}',
            'data: {"type":"RUN_FINISHED"}',
            "",
        )
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body, request=request))
    with httpx.Client(base_url="http://test", transport=transport) as client:
        with pytest.raises(RuntimeError, match="acceptance marker"):
            measure_once(
                client,
                run_number=1,
                agent_name="echo-agent",
                agent_version="0.4.1",
                thread_id="thread-release",
                prompt="return the marker",
                timeout=10,
                required_text="RELEASE_GATE_OK",
            )
