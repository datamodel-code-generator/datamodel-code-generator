"""Exercise CI coverage recording and validation through the CLI without mocks."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from scripts import ci_coverage
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/ci_coverage"
EXPECTED = ROOT / "tests/data/expected/ci_coverage"
CASES = json.loads((DATA / "cases.json").read_text(encoding="utf-8"))


def _prepare_case(case: str, tmp_path: Path) -> tuple[str, Path]:  # ruff: ignore[too-many-branches]
    """Materialize each artifact fault using actual files."""
    root = tmp_path / "inputs"
    root.mkdir()
    payload = (DATA / "coverage.txt").read_bytes()
    for name in sorted(ci_coverage.EXPECTED_NAMES):
        directory = root / name
        directory.mkdir()
        path = directory / name
        path.write_bytes(payload)
        ci_coverage.main(["record", str(path), "--run-id", "123", "--sha", "abc"])
    name = min(ci_coverage.EXPECTED_NAMES)
    directory = root / name
    path = directory / name
    metadata = directory / f"{name}.json"
    action = "prepare"
    target = root
    match case:
        case "missing":
            path.unlink()
            metadata.unlink()
            directory.rmdir()
        case "unexpected":
            (root / "extra").mkdir()
        case "duplicate":
            (directory / "duplicate").write_bytes(payload)
        case "empty":
            path.write_bytes(b"")
        case "wrong-run" | "wrong-sha" | "wrong-name":
            data = json.loads(metadata.read_text(encoding="utf-8"))
            data[{"wrong-run": "run_id", "wrong-sha": "sha", "wrong-name": "name"}[case]] = "foreign"
            metadata.write_text(json.dumps(data), encoding="utf-8")
        case "invalid-json":
            metadata.write_text("{", encoding="utf-8")
        case "artifact-file":
            path.unlink()
            metadata.unlink()
            directory.rmdir()
            directory.touch()
        case "data-directory":
            path.unlink()
            path.mkdir()
        case "stale-destination":
            (tmp_path / ".coverage.stale").touch()
        case "missing-root":
            target = root / "missing"
        case "record-empty" | "record-missing" | "record-unknown":
            action = "record"
            target = path
            match case:
                case "record-empty":
                    path.write_bytes(b"")
                case "record-missing":
                    path.unlink()
                case _:
                    target = directory / "unknown"
                    target.write_bytes(payload)
    return action, target


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("subprocess_cli", [False, True], ids=["entrypoint", "subprocess"])
def test_coverage_artifact_cli(case: str, subprocess_cli: bool, tmp_path: Path) -> None:
    """Reject incomplete or foreign artifacts before exposing any combine inputs."""
    action, target = _prepare_case(case, tmp_path)
    args = [action, str(target), "--run-id", "123", "--sha", "abc"]
    if subprocess_cli:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/ci_coverage.py"), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    else:
        out, err = StringIO(), StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                ci_coverage.main(args)
            except SystemExit as error:
                code = error.code
        stdout, stderr = out.getvalue(), err.getvalue()
    # OSError wording differs by platform; retain the failure type and verify no files moved.
    if case == "missing-root":
        stderr = "missing input directory\n" if "missing" in stderr else stderr
    output = (
        json.dumps(
            {
                "code": code,
                "stdout": stdout,
                "stderr": stderr,
                "prepared": sorted(p.name for p in tmp_path.glob(".coverage.*")),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    assert_output(output, EXPECTED / f"{case}.txt")
