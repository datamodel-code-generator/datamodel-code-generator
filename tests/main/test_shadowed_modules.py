"""Tests for the warning about a generated module that an existing package directory shadows."""

from __future__ import annotations

import errno
import os
import re
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import InputFileType, chdir
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_directory_content, create_assert_file_content
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_MAIN_PATH,
    OPEN_API_DATA_PATH,
    run_generate_file_and_assert,
    run_main_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = OPEN_API_DATA_PATH / "shadowed_modules"
EXPECTED_PATH = EXPECTED_MAIN_PATH / "shadowed_modules"
MODULE_FILES = [("__init__.py", "stale_package/__init__.py"), ("pets.py", "stale_package/pets.py")]

assert_file_content = create_assert_file_content(EXPECTED_PATH)


def _run(name: str, output: Path, *extra_args: str, **options: Any) -> None:
    """Generate one fixture document through the CLI."""
    run_main_and_assert(
        input_path=INPUT_PATH / f"{name}.json",
        output_path=output,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin", *extra_args],
        **options,
    )


def _message(module: Path) -> str:
    """Return the warning about a module beside the package directory of its name."""
    return (
        f"{module.as_posix()}: The generated module is shadowed by the existing package directory "
        f"{module.with_suffix('').as_posix()}, which Python imports instead. "
        "Remove the stale directory to import the generated module."
    )


def _pattern(module: Path) -> str:
    """Return the pattern matching exactly the warning about a module."""
    return f"^{re.escape(_message(module))}$"


def _publication_args(publication: str, tmp_path: Path) -> list[str]:
    """Return the options that select how the CLI publishes its output."""
    return {
        "direct": [],
        "json": ["--output-format", "json"],
        "update-lock": ["--update-lock", "--lockfile", str(tmp_path / "refs.lock")],
    }[publication]


@pytest.mark.parametrize("publication", ["direct", "json", "update-lock"])
def test_shadowed_module_warns(publication: str, output_dir: Path, tmp_path: Path) -> None:
    """A module written beside the stale package of its name is reported, and both stay as they are."""
    _run("package", output_dir)
    with pytest.warns(UserWarning, match=_pattern(output_dir / "pets.py")):
        _run(
            "module",
            output_dir,
            *_publication_args(publication, tmp_path),
            expected_directory=EXPECTED_PATH / "stale_package",
        )


@pytest.mark.parametrize("publication", ["direct", "json", "update-lock"])
def test_shadowed_single_file_output_warns(publication: str, output_dir: Path, tmp_path: Path) -> None:
    """A single-file output written beside a package of its name is reported like a module of a directory."""
    _run("package", output_dir)
    with pytest.warns(UserWarning, match=_pattern(output_dir / "pets.py")):
        _run(
            "single",
            output_dir / "pets.py",
            *_publication_args(publication, tmp_path),
            assert_func=assert_file_content,
            expected_file="single.py",
        )


def test_shadowed_module_warns_in_batch_jobs(output_dir: Path, tmp_path: Path) -> None:
    """Batch jobs report the shadowed module of a directory output and the shadowed single-file output."""
    _run("package", output_dir)
    for name in ("module.json", "single.json"):
        shutil.copyfile(INPUT_PATH / name, tmp_path / name)
    shutil.copyfile(DATA_PATH / "config" / "pyproject_shadowed_modules.toml", tmp_path / "pyproject.toml")
    with (
        chdir(tmp_path),
        pytest.warns(UserWarning, match=_pattern(output_dir / "pets.py")),
        pytest.warns(UserWarning, match=_pattern(tmp_path / "model.py")),
    ):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])
    assert_directory_content(output_dir, EXPECTED_PATH / "stale_package")
    assert_file_content(tmp_path / "model.py", "single.py")


@pytest.mark.parametrize("update_lock", [False, True])
def test_generate_warns_about_shadowed_module(update_lock: bool, output_dir: Path, tmp_path: Path) -> None:
    """`generate()` reports a shadowed module whether it writes the output directly or publishes it with a lock."""
    _run("package", output_dir)
    run_generate_file_and_assert(
        input_path=INPUT_PATH / "module.json",
        output_path=output_dir,
        input_file_type=InputFileType.OpenAPI,
        expected_directory=EXPECTED_PATH / "stale_package",
        expected_warnings=[_message(output_dir / "pets.py")],
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        update_lock=update_lock,
        lockfile=tmp_path / "refs.lock",
    )


