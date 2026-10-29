from pathlib import Path

from scripts.check_agent_packages import check_catalog


def test_repository_agent_catalog_is_production_ready(tmp_path: Path) -> None:
    reports = check_catalog(Path("agents"), output_directory=tmp_path)

    assert {report.snapshot.manifest.metadata.name for report in reports} == {
        "archive-assistant-agent",
        "archive-file-classifier-agent",
        "echo-agent",
        "govdoc-writer-agent",
        "general-analyst",
        "general-researcher",
        "general-reviewer",
        "helper-agent",
        "lead-agent",
        "networked-knowledge-research-agent",
        "ontology-data-agent",
        "public-opinion-agent",
        "similar-case-analysis-agent",
    }
    assert len(list(tmp_path.glob("*.zip"))) == 13
