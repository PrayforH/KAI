"""Ask the deployment whether its built-in internet actually works.

The tool being wired and the tool being able to search are different claims, and
only a Run settles the second: the platform key may be absent and the user may
have configured no key of their own, in which case every runtime fails the same
way and the runtime is not the variable.

Run inside an api container (see `e2e_deepagents_loop.py` for why).

    docker cp scripts/probe_web_run.py <api-container>:/tmp/webrun.py
    docker exec <api-container> /app/.venv/bin/python /tmp/webrun.py [runtime]
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
TENANT = "local"
USER = "builder-a"
RUNTIME = sys.argv[1] if len(sys.argv) > 1 else "claude-agent-sdk"
PROMPT = "用平台的联网搜索查一下今天有什么重要新闻，只列一条标题和来源，不要用其它工具。"

HEADERS = {
    "Authorization": f"Bearer {os.environ['HARNESS_API_BEARER_TOKEN']}",
    "X-Tenant-ID": TENANT,
    "X-User-ID": USER,
}

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    return ok


def _events(client: httpx.Client, run_id: str) -> list[dict]:
    response = client.get(f"/v1/runs/{run_id}/events")
    if response.status_code != 200:
        return []
    if "json" in response.headers.get("content-type", ""):
        body = response.json()
        items = body.get("items") if isinstance(body, dict) else body
        return [item for item in (items or []) if isinstance(item, dict)]
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
    stamp = str(int(time.time()))
    config = httpx.Client(base_url=BASE, headers=HEADERS, timeout=120)
    print("\n== web configuration ==")
    body = config.get("/v1/studio/web-configuration").json()
    print(
        f"       enabled={body.get('effectiveEnabled')} provider={body.get('provider')}"
        f" platformCredential={body.get('credentialConfigured')}"
        f" personalKey={body.get('personalKeyConfigured')}"
    )

    print(f"\n== run ({RUNTIME}) ==")
    draft = config.post(
        "/v1/studio/drafts",
        json={
            "name": f"web-probe-{stamp}",
            "domain": "verification",
            "displayName": "内置联网探针",
            "description": "验证平台内置联网在该部署上是否真的可用。",
            "template": "analyst",
        },
    ).json()
    draft_id = draft["draftId"]
    spec = draft["spec"]
    spec["runtime"] = RUNTIME
    spec["builtinTools"] = ["Read", "Glob", "WebSearch"]
    replaced = config.put(
        f"/v1/studio/drafts/{draft_id}",
        json={"expectedRevision": draft["revision"], "spec": spec},
    ).json()
    report = config.post(f"/v1/studio/drafts/{draft_id}/validate").json()
    check("the draft validates", bool(report.get("ready")), json.dumps(report.get("issues"))[:300])
    published = config.post(
        f"/v1/studio/drafts/{draft_id}/publish",
        json={"expectedRevision": replaced["revision"]},
    )
    check("publish", published.status_code in (200, 201), f"HTTP {published.status_code}")
    revision = config.get(f"/v1/studio/drafts/{draft_id}").json()["revision"]
    started = config.post(
        f"/v1/studio/drafts/{draft_id}/try-runs",
        json={
            "expectedRevision": revision,
            "prompt": PROMPT,
            "idempotencyKey": f"web-probe-{stamp}",
        },
    )
    if started.status_code != 202:
        print(f"       start failed: HTTP {started.status_code} {started.text[:400]}")
        return 1
    run_id = started.json()["run"]["run_id"]
    status = "?"
    deadline = time.time() + 300
    while time.time() < deadline:
        detail = config.get(f"/v1/runs/{run_id}")
        if detail.status_code == 200:
            status = str(detail.json().get("status", "?"))
        if status in {"succeeded", "failed", "cancelled", "timed_out"}:
            break
        time.sleep(5)
    print(f"       runId={run_id} status={status}")

    events = _events(config, run_id)
    requests = [
        payload
        for event in events
        if event.get("type") == "tool.request"
        for payload in [event.get("payload") or {}]
    ]
    print(f"       tool calls ({len(requests)}):")
    for payload in requests:
        arguments = json.dumps(payload.get("arguments") or {}, ensure_ascii=False)
        print(f"         - {payload.get('name')} {arguments[:160]}")

    searched = [payload for payload in requests if payload.get("name") == "WebSearch"]
    web = [payload for payload in requests if payload.get("name") in {"WebSearch", "WebFetch"}]
    check("the runtime reached the web tool", bool(web), f"{len(web)} web calls")
    # A tool that ran and reported a provider failure is a deployment gap, not a
    # runtime one, so the two are reported separately rather than collapsed into
    # "the web tool failed".
    bodies = " ".join(
        str((event.get("payload") or {}).get("content") or "")
        for event in events
        if event.get("type") == "tool.result"
    )
    print(f"       tool result body: {bodies[:300]}")
    if searched or bodies:
        unconfigured = "尚未配置搜索服务凭据" in bodies
        unreachable = any(
            marker in bodies for marker in ("暂时不可用", "已关闭联网", "已在个人设置中关闭")
        )
        check(
            "the deployment has a search credential",
            not unconfigured and not unreachable,
            "平台与个人都未配置搜索密钥"
            if unconfigured
            else "provider unreachable"
            if unreachable
            else "credential present",
        )
    failed = [label for label, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 64)
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    print(f"draftId={draft_id} runId={run_id} status={status}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
