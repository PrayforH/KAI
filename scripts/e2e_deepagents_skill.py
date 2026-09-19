"""Probe whether a platform Skill reaches a DeepAgents Run.

Run inside an api container (see `e2e_deepagents_loop.py` for why).

The platform mounts a Skill by materializing its files into the Run workspace
and handing the directory to the kernel. For DeepAgents that is
`create_deep_agent(skills=[".claude/skills"])`, and the kernel is expected to
discover it the way it discovers any Skill: list the directory, then read the
`SKILL.md`. So the evidence to look for is a tool call that lists or reads
inside the Skill root -- without one, the Skill was mounted but never used.

Two routes reach that same place, and they are not interchangeable:

* ``reference`` -- ``spec.skillReferences``. This is the product route, and it is
  the one ``compatibleRuntimes`` gates: while a package excludes a runtime,
  ``resolve_skills`` drops the reference silently and ``validate`` rejects it.
  A Skill that is declared and then not mounted is the worst outcome, so this
  route is the one that has to be exercised.
* ``direct`` -- ``POST /drafts/{id}/skills/catalog/{package}/install``. It
  bypasses the reference lifecycle by design, which is what made the experiment
  possible before the catalog was changed.

    docker cp scripts/e2e_deepagents_skill.py <api-container>:/tmp/skill.py
    docker exec <api-container> /app/.venv/bin/python /tmp/skill.py minimax-docx reference

`prompt` (3rd argument) exists because a Skill's own prerequisites decide whether
a Run can finish: `minimax-docx` requires the .NET SDK and will spend the whole
Run trying to install it. That is a per-Skill, per-sandbox-template concern and
it applies to every runtime, so it must not be confused with the question this
probe answers. Use a Skill whose prerequisites the template already satisfies
when the object is a fully green Run.
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

PACKAGE = sys.argv[1] if len(sys.argv) > 1 else "minimax-docx"
ROUTE = sys.argv[2] if len(sys.argv) > 2 else "reference"
PROMPT = (
    sys.argv[3]
    if len(sys.argv) > 3
    else (
        "请使用已挂载的技能，生成一份简短的 Word 文档，内容是一份三行的项目周报，"
        "保存为 weekly.docx，并说明你使用了哪个技能。"
    )
)

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


def read_events(client: httpx.Client, run_id: str) -> list[dict]:
    response = client.get(f"/v1/runs/{run_id}/events")
    if response.status_code != 200:
        return []
    if "json" in response.headers.get("content-type", ""):
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
    client = httpx.Client(base_url=BASE, headers=HEADERS, timeout=180.0)
    stamp = str(int(time.time()))

    print(f"\n== 1. draft (package={PACKAGE}) ==")
    created = client.post(
        "/v1/studio/drafts",
        json={
            "name": f"skill-probe-{stamp}",
            "domain": "verification",
            "displayName": "Skill 通路探针",
            "description": "验证平台 Skill 能否抵达 DeepAgents 运行。",
            "template": "analyst",
        },
    )
    if not check("create draft", created.status_code == 201, f"HTTP {created.status_code}"):
        print(created.text[:600])
        return 1
    draft = created.json()
    draft_id = draft["draftId"]

    spec = draft["spec"]
    spec["runtime"] = "deepagents"
    replaced = client.put(
        f"/v1/studio/drafts/{draft_id}",
        json={"expectedRevision": draft["revision"], "spec": spec},
    )
    if not check("set runtime", replaced.status_code == 200, f"HTTP {replaced.status_code}"):
        print(replaced.text[:600])
        return 1
    draft = replaced.json()
    print(f"       revision={draft['revision']} runtime={draft['spec']['runtime']}")

    print(f"\n== 2. mount the Skill ({ROUTE} route) ==")
    revision = draft["revision"]
    if ROUTE == "reference":
        # The product route: declare the reference and let the compiler resolve
        # it. Nothing is copied into `spec.skills` by the caller.
        spec = draft["spec"]
        spec["skillReferences"] = [PACKAGE]
        declared = client.put(
            f"/v1/studio/drafts/{draft_id}",
            json={"expectedRevision": revision, "spec": spec},
        )
        if not check(
            "declare the reference",
            declared.status_code == 200,
            f"HTTP {declared.status_code}",
        ):
            print(declared.text[:800])
            return 1
        draft = declared.json()
        revision = draft["revision"]
        print(
            f"       revision={revision}"
            f" skillReferences={draft['spec'].get('skillReferences')}"
            f" skills={[s.get('name') for s in draft['spec']['skills']]}"
        )
    else:
        installed = client.post(
            f"/v1/studio/drafts/{draft_id}/skills/catalog/{PACKAGE}/install",
            json={"expectedRevision": revision, "packageRevision": 1},
        )
        if not check(
            "install skill",
            installed.status_code in (200, 201),
            f"HTTP {installed.status_code}",
        ):
            print(installed.text[:800])
            return 1
        body = installed.json()
        draft = body["draft"]
        print(
            f"       skillName={body.get('skillName')} files={body.get('fileCount')}"
            f" binaries={body.get('binaryFileCount')} risk={body.get('riskLevel')}"
        )
        for finding in (body.get("findings") or [])[:3]:
            print(f"         finding: {finding}")
        revision = draft["revision"]
        print(
            f"       revision={revision}"
            f" skills={[s.get('name') for s in draft['spec']['skills']]}"
        )

    print("\n== 3. validate ==")
    validated = client.post(f"/v1/studio/drafts/{draft_id}/validate")
    if check("validate", validated.status_code == 200, f"HTTP {validated.status_code}"):
        report = validated.json()
        codes = [issue.get("code") for issue in (report.get("issues") or [])]
        print(f"       ready={report.get('ready')} issues={len(codes)}")
        for issue in (report.get("issues") or [])[:6]:
            print(f"         - {issue.get('code')}: {issue.get('message')}")
        if ROUTE == "reference":
            # This is the whole point of the gate: an incompatible reference has
            # to fail here, and a compatible one has to produce a manifest that
            # actually lists the Skill.
            check(
                "no runtime-incompatible reference",
                "skill_reference_runtime_incompatible" not in codes,
                ", ".join(str(code) for code in codes) or "no issues",
            )
            check("validation is ready", bool(report.get("ready")))
            manifest = str(report.get("manifestYaml") or "")
            check(
                "the manifest lists the Skill",
                f"skills/{PACKAGE}" in manifest,
                f"skills/{PACKAGE}",
            )

    print("\n== 4. publish ==")
    published = client.post(
        f"/v1/studio/drafts/{draft_id}/publish",
        json={"expectedRevision": revision},
    )
    if not check("publish", published.status_code in (200, 201), f"HTTP {published.status_code}"):
        print(published.text[:800])
        return 1
    refreshed = client.get(f"/v1/studio/drafts/{draft_id}")
    if refreshed.status_code == 200:
        revision = refreshed.json()["revision"]
    print(f"       revision after publish={revision}")

    print("\n== 5. run ==")
    started = client.post(
        f"/v1/studio/drafts/{draft_id}/try-runs",
        json={
            "expectedRevision": revision,
            "prompt": PROMPT,
            "idempotencyKey": f"skill-probe-{stamp}",
        },
    )
    if not check("start run", started.status_code == 202, f"HTTP {started.status_code}"):
        print(started.text[:900])
        return 1
    run_id = started.json()["run"]["run_id"]
    print(f"       runId={run_id}")

    terminal = {"succeeded", "failed", "cancelled"}
    status = "?"
    deadline = time.time() + 420
    while time.time() < deadline:
        detail = client.get(f"/v1/runs/{run_id}")
        if detail.status_code == 200:
            status = detail.json().get("status", "?")
        if status in terminal:
            break
        time.sleep(5)

    events = read_events(client, run_id)
    seen = {event.get("type") for event in events}
    requests = [
        (event.get("payload") or {})
        for event in events
        if event.get("type") == "tool.request"
    ]
    print(f"       status={status}")
    print(f"       event types ({len(seen)}): {sorted(t for t in seen if t)}")
    print(f"       tool calls ({len(requests)}):")
    for payload in requests:
        name = payload.get("name")
        arguments = json.dumps(payload.get("arguments") or {}, ensure_ascii=False)
        print(f"         - {name} {arguments[:160]}")

    print("\n== 6. skill evidence ==")
    check("run succeeded", status == "succeeded", status)
    skill_root = ".claude/skills"
    touched = [
        payload
        for payload in requests
        if skill_root in json.dumps(payload.get("arguments") or {}, ensure_ascii=False)
        or str(payload.get("name") or "") in {"ls", "read_file"}
    ]
    check(
        "the Skill root was listed or read",
        bool(touched),
        f"{len(touched)} of {len(requests)} tool calls touched it",
    )
    check(
        "staged skills were announced",
        "agent.assets.staged" in seen,
        ", ".join(
            json.dumps((event.get("payload") or {}), ensure_ascii=False)[:200]
            for event in events
            if event.get("type") == "agent.assets.staged"
        ),
    )
    check("artifact.ready observed", "artifact.ready" in seen)
    artifacts = client.get(f"/v1/runs/{run_id}/artifacts")
    if artifacts.status_code == 200:
        names = [str(item.get("name")) for item in artifacts.json()]
        print(f"       artifacts={len(names)}: {names}")

    if status == "failed":
        print("\n  -- failure diagnostics --")
        for event in events[-6:]:
            payload = json.dumps(event.get("payload") or {}, ensure_ascii=False)
            print(f"     {event.get('type')}: {payload[:400]}")

    failed = [label for label, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 64)
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    print(f"draftId={draft_id} revision={revision} runId={run_id} status={status}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
