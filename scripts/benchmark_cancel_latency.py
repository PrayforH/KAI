"""Measure active Run cancellation convergence against a running Harness API."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from harness.agent_package import pack_agent_package

ROOT = Path(__file__).parents[1]
DEFAULT_ENV = ROOT / "deploy/docker-compose/.env.docker"
ACTIVE_STATUS = "running"
TERMINAL_STATUS = {"cancelled", "failed", "rejected", "succeeded", "timed_out"}


@dataclass(frozen=True)
class CancellationSample:
    run: int
    run_id: str
    cancel_response_ms: float
    convergence_ms: float
    durable_convergence_ms: float
    status_polls: int
    cancelling_sequence: int
    terminal_sequence: int


def _nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(samples: list[CancellationSample]) -> dict[str, dict[str, float]]:
    if not samples:
        raise ValueError("at least one sample is required")
    result: dict[str, dict[str, float]] = {}
    for field in ("cancel_response_ms", "convergence_ms", "durable_convergence_ms"):
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


def _headers(compose_env: Path, tenant_id: str) -> dict[str, str]:
    headers = {
        "X-Tenant-ID": tenant_id,
        "X-User-ID": "cancel-benchmark-user",
    }
    token = os.getenv("HARNESS_API_BEARER_TOKEN") or dotenv_values(compose_env).get(
        "HARNESS_API_BEARER_TOKEN"
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _publish_benchmark_agents(client: httpx.Client) -> None:
    for manifest in (
        ROOT / "agents/helper-agent/agent.yaml",
        ROOT / "agents/echo-agent/agent.yaml",
    ):
        with tempfile.TemporaryDirectory(prefix="harness-cancel-bundle-") as directory:
            archive, _ = pack_agent_package(manifest, output_directory=directory)
            response = client.post(
                "/v1/agents/bundles",
                content=archive.read_bytes(),
                headers={"Content-Type": "application/zip"},
            )
        if response.status_code not in {201, 409}:
            response.raise_for_status()


def _wait_for_status(
    client: httpx.Client,
    run_id: str,
    *,
    target: set[str],
    deadline: float,
    poll_seconds: float,
) -> tuple[dict[str, Any], int]:
    polls = 0
    while time.perf_counter() < deadline:
        response = client.get(f"/v1/runs/{run_id}")
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        polls += 1
        if str(payload.get("status", "")) in target:
            return payload, polls
        time.sleep(poll_seconds)
    raise TimeoutError(f"run {run_id} did not reach one of {sorted(target)}")


def _event_timestamp(event: dict[str, Any]) -> datetime:
    value = str(event["timestamp"])
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _assert_cancel_boundary(client: httpx.Client, run_id: str) -> tuple[int, int, float]:
    response = client.get(f"/v1/runs/{run_id}/events")
    response.raise_for_status()
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.iter_lines()
        if line.startswith("data: ")
    ]
    cancelling = next(event for event in events if event.get("type") == "run.cancelling")
    terminal = next(event for event in events if event.get("type") == "run.cancelled")
    cancelling_sequence = int(cancelling["sequence"])
    terminal_sequence = int(terminal["sequence"])
    visible_after_cancel = [
        event
        for event in events
        if int(event["sequence"]) > cancelling_sequence
        and event.get("type") == "message.delta"
        and str(cast(dict[str, Any], event.get("payload", {})).get("text", ""))
    ]
    if visible_after_cancel:
        raise RuntimeError(f"run {run_id} emitted visible text after run.cancelling")
    archived_after_cancel = [
        event
        for event in events
        if int(event["sequence"]) > cancelling_sequence
        and event.get("type") == "workspace.archived"
    ]
    if archived_after_cancel:
        raise RuntimeError(
            f"run {run_id} archived a partial workspace after run.cancelling"
        )
    durable_convergence_ms = (
        _event_timestamp(terminal) - _event_timestamp(cancelling)
    ).total_seconds() * 1000.0
    if durable_convergence_ms < 0:
        raise RuntimeError(f"run {run_id} has non-monotonic cancellation timestamps")
    return cancelling_sequence, terminal_sequence, durable_convergence_ms


def measure_once(
    client: httpx.Client,
    *,
    run_number: int,
    session_id: str,
    timeout: float,
    poll_seconds: float,
) -> CancellationSample:
    create = client.post(
        f"/v1/sessions/{session_id}/runs",
        headers={"Idempotency-Key": f"cancel-{uuid4().hex}"},
        json={"prompt": "Reply with exactly: OK"},
    )
    create.raise_for_status()
    run_id = str(create.json()["run_id"])
    deadline = time.perf_counter() + timeout
    active, _ = _wait_for_status(
        client,
        run_id,
        target={ACTIVE_STATUS},
        deadline=deadline,
        poll_seconds=poll_seconds,
    )
    if active.get("status") != ACTIVE_STATUS:
        raise RuntimeError(f"run {run_id} was not actively owned before cancellation")

    started = time.perf_counter()
    response = client.post(f"/v1/runs/{run_id}/cancel")
    response_observed = time.perf_counter()
    response.raise_for_status()
    requested = cast(dict[str, Any], response.json())
    if requested.get("status") not in {"cancelling", "cancelled"}:
        raise RuntimeError(f"run {run_id} rejected cancellation: {requested}")
    terminal, polls = _wait_for_status(
        client,
        run_id,
        target=TERMINAL_STATUS,
        deadline=deadline,
        poll_seconds=poll_seconds,
    )
    finished = time.perf_counter()
    if terminal.get("status") != "cancelled":
        raise RuntimeError(f"run {run_id} terminated as {terminal.get('status')}")
    cancelling_sequence, terminal_sequence, durable_convergence_ms = _assert_cancel_boundary(
        client, run_id
    )
    milliseconds = 1000.0
    return CancellationSample(
        run=run_number,
        run_id=run_id,
        cancel_response_ms=(response_observed - started) * milliseconds,
        convergence_ms=(finished - started) * milliseconds,
        durable_convergence_ms=durable_convergence_ms,
        status_polls=polls,
        cancelling_sequence=cancelling_sequence,
        terminal_sequence=terminal_sequence,
    )


def run_benchmark(
    *,
    api_url: str,
    compose_env: Path,
    runs: int,
    warmups: int,
    timeout: float,
    poll_seconds: float,
) -> dict[str, Any]:
    if runs < 1 or warmups < 0 or timeout <= 0 or poll_seconds <= 0:
        raise ValueError("runs/timeout/poll interval must be positive and warmups non-negative")
    suite_id = uuid4().hex[:12]
    tenant_id = f"cancel-benchmark-{suite_id}"
    measured: list[CancellationSample] = []
    with httpx.Client(
        base_url=api_url,
        headers=_headers(compose_env, tenant_id),
        trust_env=False,
        timeout=timeout,
    ) as client:
        health = client.get("/healthz", timeout=10)
        health.raise_for_status()
        _publish_benchmark_agents(client)
        session = client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.4.1"},
        )
        session.raise_for_status()
        session_id = str(session.json()["session_id"])
        for index in range(warmups + runs):
            sample = measure_once(
                client,
                run_number=index - warmups + 1,
                session_id=session_id,
                timeout=timeout,
                poll_seconds=poll_seconds,
            )
            if index >= warmups:
                measured.append(sample)
    return {
        "configuration": {
            "api_url": api_url,
            "tenant_id": tenant_id,
            "runs": runs,
            "warmups": warmups,
            "poll_seconds": poll_seconds,
            "cancelled_from": ACTIVE_STATUS,
        },
        "samples": [asdict(sample) for sample in measured],
        "summary": summarize(measured),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--compose-env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--poll-seconds", type=float, default=0.005)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = run_benchmark(
        api_url=arguments.api_url,
        compose_env=arguments.compose_env,
        runs=arguments.runs,
        warmups=arguments.warmups,
        timeout=arguments.timeout,
        poll_seconds=arguments.poll_seconds,
    )
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    print(payload)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{payload}\n")


if __name__ == "__main__":
    main()
