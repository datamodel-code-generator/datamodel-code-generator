"""Tests for experimental feature documentation generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_experimental_docs.py"


def test_build_experimental_docs_check_is_up_to_date() -> None:
    """Generated experimental feature docs are committed."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_build_experimental_docs_release_notes() -> None:
    """The docs script can print release-note snippets from the registry."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--release-notes", "0.72.3"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "## Experimental Features" in result.stdout
    assert "[tool.datamodel-codegen.jobs], --job, --all-jobs" in result.stdout
    assert "datamodel-codegen.lock, --lockfile, --update-lock, and --locked" in result.stdout
