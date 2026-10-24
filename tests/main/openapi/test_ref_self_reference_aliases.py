"""Type aliases that name a component that is only a $ref to itself.

Such a component has no type, so no class of it is written. A type alias, whose value the module evaluates when it is
imported, writes the component's name as a string, the form of a name the module declares later, and so does every
annotation that is evaluated at import. Output that names the component only in deferred annotations keeps its 0.86.0
bytes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

ALIASES = OPEN_API_DATA_PATH / "ref_self_reference_aliases.yaml"
HOLDER_SCHEMA = Draft202012Validator({**yaml.safe_load(ALIASES.read_text()), "$ref": "#/components/schemas/Holder"})
VALID = {"count": 1, "label": "a"}
INVALID = {"count": "x"}


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--output-model-type", "dataclasses.dataclass"],
        ["--output-model-type", "typing.TypedDict"],
        ["--output-model-type", "pydantic_v2.dataclass"],
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"],
    ],
    ids=["dataclass", "typed-dict", "pydantic-dataclass", "base-model-type-alias"],
)
def test_ref_self_reference_aliases_no_class_uses(output_file: Path, extra_args: list[str]) -> None:
    """Quote the component in alias, union and list aliases, so the module imports and its class validates."""
    run_main_and_assert(
        input_path=ALIASES,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=extra_args,
        force_exec_validation=True,
    )
    HOLDER_SCHEMA.validate(VALID)
    with pytest.raises(SchemaValidationError):
        HOLDER_SCHEMA.validate(INVALID)
    assert_generated_model_json_validation(
        output_file,
        module_name=f"ref_self_reference_aliases_{extra_args[1].replace('.', '_')}",
        model_name="Holder",
        valid_json=json.dumps(VALID),
        invalid_json=json.dumps(INVALID),
        expected_error_type="int_parsing",
        expected_attribute_path=("count",),
        expected_attribute_value=1,
    )


def test_ref_self_reference_aliases_no_class_uses_msgspec(output_file: Path) -> None:
    """Quote the component in the msgspec aliases; the Struct decodes and rejects the payloads as the schema does."""
    run_main_and_assert(
        input_path=ALIASES,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "msgspec.Struct"],
        force_exec_validation=True,
    )
    HOLDER_SCHEMA.validate(VALID)
    with pytest.raises(SchemaValidationError):
        HOLDER_SCHEMA.validate(INVALID)
    with _generated_model(output_file, "ref_self_reference_aliases_msgspec", "Holder") as model:
        parsed = msgspec.json.decode(json.dumps(VALID), type=model)
        with pytest.raises(msgspec.ValidationError, match=r"Expected `int`, got `str` - at `\$\.count`"):
            msgspec.json.decode(json.dumps(INVALID), type=model)
    assert_output(f"{parsed!r}\n", name="parsed")


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "typing.TypedDict"])
def test_ref_self_reference_alias_used_by_class(output_file: Path, output_model_type: str) -> None:
    """Quote the component in the alias that a list alias of the root class names, so the module imports."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_self_reference_through_alias.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "typing.TypedDict"])
def test_ref_self_reference_without_future_imports(output_file: Path, output_model_type: str) -> None:
    """Quote the component in a class annotation that the class evaluates without ``from __future__`` imports."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_self_reference.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--disable-future-imports"],
        force_exec_validation=True,
    )
