"""Project Skill snapshots are the same immutable package the compiler uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.adapters.memory import InMemoryArtifactStore
from harness.application.agent_assets import stage_project_skill_snapshots
from harness.core.errors import ConflictError, NotFoundError
from harness.core.manifest import merge_skill_snapshot_set
from harness.projects.skill_snapshots import ProjectSkillSnapshots
from harness.storage.skill_blobs import SkillBlobStore
from harness.studio.platform_skills import skill_snapshots_for_references


def test_reference_resolves_to_a_skill_snapshot_with_skill_md() -> None:
    snapshots = skill_snapshots_for_references(("pptx-generator@1",), "claude-agent-sdk")
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.name == "pptx-generator"
    assert snapshot.content_hash and len(snapshot.content_hash) == 64
    skill_md = next(file for file in snapshot.files if file.path == "SKILL.md")
    assert skill_md.content_base64


def test_unknown_stale_and_incompatible_references_are_skipped() -> None:
    snapshots = skill_snapshots_for_references(
        ("missing@1", "pptx-generator@99", "pptx-generator"),
        "claude-agent-sdk",
    )
    assert snapshots == ()


def test_merge_keeps_agent_skills_on_a_name_collision(tmp_path: Path) -> None:
    snapshots = skill_snapshots_for_references(("pptx-generator@1",), "claude-agent-sdk")
    agent_root = tmp_path / ".claude" / "skills" / "pptx-generator"
    agent_root.mkdir(parents=True)
    (agent_root / "SKILL.md").write_text("agent-owned", encoding="utf-8")

    staged, skipped = merge_skill_snapshot_set(snapshots, tmp_path)

    assert staged == ()
    assert skipped == ("pptx-generator",)
    assert (agent_root / "SKILL.md").read_text(encoding="utf-8") == "agent-owned"


def test_codex_stage_mirrors_only_project_skills(tmp_path: Path) -> None:
    snapshots = skill_snapshots_for_references(("pptx-generator@1",), "codex-app-server")
    staged, skipped = stage_project_skill_snapshots(
        snapshots, workspace=tmp_path, runtime="codex-app-server"
    )
    assert skipped == ()
    assert staged == ("pptx-generator",)
    assert (tmp_path / ".claude" / "skills" / "pptx-generator" / "SKILL.md").exists()
    assert (tmp_path / ".agents" / "skills" / "harness-pptx-generator" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_captured_run_skills_survive_catalog_revision_and_verify_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from harness.projects import skill_snapshots as module

    storage = InMemoryArtifactStore()
    snapshots = ProjectSkillSnapshots(SkillBlobStore(storage))
    pinned = await snapshots.capture("tenant-a", ("pptx-generator@1",), "claude-agent-sdk")
    original = await snapshots.restore("tenant-a", pinned)
    assert "files" not in pinned[0] and pinned[0]["_skillBlob"]
    def unavailable(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()

    monkeypatch.setattr(module, "skill_snapshots_for_references", unavailable)
    restarted = ProjectSkillSnapshots(SkillBlobStore(storage))
    restored = await restarted.restore("tenant-a", pinned)
    assert restored == original
    assert stage_project_skill_snapshots(restored, workspace=tmp_path, runtime="claude-agent-sdk")[
        0
    ] == ("pptx-generator",)
    with pytest.raises(NotFoundError, match="not found"):
        await restarted.restore("tenant-b", pinned)


def test_new_runs_fail_explicitly_for_stale_project_skill_versions():
    with pytest.raises(ConflictError, match="update the project selection"):
        skill_snapshots_for_references(("pptx-generator@99",), "claude-agent-sdk", strict=True)
