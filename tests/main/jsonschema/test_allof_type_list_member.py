"""Generate a root model from an allOf whose member has a list of types, instead of stopping the generation."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, get_type_hints

import msgspec
import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "allof_type_list_member.json"
DOCUMENTS_PATH = DATA_PATH / "payloads" / "allof_type_list_member" / "documents.json"
PYDANTIC_OUTPUT_TYPES = ("pydantic_v2.BaseModel", "pydantic_v2.dataclass")


def _verdict(output_type: str, model: Any, document: dict[str, Any]) -> str:
    """Return whether a generated model accepts a JSON document."""
    try:
        if output_type in PYDANTIC_OUTPUT_TYPES:
            TypeAdapter(model).validate_python(document)
        else:
            msgspec.convert(document, model)
    except (PydanticValidationError, msgspec.ValidationError):
        return "rejects"
    return "accepts"


@pytest.mark.parametrize(
    "output_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_allof_type_list_member(output_file: Path, output_type: str) -> None:
    """Merge a ``$ref`` root with an allOf member whose type is a list on the types both of them admit.

    A member such as ``{"type": ["string", "null"]}`` beside a ``$ref`` to a string raised ``unhashable type: 'list'``.
    The root now admits the types its ``$ref`` and every member share, so null is excluded by the string and the
    ``$ref`` to a nullable string stays nullable only where each member admits null. The models accept the documents of
    the schema and reject the others.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type],
    )
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    lines: list[str] = []
    with _generated_model(output_file, "allof_type_list_member_model", "Model") as model:
        get_type_hints(model, localns=vars(sys.modules[model.__module__]))
        lines.extend(
            f"{json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_verdict(output_type, model, document)}\n"
            for document in json.loads(DOCUMENTS_PATH.read_text())
        )
    assert_output("".join(lines), name="payloads")
