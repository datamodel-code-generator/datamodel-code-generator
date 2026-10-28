"""Public generation coverage for a transient reference resolver failure."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import InputFileType
from datamodel_code_generator.reference import ModelResolver
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_validation,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@pytest.mark.parametrize("entry_point", ["api", "cli"])
def test_reference_cycle_preserves_raw_ref_on_resolution_failure(
    output_file: Path, monkeypatch: pytest.MonkeyPatch, entry_point: str
) -> None:
    """Keep recursive model generation valid when one inner reference cannot be normalized."""
    original_resolve_ref = ModelResolver.resolve_ref
    failed_refs: list[str] = []

    def fail_once(resolver: ModelResolver, path: Sequence[str] | str) -> str:
        if isinstance(path, str) and path == "#/$defs/Second" and not failed_refs:
            failed_refs.append(path)
            msg = "Transient reference normalization failure"
            raise ValueError(msg)
        return original_resolve_ref(resolver, path)

    monkeypatch.setattr(ModelResolver, "resolve_ref", fail_once)
    input_path = JSON_SCHEMA_DATA_PATH / "schema_reference_cycles" / "schema_names.json"
    if entry_point == "api":
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file="schema_reference_cycles/schema_names.py",
            field_constraints=True,
            strict_refs=True,
            disable_timestamp=True,
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="schema_reference_cycles/schema_names.py",
            extra_args=["--field-constraints", "--strict-refs", "--disable-timestamp"],
            force_exec_validation=True,
        )

    assert_output(
        "\n".join(failed_refs) + "\n",
        EXPECTED_JSON_SCHEMA_PATH / "schema_reference_cycles" / "resolver_failure.txt",
    )
    payloads = json.loads((DATA_PATH / "payloads" / "reference_resolution_fallback.json").read_text(encoding="utf-8"))
    assert_generated_model_json_validation(
        output_file,
        module_name="reference_resolution_fallback",
        model_name="SchemaNames",
        valid_json=json.dumps(payloads["valid"]),
        invalid_json=json.dumps(payloads["invalid"]),
        expected_error_type="model_type",
        expected_attribute_path=("value", "default"),
        expected_attribute_value=None,
    )
