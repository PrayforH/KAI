"""End-to-end regression on 174: one real Run through the deployed workers.

Creates a session and a Run through the same services the API handler uses, then waits
for a worker to pick it up from the queue and execute it in a sandbox. Nothing is
stubbed: the model, the sandbox, the policy gate and the event stream are the deployed
ones. Read-only apart from the Run it creates.
"""

import asyncio
import json

from harness.composition import build_production_container
from harness.config import Settings
from harness.studio.try_run import final_text

TENANT = "local"
USER = "user_1c16a8994ff548c298c356fd8385eb76"
AGENT_NAME = "similar-case-analysis-agent"
AGENT_VERSION = "0.1.2"
PROMPT = "你好"
DEADLINE_SECONDS = 420


async def main() -> int:
    container = build_production_container(Settings(), execution_enabled=False)
    session = await container.sessions.create(TENANT, USER, AGENT_NAME, AGENT_VERSION)
    creation = await container.runs.create_with_result(
        TENANT,
        session.session_id,
        f"review-verify-{session.session_id}",
        input={"prompt": PROMPT},
    )
    run_id = creation.run.run_id
    print(f"run={run_id} session={session.session_id} created={creation.created}")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + DEADLINE_SECONDS
    status = creation.run.status.value
    while loop.time() < deadline:
        run = await container.runs.get(TENANT, run_id)
        status = run.status.value
        if run.status.is_terminal:
            break
        await asyncio.sleep(2)

    events = await container.observed_events.list_after(TENANT, run_id, 0)
    counts: dict[str, int] = {}
    for event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    route = next(
        (dict(e.payload) for e in events if e.type == "model.route.selected"), None
    )
    outcome = next((dict(e.payload) for e in events if e.type == "runtime.result"), None)
    answer = final_text(events) or ""

    report = {
        "run": run_id,
        "status": status,
        "modelRoute": route,
        "runtimeResult": {
            key: outcome.get(key) for key in ("subtype", "is_error", "num_turns", "usage")
        }
        if outcome
        else None,
        "eventTypes": counts,
        "answerChars": len(answer),
        "answerHead": answer[:120],
        "verdict": "PASS"
        if status == "succeeded" and answer.strip() and (outcome or {}).get("is_error") is False
        else "FAIL",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


raise SystemExit(asyncio.run(main()))
