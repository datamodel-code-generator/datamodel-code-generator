"""Build one dict type per object map, at the root, as a property, as an array item and as a map value."""

from __future__ import annotations

import json
import operator
import sys
import typing
from functools import reduce
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema import Draft7Validator, Draft202012Validator
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
    _generated_package_module,
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
    "type_list_maps",
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
SINGLE_DICT = "--use-single-dict-for-object-maps"
STRICT_PATTERN_MAPS = "--check-pattern-maps-strictly"
TYPED_NULLABLE_MAP_VALUES = "--use-typed-nullable-map-values"
TYPED_MAP_VALUES = (TYPED_NULLABLE_MAP_VALUES, "--use-typed-map-values-beside-other-types")
OPTION_SETS = {
    "default": (SINGLE_DICT,),
    "typed_map_values": (SINGLE_DICT, *TYPED_MAP_VALUES),
    "field_constraints": (SINGLE_DICT, "--field-constraints"),
    "annotated": (SINGLE_DICT, "--use-annotated"),
    "typed_map_values_field_constraints": (SINGLE_DICT, *TYPED_MAP_VALUES, "--field-constraints"),
    "typed_map_values_annotated": (SINGLE_DICT, *TYPED_MAP_VALUES, "--use-annotated"),
    "omit_redundant_reference_null": (SINGLE_DICT, "--omit-redundant-reference-null"),
    "without_single_dict": (),
    "without_single_dict_typed_map_values": TYPED_MAP_VALUES,
    "typed_nullable_map_values": (SINGLE_DICT, TYPED_NULLABLE_MAP_VALUES),
    "without_single_dict_typed_nullable_map_values": (TYPED_NULLABLE_MAP_VALUES,),
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
    """Keep null, the other types of a type array and property counts on one dict, and decode every key class.

    Without --use-single-dict-for-object-maps the maps are those of 0.86.0: a union of dicts, one per value type of
    the patterns, with the null and the other types of a type array left to the field, while the values typed under
    --use-typed-nullable-map-values keep the other types of their type array. Those values are typed beside other
    types, such as in [object, string], only with --use-typed-map-values-beside-other-types; without it they stay Any,
    as in 0.86.0. msgspec, which cannot decode a union of several dicts, gets the one dict of the option there.
    """
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


def _payload_verdicts(output_path: Path, output_model_type: str, payloads: dict[str, Any]) -> str:
    """Return whether the generated ``Model`` accepts the valid payload and each invalid change of it.

    A package output gives ``Model`` from its ``__init__``. typing caches subscriptions such as
    ``NotRequired[dict[str, int] | str]`` by equal arguments, and a union equals its members in any order, so the caches
    are cleared first: Pydantic then tries the union members in the order this output writes them, whatever an earlier
    test wrote.
    """
    for clear_cache in typing._cleanups:
        clear_cache()
    package = output_path.is_dir()
    with (
        _generated_package_module(output_path, "")
        if package
        else _generated_model(output_path, "object_maps_model", "Model")
    ) as generated:
        model = generated.Model if package else generated
        verdicts = [f"valid: {_verdict(model, output_model_type, payloads['valid'])}"]
        verdicts.extend(
            f"invalid {', '.join(change)}: {_verdict(model, output_model_type, {**payloads['valid'], **change})}"
            for change in payloads["invalid"]
        )
    return "".join(f"{verdict}\n" for verdict in verdicts)


RELEASED_PATTERN_MAPS = (
    "additional_pattern_intersections/anchored_disjoint",
    "additional_pattern_intersections/minimum_float",
    "additional_pattern_intersections/minimum",
    "additional_pattern_intersections/minimum_large",
    "additional_pattern_intersections/minimum_maximum",
    "additional_pattern_intersections/minimum_null",
    "additional_pattern_intersections/minimum_number",
    "additional_pattern_intersections/minimum_shared",
    "additional_pattern_intersections/prefix_suffix",
    "additional_pattern_intersections/suffix_prefix",
    "annotated_string_keys/union",
    "pattern_intersections/annotated_objects",
    "pattern_intersections/closed_objects",
    "pattern_intersections/complex",
    "pattern_intersections/disjoint",
    "pattern_intersections/equivalent_objects",
    "pattern_intersections/incompatible_objects",
    "pattern_intersections/opaque_root",
    "pattern_intersections/overlap",
    "pattern_intersections/reachable_nested",
    "pattern_intersections/third_rejected_pattern",
    "pattern_intersections/unreachable_nested",
    "pattern_properties_boolean",
    "pattern_properties",
    "pattern_string_adapters/minimum",
    "schema_validators_property_count_root_collapse",
    "type_union_constraints/keys_count_field",
    "type_union_constraints/keys_count_root",
    "type_union_constraints/keys_field",
    "type_union_constraints/keys_root",
    "type_union_constraints/pattern_properties",
    "undeclared_required/objects",
    "unique_items_allof_mapping",
    "unknown_pattern_annotations/annotation",
    "unknown_pattern_annotations/base_class_extension",
    "unknown_pattern_annotations/classvar",
    "unknown_pattern_annotations/other_annotation",
    "unknown_pattern_annotations/python_type",
    "unknown_pattern_annotations/unknown_keyword",
)


@pytest.mark.parametrize("name", RELEASED_PATTERN_MAPS, ids=lambda name: name.replace("/", "-"))
def test_pattern_maps_without_single_dict(output_file: Path, name: str) -> None:
    """Generate the pattern maps of the intersection, annotation, key-type and required-key fixtures as 0.86.0 did.

    Their tests pin the output of --use-single-dict-for-object-maps; without it, each map of patternProperties is a
    union of dicts, one per value type, and the other types of a type array are left to the field.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / f"{name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--disable-timestamp", "--formatters", "builtin"],
    )


RELEASED_VALIDATED_PATTERN_MAPS = (
    "additional_pattern_intersections/boolean",
    "additional_pattern_intersections/boolean_integer",
    "additional_pattern_intersections/boolean_optional",
    "additional_pattern_intersections/declared_witness",
    "additional_pattern_intersections/minimum_large",
    "pattern_intersections/aliased_declared",
    "pattern_intersections/declared",
    "pattern_intersections/declared_object",
    "pattern_intersections/redundant_declared",
    "pattern_intersections/rejected",
    "pattern_intersections/unreachable_declared",
    "pattern_intersections/weaker_declared",
    "undeclared_required/closed",
    "undeclared_required/objects",
)


@pytest.mark.parametrize("name", RELEASED_VALIDATED_PATTERN_MAPS, ids=lambda name: name.replace("/", "-"))
def test_pattern_maps_schema_validators_without_single_dict(output_file: Path, name: str) -> None:
    """Check declared keys against the patterns as 0.86.0 did when schema validators check the pattern maps.

    Their tests pin the output of --check-pattern-maps-strictly; without it, a pattern checks a declared key only
    where it rejects a value of a simple declared schema: a ``false`` pattern, an integer minimum, or required keys.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / f"{name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--generate-schema-validators",
        ],
    )


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_nullable_object_maps(output_file: Path, output_model_type: str) -> None:
    """Under --strict-nullable, a nullable OpenAPI object map admits null at every position, array items included."""
    assert_output(_nullable_object_map_verdicts(output_file, output_model_type, SINGLE_DICT), name="payloads")


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_nullable_object_maps_without_single_dict(output_file: Path, output_model_type: str) -> None:
    """Without --use-single-dict-for-object-maps, a nullable OpenAPI object map is generated as in 0.86.0."""
    assert_output(_nullable_object_map_verdicts(output_file, output_model_type), name="payloads")


