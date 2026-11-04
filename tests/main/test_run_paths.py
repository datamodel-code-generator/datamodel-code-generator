"""Where the command line, its target runs and `generate()` read their input and write their outputs and lock."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import InputFileType, generate, load_pyproject_config
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


@SYMLINKS
@pytest.mark.parametrize("output", [None, "out.py"], ids=["stdout", "output"])
def test_generate_lock_update_keeps_a_symlinked_lockfile(
    output: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`generate()` updates the target of a symlinked lockfile, whether it publishes an output or not (#5583)."""
    monkeypatch.chdir(tmp_path)
    Path("remote.lock").symlink_to("real.lock")
    generate(
        PERSON,
        input_file_type=InputFileType.JsonSchema,
        output=None if output is None else Path(output),
        lockfile=tmp_path / "remote.lock",
        update_lock=True,
    )
    assert_output(_lock_files(tmp_path))


_SPELLED_LOCK_RUNS = {
    "model": [],
    "target": [
        *("--generate-client", "httpx2", "--client-output", "client"),
        *("--client-package", "client", "--client-model-package", "models"),
    ],
}


@SYMLINKS
@pytest.mark.parametrize("run", [*_SPELLED_LOCK_RUNS, "generate"])
def test_lockfile_through_a_directory_symlink_and_parent(
    run: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lockfile spelled through a directory symlink and `..` is one file in every run, `..` read as written (#5584).

    The command line, its target runs and `generate()` all update `x.lock` in the working directory.
    """
    monkeypatch.chdir(tmp_path)
    Path("real", "sub").mkdir(parents=True)
    Path("link").symlink_to(Path("real", "sub"), target_is_directory=True)
    source, lockfile = DATA_PATH / "openapi" / "api.yaml", Path("link", "..", "x.lock")
    if run == "generate":
        generate(
            source, input_file_type=InputFileType.OpenAPI, output=Path("out.py"), lockfile=lockfile, update_lock=True
        )
    else:
        run_main_with_args([
            *("--input", str(source), "--input-file-type", "openapi", "--output", "out.py"),
            *("--lockfile", str(lockfile), "--update-lock", *_SPELLED_LOCK_RUNS[run]),
        ])
    assert_output(_lock_files(tmp_path))


@pytest.mark.parametrize("run", ["command-line", "generate"])
def test_project_lock_update_from_a_subdirectory(run: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default lockfile of a project lies beside its pyproject.toml, run from a subdirectory either way (#5585)."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.datamodel-codegen]\n"
        f"input = '{PERSON.as_posix()}'\ninput-file-type = 'jsonschema'\noutput = 'out.py'\nupdate-lock = true\n",
        encoding="utf-8",
    )
    (sub := tmp_path / "sub").mkdir()
    monkeypatch.chdir(sub)
    if run == "generate":
        generate(PERSON, config=load_pyproject_config())
    else:
        run_main_with_args([])
    assert_output(_lock_files(tmp_path))
