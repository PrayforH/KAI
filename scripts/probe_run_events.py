"""Dump one Run's event stream as a compact timeline.

A diagnostic aid, not part of the acceptance path: when a Run's end state does
not match what the platform promised, the sequence of `tool.request` payloads is
the fastest way to see what the model actually asked for.

    docker cp scripts/probe_run_events.py <api-container>:/tmp/probe.py
    docker exec <api-container> /app/.venv/bin/python /tmp/probe.py <run_id>
"""

from __future__ import annotations

import json
import os
import sys

import httpx

HEADERS = {
    "Authorization": f"Bearer {os.environ['HARNESS_API_BEARER_TOKEN']}",
    "X-Tenant-ID": os.environ.get("TENANT", "local"),
    "X-User-ID": os.environ.get("USER", "builder-a"),
}

_INTERESTING = {
    "tool.request",
    "tool.result",
    "artifact.ready",
    "policy.denied",
    "runtime.result",
    "sandbox.command",
}


def events(client: httpx.Client, run_id: str) -> list[dict]:
    response = client.get(f"/v1/runs/{run_id}/events")
    if response.status_code != 200:
        return []
    if "json" in response.headers.get("content-type", ""):
        body = response.json()
        items = body.get("items") if isinstance(body, dict) else body
        return [event for event in (items or []) if isinstance(event, dict)]
    collected: list[dict] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                collected.append(event)
    return collected


def main() -> int:
    run_id = sys.argv[1]
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=HEADERS, timeout=60) as client:
        for event in events(client, run_id):
            kind = str(event.get("type") or "")
            if kind not in _INTERESTING:
                continue
            payload = event.get("payload") or {}
            if kind == "tool.request":
                print(
                    f"[{event.get('seq')}] tool.request "
                    f"name={payload.get('name')} "
                    f"runtime_name={payload.get('runtime_tool_name')} "
                    f"args={json.dumps(payload.get('arguments'), ensure_ascii=False)[:300]}"
                )
            elif kind == "tool.result":
                # The result body is what says whether a tool worked: an error
                # code alone cannot distinguish "searched and found nothing" from
                # "the provider was never reachable".
                body = json.dumps(
                    {
                        key: value
                        for key, value in payload.items()
                        if key in {"content", "text", "output", "error", "status", "decision"}
                    },
                    ensure_ascii=False,
                )
                print(
                    f"[{event.get('seq')}] tool.result "
                    f"name={payload.get('name')} "
                    f"status={payload.get('status') or payload.get('decision')} "
                    f"error={payload.get('error_code')} "
                    f"body={body[:400]}"
                )
            else:
                print(
                    f"[{event.get('seq')}] {kind} "
                    f"{json.dumps(payload, ensure_ascii=False)[:300]}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
