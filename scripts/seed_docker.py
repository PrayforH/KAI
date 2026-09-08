"""Idempotently publish production Agent bundles into a running Docker API."""

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

import yaml

from harness.agent_package import pack_agent_package
from harness.core.manifest import load_manifest
from harness.evals.suite import EvalSuite


class StudioDraftNotReadyError(ValueError):
    """An optional Studio seed cannot be published in this environment."""


def _publish_bundle(
    *,
    api_url: str,
    tenant_id: str,
    user_id: str,
    api_token: str,
    manifest: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="harness-seed-") as directory:
        archive, _ = pack_agent_package(manifest, output_directory=directory)
        headers = {
            "Content-Type": "application/zip",
            "X-Tenant-ID": tenant_id,
            "X-User-ID": user_id,
        }
        if api_token:
            headers["X-Harness-Service-Token"] = api_token
        request = urllib.request.Request(
            f"{api_url}/v1/agents/bundles",
            data=archive.read_bytes(),
            headers=headers,
            method="POST",
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.status == 201:
                        return
            except urllib.error.HTTPError as error:
                if error.code == 409:
                    return
                if error.code < 500:
                    raise
            except urllib.error.URLError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("Harness API was not ready for Agent publication")
            time.sleep(1)


def _request_json(
    *,
    api_url: str,
    tenant_id: str,
    user_id: str,
    api_token: str,
    path: str,
    method: str = "GET",
    body: object | None = None,
) -> object:
    headers = {
        "Accept": "application/json",
        "X-Tenant-ID": tenant_id,
        "X-User-ID": user_id,
    }
    if api_token:
        headers["X-Harness-Service-Token"] = api_token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Studio API {method} {path} failed with HTTP {error.code}: {detail}"
        ) from error


def _required_str(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"JSON response field is not a non-empty string: {key}")
    return item


def _required_int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int):
        raise ValueError(f"JSON response field is not an integer: {key}")
    return item


def _numeric_version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _skill_payload(path: Path) -> dict[str, object]:
    skill_md = path / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Skill frontmatter is missing: {skill_md}")
    end = next(
        index for index, line in enumerate(lines[1:], start=1)
        if line.strip() == "---"
    )
    frontmatter = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(frontmatter, dict):
        raise ValueError(f"Skill frontmatter is invalid: {skill_md}")
    metadata = cast(dict[str, object], frontmatter)
    files: list[dict[str, str]] = []
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child == skill_md:
            continue
        files.append(
            {
                "path": child.relative_to(path).as_posix(),
                "content": child.read_text(encoding="utf-8"),
            }
        )
    return {
        "name": str(metadata["name"]),
        "description": str(metadata["description"]).strip(),
        "instructions": "\n".join(lines[end + 1 :]).strip(),
        "files": files,
    }


def studio_spec_from_manifest(manifest_path: Path) -> dict[str, object]:
    snapshot = load_manifest(manifest_path, environment="production")
    manifest = snapshot.manifest
    root = manifest_path.parent
    labels = manifest.metadata.labels
    template = labels.get("template", "analyst")
    if template not in {"analyst", "operator", "orchestrator"}:
        raise ValueError(f"Unsupported Studio template: {template}")
    suite = EvalSuite.model_validate(
        yaml.safe_load((root / "evals" / "suite.yaml").read_text(encoding="utf-8"))
    )
    limits = manifest.spec.limits.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    return {
        "name": manifest.metadata.name,
        "version": manifest.metadata.version,
        "displayName": labels.get("display-name", manifest.metadata.name),
        "description": labels.get("description", manifest.metadata.name),
        "domain": labels.get("domain", manifest.metadata.name),
        "template": template,
        "runtime": manifest.spec.runtime,
        "model": {
            "routeId": manifest.spec.model.route,
            "model": manifest.spec.model.model,
            "reasoningEffort": labels.get("codex-reasoning-effort"),
            "fallbackRouteId": manifest.spec.model.fallback_route,
            "fallbackModel": manifest.spec.model.fallback_model,
            "requiredCapabilities": list(
                manifest.spec.model.required_capabilities
            ),
        },
        "systemPrompt": snapshot.system_prompt,
        "skills": [
            _skill_payload((root / relative).resolve())
            for relative in manifest.spec.skills
        ],
        "builtinTools": [
            tool.builtin for tool in manifest.spec.tools if tool.builtin is not None
        ],
        "mcpServers": [
            tool.mcp for tool in manifest.spec.tools if tool.mcp is not None
        ],
        "subagents": [
            {
                "alias": subagent.runtime_name,
                "ref": subagent.ref,
                "responsibility": (
                    subagent.description or f"Delegate to {subagent.runtime_name}"
                ),
                "background": subagent.background,
            }
            for subagent in manifest.spec.subagents
        ],
        "permissionPolicy": manifest.spec.permissions.policy,
        "executionProfile": labels.get("execution-profile", "isolated-default"),
        "evaluationEnabled": labels.get("evaluation-enabled", "true").lower()
        != "false",
        "workspace": {
            "restoreSession": manifest.spec.workspace.restore_session,
            "archiveOnComplete": manifest.spec.workspace.archive_on_complete,
        },
        "limits": {
            key: value
            for key, value in limits.items()
            if key != "maxSubagentDepth"
        },
        "evaluationCases": [
            case.model_dump(mode="json", by_alias=True, exclude_none=True)
            for case in suite.cases
        ],
    }


