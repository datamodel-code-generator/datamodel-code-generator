"""Check the numeric bounds that an if/then/else puts on properties under --generate-schema-validators (#7054)."""

from __future__ import annotations

import json
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
    ],
    ids=["ref_siblings", "read_only"],
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
