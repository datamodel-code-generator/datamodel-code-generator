"""Write the constrained members of unions in ``Annotated`` forms under --use-annotated (#7109)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

UNION_MEMBERS = JSON_SCHEMA_DATA_PATH / "annotated_union_members"
KINDS_PAYLOADS = json.loads((DATA_PATH / "payloads/annotated_union_members/kinds.json").read_text())


def test_annotated_scalar_type_list_member(output_file: Path) -> None:
    """A pattern on ``[integer, string]`` stays on the string member, written ``Annotated`` for type checkers."""
    run_main_and_assert(
        input_path=UNION_MEMBERS / "scalar_type_list.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--use-union-operator",
            "--target-python-version",
            "3.13",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="annotated_union_members_scalar_type_list",
        model_name="Example",
        valid_json='{"code": "200"}',
        invalid_json='{"code": "invalid"}',
        expected_error_type="int_parsing",
        expected_attribute_path=("code",),
        expected_attribute_value="200",
    )


def _verdict(model: object, document: object) -> str:
    """Return whether the model accepts a JSON document, with the first error type where it rejects it."""
    try:
        TypeAdapter(model).validate_json(json.dumps(document))
    except ValidationError as error:
        return f"rejected ({error.errors()[0]['type']})"
    return "accepted"


@pytest.mark.parametrize(
    ("output_model_type", "options"),
    [
        pytest.param("pydantic_v2.BaseModel", ("--use-annotated",), id="base-model"),
        pytest.param("pydantic_v2.dataclass", ("--use-annotated",), id="dataclass"),
        pytest.param("pydantic_v2.BaseModel", ("--use-annotated", "--use-type-alias"), id="type-alias"),
        pytest.param("pydantic_v2.BaseModel", ("--field-constraints",), id="field-constraints"),
    ],
)
def test_annotated_union_member_kinds(output_file: Path, output_model_type: str, options: tuple[str, ...]) -> None:
    """Each constrained kind keeps its constraints on its member, in a field, a list item, a map value and a root.

    Under --use-annotated every member is ``Annotated``, also where the root model's config selects the regex
    engine and in a type alias body; --field-constraints alone keeps the ``con*`` calls. All accept and reject the
    same payloads.
    """
    input_path = UNION_MEMBERS / "kinds.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            output_model_type,
            *options,
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
    )
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    valid = KINDS_PAYLOADS["valid"]
    validator.validate(valid)
    for change in KINDS_PAYLOADS["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**valid, **change})
    module_name = (
        "_".join(("annotated_union_member_kinds", output_model_type, *options)).replace(".", "_").replace("-", "_")
    )
    with _generated_model(output_file, module_name, "Model") as model:
        verdicts = [f"valid: {_verdict(model, valid)}"]
        verdicts.extend(
            f"invalid {json.dumps(change)}: {_verdict(model, {**valid, **change})}"
            for change in KINDS_PAYLOADS["invalid"]
        )
    assert_output("".join(f"{verdict}\n" for verdict in verdicts))


def test_annotated_union_member_bytes(tmp_path: Path, output_file: Path) -> None:
    """The ``binary`` string of a type list keeps its lengths in ``Annotated``; its integer warns of the format."""
    input_path = tmp_path / "bytes.json"
    input_path.write_text(
        json.dumps({
            "title": "Model",
            "type": "object",
            "properties": {"raw": {"type": ["string", "integer"], "format": "binary", "maxLength": 8}},
        })
    )
    with pytest.warns(UserWarning, match="format of 'binary' not understood for 'integer'"):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--use-annotated", "--disable-timestamp", "--formatters", "builtin"],
        )
    assert_generated_model_json_validation(
        output_file,
        module_name="annotated_union_member_bytes",
        model_name="Model",
        valid_json='{"raw": "abc"}',
        invalid_json='{"raw": "abcdefghi"}',
        expected_error_type="bytes_too_long",
        expected_attribute_path=("raw",),
        expected_attribute_value=b"abc",
    )
