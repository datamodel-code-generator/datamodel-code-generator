"""A field writes the constraints its schema states, the same at an object property and at a root model."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import ValidationError

from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    _model_json_validator,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

SITES = JSON_SCHEMA_DATA_PATH / "field_constraint_sites"
PAYLOADS = DATA_PATH / "payloads" / "field_constraint_sites"
LITERAL_ENUMS = ["--field-constraints", "--enum-field-as-literal", "all"]
ANYURL_WITHOUT_CONSTRAINTS = pytest.mark.filterwarnings("ignore:fields holding AnyUrl are written without:UserWarning")


@pytest.mark.parametrize(
    ("name", "extra_args"),
    [
        ("array_item_roots", []),
        ("array_item_roots", ["--field-constraints"]),
        ("array_item_roots", ["--use-type-alias"]),
        ("array_item_roots", ["--collapse-root-models"]),
        ("enum_literal_properties", LITERAL_ENUMS),
        ("enum_literal_properties", [*LITERAL_ENUMS, "--filter-literal-values"]),
        ("mixed_type_map_property", []),
        ("array_counts_beside_other_types", []),
        ("array_counts_beside_other_types", ["--field-constraints"]),
        ("array_counts_beside_retyped", []),
        ("literal_patterns", LITERAL_ENUMS),
        ("literal_patterns", ["--use-annotated", "--enum-field-as-literal", "all"]),
        ("literal_patterns", [*LITERAL_ENUMS, "--output-model-type", "pydantic_v2.dataclass"]),
        ("literal_patterns", [*LITERAL_ENUMS, "--collapse-root-models"]),
        ("literal_patterns", [*LITERAL_ENUMS, "--use-type-alias"]),
        ("union_root_keywords", ["--field-constraints"]),
        ("union_root_keywords", ["--field-constraints", "--collapse-root-models"]),
        ("union_root_keywords", ["--field-constraints", "--use-type-alias"]),
        ("union_root_keywords", ["--use-annotated", "--collapse-root-models"]),
        ("union_root_keywords", ["--field-constraints", "--output-model-type", "pydantic_v2.dataclass"]),
        ("union_root_kept_keywords", ["--field-constraints", "--use-subclass-enum", "--collapse-root-models"]),
        ("union_root_kept_keywords", ["--field-constraints", "--use-subclass-enum", "--use-type-alias"]),
        pytest.param(
            "union_root_kept_keywords",
            [
                "--field-constraints",
                "--use-specialized-enum",
                "--target-python-version",
                "3.11",
                "--collapse-root-models",
            ],
            marks=pytest.mark.skipif(sys.version_info < (3, 11), reason="StrEnum needs Python 3.11"),
        ),
        *(
            pytest.param("union_root_url_keywords", ["--field-constraints", *args], marks=ANYURL_WITHOUT_CONSTRAINTS)
            for args in (["--use-type-alias"], ["--output-model-type", "pydantic_v2.dataclass"])
        ),
        ("union_array_string_lengths", []),
        ("union_array_string_lengths", ["--collapse-root-models"]),
    ],
)
def test_field_constraint_sites(output_file: Path, name: str, extra_args: list[str]) -> None:
    """Write the constraints the schema states, and the payloads the schema rejects are rejected.

    The root model of an array item reads its keywords whatever the options, takes the item counts its boolean items
    imply and writes no length beside a tuple, as the array of an object property does; the ``Literal`` of an enum
    property under ``--field-constraints`` writes them as a const does, unless ``--filter-literal-values`` removed
    the values they reject; a map beside a type list that joins an array keeps its property counts; the item counts
    of ``contains`` belong to a field typed as an array, so a string or a model that carries them accepts what the
    schema accepts, and so does a field whose schema declares no array, as a string typed ``datetime.date`` or an
    object with array values; the pattern of a ``Literal`` that a root model or a const writes selects the
    ``python-re`` regex engine when Rust refuses it, as the pattern of a string does. A keyword beside a union leaves
    the field that holds the union when a member cannot take it and every member keeps the check without it in the
    output (the class or type alias of a branch whose call writes it, a value it does not apply to, None), so a valid
    value raises no ``TypeError`` (#6022), also where a root model collapses into a union of one member. Where a member
    would lose the check, as a string, a ``str`` enum or an ``AnyUrl`` that writes no copy of it, the field keeps
    today's output, whose ``TypeError`` for the other members stays (#6053); the payloads of those fields use the
    members that keep the check. Without ``--field-constraints``, a string length beside a union of an array and a
    string limits the string only, at a property and at the items of an array, as 0.88.0 wrote it (#6189).
    """
    input_path = SITES / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(json.loads(input_path.read_text(encoding="utf-8")))
    for payload in payloads["valid"]:
        validator.validate(payload)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"][0], **change})
    with _generated_model(output_file, f"field_constraint_sites_{name}", "Model") as model:
        validate_json = _model_json_validator(model)
        for payload in payloads["valid"]:
            validate_json(json.dumps(payload))
        for change in payloads["invalid"]:
            _rejected(validate_json, {**payloads["valid"][0], **change})


def test_root_model_of_a_reference_takes_no_item_counts(output_file: Path) -> None:
    """Write no item count beside the model a root names, which Pydantic cannot apply it to.

    ``min_length`` on a field whose type is a model raises ``Unable to apply constraint`` when the model validates any
    array, and 0.88.0 wrote none at a root.
    """
    run_main_and_assert(
        input_path=SITES / "array_counts_root_reference.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin"],
    )
    with _generated_model(output_file, "field_constraint_sites_array_counts_root_reference", "Model") as model:
        _model_json_validator(model)(json.dumps(["a", "b"]))


@pytest.mark.filterwarnings("ignore::datamodel_code_generator.NamingHintWarning")
def test_msgspec_keeps_its_output_beside_a_union(output_file: Path) -> None:
    """Keep the msgspec output of the keywords beside a union, which msgspec leaves out itself with a warning.

    ``msgspec.Meta`` takes no keyword on a union, so the field writes none of them and the decision that leaves out what
    a member cannot take (#6022) has nothing to leave out; the payloads decode as the schema says.
    """
    with pytest.warns(UserWarning, match="msgspec.Meta does not take"):
        run_main_and_assert(
            input_path=SITES / "union_root_keywords.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--formatters", "builtin", "--output-model-type", "msgspec.Struct", "--field-constraints"],
        )
    payloads = json.loads((PAYLOADS / "union_root_keywords.json").read_text(encoding="utf-8"))
    with _generated_model(output_file, "field_constraint_sites_msgspec_union_root_keywords", "Model") as model:
        for payload in payloads["valid"]:
            msgspec.json.decode(json.dumps(payload), type=model)
        for change in payloads["invalid"]:
            with pytest.raises(msgspec.ValidationError):
                msgspec.json.decode(json.dumps({**payloads["valid"][0], **change}), type=model)


def _rejected(validate_json: Any, document: object) -> None:
    """Validate a JSON document that the generated model must reject."""
    with pytest.raises(ValidationError):
        validate_json(json.dumps(document))
