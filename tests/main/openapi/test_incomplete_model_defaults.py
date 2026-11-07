"""Write a property whose object default lacks required fields of its model without that default."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING, Any

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = OPEN_API_DATA_PATH / "incomplete_model_defaults.yaml"
PAYLOADS = DATA_PATH / "payloads" / "incomplete_model_defaults.json"
OPTION_SETS = {"released": (), "omit": ("--omit-incomplete-model-defaults",)}
DECODED_OUTPUTS = ("pydantic_v2.BaseModel", "pydantic_v2.dataclass", "msgspec.Struct")
"""The outputs that validate or convert an object default, and the outputs whose payloads the test decodes."""
WARNING = r"^The defaults? of .* lacks? required fields of"


@pytest.mark.cli_doc(
    options=["--omit-incomplete-model-defaults"],
    option_description="""Write a property whose object default lacks required fields of its model without that default.

A JSON Schema `default` is an annotation that is not validated, so a schema can give a property an object default that
its own model rejects, such as `default: {}` on a property whose model has required fields (the Kubernetes specs do
this throughout). The generated model validates that default (`Field({}, validate_default=True)`, msgspec `convert`),
so valid input that leaves the property out fails. With this option such a property is written without the default
(`spec: PodSpec | None = None`), as if the schema gave none, so that input is valid.""",
    input_schema="openapi/incomplete_model_defaults.yaml",
    cli_args=["--omit-incomplete-model-defaults"],
    golden_output="main/openapi/incomplete_model_defaults/incomplete_model_defaults[pydantic_v2-omit].py",
    comparison_output="main/openapi/incomplete_model_defaults/incomplete_model_defaults[pydantic_v2-released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "output_model_type",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "msgspec.Struct", "typing.TypedDict"],
    ids=["pydantic_v2", "pydantic_v2_dataclass", "dataclass", "msgspec", "typed_dict"],
)
def test_incomplete_model_defaults(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Leave out under the option an object default that lacks required fields of its model (#4828).

    Without it a property whose default lacks a required field of its model, its own or a base's, is written with
    that default, as in 0.90.0, and the outputs that validate it, Pydantic and msgspec, reject input that leaves the
    property out, which one warning names with each such property. With it such a property is written as one without a
    default; a complete default, a map default, a default of a root model and one of a union stay. Every valid payload
    decodes under the option and every invalid one is rejected under both option sets.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.filterwarnings("always", WARNING, UserWarning)
        run_main_and_assert(
            input_path=INPUT,
            output_path=output_file,
            input_file_type="openapi",
            extra_args=[
                *("--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"),
                *OPTION_SETS[option_set],
            ],
        )
    assert_output("".join(f"{warning.message}\n" for warning in caught), name="warnings")
    if output_model_type not in DECODED_OUTPUTS:
        return
    payloads = json.loads(PAYLOADS.read_text())
    documents = [
        *((f"valid {json.dumps(payload)}", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(payload)}", payload) for payload in payloads["invalid"]),
    ]
    with _generated_model(output_file, "incomplete_model_defaults_model", "PodTemplateSpec") as model:
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
