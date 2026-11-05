"""Conventions are stated once and only where the image carries them."""

from harness.computer.conventions import (
    OFFICE_PYTHON,
    WORKSPACE,
    computer_conventions_overlay,
    office_conventions_overlay,
)


def test_base_conventions_name_the_workspace_without_promising_office():
    overlay = computer_conventions_overlay()
    assert overlay.tools == ()
    text = overlay.instructions
    assert WORKSPACE in text
    # The base overlay must not promise an interpreter the image may lack.
    assert OFFICE_PYTHON not in text
    assert "PEP 668" not in text


def test_office_conventions_name_the_pinned_runtime():
    overlay = office_conventions_overlay()
    text = overlay.instructions
    assert OFFICE_PYTHON in text
    # The system interpreter is PEP 668 managed; installing there must be ruled out.
    assert "PEP 668" in text
    assert "-m pip" in text
