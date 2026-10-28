"""End-to-end coverage for shape-aware uniqueItems union paths."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_union_mapping_shapes(output_file: Path) -> None:
    """Only require a mapping rule in union branches that can accept mappings."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_union_mapping_shapes.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_union_mapping_shapes.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_union_mapping_shapes.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_union_mapping_shapes",
        model_name="UniqueItemsUnionMappingShapes",
        valid_json='{"anyOfFallback":{"x":[1,1]},"oneOfFallback":{"kind":"plain","x":[1,1]},"anyOfMappingOnly":{"x":[1,2]},"arrayImpossibleBranch":[1,2],"mappingImpossibleBranch":{"x":[1,2]},"arrayConstScalar":[1,2],"mappingEnumScalar":{"x":[1,2]},"arrayConstLiteral":[1,1],"mappingEnumLiteral":{"x":[1,1]}}',
        invalid_json='{"anyOfFallback":{"x":[1,1]},"oneOfFallback":{"kind":"plain","x":[1,1]},"anyOfMappingOnly":{"x":[1,1]},"arrayImpossibleBranch":[1,2],"mappingImpossibleBranch":{"x":[1,2]},"arrayConstScalar":[1,2],"mappingEnumScalar":{"x":[1,2]},"arrayConstLiteral":[1,1],"mappingEnumLiteral":{"x":[1,1]}}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_union_mapping_shapes",
        model_name="UniqueItemsUnionMappingShapes",
        invalid_json='{"anyOfFallback":{"x":[1,1]},"oneOfFallback":{"kind":"plain","x":[1,1]},"anyOfMappingOnly":{"x":[1,2]},"arrayImpossibleBranch":[1,1],"mappingImpossibleBranch":{"x":[1,2]},"arrayConstScalar":[1,2],"mappingEnumScalar":{"x":[1,2]},"arrayConstLiteral":[1,1],"mappingEnumLiteral":{"x":[1,1]}}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_union_mapping_shapes",
        model_name="UniqueItemsUnionMappingShapes",
        invalid_json='{"anyOfFallback":{"x":[1,1]},"oneOfFallback":{"kind":"plain","x":[1,1]},"anyOfMappingOnly":{"x":[1,2]},"arrayImpossibleBranch":[1,2],"mappingImpossibleBranch":{"x":[1,1]},"arrayConstScalar":[1,2],"mappingEnumScalar":{"x":[1,2]},"arrayConstLiteral":[1,1],"mappingEnumLiteral":{"x":[1,1]}}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_union_mapping_shapes",
        model_name="UniqueItemsUnionMappingShapes",
        invalid_json='{"anyOfFallback":{"x":[1,1]},"oneOfFallback":{"kind":"plain","x":[1,1]},"anyOfMappingOnly":{"x":[1,2]},"arrayImpossibleBranch":[1,2],"mappingImpossibleBranch":{"x":[1,2]},"arrayConstScalar":[1,1],"mappingEnumScalar":{"x":[1,2]},"arrayConstLiteral":[1,1],"mappingEnumLiteral":{"x":[1,1]}}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_union_mapping_shapes",
        model_name="UniqueItemsUnionMappingShapes",
        invalid_json='{"anyOfFallback":{"x":[1,1]},"oneOfFallback":{"kind":"plain","x":[1,1]},"anyOfMappingOnly":{"x":[1,2]},"arrayImpossibleBranch":[1,2],"mappingImpossibleBranch":{"x":[1,2]},"arrayConstScalar":[1,2],"mappingEnumScalar":{"x":[1,1]},"arrayConstLiteral":[1,1],"mappingEnumLiteral":{"x":[1,1]}}',
        expected_error_type="value_error",
    )


def test_main_jsonschema_unique_items_union_mapping_root() -> None:
    """Apply the same mapping-shape intersection to an unwrapped root union."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_union_mapping_root.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_union_mapping_root.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_union_mapping_root.py",
        **generate_kwargs,
    )


def test_main_jsonschema_unique_items_union_ref_mapping(output_file: Path) -> None:
    """Retain mapping checks shared by inline and referenced union branches."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_union_ref_mapping.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_union_ref_mapping.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_union_ref_mapping.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_union_ref_mapping",
        model_name="UniqueItemsUnionRefMapping",
        valid_json='{"anyOfValues":{"kind":"referenced","x":[1,2]},"oneOfValues":{"kind":"inline","x":[1,2]}}',
        invalid_json='{"anyOfValues":{"kind":"referenced","x":[1,1]},"oneOfValues":{"kind":"inline","x":[1,2]}}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_union_ref_mapping",
        model_name="UniqueItemsUnionRefMapping",
        invalid_json='{"anyOfValues":{"kind":"referenced","x":[1,2]},"oneOfValues":{"kind":"inline","x":[1,1]}}',
        expected_error_type="value_error",
    )


