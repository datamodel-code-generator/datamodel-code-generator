"""allOf members that name a component that is only a ``$ref`` to itself, such as ``Loop: {$ref: Loop}``.

Up to 0.86.0 every backend wrote ``from . import Loop`` and ``class Plain(Loop)`` for such a member, and the module
failed at import: deduplication drops the component, whose root names the component itself, and redirected its users
to that same component. The models that inherit it now drop it from their bases, as they drop every other root model
and the components of a ``$ref`` loop of two, and keep their other bases.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, _model_json_validator, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = OPEN_API_DATA_PATH / "allof_self_reference_base.yaml"
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
PAYLOADS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    ("Plain", ({"x": "a"}, {}, {"x": 1})),
    ("Mixed", ({"id": 1, "y": "b"}, {"y": "b"}, {"id": 1, "y": 2})),
    (
        "Holder",
        ({"inner": {"z": 1}, "name": "n"}, {"inner": {}}, {"inner": {"z": "one"}}, {"inner": {"z": 1}, "name": 1}),
    ),
)
"""Documents per model, the first valid against its schema and the others invalid."""


def _verdicts(output_file: Path, output_model_type: str) -> str:
    """Return whether the schema and each generated model accept each document, one line per document.

    JSON Schema leaves the result of a ``$ref`` loop undefined, so the schema reads ``Loop`` as ``{}``, as the output
    reads the components of a loop of two.
    """
    schemas = yaml.safe_load(INPUT_PATH.read_text(encoding="utf-8"))["components"]["schemas"]
    lines: list[str] = []
    for name, documents in PAYLOADS:
        validator = Draft202012Validator({
            "$ref": f"#/components/schemas/{name}",
            "components": {"schemas": {**schemas, "Loop": {}}},
        })
        with _generated_model(output_file, f"allof_self_reference_base_{name}", name) as model:
            decode = (
                (lambda text, model=model: msgspec.json.decode(text, type=model))
                if output_model_type == "msgspec.Struct"
                else _model_json_validator(model)
            )
            for document in documents:
                text = json.dumps(document)
                try:
                    decode(text)
                except (ValidationError, msgspec.ValidationError):
                    verdict = "rejects"
                else:
                    verdict = "accepts"
                schema = "accepts" if validator.is_valid(document) else "rejects"
                lines.append(f"{name} {text}: schema {schema}, model {verdict}\n")
    return "".join(lines)


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_allof_self_reference_base(output_model_type: str, output_file: Path) -> None:
    """Drop the component that is only a ``$ref`` to itself from the bases of the models whose allOf names it.

    ``Plain`` keeps the default base, ``Mixed`` keeps ``Base``, and the inline model of ``Holder.inner`` gets no base
    either; the module imports and validates what the other members of each allOf declare.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin"],
    )
    assert_output(_verdicts(output_file, output_model_type), name="payloads")
