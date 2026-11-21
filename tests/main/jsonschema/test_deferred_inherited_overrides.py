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


def test_list_restated_over_an_inherited_list_const(output_file: Path) -> None:
    """Restate an inherited list ``const`` as an array: the copy keeps the list type of the const in the child's."""
    run_main_and_assert(
        input_path=DEFERRED_PATH / "list_const_restated.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin"],
    )


def test_default_values_beside_a_required_name_no_schema_declares(output_file: Path) -> None:
    """Apply ``--default-values`` to a child whose parent requires a name that no schema declares, as the child does."""
    run_main_and_assert(
        input_path=DEFERRED_PATH / "required_name_without_schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--default-values", '{"Pet.tag": "dog"}'],
    )


def test_required_only_name_keeps_the_serialization_alias_of_the_child(output_file: Path) -> None:
    """Keep the alias ``--serialization-aliases`` gives a child that requires a property of a later parent."""
    run_main_and_assert(
        input_path=DEFERRED_PATH / "required_only_alias.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--serialization-aliases",
            '{"Child.source-name": "serialized-name"}',
            "--use-serialization-alias",
        ],
    )
