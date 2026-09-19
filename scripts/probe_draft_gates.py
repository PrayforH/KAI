"""Check the compile-time gates through the API, and that their messages act on.

A gate is only useful if it names the thing to change. These probes read the
deployment's own catalog, build the smallest draft that trips each rule, and
print the issue with the message the operator will actually see.

    docker cp scripts/probe_draft_gates.py <api-container>:/tmp/gates.py
    docker exec <api-container> /app/.venv/bin/python /tmp/gates.py
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

HEADERS = {
    "Authorization": f"Bearer {os.environ['HARNESS_API_BEARER_TOKEN']}",
    "X-Tenant-ID": os.environ.get("TENANT", "local"),
    "X-User-ID": os.environ.get("USER", "builder-a"),
}

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    return ok


def _issues(client: httpx.Client, draft_id: str) -> dict[str, str]:
    report = client.post(f"/v1/studio/drafts/{draft_id}/validate").json()
    return {
        str(issue.get("code")): str(issue.get("message"))
        for issue in (report.get("issues") or [])
    }


def _draft(client: httpx.Client, stamp: str, runtime: str, **spec_updates: object) -> str:
    created = client.post(
        "/v1/studio/drafts",
        json={
            "name": f"gate-probe-{stamp}-{runtime[:6]}-{len(spec_updates)}",
            "domain": "verification",
            "displayName": "门禁探针",
            "description": "验证编译期门禁与报错可操作性。",
            "template": "analyst",
        },
    )
    draft = created.json()
    spec = draft["spec"]
    spec["runtime"] = runtime
    spec.update(spec_updates)
    replaced = client.put(
        f"/v1/studio/drafts/{draft['draftId']}",
        json={"expectedRevision": draft["revision"], "spec": spec},
    )
    return str(replaced.json()["draftId"])


def main() -> int:
    stamp = str(int(time.time()))
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=HEADERS, timeout=90) as client:
        capabilities = client.get("/v1/studio/capabilities").json()
        runtimes = {
            item["runtime"]: set(item.get("capabilities") or [])
            for item in capabilities.get("runtimeCapabilities") or []
        }
        limitations = {
            item["runtime"]: " ".join(item.get("limitations") or [])
            for item in capabilities.get("runtimeCapabilities") or []
        }
        print("\n== 1. runtime features ==")
        check("the claude runtime declares web", "web" in runtimes["claude-agent-sdk"], "web")
        for runtime in ("codex-app-server", "deepagents"):
            check(
                f"{runtime} does not declare web",
                "web" not in runtimes[runtime],
                ", ".join(sorted(runtimes[runtime])),
            )
        check(
            "deepagents does not declare subagents",
            "subagents" not in runtimes["deepagents"],
        )
        # A limitation is a promise the console repeats to the user, so a stale
        # one is a false statement about a runtime that has since been measured.
        check(
            "deepagents no longer disclaims reviewed platform Skills",
            "not reviewed" not in limitations["deepagents"],
            limitations["deepagents"],
        )
        for runtime in ("codex-app-server", "deepagents"):
            check(
                f"{runtime} states the web limitation it has",
                "web tools are not connected" in limitations[runtime],
            )

        print("\n== 2. the web gate ==")
        for runtime, expected in (
            ("claude-agent-sdk", False),
            ("codex-app-server", True),
            ("deepagents", True),
        ):
            issues = _issues(
                client,
                _draft(client, stamp, runtime, builtinTools=["Read", "WebSearch"]),
            )
            fired = "web_tools_runtime_unsupported" in issues
            check(
                f"{runtime}: web rejected is {expected}",
                fired is expected,
                issues.get("web_tools_runtime_unsupported", "accepted"),
            )

        print("\n== 3. the Sub Agent gate ==")
        for runtime, expected in (
            ("claude-agent-sdk", False),
            ("codex-app-server", False),
            ("deepagents", True),
        ):
            issues = _issues(client, _draft(client, stamp, runtime, builtinTools=["Read", "Task"]))
            fired = "runtime_subagents_unsupported" in issues
            check(
                f"{runtime}: Task rejected is {expected}",
                fired is expected,
                issues.get("runtime_subagents_unsupported", "accepted"),
            )

        print("\n== 4. an unregistered model names the ones the route serves ==")
        route = next(
            item
            for item in capabilities["modelRoutes"]
            if item.get("models") and item.get("modelType") in {"chat", "vision"}
        )
        gated = _issues(
            client,
            _draft(
                client,
                stamp,
                "claude-agent-sdk",
                model={
                    "routeId": route["routeId"],
                    "model": "not-a-real-model",
                    "requiredCapabilities": ["streaming", "tool_use"],
                },
            ),
        )
        message = gated.get("model_not_available", "")
        check("the model gate fires", bool(message), message)
        check(
            "the message names a model the route serves",
            str(route["models"][0]) in message,
            f"route {route['routeId']} models={json.dumps(route['models'], ensure_ascii=False)}",
        )

    failed = [label for label, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 64)
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
