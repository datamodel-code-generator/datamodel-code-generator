"""End-to-end tests for release analysis routing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import prepare_release_draft_analysis as preparer
from tests.conftest import assert_output

DATA_PATH = Path(__file__).parent / "data" / "prepare_release_draft_analysis"
REPOSITORY_ROOT = Path(__file__).parents[1]
ROUTING_CASES = (
    ("blank_record", 1),
    ("codspeed", 1),
    ("codspeed", 2),
    ("codspeed_mixed_runtime", 2),
    ("codspeed_rename_from_runtime", 1),
    ("codspeed_rename_to_runtime", 1),
    ("empty", 0),
    ("empty", 1),
    ("empty_path", 1),
    ("docs_guide", 1),
    ("dockerfile", 1),
    ("invalid_json", 1),
    ("invalid_record_type", 1),
    ("invalid_type", 1),
    ("lock_file", 1),
    ("malformed", 1),
    ("mismatched_count", 3),
    ("newline", 1),
    ("package_metadata", 1),
    ("playground", 1),
    ("pre_commit_hooks", 1),
    ("prompt_data", 1),
    ("readme", 1),
    ("release_diagnostics", 1),
    ("rename", 1),
    ("rename_safe", 1),
    ("runtime", 1),
    ("safe", 5),
    ("safe", 3_000),
    ("shipped_action", 1),
    ("skip_safe", 16),
    ("tests_root", 1),
    ("unknown_path", 1),
    ("unsafe_mixed", 3),
    ("whitespace", 1),
    ("workflow_docs_deploy", 1),
    ("workflow_docker", 1),
    ("workflow_known", 1),
    ("workflow_publish", 1),
    ("workflow_root", 1),
    ("workflow_unknown", 1),
)


@pytest.mark.parametrize(("case_name", "expected_changed_files"), ROUTING_CASES)
def test_script_routes_release_analysis(case_name: str, expected_changed_files: int, tmp_path: Path) -> None:
    """The real CLI skips Claude only for a nonempty safe path list."""
    analysis_path = tmp_path / "analysis.json"
    github_output_path = tmp_path / "github-output.txt"
    github_output_path.touch()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_release_draft_analysis.py",
            "--changed-files-path",
            str(DATA_PATH / f"{case_name}.files"),
            "--expected-changed-files",
            str(expected_changed_files),
            "--analysis-path",
            str(analysis_path),
            "--github-output-path",
            str(github_output_path),
        ],
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
    )

    result.check_returncode()
    assert_output(
        json.dumps(
            {
                "analysis": analysis_path.read_text(encoding="utf-8") if analysis_path.exists() else None,
                "github_output": github_output_path.read_text(encoding="utf-8"),
                "stderr": result.stderr,
                "stdout": result.stdout,
            },
            sort_keys=True,
        )
        + "\n"
    )


@pytest.mark.parametrize(("case_name", "expected_changed_files"), ROUTING_CASES)
def test_main_routes_files_without_mocks(case_name: str, expected_changed_files: int, tmp_path: Path) -> None:
    """The in-process CLI path keeps production routing under normal coverage."""
    analysis_path = tmp_path / "analysis.json"
    github_output_path = tmp_path / "github-output.txt"
    github_output_path.touch()

    preparer.main([
        "--changed-files-path",
        str(DATA_PATH / f"{case_name}.files"),
        "--expected-changed-files",
        str(expected_changed_files),
        "--analysis-path",
        str(analysis_path),
        "--github-output-path",
        str(github_output_path),
    ])

    assert_output(
        json.dumps(
            {
                "analysis": analysis_path.read_text(encoding="utf-8") if analysis_path.exists() else None,
                "github_output": github_output_path.read_text(encoding="utf-8"),
                "stderr": "",
                "stdout": "",
            },
            sort_keys=True,
        )
        + "\n"
    )


def test_missing_changed_files_fail_closed(tmp_path: Path) -> None:
    """An unavailable path list routes through semantic analysis."""
    analysis_path = tmp_path / "analysis.json"
    github_output_path = tmp_path / "github-output.txt"
    github_output_path.touch()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_release_draft_analysis.py",
            "--changed-files-path",
            str(tmp_path / "missing.files"),
            "--expected-changed-files",
            "1",
            "--analysis-path",
            str(analysis_path),
            "--github-output-path",
            str(github_output_path),
        ],
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
    )

    result.check_returncode()
    assert_output(github_output_path.read_text(encoding="utf-8"))


def test_main_missing_changed_files_fail_closed(tmp_path: Path) -> None:
    """The covered CLI path also fails closed for an unavailable file list."""
    github_output_path = tmp_path / "github-output.txt"
    github_output_path.touch()

    preparer.main([
        "--changed-files-path",
        str(tmp_path / "missing.files"),
        "--expected-changed-files",
        "1",
        "--analysis-path",
        str(tmp_path / "analysis.json"),
        "--github-output-path",
        str(github_output_path),
    ])

    assert_output(github_output_path.read_text(encoding="utf-8"))


def test_main_unreadable_changed_files_fail_closed(tmp_path: Path) -> None:
    """A non-file path list cannot suppress release processing."""
    changed_files_path = tmp_path / "changed-files"
    changed_files_path.mkdir()
    github_output_path = tmp_path / "github-output.txt"
    github_output_path.touch()

    preparer.main([
        "--changed-files-path",
        str(changed_files_path),
        "--expected-changed-files",
        "1",
        "--analysis-path",
        str(tmp_path / "analysis.json"),
        "--github-output-path",
        str(github_output_path),
    ])

    assert_output(github_output_path.read_text(encoding="utf-8"))


def test_main_invalid_utf8_changed_files_fail_closed(tmp_path: Path) -> None:
    """A non-UTF-8 path list cannot suppress release processing."""
    changed_files_path = tmp_path / "changed-files"
    changed_files_path.write_bytes(b"\x80")
    github_output_path = tmp_path / "github-output.txt"
    github_output_path.touch()

    preparer.main([
        "--changed-files-path",
        str(changed_files_path),
        "--expected-changed-files",
        "1",
        "--analysis-path",
        str(tmp_path / "analysis.json"),
        "--github-output-path",
        str(github_output_path),
    ])

    assert_output(github_output_path.read_text(encoding="utf-8"))
