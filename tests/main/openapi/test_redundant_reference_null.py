"""A reference to a model that holds null itself restates its None only without --omit-redundant-reference-null."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
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

OPTION = "--omit-redundant-reference-null"
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
TYPED_DICT_SKIP = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="Pydantic validates typing.TypedDict from Python 3.12 on"
)
MATRIX_PAYLOADS: dict[str, Any] = {
    "valid": {
        "name_f": None,
        "name_a": ["a", None],
        "name_m": {"k": None},
        "name_u": None,
        "name_o": None,
        "name_d": None,
        "color_f": None,
        "color_a": ["red", None],
        "color_m": {"k": None},
        "color_u": None,
        "size_f": "s",
        "size_a": ["m"],
        "size_m": {"k": "s"},
        "size_u": 1,
        "pet_f": {"id": 1},
        "pet_a": [],
        "pet_m": {},
        "pet_u": 2,
    },
    "invalid": [{"name_f": 1}, {"pet_f": None}, {"size_f": None}, {"pet_a": [None]}],
}
PYDANTIC_MODEL_TYPES = ("pydantic_v2.BaseModel", "pydantic_v2.dataclass")
"""A model of ``type: "null"`` is ``None``: the other backends cannot evaluate ``Nothing | None`` without the option."""
DOCUMENTS: dict[str, tuple[str, tuple[str, ...], dict[str, Any]]] = {
    "matrix": ("redundant_reference_null.yaml", ("--strict-nullable",), MATRIX_PAYLOADS),
    "matrix_type_alias": ("redundant_reference_null.yaml", ("--strict-nullable", "--use-type-alias"), MATRIX_PAYLOADS),
    "null_type": (
        "redundant_reference_null_null_type.yaml",
        ("--strict-nullable",),
        {
            "valid": {"nothing_f": None, "nothing_a": [None], "nothing_m": {"k": None}, "nothing_u": None},
            "invalid": [{"nothing_f": 0}, {"nothing_a": [1]}],
        },
    ),
    "nullable_oas30": (
        "redundant_reference_null_oas30.yaml",
        ("--strict-nullable",),
        {
            "valid": {
                "items_s": ["a", None],
                "items_e": ["a"],
                "str_f": None,
                "str_a": ["x", None],
                "enum_f": None,
                "enum_a": ["a", None],
                "obj_f": None,
                "obj_a": [None, {"id": 1}],
            },
            "invalid": [{"str_f": 1}, {"enum_f": "c"}, {"obj_f": 1}],
        },
    ),
    "inline_items": (
        "redundant_reference_null_inline.yaml",
        ("--strict-nullable", "--use-annotated", "--field-constraints"),
        {"valid": {"tags": ["a", None]}, "invalid": [{"tags": [""]}, {"tags": [1]}]},
    ),
}


def _decode(model: Any, output_model_type: str, document: object) -> tuple[str, Any]:
    """Return whether a generated model accepts a JSON document, and the value it parses or the first error type."""
    text = json.dumps(document)
    try:
        if output_model_type == "msgspec.Struct":
            return "accepted", msgspec.json.decode(text, type=model)
        return "accepted", TypeAdapter(model).validate_json(text)
    except ValidationError as error:
        return f"rejected ({error.errors()[0]['type']})", None
    except msgspec.ValidationError:
        return "rejected", None


def _payload_lines(output_file: Path, output_model_type: str, model_name: str, payloads: dict[str, Any]) -> str:
    """Return the verdict and the parsed value of the valid payload, and the verdict of each invalid change of it."""
    with _generated_model(output_file, "redundant_reference_null_model", model_name) as model:
        verdict, parsed = _decode(model, output_model_type, payloads["valid"])
        lines = [f"valid: {verdict}", f"parsed: {parsed!r}"]
        lines.extend(
            f"invalid {', '.join(change)}: {_decode(model, output_model_type, {**payloads['valid'], **change})[0]}"
            for change in payloads["invalid"]
        )
    return "".join(f"{line}\n" for line in lines)


@pytest.mark.cli_doc(
    options=["--omit-redundant-reference-null"],
    option_description="""Do not write None again at a reference to a model that holds null itself.

When a field, an array item, a map value or a union member references a schema that admits null, the reference
writes `| None` beside the model. A root model whose type admits None, a type alias whose type admits None and an
enum with a None member hold that null themselves, so with this option the reference writes the model alone and a null
parses to the model (`Name(root=None)` instead of `None`). An object model, and an enum without a None member, cannot
hold null and keep the `| None`; so does a None the reference writes itself, such as `anyOf: [{$ref}, {type: null}]`
or an optional field's default.""",
    input_schema="openapi/redundant_reference_null.yaml",
    cli_args=["--strict-nullable", "--omit-redundant-reference-null"],
    golden_output="main/openapi/redundant_reference_null/redundant_reference_null[matrix-pydantic_v2.BaseModel-option].py",
    comparison_output="main/openapi/redundant_reference_null/redundant_reference_null[matrix-pydantic_v2.BaseModel-default].py",
)
@pytest.mark.parametrize("option", [pytest.param((), id="default"), pytest.param((OPTION,), id="option")])
@pytest.mark.parametrize(
    ("document", "output_model_type"),
    [
        pytest.param(
            document,
            output_model_type,
            id=f"{document}-{output_model_type}",
            marks=[TYPED_DICT_SKIP] if output_model_type == "typing.TypedDict" else [],
        )
        for document in DOCUMENTS
        for output_model_type in (PYDANTIC_MODEL_TYPES if document == "null_type" else OUTPUT_MODEL_TYPES)
    ],
)
def test_redundant_reference_null(
    output_file: Path, document: str, option: tuple[str, ...], output_model_type: str
) -> None:
    """Drop the None a reference restates of a model holding null only with the option, and parse null either way.

    Root models and type aliases whose type admits None, and enums with a None member, take the null; object models,
    enums without a None member and the None a use writes itself keep it. The schema accepts and rejects the payloads
    as each generated model does.
    """
    file_name, options, payloads = DOCUMENTS[document]
    input_path = OPEN_API_DATA_PATH / file_name
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--formatters", "builtin", "--output-model-type", output_model_type, *options, *option),
            *(["--use-msgspec-nullable-enum-alias"] if output_model_type == "msgspec.Struct" else []),
        ],
    )
    if (spec := yaml.safe_load(input_path.read_text()))["openapi"].startswith("3.1"):
        validator = Draft202012Validator({**spec, "$ref": "#/components/schemas/Use"})
        validator.validate(payloads["valid"])
        for change in payloads["invalid"]:
            with pytest.raises(SchemaValidationError):
                validator.validate({**payloads["valid"], **change})
    assert_output(_payload_lines(output_file, output_model_type, "Use", payloads), name="payloads")


