"""Regression coverage for validation siblings on a single allOf reference."""

from __future__ import annotations

import json
import sys
from operator import itemgetter
from typing import TYPE_CHECKING

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from packaging.version import Version
from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import TypeAdapter, ValidationError
from referencing import Registry, Resource

from datamodel_code_generator import DataModelType, Error, InputFileType, generate
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import assert_file_content
from tests.main.payload_validation.constants import ALLOF_REF_SIBLING_DIAGNOSTICS

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("schema_validators", [False, True])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("field_constraints", [False, True])
@pytest.mark.parametrize("merge_mode", ["all", "constraints", "none"])
@pytest.mark.parametrize(
    "case", json.loads((DATA_PATH / "payloads/allof_ref_siblings.json").read_text()), ids=itemgetter("name")
)
def test_allof_ref_siblings(
    output_file: Path,
    entrypoint: str,
    field_constraints: bool,
    schema_validators: bool,
    merge_mode: str,
    case: dict,
) -> None:
    """Preserve modern sibling constraints and ignore them in older reference drafts."""
    data = JSON_SCHEMA_DATA_PATH / "allof_ref_siblings"
    schema_path = data / f"{case['name']}.json"
    expected = f"allof_ref_siblings/{case['name']}_{field_constraints}_{schema_validators}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=schema_path,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--disable-timestamp",
                "--output-model-type",
                DataModelType.PydanticV2BaseModel.value,
                "--allof-merge-mode",
                merge_mode,
                *(["--field-constraints"] if field_constraints else []),
                *(["--generate-schema-validators"] if schema_validators else []),
                *(["--use-tuple-for-fixed-items"] if case.get("use_tuple") else []),
            ],
            assert_func=assert_file_content,
            expected_file=expected,
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=schema_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            output_model_type=DataModelType.PydanticV2BaseModel,
            disable_timestamp=True,
            allof_merge_mode=merge_mode,
            field_constraints=field_constraints,
            generate_schema_validators=schema_validators,
            use_tuple_for_fixed_items=case.get("use_tuple", False),
            assert_func=assert_file_content,
            expected_file=expected,
        )
    schema = json.loads(schema_path.read_text())
    registry = Registry().with_resource(
        "external_types.json", Resource.from_contents(json.loads((data / "external_types.json").read_text()))
    )
    validator = validator_for(schema)(schema, registry=registry)
    with _generated_model(output_file, "allof_sibling_model", "Root") as model:
        adapter = TypeAdapter(model)
        generated_schema = adapter.json_schema()
        generated_validator = validator_for(generated_schema)(generated_schema)
        if not schema_validators:
            expected = DATA_PATH / "payloads/allof_ref_sibling_outputs" / f"{case['name']}_without_validators.txt"
            # Pydantic 2.1 through 2.13 validate a value matching no member of an enum with a None member as None.
            if case["name"] == "enum_null" and not (Version("2.1") <= Version(PYDANTIC_VERSION) < Version("2.14")):
                expected = expected.with_name("enum_null_rejected_without_validators.txt")
            actual = []
            for value, expected_value in zip(
                [*case["valid"], *case["invalid"]],
                json.loads(expected.read_text(encoding="utf-8")),
                strict=True,
            ):
                if expected_value["accepted"]:
                    actual.append({
                        "accepted": True,
                        "value": json.loads(adapter.dump_json(adapter.validate_json(json.dumps(value)))),
                    })
                else:
                    with pytest.raises(ValidationError):
                        adapter.validate_json(json.dumps(value))
                    actual.append({"accepted": False})
            assert_output(
                json.dumps(actual, indent=2) + "\n",
                expected,
            )
            code = output_file.read_text(encoding="utf-8")
            assert_output(
                json.dumps({
                    "custom_validator": any(
                        marker in code
                        for marker in (
                            "@model_validator",
                            "@field_validator",
                            "_json_schema_literal_key",
                        )
                    )
                })
                + "\n",
                DATA_PATH / "payloads/allof_ref_sibling_outputs/without_validators.txt",
            )
            return
        actual = []
        for value in case["valid"]:
            validator.validate(value)
            generated_validator.validate(value)
            actual.append(json.loads(adapter.dump_json(adapter.validate_json(json.dumps(value)))))
        assert_output(
            json.dumps(actual, indent=2) + "\n",
            DATA_PATH / "payloads" / "allof_ref_sibling_outputs" / f"{case['name']}.txt",
        )
        if case["name"].startswith(("enum_", "const_")):
            base = vars(sys.modules[model.__module__])["Base"]
            parsed_inputs = []
            for value in case["valid"]:
                base_value = TypeAdapter(base).validate_json(json.dumps(value))
                parsed_inputs.append(json.loads(adapter.dump_json(adapter.validate_python(base_value))))
            assert_output(
                json.dumps(parsed_inputs, indent=2) + "\n",
                DATA_PATH / "payloads" / "allof_ref_sibling_outputs" / f"{case['name']}.txt",
            )
        for value in case["invalid"]:
            with pytest.raises(JsonSchemaValidationError):
                validator.validate(value)
            with pytest.raises(JsonSchemaValidationError):
                generated_validator.validate(value)
            with pytest.raises(ValidationError):
                adapter.validate_json(json.dumps(value))


