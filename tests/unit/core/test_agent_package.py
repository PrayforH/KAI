import base64
import io
import json
import zipfile
from pathlib import Path
from shutil import copytree

import pytest

from harness.agent_package import (
    AgentBundleValidationError,
    AgentPackageCheckError,
    check_agent_package,
    extract_agent_bundle,
    pack_agent_package,
)
from harness.core.manifest import (
    ManifestValidationError,
    load_manifest,
    materialize_skill_snapshots,
)


def _write_agent(root: Path, *, skills: list[str]) -> Path:
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "system.md").write_text("Evidence-first domain agent")
    rendered_skills = "\n".join(f"    - {skill}" for skill in skills)
    manifest = root / "agent.yaml"
    manifest.write_text(
        f"""apiVersion: harness/v1alpha1
kind: Agent
metadata:
  name: package-agent
  version: 1.0.0
spec:
  runtime: claude-agent-sdk
  model:
    route: new-api-default
    model: claude-sonnet-4-6
    requiredCapabilities: [streaming, tool_use]
  prompt:
    system: prompts/system.md
  skills:
{rendered_skills or '    []'}
  tools:
    - builtin: Read
  permissions:
    policy: production-read-only
  limits:
    maxBudgetUsd: 1
"""
    )
    return manifest


def _write_skill(root: Path, directory: str, *, name: str) -> None:
    skill = root / directory
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"""---
name: {name}
description: Evidence workflow for {name}.
---

# Workflow

Read the supplied evidence and cite it.
"""
    )
    (skill / "references").mkdir()
    (skill / "references" / "quality.md").write_text("Never invent records.\n")


def test_manifest_snapshots_skill_files_and_materializes_sdk_layout(tmp_path: Path) -> None:
    _write_skill(tmp_path, "skills/evidence", name="evidence")
    manifest = _write_agent(tmp_path, skills=["skills/evidence"])

    snapshot = load_manifest(manifest, environment="production")

    assert [skill.name for skill in snapshot.skill_snapshots] == ["evidence"]
    files = snapshot.skill_snapshots[0].files
    assert [file.path for file in files] == ["SKILL.md", "references/quality.md"]
    assert base64.b64decode(files[1].content_base64) == b"Never invent records.\n"

    workspace = tmp_path / "workspace"
    names = materialize_skill_snapshots(snapshot, workspace)

    assert names == ("evidence",)
    assert (workspace / ".claude/skills/evidence/SKILL.md").is_file()
    assert (
        workspace / ".claude/skills/evidence/references/quality.md"
    ).read_text() == "Never invent records.\n"


def test_manifest_rejects_duplicate_skill_names(tmp_path: Path) -> None:
    _write_skill(tmp_path, "skills/one/evidence", name="evidence")
    _write_skill(tmp_path, "skills/two/evidence", name="evidence")
    manifest = _write_agent(
        tmp_path, skills=["skills/one/evidence", "skills/two/evidence"]
    )

    with pytest.raises(ManifestValidationError, match="duplicate Skill name"):
        load_manifest(manifest, environment="production")


def test_manifest_rejects_skill_symlinks(tmp_path: Path) -> None:
    _write_skill(tmp_path, "skills/evidence", name="evidence")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (tmp_path / "skills/evidence/leak.txt").symlink_to(outside)
    manifest = _write_agent(tmp_path, skills=["skills/evidence"])

    with pytest.raises(ManifestValidationError, match="symlink"):
        load_manifest(manifest, environment="production")


def test_manifest_hash_changes_when_skill_support_file_changes(tmp_path: Path) -> None:
    _write_skill(tmp_path, "skills/evidence", name="evidence")
    manifest = _write_agent(tmp_path, skills=["skills/evidence"])
    first = load_manifest(manifest)

    (tmp_path / "skills/evidence/references/quality.md").write_text("Updated policy\n")
    second = load_manifest(manifest)

    assert first.content_hash != second.content_hash
    assert first.skill_snapshots[0].files != second.skill_snapshots[0].files


def test_reproducible_bundle_can_be_safely_extracted(tmp_path: Path) -> None:
    archive, report = pack_agent_package(
        "agents/public-opinion-agent/agent.yaml",
        output_directory=tmp_path / "dist",
    )

    manifest, claimed_hash, claimed_package_hash = extract_agent_bundle(
        archive.read_bytes(), destination=tmp_path / "extracted"
    )

    assert manifest.name == "agent.yaml"
    assert claimed_hash == report.snapshot.content_hash
    assert claimed_package_hash == report.package_hash


