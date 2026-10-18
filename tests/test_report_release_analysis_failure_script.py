"""Exercise safe failure diagnostics through both real CLI entrypoints."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import report_release_analysis_failure as reporter
from tests.conftest import assert_output

DATA_PATH = Path(__file__).parent / "data" / "report_release_analysis_failure"
EXPECTED_PATH = Path(__file__).parent / "data" / "expected" / "report_release_analysis_failure"
CASES = json.loads((DATA_PATH / "cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
@pytest.mark.parametrize("entrypoint", ["subprocess", "main"])
def test_failure_diagnostics(
    case: dict[str, str | int],
    entrypoint: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Untrusted records can select fixed labels but cannot publish their content."""
    execution_path = DATA_PATH / str(case["input"])
    if repeat := case.get("repeat"):
        execution_path = tmp_path / "execution.json"
        execution_path.write_bytes((DATA_PATH / str(case["input"])).read_bytes() * int(repeat))
    args = ["--execution-path", str(execution_path)]
    if entrypoint == "subprocess":
        result = subprocess.run(
            [sys.executable, "scripts/report_release_analysis_failure.py", *args],
            capture_output=True,
            check=True,
            cwd=Path(__file__).parents[1],
            text=True,
        )
        stdout, stderr = result.stdout, result.stderr
    else:
        reporter.main(args)
        captured = capsys.readouterr()
        stdout, stderr = captured.out, captured.err
    assert_output(stdout, EXPECTED_PATH / f"{case['expected']}.txt")
    assert_output(stderr, EXPECTED_PATH / "empty.txt")
