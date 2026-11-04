"""Where the command line, its target runs and `generate()` read and write the remote lock."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, run_main_with_args

PERSON = DATA_PATH / "jsonschema" / "person.json"
SYMLINKS = pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")


def _lock_files(root: Path) -> str:
    """Name every lock file below a run's directory, a symlink with the path it points to."""
    return "".join(
        f"{path.relative_to(root).as_posix()}{f' -> {os.readlink(path)}' if path.is_symlink() else ''}\n"
        for path in sorted(root.rglob("*.lock"))
    )


@SYMLINKS
@pytest.mark.parametrize(
    ("policy", "expected_exit"),
    [([], Exit.OK), (["--update-lock"], Exit.OK), (["--locked"], Exit.ERROR)],
    ids=["verify", "update", "locked"],
)
def test_lockfile_symlink_loop(
    policy: list[str],
    expected_exit: Exit,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lockfile that is a symlink loop is the path it spells on every Python version (#5586).

    No lock is verified, an update replaces the loop's first link and a locked run finds no lock.
    """
    monkeypatch.chdir(tmp_path)
    Path("loop1.lock").symlink_to("loop2.lock")
    Path("loop2.lock").symlink_to("loop1.lock")
    run_main_with_args(
        ["--input", str(PERSON), "--input-file-type", "jsonschema", "--output", "out.py", "--lockfile", "loop1.lock"]
        + policy,
        expected_exit=expected_exit,
    )
    stderr = capsys.readouterr().err.replace(tmp_path.resolve().as_posix(), "<root>")
    assert_output(f"{stderr}{_lock_files(tmp_path)}out.py written: {Path('out.py').is_file()}\n")
