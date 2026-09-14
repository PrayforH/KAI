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
    assert first.revision == 2
    first_party = [
        "evidence-reporting",
        "research-synthesis",
        "delivery-verification",
        "document-spreadsheet-production",
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
    assert "skill-authoring-quality" not in package_ids
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


def test_catalog_listing_drops_file_payloads_but_keeps_provenance() -> None:
    """The 技能 page lists packages; shipping asset bytes there stalled it."""

    from harness.studio.models import PlatformSkillCatalogListing
    from harness.studio.platform_skills import platform_skill_catalog_listing

    listing = platform_skill_catalog_listing()
    full = default_platform_skill_catalog()

    assert isinstance(listing, PlatformSkillCatalogListing)
    assert listing.revision == full.revision
    assert [entry.package_id for entry in listing.packages] == [
        package.package_id for package in full.packages
    ]

    full_by_id = {package.package_id: package for package in full.packages}
    for entry in listing.packages:
        package = full_by_id[entry.package_id]
        # Governance fields the drawer and install gate read stay intact.
        assert entry.content_hash == package.content_hash
        assert entry.license == package.license
        assert entry.risk_level == package.risk_level
        assert entry.findings == package.findings
        assert entry.source_url == package.source_url
        assert entry.source_revision == package.source_revision
        assert entry.compatible_runtimes == package.compatible_runtimes
        assert entry.evaluation_case_count == len(package.evaluation_cases)
        assert entry.skill.instructions == package.skill.instructions
        assert entry.skill.file_count == len(package.skill.files)
        assert [file.path for file in entry.skill.files] == [
            file.path for file in package.skill.files
        ]
        # Only the metadata crosses the wire, never the payload.
        for file in entry.skill.files:
            assert set(file.model_dump(by_alias=True)) == {"path", "binary", "sizeBytes"}

    serialized = listing.model_dump_json(by_alias=True)
    payload = full.model_dump_json(by_alias=True)
    assert len(serialized) * 10 < len(payload), "listing must be far smaller than the catalog"


def test_catalog_listing_file_sizes_cover_text_and_binary_payloads() -> None:
    from harness.studio.models import DraftSkillFile
    from harness.studio.platform_skills import _listing_file_size

    digest = "a" * 64
    assert (
        _listing_file_size(
            DraftSkillFile(path="retained.bin", retained=True, sizeBytes=12, contentSha256=digest)
        )
        == 12
    )
    assert _listing_file_size(DraftSkillFile(path="text", content="abcdef")) == 6
    # Two padding characters mean a 4-byte payload, not the 6 raw base64/4*3 gives.
    assert _listing_file_size(DraftSkillFile(path="bin", contentBase64="YWJjZA==")) == 4
    assert _listing_file_size(DraftSkillFile(path="bin", contentBase64="YWJjZGVm")) == 6
