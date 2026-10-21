"""A model extracted from one branch of a field's union keeps the field's default off its root.

Only with --keep-field-defaults-off-branch-roots: without it the root takes the field's default, as in 0.86.0.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = OPEN_API_DATA_PATH / "extracted_branch_default.yaml"
PAYLOADS = DATA_PATH / "payloads" / "extracted_branch_default" / "model.json"
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
KEEP_DEFAULTS_OFF = "--keep-field-defaults-off-branch-roots"
OPTION_SETS = {
    "default": (),
    "field_constraints": ("--field-constraints", KEEP_DEFAULTS_OFF),
    "annotated": ("--use-annotated", KEEP_DEFAULTS_OFF),
    "strict_nullable": ("--strict-nullable", "--field-constraints", KEEP_DEFAULTS_OFF),
}
OPTION_SETS_WITHOUT_OPTION = {
    name: tuple(option for option in options if option != KEEP_DEFAULTS_OFF)
    for name, options in OPTION_SETS.items()
    if name != "default"
}
"""The option sets whose extracted branch roots take the field's default without the option, as in 0.86.0."""


def _verdict(model: Any, output_model_type: str, document: object) -> str:
    """Return whether a generated model accepts a JSON document, with the first error type where Pydantic rejects it."""
    text = json.dumps(document)
    try:
        if output_model_type == "msgspec.Struct":
            msgspec.json.decode(text, type=model)
        else:
            TypeAdapter(model).validate_json(text)
    except ValidationError as error:
        return f"rejected ({error.errors()[0]['type']})"
    except msgspec.ValidationError:
        return "rejected"
    return "accepted"


def _root_model_verdict(output_file: Path) -> str:
    """Return whether the extracted ``Weight`` model builds without its value, with the error type where it does not."""
    with _generated_model(output_file, "extracted_branch_default_weight", "Weight") as weight:
        try:
            weight()
        except ValidationError as error:
            return f"rejected ({error.errors()[0]['type']})"
    return "accepted"


@pytest.mark.cli_doc(
    options=["--keep-field-defaults-off-branch-roots"],
    option_description="""Leave a property's default off the root models extracted from its `anyOf` or `oneOf` branches.

Under `--field-constraints` or `--use-annotated` a branch with constraints, such as `{type: number, minimum: 0}` in
the `anyOf` of a property with `default: null`, becomes a root model of its own. Without the option that root takes
the property's default, as in 0.86.0: `root: float = Field(None, ge=0.0)`, so the model builds without a value and
holds a default its type rejects. With `--keep-field-defaults-off-branch-roots` the default stays with the property,
and the root requires its value: `root: float = Field(..., ge=0.0)`.""",
    input_schema="openapi/extracted_branch_default.yaml",
    cli_args=["--field-constraints", "--keep-field-defaults-off-branch-roots"],
    golden_output=(
        "main/openapi/extracted_branch_default/extracted_branch_default[field_constraints-pydantic_v2.BaseModel].py"
    ),
    comparison_output=(
        "main/openapi/extracted_branch_default/without_option[field_constraints-pydantic_v2.BaseModel].py"
    ),
)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_extracted_branch_default(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Leave the default with the field that declares it and keep the extracted branch's value required."""
    _record_extracted_branch_default(
        output_file, output_model_type, OPTION_SETS[option_set], root_model=option_set != "default"
    )


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
@pytest.mark.parametrize("option_set", OPTION_SETS_WITHOUT_OPTION)
def test_extracted_branch_default_without_option(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Give the extracted branch's root the field's default without --keep-field-defaults-off-branch-roots.

    The root of ``Weight`` is then ``Field(None, ge=0.0)``, so ``Weight()`` builds without a value, as in 0.86.0.
    """
    _record_extracted_branch_default(
        output_file, output_model_type, OPTION_SETS_WITHOUT_OPTION[option_set], root_model=True
    )


def _record_extracted_branch_default(
    output_file: Path, output_model_type: str, options: tuple[str, ...], *, root_model: bool
) -> None:
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *options],
    )
    payloads = json.loads(PAYLOADS.read_text())
    validator = Draft202012Validator(yaml.safe_load(INPUT.read_text())["components"]["schemas"]["Model"])
    validator.validate(payloads["valid"])
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"], **change})
    with _generated_model(output_file, "extracted_branch_default_model", "Model") as model:
        verdicts = [
            f"empty: {_verdict(model, output_model_type, {})}",
            f"valid: {_verdict(model, output_model_type, payloads['valid'])}",
        ]
        verdicts.extend(
            f"invalid {', '.join(change)}: {_verdict(model, output_model_type, {**payloads['valid'], **change})}"
            for change in payloads["invalid"]
        )
    if output_model_type == "pydantic_v2.BaseModel" and root_model:
        verdicts.append(f"Weight(): {_root_model_verdict(output_file)}")
    assert_output("".join(f"{verdict}\n" for verdict in verdicts), name="payloads")