def _nullable_object_map_verdicts(output_file: Path, output_model_type: str, *extra_args: str) -> str:
    """Generate the nullable object maps under --strict-nullable and return the verdicts on their payloads."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_object_maps.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--strict-nullable",
            *extra_args,
        ],
    )
    return _payload_verdicts(
        output_file, output_model_type, json.loads((PAYLOADS / "nullable_object_maps.json").read_text())
    )


@pytest.mark.parametrize("name", OBJECT_MAP_INPUTS)
def test_object_maps_schema_validators(output_file: Path, recwarn: pytest.WarningsRecorder, name: str) -> None:
    """Build the same maps where schema validators check the objects that one dict type cannot express.

    Under --check-pattern-maps-strictly, the dict of a validated map counts every key its patterns admit and keeps the
    keys of propertyNames, an object model of a type array keeps its other types, and a pattern checks every declared
    key whose schema does not imply the pattern's.
    """
    run_main_and_assert(
        input_path=OBJECT_MAPS / f"{name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators", STRICT_PATTERN_MAPS, SINGLE_DICT],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize("name", OBJECT_MAP_INPUTS)
def test_object_maps_schema_validators_without_single_dict(
    output_file: Path, recwarn: pytest.WarningsRecorder, name: str
) -> None:
    """Without --check-pattern-maps-strictly and --use-single-dict-for-object-maps, validators build 0.86.0 maps.

    The dict root of a validated map has ``str`` keys and counts its keys without its patterns, an object model of a
    type array leaves its other types out, and a pattern checks a declared key only where it rejects a value of a
    simple declared schema.
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
            STRICT_PATTERN_MAPS,
            SINGLE_DICT,
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
        extra_args=["--formatters", "builtin", "--generate-schema-validators", STRICT_PATTERN_MAPS, SINGLE_DICT],
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
        extra_args=["--formatters", "builtin", "--generate-schema-validators", STRICT_PATTERN_MAPS, SINGLE_DICT],
    )
    payloads = json.loads((PAYLOADS / "false_property.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param([STRICT_PATTERN_MAPS], id="strict"),
        pytest.param(["--read-only-write-only-model-type", "request-response"], id="request-response"),
    ],
)
def test_object_maps_schema_validators_false_all_of(output_file: Path, extra_args: list[str]) -> None:
    """Read an allOf that holds false, with only annotations beside it, as false in the patternProperties rules.

    The additionalProperties of ``m`` forbids the keys that match neither ``a`` nor ``^x``, and the pattern ``^x`` of
    ``p`` forbids the keys it matches, declared ``xa`` included. The rules type no value with ``Item``, so its readOnly
    property splits no class that names ``m``.
    """
    input_path = OBJECT_MAPS / "false_all_of.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators", *extra_args],
    )
    payloads = json.loads((PAYLOADS / "false_all_of.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


def test_object_maps_nested_false_all_of_annotated_value(output_file: Path) -> None:
    """Keep the module generated for an additionalProperties allOf whose member is an allOf holding false.

    No level of the value reads as false, so under --use-annotated the constraint merge meets the nested false and
    gives up, and the value is typed as a model of its own.
    """
    run_main_and_assert(
        input_path=OBJECT_MAPS / "false_all_of_nested_annotated.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--use-annotated"],
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param([SINGLE_DICT, TYPED_NULLABLE_MAP_VALUES], id="single-dict-typed-nullable-map-values"),
        pytest.param(["--read-unevaluated-properties"], id="read-unevaluated-properties"),
        pytest.param(["--read-only-write-only-model-type", "request-response"], id="request-response"),
    ],
)
@pytest.mark.parametrize("name", ["false_all_of_values", "false_values"])
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_maps_false_all_of_values(
    output_file: Path, recwarn: pytest.WarningsRecorder, name: str, output_model_type: str, extra_args: list[str]
) -> None:
    """Read the additionalProperties of a map and the items of an array that are an allOf holding false as false.

    ``false_all_of_values`` writes them as an allOf that holds false, with only annotations beside it, and
    ``false_values`` as false: both give the same module, whose maps admit no key they type and whose arrays no item
    past their prefixItems, at the root, as a property, as a map value, beside propertyNames, for the keys no pattern
    matches under --use-single-dict-for-object-maps and in a type array under --use-typed-nullable-map-values. The
    readOnly property of a member beside false splits no class under request-response models, and the warnings that
    name an inexact map are those of false.
    """
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--custom-file-header",
            "# generated by datamodel-codegen",
            *extra_args,
        ],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / "false_all_of_values.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, output_model_type, payloads), name="payloads")


