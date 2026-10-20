"""Build one dict type per object map, at the root, as a property, as an array item and as a map value."""

from __future__ import annotations

import json
import operator
import sys
from functools import reduce
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import TypeAdapter, ValidationError
from referencing import Registry, Resource

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
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

    from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
    from datamodel_code_generator.types import DataType

OBJECT_MAPS = JSON_SCHEMA_DATA_PATH / "object_maps"
PAYLOADS = DATA_PATH / "payloads" / "object_maps"
OBJECT_MAP_INPUTS = (
    "pattern_maps",
    "nullable_maps",
    "type_array_maps",
    "pattern_counts",
    "pattern_additional",
    "declared_patterns",
    "pattern_checks",
)
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    pytest.param(
        "typing.TypedDict",
        marks=pytest.mark.skipif(
            sys.version_info < (3, 12), reason="Pydantic validates typing.TypedDict from Python 3.12 on"
        ),
    ),
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


@pytest.mark.parametrize(
    "option_set", [pytest.param((), id="root_models"), pytest.param(("--use-type-alias",), id="type_aliases")]
)
def test_object_maps_schema_validators_target_pydantic_version(
    output_file: Path, recwarn: pytest.WarningsRecorder, option_set: tuple[str, ...]
) -> None:
    """State the release whose regex_engine the validator adapters need above the target, as a model's config does.

    A type alias keeps a string pattern on a target before compiled patterns, so the adapter of its user selects the
    engine.
    """
    run_main_and_assert(
        input_path=OBJECT_MAPS / "pattern_checks.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--generate-schema-validators",
            "--target-pydantic-version",
            "2",
            *option_set,
        ],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / "pattern_checks.json").read_text())
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


def test_object_maps_schema_validators_cross_document(output_file: Path) -> None:
    """Check a pattern that names the reference of another document as a declaration does, as it is another schema."""
    input_path = OBJECT_MAPS / "cross_document.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators"],
    )
    payloads = json.loads((PAYLOADS / "cross_document.json").read_text())
    other = json.loads((OBJECT_MAPS / "cross_document_other.json").read_text())
    validator = Draft202012Validator(
        json.loads(input_path.read_text()),
        registry=Registry().with_resource("cross_document_other.json", Resource.from_contents(other)),
    )
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


def test_object_maps_schema_validators_false_property(output_file: Path) -> None:
    """Treat a property that is the boolean schema false as no plain object when the maps beside it are compared."""
    input_path = OBJECT_MAPS / "false_property.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators"],
    )
    payloads = json.loads((PAYLOADS / "false_property.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


def test_object_maps_released_pattern_properties_hook(tmp_path: Path) -> None:
    """Call a ``parse_pattern_properties`` override written for 0.86.0 with the arguments of that release.

    The dict of a map whose additionalProperties types the keys that match no pattern is built without the hook.
    """

    class ReleasedHookParser(JsonSchemaParser):
        def parse_pattern_properties(
            self,
            name: str,
            pattern_properties: dict[str, JsonSchemaObject | bool],
            path: list[str],
            *,
            property_names: JsonSchemaObject | bool | None = None,
        ) -> DataType:
            return super().parse_pattern_properties(name, pattern_properties, path, property_names=property_names)

    (input_path := tmp_path / "maps.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "patterns": {"type": "object", "patternProperties": {"^a": {"type": "integer"}}},
                "others": {
                    "type": "object",
                    "patternProperties": {"^a": {"type": "integer"}},
                    "additionalProperties": {"type": "integer"},
                },
            },
        }),
        encoding="utf-8",
    )
    parser = ReleasedHookParser(input_path, base_path=tmp_path, formatters=[Formatter.BUILTIN])
    assert_output(parser.parse())


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_map_bodies(output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str) -> None:
    """Build the models of object-kind request and response bodies in the paths scope as definitions are built."""
    input_path = OPEN_API_DATA_PATH / "object_map_bodies.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, "--openapi-scopes", "paths"],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / "object_map_bodies.json").read_text())
    assert_output(_body_verdicts(input_path, output_file, output_model_type, payloads), name="payloads")


def _body_verdicts(input_path: Path, output_file: Path, output_model_type: str, payloads: dict[str, Any]) -> str:
    """Check each body payload against its schema in the document and return whether its generated model accepts it."""
    document = yaml.safe_load(input_path.read_text(encoding="utf-8"))
    verdicts: list[str] = []
    for model_name, body in payloads.items():
        validator = Draft202012Validator(reduce(operator.getitem, body["schema"], document))
        for payload in body["valid"]:
            validator.validate(payload)
        for payload in body["invalid"]:
            with pytest.raises(SchemaValidationError):
                validator.validate(payload)
        with _generated_model(output_file, "object_map_bodies_model", model_name) as model:
            verdicts.extend(
                f"{model_name} {kind} {json.dumps(payload)}: {_verdict(model, output_model_type, payload)}\n"
                for kind in ("valid", "invalid")
                for payload in body[kind]
            )
    return "".join(verdicts)


PATHS_BODIES = ("type_array_request_body", "type_array_response_body", "map_keyword_bodies", "nullable_map_value_types")


@pytest.mark.cli_doc(
    options=["--openapi-paths-bodies-as-definitions"],
    option_description="""Give the bodies of the paths and webhooks scopes the models their schema definitions get.

With `--openapi-scopes paths` (or `webhooks`), request and response bodies are parsed by their own dispatch,
which types some object bodies inline although a schema definition of the same body gets a model: a body
whose type array admits objects, such as `type: [object, "null"]`, or one with `additionalProperties`,
`patternProperties` or `propertyNames` and no `type`. With `--openapi-paths-bodies-as-definitions`, such a
body gets the model its definition would get, such as a root model of `dict[str, Any] | None`, whose values
`--use-typed-nullable-map-values` types as declared. A document whose only bodies are such schemas then
generates models instead of failing with "Models not found in the input data", whose message names this
option when it applies. Bodies typed as objects, arrays, combinations, enums and references, and the `api`
scope, are unchanged.""",
    input_schema="openapi/type_array_request_body.yaml",
    cli_args=["--openapi-scopes", "paths", "--openapi-paths-bodies-as-definitions"],
    golden_output=(
        "jsonschema/object_maps/paths_bodies_as_definitions[default-pydantic_v2.BaseModel-type_array_request_body].py"
    ),
)
@pytest.mark.parametrize("name", PATHS_BODIES)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
@pytest.mark.parametrize("option_set", ["default", "typed_map_values"])
def test_paths_bodies_as_definitions(output_file: Path, output_model_type: str, name: str, option_set: str) -> None:
    """Build the models that definitions of the same schemas get for object bodies the paths scope types inline."""
    input_path = OPEN_API_DATA_PATH / f"{name}.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--openapi-scopes",
            "paths",
            "--openapi-paths-bodies-as-definitions",
            *OPTION_SETS[option_set],
        ],
    )
    payloads = json.loads((PAYLOADS / "paths_bodies_as_definitions.json").read_text())[name]
    assert_output(_body_verdicts(input_path, output_file, output_model_type, payloads), name="payloads")


@pytest.mark.parametrize("name", PATHS_BODIES[:3])
def test_paths_bodies_inline_hint(output_file: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    """Name --openapi-paths-bodies-as-definitions when the only bodies are object bodies typed inline."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{name}.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--openapi-scopes", "paths"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=(
            "Models not found in the input data; request or response bodies of the paths or webhooks scope are "
            "typed inline, and --openapi-paths-bodies-as-definitions gives them the models their schema definitions get"
        ),
    )
