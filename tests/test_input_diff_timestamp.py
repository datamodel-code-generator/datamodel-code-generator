"""Real-time regression coverage for --diff-against generated headers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.data.python.custom_formatters.advance_time_before_second_generation import (
    CodeFormatter,
)
from tests.main.conftest import DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.allow_direct_assert
def test_diff_against_identical_inputs_share_the_default_timestamp_across_generation_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real formatter crossing a second boundary does not create a header-only diff."""
    CodeFormatter.apply_count = 0
    CodeFormatter.crossed_second_boundary = False
    input_path = DATA_PATH / "jsonschema" / "input_diff" / "same_new.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=tmp_path / "models.py",
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(DATA_PATH / "jsonschema" / "input_diff" / "same_new.json"),
            "--custom-formatters",
            "tests.data.python.custom_formatters.advance_time_before_second_generation",
            "--formatters",
            "builtin",
        ],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=DATA_PATH / "expected" / "main" / "input_diff" / "identical.txt",
        assert_no_stderr=True,
    )
    assert CodeFormatter.apply_count == 2
    assert CodeFormatter.crossed_second_boundary
