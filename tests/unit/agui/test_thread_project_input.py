"""The task-move input must accept the console's camelCase payload."""

from harness.agui.routes import AguiThreadUpdateInput


def test_camel_case_project_id_is_recognised() -> None:
    body = AguiThreadUpdateInput.model_validate({"projectId": "project_1"})
    assert body.project_id == "project_1"
    assert "project_id" in body.model_fields_set


def test_clearing_the_project_is_a_requested_update() -> None:
    body = AguiThreadUpdateInput.model_validate({"projectId": None})
    assert body.project_id is None
    assert "project_id" in body.model_fields_set


def test_snake_case_still_works() -> None:
    body = AguiThreadUpdateInput.model_validate({"project_id": "project_2"})
    assert body.project_id == "project_2"


def test_unrelated_payload_does_not_request_a_move() -> None:
    body = AguiThreadUpdateInput.model_validate({"pinned": True})
    assert "project_id" not in body.model_fields_set
