"""Dump the model routes a tenant actually has, and which runtime can use them.

A validation failure like "model X is not in route Y" is only actionable once the
route's real contents are known, so this probe reads them from the deployment
rather than from the source defaults.

    docker cp scripts/probe_model_routes.py <api-container>:/tmp/routes.py
    docker exec <api-container> /app/.venv/bin/python /tmp/routes.py
"""

from __future__ import annotations

import json
import os

import httpx

HEADERS = {
    "Authorization": f"Bearer {os.environ['HARNESS_API_BEARER_TOKEN']}",
    "X-Tenant-ID": os.environ.get("TENANT", "local"),
    "X-User-ID": os.environ.get("USER", "builder-a"),
}


def main() -> int:
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=HEADERS, timeout=60) as client:
        catalog = client.get("/v1/studio/capabilities")
        if catalog.status_code != 200:
            print(f"capabilities HTTP {catalog.status_code}: {catalog.text[:300]}")
            return 1
        body = catalog.json()
        for route in body.get("modelRoutes") or []:
            print(
                f"route_id={route.get('routeId')!r} "
                f"label={route.get('label')!r} "
                f"enabled={route.get('enabled')} "
                f"api_format={route.get('apiFormat')} "
                f"model_type={route.get('modelType')} "
                f"capabilities={route.get('capabilities')}"
            )
            print(f"    models={json.dumps(route.get('models'), ensure_ascii=False)}")
        print("--- runtime api formats ---")
        for runtime in body.get("runtimeCapabilities") or []:
            print(
                f"{runtime.get('runtime')} -> {runtime.get('modelApiFormats')}"
                f"  capabilities={runtime.get('capabilities')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
