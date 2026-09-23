from io import BytesIO
from zipfile import ZipFile

import pytest

from harness.studio.skill_import import (
    MAX_SKILL_UPLOAD_BYTES,
    SkillImportError,
    import_skill,
)


def skill_zip(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def binary_skill_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_imports_a_declarative_skill_as_low_risk() -> None:
    imported = import_skill(
        skill_zip(
            {
                "research/SKILL.md": (
                    "---\n"
                    "name: source-research\n"
                    "description: Research with cited sources.\n"
                    "---\n\n"
                    "# Workflow\n\nFind, compare, and cite primary sources.\n"
                ),
                "research/references/checklist.md": "Check dates and original sources.\n",
            }
        ),
        filename="research.zip",
    )

    assert imported.skill.name == "source-research"
    assert imported.risk_level == "low"
    assert imported.skill.files[0].path == "references/checklist.md"
    assert imported.findings == ()
    assert len(imported.source_content_hash) == 64


def test_imports_scripts_but_marks_them_for_review() -> None:
    imported = import_skill(
        skill_zip(
            {
                "ppt/SKILL.md": (
                    "---\nname: ppt-builder\ndescription: Build slides.\n---\n\n"
                    "Run the renderer and publish the deck.\n"
                ),
                "ppt/scripts/render.py": "print('render')\n",
                "ppt/requirements.txt": "python-pptx==1.0.2\n",
            }
        ),
        filename="ppt.zip",
    )

    assert imported.risk_level == "review"
    assert imported.findings == (
        "包含可执行脚本：scripts/render.py",
        "包含依赖声明：requirements.txt",
    )
    assert any("权限门" in warning for warning in imported.warnings)


def test_preserves_binary_assets_for_large_skill_packages() -> None:
    imported = import_skill(
        binary_skill_zip(
            {
                "ppt/SKILL.md": (
                    b"---\nname: ppt-builder\ndescription: Build slides.\n---\n\n"
                    b"Use the packaged visual assets.\n"
                ),
                "ppt/assets/template.png": b"\x89PNG\r\n\x1a\n\x00\xff",
            }
        ),
        filename="ppt.zip",
    )

    asset = imported.skill.files[0]
    assert MAX_SKILL_UPLOAD_BYTES == 100 * 1024 * 1024
    assert asset.path == "assets/template.png"
    assert asset.content is None
    assert asset.content_base64 is not None
    assert imported.warnings == ("已保留 1 个二进制 asset",)


def test_rejects_path_traversal_and_secret_files() -> None:
    with pytest.raises(SkillImportError, match="不安全路径"):
        import_skill(
            skill_zip(
                {
                    "safe/SKILL.md": (
                        "---\nname: safe-skill\ndescription: Safe.\n---\n\nDo work.\n"
                    ),
                    "../escape.txt": "escape",
                }
            ),
            filename="unsafe.zip",
        )

    with pytest.raises(SkillImportError, match="凭据类文件"):
        import_skill(
            skill_zip(
                {
                    "safe/SKILL.md": (
                        "---\nname: safe-skill\ndescription: Safe.\n---\n\nDo work.\n"
                    ),
                    "safe/.env": "TOKEN=secret",
                }
            ),
            filename="secret.zip",
        )


def test_imports_a_single_markdown_skill_and_normalizes_name() -> None:
    imported = import_skill(
        (
            b"---\nname: PPT Master\ndescription: Build a presentation.\n---\n\n"
            b"Create the requested deck.\n"
        ),
        filename="SKILL.md",
    )

    assert imported.skill.name == "ppt-master"
    assert imported.warnings == ("Skill 名称已规范化为 ppt-master",)


@pytest.mark.parametrize("entry", ["SKILL.md", "skill.md", "Skill.md"])
def test_repository_zip_prefers_nested_skill_over_hidden_maintainer_skills(entry: str) -> None:
    markdown = "---\nname: archify\ndescription: Draw diagrams.\n---\nUse assets/template.html.\n"
    imported = import_skill(skill_zip({
        f"archify-main/archify/{entry}": markdown,
        "archify-main/archify/assets/template.html": "<html>template</html>",
        "archify-main/archify/scripts/render.mjs": "// renderer",
        "archify-main/.agents/skills/archify-review/SKILL.md": markdown,
        "archify-main/.claude/skills/reviewer/SKILL.md": markdown,
        "archify-main/README.md": "Repository readme, not part of the skill.",
    }), filename="archify-main.zip")
    assert imported.skill.name == "archify"
    assert {file.path for file in imported.skill.files} == {
        "assets/template.html", "scripts/render.mjs",
    }


def test_imports_skill_from_hidden_directory_when_it_is_the_only_candidate() -> None:
    imported = import_skill(skill_zip({
        "repo/.agents/skills/reviewer/SKILL.md":
            "---\nname: reviewer\ndescription: Review changes.\n---\nCheck changes.\n",
        "repo/.agents/skills/reviewer/references/checklist.md": "Check compatibility.",
    }), filename="reviewer.zip")
    assert imported.skill.name == "reviewer"
    assert [file.path for file in imported.skill.files] == ["references/checklist.md"]


@pytest.mark.parametrize("paths", [
    ["repo/first/SKILL.md", "repo/second/SKILL.md"],
    ["repo/.agents/skills/first/SKILL.md", "repo/.agents/skills/second/SKILL.md"],
    ["repo/first/SKILL.md", "repo/first/skill.md"],
])
def test_multiple_skill_entries_report_candidates_instead_of_claiming_missing(paths) -> None:
    markdown = "---\nname: example\ndescription: Example.\n---\nDo work.\n"
    with pytest.raises(SkillImportError, match="多个技能入口") as error:
        import_skill(skill_zip(dict.fromkeys(paths, markdown)), filename="repo.zip")
    for path in paths:
        assert path in str(error.value)


def test_missing_skill_entry_explains_nested_directories_are_supported() -> None:
    with pytest.raises(SkillImportError, match="未找到 SKILL.md（支持子目录）"):
        import_skill(skill_zip({"repo/README.md": "No skill entry."}), filename="repo.zip")