def test_object_maps_false_all_of_unread_values(output_file: Path) -> None:
    """Keep the output of an allOf holding false where no value is typed with it.

    The additionalProperties of a model with properties, those a patternProperties validator owns and the items of
    a tuple of no items under --use-tuple-for-fixed-length-arrays type no value: their output is that of 0.86.0.
    """
    input_path = OBJECT_MAPS / "false_all_of_unread.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            SINGLE_DICT,
            "--generate-schema-validators",
            "--use-tuple-for-fixed-length-arrays",
        ],
    )
    payloads = json.loads((PAYLOADS / "false_all_of_unread.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize("name", ["false_all_of_unevaluated", "false_unevaluated"])
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_maps_false_all_of_unevaluated_properties(output_file: Path, name: str, output_model_type: str) -> None:
    """Read the unevaluatedProperties of a model without properties that is an allOf holding false as false.

    Under --read-unevaluated-properties ``false_all_of_unevaluated`` and ``false_unevaluated`` give the same module,
    whose root class accepts no key.
    """
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--custom-file-header",
            "# generated by datamodel-codegen",
            "--read-unevaluated-properties",
        ],
    )
    payloads = json.loads((PAYLOADS / "false_all_of_unevaluated.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, output_model_type, payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param([SINGLE_DICT, TYPED_NULLABLE_MAP_VALUES], id="single-dict-typed-nullable-map-values"),
        pytest.param(["--use-tuple-for-closed-arrays", "--use-tuple-for-fixed-items"], id="tuples"),
        pytest.param(["--use-annotated", "--field-constraints"], id="annotated"),
        pytest.param(["--use-closed-typed-dict"], id="closed-typed-dict"),
    ],
)
@pytest.mark.parametrize("kind", ["keywords", "tuples"])
@pytest.mark.parametrize("prefix", ["false_all_of_", "false_"])
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_maps_false_all_of_keywords(
    output_file: Path, prefix: str, kind: str, output_model_type: str, extra_args: list[str]
) -> None:
    """Read an allOf holding false as false in the other keywords that type the items of an array or the keys of a map.

    ``false_all_of_*`` writes ``false`` as an allOf that holds false, with only annotations beside it, and ``false_*``
    as ``false``: both give the same module. ``keywords`` holds unevaluatedItems, a prefixItems entry beside an items
    tail, patternProperties and propertyNames and a prefixItems entry in the extra_items type of a closed TypedDict,
    ``tuples`` the entries of a tuple ``items`` and additionalItems of draft 7. Each is read as a property, as an array
    item, as a map value and through a ``$ref``, and an array or a map that types a position or a key with it admits
    none.
    """
    input_path = OBJECT_MAPS / f"{prefix}{kind}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--custom-file-header",
            "# generated by datamodel-codegen",
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / f"false_all_of_{kind}.json").read_text())
    validator = (Draft202012Validator if kind == "keywords" else Draft7Validator)(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, output_model_type, payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args", [pytest.param([], id="default"), pytest.param(["--generate-schema-validators"], id="validators")]
)
@pytest.mark.parametrize("kind", ["keywords", "tuples"])
def test_object_maps_false_all_of_unread_keywords(output_file: Path, kind: str, extra_args: list[str]) -> None:
    """Keep the output of an allOf holding false in a keyword whose position or key no parse types.

    An unevaluatedItems beside an items tail or tuple, the prefixItems entries and tuple items no array types, an
    additionalItems beside no tuple, the patternProperties and propertyNames of a model with properties or
    combinators, the propertyNames beside patternProperties and the patternProperties a validator owns type nothing:
    their output is that of 0.86.0.
    """
    input_path = OBJECT_MAPS / f"false_all_of_unread_{kind}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    payloads = json.loads((PAYLOADS / f"false_all_of_unread_{kind}.json").read_text())
    validator = (Draft202012Validator if kind == "keywords" else Draft7Validator)(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize("name", ["recursive_pattern", "recursive_cycle", "recursive_additional", "recursive_compound"])
def test_object_maps_schema_validators_recursive_values(output_file: Path, name: str) -> None:
    """Name a map value type that is the model itself or a later model of its cycle as a string in the class body.

    The class body evaluates its validator rules before those models are bound, so the module imports only when the
    helper resolves the names as it validates.
    """
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators"],
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param(["--collapse-root-models"], id="collapse-root-models"),
        pytest.param(["--use-type-alias"], id="use-type-alias"),
        pytest.param(["--use-exact-imports"], id="use-exact-imports"),
    ],
)
def test_object_maps_schema_validators_cross_module_rule_types(output_dir: Path, extra_args: list[str]) -> None:
    """Import the models of another module that the validator rules of a class name, as its field types import them.

    The rules of ``Holder`` and ``Labels`` name ``Count`` and ``Name`` of the ``common`` module; ``--use-type-alias``
    makes the two classes only once every module is processed.
    """
    input_path = OBJECT_MAPS / "cross_module_rule_types.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp", "--generate-schema-validators", *extra_args],
    )
    payloads = json.loads((PAYLOADS / "cross_module_rule_types.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_dir, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    ("name", "extra_args"),
    [
        pytest.param("rule_type_module_cycle", [], id="class"),
        pytest.param("alias_rule_type_module_cycle", ["--use-type-alias"], id="use-type-alias"),
    ],
)
def test_object_maps_schema_validators_rule_type_module_cycle(
    output_dir: Path, name: str, extra_args: list[str]
) -> None:
    """Merge the modules that a validator rule type closes a cycle of into ``_internal``, as a field type closes one.

    ``common.Box`` holds ``other.Holder``, whose rule names ``Box``: the class body evaluates the rule as the module
    runs, so the two modules imported each other and the package failed to import. Under ``--use-type-alias``
    ``Holder`` is an alias written as a class for its rules only once every module is processed.
    """
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp", "--generate-schema-validators", *extra_args],
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_dir, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--reuse-scope", "tree"], id="tree-scope"),
        pytest.param(["--use-type-alias"], id="use-type-alias"),
        pytest.param(["--use-type-alias", "--collapse-reuse-models"], id="use-type-alias-collapse-reuse-models"),
    ],
)
def test_object_maps_schema_validators_same_name_rule_types(output_dir: Path, extra_args: list[str]) -> None:
    """Keep apart under --reuse-model the maps whose validator rules name models of other modules with one name.

    The rules of ``First`` and ``Second`` name ``Count`` of the ``left`` and of the ``right`` module, which render alike
    until imports are final.
    """
    input_path = OBJECT_MAPS / "same_name_rule_types.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            "--reuse-model",
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / "same_name_rule_types.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_dir, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param(["--field-constraints"], id="field_constraints"),
        pytest.param(["--use-annotated"], id="annotated"),
    ],
)
def test_object_maps_schema_validators_collapsed_rule_root(output_file: Path, extra_args: list[str]) -> None:
    """Keep writing a root model that a validator rule names after --collapse-root-models inlines it into its fields.

    The rules of ``counts`` and ``tallies`` check their values through the class, which keeps the minimum that
    the ``count`` field takes beside the inlined type.
    """
    input_path = OBJECT_MAPS / "collapsed_rule_root.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            "--collapse-root-models",
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / "collapsed_rule_root.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize("strategy", ["parent", "child"])
def test_object_maps_schema_validators_collapsed_rule_reference_root(output_file: Path, strategy: str) -> None:
    """Collapse a root model of a reference that a validator rule names by each --collapse-root-models-name-strategy.

    Under ``parent`` the inner model replaces ``Wrapper`` under its name and the rule of ``ws`` names it; under
    ``child`` ``Wrapper`` stays written for the rule.
    """
    input_path = OBJECT_MAPS / "collapsed_rule_reference_root.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            "--collapse-root-models",
            "--collapse-root-models-name-strategy",
            strategy,
        ],
    )
    payloads = json.loads((PAYLOADS / "collapsed_rule_reference_root.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


def test_object_maps_schema_validators_collapsed_rule_reference_root_modules(output_dir: Path) -> None:
    """Import the inner model that replaces a root model of another module for the validator rules that name it.

    The ``parent`` strategy gives the ``Inner`` model of the ``m`` module the name of ``a.Wrapper``; the rules of
    ``Map`` and of ``a.Map``, whose modules are processed before the holder of ``Wrapper`` is collapsed, then name
    ``m.Wrapper``.
    """
    input_path = OBJECT_MAPS / "collapsed_rule_reference_root_modules.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            "--collapse-root-models",
            "--collapse-root-models-name-strategy",
            "parent",
        ],
    )
    payloads = json.loads((PAYLOADS / "collapsed_rule_reference_root_modules.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_dir, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    ("name", "extra_args"),
    [
        pytest.param("internal_rule_alias", [], id="internal-module"),
        pytest.param(
            "collapsed_rule_alias_root",
            ["--collapse-root-models", "--collapse-root-models-name-strategy", "parent"],
            id="collapsed-root",
        ),
    ],
)
def test_object_maps_schema_validators_moved_rule_alias(output_dir: Path, name: str, extra_args: list[str]) -> None:
    """Keep the validator rules of an alias written as a class after an earlier pass gave it another path.

    Under ``--use-type-alias`` the alias ``Holder`` takes its class only once every module is processed. The class
    keeps the rules ``Holder`` holds when the import cycle of ``common`` and ``other`` moves it into ``_internal``,
    and when it replaces the collapsed ``a.Wrapper`` under the ``parent`` strategy.
    """
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            "--use-type-alias",
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_dir, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    ("name", "extra_args"),
    [
        pytest.param("collapsed_recursive_pattern_map", [SINGLE_DICT], id="pattern-map"),
        pytest.param("collapsed_recursive_property_names_map", [], id="property-names-map"),
    ],
)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_maps_collapsed_recursive_root(
    output_file: Path, name: str, extra_args: list[str], output_model_type: str
) -> None:
    """Import ``Any`` for the value of a recursive map whose root model --collapse-root-models inlines.

    ``Entry`` takes the dict of ``Map`` before the reference of that dict to ``Choice`` is collapsed, so its dict keeps
    a value that writes nothing, which a map with a key type writes as ``Any``. That ``Any`` was not imported, so the
    Pydantic module failed to import: the pattern map under --use-single-dict-for-object-maps and the propertyNames map
    without it.
    """
    input_path = OBJECT_MAPS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--collapse-root-models",
            "--output-model-type",
            output_model_type,
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / "collapsed_recursive_map.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, output_model_type, payloads), name="payloads")


