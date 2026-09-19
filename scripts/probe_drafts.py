"""List drafts, or explain why one will not validate.

A "draft is not ready" message names the symptoms; the draft's own spec is what
says which field caused them. This prints the fields the compiler reads -- the
runtime, the model route and model id, and the builtin tools -- next to the
issues, so the two can be read together.

    docker cp scripts/probe_drafts.py <api-container>:/tmp/drafts.py
    docker exec <api-container> /app/.venv/bin/python /tmp/drafts.py            # list
    docker exec <api-container> /app/.venv/bin/python /tmp/drafts.py <draft_id> # explain
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

_SPEC_FIELDS = (
    "runtime",
    "model",
    "builtinTools",
    "skillReferences",
    "mcpServers",
    "knowledgeReferences",
    "toolExposureMode",
)


def _items(body: object) -> list[dict]:
    if isinstance(body, dict):
        items = body.get("items") or body.get("drafts") or []
    else:
        items = body
    return [item for item in items if isinstance(item, dict)]


def main() -> int:
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=HEADERS, timeout=60) as client:
        if len(sys.argv) < 2:
            response = client.get("/v1/studio/drafts")
            print(f"HTTP {response.status_code}")
            for draft in _items(response.json())[:20]:
                spec = draft.get("spec") or {}
                print(
                    f"{draft.get('draftId')} rev={draft.get('revision')}"
                    f" runtime={spec.get('runtime')}"
                    f" model={json.dumps(spec.get('model'), ensure_ascii=False)}"
                    f" builtinTools={spec.get('builtinTools')}"
                    f" updated={draft.get('updatedAt')}"
                )
            return 0

        draft_id = sys.argv[1]
        draft = client.get(f"/v1/studio/drafts/{draft_id}").json()
        spec = draft.get("spec") or {}
        print(f"draftId={draft_id} revision={draft.get('revision')}")
        for field in _SPEC_FIELDS:
            print(f"    {field} = {json.dumps(spec.get(field), ensure_ascii=False)}")

        routes = client.get("/v1/studio/capabilities").json().get("modelRoutes") or []
        route_id = (spec.get("model") or {}).get("routeId")
        for route in routes:
            if route.get("routeId") == route_id:
                print(
                    f"    route {route_id!r}: apiFormat={route.get('apiFormat')}"
                    f" enabled={route.get('enabled')}"
                    f" models={json.dumps(route.get('models'), ensure_ascii=False)}"
                )

        report = client.post(f"/v1/studio/drafts/{draft_id}/validate")
        print(f"\nvalidate HTTP {report.status_code}")
        body = report.json()
        print(f"ready={body.get('ready')}")
        for issue in body.get("issues") or []:
            print(
                f"  [{issue.get('severity')}] {issue.get('code')} @ {issue.get('path')}"
                f" -- {issue.get('message')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
