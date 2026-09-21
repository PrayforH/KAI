"""Ask the agent builder to create a Skill and report exactly how it fails.

A builder rejection only reaches the client — as an HTTP 409, or inside the SSE
body — so it leaves no server-side trace to read afterwards. This drives the
same request the console sends, with ``Accept: application/json`` so the
non-streaming branch answers with a real status and message, which is the only
way to tell the gates in the chain apart.

    docker cp scripts/probe_builder_skill.py <api-container>:/tmp/
    docker exec <api-container> /app/.venv/bin/python /tmp/probe_builder_skill.py [draft_id]
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
PROMPT = "帮我创建一个技能：把长文压缩成要点，保留关键数字与结论。只做技能，不要改别的配置。"


def _items(body: object) -> list[dict]:
    if isinstance(body, dict):
        found = body.get("items") or body.get("drafts") or []
    else:
        found = body
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def main() -> int:
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=HEADERS, timeout=900) as client:
        listing = client.get("/v1/studio/drafts")
        print(f"GET /v1/studio/drafts -> HTTP {listing.status_code}")
        drafts = _items(listing.json() if listing.status_code < 400 else [])
        for draft in drafts[:10]:
            spec = draft.get("spec") or {}
            print(f"  {draft.get('draftId')} rev={draft.get('revision')} name={spec.get('name')}")
        if not drafts:
            print("no drafts visible to this identity; nothing to probe")
            return 1

        wanted = sys.argv[1] if len(sys.argv) > 1 else str(drafts[0].get("draftId"))
        chosen = next((d for d in drafts if d.get("draftId") == wanted), None)
        if chosen is None:
            print(f"draft {wanted} is not visible to this identity")
            return 1
        revision = chosen.get("revision")
        print(f"\nPOST builder-conversation on {wanted} rev={revision}")
        response = client.post(
            f"/v1/studio/drafts/{wanted}/builder-conversation",
            json={
                "expectedRevision": revision,
                "intent": "edit",
                "messages": [{"role": "user", "content": PROMPT}],
            },
            headers={"Accept": "application/json"},
        )
        print(f"HTTP {response.status_code}")
        try:
            print(json.dumps(response.json(), ensure_ascii=False, indent=2)[:4000])
        except ValueError:
            print(response.text[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
