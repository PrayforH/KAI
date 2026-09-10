from pathlib import Path

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
    first_party = [
        "evidence-reporting",
        "research-synthesis",
        "delivery-verification",
        "document-spreadsheet-production",
        "skill-authoring-quality",
    ]
    package_ids = [package.package_id for package in first.packages]
    # First-party reviewed packs, plus the vendored upstream Skill library.
    assert set(first_party) <= set(package_ids)
    assert {"minimax-docx", "minimax-xlsx", "minimax-pdf", "pptx-generator"} <= set(
        package_ids
    )
    assert {"skill-creator", "mcp-builder", "canvas-design", "theme-factory"} <= set(
        package_ids
    )
    assert len(package_ids) == len(set(package_ids))
    assert len({package.content_hash for package in first.packages}) == len(package_ids)
    for package in first.packages:
        assert package.evaluation_cases[0].tags == (
            "happy",
            f"skill:{package.package_id}",
        )
        assert package.skill.source is not None
        assert package.skill.source.content_hash == package.content_hash
        assert package.skill.source.modified is False
        # Every vendored package must carry its upstream license text.
    for package in (item for item in first.packages if item.package_id in first_party):
        assert package.license == "Apache-2.0"
        assert package.risk_level == "low"
        assert package.findings == ()
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


def test_vendored_skills_preserve_upstream_license_and_provenance() -> None:
    from harness.studio.vendor_skills import VENDORED_SOURCES, load_vendored_skills

    skills = {skill.name: skill for skill in load_vendored_skills()}
    assert skills, "vendored Skill tree must resolve"

    catalog = {
        package.package_id: package for package in default_platform_skill_catalog().packages
    }
    for name, (source_url, revision, license_name) in VENDORED_SOURCES.items():
        assert name in skills
        package = catalog[name]
        assert package.license == license_name
        assert package.source_revision == revision
        assert package.source_url == source_url
        # Attribution must travel with the shipped files.
        license_files = [
            file.path for file in skills[name].files if Path(file.path).name.startswith("LICENSE")
        ]
        if license_name != "MIT":
            assert license_files, f"{name} must ship its upstream license text"

    # MiniMax ships the office documents; the Anthropic document skills are
    # proprietary and must never be vendored.
    assert not {"docx", "pptx", "xlsx", "pdf", "doc-coauthoring"} & set(skills)
    # Vendored package descriptions must fit the model budget.
    for skill in skills.values():
        assert 0 < len(skill.description) <= 500
