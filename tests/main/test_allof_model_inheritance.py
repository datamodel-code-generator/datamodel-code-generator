"""Exercise builtin emitted inheritance through public CLI/API and native backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.allof_model_inheritance import inheritance_report, late_local_base_report
from tests.main.conftest import run_main_with_args


def test_allof_model_inheritance(tmp_path: Path) -> None:
    """Keep each recorded byte/native result and reject invalid output transactions."""
    assert_output(
        inheritance_report(tmp_path, run_main_with_args),
        Path("tests/data/expected/main/allof_model_inheritance/report.json").resolve(),
    )


@pytest.mark.parametrize("entry", ["api", "cli"])
def test_allof_late_local_base(entry: str, tmp_path: Path) -> None:
    """Reject shared child-before-parent definitions without changing existing output."""
    assert_output(
        late_local_base_report(entry, tmp_path, run_main_with_args),
        Path(f"tests/data/expected/main/allof_model_inheritance/late_local_base_{entry}.json").resolve(),
    )
