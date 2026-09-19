"""Drive the DeepAgents closed loop against a live deployment.

This is the acceptance check for the DeepAgents runtime: it walks the whole
path a user walks and asserts the event contract the platform promises.

    create draft -> set runtime=deepagents -> validate -> code view
    -> export ZIP -> publish -> try run -> observe runtime events

Run it **inside an api container**, not on the host, so it reaches the same
control plane the Worker does and inherits the deployment's bearer token:

    docker cp scripts/e2e_deepagents_loop.py <api-container>:/tmp/loop.py
    docker exec <api-container> /app/.venv/bin/python /tmp/loop.py

`TENANT` must be a tenant that holds model-route credentials; a fresh tenant
resolves no route and the Run fails before the kernel is reached. Every
invariant worth keeping belongs here as a `check(...)` rather than in a
manual reading of the stream -- this is the only place an invariant is
asserted against a live deployment.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
# The tenant that actually holds the model-route credentials on this host; a
# fresh tenant resolves no route and the run fails before the kernel starts.
TENANT = "local"
USER = "builder-a"

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


def read_event_types(client: httpx.Client, run_id: str) -> set[str]:
    """The events endpoint streams SSE; accept a JSON body too."""

    return {str(event.get("type") or "") for event in _read_events(client, run_id)}


def _read_events(client: httpx.Client, run_id: str) -> list[dict]:
    """Both shapes the events endpoint is known to answer with, as one list."""

    response = client.get(f"/v1/runs/{run_id}/events")
    if response.status_code != 200:
        return []
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        body = response.json()
        items = body.get("items") if isinstance(body, dict) else body
        return [event for event in (items or []) if isinstance(event, dict)]
    events: list[dict] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def main() -> int:
    client = httpx.Client(base_url=BASE, headers=HEADERS, timeout=120.0)
    stamp = str(int(time.time()))

    # ---------------------------------------------------------------- draft
    print("\n== 1. create draft ==")
    created = client.post(
        "/v1/studio/drafts",
        json={
            "name": f"deepagents-loop-{stamp}",
            "domain": "verification",
            "displayName": "DeepAgents 闭环验证",
            "description": "验证 DeepAgents 运行时的端到端闭环。",
            "template": "analyst",
        },
    )
    if not check("create draft", created.status_code == 201, f"HTTP {created.status_code}"):
        print(created.text[:600])
        return 1
    draft = created.json()
    draft_id = draft["draftId"]
    print(
        f"       draftId={draft_id} revision={draft['revision']}"
        f" runtime={draft['spec']['runtime']}"
    )

    # ------------------------------------------------- runtime=deepagents
    print("\n== 2. set runtime=deepagents ==")
    spec = draft["spec"]
    spec["runtime"] = "deepagents"
    replaced = client.put(
        f"/v1/studio/drafts/{draft_id}",
        json={"expectedRevision": draft["revision"], "spec": spec},
    )
    if not check("replace draft", replaced.status_code == 200, f"HTTP {replaced.status_code}"):
        print(replaced.text[:600])
        return 1
    draft = replaced.json()
    revision = draft["revision"]
    check("runtime persisted", draft["spec"]["runtime"] == "deepagents", draft["spec"]["runtime"])
    print(f"       revision={revision} model={draft['spec']['model']}")

    # -------------------------------------------------------------- validate
    print("\n== 3. validate ==")
    validated = client.post(f"/v1/studio/drafts/{draft_id}/validate")
    ok = check("validate", validated.status_code == 200, f"HTTP {validated.status_code}")
    if ok:
        body = validated.json()
        print(f"       ready={body.get('ready')} issues={len(body.get('issues') or [])}")
        for issue in (body.get("issues") or [])[:5]:
            print(f"         - {issue.get('code')}: {issue.get('message')}")

    # -------------------------------------------------------------- code view
    print("\n== 4. code view ==")
    view = client.get(
        f"/v1/studio/drafts/{draft_id}/deepagents-project/files",
        params={"expectedRevision": revision},
    )
    if check("code view", view.status_code == 200, f"HTTP {view.status_code}"):
        source = view.json()
        files = source.get("files") or []
        print(f"       files={len(files)}")
        for item in files[:8]:
            print(f"         - {item.get('path')}")
        check("code view has files", len(files) > 0, f"{len(files)} files")

    # --------------------------------------------------------------- export
    print("\n== 5. export ZIP ==")
    exported = client.get(
        f"/v1/studio/drafts/{draft_id}/deepagents-project",
        params={"expectedRevision": revision},
    )
    if check("export zip", exported.status_code == 200, f"HTTP {exported.status_code}"):
        content = exported.content
        check("zip is a zip", content[:2] == b"PK", f"{len(content)} bytes")
        print(f"       bytes={len(content)} format={exported.headers.get('X-Agent-Export-Format')}")

    # -------------------------------------------------------------- publish
    print("\n== 6. publish ==")
    published = client.post(
        f"/v1/studio/drafts/{draft_id}/publish",
        json={"expectedRevision": revision},
    )
    if not check("publish", published.status_code in (200, 201), f"HTTP {published.status_code}"):
        print(published.text[:800])
        return 1
    version = published.json()
    print(f"       published={json.dumps(version)[:300]}")

    # Publishing advances the draft, so the run must quote the new revision.
    refreshed = client.get(f"/v1/studio/drafts/{draft_id}")
    if refreshed.status_code == 200:
        revision = refreshed.json()["revision"]
    print(f"       revision after publish={revision}")

    # -------------------------------------------------------------- try run
    print("\n== 7. run ==")
    started = client.post(
        f"/v1/studio/drafts/{draft_id}/try-runs",
        json={
            "expectedRevision": revision,
            "prompt": "在工作区创建 hello.txt，内容为 deepagents loop ok，然后简要说明你做了什么。",
            "idempotencyKey": f"deepagents-loop-{stamp}",
        },
    )
    if not check("start run", started.status_code == 202, f"HTTP {started.status_code}"):
        print(started.text[:900])
        return 1
    run_id = started.json()["run"]["run_id"]
    print(f"       runId={run_id}")

    seen: set[str] = set()
    terminal = {"succeeded", "failed", "cancelled"}
    status = "?"
    deadline = time.time() + 420
    while time.time() < deadline:
        seen |= read_event_types(client, run_id)
        detail = client.get(f"/v1/runs/{run_id}")
        if detail.status_code == 200:
            status = detail.json().get("status", "?")
        if status in terminal:
            break
        time.sleep(5)
    seen |= read_event_types(client, run_id)

    print(f"       status={status}")
    print(f"       event types observed ({len(seen)}): {sorted(seen)}")

    # A failure surfaces only as `run.failed`; the reason lives in the last few
    # events, so print them rather than making the operator go to the Worker log.
    if status == "failed":
        print("\n  -- failure diagnostics --")
        for event in _read_events(client, run_id)[-6:]:
            payload = json.dumps(event.get("payload") or {}, ensure_ascii=False)
            print(f"     {event.get('type')}: {payload[:400]}")

    # ------------------------------------------------------------- assertions
    print("\n== 8. event contract ==")
    check("run reached a terminal state", status in terminal, status)
    check("run succeeded", status == "succeeded", status)
    check("model.route.selected observed", "model.route.selected" in seen)
    check("tool.request observed", "tool.request" in seen)
    check("runtime.result observed", "runtime.result" in seen)
    check(
        "no runtime thread was bound",
        not any("thread" in name for name in seen),
        ", ".join(sorted(n for n in seen if "thread" in n)) or "none",
    )

    # The gate is the only writer of `tool.request`. A second copy would carry the
    # runtime's own tool name, so the Worker's generic policy pass would re-decide
    # a call the gate already decided -- and record the unredacted arguments and a
    # bogus denial while doing it.
    events = _read_events(client, run_id)
    requests = [event for event in events if event.get("type") == "tool.request"]
    request_ids = [
        str((event.get("payload") or {}).get("tool_call_id") or "") for event in requests
    ]
    check(
        "one tool.request per tool call",
        len(request_ids) == len(set(request_ids)),
        f"{len(requests)} requests, {len(set(request_ids))} distinct calls",
    )
    check(
        "no bogus policy_denied on a call that ran",
        not any(
            (event.get("payload") or {}).get("error", {}).get("rule") == "implicit-deny"
            for event in events
            if event.get("type") == "tool.result"
        ),
    )

    # `publish_artifact` is a Claude-SDK in-process MCP tool, so a DeepAgents Run
    # has no artifact route yet (design doc 7.3). Reported, not asserted.
    print(
        f"       artifact.ready observed: {'artifact.ready' in seen}"
        "  (DeepAgents has no artifact route yet)"
    )

    artifacts = client.get(f"/v1/runs/{run_id}/artifacts")
    if artifacts.status_code == 200:
        items = artifacts.json()
        print(f"       artifacts={len(items)}")
        for item in items[:5]:
            print(f"         - {item.get('name')}")

    # ------------------------------------------------------------------ report
    failed = [label for label, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 64)
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
    print(f"draftId={draft_id} revision={revision} runId={run_id} status={status}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
