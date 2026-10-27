"""Type aliases of components that only reference each other, which Pydantic cannot build nor msgspec decode.

Generation stops when a class of output that Pydantic builds or msgspec decodes reaches such aliases (see
``test_error_messages.py``); output where no class reaches them keeps its 0.86.0 bytes, and so does a BaseModel, whose
root models Pydantic builds.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--output-model-type", "pydantic_v2.dataclass"],
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"],
    ],
    ids=["pydantic-dataclass", "base-model-type-alias"],
)
def test_ref_alias_cycle_no_class_uses(output_file: Path, extra_args: list[str]) -> None:
    """Keep the aliases of a cycle that no class reaches beside a class that uses other aliases and a class."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_alias_cycle_unused.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=extra_args,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"ref_alias_cycle_unused_{extra_args[1].replace('.', '_')}",
        model_name="Holder",
        valid_json='{"first": "a", "second": "b", "label": "c", "size": {"value": 1}, "count": 2}',
        invalid_json='{"size": {"value": "x"}}',
        expected_error_type="int_parsing",
        expected_attribute_path=("size", "value"),
        expected_attribute_value=1,
    )


def test_ref_alias_cycle_no_class_uses_msgspec(output_file: Path) -> None:
    """Keep the msgspec aliases of a cycle that no class reaches; the class that uses other aliases decodes.

    The schema accepts and rejects the payloads as the generated Struct does.
    """
    input_path = OPEN_API_DATA_PATH / "ref_alias_cycle_unused.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "msgspec.Struct"],
    )
    valid = {"first": "a", "second": "b", "label": "c", "size": {"value": 1}, "count": 2}
    invalid = {"size": {"value": "x"}}
    validator = Draft202012Validator({**yaml.safe_load(input_path.read_text()), "$ref": "#/components/schemas/Holder"})
    validator.validate(valid)
    with pytest.raises(SchemaValidationError):
        validator.validate(invalid)
    with _generated_model(output_file, "ref_alias_cycle_unused_msgspec", "Holder") as model:
        parsed = msgspec.json.decode(json.dumps(valid), type=model)
        with pytest.raises(msgspec.ValidationError, match=r"Expected `int`, got `str` - at `\$\.size\.value`"):
            msgspec.json.decode(json.dumps(invalid), type=model)
    assert_output(f"{parsed!r}\n", name="parsed")


def test_ref_alias_cycle_base_model(output_file: Path) -> None:
    """Keep the root models of a BaseModel, whose field that uses the cycle takes null."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_alias_cycle.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="ref_alias_cycle_base_model",
        model_name="Holder",
        valid_json='{"a": null}',
        invalid_json="[]",
        expected_error_type="model_type",
        expected_attribute_path=("a",),
        expected_attribute_value=None,
    )


@pytest.mark.parametrize(
    "output_model_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_ref_sibling_cycle_description_collapse_root_models(output_file: Path, output_model_type: str) -> None:
    """Write None where --collapse-root-models inlines the root of a cycle that deduplication left without a type.

    The classes that name the cycle take the type their field writes without ``Annotated``: msgspec writes it inside
    ``Annotated`` too, instead of leaving it empty, which is not Python.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_sibling_cycle_description.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--collapse-root-models"],
    )


def test_ref_sibling_cycle_description_collapse_root_models_msgspec_decode(output_file: Path) -> None:
    """Decode the msgspec classes that name a cycle beside a description; the schema decides the other fields."""
    input_path = OPEN_API_DATA_PATH / "ref_sibling_cycle_description.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "msgspec.Struct", "--collapse-root-models"],
    )
    valid = {"count": 1}
    invalid = {"count": "x"}
    validator = Draft202012Validator({**yaml.safe_load(input_path.read_text()), "$ref": "#/components/schemas/Holder"})
    validator.validate({})
    validator.validate(valid)
    with pytest.raises(SchemaValidationError):
        validator.validate(invalid)
    with _generated_model(output_file, "ref_sibling_cycle_description_msgspec", "Holder") as model:
        parsed = [msgspec.json.decode(json.dumps(payload), type=model) for payload in ({}, valid)]
        with pytest.raises(msgspec.ValidationError, match=r"Expected `int`, got `str` - at `\$\.count`"):
            msgspec.json.decode(json.dumps(invalid), type=model)
    assert_output(f"{parsed!r}\n", name="parsed")


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "typing.TypedDict"])
def test_ref_self_reference_unnamed_imports(output_file: Path, output_model_type: str) -> None:
    """Keep the output where no type names a component that is only a $ref to itself: the module imports."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_self_reference_unnamed.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type],
    )
    with _generated_model(output_file, "ref_self_reference_unnamed", "Holder") as model:
        assert_output(f"{model(labels=['x'], tree=[[]])!r}\n", name="instance")


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "typing.TypedDict"])
def test_ref_self_reference_unnamed_type_statement(output_file: Path, output_model_type: str) -> None:
    """Keep the ``type`` statements beside a component that is only a $ref to itself, which no type names."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_self_reference_unnamed.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--target-python-version", "3.12"],
    )