@pytest.mark.parametrize("update_lock", [False, True])
def test_generate_warns_about_shadowed_single_file_output(update_lock: bool, output_dir: Path, tmp_path: Path) -> None:
    """`generate()` reports a shadowed single-file output whether it writes it directly or publishes it with a lock."""
    _run("package", output_dir)
    run_generate_file_and_assert(
        input_path=INPUT_PATH / "single.json",
        output_path=output_dir / "pets.py",
        input_file_type=InputFileType.OpenAPI,
        assert_func=assert_file_content,
        expected_file="single.py",
        expected_warnings=[_message(output_dir / "pets.py")],
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        update_lock=update_lock,
        lockfile=tmp_path / "refs.lock",
    )


def test_disable_warnings_silences_shadowed_module(output_dir: Path) -> None:
    """`--disable-warnings` silences the report and changes nothing else."""
    _run("package", output_dir)
    _run("module", output_dir, "--disable-warnings", expected_directory=EXPECTED_PATH / "stale_package")


def test_check_lists_stale_package_without_warning(output_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--check` writes no module, so it lists the stale package files as extra and warns about nothing."""
    _run("package", output_dir)
    with pytest.warns(UserWarning, match=_pattern(output_dir / "pets.py")):
        _run("module", output_dir)
    _run(
        "module",
        output_dir,
        "--check",
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED_PATH / "check.txt",
    )


def test_check_single_file_output_does_not_warn(output_dir: Path) -> None:
    """`--check` of a single-file output beside a package of its name writes nothing and warns about nothing."""
    _run("package", output_dir)
    with pytest.warns(UserWarning, match=_pattern(output_dir / "pets.py")):
        _run("single", output_dir / "pets.py")
    _run("single", output_dir / "pets.py", "--check")


@pytest.mark.parametrize(("name", "output"), [("module", ""), ("single", "pets.py")])
def test_input_diff_does_not_warn(name: str, output: str, output_dir: Path) -> None:
    """`--diff-against` writes no module, so it warns about nothing beside a package of the module's name."""
    _run("package", output_dir)
    _run(name, output_dir / output, "--diff-against", str(INPUT_PATH / f"{name}.json"))


@pytest.mark.abnormal_path("a directory that can be written but not listed is not portable to Windows or root")
def test_unlistable_directory_does_not_stop_generation(output_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory whose entries cannot be read is written like any other and reports nothing.

    The output directory is the only one the run lists, so every listing fails.
    """

    def fail(path: Path) -> list[str]:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))

    _run("package", output_dir)
    monkeypatch.setattr(os, "listdir", fail)
    _run("module", output_dir, expected_directory=EXPECTED_PATH / "stale_package")


def test_directory_without_init_does_not_shadow(output_dir: Path) -> None:
    """A leftover directory without `__init__.py` loses to the module of its name."""
    _run("package", output_dir)
    (output_dir / "pets" / "__init__.py").unlink()
    _run("module", output_dir, assert_func=assert_file_content, output_to_expected=MODULE_FILES)


def test_differently_spelled_package_does_not_shadow(output_dir: Path) -> None:
    """A package whose name differs in case is another module, also on a case-insensitive filesystem."""
    (output_dir / "PETS").mkdir(parents=True)
    (output_dir / "PETS" / "__init__.py").touch()
    _run("module", output_dir, assert_func=assert_file_content, output_to_expected=MODULE_FILES)


def test_package_beside_stale_module_does_not_warn(output_dir: Path) -> None:
    """A generated package wins over the stale module of its name, so nothing is shadowed."""
    _run("module", output_dir)
    _run("package", output_dir, expected_directory=EXPECTED_PATH / "stale_module")


def test_fresh_output_directory_does_not_warn(output_dir: Path) -> None:
    """A new output directory holds no package that could shadow a module."""
    _run("module", output_dir, assert_func=assert_file_content, output_to_expected=MODULE_FILES)


def test_single_file_output_without_package_does_not_warn(output_dir: Path) -> None:
    """A single-file output is written without a warning into a new directory and beside unrelated modules."""
    _run("single", output_dir / "pets.py", assert_func=assert_file_content, expected_file="single.py")
    _run("module", output_dir / "other")
    _run("single", output_dir / "pets.py", assert_func=assert_file_content, expected_file="single.py")
