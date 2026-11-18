"""Check the numeric bounds that an if/then/else puts on properties under --generate-schema-validators (#7054)."""

from __future__ import annotations

import enum
import json
import typing
from typing import TYPE_CHECKING

import pydantic
import pytest
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.jsonschema import specification_with

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_conditional_bounds.json"
DOCUMENTS = (
    {"kind": "a", "value": 10},
    {"kind": "a", "value": 1},
    {"kind": "b", "value": 5},
    {"kind": "b", "value": 10},
    {"kind": "b", "value": 5, "flagged": {"flag": True, "size": 11}},
    {"kind": "b", "value": 5, "flagged": {"flag": True, "size": 10}},
    {"kind": "b", "value": 5, "flagged": {"flag": 1, "size": 1}},
    {"kind": "b", "value": 5, "readings": [{"unit": "c", "temp": 99.5}, {"unit": "f", "temp": 212}]},
    {"kind": "b", "value": 5, "readings": [{"unit": "c", "temp": 100}]},
    {"kind": "b", "value": 5, "child": {"mode": "few", "count": 3}},
    {"kind": "b", "value": 5, "child": {"mode": "few", "count": 4}},
    {"kind": "b", "value": 5, "child": {"mode": "many", "count": 4, "limit": 100}},
    {"kind": "b", "value": 5, "child": {"mode": "many", "limit": 99}},
    {"kind": "b", "value": 5, "combo": {"t": "x", "n": 3}},
    {"kind": "b", "value": 5, "combo": {"t": "x", "n": 4}},
    {"kind": "b", "value": 5, "combo": {"t": "x"}},
    {"kind": "b", "value": 5, "combo": {"t": "y", "n": 4}},
    {"kind": "b", "value": 5, "derived": {"p": 0}},
    {"kind": "b", "value": 5, "derived": {"p": 4}},
    {"kind": "b", "value": 5, "derived": {"p": 5}},
    {"kind": "b", "value": 5, "gate": {"open": True}},
    {"kind": "b", "value": 5, "gate": {"open": True, "key": "k"}},
    {"kind": "b", "value": 5, "diamond": {"mode": "few", "count": 4}},
    {"kind": "b", "value": 5, "diamond": {"mode": "many", "limit": 1}},
    {"kind": "b", "value": 5, "diamond": {"mode": "many", "limit": 100}},
)
REF_SIBLINGS_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_conditional_bounds_ref_siblings.json"
READ_ONLY_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_conditional_bounds_read_only.json"
ALL_OF_REF_SIBLINGS_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_conditional_bounds_all_of_ref_siblings.json"
INPUT_NAMES_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_conditional_bounds_input_names.json"