@pytest.mark.cli_doc(
    options=["--check-pattern-maps-strictly"],
    option_description="""Check every key of the pattern maps that `--generate-schema-validators` validates.

Without the option the schema validators check a pattern map as in 0.86.0: a declared property skips the
`patternProperties` that match its name unless a simple witness shows the pattern rejects one of its values, and the
dict root of an object without `properties` counts its keys as if the patterns admitted none (`max_length=0` beside
`additionalProperties: false`, so every non-empty object is rejected). With `--check-pattern-maps-strictly`, a
declared property is also checked against every matching pattern whose schema its own does not imply, the dict root
counts every key a pattern admits and takes its keys from `propertyNames`, and the model of an object without
`properties` keeps the other types of its `type` array. The option has no effect without
`--generate-schema-validators`.""",
    input_schema="jsonschema/object_maps/strict_pattern_maps.json",
    cli_args=["--generate-schema-validators", "--check-pattern-maps-strictly"],
    golden_output="main/jsonschema/object_maps/check_pattern_maps_strictly[with-option].py",
    comparison_output="main/jsonschema/object_maps/check_pattern_maps_strictly[without-option].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [pytest.param([STRICT_PATTERN_MAPS], id="with-option"), pytest.param([], id="without-option")],
)
def test_check_pattern_maps_strictly(output_file: Path, extra_args: list[str]) -> None:
    """Check a declared key against its pattern and count the keys of a closed pattern map with the option.

    Without --check-pattern-maps-strictly the validators are those of 0.86.0: ``x-id`` skips the ``maxLength`` of the
    ``^x-`` pattern, and the closed ``labels`` map rejects every non-empty object.
    """
    input_path = OBJECT_MAPS / "strict_pattern_maps.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp", "--generate-schema-validators", *extra_args],
    )
    payloads = json.loads((PAYLOADS / "strict_pattern_maps.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([STRICT_PATTERN_MAPS], id="with-option"),
        pytest.param([], id="without-option"),
        pytest.param(["--collapse-root-models"], id="collapse-root-models"),
    ],
)
def test_check_pattern_maps_strictly_type_alias(output_file: Path, extra_args: list[str]) -> None:
    """Write a validated map that --use-type-alias would write as an alias as the RootModel it is without the option.

    An alias has no class body that could hold the validator rules of the closed ``labels`` map.
    """
    run_main_and_assert(
        input_path=OBJECT_MAPS / "strict_pattern_maps.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            "--use-type-alias",
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / "strict_pattern_maps.json").read_text())
    assert_output(_payload_verdicts(output_file, "pydantic_v2.BaseModel", payloads), name="payloads")


