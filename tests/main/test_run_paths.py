"""Where the command line, its target runs and `generate()` read their input and write their outputs and lock."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Iterator

PERSON = DATA_PATH / "jsonschema" / "person.json"
SYMLINKS = pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")


def _lock_files(root: Path) -> str:
    """Name every lock file below a run's directory, a symlink with the path it points to."""
    return "".join(
        f"{path.relative_to(root).as_posix()}{f' -> {path.readlink().as_posix()}' if path.is_symlink() else ''}\n"
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
        [
            *("--input", str(PERSON), "--input-file-type", "jsonschema", "--output", "out.py"),
            *("--lockfile", "loop1.lock", *policy),
        ],
        expected_exit=expected_exit,
    )
    stderr = capsys.readouterr().err.replace(tmp_path.resolve().as_posix(), "<root>")
    assert_output(f"{stderr}{_lock_files(tmp_path)}out.py written: {Path('out.py').is_file()}\n")


@contextmanager
def _stdin_pipe(data: bytes) -> Iterator[None]:
    """Make the process's standard input a pipe that holds ``data``, as a shell pipeline does."""
    read_end, write_end = os.pipe()
    os.write(write_end, data)
    os.close(write_end)
    saved = os.dup(0)
    os.dup2(read_end, 0)
    os.close(read_end)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)


@pytest.mark.skipif(os.name == "nt", reason="/dev/stdin is a POSIX path")
@pytest.mark.parametrize("output_exists", [False, True], ids=["new-output", "existing-output"])
def test_input_dev_stdin_reads_a_pipe(output_exists: bool, tmp_path: Path) -> None:
    """`--input /dev/stdin` reads the schema piped to the command, whether its output exists or not (#5378)."""
    output = tmp_path / "out.py"
    if output_exists:
        output.write_text("kept = 1\n", encoding="utf-8")
    with _stdin_pipe(PERSON.read_bytes()):
        run_main_with_args(
            [
                "--input",
                "/dev/stdin",
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output),
                "--disable-timestamp",
            ],
            use_parsed_source_cache=False,
        )
    assert_output(output.read_text(encoding="utf-8"))
