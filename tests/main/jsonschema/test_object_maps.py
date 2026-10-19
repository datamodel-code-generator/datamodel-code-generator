"""Build one dict type per object map, at the root, as a property, as an array item and as a map value."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OBJECT_MAPS = JSON_SCHEMA_DATA_PATH / "object_maps"
PAYLOADS = DATA_PATH / "payloads" / "object_maps"
OBJECT_MAP_INPUTS = ("pattern_maps", "nullable_maps", "type_array_maps", "pattern_counts")
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
OPTION_SETS = {
    "default": (),
    "typed_map_values": ("--use-typed-nullable-map-values",),
    "field_constraints": ("--field-constraints",),
    "annotated": ("--use-annotated",),
}


def _verdict(model: Any, output_model_type: str, document: object) -> str:
    """Return whether a generated model accepts a JSON document, with the first error type where Pydantic rejects it."""
    text = json.dumps(document)
    try:
        if output_model_type == "msgspec.Struct":
            msgspec.json.decode(text, type=model)
        else:
            TypeAdapter(model).validate_json(text)
    except ValidationError as error:
        return f"rejected ({error.errors()[0]['type']})"
    except msgspec.ValidationError:
        return "rejected"
    return "accepted"


@pytest.mark.parametrize("name", OBJECT_MAP_INPUTS)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_object_maps(
    output_file: Path, recwarn: pytest.WarningsRecorder, name: str, output_model_type: str, option_set: str
) -> None:
    """Keep null, the other types of a type array and property counts on one dict, and decode every key class."""
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, output_model_type, payloads), name="payloads")


def _payload_verdicts(output_file: Path, output_model_type: str, payloads: dict[str, Any]) -> str:
    """Return whether the generated ``Model`` accepts the valid payload and each invalid change of it."""
    with _generated_model(output_file, "object_maps_model", "Model") as model:
        verdicts = [f"valid: {_verdict(model, output_model_type, payloads['valid'])}"]
        verdicts.extend(
            f"invalid {', '.join(change)}: {_verdict(model, output_model_type, {**payloads['valid'], **change})}"
            for change in payloads["invalid"]
        )
    return "".join(f"{verdict}\n" for verdict in verdicts)


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_nullable_object_maps(output_file: Path, output_model_type: str) -> None:
    """Under --strict-nullable, a nullable OpenAPI object map admits null at every position, array items included."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_object_maps.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, "--strict-nullable"],
    )
    payloads = json.loads((PAYLOADS / "nullable_object_maps.json").read_text())
    assert_output(_payload_verdicts(output_file, output_model_type, payloads), name="payloads")


@pytest.mark.parametrize("name", OBJECT_MAP_INPUTS)
def test_object_maps_schema_validators(output_file: Path, recwarn: pytest.WarningsRecorder, name: str) -> None:
    """Build the same maps where schema validators check the objects that one dict type cannot express.

    The dict of a validated map counts every key its patterns admit and keeps the keys of propertyNames.
    """
    run_main_and_assert(
        input_path=OBJECT_MAPS / f"{name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators"],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")