MOVED_OUTPUTS: dict[str, tuple[Path, str, tuple[str, ...]]] = {
    "root_model_aliases": (DATA_PATH / "dynamic_models" / "root_model_aliases.json", "jsonschema", ()),
    "allof_nullable_ref": (JSON_SCHEMA_DATA_PATH / "allof_root_types" / "nullable_ref.json", "jsonschema", ()),
    "recursive_nullable_union": (
        JSON_SCHEMA_DATA_PATH / "recursive_nullable_union.json",
        "jsonschema",
        ("--target-python-version", "3.10", "--use-standard-collections", "--use-union-operator"),
    ),
    "redundant_constraints": (
        JSON_SCHEMA_DATA_PATH / "redundant_constraints.json",
        "jsonschema",
        ("--use-annotated", "--use-non-positive-negative-number-constrained-types", "--use-single-value-enum-as-const"),
    ),
    "ref_type_has_null": (JSON_SCHEMA_DATA_PATH / "ref_type_has_null.json", "jsonschema", ("--use-union-operator",)),
    "root_model_metadata": (
        JSON_SCHEMA_DATA_PATH / "root_model_metadata.json",
        "jsonschema",
        ("--target-python-version", "3.10"),
    ),
    "use_tag_as_union_variant_name_fallbacks": (
        JSON_SCHEMA_DATA_PATH / "use_tag_as_union_variant_name_fallbacks.json",
        "jsonschema",
        ("--use-tag-as-union-variant-name", "--external-ref-mapping", "external_null.json=external.models"),
    ),
    "null_type_array_definition": (
        JSON_SCHEMA_DATA_PATH / "null_type_array_definition.json",
        "jsonschema",
        ("--target-python-version", "3.10"),
    ),
    "allof_no_merge_constraint_type_shape": (
        OPEN_API_DATA_PATH / "allof_no_merge_constraint_type_shape.yaml",
        "openapi",
        ("--target-python-version", "3.10", "--allof-merge-mode", "none", "--use-tuple-for-fixed-items"),
    ),
    "discriminator_duplicate_value": (
        OPEN_API_DATA_PATH / "discriminator_duplicate_value.yaml",
        "openapi",
        ("--target-python-version", "3.10"),
    ),
}


@pytest.mark.parametrize("name", MOVED_OUTPUTS)
def test_redundant_reference_null_stored_inputs(output_file: Path, name: str) -> None:
    """Drop the restated None from the stored inputs whose references name a model holding null, with the option."""
    input_path, input_file_type, options = MOVED_OUTPUTS[name]
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=["--disable-timestamp", "--formatters", "builtin", *options, OPTION],
    )


@pytest.mark.parametrize(
    "output_model_type",
    [
        pytest.param(output_model_type, marks=[TYPED_DICT_SKIP] if output_model_type == "typing.TypedDict" else [])
        for output_model_type in OUTPUT_MODEL_TYPES
    ],
)
def test_redundant_reference_null_object_maps(output_file: Path, output_model_type: str) -> None:
    """Under --strict-nullable, a nullable object map referenced as a map holding null parses null to the map."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_object_maps.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, "--strict-nullable", OPTION],
    )
    payloads = json.loads((DATA_PATH / "payloads" / "object_maps" / "nullable_object_maps.json").read_text())
    assert_output(_payload_lines(output_file, output_model_type, "Model", payloads), name="payloads")


@pytest.mark.parametrize("option", [pytest.param((), id="default"), pytest.param((OPTION,), id="option")])
@pytest.mark.parametrize(
    "output_model_type",
    [
        pytest.param(output_model_type, marks=[TYPED_DICT_SKIP] if output_model_type == "typing.TypedDict" else [])
        for output_model_type in OUTPUT_MODEL_TYPES
    ],
)
def test_reference_null_kept_after_duplicate_deletion(
    output_file: Path, output_model_type: str, option: tuple[str, ...]
) -> None:
    """Keep the None of a nullable object model at a use whose model deletion points at a duplicate without one.

    The branch that does not narrow the property accepts null as the schema does; the branch that narrows it to an
    object rejects it.
    """
    input_path = JSON_SCHEMA_DATA_PATH / "nullable_object_narrowed_in_oneof.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *option],
    )
    payloads: dict[str, Any] = {"valid": {"kind": "b", "item": None}, "invalid": [{"kind": "a"}, {"item": 1}]}
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    assert_output(_payload_lines(output_file, output_model_type, "Model", payloads), name="payloads")
