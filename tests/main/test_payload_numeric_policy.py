"""Check exact source truth and one independent native float lowering contract."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.conftest import assert_inputs_not_mutated, assert_output

from .payload_validation import (
    SCHEMA_CASES,
    GeneratedModelCache,
    SchemaCase,
    load_generated_payload_adapter,
    payload_strategy,
    source_schema_validator,
    validate_with_source_schema,
)
from .payload_validation.native_numeric import (
    NATIVE_FLOAT_CONTRACT,
    native_float_multiple_errors,
    pydantic_payload_result,
)

INPUT_PATH = Path(__file__).parents[1] / "data" / "payloads" / "numeric_policy"
EXPECTED_PATH = Path(__file__).parents[1] / "data" / "expected" / "main" / "payloads" / "numeric_policy"
WITNESSES = json.loads((INPUT_PATH / "witnesses.json").read_text())
ORACLE_CASES = json.loads((INPUT_PATH / "source_oracle.json").read_text())
REGISTERED_CASE = next(case for case in SCHEMA_CASES if case.id == NATIVE_FLOAT_CONTRACT["case_id"])


@pytest.mark.parametrize("name", ORACLE_CASES)
def test_canonical_json_source_oracle(name: str) -> None:
    """Preserve exact decimal source truth without modifying runtime inputs."""
    record = ORACLE_CASES[name]
    case = SchemaCase(
        id=f"numeric_policy/{name}",
        input_file_type="jsonschema",
        source_path=INPUT_PATH / "source_oracle.json",
        source_schema=record["schema"],
        codegen_schema=record["schema"],
        temp_input_suffix=".json",
    )
    with assert_inputs_not_mutated(record):
        valid = source_schema_validator(case).is_valid(record["payload"])
        if valid:
            validate_with_source_schema(case, record["payload"])
        assert_output(json.dumps(valid), EXPECTED_PATH / f"oracle_{name}.txt")


@pytest.mark.parametrize(
    "variant", ["original", "weaker", "stronger", "unconstrained", "wrong_type", "wrong_context", "wrong_path"]
)
def test_native_float_contract_generation(variant: str, tmp_path: Path) -> None:
    """Real generated mutants must disagree with the independent native contract."""
    case = REGISTERED_CASE
    if variant != "original":
        case = replace(case, codegen_schema=json.loads((INPUT_PATH / f"{variant}.json").read_text()))
    cache = GeneratedModelCache({"base": tmp_path, "adapters": {}})
    adapter = load_generated_payload_adapter(case, cache)
    records = {}
    with assert_inputs_not_mutated({
        "source": case.source_schema,
        "codegen": case.codegen_schema,
        "payloads": WITNESSES,
    }):
        for name, payload in WITNESSES.items():
            source_valid = source_schema_validator(case).is_valid(payload)
            expected_errors = native_float_multiple_errors(case, payload)
            validated, errors = pydantic_payload_result(adapter, payload)
            if not errors:
                dumped = adapter.dump_python(validated, mode="json", by_alias=True, exclude_unset=True)
                assert_output(
                    json.dumps(source_schema_validator(case).is_valid(dumped) == source_valid),
                    EXPECTED_PATH / f"dump_{variant}_{name}.txt",
                )
            records[name] = {"source_valid": source_valid, "matches_native": errors == expected_errors}
        assert_output(json.dumps(records, indent=2), EXPECTED_PATH / f"generation_{variant}.txt")


@pytest.mark.parametrize("change", ["source", "input"])
def test_native_float_contract_rejects_unproven_assumptions(change: str) -> None:
    """Changed source constraints and unrelated native errors cannot be classified."""
    case = REGISTERED_CASE
    payload: dict[str, Any] = {"decimal_multiple_field": "invalid"}
    if change == "source":
        case = replace(case, source_schema={"properties": {}})
    with pytest.raises(ValueError, match="Native float contract"):
        native_float_multiple_errors(case, payload)


def test_unregistered_numeric_case_uses_source_acceptance() -> None:
    """An unregistered shape cannot silently acquire native rejection handling."""
    case = replace(REGISTERED_CASE, id="unregistered")
    assert_output(
        json.dumps(native_float_multiple_errors(case, WITNESSES["ordinary"])), EXPECTED_PATH / "unregistered.txt"
    )


@pytest.mark.parametrize("name", ["constant", "enum", "ordinary_constant", "string_constant", "mixed_enum"])
@settings(
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_integral_float_constants_reach_generated_models(name: str, tmp_path: Path, data: st.DataObject) -> None:
    """Sample exact canonical const/enum values and preserve generated-model dumps."""
    schemas = json.loads((INPUT_PATH / "constant_sampling.json").read_text())
    schema = schemas[name]
    case = SchemaCase(
        id=f"numeric_policy/sampling/{name}",
        input_file_type="jsonschema",
        source_path=INPUT_PATH / "constant_sampling.json",
        source_schema=schema,
        codegen_schema=schema,
        temp_input_suffix=".json",
    )
    cache = GeneratedModelCache({"base": tmp_path, "adapters": {}})
    with assert_inputs_not_mutated({"source": schema, "codegen": case.codegen_schema}):
        payload = data.draw(payload_strategy(case))
        validate_with_source_schema(case, payload)
        adapter = load_generated_payload_adapter(case, cache)
        validated = adapter.validate_python(payload)
        dumped = adapter.dump_python(validated, mode="json", by_alias=True, exclude_unset=True)
        validate_with_source_schema(case, dumped)
        assert_output(
            json.dumps(type(payload).__name__ in schemas["runtime_types"][name]), EXPECTED_PATH / "runtime_numbers.txt"
        )