@pytest.mark.parametrize("case_name", ALLOF_REF_SIBLING_DIAGNOSTICS)
def test_allof_ref_sibling_empty_intersection(output_file: Path, case_name: str) -> None:
    """Reject disjoint literal intersections, including JSON booleans versus numbers."""
    schema_path = JSON_SCHEMA_DATA_PATH / "allof_ref_siblings" / f"{case_name}.json"
    schema = json.loads(schema_path.read_text())
    validator = validator_for(schema)(schema)
    for value in ALLOF_REF_SIBLING_DIAGNOSTICS[case_name]:
        with pytest.raises(JsonSchemaValidationError):
            validator.validate(value)
    run_main_and_assert(input_path=schema_path, output_path=output_file, expected_exit=Exit.ERROR)
    with pytest.raises(Error, match="unsatisfiable"):
        generate(schema_path, input_file_type=InputFileType.JsonSchema)


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_allof_literal_custom_template(output_file: Path, entrypoint: str) -> None:
    """Preserve the literal and base constraints when a custom template renders validators."""
    schema_path = JSON_SCHEMA_DATA_PATH / "allof_ref_siblings/enum_length.json"
    template_dir = DATA_PATH / "templates_allof_literals"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=schema_path,
            output_path=output_file,
            extra_args=[
                "--generate-schema-validators",
                "--disable-timestamp",
                "--custom-template-dir",
                str(template_dir),
            ],
            assert_func=assert_file_content,
            expected_file="allof_ref_siblings/enum_length_False_True.py",
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=schema_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            generate_schema_validators=True,
            custom_template_dir=template_dir,
            assert_func=assert_file_content,
            expected_file="allof_ref_siblings/enum_length_False_True.py",
        )
    case = next(
        case
        for case in json.loads((DATA_PATH / "payloads/allof_ref_siblings.json").read_text())
        if case["name"] == "enum_length"
    )
    schema = json.loads(schema_path.read_text())
    validator = validator_for(schema)(schema)
    with _generated_model(output_file, "allof_literal_custom", "Root") as model:
        actual = []
        for value in case["valid"]:
            validator.validate(value)
            actual.append(model.model_validate_json(json.dumps(value)).root)
        assert_output(
            json.dumps(actual, indent=2) + "\n",
            DATA_PATH / "payloads/allof_ref_sibling_outputs/enum_length.txt",
        )
        for value in case["invalid"]:
            with pytest.raises(JsonSchemaValidationError):
                validator.validate(value)
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(value))


