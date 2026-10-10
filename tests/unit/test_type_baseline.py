from collections import Counter
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from scripts.check_type_baseline import Diagnostic, baseline_diagnostics, compare, diagnostics
from scripts.check_type_baseline import main as check_types


def test_new_error_cannot_hide_behind_a_lower_total_count() -> None:
    old = Diagnostic("src/old.py", "reportUnknownVariableType", "unknown old variable")
    new = Diagnostic("src/new.py", "reportArgumentType", "invalid argument")
    added, removed = compare(Counter({new: 1}), Counter({old: 2}))
    assert added == Counter({new: 1})
    assert removed == Counter({old: 2})


def test_duplicate_diagnostics_are_counted_and_fixed_errors_are_allowed() -> None:
    old = Diagnostic("src/old.py", "reportArgumentType", "invalid argument")
    assert compare(Counter({old: 2}), Counter({old: 1}))[0] == Counter({old: 1})
    assert not compare(Counter(), Counter({old: 1}))[0]


def test_report_ignores_line_shifts_and_absolute_checkout_path(tmp_path: Path) -> None:
    report: dict[str, Any] = {
        "generalDiagnostics": [
            {"file": str(tmp_path / "src/code.py"), "severity": "error",
             "rule": "reportArgumentType", "message": "invalid argument",
             "range": {"start": {"line": 90, "character": 1}}},
            {"file": str(tmp_path / "src/code.py"), "severity": "warning"},
        ],
        "summary": {"errorCount": 1},
    }
    expected = Diagnostic("src/code.py", "reportArgumentType", "invalid argument")
    assert diagnostics(report, tmp_path) == Counter({expected: 1})
    report["summary"]["errorCount"] = 0
    with pytest.raises(ValueError, match="summary"):
        diagnostics(report, tmp_path)


def test_invalid_baseline_is_not_treated_as_a_clean_type_check() -> None:
    document: dict[str, Any] = {
        "schema_version": 1, "python_platform": "Linux",
        "diagnostics": [{"file": "src/code.py", "rule": "x", "message": "bad", "count": 0}],
    }
    with pytest.raises(ValueError, match="invalid"):
        baseline_diagnostics(document)


@pytest.mark.parametrize(("returncode", "stdout"), [(2, ""), (0, "not JSON")])
def test_broken_checker_execution_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, stdout: str
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(["pyright"], returncode, stdout=stdout, stderr="broken checker")

    monkeypatch.setattr("scripts.check_type_baseline.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["typecheck", "--report", str(tmp_path / "report.json")])
    assert check_types() == 2
