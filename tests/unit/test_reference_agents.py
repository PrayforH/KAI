from pathlib import Path

from harness.agent_package import check_agent_package


def test_public_opinion_reference_agent_passes_production_package_gates() -> None:
    manifest = Path("agents/public-opinion-agent/agent.yaml")

    report = check_agent_package(manifest, environment="production")

    assert report.snapshot.manifest.metadata.name == "public-opinion-agent"
    assert report.snapshot.manifest.metadata.version == "0.3.22"
    assert report.snapshot.manifest.metadata.labels["codex-reasoning-effort"] == "low"
    assert report.snapshot.manifest.spec.model.model == "deepseek-v4-flash-vision-exp"
    assert report.snapshot.manifest.spec.limits.max_tool_calls == 512
    assert report.snapshot.manifest.spec.limits.timeout_seconds == 7200
    assert "涉非舆情分析智能体" in report.snapshot.system_prompt
    assert "0.3.22 未接入知识库或公网搜索" in report.snapshot.system_prompt
    assert "只指涉及非法集资及相关非法金融风险" in report.snapshot.system_prompt
    assert "绝不表示“涉及非洲”" in report.snapshot.system_prompt
    assert "首个模型动作必须直接读取必需 Skill 契约" in report.snapshot.system_prompt
    assert "Mandatory skill preflight" in report.snapshot.system_prompt
    assert report.snapshot.manifest.spec.permissions.policy == "production-orchestrator"
    assert "Edit" in {
        tool.builtin for tool in report.snapshot.manifest.spec.tools
    }
    assert "Bash" in {
        tool.builtin for tool in report.snapshot.manifest.spec.tools
    }
    assert {
        subagent.alias for subagent in report.snapshot.manifest.spec.subagents
    } == {"fact-researcher", "audience-analyst", "industry-analyst"}
    assert [skill.name for skill in report.snapshot.skill_snapshots] == [
        "public-opinion-analysis"
    ]
    assert {
        Path(file.path).name
        for file in report.snapshot.skill_snapshots[0].files
    } >= {
        "query-contract.md",
        "report-contract.md",
        "report-rendering.md",
        "risk-rubric.md",
    }
    assert {tag for case in report.eval_suite.cases for tag in case.tags} >= {
        "happy",
        "ambiguous",
        "safety",
        "query",
        "artifact",
        "depth",
        "terminology",
    }
    shorthand_case = next(
        case for case in report.eval_suite.cases if case.id == "shorthand-disambiguation"
    )
    assert shorthand_case.expect.output_contains == ("喜来鹿", "非法集资")
    query_contract = Path(
        "agents/public-opinion-agent/skills/public-opinion-analysis/"
        "references/query-contract.md"
    ).read_text(encoding="utf-8")
    assert "不表示“涉及非洲”" in query_contract
    assert "必须剔除并记录为歧义纠偏" in query_contract
    artifact_case = next(
        case for case in report.eval_suite.cases if case.id == "html-report-artifact"
    )
    assert "Bash" not in artifact_case.expect.forbidden_tools
    assert artifact_case.expect.output_contains == (
        "outputs/门店服务与产品交付-舆情风险分析报告.html",
    )
    rendering = Path(
        "agents/public-opinion-agent/skills/public-opinion-analysis/"
        "references/report-rendering.md"
    ).read_text(encoding="utf-8")
    assert "outputs/<主体或事件>-舆情风险分析报告.html" in rendering
    assert "outputs/public-opinion-report.html" not in rendering
