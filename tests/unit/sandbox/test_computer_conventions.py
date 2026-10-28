"""The computer session must state the guest's office runtime once."""

from harness.computer.conventions import OFFICE_PYTHON, WORKSPACE, computer_conventions_overlay


def test_conventions_name_the_pinned_runtime_and_workspace():
    overlay = computer_conventions_overlay()
    assert overlay.tools == ()
    text = overlay.instructions
    assert OFFICE_PYTHON in text
    assert WORKSPACE in text
    # The system interpreter is PEP 668 managed; installing there must be ruled out.
    assert "PEP 668" in text
    assert "-m pip" in text
