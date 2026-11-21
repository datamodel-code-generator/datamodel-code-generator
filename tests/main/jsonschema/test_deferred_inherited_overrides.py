"""Properties that restate an inherited property, resolved as copies of the inherited field or kept as written."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DEFERRED_PATH = JSON_SCHEMA_DATA_PATH / "deferred_inherited_overrides"

CHILD_BEFORE_PARENT_OPTION_SETS = {
    "default": (),
    "default_values": ("--default-values", '{"Product.name": "unnamed", "Product.tags": ["featured"]}'),
}


@pytest.mark.parametrize("option_set", CHILD_BEFORE_PARENT_OPTION_SETS)
def test_deferred_override_of_a_parent_defined_later(output_file: Path, option_set: str) -> None:
    """Resolve the restated properties of a child written before its parent as copies of the inherited fields.

    The list the child restates keeps the inherited item type, the nullable list stays nullable, and the defaults that
    ``--default-values`` gives the restated name and list are resolved in the scope of the child.
    """
    run_main_and_assert(
        input_path=DEFERRED_PATH / "child_before_parent.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *CHILD_BEFORE_PARENT_OPTION_SETS[option_set]],
    )


def test_request_response_variant_keeps_a_constrained_override(output_file: Path) -> None:
    """Keep the constrained property a child declares over an inherited one in its request and response variants."""
    run_main_and_assert(
        input_path=DEFERRED_PATH / "request_response_override.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--read-only-write-only-model-type", "request-response"],
    )