def test_package_hash_changes_when_evaluation_changes(tmp_path: Path) -> None:
    package = tmp_path / "public-opinion-agent"
    copytree(Path("agents/public-opinion-agent"), package)
    first = check_agent_package(package / "agent.yaml")
    suite = package / "evals/suite.yaml"
    suite.write_text(suite.read_text() + "\n# Clarified evaluation notes.\n")

    second = check_agent_package(package / "agent.yaml")

    assert first.snapshot.content_hash == second.snapshot.content_hash
    assert first.package_hash != second.package_hash


@pytest.mark.parametrize("prompt", [
    "根据提供的资料回答，注明来源；缺失的信息明确说明，不编造。",
    "# 资料助理\n\n## 回答方式\n先给结论，再给依据。\n\n## 无资料时\n说明缺失信息。",
])
def test_publish_preserves_freeform_prompt(tmp_path: Path, prompt: str) -> None:
    package = tmp_path / "agent"
    copytree(Path("agents/public-opinion-agent"), package)
    (package / "prompts/system.md").write_text(prompt)
    archive, report = pack_agent_package(package / "agent.yaml", output_directory=tmp_path / "dist")
    manifest, _, _ = extract_agent_bundle(archive.read_bytes(), destination=tmp_path / "extracted")
    assert report.snapshot.system_prompt == prompt
    assert load_manifest(manifest).system_prompt == prompt


def test_publish_rejects_whitespace_prompt(tmp_path: Path) -> None:
    package = tmp_path / "agent"
    copytree(Path("agents/public-opinion-agent"), package)
    (package / "prompts/system.md").write_text(" \n ")
    with pytest.raises(AgentPackageCheckError, match="system prompt must not be empty"):
        check_agent_package(package / "agent.yaml")


def test_bundle_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../agent.yaml", "unsafe")
        archive.writestr(
            "bundle.json", json.dumps({"manifestContentHash": "a" * 64})
        )

    with pytest.raises(AgentBundleValidationError, match="unsafe path"):
        extract_agent_bundle(output.getvalue(), destination=tmp_path / "extracted")


def test_bundle_extraction_rejects_file_content_tampering(tmp_path: Path) -> None:
    archive, _ = pack_agent_package(
        "agents/public-opinion-agent/agent.yaml",
        output_directory=tmp_path / "dist",
    )
    tampered = io.BytesIO()
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "prompts/system.md":
                content += b"\nTampered after release.\n"
            target.writestr(info, content)

    with pytest.raises(AgentBundleValidationError, match="does not match provenance"):
        extract_agent_bundle(
            tampered.getvalue(), destination=tmp_path / "tampered-extracted"
        )


def test_production_check_rejects_secret_material_inside_skill(tmp_path: Path) -> None:
    package = tmp_path / "public-opinion-agent"
    copytree(Path("agents/public-opinion-agent"), package)
    skill = package / "skills/public-opinion-analysis/SKILL.md"
    skill.write_text(
        skill.read_text() + "\nAccidentally pasted: sk-live-1234567890abcdefghijklmnop\n"
    )

    with pytest.raises(AgentPackageCheckError, match="secret-like content"):
        check_agent_package(package / "agent.yaml", environment="production")


def test_production_check_scans_secret_content_in_non_text_extension(
    tmp_path: Path,
) -> None:
    package = tmp_path / "public-opinion-agent"
    copytree(Path("agents/public-opinion-agent"), package)
    (package / "evals/private.csv").write_text(
        "credential,sk-live-1234567890abcdefghijklmnop\n"
    )

    with pytest.raises(AgentPackageCheckError, match="secret-like content"):
        check_agent_package(package / "agent.yaml", environment="production")


def test_bundle_extraction_rejects_overlong_path_before_filesystem_write(
    tmp_path: Path,
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"skills/{'x' * 300}/SKILL.md", "unsafe")

    with pytest.raises(AgentBundleValidationError, match="length limit"):
        extract_agent_bundle(output.getvalue(), destination=tmp_path / "extracted")
