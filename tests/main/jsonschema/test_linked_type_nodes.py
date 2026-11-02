"""End-to-end coverage for type nodes that a model writes beside its fields, which reuse and collapse must reach."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "extra_args", [pytest.param([], id="reuse"), pytest.param(["--collapse-root-models"], id="collapse")]
)
def test_main_schema_validator_rule_names_reused_map_value(output_file: Path, extra_args: list[str]) -> None:
    """Name the model a reuse keeps in a patternProperties rule whose map value model reuse removed.

    The type of a schema validator rule is held by no field, so reuse of a typed map value, which handed only the
    users that the models of a module own to the model it keeps, left the rule naming the removed model: the module
    failed to import with a NameError.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validator_rule_reused_map_value.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", "pydantic_v2.BaseModel", "--generate-schema-validators"),
            *("--use-typed-nullable-map-values", "--reuse-model", *extra_args),
            *("--formatters", "builtin", "--disable-timestamp", "--target-python-version", "3.11"),
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="schema_validator_rule_reused_map_value",
        model_name="Holder",
        valid_json=json.dumps({"x-a": {"k": {"id": 1}}}),
        invalid_json=json.dumps({"x-a": {"k": {"id": "no"}}}),
        expected_error_type="int_parsing",
    )


@pytest.mark.parametrize("collapse", [False, True])
def test_main_msgspec_dict_key_of_unconstrained_union_member(output_file: Path, collapse: bool) -> None:
    """Link the dict key of a union member that msgspec writes again without Meta, so collapse inlines the key.

    The copy of the member `dict[Key, int]` had no link from its key to the dict, so collapsing the root model `Key`
    took the key for a node that nothing holds and left the removed name `Key` in the field: decoding raised a
    NameError.
    """
    with pytest.warns(UserWarning, match=r"Holder\.f is generated without these constraints"):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "msgspec_collapsed_dict_key_in_spread_union.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                *("--output-model-type", "msgspec.Struct", "--formatters", "builtin"),
                *("--disable-timestamp", "--target-python-version", "3.11"),
                *(["--collapse-root-models"] if collapse else []),
            ],
            force_exec_validation=True,
        )
    with _generated_model(output_file, "msgspec_collapsed_dict_key_in_spread_union", "Holder") as model:
        decoded = [msgspec.json.decode(payload, type=model) for payload in (b'{"f": 2}', b'{"f": {"a": 1}}')]
    assert_output("".join(f"{value!r}\n" for value in decoded))