def _sync_studio_agent(
    *,
    api_url: str,
    tenant_id: str,
    user_id: str,
    api_token: str,
    manifest: Path,
) -> None:
    spec = studio_spec_from_manifest(manifest)
    summaries = _request_json(
        api_url=api_url,
        tenant_id=tenant_id,
        user_id=user_id,
        api_token=api_token,
        path="/v1/studio/drafts",
    )
    if not isinstance(summaries, list):
        raise ValueError("Studio draft list response is invalid")
    summary: dict[str, object] | None = None
    for raw_item in cast(list[object], summaries):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        if item.get("name") == spec["name"]:
            summary = item
            break
    if summary is None:
        draft = _request_json(
            api_url=api_url,
            tenant_id=tenant_id,
            user_id=user_id,
            api_token=api_token,
            path="/v1/studio/drafts",
            method="POST",
            body={
                "name": spec["name"],
                "domain": spec["domain"],
                "displayName": spec["displayName"],
                "description": spec["description"],
                "template": spec["template"],
            },
        )
    else:
        draft_id = _required_str(summary, "draftId")
        draft = _request_json(
            api_url=api_url,
            tenant_id=tenant_id,
            user_id=user_id,
            api_token=api_token,
            path=f"/v1/studio/drafts/{draft_id}",
        )
    if not isinstance(draft, dict):
        raise ValueError("Studio draft response is invalid")
    draft_object = cast(dict[str, object], draft)
    published_version = draft_object.get("publishedVersion")
    if published_version == spec["version"]:
        return
    published_key = _numeric_version(published_version)
    seed_key = _numeric_version(spec["version"])
    if published_key is not None and seed_key is not None and published_key > seed_key:
        # Seed manifests establish a baseline. Never roll a user-managed draft back
        # over a newer immutable release during a repeated Compose startup.
        return
    draft_id = _required_str(draft_object, "draftId")
    revision = _required_int(draft_object, "revision")
    draft = _request_json(
        api_url=api_url,
        tenant_id=tenant_id,
        user_id=user_id,
        api_token=api_token,
        path=f"/v1/studio/drafts/{draft_id}",
        method="PUT",
        body={"expectedRevision": revision, "spec": spec},
    )
    if not isinstance(draft, dict):
        raise ValueError("Studio draft replacement response is invalid")
    draft_object = cast(dict[str, object], draft)
    draft_id = _required_str(draft_object, "draftId")
    revision = _required_int(draft_object, "revision")
    validation = _request_json(
        api_url=api_url,
        tenant_id=tenant_id,
        user_id=user_id,
        api_token=api_token,
        path=f"/v1/studio/drafts/{draft_id}/validate",
        method="POST",
    )
    validation_object = (
        cast(dict[str, object], validation)
        if isinstance(validation, dict)
        else None
    )
    if validation_object is None or validation_object.get("ready") is not True:
        raise StudioDraftNotReadyError(
            f"Studio draft is not ready: {spec['name']} "
            f"{validation_object.get('issues') if validation_object else validation}"
        )
    _request_json(
        api_url=api_url,
        tenant_id=tenant_id,
        user_id=user_id,
        api_token=api_token,
        path=f"/v1/studio/drafts/{draft_id}/publish",
        method="POST",
        body={"expectedRevision": revision},
    )


def _sync_optional_studio_agent(
    *,
    api_url: str,
    tenant_id: str,
    user_id: str,
    api_token: str,
    manifest: Path,
) -> None:
    try:
        _sync_studio_agent(
            api_url=api_url,
            tenant_id=tenant_id,
            user_id=user_id,
            api_token=api_token,
            manifest=manifest,
        )
    except StudioDraftNotReadyError as error:
        # Optional examples may depend on private MCP services that are not
        # present in every deployment. Keep the draft and surface the reason,
        # but do not block the rest of the application from starting.
        print(f"WARNING: optional Studio seed skipped: {error}", flush=True)


def main() -> None:
    api_url = os.getenv("HARNESS_API_URL", "http://api:8000").rstrip("/")
    tenant_id = os.getenv("HARNESS_TENANT_ID", "local")
    user_id = os.getenv("HARNESS_USER_ID", "system")
    api_token = os.getenv("HARNESS_API_BEARER_TOKEN", "")
    raw_manifests = os.getenv(
        "HARNESS_SEED_AGENT_MANIFESTS",
        (
            "/app/agents/lead-agent/agent.yaml,"
            "/app/agents/helper-agent/agent.yaml,"
            "/app/agents/echo-agent/agent.yaml,"
            "/app/agents/archive-file-classifier-agent/agent.yaml"
        ),
    )
    for value in raw_manifests.split(","):
        value = value.strip()
        if not value:
            continue
        _publish_bundle(
            api_url=api_url,
            tenant_id=tenant_id,
            user_id=user_id,
            api_token=api_token,
            manifest=Path(value),
        )
    raw_studio_manifests = os.getenv(
        "HARNESS_SEED_STUDIO_MANIFESTS",
        (
            "/app/agents/similar-case-analysis-agent/agent.yaml,"
            "/app/agents/govdoc-writer-agent/agent.yaml,"
            "/app/agents/archive-assistant-agent/agent.yaml"
        ),
    )
    for value in raw_studio_manifests.split(","):
        value = value.strip()
        if not value:
            continue
        _sync_studio_agent(
            api_url=api_url,
            tenant_id=tenant_id,
            user_id=user_id,
            api_token=api_token,
            manifest=Path(value),
        )
    raw_optional_studio_manifests = os.getenv(
        "HARNESS_SEED_OPTIONAL_STUDIO_MANIFESTS",
        (
            "/app/agents/public-opinion-agent/agent.yaml,"
            "/app/agents/networked-knowledge-research-agent/agent.yaml"
        ),
    )
    for value in raw_optional_studio_manifests.split(","):
        value = value.strip()
        if not value:
            continue
        _sync_optional_studio_agent(
            api_url=api_url,
            tenant_id=tenant_id,
            user_id=user_id,
            api_token=api_token,
            manifest=Path(value),
        )


if __name__ == "__main__":
    main()
