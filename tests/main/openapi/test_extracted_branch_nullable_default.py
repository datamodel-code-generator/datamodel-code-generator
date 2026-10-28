"""A branch root of a nullable field keeps its value required when the field's default stays with the field.

Only with --keep-field-defaults-off-branch-roots: without it the root takes the default and is optional, as in 0.86.0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

KEEP_DEFAULTS_OFF = "--keep-field-defaults-off-branch-roots"


@pytest.mark.parametrize("keep", [False, True], ids=["without_option", "keep"])
@pytest.mark.parametrize("constraint_option", ["--field-constraints", "--use-annotated"])
def test_extracted_branch_nullable_default(output_file: Path, constraint_option: str, *, keep: bool) -> None:
    """Require the value of ``Grade`` and accept null as a supplied value with the option."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "extracted_branch_nullable_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", constraint_option, *([KEEP_DEFAULTS_OFF] if keep else [])],
    )
    with _generated_model(output_file, f"extracted_branch_nullable_default_{keep}", "Grade") as grade:
        verdicts = [f"Grade(): {_verdict(grade)}", f"Grade(None): {_verdict(grade, None)}"]
    assert_output("".join(f"{verdict}\n" for verdict in verdicts), name="payloads")


def _verdict(root_model: Any, *args: object) -> str:
    """Return whether a root model builds from the given value, with the error type where it does not."""
    try:
        root_model(*args)
    except ValidationError as error:
        return f"rejected ({error.errors()[0]['type']})"
    return "accepted"
