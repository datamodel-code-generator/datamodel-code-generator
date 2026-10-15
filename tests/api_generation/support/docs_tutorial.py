"""Build the documentation's projects as a reader does: generate them from pyproject.toml, then run their code.

The arguments and the pyproject.toml tables come from scripts/build_docs_examples.py, which renders the same ones into
the guides, so the documented settings are the tested ones.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from scripts.build_docs_examples import pyproject_table, with_newest_preset
from tests.main.conftest import run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    import pytest

SERVER_URL = "http://localhost:8000"
RUNNING = re.compile(r"Uvicorn running on (\S+)")
DURATION = re.compile(r" in \d+(?:\.\d+)?s\b")
NOISY_VARIABLES = frozenset({"COLUMNS", "FORCE_COLOR", "PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER", "PY_COLORS"})


@dataclass(frozen=True, slots=True)
class Project:
    """A generated documentation project: its directory, the dependency notice, and the files it added."""

    root: Path
    notice: str
    files: str


def _listing(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def generate_project(
    source: Path,
    root: Path,
    arguments: Iterable[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    server_url: str = SERVER_URL,
) -> Project:
    """Copy a documentation project, point its document at the server URL, and run `datamodel-codegen` in it.

    A reader creates `app/__init__.py` to make `app` a package, so it is among the files the project adds. The project's
    scripts get the newest standard preset its guide renders in place of the placeholder. The files of its
    `implementation` directory then replace the stubs generation wrote, as a user implements them.
    """
    shutil.copytree(source, root)
    for script in root.glob("*.py"):
        script.write_text(with_newest_preset(script.read_text(encoding="utf-8")), encoding="utf-8")
    document = root / "openapi.yaml"
    document.write_text(document.read_text(encoding="utf-8").replace(SERVER_URL, server_url), encoding="utf-8")
    (root / "pyproject.toml").write_text(f"{pyproject_table(arguments)}\n", encoding="utf-8")
    copied = _listing(root)
    (root / "app").mkdir(exist_ok=True)
    (root / "app/__init__.py").touch()
    monkeypatch.chdir(root)
    capsys.readouterr()
    run_main_with_args([], use_builtin_default_formatter=False)
    files = "".join(f"{name}\n" for name in sorted(_listing(root) - copied))
    if (implementation := root / "implementation").is_dir():
        shutil.copytree(implementation, root, dirs_exist_ok=True)
    return Project(root, capsys.readouterr().err, files)


def _environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name not in NOISY_VARIABLES}


def run_python(root: Path, *arguments: str) -> str:
    """Run Python in a project as a reader would, returning the exit code and the output without durations."""
    result = subprocess.run(
        [sys.executable, *arguments], cwd=root, env=_environment(), capture_output=True, text=True, check=False
    )
    return DURATION.sub(" in <duration>", f"exit {result.returncode}\n{result.stdout}{result.stderr}")


def run_tests(root: Path, *tests: str) -> str:
    """Run a project's own tests with pytest."""
    return run_python(root, "-m", "pytest", "-qq", "-p", "no:cacheprovider", "-p", "no:warnings", "--color=no", *tests)


@contextmanager
def serve(root: Path, *arguments: str) -> Iterator[str]:
    """Serve an application with uvicorn on a free port from a directory, yielding its URL.

    The arguments name the application, such as `app.server.main:app`, and any options, such as `--app-dir`.
    """
    with subprocess.Popen(
        [sys.executable, "-m", "uvicorn", *arguments, "--port", "0"],
        cwd=root,
        env=_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as server:
        try:
            yield next(match[1] for line in cast("Iterable[str]", server.stderr) if (match := RUNNING.search(line)))
        finally:
            server.terminate()
