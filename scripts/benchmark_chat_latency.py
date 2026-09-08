"""Measure black-box AG-UI chat latency against a running Harness API."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from harness.agent_package import pack_agent_package

ROOT = Path(__file__).parents[1]
DEFAULT_ENV = ROOT / "deploy/docker-compose/.env.docker"
TERMINAL_TYPES = {"RUN_FINISHED", "RUN_ERROR"}


@dataclass(frozen=True)
class LatencySample:
    run: int
    harness_run_id: str
    response_headers_ms: float
    first_event_ms: float
    run_started_ms: float
    first_text_ms: float
    total_ms: float
    event_count: int
    text_characters: int


def _nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(samples: list[LatencySample]) -> dict[str, dict[str, float]]:
    """Summarize latency columns with stable nearest-rank percentiles."""

    if not samples:
        raise ValueError("at least one sample is required")
    fields = (
        "response_headers_ms",
        "first_event_ms",
        "run_started_ms",
        "first_text_ms",
        "total_ms",
    )
    result: dict[str, dict[str, float]] = {}
    for field in fields:
        values = [float(getattr(sample, field)) for sample in samples]
        result[field] = {
            "min": min(values),
            "p50": _nearest_rank(values, 0.50),
            "p95": _nearest_rank(values, 0.95),
            "p99": _nearest_rank(values, 0.99),
            "max": max(values),
            "mean": sum(values) / len(values),
        }
    return result


def _event_data(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        if not line.startswith("data: "):
            continue
        yield cast(dict[str, Any], json.loads(line.removeprefix("data: ")))


def _payload(*, thread_id: str, run_id: str, prompt: str) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": [
            {
                "id": f"message-{run_id}",
                "role": "user",
                "content": prompt,
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def measure_once(
    client: httpx.Client,
    *,
    run_number: int,
    agent_name: str,
    agent_version: str,
    thread_id: str,
    prompt: str,
    timeout: float,
    required_text: str | None = None,
) -> LatencySample:
    client_run_id = f"latency-{uuid4().hex}"
    started = time.perf_counter()
    first_event: float | None = None
    run_started: float | None = None
    first_text: float | None = None
    event_count = 0
    text_characters = 0
    text_parts: list[str] = []
    terminal_type: str | None = None

    with client.stream(
        "POST",
        "/v1/agui",
        params={"agent_name": agent_name, "agent_version": agent_version},
        json=_payload(thread_id=thread_id, run_id=client_run_id, prompt=prompt),
        timeout=timeout,
    ) as response:
        response_headers = time.perf_counter()
        response.raise_for_status()
        harness_run_id = response.headers.get("X-Harness-Run-ID", "")
        for event in _event_data(response.iter_lines()):
            observed = time.perf_counter()
            event_count += 1
            first_event = first_event or observed
            event_type = str(event.get("type", ""))
            if event_type == "RUN_STARTED" and run_started is None:
                run_started = observed
            if event_type == "TEXT_MESSAGE_CONTENT":
                delta = str(event.get("delta", ""))
                text_characters += len(delta)
                text_parts.append(delta)
                if delta and first_text is None:
                    first_text = observed
            if event_type in TERMINAL_TYPES:
                terminal_type = event_type
    finished = time.perf_counter()

    if terminal_type != "RUN_FINISHED":
        raise RuntimeError(f"chat run did not finish successfully (terminal={terminal_type!r})")
    if first_event is None or run_started is None or first_text is None:
        raise RuntimeError("chat run did not emit the required streaming milestones")
    if required_text is not None and required_text not in "".join(text_parts):
        raise RuntimeError("chat response did not contain the required acceptance marker")

    milliseconds = 1000.0
    return LatencySample(
        run=run_number,
        harness_run_id=harness_run_id,
        response_headers_ms=(response_headers - started) * milliseconds,
        first_event_ms=(first_event - started) * milliseconds,
        run_started_ms=(run_started - started) * milliseconds,
        first_text_ms=(first_text - started) * milliseconds,
        total_ms=(finished - started) * milliseconds,
        event_count=event_count,
        text_characters=text_characters,
    )


def _headers(
    compose_env: Path,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, str]:
    headers = {
        "X-Tenant-ID": tenant_id,
        "X-User-ID": user_id,
    }
    token = os.getenv("HARNESS_API_BEARER_TOKEN") or dotenv_values(compose_env).get(
        "HARNESS_API_BEARER_TOKEN"
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _publish_benchmark_agents(client: httpx.Client) -> None:
    """Publish the echo agent and its pinned child into the benchmark tenant."""

    for manifest in (
        ROOT / "agents/helper-agent/agent.yaml",
        ROOT / "agents/echo-agent/agent.yaml",
    ):
        with tempfile.TemporaryDirectory(prefix="harness-latency-bundle-") as directory:
            archive, _ = pack_agent_package(manifest, output_directory=directory)
            response = client.post(
                "/v1/agents/bundles",
                content=archive.read_bytes(),
                headers={"Content-Type": "application/zip"},
            )
        if response.status_code not in {201, 409}:
            response.raise_for_status()


def run_benchmark(
    *,
    api_url: str,
    compose_env: Path,
    agent_name: str,
    agent_version: str,
    runs: int,
    warmups: int,
    reuse_thread: bool,
    prompt: str,
    timeout: float,
    tenant_id: str = "latency-benchmark",
    user_id: str = "latency-benchmark-user",
    publish_agents: bool = True,
    required_text: str | None = None,
) -> dict[str, Any]:
    if runs < 1 or warmups < 0:
        raise ValueError("runs must be positive and warmups cannot be negative")
    suite_id = uuid4().hex
    shared_thread_id = f"latency-thread-{suite_id}"
    measured: list[LatencySample] = []
    with httpx.Client(
        base_url=api_url,
        headers=_headers(compose_env, tenant_id=tenant_id, user_id=user_id),
        trust_env=False,
    ) as client:
        health = client.get("/healthz", timeout=10)
        health.raise_for_status()
        if publish_agents:
            _publish_benchmark_agents(client)
        for index in range(warmups + runs):
            thread_id = shared_thread_id if reuse_thread else f"latency-thread-{suite_id}-{index}"
            sample = measure_once(
                client,
                run_number=index - warmups + 1,
                agent_name=agent_name,
                agent_version=agent_version,
                thread_id=thread_id,
                prompt=prompt,
                timeout=timeout,
                required_text=required_text,
            )
            if index >= warmups:
                measured.append(sample)
    return {
        "configuration": {
            "api_url": api_url,
            "agent": f"{agent_name}@{agent_version}",
            "runs": runs,
            "warmups": warmups,
            "reuse_thread": reuse_thread,
            "prompt": prompt,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "published_agents": publish_agents,
            "required_text": required_text,
        },
        "samples": [asdict(sample) for sample in measured],
        "summary": summarize(measured),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--compose-env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--agent-name", default="echo-agent")
    parser.add_argument("--agent-version", default="0.4.1")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--reuse-thread", action="store_true")
    parser.add_argument("--prompt", default="Reply with exactly: OK")
    parser.add_argument("--tenant-id", default="latency-benchmark")
    parser.add_argument("--user-id", default="latency-benchmark-user")
    parser.add_argument("--skip-publish", action="store_true")
    parser.add_argument("--require-text")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = run_benchmark(
        api_url=arguments.api_url,
        compose_env=arguments.compose_env,
        agent_name=arguments.agent_name,
        agent_version=arguments.agent_version,
        runs=arguments.runs,
        warmups=arguments.warmups,
        reuse_thread=arguments.reuse_thread,
        prompt=arguments.prompt,
        timeout=arguments.timeout,
        tenant_id=arguments.tenant_id,
        user_id=arguments.user_id,
        publish_agents=not arguments.skip_publish,
        required_text=arguments.require_text,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
