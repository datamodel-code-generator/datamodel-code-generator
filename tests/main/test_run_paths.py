"""Where the command line, its target runs and `generate()` read their input and write their outputs and lock."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error, InputFileType, generate, load_pyproject_config
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Iterator

PERSON = DATA_PATH / "jsonschema" / "person.json"
SYMLINKS = pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")


def _rooted(text: str, root: Path) -> str:
    """Spell a run's directory in its messages as ``<root>``, with ``/`` between the parts below it on every OS."""
    root = root.resolve()
    for spelling in (repr(str(root))[1:-1], str(root), root.as_posix()):
        text = text.replace(spelling, "<root>")
    return text.replace("<root>\\\\", "<root>/").replace("<root>\\", "<root>/")


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
    stderr = _rooted(capsys.readouterr().err, tmp_path)
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


_MISSING_ROOT_RUNS = {
    "output": ["--output", "{missing}/out.py", "--update-lock"],
    "metadata": ["--output", "out.py", "--emit-model-metadata", "{missing}/meta.json", "--update-lock"],
    "lockfile": ["--output", "out.py", "--lockfile", "{missing}/x.lock", "--update-lock"],
    "jobs": ["--all-jobs"],
    "target": [
        *("--output", "models.py", "--generate-client", "httpx2", "--client-output", "{missing}/client"),
        *("--client-package", "client", "--client-model-package", "models"),
    ],
}


@pytest.mark.abnormal_path("only Windows has a root that does not exist, a missing drive or share")
@pytest.mark.parametrize("run", _MISSING_ROOT_RUNS)
def test_output_on_a_missing_root(
    run: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that stages an output on a root that does not exist names the root instead of testing it again (#5722)."""
    root = Path(tmp_path.anchor)
    missing = (root / f"dcg-missing-root-{tmp_path.name}").as_posix()
    exists, is_dir = Path.exists, Path.is_dir
    monkeypatch.setattr(Path, "exists", lambda path, *args, **kwargs: path != root and exists(path, *args, **kwargs))
    monkeypatch.setattr(Path, "is_dir", lambda path, *args, **kwargs: path != root and is_dir(path, *args, **kwargs))
    monkeypatch.chdir(tmp_path)
    source = DATA_PATH / "openapi" / "api.yaml"
    if run == "jobs":
        Path("pyproject.toml").write_text(
            "[tool.datamodel-codegen.jobs.models]\n"
            f"input = '{source.as_posix()}'\ninput-file-type = 'openapi'\noutput = '{missing}/out.py'\n",
            encoding="utf-8",
        )
        options = _MISSING_ROOT_RUNS[run]
    else:
        options = [
            *("--input", str(source), "--input-file-type", "openapi"),
            *(option.format(missing=missing) for option in _MISSING_ROOT_RUNS[run]),
        ]
    run_main_with_args(options, expected_exit=Exit.ERROR)
    assert_output(capsys.readouterr().err.replace(repr(str(root)), "'<root>'"))


_BLOCKED_RUNS = {
    "output": ["--output", "blocker/out.py"],
    "metadata": ["--output", "out.py", "--emit-model-metadata", "blocker/meta.json"],
    "staged-output": ["--output", "blocker/out.py", "--update-lock"],
    "staged-metadata": ["--output", "out.py", "--emit-model-metadata", "blocker/meta.json", "--update-lock"],
    "lockfile": ["--output", "out.py", "--lockfile", "blocker/x.lock", "--update-lock"],
    "jobs": ["--all-jobs"],
}


@pytest.mark.parametrize("run", _BLOCKED_RUNS)
def test_output_below_a_file(
    run: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that writes below a regular file names that file the same way every time and writes nothing (#5618)."""
    monkeypatch.chdir(tmp_path)
    Path("blocker").write_text("", encoding="utf-8")
    if run == "jobs":
        Path("pyproject.toml").write_text(
            "[tool.datamodel-codegen.jobs.models]\n"
            f"input = '{PERSON.as_posix()}'\ninput-file-type = 'jsonschema'\noutput = 'out.py'\n"
            "emit-model-metadata = 'blocker/meta.json'\n",
            encoding="utf-8",
        )
        options = _BLOCKED_RUNS[run]
    else:
        options = ["--input", str(PERSON), "--input-file-type", "jsonschema", *_BLOCKED_RUNS[run]]
    run_main_with_args(options, expected_exit=Exit.ERROR)
    stderr = _rooted(capsys.readouterr().err, tmp_path)
    written = sorted(path.name for path in tmp_path.iterdir() if path.name not in {"blocker", "pyproject.toml"})
    assert_output(f"{stderr}written: {written}\n")


@pytest.mark.parametrize("update_lock", [False, True], ids=["direct", "staged"])
def test_generate_output_below_a_file(update_lock: bool, tmp_path: Path) -> None:
    """`generate()` refuses a model metadata path below a regular file with an error that names the file (#5618)."""
    (blocker := tmp_path / "blocker").write_text("", encoding="utf-8")
    with pytest.raises(Error) as raised:
        generate(
            PERSON,
            input_file_type=InputFileType.JsonSchema,
            output=tmp_path / "out.py",
            emit_model_metadata=blocker / "meta.json",
            update_lock=update_lock,
            lockfile=tmp_path / "x.lock",
        )
    written = sorted(path.name for path in tmp_path.iterdir() if path != blocker)
    assert_output(f"{_rooted(str(raised.value), tmp_path)}\nwritten: {written}\n")


@pytest.mark.abnormal_path("only another process removes a staged file before the run publishes it")
@pytest.mark.parametrize("run", ["command-line", "generate"])
def test_missing_staged_model_metadata_publishes_nothing(
    run: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged model metadata file that is gone when the run publishes fails the publication of every file (#5620)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("datamodel_code_generator._write_model_metadata", lambda *_args: None)
    if run == "generate":
        with pytest.raises(FileNotFoundError):
            generate(
                PERSON,
                input_file_type=InputFileType.JsonSchema,
                output=Path("out.py"),
                emit_model_metadata=Path("meta.json"),
                update_lock=True,
            )
    else:
        run_main_with_args(
            [
                *("--input", str(PERSON), "--input-file-type", "jsonschema", "--output", "out.py"),
                *("--emit-model-metadata", "meta.json", "--update-lock"),
            ],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="Error: could not publish batch output: ",
        )
    assert_output(f"written: {sorted(path.name for path in tmp_path.iterdir())}\n")
