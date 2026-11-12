"""Execute required-group and conditional-presence rules from external fixtures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, Error, Formatter, InputFileType
from datamodel_code_generator.model.base import TEMPLATE_DIR
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path

_CASES = json.loads((DATA_PATH / "payloads/schema_validators_presence_cases.json").read_text())


@pytest.mark.parametrize("case_name", _CASES)
@pytest.mark.parametrize("template", ["compiled", "builtin_jinja", "custom_directory"])
def test_schema_validators_presence(output_file: Path, tmp_path: Path, case_name: str, template: str) -> None:
    """Compare generated code and execute presence rules through each template path."""
    case = _CASES[case_name]
    custom_dir = None
    if template == "builtin_jinja":
        custom_dir = TEMPLATE_DIR
    elif template == "custom_directory":
        custom_dir = tmp_path / "templates"
        custom_dir.mkdir()
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / f"{case_name}.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "schema_validators_presence" / f"{case_name}.py",
        output_model_type=DataModelType.PydanticV2BaseModel,
        generate_schema_validators=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        custom_template_dir=custom_dir,
        **case.get("options", {}),
    )
    for valid in case["valid"]:
        assert_generated_model_json_validation(
            output_file,
            module_name=case_name,
            model_name=case["model"],
            valid_json=json.dumps(valid),
            invalid_json=json.dumps(case["invalid"][0]),
            expected_error_type=case["error"],
        )
    for invalid in case["invalid"]:
        assert_generated_model_json_invalid(
            output_file,
            module_name=case_name,
            model_name=case["model"],
            invalid_json=json.dumps(invalid),
            expected_error_type=case["error"],
        )


@pytest.mark.parametrize("case_name", ["schema_validators_presence_else", "schema_validators_not_required"])
def test_schema_validators_presence_custom_helper(output_file: Path, case_name: str) -> None:
    """Reject incompatible custom helpers before producing incorrect runtime rules."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / f"{case_name}.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        expected_error=Error,
        expected_error_match="Custom schema runtime validation helper overrides",
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "schema_validators_presence/custom_helper_error.txt",
        output_model_type=DataModelType.PydanticV2BaseModel,
        generate_schema_validators=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        custom_template_dir=DATA_PATH / "templates/schema_validators_presence",
    )


_CONDITIONAL_OUTCOME_CASES = (
    (
        '{"generate": {"version": "v2", "plugins": [{"local": "cpp"}]}}',
        '{"generate": {"version": "v2", "plugins": [{"plugin": "cpp"}]}}',
        "extra_forbidden",
        (),
        None,
    ),
    (
        '{"manifest": {"manifest_version": 2, "web_accessible_resources": ["style.css"]}}',
        '{"manifest": {"manifest_version": 3, "web_accessible_resources": ["style.css"]}}',
        "model_type",
        ("manifest", "manifest_version"),
        2,
    ),
    (
        '{"event": {"kind": "note", "metric": "seven", "code": "x1"}}',
        '{"event": {"kind": "metric", "metric": 1, "code": "xb"}}',
        "string_pattern_mismatch",
        ("event", "metric"),
        "seven",
    ),
    ('{"upload": {"mode": "url", "url": "u"}}', '{"upload": {"mode": "url", "path": "a"}}', "value_error", (), None),
    (
        '{"strict_event": {"kind": "metric", "metric": 1}}',
        '{"strict_event": {"kind": "note", "metric": 1}}',
        "value_error",
        ("strict_event", "metric"),
        1,
    ),
    ('{"retired": {"version": 1}}', '{"retired": {"version": 2, "x": 1}}', "value_error", (), None),
    (
        '{"unconditional": {"name": "a", "alias": 5}}',
        '{"unconditional": {"name": 5}}',
        "string_type",
        ("unconditional", "alias"),
        5,
    ),
    (
        '{"person": {"role": "user", "first-name": "a", "badge": "b", "note": 3}}',
        '{"person": {"role": "admin", "first-name": "a"}}',
        "string_too_short",
        ("person", "note"),
        3,
    ),
    (
        '{"node": {"leaf": false, "child": {"leaf": true}}}',
        '{"node": {"leaf": false, "child": {"leaf": false}}}',
        "value_error",
        ("node", "child", "leaf"),
        True,
    ),
    (
        '{"closed_parent": {"kind": "small", "size": 3}}',
        '{"closed_parent": {"kind": "big", "size": 3}}',
        "greater_than_equal",
        ("closed_parent", "size"),
        3,
    ),
    (
        '{"job": {"kind": "http", "request": {"url": "u"}}}',
        '{"job": {"kind": "http", "request": {}}}',
        "missing",
        (),
        None,
    ),
    (
        '{"headers": {"Mode": "signed", "Signature": "s"}}',
        '{"headers": {"Mode": "signed"}}',
        "value_error",
        (),
        None,
    ),
    (
        '{"pattern_guard": {"code": "a", "detail": 5}}',
        '{"pattern_guard": {"code": 5}}',
        "string_type",
        ("pattern_guard", "detail"),
        5,
    ),
    (
        '{"empty_guard": {"code": "c", "detail": 3}}',
        '{"empty_guard": {"code": 5}}',
        "string_type",
        ("empty_guard", "detail"),
        3,
    ),
)


@pytest.mark.parametrize(
    "options", [[], ["--field-constraints"], ["--use-annotated"], ["--use-type-alias", "--reuse-model"]]
)
def test_conditional_properties_by_outcome(output_file: Path, options: list[str]) -> None:
    """Check the properties of the ``then`` or ``else`` that applies, typed by what each outcome admits (#6735).

    A property both branches declare takes the union of their schemas and the branch that applies checks its own, as
    ``plugins`` of buf and ``web_accessible_resources`` of a Chrome manifest; a key one outcome leaves open also admits
    any value, so a ``metric`` that only ``then`` constrains keeps a value ``else`` admits, and a key without ``if``
    any value; an ``if`` property that is not required passes where the key is absent. A closed branch admits only its
    keys, a ``false`` property and, under ``unevaluatedProperties: false``, the keys only the other branch declares are
    rejected; an own key a branch tightens is checked by the tighter type, with its constraints, aliases and a pattern
    of Python's regex engine, a ``$ref`` and a recursive reference included. An ``if`` the rule cannot read checks
    nothing, and a map that a type alias would write keeps its rule as a class, compared by its rules for reuse.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validators_conditional_outcomes.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--generate-schema-validators",
            "--formatters",
            "builtin",
            *options,
        ],
    )
    for valid_json, invalid_json, error_type, attribute_path, attribute_value in _CONDITIONAL_OUTCOME_CASES:
        assert_generated_model_json_validation(
            output_file,
            module_name="conditional_outcomes",
            model_name="Settings",
            valid_json=valid_json,
            invalid_json=invalid_json,
            expected_error_type=error_type,
            expected_attribute_path=attribute_path,
            expected_attribute_value=attribute_value,
        )


def test_conditional_properties_by_outcome_custom_helper(output_file: Path) -> None:
    """Reject a custom helper that cannot check the outcome of ``if`` before producing incorrect runtime rules."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "schema_validators_conditional_outcomes.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        expected_error=Error,
        expected_error_match="do not yet support generated if/then/else outcome checks",
        output_model_type=DataModelType.PydanticV2BaseModel,
        generate_schema_validators=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        custom_template_dir=DATA_PATH / "templates/schema_validators_presence",
    )
