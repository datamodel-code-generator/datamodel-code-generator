"""Keep the existing redundant inheritance DAG valid through shared join consumption."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import assert_output
from tests.data.python.allof_model_shared_join import shared_join_report
from tests.main.conftest import run_main_with_args


def test_allof_model_shared_join(tmp_path: Path) -> None:
    """Preserve generated declarations, importability and native values for shared joins."""
    assert_output(
        shared_join_report(tmp_path, run_main_with_args),
        Path("tests/data/expected/main/allof_model_inheritance/shared_join.json").resolve(),
    )
