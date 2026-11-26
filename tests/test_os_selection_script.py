"""Tests for the selection of the Windows and macOS pull request tests, tests/os_selection.toml."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from coverage import CoverageData

from scripts import os_selection
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "os_selection.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )


@pytest.mark.allow_direct_assert("an entry naming a test that no longer exists must fail, not be stored as output")
def test_selection_names_only_existing_tests() -> None:
    """Every entry of tests/os_selection.toml names a test module or a test function of today's suite."""
    result = _run_script("check")
    assert (result.returncode, result.stderr) == (0, "")


@pytest.mark.allow_direct_assert("the list grows with each test of scripts/: its members are checked, not stored")
def test_ubuntu_only_prints_the_tests_of_scripts() -> None:
    """The modules Windows and macOS never run hold the tests of scripts/, this module among them."""
    modules = _run_script("ubuntu-only").stdout.splitlines()
    assert modules == sorted(modules)
    assert {"tests/main/test_payload_validation.py", "tests/test_os_selection_script.py"} <= set(modules)
    assert all((ROOT / module).is_file() for module in modules)


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


def test_check_reports_each_stale_entry(tmp_path: Path) -> None:
    """The check names a glob that matches no file and a test that is gone or in a module Windows never runs."""
    _write(
        tmp_path, "tests/test_a.py", "def test_a():\n    pass\n\n\nclass TestB:\n    def test_c(self):\n        pass\n"
    )
    _write(tmp_path, "tests/test_tool_script.py", "def test_tool():\n    pass\n")
    selection = _write(
        tmp_path,
        "tests/os_selection.toml",
        """
        ubuntu-only = ["tests/test_*_script.py", "tests/test_gone_*.py"]

        [select]
        modules = ["tests/test_a.py", "tests/old/**/test_*.py"]
        tests = ["tests/test_a.py::test_a", "tests/test_a.py::TestB::test_c", "tests/test_a.py::test_b"]
        cover = ["tests/test_tool_script.py::test_tool", "tests/test_moved.py::test_a", "tests/test_a.py"]
        """,
    )
    stale = os_selection.Selection.load(selection, tmp_path).stale()
    assert_output("\n".join(stale) + "\n")


@pytest.mark.parametrize(
    ("kept", "chosen"),
    [
        ((), ["a", "b", "d"]),
        (("d",), ["a", "b"]),
        (("a",), ["b"]),
        (("a", "b"), []),
    ],
)
@pytest.mark.allow_direct_assert("each case names the functions the cover must choose")
def test_greedy_cover_takes_the_most_arcs_per_duration(kept: tuple[str, ...], chosen: list[str]) -> None:
    """The cover runs every arc the kept tests leave unrun, taking the cheapest arcs per duration first."""
    arcs = {"a": {(1, 1, 2), (1, 2, 3), (1, 3, 4)}, "b": {(1, 3, 4), (1, 4, 5)}, "c": {(1, 4, 5)}, "d": {(1, 1, 2)}}
    weights = {"a": 3, "b": 1, "c": 1, "d": 1}
    assert os_selection.greedy_cover(arcs, weights, kept) == chosen


def test_write_cover_replaces_only_the_cover(tmp_path: Path) -> None:
    """Writing the cover keeps the comments and every other entry of the file."""
    path = _write(
        tmp_path,
        "os_selection.toml",
        """
        [select]
        # kept
        tests = ["tests/test_a.py::test_a"]
        cover = [
          "tests/test_old.py::test_old",
        ]
        """,
    )
    os_selection.write_cover(path, ["tests/test_b.py::test_b", "tests/test_c.py::TestC::test_c"])
    assert_output(path.read_text(encoding="utf-8"))


def test_cover_keeps_a_test_of_each_platform_arc(tmp_path: Path) -> None:
    """The cover takes, from a per-test map, the tests that run the arcs of platform functions the rules leave unrun.

    ``read`` reads a file (a platform function, as heki reports it), ``add`` does not: the test that runs every
    arc of ``read`` joins the cover, not one that runs only arcs it runs too, the test the rules keep already or the
    one of a module Windows never runs.
    """
    _write(tmp_path, "src/datamodel_code_generator/__init__.py", '"""A package."""\n')
    module = _write(
        tmp_path,
        "src/datamodel_code_generator/tool.py",
        """
        from pathlib import Path


        def read(path: Path) -> str:
            text = path.read_text(encoding="utf-8")
            return text.strip()


        def add(a: int, b: int) -> int:
            return a + b
        """,
    )
    for name in ("test_read", "test_add", "test_whole", "test_tool_script"):
        _write(tmp_path, f"tests/{name}.py", f"def {name}():\n    pass\n\n\ndef {name}_again():\n    pass\n")
    selection = _write(
        tmp_path,
        "tests/os_selection.toml",
        """
        ubuntu-only = ["tests/test_tool_script.py"]

        [select]
        modules = ["tests/test_whole.py"]
        cover = [
        ]
        """,
    )
    data = CoverageData(str(tmp_path / ".coverage"))
    reads = {str(module): {(-4, 5), (5, 6), (6, -4)}}
    for context, arcs in (
        ("tests/test_read.py::test_read|run", reads),
        ("tests/test_read.py::test_read_again|run", {str(module): {(-4, 5), (5, 6)}}),
        ("tests/test_add.py::test_add|run", {str(module): {(-9, 10), (10, -9)}}),
        ("tests/test_whole.py::test_whole|run", {str(module): {(-4, 5)}}),
        ("tests/test_tool_script.py::test_tool_script|run", reads),
    ):
        data.set_context(context)
        data.add_arcs(arcs)
    data.write()
    functions = {"src/datamodel_code_generator/tool.py": {"read"}}
    cover = os_selection.cover(os_selection.Selection.load(selection, tmp_path), tmp_path / ".coverage", functions)
    assert_output("\n".join(cover) + "\n")
