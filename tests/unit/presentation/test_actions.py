"""Stage B: forms and the actions a block may offer.

An action can only ever ask for a turn, so what matters here is that ids are the
platform's, that a form cannot exist without somewhere to submit, and that the
bounds hold.
"""

from __future__ import annotations

from harness.presentation.schema import MAX_ACTIONS, validate_block


def _validate(block: object, *, index: int = 0):
    return validate_block(
        block, run_id="run-1", message_id="assistant-run-1", index=index, origin="model",
        reason_code="model_selected",
    )


def _form_block(**overrides: object) -> dict[str, object]:
    block: dict[str, object] = {
        "version": 1,
        "kind": "form",
        "fallback_text": "需要你补充采购名称与预算。",
        "data": {
            "fields": [
                {"key": "project", "label": "项目名称", "type": "text", "required": True},
                {"key": "budget", "label": "预算", "type": "number"},
                {"key": "level", "label": "优先级", "type": "single", "options": ["高", "中", "低"]},
            ]
        },
        "config": {"submit_label": "提交立项"},
        "actions": [{"kind": "write", "label": "提交立项", "prompt": "请按以下信息发起立项", "style": "primary"}],
    }
    block.update(overrides)
    return block


def test_accepts_a_form_and_normalises_its_fields() -> None:
    presentation = _validate(_form_block())

    assert presentation is not None
    assert presentation.renderer_version == "form/1"
    assert [field["key"] for field in presentation.data["fields"]] == ["project", "budget", "level"]
    assert presentation.data["fields"][0]["required"] is True
    assert presentation.data["fields"][2]["options"] == ["高", "中", "低"]
    assert presentation.config["submit_label"] == "提交立项"


def test_assigns_action_ids_in_declaration_order() -> None:
    presentation = _validate(
        _form_block(
            actions=[
                {"kind": "write", "label": "提交", "prompt": "请提交这些字段"},
                {"kind": "conversation", "label": "先解释口径", "prompt": "请解释这些字段的口径"},
            ]
        )
    )

    assert presentation is not None
    ids = [action["action_id"] for action in presentation.actions]
    assert ids == [f"{presentation.presentation_id}-a0", f"{presentation.presentation_id}-a1"]
    assert [action["kind"] for action in presentation.actions] == ["write", "conversation"]


def test_refuses_a_form_with_no_place_to_submit() -> None:
    assert _validate(_form_block(actions=[])) is None
    assert _validate(_form_block(actions=None)) is None
    assert _validate(
        _form_block(actions=[{"kind": "conversation", "label": "聊聊", "prompt": "请聊聊"}])
    ) is None


def test_accepts_a_table_that_offers_actions() -> None:
    presentation = _validate(
        {
            "version": 1,
            "kind": "table",
            "fallback_text": "三个方案的对比。",
            "data": {"columns": [{"key": "a", "label": "方案"}], "rows": [{"a": "甲"}, {"a": "乙"}]},
            "actions": [{"kind": "conversation", "label": "解释这一行", "prompt": "请展开说明这一行"}],
        }
    )

    assert presentation is not None
    assert presentation.actions[0]["kind"] == "conversation"
    assert presentation.actions[0]["style"] == "default"


def test_refuses_an_action_without_a_prompt_to_send() -> None:
    assert _validate(
        _form_block(actions=[{"kind": "write", "label": "提交"}])
    ) is None


def test_refuses_a_local_action_the_platform_does_not_round_trip() -> None:
    """Client-side behaviour is the component's own affordance, not a declared action."""
    assert _validate(
        _form_block(actions=[{"kind": "local", "label": "排序"}])
    ) is None


def test_refuses_more_actions_than_the_protocol_carries() -> None:
    too_many = [
        {"kind": "conversation", "label": f"动作{index}", "prompt": "请继续"}
        for index in range(MAX_ACTIONS + 1)
    ]

    assert _validate(_form_block(actions=too_many)) is None


def test_refuses_a_choice_field_without_real_options() -> None:
    assert _validate(
        _form_block(
            data={"fields": [{"key": "level", "label": "优先级", "type": "single", "options": ["高"]}]}
        )
    ) is None
    assert _validate(
        _form_block(
            data={"fields": [{"key": "note", "label": "说明", "type": "text", "options": ["a", "b"]}]}
        )
    ) is None


def test_refuses_duplicate_field_keys() -> None:
    assert _validate(
        _form_block(
            data={
                "fields": [
                    {"key": "a", "label": "甲", "type": "text"},
                    {"key": "a", "label": "乙", "type": "text"},
                ]
            }
        )
    ) is None
