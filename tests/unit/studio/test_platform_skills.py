import pytest

from harness.core.errors import ConflictError, NotFoundError
from harness.studio.platform_skills import (
    default_platform_skill_catalog,
    imported_platform_skill,
    platform_skill_package,
)


def test_platform_skill_catalog_is_curated_offline_and_deterministic() -> None:
    first = default_platform_skill_catalog()
    second = default_platform_skill_catalog()

    assert first == second
    assert first.revision == 1
    assert [package.package_id for package in first.packages] == [
        "evidence-reporting",
        "research-synthesis",
        "delivery-verification",
        "document-spreadsheet-production",
        "skill-authoring-quality",
    ]
    assert len({package.content_hash for package in first.packages}) == 5
    for package in first.packages:
        assert package.license == "Apache-2.0"
        assert package.risk_level == "low"
        assert package.findings == ()
        assert package.evaluation_cases[0].tags == (
            "happy",
            f"skill:{package.package_id}",
        )
        assert package.skill.source is not None
        assert package.skill.source.content_hash == package.content_hash
        assert package.skill.source.modified is False
        assert all(not file.path.startswith("scripts/") for file in package.skill.files)


def test_platform_skill_install_material_preserves_package_provenance() -> None:
    package = platform_skill_package("evidence-reporting", 1)
    imported = imported_platform_skill(package)

    assert imported.skill.source is not None
    assert imported.skill.source.package_id == "evidence-reporting"
    assert imported.source_content_hash == package.content_hash
    assert "导入草稿快照" in imported.warnings[0]


def test_open_source_skill_package_pins_upstream_revision() -> None:
    package = platform_skill_package("skill-authoring-quality", 1)

    assert package.license == "Apache-2.0"
    assert package.source_url.startswith("https://github.com/openai/skills/blob/")
    assert package.source_revision == "49f948faa9258a0c61caceaf225e179651397431"
    assert any(file.path == "references/UPSTREAM.md" for file in package.skill.files)


def test_platform_skill_package_rejects_unknown_or_stale_revision() -> None:
    with pytest.raises(NotFoundError):
        platform_skill_package("missing-skill", 1)
    with pytest.raises(ConflictError, match="revision changed"):
        platform_skill_package("evidence-reporting", 2)
