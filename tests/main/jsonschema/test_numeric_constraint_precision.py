"""End-to-end coverage for integer precision in numeric constraints."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

from datamodel_code_generator import Formatter, InputFileType
from datamodel_code_generator.enums import StrictTypes
from datamodel_code_generator.model._constraints import Constraints
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _assert_model_json_invalid,
    _generated_model,
    _model_json_validator,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

BEYOND_FLOAT_RANGE = JSON_SCHEMA_DATA_PATH / "numeric_constraint_precision" / "beyond_float_range.json"
BEYOND_FLOAT_RANGE_PAYLOADS = JSON_DATA_PATH / "numeric_constraint_precision" / "beyond_float_range_payloads.json"
NON_FINITE_MULTIPLE_OF = JSON_SCHEMA_DATA_PATH / "numeric_constraint_precision" / "non_finite_multiple_of.json"


@pytest.mark.parametrize("entry_point", ["api", "cli"])
@pytest.mark.parametrize(
    ("fixture", "field_constraints", "strict", "expected_name"),
    [
        ("large", True, False, "large_field"),
        ("large", False, False, "large_constrained"),
        ("large", True, True, "large_strict"),
        ("ordinary", True, False, "ordinary_field"),
        ("ordinary", False, False, "ordinary_constrained"),
        ("draft4", True, False, "draft4_field"),
        ("draft4", False, False, "draft4_constrained"),
    ],
)
def test_numeric_constraint_precision(
    output_file: Path, entry_point: str, fixture: str, field_constraints: bool, strict: bool, expected_name: str
) -> None:
    """Retain large integer constraints while preserving ordinary floats and Draft 4 booleans."""
    input_path = JSON_SCHEMA_DATA_PATH / "numeric_constraint_precision" / f"{fixture}.json"
    if entry_point == "api":
        options = {
            "input_file_type": InputFileType.JsonSchema,
            "field_constraints": field_constraints,
            "strict_types": [StrictTypes.int] if strict else [],
            "disable_timestamp": True,
            "formatters": [Formatter.BUILTIN],
        }
        run_generate_and_assert(
            input_=json.loads(input_path.read_text(encoding="utf-8")),
            expected_file=EXPECTED_JSON_SCHEMA_PATH / "numeric_constraint_precision" / f"{expected_name}_api.py",
            assert_input_unchanged=True,
            **options,
        )
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            assert_func=assert_file_content,
            expected_file=f"numeric_constraint_precision/{expected_name}.py",
            **options,
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=f"numeric_constraint_precision/{expected_name}.py",
            extra_args=[
                "--disable-timestamp",
                "--formatters",
                "builtin",
                *(["--field-constraints"] if field_constraints else []),
                *(["--strict-types", "int"] if strict else []),
            ],
            force_exec_validation=True,
        )

    payloads = json.loads(
        (JSON_DATA_PATH / "numeric_constraint_precision" / f"{fixture}_payloads.json").read_text(encoding="utf-8")
    )
    valid_payload = {name: payload["valid"] for name, payload in payloads.items()}
    native = TypeAdapter(dict[str, Any])
    try:
        native.validate_json(json.dumps(valid_payload))
    except ValidationError:
        _assert_model_json_invalid(native.validate_json, json.dumps(valid_payload), "json_invalid")
        with _generated_model(output_file, "numeric_constraint_precision", "NumericConstraints") as model:
            _assert_model_json_invalid(model.model_validate_json, json.dumps(valid_payload), "json_invalid")
            assert_output(
                json.dumps(model.model_validate(valid_payload).model_dump(), indent=2) + "\n",
                EXPECTED_JSON_SCHEMA_PATH / "numeric_constraint_precision/large_native_python.txt",
            )
            for name, payload in payloads.items():
                _assert_model_json_invalid(
                    model.model_validate, {**valid_payload, name: payload["invalid"]}, payload["error_type"]
                )
        return
    for name, payload in payloads.items():
        assert_generated_model_json_validation(
            output_file,
            module_name="numeric_constraint_precision",
            model_name="NumericConstraints",
            valid_json=json.dumps(valid_payload),
            invalid_json=json.dumps({**valid_payload, name: payload["invalid"]}),
            expected_error_type=payload["error_type"],
            expected_attribute_path=payload.get("attribute_path", [name]),
            expected_attribute_value=payload["valid"],
        )


@pytest.mark.parametrize("fixture", ["large", "ordinary"])
@pytest.mark.parametrize("serialization", ["python", "json"])
@pytest.mark.parametrize("constraint_model", ["schema", "constraints"])
def test_numeric_constraint_serialization(fixture: str, serialization: str, constraint_model: str) -> None:
    """Preserve numeric constraints through schema model Python and JSON serialization."""
    input_path = JSON_SCHEMA_DATA_PATH / "numeric_constraint_precision" / f"{fixture}.json"
    schema = json.loads(input_path.read_text(encoding="utf-8"))
    for name, value in schema["properties"].items():
        if constraint_model == "schema":
            parsed = JsonSchemaObject.model_validate(value)
        elif "multipleOf" in value:
            parsed = Constraints.model_validate({"multipleOf": value["multipleOf"]})
        else:
            continue
        serialized = parsed.model_dump(exclude_unset=True, by_alias=True)
        if serialization == "json":
            try:
                TypeAdapter(dict[str, Any]).dump_json(value)
            except PydanticSerializationError as native_error:
                with pytest.raises(PydanticSerializationError) as generated_error:
                    parsed.model_dump_json(exclude_unset=True, by_alias=True)
                assert_output(
                    f"native: {native_error}\ngenerated: {generated_error.value}\n",
                    EXPECTED_JSON_SCHEMA_PATH / "numeric_constraint_precision/native_json_overflow.txt",
                )
            else:
                serialized = json.loads(parsed.model_dump_json(exclude_unset=True, by_alias=True))
        schema["properties"][name] = serialized if constraint_model == "schema" else {**value, **serialized}
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "numeric_constraint_precision" / f"{fixture}_field_api.py",
        assert_input_unchanged=True,
        input_file_type=InputFileType.JsonSchema,
        field_constraints=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param((), id="constrained"),
        pytest.param(("--field-constraints",), id="field"),
        pytest.param(("--use-annotated",), id="annotated"),
        pytest.param(("--output-model-type", "pydantic_v2.dataclass", "--strict-types", "float"), id="dataclass"),
    ],
)
def test_number_bounds_beyond_float_range(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Write a number bound beyond the float range as the infinity of its sign, as the same number written 1e400."""
    run_main_and_assert(
        input_path=BEYOND_FLOAT_RANGE,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--formatters", "builtin", *extra_args],
    )
    payloads = json.loads(BEYOND_FLOAT_RANGE_PAYLOADS.read_text(encoding="utf-8"))
    documents = [
        *payloads["valid"],
        *({**payloads["valid"][0], **change} for change in payloads["invalid"]),
    ]
    validator = Draft202012Validator(json.loads(BEYOND_FLOAT_RANGE.read_text(encoding="utf-8")))
    with _generated_model(output_file, "number_bounds_beyond_float_range", "Model") as model:
        validate_json = _model_json_validator(model)
        verdicts = "".join(
            f"{json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_model_verdict(validate_json, document)}\n"
            for document in documents
        )
    assert_output(verdicts, name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param((), id="constrained"),
        pytest.param(("--field-constraints",), id="field"),
        pytest.param(("--use-annotated",), id="annotated"),
        pytest.param(("--use-decimal-for-multiple-of",), id="decimal"),
        pytest.param(("--output-model-type", "pydantic_v2.dataclass"), id="dataclass"),
    ],
)
def test_non_finite_multiple_of_left_out_accepts_non_multiples(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: tuple[str, ...]
) -> None:
    """Leave out a multipleOf Pydantic would take as an infinity, which it refuses, and warn, so 1.5 is accepted.

    A multipleOf of 1e400, or of 10**400 on a float, has no finite value in the written type; the module imports
    without it and accepts numbers that are no multiples, such as 1.5, which the schema rejects. A ``Decimal`` from
    ``--use-decimal-for-multiple-of`` keeps 10**400 and rejects 1.5.
    """
    run_main_and_assert(
        input_path=NON_FINITE_MULTIPLE_OF,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--formatters", "builtin", *extra_args],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
        ),
        name="warnings",
    )
    with _generated_model(output_file, "non_finite_multiple_of", "Model") as model:
        validate_json = _model_json_validator(model)
        verdicts = "".join(
            f"{json.dumps(document)}: model {_model_verdict(validate_json, document)}\n"
            for name in ("infinite", "beyond", "bounded", "count", "amount", "either")
            for document in ({name: 0}, {name: 3 if name == "count" else 1.5})
        )
    assert_output(verdicts, name="payloads")


def _model_verdict(validate_json: Callable[[str], Any], document: object) -> str:
    """Return whether a generated model accepts or rejects a JSON document."""
    try:
        validate_json(json.dumps(document))
    except ValidationError:
        return "rejects"
    return "accepts"