def test_object_maps_released_pattern_properties_hook(tmp_path: Path) -> None:
    """Call a ``parse_pattern_properties`` override written for 0.86.0 with the arguments of that release.

    Under --use-single-dict-for-object-maps, the dict of a map whose additionalProperties types the keys that match
    no pattern is built without the hook.
    """
    assert_output(_released_hook_parser(tmp_path, single_dict=True).parse())


def test_object_maps_released_pattern_properties_hook_without_single_dict(tmp_path: Path) -> None:
    """Without --use-single-dict-for-object-maps, the hook builds every pattern map, as in 0.86.0."""
    assert_output(_released_hook_parser(tmp_path, single_dict=False).parse())


def _released_hook_parser(tmp_path: Path, *, single_dict: bool) -> JsonSchemaParser:
    """Return a parser for two pattern maps whose ``parse_pattern_properties`` override was written for 0.86.0."""

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
    return ReleasedHookParser(
        input_path, base_path=tmp_path, formatters=[Formatter.BUILTIN], use_single_dict_for_object_maps=single_dict
    )


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_map_bodies(output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str) -> None:
    """Build the models of object-kind request and response bodies in the paths scope as definitions are built."""
    warnings, verdicts = _object_map_body_outputs(output_file, recwarn, output_model_type, SINGLE_DICT)
    assert_output(warnings, name="warnings")
    assert_output(verdicts, name="payloads")


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_map_bodies_without_single_dict(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str
) -> None:
    """Build the same body models without --use-single-dict-for-object-maps, whose maps are those of 0.86.0."""
    warnings, verdicts = _object_map_body_outputs(output_file, recwarn, output_model_type)
    assert_output(warnings, name="warnings")
    assert_output(verdicts, name="payloads")


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_object_map_bodies_as_definitions(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str
) -> None:
    """Give every object map body the root model of its definition under --openapi-as-definitions paths-bodies."""
    warnings, verdicts = _object_map_body_outputs(
        output_file, recwarn, output_model_type, SINGLE_DICT, "--openapi-as-definitions", "paths-bodies"
    )
    assert_output(warnings, name="warnings")
    assert_output(verdicts, name="payloads")


