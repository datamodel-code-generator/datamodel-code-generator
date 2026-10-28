"""Tests for the changelog generation script."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.conftest import assert_output

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_changelog.sh"


@pytest.mark.skipif(shutil.which("jq") is None, reason="generate_changelog.sh requires jq")
@pytest.mark.skipif(sys.platform == "win32", reason="generate_changelog.sh is tested on POSIX runners")
@pytest.mark.parametrize("repo", [None, "datamodel-code-generator/datamodel-code-generator", "another/project"])
def test_prepend_release_entry_replaces_existing_tag(tmp_path: Path, repo: str | None) -> None:
    """Existing release entries are replaced instead of duplicated."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        textwrap.dedent(
            """\
            # Changelog

            All notable changes to this project are documented in this file.
            This changelog is automatically generated from GitHub Releases.

            ---

            ## [0.57.0](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.57.0) - 2026-05-07

            Old release body

            ---

            ## [0.56.1](https://github.com/koxudaxi/datamodel-code-generator/releases/tag/0.56.1) - 2026-04-16

            Previous release body

            ---
            """
        )
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            cat <<'JSON'
            {
              "body": "## What's Changed\\n* Fixed changelog generation",
              "createdAt": "2026-05-07T00:00:00Z",
              "isDraft": false,
              "publishedAt": "2026-05-07T00:00:00Z"
            }
            JSON
            """
        )
    )
    gh.chmod(0o755)

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    command = [
        "bash",
        str(SCRIPT),
        *(["--repo", repo] if repo else []),
        "--tag",
        "0.57.0",
        "--prepend-to",
        str(changelog),
    ]

    subprocess.run(command, check=True, env=env)
    first_run = changelog.read_text()

    subprocess.run(command, check=True, env=env)
    second_run = changelog.read_text()

    assert_output(
        first_run,
        Path(__file__).parent
        / "data/expected/repository_transfer"
        / ("changelog_override.txt" if repo == "another/project" else "changelog.txt"),
    )
    assert first_run == second_run
    assert first_run.count("## [0.57.0]") == 1
    assert "Old release body" not in first_run
    assert "* Fixed changelog generation" in first_run
    assert "## [0.56.1]" in first_run
    assert "Previous release body" in first_run
