"""Exercise builtin emitted inheritance through public CLI/API and native backends."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import assert_output
from tests.data.python.allof_model_inheritance import inheritance_report
from tests.main.conftest import run_main_with_args


def test_allof_model_inheritance(tmp_path: Path) -> None:
    """Keep each recorded byte/native result and reject invalid output transactions."""
    assert_output(
        inheritance_report(tmp_path, run_main_with_args),
        Path("tests/data/expected/main/allof_model_inheritance/report.json").resolve(),
    )
