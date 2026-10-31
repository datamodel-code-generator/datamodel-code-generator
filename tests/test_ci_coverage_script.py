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
MODULE = """import sys


def sign(value):
    if value < 0:
        return -1
    return 1


print(sign(int(sys.argv[1])), sign(int(sys.argv[2])))
"""


def _report_payload(case: str, tmp_path: Path) -> bytes:
    """Record real coverage data of a module staged in a git project whose `base` branch lacks it."""
    git = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = true\nrelative_files = true\nsource = .\n", encoding="utf-8")
    for command in (["init", "-q", "-b", "base"], ["add", ".coveragerc"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *command], cwd=tmp_path, check=True)
    (tmp_path / "sign.py").write_text(MODULE, encoding="utf-8")
    subprocess.run([*git, "add", "sign.py"], cwd=tmp_path, check=True)
    data = tmp_path / "seed.coverage"
    values = ("1", "1") if case == "report-missing-lines" else ("1", "-1")
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", f"--data-file={data}", "sign.py", *values],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    payload = data.read_bytes()
    data.unlink()
    return payload


def _prepare_case(case: str, tmp_path: Path) -> list[str]:  # ruff: ignore[too-many-branches]
    """Materialize each artifact fault using actual files."""
    root = tmp_path / "inputs"
    root.mkdir()
    report = case.startswith("report-")
    payload = _report_payload(case, tmp_path) if report else (DATA / "coverage.txt").read_bytes()
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
    if report:
        ci_coverage.main(["prepare", str(root), "--run-id", "123", "--sha", "abc"])
        match case:
            case "report-no-source":
                (tmp_path / "sign.py").unlink()
            case "report-missing-input":
                (tmp_path / name).unlink()
        return ["report", ".", "--compare-branch", "base"]
    return [action, str(target), "--run-id", "123", "--sha", "abc"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("subprocess_cli", [False, True], ids=["entrypoint", "subprocess"])
def test_coverage_artifact_cli(
    case: str, subprocess_cli: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject incomplete or foreign artifacts before exposing any combine inputs, then combine and report them."""
    args = _prepare_case(case, tmp_path)
    monkeypatch.chdir(tmp_path)
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