def test_main_jsonschema_unique_items_union_ref_mapping_root(output_file: Path) -> None:
    """Apply a shared referenced mapping rule to an unwrapped root union."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_union_ref_mapping_root.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_union_ref_mapping_root.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_union_ref_mapping_root.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_union_ref_mapping_root",
        model_name="UniqueItemsUnionRefMappingRoot",
        valid_json='{"x":[1,2]}',
        invalid_json='{"x":[1,1]}',
        expected_error_type="value_error",
        expected_attribute_path=("root",),
        expected_attribute_value={"x": [1, 2]},
    )


@pytest.mark.parametrize(
    ("extra_args", "module_name"),
    [
        pytest.param(
            ["--use-type-alias-type", "--field-constraints"],
            "unique_items_union_ref_mapping_root_type_alias_type",
            id="type-alias-type",
        ),
        pytest.param(
            ["--use-type-alias", "--collapse-root-models"],
            "unique_items_union_ref_mapping_root_collapsed_alias",
            id="collapse-root-models",
        ),
    ],
)
def test_main_jsonschema_unique_items_union_ref_mapping_root_type_alias(
    output_file: Path, extra_args: list[str], module_name: str
) -> None:
    """Write the referenced mapping that a type alias option would alias as a RootModel, unless collapse inlines it.

    The alias holds the mapping's uniqueItems rule, which only a class can run; inlined, it leaves the rule to the root.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "unique_items_union_ref_mapping_root.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--generate-schema-validators",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            *extra_args,
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=module_name,
        model_name="UniqueItemsUnionRefMappingRoot",
        valid_json='{"x":[1,2]}',
        invalid_json='{"x":[1,1]}',
        expected_error_type="value_error",
    )


def test_main_jsonschema_unique_items_union_const_root(output_file: Path) -> None:
    """Do not let a scalar const branch suppress a root array rule."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_union_const_root.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_union_const_root.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_union_const_root.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_union_const_root",
        model_name="UniqueItemsUnionConstRoot",
        valid_json="[1,2]",
        invalid_json="[1,1]",
        expected_error_type="value_error",
        expected_attribute_path=("root",),
        expected_attribute_value=[1, 2],
    )


def test_main_jsonschema_unique_items_union_enum_root(output_file: Path) -> None:
    """Do not let a scalar enum branch suppress a root mapping rule."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_union_enum_root.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_union_enum_root.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_union_enum_root.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_union_enum_root",
        model_name="UniqueItemsUnionEnumRoot",
        valid_json='{"x":[1,2]}',
        invalid_json='{"x":[1,1]}',
        expected_error_type="value_error",
        expected_attribute_path=("root",),
        expected_attribute_value={"x": [1, 2]},
    )


def test_main_jsonschema_unique_items_union_ref_sibling_dialects(output_file: Path) -> None:
    """Honor the draft-specific JSON Schema meaning of keywords beside ``$ref``."""
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    for filename, model_name, valid_json, invalid_json, expected_error_type in (
        (
            "unique_items_union_ref_sibling_draft7.json",
            "UniqueItemsUnionRefSiblingDraft7",
            "[1,1]",
            '"scalar"',
            "list_type",
        ),
        (
            "unique_items_union_ref_sibling_2020.json",
            "UniqueItemsUnionRefSibling2020",
            "[1,2]",
            "[1,1]",
            "value_error",
        ),
    ):
        schema = json.loads((JSON_SCHEMA_DATA_PATH / filename).read_text())
        run_generate_and_assert(
            input_=schema,
            input_filename=filename,
            expected_file=EXPECTED_JSON_SCHEMA_PATH / filename.replace(".json", ".py"),
            **generate_kwargs,
        )
        generate(input_=schema, output=output_file, input_filename=filename, **generate_kwargs)
        assert_generated_model_json_validation(
            output_file,
            module_name=filename.removesuffix(".json"),
            model_name=model_name,
            valid_json=valid_json,
            invalid_json=invalid_json,
            expected_error_type=expected_error_type,
            expected_attribute_path=("root",),
            expected_attribute_value=[1, 1] if valid_json == "[1,1]" else [1, 2],
        )
