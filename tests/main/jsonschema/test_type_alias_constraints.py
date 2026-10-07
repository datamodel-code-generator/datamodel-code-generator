"""Check that type alias constraints keep the validation of the ``con*`` calls they replace."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from pydantic import Field, RootModel, TypeAdapter, ValidationError
from typing_extensions import TypeAliasType

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path

CONSTRAINTS_SCHEMA = JSON_SCHEMA_DATA_PATH / "type_alias_annotated" / "constraints.json"
CONSTRAINTS_EXPECTED = EXPECTED_JSON_SCHEMA_PATH / "type_alias_annotated"
CONSTRAINT_PAYLOADS: dict[str, list[Any]] = json.loads(
    (DATA_PATH / "payloads/type_alias_annotated/cases.json").read_text()
)


def _outcome(validate: Any, adapter: TypeAdapter[Any], value: Any) -> dict[str, Any]:
    """Describe a validation result by its value and serialization, or by its errors."""
    try:
        validated = validate(value)
    except ValidationError as error:
        return {"errors": [[detail["type"], list(detail["loc"]), detail["msg"]] for detail in error.errors()]}
    return {
        "value": repr(validated),
        "python": adapter.dump_python(validated, mode="json"),
        "json": adapter.dump_json(validated).decode(),
    }


def _constraint_report(adapters: dict[str, TypeAdapter[Any]]) -> str:
    """Validate each payload as Python and as JSON, and record the JSON schema of each alias."""
    report = {
        name: {
            "schema": adapter.json_schema(),
            "values": [
                {
                    "input": value,
                    "python": _outcome(adapter.validate_python, adapter, value),
                    "json": _outcome(adapter.validate_json, adapter, json.dumps(value)),
                }
                for value in CONSTRAINT_PAYLOADS[name]
            ],
        }
        for name, adapter in adapters.items()
    }
    return json.dumps(report, indent=2) + "\n"


def _con_alias(name: str, root_model: type[RootModel[Any]]) -> TypeAliasType:
    """Rebuild the previous ``con*`` alias body from the unchanged RootModel output."""
    (root_type,) = root_model.__bases__[0].__pydantic_generic_metadata__["args"]
    if (description := root_model.model_fields["root"].description) is None:
        return TypeAliasType(name, root_type)
    return TypeAliasType(name, Annotated[root_type, Field(..., description=description)])


@pytest.mark.parametrize(
    ("variant", "extra_args"),
    [("default", []), ("strict", ["--strict-types", "str", "int", "float"])],
)
def test_type_alias_constraints_match_con_types(
    tmp_path: Path, output_file: Path, variant: str, extra_args: list[str]
) -> None:
    """Annotated alias constraints validate, serialize, and describe values like the ``con*`` calls.

    The RootModel output still uses ``con*`` calls, so its parameters rebuild the previous aliases.
    Both reports must equal the same expected file.
    """
    run_main_and_assert(
        input_path=CONSTRAINTS_SCHEMA,
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=f"type_alias_annotated/{variant}.py",
        extra_args=["--use-type-alias", "--disable-timestamp", *extra_args],
        force_exec_validation=True,
    )
    root_models = tmp_path / "root_models.py"
    run_main_and_assert(
        input_path=CONSTRAINTS_SCHEMA,
        output_path=root_models,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--formatters", "isort", *extra_args],
    )
    expected = CONSTRAINTS_EXPECTED / f"{variant}_runtime.txt"
    alias_module = f"type_alias_constraints_{variant}"
    with _generated_model(output_file, alias_module, "Constraints"):
        namespace = vars(sys.modules[alias_module])
        assert_output(
            _constraint_report({name: TypeAdapter(namespace[name]) for name in CONSTRAINT_PAYLOADS}), expected
        )
    root_module = f"type_alias_constraints_{variant}_root_models"
    with _generated_model(root_models, root_module, "Constraints"):
        namespace = vars(sys.modules[root_module])
        assert_output(
            _constraint_report({name: TypeAdapter(_con_alias(name, namespace[name])) for name in CONSTRAINT_PAYLOADS}),
            expected,
        )


def test_type_alias_constraints_with_shadowed_imports(output_file: Path) -> None:
    """Alias constraints use the aliased ``Annotated``, ``Field``, ``Decimal``, and ``compile`` imports."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_alias_annotated" / "shadowed_imports.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="type_alias_annotated/shadowed_imports.py",
        extra_args=["--use-type-alias", "--disable-timestamp"],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="type_alias_constraints_shadowed_imports",
        model_name="Holder",
        valid_json='{"Decimal": "x", "d": "0.75", "p": "ab"}',
        invalid_json='{"d": "0.3"}',
        expected_error_type="multiple_of",
        expected_attribute_path=("p",),
        expected_attribute_value="ab",
    )
