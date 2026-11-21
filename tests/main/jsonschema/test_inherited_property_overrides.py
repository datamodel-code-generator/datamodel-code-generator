"""Properties that override the property an allOf parent declares, under each ``--allof-merge-mode``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

OVERRIDES_PATH = JSON_SCHEMA_DATA_PATH / "inherited_property_overrides"

OPTION_SETS = {
    "default": (),
    "no_merge": ("--allof-merge-mode", "none"),
    "merge_all": ("--allof-merge-mode", "all"),
    "no_merge_intersected": ("--allof-merge-mode", "none", "--intersect-combined-schema-keywords"),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "fixture",
    ["parent_types.json", "type_shapes.json", "distributed_keywords.json", "partial_overrides.json"],
)
def test_inherited_property_overrides(
    output_file: Path, recwarn: pytest.WarningsRecorder, fixture: str, option_set: str
) -> None:
    """Write each override on the type of the inherited property it narrows, or keep it where it does not.

    The parents declare their properties by ``type``, by structure only, by ``enum``, ``const`` or ``allOf`` values,
    as unions, maps, tuples and boolean schemas; the overrides narrow them, add constraints of other types, widen or
    close them with ``true`` and ``false``, and wrap their keywords in one-member compositions.
    """
    run_main_and_assert(
        input_path=OVERRIDES_PATH / fixture,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *OPTION_SETS[option_set]],
    )
    warnings = dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
    assert_output("".join(f"{message}\n" for message in warnings), name="warnings")


LITERAL_OPTION_SETS = {
    "default": (),
    "literals": ("--enum-field-as-literal", "all"),
}


@pytest.mark.parametrize("option_set", LITERAL_OPTION_SETS)
def test_inherited_property_overrides_binary_values(output_file: Path, option_set: str) -> None:
    """Read a YAML ``!!binary`` enum value, which has no JSON type, as a bytes member of the enum or as ``Any``."""
    run_main_and_assert(
        input_path=OVERRIDES_PATH / "binary_values.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *LITERAL_OPTION_SETS[option_set]],
    )
