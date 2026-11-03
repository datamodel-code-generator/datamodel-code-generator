"""A field writes the constraints its schema states, the same at an object property and at a root model."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

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
    object with array values.
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


def _rejected(validate_json: Any, document: object) -> None:
    """Validate a JSON document that the generated model must reject."""
    with pytest.raises(ValidationError):
        validate_json(json.dumps(document))
