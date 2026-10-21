"""End-to-end coverage for object allOf bound and enum intersections."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft202012Validator

from datamodel_code_generator import InputFileType
from datamodel_code_generator.enums import AllOfClassHierarchy, AllOfMergeMode
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entry_point", ["api", "cli"])
@pytest.mark.parametrize(
    ("fixture", "mode", "field_constraints", "hierarchy"),
    [
        ("inline", "constraints", True, "if-no-conflict"),
        ("refs", "constraints", True, "if-no-conflict"),
        ("inline", "all", True, "if-no-conflict"),
        ("refs", "all", True, "if-no-conflict"),
        ("inline", "constraints", False, "if-no-conflict"),
        ("refs", "constraints", False, "if-no-conflict"),
        ("ordinary", "constraints", True, "if-no-conflict"),
        ("ordinary", "all", True, "if-no-conflict"),
        ("inline", "none", True, "if-no-conflict"),
        ("refs", "none", True, "if-no-conflict"),
        ("refs", "constraints", True, "always"),
        ("ordinary", "none", True, "if-no-conflict"),
        ("complex", "constraints", True, "if-no-conflict"),
        ("complex", "none", True, "if-no-conflict"),
    ],
)
def test_allof_constraint_intersections(
    output_file: Path, fixture: str, mode: str, field_constraints: bool, hierarchy: str, entry_point: str
) -> None:
    """Intersect enabled object merges while preserving explicit opt-outs and ordinary metadata."""
    input_path = JSON_SCHEMA_DATA_PATH / "allof_constraint_intersections" / f"{fixture}.json"
    expected_mode = "constraints" if mode == "all" or fixture == "ordinary" else mode
    expected_name = f"{fixture}_{expected_mode}_{'field' if field_constraints else 'constrained'}_{hierarchy}"
    if entry_point == "api":
        options = {
            "input_file_type": InputFileType.JsonSchema,
            "field_constraints": field_constraints,
            "allof_merge_mode": AllOfMergeMode(mode),
            "allof_class_hierarchy": AllOfClassHierarchy(hierarchy),
            "disable_timestamp": True,
        }
        run_generate_and_assert(
            input_=json.loads(input_path.read_text(encoding="utf-8")),
            expected_file=EXPECTED_JSON_SCHEMA_PATH / "allof_constraint_intersections" / f"{expected_name}_api.py",
            assert_input_unchanged=True,
            **options,
        )
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            assert_func=assert_file_content,
            expected_file=f"allof_constraint_intersections/{expected_name}.py",
            **options,
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=f"allof_constraint_intersections/{expected_name}.py",
            extra_args=[
                "--disable-timestamp",
                "--allof-merge-mode",
                mode,
                "--allof-class-hierarchy",
                hierarchy,
                *(["--field-constraints"] if field_constraints else []),
            ],
            force_exec_validation=True,
        )

    payloads = json.loads((DATA_PATH / "payloads" / "allof_constraint_intersections.json").read_text(encoding="utf-8"))[
        fixture if fixture in {"ordinary", "complex"} else "target"
    ]
    valid = {name: payload["valid"] for name, payload in payloads.items()}
    if mode == "none" or hierarchy == "always":
        assert_generated_model_json_validation(
            output_file,
            module_name="allof_constraint_intersections",
            model_name="Root",
            valid_json=json.dumps(valid),
            invalid_json="[]",
            expected_error_type="model_type",
        )
    else:
        for name, payload in payloads.items():
            for invalid in payload["invalid"]:
                assert_generated_model_json_validation(
                    output_file,
                    module_name="allof_constraint_intersections",
                    model_name="Root",
                    valid_json=json.dumps(valid),
                    invalid_json=json.dumps({**valid, name: invalid}),
                    expected_error_type=payload["error_type"],
                    expected_attribute_path=payload["attribute_path"],
                    expected_attribute_value=payload.get("attribute_value", payload["valid"]),
                )


@pytest.mark.parametrize("fixture", ["empty_enum", "boolean_number_enum", "complex_empty_enum", "empty_enums"])
@pytest.mark.parametrize("entry_point", ["api", "cli"])
@pytest.mark.parametrize("schema_validators", [False, True])
def test_allof_empty_enum_intersection(
    output_file: Path, fixture: str, entry_point: str, *, schema_validators: bool
) -> None:
    """Keep optional properties usable when their enum intersection has no values."""
    input_path = DATA_PATH / "parser/jsonschema/allof_constraint_intersections" / f"{fixture}.json"
    expected_file = f"allof_constraint_intersections/{fixture}.py"
    if entry_point == "api":
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            generate_schema_validators=schema_validators,
            assert_func=assert_file_content,
            expected_file=expected_file,
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp", *(["--generate-schema-validators"] if schema_validators else [])],
            assert_func=assert_file_content,
            expected_file=expected_file,
        )
    payloads = json.loads((DATA_PATH / "payloads/allof_constraint_intersection_empty.json").read_text())
    Draft202012Validator(json.loads(input_path.read_text())).validate(payloads["valid"])
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_empty_enum_{fixture}_{entry_point}",
        model_name="Root",
        valid_json=json.dumps(payloads["valid"]),
        invalid_json=json.dumps(payloads["invalid"]),
        expected_error_type="model_type",
    )


@pytest.mark.parametrize("mode", ["partial", "equal"])
@pytest.mark.parametrize("scalar_kind", ["inherited", "unhashable", "custom_hash", "custom_equality"])
@pytest.mark.parametrize("subclass_side", ["both", "parent"])
def test_allof_enum_scalar_subclasses(output_file: Path, mode: str, scalar_kind: str, subclass_side: str) -> None:
    """Accept public Mapping inputs with scalar subclasses and retain enum aliases in declaration order."""
    schema = json.loads(
        (JSON_SCHEMA_DATA_PATH / "allof_constraint_intersections" / f"scalars_{mode}.json").read_text(encoding="utf-8")
    )
    for item in schema["allOf"] if subclass_side == "both" else schema["allOf"][:1]:
        for field in item["properties"].values():
            base = type(field["enum"][0])
            attributes = {}
            if scalar_kind == "unhashable":
                attributes["__hash__"] = None
            elif scalar_kind == "custom_hash":
                attributes["__hash__"] = lambda _self: 7
            elif scalar_kind == "custom_equality":
                attributes["__eq__"] = lambda self, other: type(self).__mro__[1](self) == other
                attributes["__hash__"] = base.__hash__
            scalar = type("Scalar", (base,), attributes)
            field["enum"] = [scalar(value) for value in field["enum"]]
    suffix = "_parent" if mode == "equal" and subclass_side == "parent" else ""
    expected = EXPECTED_JSON_SCHEMA_PATH / "allof_constraint_intersections" / f"scalars_{mode}_api{suffix}.py"
    run_generate_and_assert(
        input_=schema,
        output=output_file,
        expected_file=expected,
        assert_input_unchanged=True,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
    )
    payloads = json.loads(
        (DATA_PATH / "payloads/allof_constraint_intersection_scalars.json").read_text(encoding="utf-8")
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_enum_scalar_subclasses",
        model_name="Root",
        valid_json=json.dumps(payloads["valid"]),
        invalid_json=json.dumps(payloads["invalid"]),
        expected_error_type="enum",
        expected_attribute_path=("integer", "value"),
        expected_attribute_value=1,
    )


@pytest.mark.parametrize("mode", ["partial", "equal"])
def test_allof_enum_scalar_cli_control(output_file: Path, mode: str) -> None:
    """Keep the canonical JSON CLI representation alongside public subclass inputs."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "allof_constraint_intersections" / f"scalars_{mode}.json",
        input_file_type="jsonschema",
        output_path=output_file,
        extra_args=["--disable-timestamp"],
        assert_func=assert_file_content,
        expected_file=f"allof_constraint_intersections/scalars_{mode}.py",
        force_exec_validation=True,
    )