@pytest.mark.parametrize(
    "option",
    [
        ("--generate-schema-validators",),
        ("--schema-validator-type", "pydantic-v2"),
        ("--preset", "practical-py310-20261113", "--generate-schema-validators"),
    ],
)
def test_schema_validator_conditional_bounds(output_file: Path, option: tuple[str, ...]) -> None:
    """Accept and reject numbers as the branch the condition selects bounds them, as the schema does.

    The condition reads JSON values (``1`` is not ``true``), a branch that references a schema bounds through it, and
    a model checks the bounds of its base class beside its own.
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
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text(encoding="utf-8")))
    with _generated_model(output_file, INPUT_PATH.stem, "Bounds") as model:
        assert_output(
            "".join(
                f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
                f"{_verdict(model, document)}\n"
                for document in DOCUMENTS
            ),
            name="payloads",
        )


@pytest.mark.parametrize(
    ("input_path", "model_name", "documents", "option", "mapped"),
    [
        (
            REF_SIBLINGS_PATH,
            "Legacy",
            (
                {"kind": "a", "value": 2},
                {"kind": "a", "value": 6},
                {"kind": "b", "value": 6},
                {"kind": "b", "value": 6, "note": "n"},
            ),
            ("--external-ref-mapping", "common.json=common_models"),
            {"common.json": {"definitions": {"Note": {"type": "string"}}}},
        ),
        (
            READ_ONLY_PATH,
            "OrderResponse",
            ({"kind": "a", "value": 10}, {"kind": "a", "value": 1}, {"kind": "a", "value": 10, "id": 5}),
            ("--read-only-write-only-model-type", "request-response"),
            {},
        ),
        (
            ALL_OF_REF_SIBLINGS_PATH,
            "Siblings",
            (
                {"kind": "a", "value": 10},
                {"kind": "a", "value": 1},
                {"kind": "b", "size": 10},
                {"kind": "b", "size": 1},
                {"kind": "b", "value": 1},
                {"kind": "a", "size": 1},
            ),
            (),
            {},
        ),
        (
            INPUT_NAMES_PATH,
            "Wire",
            (
                {"wire-kind": "b", "wire_kind": "a", "value": 1},
                {"wire-kind": "a", "wire_kind": "b", "value": 1},
                {"wire-kind": "a", "value": 1},
                {"wire-kind": "b", "value": 1},
            ),
            ("--allow-population-by-field-name",),
            {},
        ),
    ],
    ids=["ref_siblings", "read_only", "all_of_ref_siblings", "input_names"],
)
def test_schema_validator_conditional_bounds_rule_scope(
    output_file: Path,
    input_path: Path,
    model_name: str,
    documents: tuple[dict[str, object], ...],
    option: tuple[str, ...],
    mapped: dict[str, dict[str, object]],
) -> None:
    """Read the bounds a draft-07 ``$ref`` names but not those beside it, nor a mapped one; keep them on variants.

    In draft 2020-12 the bounds beside an allOf ``$ref`` apply, in a branch and in a property; a property with several
    input names is read, in the condition too, by the first one the input holds, which its field reads.

    ``mapped`` holds the documents that the mapped references name, which only the schema validator reads.
    """
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
    validator_class = validator_for(schema)
    registry = Registry().with_resources(
        (
            uri,
            Resource.from_contents(
                document, default_specification=specification_with(validator_class.META_SCHEMA["$schema"])
            ),
        )
        for uri, document in mapped.items()
    )
    validator = validator_class(schema, registry=registry)
    with _generated_model(output_file, input_path.stem, model_name) as model:
        assert_output(
            "".join(
                f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
                f"{_verdict(model, document)}\n"
                for document in documents
            ),
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


ENUM_MEMBERS_PATH = JSON_SCHEMA_DATA_PATH / "schema_validator_enum_members.json"
ENUM_MEMBER_DOCUMENTS = (
    {"kind": "a", "value": 10, "name": "n"},
    {"kind": "a", "value": 1, "name": "n"},
    {"kind": "a", "value": 10},
    {"kind": "b", "value": 1},
    {"kind": "b", "value": 10},
    {"kind": "b", "value": 1, "marks": ["p", "q"]},
    {"kind": "b", "value": 1, "marks": ["p", "p"]},
    {"kind": "b", "value": 1, "marks": ["q"]},
    {"kind": "a", "value": 10, "name": "n", "level": 10},
    {"kind": "a", "value": 10, "name": "n", "level": 2},
    {"kind": "b", "value": 1, "level": 2},
    {"kind": "b", "value": 1, "level": 10},
    {"kind": "b", "value": 1, "ints": [3, 4]},
    {"kind": "b", "value": 1, "ints": [3, 3]},
    {"kind": "b", "value": 1, "words": ["ab", "cd"]},
    {"kind": "b", "value": 1, "words": ["ab", "ab"]},
)


def test_schema_validator_enum_members(output_file: Path) -> None:
    """Judge a model built with enum members as the same JSON document: conditions, bounds, required, contains."""
    run_main_and_assert(
        input_path=ENUM_MEMBERS_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--generate-schema-validators",
        ],
    )
    validator = Draft202012Validator(json.loads(ENUM_MEMBERS_PATH.read_text(encoding="utf-8")))
    with _generated_model(output_file, ENUM_MEMBERS_PATH.stem, "Probe") as model:
        assert_output(
            "".join(
                f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
                f"{_accepts(model.model_validate, _with_members(model, document))}/"
                f"{_accepts(model.model_validate_json, json.dumps(document))}\n"
                for document in ENUM_MEMBER_DOCUMENTS
            ),
        )


def _enum_class(annotation: object) -> type[enum.Enum] | None:
    """Return the enum class an annotation names, also inside its arguments."""
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return annotation
    return next((found for argument in typing.get_args(annotation) if (found := _enum_class(argument))), None)


def _with_members(model: type[pydantic.BaseModel], document: dict[str, object]) -> dict[str, object]:
    """Return the document with each value of an enum field, and each item of a list of them, as the enum's member."""
    members: dict[str, object] = {}
    for name, value in document.items():
        enum_class = _enum_class(model.model_fields[name].annotation)
        members[name] = (
            [enum_class(item) for item in value]
            if isinstance(value, list) and enum_class
            else enum_class(value)
            if enum_class
            else value
        )
    return members
