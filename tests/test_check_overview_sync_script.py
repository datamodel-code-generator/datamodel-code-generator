"""Tests for README/docs overview synchronization checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from scripts import check_overview_sync
from tests.conftest import assert_output

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_overview_sync.py"
EXPECTED_OVERVIEW_SYNC_PATH = Path(__file__).resolve().parent / "data" / "expected" / "overview_sync"


def _completed_process_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"returncode: {result.returncode}\nstdout:\n{result.stdout}stderr:\n{result.stderr}"


def _run_overview_sync(tmp_path: Path, readme_path: Path, docs_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--readme",
            readme_path.name,
            "--docs-index",
            docs_path.name,
        ],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )


def test_check_overview_sync_reports_current_files_are_synchronized() -> None:
    """The committed README and docs overview blocks stay in sync."""
    result = subprocess.run([sys.executable, str(SCRIPT)], check=True, capture_output=True, text=True)

    assert_output(result.stdout, EXPECTED_OVERVIEW_SYNC_PATH / "check_current.txt")


def test_compare_snapshots_reports_changed_fields() -> None:
    """Snapshot comparison reports only fields that differ."""
    readme = check_overview_sync.OverviewSnapshot(
        tagline="Generate Python data models from schema definitions in seconds.",
        badges=("badge",),
        hero_alt="hero",
        privacy_note="privacy",
        feature_bullets=("- feature",),
    )
    docs = check_overview_sync.OverviewSnapshot(
        tagline="Generate Python data models from schema definitions in seconds.",
        badges=("badge",),
        hero_alt="hero",
        privacy_note="updated privacy",
        feature_bullets=("- feature",),
    )

    assert_output(
        "\n".join((*check_overview_sync.compare_snapshots(readme, docs), "")),
        EXPECTED_OVERVIEW_SYNC_PATH / "changed_fields.txt",
    )


def test_check_overview_sync_reports_extraction_error_without_traceback(tmp_path: Path) -> None:
    """Extraction failures should be short CI diagnostics."""
    readme_path = tmp_path / "bad-readme.md"
    docs_path = tmp_path / "docs-index.md"
    readme_path.write_text("No heading\n", encoding="utf-8")
    docs_path.write_text("", encoding="utf-8")

    result = _run_overview_sync(tmp_path, readme_path, docs_path)

    assert_output(_completed_process_output(result), EXPECTED_OVERVIEW_SYNC_PATH / "extraction_error.txt")


def test_check_overview_sync_reports_mismatched_fields(tmp_path: Path) -> None:
    """Mismatched overview fields should be reported as CI diagnostics."""
    readme_path = tmp_path / "README.md"
    docs_path = tmp_path / "index.md"
    readme_path.write_text(
        dedent("""\
        # datamodel-code-generator
        Generate Python data models from schema definitions in seconds.
        [![PyPI](https://img.shields.io/pypi/v/datamodel-code-generator.svg)](https://pypi.org/project/datamodel-code-generator/)
        <img alt="hero" src="docs/assets/hero-light.svg">
        > [!NOTE]
        > Playground privacy: README note
        ## ✨ What it does
        - Generate models
        ---
        """),
        encoding="utf-8",
    )
    docs_path.write_text(
        dedent("""\
        # datamodel-code-generator
        Generate Python data models from schema definitions in seconds.
        [![PyPI](https://img.shields.io/pypi/v/datamodel-code-generator.svg)](https://pypi.org/project/datamodel-code-generator/)
        ![hero](assets/hero-light.svg)
        !!! note "Playground privacy"
            Docs note
        ## ✨ What it does
        - Generate models
        ---
        """),
        encoding="utf-8",
    )

    result = _run_overview_sync(tmp_path, readme_path, docs_path)

    assert_output(_completed_process_output(result), EXPECTED_OVERVIEW_SYNC_PATH / "mismatch.txt")


def test_extract_badges_matches_known_badge_hosts_only() -> None:
    """Badge extraction checks URL hosts instead of arbitrary substrings."""
    badges = check_overview_sync._extract_badges([
        "# datamodel-code-generator",
        "https://example.com/not-a-badge/img.shields.io",
        "[![PyPI](https://img.shields.io/pypi/v/datamodel-code-generator.svg)](https://pypi.org/project/datamodel-code-generator/)",
        "[![Codecov](https://codecov.io/gh/datamodel-code-generator/datamodel-code-generator/branch/main/graph/badge.svg)](https://codecov.io/gh/datamodel-code-generator/datamodel-code-generator)",
        "## ✨ What it does",
    ])

    assert_output("\n".join((*badges, "")), EXPECTED_OVERVIEW_SYNC_PATH / "badge_hosts.txt")