@pytest.mark.parametrize("fixture", ["metadata", "metadata_control"])
@pytest.mark.parametrize("entry_point", ["api", "cli"])
def test_allof_enum_metadata(output_file: Path, fixture: str, entry_point: str) -> None:
    """Retain enum annotation ownership and unchanged alias order through real generation."""
    input_path = JSON_SCHEMA_DATA_PATH / "allof_constraint_intersections" / f"{fixture}.json"
    expected = f"allof_constraint_intersections/{fixture}.py"
    if entry_point == "api":
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            use_field_description=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp", "--use-field-description"],
            assert_func=assert_file_content,
            expected_file=expected,
            force_exec_validation=True,
        )
    if fixture == "metadata_control":
        return
    payloads = json.loads((DATA_PATH / "payloads/allof_enum_metadata.json").read_text())
    native = Draft202012Validator(json.loads(input_path.read_text()))
    invalid = [{**payloads["valid"][0], name: value} for name, value in payloads["invalid"].items()]
    with _generated_model(output_file, "allof_enum_metadata", "Root") as model:
        observations = {
            "native_valid": [native.is_valid(value) for value in payloads["valid"]],
            "native_invalid": [native.is_valid(value) for value in invalid],
            "members": {
                name: list(field.annotation.__members__)
                for name, field in model.model_fields.items()
                if not name.endswith("_descriptions")
            },
        }
        assert_output(
            json.dumps(observations, indent=2) + "\n",
            EXPECTED_JSON_SCHEMA_PATH / "allof_constraint_intersections/metadata_runtime.txt",
        )
    for valid in payloads["valid"]:
        for value in invalid:
            assert_generated_model_json_validation(
                output_file,
                module_name="allof_enum_metadata",
                model_name="Root",
                valid_json=json.dumps(valid),
                invalid_json=json.dumps(value),
                expected_error_type="enum",
            )


def test_allof_property_named_like_schema_keyword(output_file: Path) -> None:
    """Compare inherited property maps key by key so properties named like schema keywords stay properties."""
    input_path = JSON_SCHEMA_DATA_PATH / "allof_constraint_intersections" / "property_named_bounds.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp"],
        assert_func=assert_file_content,
    )
