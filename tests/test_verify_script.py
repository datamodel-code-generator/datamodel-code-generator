"""Verify changes of a stand-in generator's checkout through `scripts/verify.py`."""

from __future__ import annotations

import re
from contextlib import redirect_stdout
from io import StringIO
from typing import TYPE_CHECKING

import pytest

from scripts import behaviour_diff, verify
from tests.capabilities import needs
from tests.conftest import assert_output
from tests.test_behaviour_diff_script import FAKE_DEPRECATION, FAKE_FIRST, PICK_CATALOG_INPUTS, _fake_generator

if TYPE_CHECKING:
    from pathlib import Path

DEPRECATION = 'warnings.warn(spec["deprecated"], DeprecationWarning'
OUTPUT_CHANGE = FAKE_FIRST.replace(DEPRECATION, DEPRECATION.replace('"]', '"] + "!"'))
PASSING_TEST = "def test_passes() -> None:\n    pass\n"
FAILING_TEST = "def test_fails() -> None:\n    raise AssertionError\n"
CANDIDATES = {
    "src unchanged": (None, "given", {}),
    "a change of no output": (FAKE_DEPRECATION, "given", {}),
    "a change no cell runs": (FAKE_FIRST.replace("raise MemoryError\n", "raise MemoryError()\n"), "given", {}),
    "an output change": (OUTPUT_CHANGE, "given", {}),
    "a flaky change": (
        FAKE_FIRST.replace(DEPRECATION, DEPRECATION.replace('"]', '"] + "-" + os.urandom(4).hex()')),
        "given",
        {},
    ),
    "a catalog in the cache": (OUTPUT_CHANGE, "cached", {}),
    "no catalog yet": (OUTPUT_CHANGE, "none", {}),
    "a changed test module that passes": (None, "given", {"tests/test_sample.py": PASSING_TEST}),
    "a changed test module that fails": (None, "given", {"tests/test_sample.py": FAILING_TEST}),
    "a changed test helper alone": (None, "given", {"tests/helpers.py": "VALUE = 1\n"}),
}
"""Each candidate's `__main__.py` (None: the base's), where its catalog is (given, in the cache or none) and the
files of the test suite it adds."""


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(CANDIDATES))
def test_verify_compares_the_picked_cells_twice(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Changed suite modules run; picked cells run twice: a difference or a flaky cell fails, unrun code warns."""
    text, where, suite = CANDIDATES[case]
    repo = tmp_path / "repo"
    commit = _fake_generator(repo, ["first"], None)[0]
    monkeypatch.setenv(behaviour_diff.REPOSITORY_ENV, str(repo))
    catalog = repo / ".git" / verify.CATALOGS / f"{commit}.json.gz"
    if where != "none":
        catalog.parent.mkdir(parents=True)
        selects = [arg for name in PICK_CATALOG_INPUTS for arg in ("--select", f"tests/data/jsonschema/{name}")]
        with redirect_stdout(StringIO()):
            behaviour_diff.main(["catalog", str(catalog), commit, *selects, "-j", "1", "--cache-dir", str(tmp_path)])
    if text is not None:
        (repo / "src" / "datamodel_code_generator" / "__main__.py").write_text(text, encoding="utf-8")
    for name, content in suite.items():
        (repo / name).write_text(content, encoding="utf-8")
    monkeypatch.chdir(repo)
    argv = [
        "--base",
        commit,
        "-j",
        "2",
        "--cache-dir",
        str(tmp_path / "cache"),
        *(["--catalog", str(catalog)] * (where == "given")),
    ]
    code = verify.main(argv)
    out = capfd.readouterr().out
    lines = [
        re.sub(r"\d+s\b", "<n>s", re.sub(r"-[0-9a-f]{8}\"", '-<random>"', line))
        for line in out.splitlines()
        if line.startswith((
            "verify:",
            "WARN",
            "violation:",
            "FAILED tests/",
            "    ",
            "pick: cell",
            "evidence: complete",
            "evidence: incomplete",
        ))
    ]
    assert code == (0 if lines[-1].startswith("verify: passed") else 1), out
    assert_output(
        "\n"
        .join([f"exit={code}", *lines, ""])
        .replace(commit, "<sha>")
        .replace(commit[:12], "<sha>")
        .replace(str(tmp_path), "TMP")
    )


FLAKY_CASES = {
    "same runs": ([("a", 0, 0)], [("a", 0, 0)]),
    "a side that changed": ([("a", 0, 0), ("b", 0, 1)], [("a", 0, 0), ("b", 0, 2)]),
    "a cell one run lacks": ([("a", 0, 0)], [("a", 0, 0), ("b", 0, 0)]),
}
"""Two runs' cells as (name, base exit, candidate exit)."""


@pytest.mark.parametrize("case", sorted(FLAKY_CASES))
def test_flaky_cells_differ_between_two_runs(case: str) -> None:
    """A cell is flaky where its two runs differ on either side, or where one run lacks it."""
    runs = [
        {"cells": [{"cell": name, "base": {"exit": base}, "candidate": {"exit": head}} for name, base, head in run]}
        for run in FLAKY_CASES[case]
    ]
    assert_output(f"{verify.flaky_cells(*runs)}\n")
