"""Single-source release version audit across every shipped product surface."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_PYTHON_VERSION = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


class PlatformVersionAudit(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    platform_version: str = Field(alias="platformVersion")
    sources: dict[str, str]
    changelog_entry: str = Field(alias="changelogEntry")
    release_state: str = Field(alias="releaseState")


def audit_platform_version(
    root: Path, *, expected: str | None = None, require_released: bool = False
) -> PlatformVersionAudit:
    """Require Python, Web, Helm and release notes to identify one version."""

    repository = root.resolve()
    pyproject = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads(
        (repository / "web/harness-console/package.json").read_text(encoding="utf-8")
    )
    chart = yaml.safe_load(
        (repository / "deploy/helm/agent-harness/Chart.yaml").read_text(encoding="utf-8")
    )
    init_text = (repository / "src/harness/__init__.py").read_text(encoding="utf-8")
    init_match = _PYTHON_VERSION.search(init_text)
    if init_match is None:
        raise ValueError("src/harness/__init__.py does not declare __version__")
    if not all(isinstance(value, dict) for value in (pyproject, package, chart)):
        raise ValueError("release version metadata must be objects")

    pyproject_data = cast(dict[str, object], pyproject)
    package_data = cast(dict[str, object], package)
    chart_data = cast(dict[str, object], chart)
    project = pyproject_data.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml is missing [project]")
    project_data = cast(dict[str, object], project)
    sources = {
        "pyproject": str(project_data.get("version", "")),
        "python": init_match.group(1),
        "web": str(package_data.get("version", "")),
        "helmChart": str(chart_data.get("version", "")),
        "helmApp": str(chart_data.get("appVersion", "")),
    }
    versions = set(sources.values())
    if len(versions) != 1:
        raise ValueError(f"platform versions disagree: {sources}")
    version = versions.pop()
    if _SEMVER.fullmatch(version) is None:
        raise ValueError(f"platform version is not SemVer: {version}")
    if expected is not None and version != expected:
        raise ValueError(f"release version {version} does not match requested version {expected}")

    changelog = repository / "CHANGELOG.md"
    heading = f"## [{version}]"
    changelog_text = changelog.read_text(encoding="utf-8")
    entry = re.search(
        rf"^{re.escape(heading)}(?:\s+-\s+(?P<state>.+))?$", changelog_text, re.MULTILINE
    )
    if entry is None:
        raise ValueError(f"CHANGELOG.md is missing release heading {heading}")
    release_state = entry.group("state") or "unspecified"
    if require_released and re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_state) is None:
        raise ValueError(
            f"CHANGELOG.md release {version} must have an ISO date, found {release_state}"
        )
    return PlatformVersionAudit(
        platformVersion=version,
        sources=sources,
        changelogEntry=heading,
        releaseState=release_state,
    )
