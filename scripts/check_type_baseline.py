"""Block new Pyright diagnostics, including when the total error count shrinks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, order=True)
class Diagnostic:
    file: str
    rule: str
    message: str


def diagnostics(report: dict[str, Any], root: Path) -> Counter[Diagnostic]:
    result: Counter[Diagnostic] = Counter()
    for item in report["generalDiagnostics"]:
        if item["severity"] != "error":
            continue
        path = Path(item["file"]).resolve().relative_to(root.resolve()).as_posix()
        result[Diagnostic(path, item.get("rule", ""), item["message"])] += 1
    if sum(result.values()) != report["summary"]["errorCount"]:
        raise ValueError("Pyright summary does not match its error diagnostics")
    return result


def baseline_diagnostics(document: dict[str, Any]) -> Counter[Diagnostic]:
    if document["schema_version"] != 1 or document["python_platform"] != "Linux":
        raise ValueError("unsupported type baseline format")
    result: Counter[Diagnostic] = Counter()
    for item in document["diagnostics"]:
        diagnostic = Diagnostic(item["file"], item["rule"], item["message"])
        count = item["count"]
        if diagnostic in result or not isinstance(count, int) or count < 1:
            raise ValueError("duplicate or invalid baseline diagnostic")
        result[diagnostic] = count
    return result


def compare(
    current: Counter[Diagnostic], baseline: Counter[Diagnostic]
) -> tuple[Counter[Diagnostic], Counter[Diagnostic]]:
    return current - baseline, baseline - current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("quality/pyright-baseline.json"))
    parser.add_argument("--report", type=Path, default=Path("dist/quality/pyright.json"))
    arguments = parser.parse_args()
    root = Path.cwd()
    process = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson", "--pythonplatform", "Linux",
         "--pythonpath", sys.executable],
        text=True,
        capture_output=True,
        check=False,
    )
    # Exit 1 represents type errors; crashes/configuration failures are not debt.
    if process.returncode not in (0, 1):
        print(process.stderr or process.stdout, file=sys.stderr)
        return 2
    try:
        report = json.loads(process.stdout)
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        document = json.loads(arguments.baseline.read_text())
        if report["version"] != document["pyright_version"]:
            raise ValueError("Pyright version changed; review the diagnostic baseline")
        current = diagnostics(report, root)
        baseline = baseline_diagnostics(document)
        added, removed = compare(current, baseline)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Type check failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Pyright: {sum(current.values())} existing errors, "
        f"{sum(added.values())} new, {sum(removed.values())} resolved"
    )
    for diagnostic, count in sorted(added.items()):
        print(f"{diagnostic.file}: {diagnostic.rule} ({count}): {diagnostic.message}")
    return 1 if added else 0


if __name__ == "__main__":
    raise SystemExit(main())
