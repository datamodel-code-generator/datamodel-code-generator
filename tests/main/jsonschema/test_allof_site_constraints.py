"""Write the keywords an allOf site adds to its one ``$ref`` where the field's type is no class."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

CASES = {
    "pydantic_v2-default": ("pydantic_v2.BaseModel",),
    "pydantic_v2-collapse": ("pydantic_v2.BaseModel", "--collapse-root-models"),
    "pydantic_v2-collapse_field_constraints": (
        "pydantic_v2.BaseModel",
        "--collapse-root-models",
        "--field-constraints",
    ),
    "pydantic_v2-collapse_annotated": ("pydantic_v2.BaseModel", "--collapse-root-models", "--use-annotated"),
    "pydantic_v2-type_alias": ("pydantic_v2.BaseModel", "--use-type-alias"),
    "pydantic_v2_dataclass-default": ("pydantic_v2.dataclass",),
    "pydantic_v2_dataclass-strip_default_none": ("pydantic_v2.dataclass", "--strip-default-none"),
    "msgspec-default": ("msgspec.Struct",),
    "msgspec-collapse": ("msgspec.Struct", "--collapse-root-models"),
    "typed_dict-collapse": ("typing.TypedDict", "--collapse-root-models"),
    "msgspec-reuse": ("msgspec.Struct", "--reuse-model"),
}


@pytest.mark.parametrize("case", CASES)
def test_allof_site_constraints(output_file: Path, case: str) -> None:
    """Check the keywords an allOf site adds to its one ``$ref`` wherever the field writes no class (#6024, #6234).

    A site typed by its first ``$ref`` alone kept only that target: ``name`` lost ``maxLength``, ``labels`` its
    ``minProperties``, ``counts`` the ``minItems`` of its second member, ``code`` the pattern of its second member,
    ``score`` the bound beside its ``$ref`` to an allOf of one ``$ref`` with its own bound, and ``short`` the
    ``maxLength`` of the definition it names. Where root-model collapse inlines the target or the field names a type
    alias (``--use-type-alias``, the aliases of Pydantic dataclasses and msgspec), the field writes them, so every
    invalid payload is rejected; where the field names a root model class, as Pydantic writes by default, it keeps
    its output, and a TypedDict writes no constraint. A Pydantic dataclass field that writes no ``Field()`` call takes
    them in ``Annotated``, so it keeps its place in the constructor, also where ``--strip-default-none`` leaves the
    optional fields without a default. A site whose ``$ref`` names a duplicate that ``--reuse-model``
    makes an alias of the type it reuses reads that type (``e``).
    """
    output_model_type, *options = CASES[case]
    name = "allof_site_constraints_reuse" if case == "msgspec-reuse" else "allof_site_constraints"
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / f"{name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"),
            *options,
        ],
    )
    if output_model_type == "typing.TypedDict":
        return
    payloads = json.loads((DATA_PATH / "payloads" / f"{name}.json").read_text())
    documents = [
        *((f"valid {json.dumps(payload)}", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(payload)}", payload) for payload in payloads["invalid"]),
    ]
    with _generated_model(output_file, "allof_site_constraints_model", "Model") as model:
        assert_output(
            "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents), name="payloads"
        )


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a document."""
    try:
        if isinstance(model, type) and issubclass(model, msgspec.Struct):
            msgspec.convert(document, model)
        else:
            pydantic.TypeAdapter(model).validate_python(document)
    except (msgspec.ValidationError, pydantic.ValidationError):
        return "rejected"
    return "accepted"
