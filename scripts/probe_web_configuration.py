"""Report whether the platform's built-in internet can actually reach a provider.

A wired tool and a working tool are different claims. The built-in web tools are
only as good as the credential behind them, and there are two places it can come
from: the deployment's platform key, or the calling user's personal settings. This
prints both, with keys reduced to whether they are set.

    docker cp scripts/probe_web_configuration.py <api-container>:/tmp/webcfg.py
    docker exec <api-container> /app/.venv/bin/python /tmp/webcfg.py [user_id]
"""

from __future__ import annotations

import json
import os
import sys

import httpx

USER = sys.argv[1] if len(sys.argv) > 1 else "builder-a"
HEADERS = {
    "Authorization": f"Bearer {os.environ['HARNESS_API_BEARER_TOKEN']}",
    "X-Tenant-ID": os.environ.get("TENANT", "local"),
    "X-User-ID": USER,
}

_SECRET_MARKERS = ("key", "token", "secret", "password", "authorization", "signature")


def _masked(value: object) -> object:
    """Reduce a secret-bearing field to whether it holds anything.

    Only a non-empty string is redacted: a field named ``...Configured`` is a
    boolean answer, not a secret, and masking it would hide the very thing this
    probe exists to report.
    """

    if isinstance(value, dict):
        return {
            str(key): (
                "<set>"
                if isinstance(item, str)
                and item
                and any(marker in str(key).lower() for marker in _SECRET_MARKERS)
                else _masked(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_masked(item) for item in value]
    return value


def main() -> int:
    print(f"platform HARNESS_WEB_SEARCH_API_KEY set: {bool(os.environ.get('HARNESS_WEB_SEARCH_API_KEY'))}")
    print(f"platform HARNESS_WEB_SEARCH_PROVIDER: {os.environ.get('HARNESS_WEB_SEARCH_PROVIDER', '<unset>')}")
    print(f"platform HARNESS_WEB_ENABLED: {os.environ.get('HARNESS_WEB_ENABLED', '<unset>')}")
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=HEADERS, timeout=60) as client:
        response = client.get("/v1/studio/web-configuration")
        print(f"\nGET /v1/studio/web-configuration for {USER}: HTTP {response.status_code}")
        if response.status_code != 200:
            print(response.text[:400])
            return 1
        print(json.dumps(_masked(response.json()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
