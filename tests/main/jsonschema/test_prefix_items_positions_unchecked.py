"""Warn about the arrays with ``prefixItems`` that the output writes as a list, which checks no position (#4849)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

OPTION_SETS = {
    "default": (),
    "closed_arrays": ("--use-tuple-for-closed-arrays",),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_prefix_items_positions_unchecked(output_file: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Name each array whose ``prefixItems`` positions the list it is written as does not check.

    An open array, one closed without a ``minItems`` that fixes its length, and one with a schema-valued ``items`` are
    lists. ``--use-tuple-for-closed-arrays`` is named only where the schema's length bounds fix the length, and the
    arrays written as tuples, the list whose positions all accept the same values and the list whose ``false`` position
    ends it after positions that accept the same values are not warned about.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "prefix_items_positions_unchecked.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *OPTION_SETS[option_set]],
    )
    warnings = dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
    assert_output("".join(f"{message}\n" for message in warnings), name="warnings")
