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
EXPECTED_PATH = Path(__file__).parent / "data" / "expected" / "prepare_release_draft_analysis"
REPOSITORY_ROOT = Path(__file__).parents[1]
ROUTING_CASES = (
    ("blank_record", 1, "requires_claude"),
    ("codspeed", 1, "safe"),
    ("codspeed", 2, "requires_claude"),
    ("codspeed_mixed_runtime", 2, "requires_claude"),
    ("codspeed_rename_from_runtime", 1, "requires_claude"),
    ("codspeed_rename_to_runtime", 1, "requires_claude"),
    ("empty", 0, "requires_claude"),
    ("empty", 1, "requires_claude"),
    ("empty_path", 1, "requires_claude"),
    ("docs_guide", 1, "requires_claude"),
    ("dockerfile", 1, "requires_claude"),
    ("invalid_json", 1, "requires_claude"),
    ("invalid_record_type", 1, "requires_claude"),
    ("invalid_type", 1, "requires_claude"),
    ("lock_file", 1, "requires_claude"),
    ("malformed", 1, "requires_claude"),
    ("mismatched_count", 3, "requires_claude"),
    ("newline", 1, "requires_claude"),
    ("package_metadata", 1, "requires_claude"),
    ("playground", 1, "requires_claude"),
    ("pre_commit_hooks", 1, "requires_claude"),
    ("prompt_data", 1, "requires_claude"),
    ("readme", 1, "requires_claude"),
    ("release_diagnostics", 1, "safe"),
    ("rename", 1, "requires_claude"),
    ("rename_safe", 1, "safe"),
    ("runtime", 1, "requires_claude"),
    ("safe", 5, "safe"),
    ("safe", 3_000, "requires_claude"),
    ("shipped_action", 1, "requires_claude"),
    ("skip_safe", 16, "skip_changelog"),
    ("tests_root", 1, "requires_claude"),
    ("unknown_path", 1, "requires_claude"),
    ("unsafe_mixed", 3, "requires_claude"),
    ("whitespace", 1, "requires_claude"),
    ("workflow_docs_deploy", 1, "requires_claude"),
    ("workflow_docker", 1, "requires_claude"),
    ("workflow_known", 1, "skip_changelog"),
    ("workflow_publish", 1, "requires_claude"),
    ("workflow_root", 1, "requires_claude"),
    ("workflow_unknown", 1, "requires_claude"),
)


@pytest.mark.parametrize(
    ("case_name", "expected_changed_files", "expected_name"),
    ROUTING_CASES,
)
def test_script_routes_release_analysis(
    case_name: str,
    expected_changed_files: int,
    expected_name: str,
    tmp_path: Path,
) -> None:
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
        + "\n",
        EXPECTED_PATH / f"{expected_name}.txt",
    )


@pytest.mark.parametrize(("case_name", "expected_changed_files", "expected_name"), ROUTING_CASES)
def test_main_routes_files_without_mocks(
    case_name: str,
    expected_changed_files: int,
    expected_name: str,
    tmp_path: Path,
) -> None:
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
        + "\n",
        EXPECTED_PATH / f"{expected_name}.txt",
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
    assert_output(github_output_path.read_text(encoding="utf-8"), EXPECTED_PATH / "missing.txt")


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

    assert_output(github_output_path.read_text(encoding="utf-8"), EXPECTED_PATH / "missing.txt")


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

    assert_output(github_output_path.read_text(encoding="utf-8"), EXPECTED_PATH / "missing.txt")


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

    assert_output(github_output_path.read_text(encoding="utf-8"), EXPECTED_PATH / "missing.txt")
