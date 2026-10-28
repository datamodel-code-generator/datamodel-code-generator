"""Write no None beside the name of a type alias that is None, which ``|`` cannot join."""

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

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "null_only_ref_nullable.json"
DOCUMENTS_PATH = DATA_PATH / "payloads" / "null_only_ref_nullable" / "documents.json"
PYDANTIC_OUTPUT_TYPES = ("pydantic_v2.BaseModel", "pydantic_v2.dataclass")
OPTION_SETS = {
    "default": (),
    "type_alias": ("--use-type-alias",),
    "typing_union": ("--no-use-union-operator",),
}


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


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "output_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_null_only_reference_alias(output_file: Path, output_type: str, option_set: str) -> None:
    """Evaluate the annotations of a model whose field names a component that only holds null.

    Such a component is a ``TypeAlias`` of ``None`` in dataclass, TypedDict and msgspec output, and a field that
    names it beside ``null`` wrote ``Nothing | None``, which raises ``TypeError`` when the annotation is evaluated.
    The field now writes the name alone, while the name of a ``TypeAlias`` that is not None and a Pydantic root model
    keep their None, and the models accept the documents of the schema and reject the others.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type, *OPTION_SETS[option_set]],
    )
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    lines: list[str] = []
    with _generated_model(output_file, "null_only_reference_alias_model", "Root") as model:
        get_type_hints(model, localns=vars(sys.modules[model.__module__]))
        lines.extend(
            f"{json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_verdict(output_type, model, document)}\n"
            for document in json.loads(DOCUMENTS_PATH.read_text())
        )
    assert_output("".join(lines), name="payloads")
