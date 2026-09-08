from harness.storage.studio_repository import _summary_fields


def test_summary_fields_supports_task_contract_card_content() -> None:
    summary = _summary_fields(
        {
            "spec": {
                "description": "备用说明",
                "taskContract": {
                    "goal": "监测供应商风险",
                    "outputs": ["每日风险报告"],
                    "constraints": ["不访问外网"],
                },
                "skills": [],
                "builtinTools": ["Read", "Write"],
                "pythonTools": [{"name": "score"}],
                "mcpServers": ["internal-kb"],
            }
        }
    )

    assert summary == {
        "parentDraftId": None,
        "goal": "监测供应商风险",
        "primaryOutput": "每日风险报告",
        "primaryConstraint": "不访问外网",
        "skillCount": 0,
        "toolCount": 4,
        "networkToolsEnabled": False,
    }


def test_summary_fields_falls_back_for_historical_payloads() -> None:
    summary = _summary_fields(
        {
            "spec": {
                "description": "整理历史政策材料",
                "builtinTools": ["Read", "WebSearch"],
            }
        }
    )

    assert summary["goal"] == "整理历史政策材料"
    assert summary["primaryOutput"] == "按 System Prompt 生成可核验结果"
    assert summary["primaryConstraint"] is None
    assert summary["skillCount"] == 0
    assert summary["toolCount"] == 2
    assert summary["networkToolsEnabled"] is True
