"""End-to-end tests for trusted release draft PR resolution."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import assert_output

DATA_PATH = Path(__file__).parent / "data" / "resolve_release_draft_pr"


@pytest.mark.parametrize("input_path", sorted(DATA_PATH.glob("*.json")), ids=lambda path: path.stem)
def test_resolve_release_draft_pr_cli(input_path: Path, tmp_path: Path) -> None:
    """The real CLI emits only validated scalars and ignores untrusted PR text."""
    output_path = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/resolve_release_draft_pr.py",
            "--pr-path",
            str(input_path),
            "--pr-number",
            "4071",
            "--repository",
            "owner/repo",
            "--github-output-path",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
    )
    assert_output(
        json.dumps(
            {
                "returncode": result.returncode,
                "output": output_path.read_text(encoding="utf-8") if output_path.exists() else None,
                "stderr": result.stderr,
            },
            sort_keys=True,
        )
        + "\n",
        input_path.with_suffix(".txt"),
    )


def test_import_does_not_resolve_a_pr() -> None:
    """Importing the CLI module cannot parse caller arguments or write workflow outputs."""
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.resolve_release_draft_pr"],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
    )
    assert_output(
        json.dumps({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, sort_keys=True)
        + "\n",
        DATA_PATH / "import.txt",
    )
