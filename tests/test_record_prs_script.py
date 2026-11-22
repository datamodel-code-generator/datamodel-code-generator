"""End-to-end test of the pull request that added each unreleased change record."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.record_prs import main
from tests.conftest import assert_output

if TYPE_CHECKING:
    import pytest

FRAGMENTS = Path("docs", "data", "migration", "unreleased")
ENVIRONMENT = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
HISTORY = (
    ("Add the first records (#10)", {"kept.toml": "a", "stamped.toml": "b", "renamed.toml": "c", "notes.txt": "d"}),
    ("Add a record without a pull request", {"direct.toml": "e"}),
    ("Edit a record (#11)", {"kept.toml": "a2"}),
    ("Stamp 0.1.0 (#12)", {"stamped.toml": None}),
    ("Add a record again under its old id (#13)", {"stamped.toml": "f"}),
    ("Rename a record (#14)", {"renamed.toml": None, "new-name.toml": "c"}),
    ("Add and remove a record (#15)", {"gone.toml": "g"}),
    ("Remove it (#16)", {"gone.toml": None}),
    ("Add a record (#17)", {"suffixless.toml": "h"}),
    ("Stamp 0.2.0 (#18)", {"suffixless.toml": None}),
    ("Add the record again without a pull request", {"suffixless.toml": "i"}),
)
"""Commits of the default branch: the pull request of a record is the last one that added its file."""


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, env=ENVIRONMENT, capture_output=True, check=True, text=True
    ).stdout


def test_record_prs(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Each fragment the revision holds maps to the pull request of the last commit that added it.

    An edit does not change it, a record stamped away and written again belongs to the later pull request, a renamed
    one to the pull request that renamed it. A record whose latest addition has no pull request number gives nothing,
    even if an earlier addition had one; so do a removed fragment and a file that is no record. An earlier revision
    lists its own fragments.
    """
    _git(tmp_path, "init", "--quiet", "--initial-branch=main")
    for subject, files in HISTORY:
        for name, content in files.items():
            path = tmp_path / FRAGMENTS / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.unlink()
            else:
                path.write_text(content, encoding="utf-8")
        _git(tmp_path, "add", "--all")
        _git(tmp_path, "commit", "--quiet", "-m", subject)
    for environment in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
        monkeypatch.setenv(environment, ENVIRONMENT[environment])
    outputs = []
    for revision in ("HEAD", "HEAD~7"):
        main(["--repository", str(tmp_path), "--revision", revision])
        outputs.append(f"=== {revision} ===\n{capsys.readouterr().out}")
    assert_output("".join(outputs))
