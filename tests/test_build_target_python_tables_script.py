"""Check the per-target Unicode tables against the interpreter that runs the tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scripts import build_target_python_tables
from tests.conftest import assert_output

if TYPE_CHECKING:
    import pytest

EXPECTED = Path(__file__).parent / "data" / "expected" / "target_python_tables"


def test_target_python_tables_match_this_interpreter(capsys: pytest.CaptureFixture[str]) -> None:
    """Keep the tables of the running Python's version, and the rules built on them, what that Python answers.

    Each CI interpreter checks its own version, so the tables of every target are checked against their CPython.
    """
    status = build_target_python_tables.main(["--check-interpreter"])
    assert_output(f"status: {status}\n{capsys.readouterr().err}", EXPECTED / "check_interpreter.txt")