def test_allof_literal_custom_template_missing_validators(output_file: Path) -> None:
    """Diagnose a custom root template that omits the required literal validator."""
    schema_path = JSON_SCHEMA_DATA_PATH / "allof_ref_siblings/enum_length.json"
    template_dir = DATA_PATH / "templates_docstring_escaping"
    run_main_and_assert(
        input_path=schema_path,
        output_path=output_file,
        extra_args=["--generate-schema-validators", "--custom-template-dir", str(template_dir)],
        expected_exit=Exit.ERROR,
    )
    with pytest.raises(Error, match="must render class_body_lines"):
        generate(
            schema_path,
            input_file_type=InputFileType.JsonSchema,
            custom_template_dir=template_dir,
            generate_schema_validators=True,
        )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_allof_literal_validated_model_items(output_file: Path, entrypoint: str) -> None:
    """Compare declared model fields without trusting overriding serializers."""
    from tests.data.python.allof_literal_model_inputs import model_inputs

    schema_path = JSON_SCHEMA_DATA_PATH / "allof_ref_siblings/enum_model_items.json"
    expected = "allof_ref_siblings/enum_model_items_False_True.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=schema_path,
            output_path=output_file,
            extra_args=["--generate-schema-validators", "--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=schema_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            generate_schema_validators=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )
    schema = json.loads(schema_path.read_text())
    validator = validator_for(schema)(schema)
    actual = []
    with _generated_model(output_file, "allof_model_items", "Root") as model:
        item = vars(sys.modules[model.__module__])["RootItem"]
        for (label, target, payload), (_, native_target, native_payload) in zip(
            model_inputs(model, item), model_inputs(model, item), strict=True
        ):
            adapter = TypeAdapter(native_target.model_fields["root"].annotation)
            input_before = repr(payload)
            try:
                native_value = adapter.validate_python(native_payload)
                native_json = adapter.dump_python(native_value, mode="json", by_alias=True, warnings=False)
                if isinstance(payload[0], dict):
                    native_json[0]["a-value"] = payload[0]["a-value"]
                native_valid = validator.is_valid(native_json)
            except (AttributeError, TypeError, ValueError):
                native_valid = False
            if native_valid:
                result = target.model_validate(payload)
                actual.append([label, True, result.root[0] is payload[0]])
            else:
                with pytest.raises(ValidationError):
                    target.model_validate(payload)
                actual.append([label, False])
            if label not in {"opaque", "opaque-model"} and not isinstance(payload[0], dict):
                actual.extend([
                    ["own-item-serializer", payload[0].model_dump(mode="json", by_alias=True)],
                    ["own-root-serializer", target.model_construct(payload).model_dump(mode="json", by_alias=True)],
                ])
            if isinstance(payload[0], dict):
                actual.append(["own-nested-serializer", payload[0]["b"].model_dump(mode="json")])
            actual.extend([
                ["input-unchanged", repr(payload) == input_before],
                ["native-input-match", repr(payload) == repr(native_payload)],
            ])
            if label == "after-counter":
                actual.append(["after-calls", payload[0]._calls, native_payload[0]._calls])
        instance = model.model_validate([{"a-value": 1, "b": ["x"]}])
        actual.append(["own-root-identity", model.model_validate(instance) is instance])
    assert_output(
        json.dumps(actual, indent=2) + "\n",
        DATA_PATH / "payloads/allof_ref_sibling_outputs/validated_model_items.txt",
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("option", ["enum_field_as_literal", "ignore_enum_constraints"])
def test_allof_literal_options_without_validators(output_file: Path, entrypoint: str, option: str) -> None:
    """Respect normal enum options without injecting runtime validator methods."""
    schema_path = JSON_SCHEMA_DATA_PATH / "allof_ref_siblings/enum_sibling.json"
    expected = f"allof_ref_siblings/without_validators_{option}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=schema_path,
            output_path=output_file,
            extra_args=[
                "--disable-timestamp",
                "--" + option.replace("_", "-"),
                *(["all"] if option == "enum_field_as_literal" else []),
            ],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=schema_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            **{option: "all" if option == "enum_field_as_literal" else True},
            assert_func=assert_file_content,
            expected_file=expected,
        )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("output_model_type", [DataModelType.PydanticV2BaseModel, DataModelType.DataclassesDataclass])
def test_allof_ref_siblings_root_config(output_file: Path, entrypoint: str, output_model_type: DataModelType) -> None:
    """Preserve root config and native alias output while merging scalar siblings."""
    validators = output_model_type == DataModelType.PydanticV2BaseModel
    case = "enum_sibling" if validators else "string_2019-09"
    schema_path = JSON_SCHEMA_DATA_PATH / f"allof_ref_siblings/{case}.json"
    expected = f"allof_ref_siblings/root_config_{output_model_type.name}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=schema_path,
            output_path=output_file,
            extra_args=[
                "--disable-timestamp",
                "--output-model-type",
                output_model_type.value,
                *(["--generate-schema-validators", "--enable-faux-immutability"] if validators else []),
            ],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=schema_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            output_model_type=output_model_type,
            generate_schema_validators=validators,
            enable_faux_immutability=validators,
            assert_func=assert_file_content,
            expected_file=expected,
        )


def test_allof_ref_siblings_pattern_properties(output_file: Path) -> None:
    """Merge pattern properties in referenced array items through the CLI."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "allof_ref_siblings/items_pattern.json",
        output_path=output_file,
        extra_args=["--disable-timestamp", "--generate-schema-validators"],
        assert_func=assert_file_content,
        expected_file="allof_ref_siblings/items_pattern.py",
        force_exec_validation=True,
    )
