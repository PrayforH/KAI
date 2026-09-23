import base64
import hashlib
import json

from harness.core.manifest import SkillFileSnapshot, SkillSnapshot, load_manifest
from harness.studio.version_source import version_source


def test_version_sources_preserve_assets_and_compare_content_not_version_numbers() -> None:
    snapshot = load_manifest("agents/echo-agent/agent.yaml")
    old = version_source(snapshot)
    bumped = snapshot.model_copy(update={"manifest": snapshot.manifest.model_copy(update={
        "metadata": snapshot.manifest.metadata.model_copy(update={"version": "9.0.0"})
    })})
    assert old.files == version_source(bumped).files
    assert "version" not in json.loads(old.files[0].content)["metadata"]
    raw = b"\xff\x00"
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = snapshot.model_copy(update={"skill_snapshots": (SkillSnapshot(
        name="image-skill", description="Images", source="skills/image-skill",
        content_hash=digest, files=(SkillFileSnapshot(
            path="assets/image.png", content_base64=base64.b64encode(raw).decode(),
            sha256=digest, size_bytes=len(raw),
        ),),
    ),)})
    file = next(f for f in version_source(snapshot).files if f.path.endswith("image.png"))
    assert file.content is None and file.digest == digest and file.size == 2
    assert "二进制" in file.unavailable


def test_version_source_bounds_text_without_losing_change_digests(monkeypatch) -> None:
    monkeypatch.setattr("harness.studio.version_source.PROJECT_SOURCE_TOTAL_LIMIT", 1)
    result = version_source(load_manifest("agents/echo-agent/agent.yaml"))
    assert all(f.content is None and f.digest and f.unavailable for f in result.files)
