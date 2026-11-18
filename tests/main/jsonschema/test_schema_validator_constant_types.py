"""Reject the other JSON type for a const or enum under --generate-schema-validators (#7053)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pydantic
import pytest
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_constant_types.json"
DOCUMENTS = (
    {"one": 1},
    {"one": 1.0},
    {"one": True},
    {"yes": True},
    {"yes": 1},
    {"yes": False},
    {"number": 2},
    {"number": True},
    {"mixed": "x"},
    {"mixed": 1},
    {"mixed": True},
    {"both": True},
    {"both": 1},
    {"items": [1, 2.0]},
    {"items": [2, True]},
    {"values": {"a": 1}},
    {"values": {"a": True}},
    {"color": 0},
    {"color": False},
    {"switch": 1},
    {"switch": True},
    {"switch": False},
    {"closed": None},
    {"closed": True},
    {"open": True},
)


@pytest.mark.parametrize(
    "option",
    [
        ("--generate-schema-validators",),
        ("--schema-validator-type", "pydantic-v2"),
        ("--generate-schema-validators", "--collapse-root-models", "--enum-field-as-literal", "all"),
    ],
)
def test_schema_validator_constant_types(output_file: Path, option: tuple[str, ...]) -> None:
    """Accept and reject booleans and numbers for constants as the schema does, at properties, items and values.

    Python's ``True == 1`` lets a Literal or an Enum take ``true`` for ``1``; the validators reject it, and the
    number ``1.0`` that JSON Schema equals to ``1`` stays accepted.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            *option,
        ],
    )
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    with _generated_model(output_file, "schema_validator_constant_types", "Constants") as model:
        assert_output(
            "".join(
                f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
                f"{_verdict(model, document)}\n"
                for document in DOCUMENTS
            ),
            name="payloads",
        )


def _verdict(model: type[pydantic.BaseModel], document: object) -> str:
    """Return whether the generated model accepts a document in Python and in JSON."""
    return f"{_accepts(model.model_validate, document)}/{_accepts(model.model_validate_json, json.dumps(document))}"


def _accepts(validate: Callable[..., object], value: object) -> str:
    """Return whether ``validate`` accepts ``value``."""
    try:
        validate(value)
    except pydantic.ValidationError:
        return "rejected"
    return "accepted"


UNION_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_constant_types_union.json"
IGNORED_ENUM_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_constant_types_ignored_enum.json"


@pytest.mark.parametrize(
    ("input_path", "documents", "option"),
    [
        (
            UNION_PATH,
            (
                {"objects": {"x": 1}},
                {"objects": {"x": True}},
                {"objects": {"x": 2}},
                {"arrays": [1]},
                {"arrays": [True]},
                {"arrays": [2]},
            ),
            (),
        ),
        (
            IGNORED_ENUM_PATH,
            ({"flag": True}, {"flag": 1}, {"flag": 2}, {"fixed": 1}, {"fixed": True}, {"fixed": 2}),
            ("--ignore-enum-constraints",),
        ),
    ],
    ids=["union", "ignored_enum"],
)
def test_schema_validator_constant_types_rule_scope(
    output_file: Path, input_path: Path, documents: tuple[dict[str, object], ...], option: tuple[str, ...]
) -> None:
    """Compare a union's branches by the JSON type of their constants, and leave out an ignored enum's values."""
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
            *option,
        ],
    )
    schema = json.loads(input_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    if option:
        validator = Draft202012Validator({
            **schema,
            "properties": {
                name: {key: value for key, value in prop.items() if key != "enum"}
                for name, prop in schema["properties"].items()
            },
        })
    with _generated_model(output_file, input_path.stem, schema["title"]) as model:
        assert_output(
            "".join(
                f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
                f"{_verdict(model, document)}\n"
                for document in documents
            ),
        )