def _object_map_body_outputs(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, *extra_args: str
) -> tuple[str, str]:
    """Generate the object map bodies of the paths scope and return the warnings and the verdicts on the payloads."""
    input_path = OPEN_API_DATA_PATH / "object_map_bodies.yaml"
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
            *extra_args,
        ],
    )
    payloads = json.loads((PAYLOADS / "object_map_bodies.json").read_text())
    return (
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        _body_verdicts(input_path, output_file, output_model_type, payloads),
    )


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
    options=["--openapi-as-definitions"],
    option_description="""Give OpenAPI paths bodies or ref components the models their definitions get.

The `--openapi-as-definitions` option takes one or more values, `paths-bodies` and `ref-components`, each a
separate change.

`paths-bodies`: with `--openapi-scopes paths` (or `webhooks`), request and response bodies are parsed by their own
dispatch, which types some object bodies inline although a schema definition of the same body gets a model: a body
whose type array admits objects, such as `type: [object, "null"]`, or one with `additionalProperties`,
`patternProperties` or `propertyNames` and no `type`. With `--openapi-as-definitions paths-bodies`, such a body gets
the model its definition would get, such as a root model of `dict[str, Any] | None`, whose values
`--use-typed-nullable-map-values` types as declared. A document whose only bodies are such schemas then generates
models instead of failing with "Models not found in the input data", whose message names this value when it
applies. A body of `type: object` with `patternProperties` or `propertyNames` and no `properties` gets the root model
of a map its definition gets, such as `dict[constr(pattern=r'^a'), int]`, instead of the model or `dict[str, ...]` of
0.86.0; one whose `patternProperties` admit its only keys (`additionalProperties: false`) gets that root model with or
without the value. Bodies typed as arrays, combinations, enums and references, and the `api` scope, are unchanged.

`ref-components`: a component such as `Tree: {$ref: '#/components/schemas/Node', minProperties: 1}`, where `Node`
leads back to `Tree` or to itself, cannot merge its sibling keywords into `Node`. Without the value it gets no model,
as in 0.86.0, and a reference to it is typed by `Node`; with `--openapi-as-definitions ref-components` it gets the
root model `Tree(RootModel[Node])` that the same schema gets as a `$defs` entry, references to it name `Tree`, and a
component nothing references is generated too. Such a component in another document gets that model with or without
the value.""",
    input_schema="openapi/type_array_request_body.yaml",
    cli_args=["--openapi-scopes", "paths", "--openapi-as-definitions", "paths-bodies"],
    golden_output=(
        "jsonschema/object_maps/paths_bodies_as_definitions[default-pydantic_v2.BaseModel-type_array_request_body].py"
    ),
    primary=True,
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
            "--openapi-as-definitions",
            "paths-bodies",
            *OPTION_SETS[option_set],
        ],
    )
    payloads = json.loads((PAYLOADS / "paths_bodies_as_definitions.json").read_text())[name]
    assert_output(_body_verdicts(input_path, output_file, output_model_type, payloads), name="payloads")


@pytest.mark.parametrize("name", PATHS_BODIES[:3])
def test_paths_bodies_inline_hint(output_file: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    """Name --openapi-as-definitions paths-bodies when the only bodies are object bodies typed inline."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{name}.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--openapi-scopes", "paths"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=(
            "Models not found in the input data; request or response bodies of the paths or webhooks scope are "
            "typed inline, and --openapi-as-definitions paths-bodies gives them the models their schema definitions get"
        ),
    )
