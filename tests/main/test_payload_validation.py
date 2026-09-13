"""Validate generated Pydantic v2 models with schema-derived payloads."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, TypeAlias

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import Draft7Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from packaging.version import Version
from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import ValidationError

from datamodel_code_generator.format import PythonVersion
from tests.conftest import assert_inputs_not_mutated, assert_output
from tests.main.conftest import DATA_PATH, EXPECTED_MAIN_PATH, JSON_SCHEMA_DATA_PATH
from tests.main.payload_validation.codegen import PayloadAdapterError, generate_payload_runtime
from tests.main.payload_validation.schema import _schema_exclusion_reason

from .payload_validation import (
    BACKEND_ACCEPTANCE_EXCLUDED_CASES,
    BACKEND_REJECTED_MUTATION_CONSTRAINTS,
    BACKEND_REJECTION_EXCLUDED_CASES,
    BACKEND_UNASSERTED_MUTATION_CONSTRAINTS,
    PAYLOAD_MUTATION_CONSTRAINTS,
    PAYLOAD_VALIDATION_BACKENDS,
    PYDANTIC_V2_0_RUNTIME_MAX_VERSION,
    PYDANTIC_V2_0_RUNTIME_ROUND_TRIP_EXCLUDED_CASES,
    PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_EXCLUDED_CASES,
    PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN_VERSION,
    PYDANTIC_V2_LEGACY_RUNTIME_EXCLUDED_CASES,
    PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUDED_CASES,
    PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_EXCLUDED_CASES,
    PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN_VERSION,
    PYDANTIC_V2_TYPE_ALIAS_RUNTIME_EXCLUDED_CASES,
    PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN_VERSION,
    ROUND_TRIP_EXCLUDED_CASES,
    SCHEMA_CASES,
    GeneratedModelCache,
    PayloadBackend,
    SchemaCase,
    backend_acceptance_exclusion_reason,
    backend_rejection_exclusion_reason,
    discover_unaccounted_files,
    has_rejection_oracle_constraints,
    invalid_payload_mutations,
    load_generated_payload_adapter,
    load_generated_payload_runtime,
    payload_strategy,
    rejection_constraint_ids,
    source_schema_validator,
    validate_with_source_schema,
)
from .payload_validation import codegen as payload_codegen
from .payload_validation.conformance import _msgspec_type_statement_exclusion_reason
from .payload_validation.constants import (
    PAYLOAD_TARGET_PYTHON_VERSION,
    PYDANTIC_V2_FLOAT_MULTIPLE_OF_CASE_IDS,
    PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN_VERSION,
)
from .payload_validation.native_numeric import (
    NATIVE_NUMERIC_SAMPLING_ADAPTERS,
    native_float_multiple_errors,
    pydantic_payload_result,
)
from .payload_validation.schema import _schema_for_payload_generation
from .payload_validation.strategy import _bound_float_multiples

NATIVE_NUMERIC_EMPTY_ERRORS = Path(__file__).parents[1] / "data" / "payloads" / "native_numeric_empty_errors.txt"

NUMERIC_SAMPLING_PATH = Path(__file__).parents[1] / "data" / "payloads" / "numeric_sampling.json"
NUMERIC_SAMPLING_SCHEMAS = json.loads(NUMERIC_SAMPLING_PATH.read_text())
NUMERIC_SAMPLING_OUTSIDE_SCHEMAS = json.loads(
    NUMERIC_SAMPLING_PATH.with_name("numeric_sampling_outside_domain.json").read_text()
)


def _max_examples_from_env(raw_examples: str | None = None) -> int:
    if raw_examples is None:
        raw_examples = os.environ.get("DCG_PAYLOAD_MAX_EXAMPLES")
    if raw_examples is None:
        return 10
    try:
        max_examples = int(raw_examples)
    except ValueError as exc:
        msg = "DCG_PAYLOAD_MAX_EXAMPLES must be a positive integer"
        raise ValueError(msg) from exc
    if max_examples < 1:
        msg = "DCG_PAYLOAD_MAX_EXAMPLES must be a positive integer"
        raise ValueError(msg)
    return max_examples


BackendCaseMode = Literal["representative", "all"]


def _backend_case_mode_from_env(raw_mode: str | None = None) -> BackendCaseMode:
    if raw_mode is None:
        raw_mode = os.environ.get("DCG_PAYLOAD_BACKEND_CASES")
    match raw_mode:
        case None | "" | "representative":
            return "representative"
        case "all":
            return "all"
        case _:
            msg = "DCG_PAYLOAD_BACKEND_CASES must be either 'representative' or 'all'"
            raise ValueError(msg)


MAX_EXAMPLES = _max_examples_from_env()
BACKEND_CASE_MODE = _backend_case_mode_from_env()
PYDANTIC_RUNTIME_VERSION = Version(PYDANTIC_VERSION)
PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN = Version(PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN_VERSION)
PYDANTIC_V2_0_RUNTIME_MAX = Version(PYDANTIC_V2_0_RUNTIME_MAX_VERSION)
PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN = Version(PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN_VERSION)
PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN = Version(PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN_VERSION)
PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN = Version(PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN_VERSION)
PydanticV2LegacyRuntimeExclusions: TypeAlias = Mapping[PayloadBackend, Mapping[str, str]]
PydanticV2LegacyRuntimeExclusionGroups: TypeAlias = Sequence[tuple[Version, PydanticV2LegacyRuntimeExclusions]]
PYDANTIC_V2_LEGACY_RUNTIME_EXCLUSION_GROUPS: PydanticV2LegacyRuntimeExclusionGroups = (
    (PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN, PYDANTIC_V2_LEGACY_RUNTIME_EXCLUDED_CASES),
    (PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN, PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_EXCLUDED_CASES),
    (PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN, PYDANTIC_V2_TYPE_ALIAS_RUNTIME_EXCLUDED_CASES),
    (PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN, PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_EXCLUDED_CASES),
)
PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS: PydanticV2LegacyRuntimeExclusionGroups = (
    (PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN, PYDANTIC_V2_LEGACY_RUNTIME_EXCLUDED_CASES),
    (PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN, PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_EXCLUDED_CASES),
    (PYDANTIC_V2_0_RUNTIME_MAX, PYDANTIC_V2_0_RUNTIME_ROUND_TRIP_EXCLUDED_CASES),
    (PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN, PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUDED_CASES),
    (PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN, PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_EXCLUDED_CASES),
)
REJECTION_ORACLE_CASES = [case for case in SCHEMA_CASES if has_rejection_oracle_constraints(case)]
SCHEMA_CASE_BY_ID = {case.id: case for case in SCHEMA_CASES}
ROUND_TRIP_CASES = [case for case in SCHEMA_CASES if case.id not in ROUND_TRIP_EXCLUDED_CASES]


def _synthetic_schema_case(source_schema: dict[str, Any]) -> SchemaCase:
    return SchemaCase(
        id="jsonschema/synthetic_payload_validation_case.json",
        input_file_type="jsonschema",
        source_path=Path("synthetic_payload_validation_case.json"),
        source_schema=source_schema,
        codegen_schema=source_schema,
        temp_input_suffix=".json",
    )


BACKEND_ACCEPTANCE_CASE_IDS = {
    PayloadBackend.PYDANTIC_V2_DATACLASS: (
        "jsonschema/pydantic_v2_runtime_value.json",
        "jsonschema/simple_string.json",
        "jsonschema/extra_fields.json",
        "jsonschema/nested_json_pointer.json",
        "jsonschema/array_field_constraints.json",
        "openapi/api.yaml::components.schemas.Pet",
        "openapi/additional_properties.yaml::components.schemas.Error",
    ),
    PayloadBackend.MSGSPEC: (
        "jsonschema/pydantic_v2_runtime_value.json",
        "jsonschema/simple_string.json",
        "jsonschema/extra_fields.json",
        "jsonschema/array_field_constraints.json",
        "openapi/api.yaml::components.schemas.Pet",
        "openapi/additional_properties.yaml::components.schemas.Error",
    ),
    PayloadBackend.DATACLASSES: (
        "jsonschema/pydantic_v2_runtime_value.json",
        "jsonschema/extra_fields.json",
        "jsonschema/nested_json_pointer.json",
        "openapi/additional_properties.yaml::components.schemas.Error",
    ),
}
BACKEND_REJECTION_CASE_IDS = {
    PayloadBackend.PYDANTIC_V2_DATACLASS: (
        "jsonschema/pydantic_v2_runtime_value.json",
        "jsonschema/extra_fields.json",
        "jsonschema/nested_json_pointer.json",
        "openapi/additional_properties.yaml::components.schemas.Error",
    ),
    PayloadBackend.MSGSPEC: (
        "jsonschema/pydantic_v2_runtime_value.json",
        "jsonschema/extra_fields.json",
        "openapi/additional_properties.yaml::components.schemas.Error",
    ),
}


def _backend_acceptance_case_ids(
    backend: PayloadBackend,
    case_mode: BackendCaseMode = BACKEND_CASE_MODE,
) -> tuple[str, ...]:
    if case_mode == "representative" or backend is PayloadBackend.DATACLASSES:
        return BACKEND_ACCEPTANCE_CASE_IDS[backend]
    representative_cases = set(BACKEND_ACCEPTANCE_CASE_IDS[backend])
    return tuple(
        case.id
        for case in SCHEMA_CASES
        if case.id in representative_cases or backend_acceptance_exclusion_reason(case, backend) is None
    )


def _backend_rejection_case_ids(
    backend: PayloadBackend,
    case_mode: BackendCaseMode = BACKEND_CASE_MODE,
) -> tuple[str, ...]:
    if case_mode == "representative":
        return BACKEND_REJECTION_CASE_IDS[backend]
    representative_cases = set(BACKEND_REJECTION_CASE_IDS[backend])
    return tuple(
        case.id
        for case in SCHEMA_CASES
        if (case.id in representative_cases or backend_rejection_exclusion_reason(case, backend) is None)
        and has_rejection_oracle_constraints(case, backend)
    )


def _pydantic_v2_legacy_runtime_exclusion_reason(
    case: SchemaCase,
    backend: PayloadBackend,
    runtime_version: Version = PYDANTIC_RUNTIME_VERSION,
    exclusion_groups: PydanticV2LegacyRuntimeExclusionGroups = PYDANTIC_V2_LEGACY_RUNTIME_EXCLUSION_GROUPS,
) -> str | None:
    for max_version, excluded_cases in exclusion_groups:
        if runtime_version >= max_version:
            continue
        if reason := excluded_cases.get(backend, {}).get(case.id):
            return reason
    return None


def _pydantic_v2_legacy_runtime_skip_marks(
    case: SchemaCase,
    backend: PayloadBackend,
    runtime_version: Version = PYDANTIC_RUNTIME_VERSION,
    exclusion_groups: PydanticV2LegacyRuntimeExclusionGroups = PYDANTIC_V2_LEGACY_RUNTIME_EXCLUSION_GROUPS,
) -> tuple[pytest.MarkDecorator, ...]:
    if reason := _pydantic_v2_legacy_runtime_exclusion_reason(case, backend, runtime_version, exclusion_groups):
        return (pytest.mark.skip(reason=reason),)
    return ()


def _pydantic_v2_case_param(case: SchemaCase) -> pytest.ParameterSet:
    return pytest.param(
        case,
        id=case.id,
        marks=_pydantic_v2_legacy_runtime_skip_marks(case, PayloadBackend.PYDANTIC_V2),
    )


def _pydantic_v2_round_trip_case_param(case: SchemaCase) -> pytest.ParameterSet:
    return pytest.param(
        case,
        id=case.id,
        marks=_pydantic_v2_legacy_runtime_skip_marks(
            case,
            PayloadBackend.PYDANTIC_V2,
            exclusion_groups=PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS,
        ),
    )


def _backend_case_param(backend: PayloadBackend, case: SchemaCase) -> pytest.ParameterSet:
    return pytest.param(
        backend,
        case,
        id=f"{backend.value}:{case.id}",
        marks=_pydantic_v2_legacy_runtime_skip_marks(case, backend),
    )


PYDANTIC_V2_ACCEPTANCE_CASES = [_pydantic_v2_case_param(case) for case in SCHEMA_CASES]
PYDANTIC_V2_ROUND_TRIP_CASES = [_pydantic_v2_round_trip_case_param(case) for case in ROUND_TRIP_CASES]
PYDANTIC_V2_REJECTION_CASES = [_pydantic_v2_case_param(case) for case in REJECTION_ORACLE_CASES]
BACKEND_ACCEPTANCE_CASES = [
    _backend_case_param(backend, SCHEMA_CASE_BY_ID[case_id])
    for backend in BACKEND_ACCEPTANCE_CASE_IDS
    for case_id in _backend_acceptance_case_ids(backend)
]
BACKEND_REJECTION_CASES = [
    _backend_case_param(backend, SCHEMA_CASE_BY_ID[case_id])
    for backend in BACKEND_REJECTION_CASE_IDS
    for case_id in _backend_rejection_case_ids(backend)
]


@pytest.fixture(scope="session")
def generated_model_cache(tmp_path_factory: pytest.TempPathFactory) -> GeneratedModelCache:
    """Cache generated Payload adapters across Hypothesis examples."""
    return GeneratedModelCache({"base": tmp_path_factory.mktemp("payload_validation"), "adapters": {}})


def test_numeric_payload_sampling_preserves_source_schemas() -> None:
    """Only actual numeric schemas receive sampling bounds; literals retain their bytes and order."""
    with assert_inputs_not_mutated({"schemas": NUMERIC_SAMPLING_SCHEMAS}):
        normalized = {}
        for name, original in NUMERIC_SAMPLING_SCHEMAS.items():
            schema = _schema_for_payload_generation(deepcopy(original))
            _bound_float_multiples(schema)
            normalized[name] = schema
        assert_output(
            json.dumps(normalized, indent=2) + "\n",
            NUMERIC_SAMPLING_PATH.parents[1] / "expected" / "payloads" / "numeric_sampling.txt",
        )


@pytest.mark.parametrize(("name", "schema"), NUMERIC_SAMPLING_SCHEMAS.items())
@settings(
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=100,
    suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
)
@given(data=st.data())
def test_numeric_payload_sampling_validates_generated_models(
    name: str,
    schema: dict[str, Any],
    generated_model_cache: GeneratedModelCache,
    data: st.DataObject,
) -> None:
    """Finite float multiples remain source-valid before and after generated-model validation."""
    case = SchemaCase(
        id=f"numeric_sampling/{name}",
        input_file_type="jsonschema",
        source_path=NUMERIC_SAMPLING_PATH,
        source_schema=schema,
        codegen_schema=schema,
        temp_input_suffix=".json",
    )
    with assert_inputs_not_mutated({"source": schema, "codegen": case.codegen_schema}):
        payload = data.draw(payload_strategy(case), label=name)
        validate_with_source_schema(case, payload)
        adapter = load_generated_payload_adapter(case, generated_model_cache)
        expected_errors = (
            pydantic_payload_result(native_adapter, payload)[1]
            if (native_adapter := NATIVE_NUMERIC_SAMPLING_ADAPTERS.get(name)) is not None
            else []
        )
        validated, errors = pydantic_payload_result(adapter, payload)
        assert_output(
            json.dumps([] if errors == expected_errors else {"expected": expected_errors, "actual": errors}, indent=2),
            NATIVE_NUMERIC_EMPTY_ERRORS,
        )
        if errors:
            return
        dumped = adapter.dump_python(validated, mode="json", by_alias=True, exclude_unset=True)
        validate_with_source_schema(case, dumped)


@pytest.mark.parametrize(("name", "schema"), NUMERIC_SAMPLING_OUTSIDE_SCHEMAS.items())
def test_numeric_payload_sampling_reports_unrepresentable_interval(name: str, schema: dict[str, Any]) -> None:
    """An unsupported sampling interval is diagnosed without claiming the source schema is empty."""
    case = SchemaCase(
        id=f"numeric_sampling/{name}",
        input_file_type="jsonschema",
        source_path=NUMERIC_SAMPLING_PATH.with_name("numeric_sampling_outside_domain.json"),
        source_schema=schema,
        codegen_schema=schema,
        temp_input_suffix=".json",
    )
    validate_with_source_schema(case, schema["examples"][0])
    with (
        assert_inputs_not_mutated({"source": schema}),
        pytest.raises(ValueError, match="Numeric interval lies outside the finite multipleOf sampling domain"),
    ):
        payload_strategy(case)


def test_payload_validation_cases_cover_discovered_schema_files() -> None:
    """Every discovered schema fixture must be validated or explicitly excluded."""
    unaccounted = discover_unaccounted_files(SCHEMA_CASES)
    if unaccounted:  # pragma: no cover
        pytest.fail(
            "Schema files must be covered by payload validation or added to EXCLUDED_FILES with a reason:\n"
            + "\n".join(unaccounted)
        )


def test_payload_rejection_oracle_policy_is_classified() -> None:
    """Every invalid-payload mutation constraint must be machine-classified."""
    for backend in PAYLOAD_VALIDATION_BACKENDS:
        rejected_constraints = BACKEND_REJECTED_MUTATION_CONSTRAINTS[backend]
        unasserted_constraints = BACKEND_UNASSERTED_MUTATION_CONSTRAINTS[backend]
        if overlap := sorted(  # pragma: no cover
            set(rejected_constraints) & set(unasserted_constraints)
        ):
            pytest.fail(
                f"{backend.value}: mutation constraints cannot be both asserted and unasserted:\n" + "\n".join(overlap)
            )

        if missing := sorted(  # pragma: no cover
            PAYLOAD_MUTATION_CONSTRAINTS - set(rejected_constraints) - set(unasserted_constraints)
        ):
            pytest.fail(f"{backend.value}: mutation constraints need rejection policy reasons:\n" + "\n".join(missing))

        reasons = {
            **rejected_constraints,
            **unasserted_constraints,
        }
        if unclassified := sorted(  # pragma: no cover
            constraint for constraint, reason in reasons.items() if not reason
        ):
            pytest.fail(f"{backend.value}: mutation policy entries need non-empty reasons:\n" + "\n".join(unclassified))


def test_payload_rejection_oracle_covers_supported_policy_constraints() -> None:
    """The supported rejection policy must correspond to at least one schema case."""
    for backend, rejected_constraints in BACKEND_REJECTED_MUTATION_CONSTRAINTS.items():
        covered_constraints = frozenset(
            constraint for case in SCHEMA_CASES for constraint in rejection_constraint_ids(case, backend)
        )
        if missing := sorted(  # pragma: no cover
            set(rejected_constraints) - covered_constraints
        ):
            pytest.fail(
                f"{backend.value}: supported rejection constraints are not covered by schema cases:\n"
                + "\n".join(missing)
            )


def test_payload_backend_representative_matrix_is_classified() -> None:
    """Every non-default backend must declare representative cases for its supported oracle shape."""
    missing_acceptance = [
        backend.value
        for backend in (PayloadBackend.PYDANTIC_V2_DATACLASS, PayloadBackend.MSGSPEC, PayloadBackend.DATACLASSES)
        if not BACKEND_ACCEPTANCE_CASE_IDS.get(backend)
    ]
    if missing_acceptance:  # pragma: no cover
        pytest.fail("Payload backend acceptance matrix needs representative cases:\n" + "\n".join(missing_acceptance))

    missing_rejection = [
        backend.value
        for backend in PAYLOAD_VALIDATION_BACKENDS
        if backend is not PayloadBackend.PYDANTIC_V2 and not BACKEND_REJECTION_CASE_IDS.get(backend)
    ]
    if missing_rejection:  # pragma: no cover
        pytest.fail("Payload backend rejection matrix needs representative cases:\n" + "\n".join(missing_rejection))


def test_payload_backend_full_matrix_exclusions_are_classified() -> None:
    """Full backend matrix exclusions must reference existing cases and carry reasons."""
    known_cases = set(SCHEMA_CASE_BY_ID)
    exclusion_sets = {
        "acceptance": BACKEND_ACCEPTANCE_EXCLUDED_CASES,
        "rejection": BACKEND_REJECTION_EXCLUDED_CASES,
    }
    for exclusion_type, backend_exclusions in exclusion_sets.items():
        for backend, excluded_cases in backend_exclusions.items():
            if missing_reasons := sorted(  # pragma: no cover
                case_id for case_id, reason in excluded_cases.items() if not reason
            ):
                pytest.fail(
                    f"{backend.value}: {exclusion_type} exclusions require reasons:\n" + "\n".join(missing_reasons)
                )
            if unknown_cases := sorted(set(excluded_cases) - known_cases):  # pragma: no cover
                pytest.fail(
                    f"{backend.value}: {exclusion_type} exclusions reference unknown cases:\n"
                    + "\n".join(unknown_cases)
                )

    representative_dataclass_cases = set(BACKEND_ACCEPTANCE_CASE_IDS[PayloadBackend.DATACLASSES])
    if unclassified_dataclasses := [  # pragma: no cover
        case.id
        for case in SCHEMA_CASES
        if case.id not in representative_dataclass_cases
        and not backend_acceptance_exclusion_reason(case, PayloadBackend.DATACLASSES)
    ]:
        pytest.fail(
            f"{PayloadBackend.DATACLASSES.value}: full-matrix exclusions require reasons:\n"
            + "\n".join(unclassified_dataclasses)
        )

    for backend in (PayloadBackend.PYDANTIC_V2_DATACLASS, PayloadBackend.MSGSPEC):
        representative_acceptance_cases = set(BACKEND_ACCEPTANCE_CASE_IDS[backend])
        if unclassified_acceptance := sorted(  # pragma: no cover
            case.id
            for case in SCHEMA_CASES
            if case.input_file_type == "openapi"
            and case.id not in representative_acceptance_cases
            and not backend_acceptance_exclusion_reason(case, backend)
        ):
            pytest.fail(
                f"{backend.value}: OpenAPI acceptance exclusions require reasons:\n"
                + "\n".join(unclassified_acceptance)
            )

        representative_rejection_cases = set(BACKEND_REJECTION_CASE_IDS.get(backend, ()))
        if unclassified_rejection := sorted(  # pragma: no cover
            case.id
            for case in SCHEMA_CASES
            if case.input_file_type == "openapi"
            and case.id not in representative_rejection_cases
            and has_rejection_oracle_constraints(case, backend)
            and not backend_rejection_exclusion_reason(case, backend)
        ):
            pytest.fail(
                f"{backend.value}: OpenAPI rejection exclusions require reasons:\n" + "\n".join(unclassified_rejection)
            )


@pytest.mark.parametrize(
    "case_id",
    [
        "jsonschema/constrained_types_keyword_order.json",
        "jsonschema/allof_constraint_intersections/complex.json",
        "jsonschema/allof_constraint_intersections/metadata.json",
        "jsonschema/allof_constraint_intersections/scalars_equal.json",
        "jsonschema/allof_constraint_intersections/scalars_partial.json",
        "jsonschema/custom_template_dependencies_many.json",
        "jsonschema/optional_nested_factory_requirements/union.json",
        "jsonschema/numeric_constraint_precision/large.json",
        "jsonschema/exact_imports_collapse_root_models_module_split_oneof.json",
        "jsonschema/strict_types.json",
        "jsonschema/type_array_only_null.json",
        "jsonschema/type_mappings.json",
    ],
)
@pytest.mark.allow_direct_assert
def test_msgspec_schema_runtime_exclusions_cover_known_semantic_gaps(case_id: str) -> None:
    """Known msgspec runtime semantic gaps must stay outside the full payload matrix."""
    case = SCHEMA_CASE_BY_ID[case_id]
    assert backend_acceptance_exclusion_reason(case, PayloadBackend.MSGSPEC)
    assert backend_rejection_exclusion_reason(case, PayloadBackend.MSGSPEC)


@pytest.mark.parametrize(
    ("case_id", "expected_reason"),
    [
        (
            "jsonschema/unique_items_union_enum_root.json",
            "msgspec conversion only supports Enum classes with homogeneous str or int values",
        ),
        (
            "jsonschema/unique_items_union_ref_sibling_2020.json",
            "msgspec conversion rejects unions containing multiple array-like runtime types",
        ),
        (
            "jsonschema/unique_items_union_ref_sibling_draft7.json",
            "msgspec conversion rejects unions containing multiple array-like runtime types",
        ),
    ],
)
@pytest.mark.allow_direct_assert
def test_msgspec_unique_items_runtime_exclusions_match_type_limits(case_id: str, expected_reason: str) -> None:
    """UniqueItems fixtures preserve msgspec's array, mapping, and Enum union limits."""
    case = SCHEMA_CASE_BY_ID[case_id]
    assert backend_acceptance_exclusion_reason(case, PayloadBackend.MSGSPEC) == expected_reason
    assert backend_rejection_exclusion_reason(case, PayloadBackend.MSGSPEC) == expected_reason


@pytest.mark.parametrize(
    ("case_id", "expected_reason"),
    [
        (
            "jsonschema/unique_items_schema_validators.json",
            "msgspec conversion rejects unions containing multiple array-like runtime types",
        ),
        (
            "jsonschema/unique_items_union_ref_mapping_root.json",
            "msgspec conversion rejects unions containing multiple dict-like runtime types",
        ),
    ],
)
@pytest.mark.allow_direct_assert
def test_msgspec_pep_695_unique_items_runtime_exclusions_match_target(case_id: str, expected_reason: str) -> None:
    """PEP 695 aliases make these msgspec unions unsupported only for Python 3.12+ targets."""
    case = SCHEMA_CASE_BY_ID[case_id]
    current_target = PythonVersion(PAYLOAD_TARGET_PYTHON_VERSION)
    current_reason = expected_reason if current_target.has_type_statement else None

    assert backend_acceptance_exclusion_reason(case, PayloadBackend.MSGSPEC) == current_reason
    assert backend_rejection_exclusion_reason(case, PayloadBackend.MSGSPEC) == current_reason
    assert _msgspec_type_statement_exclusion_reason(case, PythonVersion.PY_311) is None
    assert _msgspec_type_statement_exclusion_reason(case, PythonVersion.PY_312) == expected_reason


@pytest.mark.allow_direct_assert
def test_msgspec_schema_runtime_exclusions_ignore_literal_payloads() -> None:
    """Literal payload values must not be traversed as nested JSON Schemas."""
    case = _synthetic_schema_case({
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "enum": [{"type": "object", "properties": {"value": {"type": "null"}}}],
        "default": {"type": "string", "format": "binary"},
        "examples": [{"type": "number", "multipleOf": 0.5}],
    })

    assert backend_acceptance_exclusion_reason(case, PayloadBackend.MSGSPEC) is None
    assert backend_rejection_exclusion_reason(case, PayloadBackend.MSGSPEC) is None


@pytest.mark.allow_direct_assert
def test_msgspec_schema_runtime_exclusions_detect_untyped_fractional_multiple_of() -> None:
    """JSON Schema multipleOf remains numeric metadata when type is omitted."""
    case = _synthetic_schema_case({"multipleOf": 0.5})

    assert backend_acceptance_exclusion_reason(case, PayloadBackend.MSGSPEC)
    assert backend_rejection_exclusion_reason(case, PayloadBackend.MSGSPEC)


@pytest.mark.allow_direct_assert
def test_payload_codegen_restores_parsed_source_cache_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Payload codegen restores the parsed-source cache hook after generation failures."""
    events: list[str] = []

    def enable_cache() -> Any:
        events.append("enable")
        return lambda: events.append("restore")

    def fail_codegen(_args: list[str]) -> payload_codegen.Exit:
        events.append("main")
        msg = "generation failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(payload_codegen, "enable_parsed_source_cache", enable_cache)
    monkeypatch.setattr(payload_codegen, "main", fail_codegen)

    with pytest.raises(RuntimeError, match="generation failed"):
        payload_codegen._run_payload_codegen([])

    assert events == ["enable", "main", "restore"]


def test_payload_round_trip_exclusions_are_classified() -> None:
    """Round-trip dump exclusions must name existing cases and carry reasons."""
    if missing_reasons := sorted(  # pragma: no cover
        case_id for case_id, reason in ROUND_TRIP_EXCLUDED_CASES.items() if not reason
    ):
        pytest.fail("Payload round-trip exclusions require reasons:\n" + "\n".join(missing_reasons))

    if unknown_cases := sorted(set(ROUND_TRIP_EXCLUDED_CASES) - set(SCHEMA_CASE_BY_ID)):  # pragma: no cover
        pytest.fail("Payload round-trip exclusions reference unknown cases:\n" + "\n".join(unknown_cases))


@pytest.mark.parametrize(
    "case_id",
    [
        "jsonschema/one_of_with_sub_schema_array_item.json",
        "jsonschema/unique_items_schema_validators.json",
    ],
)
@pytest.mark.allow_direct_assert
def test_pydantic_round_trip_exclusions_cover_unique_items_normalization(case_id: str) -> None:
    """Pydantic normalization can collapse distinct JSON Schema uniqueItems values."""
    assert ROUND_TRIP_EXCLUDED_CASES[case_id]
    assert case_id not in {case.id for case in ROUND_TRIP_CASES}


def test_pydantic_v2_legacy_runtime_exclusions_are_classified() -> None:
    """Pydantic-version-specific payload exclusions must name existing cases and carry reasons."""
    known_cases = set(SCHEMA_CASE_BY_ID)
    supported_backends = {PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS}
    exclusion_groups = {
        "payload": PYDANTIC_V2_LEGACY_RUNTIME_EXCLUSION_GROUPS,
        "round-trip": PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS,
    }
    for exclusion_kind, exclusion_group in exclusion_groups.items():
        for _, exclusion_set in exclusion_group:
            if unsupported_backends := sorted(
                backend.value for backend in exclusion_set if backend not in supported_backends
            ):  # pragma: no cover
                pytest.fail(
                    f"Pydantic legacy runtime {exclusion_kind} exclusions reference unsupported backends:\n"
                    + "\n".join(unsupported_backends)
                )

            for backend, excluded_cases in exclusion_set.items():
                if missing_reasons := sorted(  # pragma: no cover
                    case_id for case_id, reason in excluded_cases.items() if not reason
                ):
                    pytest.fail(
                        f"{backend.value}: Pydantic legacy runtime {exclusion_kind} exclusions require reasons:\n"
                        + "\n".join(missing_reasons)
                    )
                if unknown_cases := sorted(set(excluded_cases) - known_cases):  # pragma: no cover
                    pytest.fail(
                        f"{backend.value}: Pydantic {exclusion_kind} exclusions reference unknown cases:\n"
                        + "\n".join(unknown_cases)
                    )


@pytest.mark.allow_direct_assert
def test_pydantic_v2_legacy_runtime_exclusions_are_version_gated() -> None:
    """Pydantic 2.0 keeps only version-specific skips; newer runtimes keep full coverage."""
    case = SCHEMA_CASE_BY_ID["jsonschema/lookaround_anyof_nullable.json"]
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, PayloadBackend.PYDANTIC_V2, Version("2.0.3"))
    assert (
        _pydantic_v2_legacy_runtime_exclusion_reason(
            case, PayloadBackend.PYDANTIC_V2, PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN
        )
        is None
    )
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, PayloadBackend.MSGSPEC, Version("2.0.3")) is None
    assert _pydantic_v2_legacy_runtime_skip_marks(case, PayloadBackend.PYDANTIC_V2, Version("2.0.3"))
    assert not _pydantic_v2_legacy_runtime_skip_marks(
        case, PayloadBackend.PYDANTIC_V2, PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN
    )
    for round_trip_case_id, max_version in (
        ("jsonschema/msgspec_payload_runtime_compatibility.json", PYDANTIC_V2_0_RUNTIME_MAX),
        ("jsonschema/property_names_ref_enum.json", PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN),
    ):
        round_trip_case = SCHEMA_CASE_BY_ID[round_trip_case_id]
        assert _pydantic_v2_legacy_runtime_exclusion_reason(
            round_trip_case,
            PayloadBackend.PYDANTIC_V2,
            Version("2.0.3"),
            PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS,
        )
        assert (
            _pydantic_v2_legacy_runtime_exclusion_reason(
                round_trip_case,
                PayloadBackend.PYDANTIC_V2,
                max_version,
                PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS,
            )
            is None
        )
        assert (
            _pydantic_v2_legacy_runtime_exclusion_reason(
                round_trip_case,
                PayloadBackend.PYDANTIC_V2,
                Version("2.0.3"),
            )
            is None
        )


@pytest.mark.parametrize(
    ("backend", "case_id"),
    [
        pytest.param(backend, case_id, id=f"{backend.value}:{case_id}")
        for backend in (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS)
        for case_id in PYDANTIC_V2_FLOAT_MULTIPLE_OF_CASE_IDS
    ],
)
@pytest.mark.parametrize(
    "exclusion_groups",
    [
        pytest.param(PYDANTIC_V2_LEGACY_RUNTIME_EXCLUSION_GROUPS, id="default"),
        pytest.param(PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS, id="round-trip"),
    ],
)
@pytest.mark.allow_direct_assert
def test_pydantic_v2_float_multiple_of_exclusions_are_version_gated(
    backend: PayloadBackend,
    case_id: str,
    exclusion_groups: PydanticV2LegacyRuntimeExclusionGroups,
) -> None:
    """Pydantic before 2.5.2 rejects some source-valid float multipleOf payloads."""
    case = SCHEMA_CASE_BY_ID[case_id]
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, backend, Version("2.5.1"), exclusion_groups)
    exclusion_reason = _pydantic_v2_legacy_runtime_exclusion_reason(
        case, backend, PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN, exclusion_groups
    )
    assert exclusion_reason is None


@pytest.mark.parametrize("backend", [PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS])
@pytest.mark.allow_direct_assert
def test_pydantic_v2_legacy_runtime_cross_module_lookaround_exclusions_are_version_gated(
    backend: PayloadBackend,
) -> None:
    """Cross-module RootModel lookaround validators need Pydantic 2.5+ for both Pydantic backends."""
    case = SCHEMA_CASE_BY_ID["jsonschema/schema_validators_runtime_root_cross_module/a.json"]
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, backend, Version("2.0.3"))
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, backend, PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN) is None


@pytest.mark.parametrize(
    ("case_id", "legacy_version", "min_version"),
    [
        ("jsonschema/false_reference_fast_path.json", Version("2.9.2"), PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN),
        ("jsonschema/use_decimal_for_multiple_of.json", Version("2.0.3"), PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN),
    ],
)
@pytest.mark.allow_direct_assert
def test_pydantic_v2_dataclass_legacy_exclusions_are_version_gated(
    case_id: str, legacy_version: Version, min_version: Version
) -> None:
    """Pydantic v2 dataclass old-runtime exclusions restore coverage at the configured cutoff."""
    case = SCHEMA_CASE_BY_ID[case_id]
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, PayloadBackend.PYDANTIC_V2_DATACLASS, legacy_version)
    assert (
        _pydantic_v2_legacy_runtime_exclusion_reason(
            case,
            PayloadBackend.PYDANTIC_V2_DATACLASS,
            min_version,
        )
        is None
    )


@pytest.mark.allow_direct_assert
def test_pydantic_v2_dataclass_type_alias_exclusion_is_backend_specific() -> None:
    """Only the generated dataclass form needs the old Pydantic nested TypeAliasType exclusion."""
    case = SCHEMA_CASE_BY_ID["jsonschema/false_reference_fast_path.json"]
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, PayloadBackend.PYDANTIC_V2_DATACLASS, Version("2.9.2"))
    assert _pydantic_v2_legacy_runtime_exclusion_reason(case, PayloadBackend.PYDANTIC_V2, Version("2.9.2")) is None


@pytest.mark.parametrize(
    ("raw_examples", "expected_examples"),
    [
        (None, 10),
        ("1", 1),
        ("100", 100),
    ],
)
@pytest.mark.allow_direct_assert
def test_payload_max_examples_env_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    raw_examples: str | None,
    expected_examples: int,
) -> None:
    """Nightly runs can raise Hypothesis examples without changing PR defaults."""
    monkeypatch.delenv("DCG_PAYLOAD_MAX_EXAMPLES", raising=False)
    assert _max_examples_from_env(raw_examples) == expected_examples
    monkeypatch.setenv("DCG_PAYLOAD_MAX_EXAMPLES", "50")
    assert _max_examples_from_env(None) == 50


@pytest.mark.parametrize("raw_examples", ["", "0", "-1", "not-an-int"])
def test_payload_max_examples_env_rejects_invalid_values(raw_examples: str) -> None:
    """Invalid example-count overrides fail before test settings are created."""
    with pytest.raises(ValueError, match="positive integer"):
        _max_examples_from_env(raw_examples)


@pytest.mark.parametrize(
    ("raw_mode", "expected_mode"),
    [
        (None, "representative"),
        ("", "representative"),
        ("representative", "representative"),
        ("all", "all"),
    ],
)
@pytest.mark.allow_direct_assert
def test_payload_backend_case_mode_env_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    raw_mode: str | None,
    expected_mode: str,
) -> None:
    """Nightly runs can widen non-default backend coverage without changing PR defaults."""
    monkeypatch.delenv("DCG_PAYLOAD_BACKEND_CASES", raising=False)
    assert _backend_case_mode_from_env(raw_mode) == expected_mode
    monkeypatch.setenv("DCG_PAYLOAD_BACKEND_CASES", "all")
    assert _backend_case_mode_from_env(None) == "all"


def test_payload_backend_case_mode_env_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid backend matrix modes fail before parametrization."""
    with pytest.raises(ValueError, match="representative"):
        _backend_case_mode_from_env("everything")
    monkeypatch.setenv("DCG_PAYLOAD_BACKEND_CASES", "everything")
    with pytest.raises(ValueError, match="representative"):
        _backend_case_mode_from_env(None)


@pytest.mark.allow_direct_assert
def test_payload_backend_all_case_mode_widens_runtime_validating_backends() -> None:
    """Full backend mode widens runtime-validating backends and leaves plain dataclasses representative-only."""
    for backend in (PayloadBackend.PYDANTIC_V2_DATACLASS, PayloadBackend.MSGSPEC):
        representative_acceptance_cases = set(_backend_acceptance_case_ids(backend, "representative"))
        all_acceptance_cases = set(_backend_acceptance_case_ids(backend, "all"))
        assert representative_acceptance_cases < all_acceptance_cases
        assert any(
            case_id.startswith("jsonschema/") for case_id in all_acceptance_cases - representative_acceptance_cases
        )

        representative_rejection_cases = set(_backend_rejection_case_ids(backend, "representative"))
        all_rejection_cases = set(_backend_rejection_case_ids(backend, "all"))
        assert representative_rejection_cases < all_rejection_cases
        assert all(
            has_rejection_oracle_constraints(SCHEMA_CASE_BY_ID[case_id], backend) for case_id in all_rejection_cases
        )

    assert _backend_acceptance_case_ids(PayloadBackend.DATACLASSES, "all") == _backend_acceptance_case_ids(
        PayloadBackend.DATACLASSES,
        "representative",
    )


@pytest.mark.parametrize("case", PYDANTIC_V2_ACCEPTANCE_CASES)
def test_generated_pydantic_v2_model_accepts_schema_derived_payloads(
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
) -> None:
    """Check every schema with fresh Hypothesis settings, avoiding parametrization parent chains."""

    @settings(
        database=None,
        deadline=None,
        derandomize=True,
        max_examples=MAX_EXAMPLES,
        suppress_health_check=[
            HealthCheck.filter_too_much,
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    @given(data=st.data())
    def check_acceptance(data: st.DataObject) -> None:
        """Source-valid payloads preserve acceptance or a proven native float rejection."""
        payload = data.draw(payload_strategy(case), label=case.id)
        validate_with_source_schema(case, payload)
        adapter = load_generated_payload_adapter(case, generated_model_cache)
        expected_errors = native_float_multiple_errors(case, payload)
        validated, errors = pydantic_payload_result(adapter, payload)
        expected = expected_errors or []
        assert_output(
            json.dumps([] if errors == expected else {"expected": expected, "actual": errors}, indent=2),
            NATIVE_NUMERIC_EMPTY_ERRORS,
        )
        if expected_errors is not None and not errors:
            dumped = adapter.dump_python(validated, mode="json", by_alias=True, exclude_unset=True)
            validate_with_source_schema(case, dumped)

    check_acceptance()


@pytest.mark.parametrize("case", PYDANTIC_V2_ROUND_TRIP_CASES)
def test_generated_pydantic_v2_model_dumps_schema_valid_payloads(
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
) -> None:
    """Check every schema with fresh Hypothesis settings, avoiding parametrization parent chains."""

    @settings(
        database=None,
        deadline=None,
        derandomize=True,
        max_examples=MAX_EXAMPLES,
        suppress_health_check=[
            HealthCheck.filter_too_much,
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    @given(data=st.data())
    def check_round_trip(data: st.DataObject) -> None:
        """Payloads accepted by generated models should dump back to source-valid JSON."""
        payload = data.draw(payload_strategy(case), label=f"{case.id}:round_trip")
        validate_with_source_schema(case, payload)
        adapter = load_generated_payload_adapter(case, generated_model_cache)
        expected_errors = native_float_multiple_errors(case, payload) or []
        validated_payload, errors = pydantic_payload_result(adapter, payload)
        assert_output(
            json.dumps([] if errors == expected_errors else {"expected": expected_errors, "actual": errors}, indent=2),
            NATIVE_NUMERIC_EMPTY_ERRORS,
        )
        if errors:
            return
        dumped_payload = adapter.dump_python(validated_payload, mode="json", by_alias=True, exclude_unset=True)
        validate_with_source_schema(case, dumped_payload)

    check_round_trip()


@pytest.mark.parametrize("case", PYDANTIC_V2_REJECTION_CASES)
@settings(
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=MAX_EXAMPLES,
    suppress_health_check=[
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(data=st.data())
def test_generated_pydantic_v2_model_rejects_schema_invalid_payloads(
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
    data: st.DataObject,
) -> None:
    """Near-miss payloads rejected by the source schema should fail generated validation."""
    payload = data.draw(payload_strategy(case), label=f"{case.id}:valid")
    validate_with_source_schema(case, payload)
    mutations = invalid_payload_mutations(case, payload)
    assume(bool(mutations))

    mutation = data.draw(st.sampled_from(mutations), label=f"{case.id}:invalid")
    if source_schema_validator(case).is_valid(mutation.payload):  # pragma: no cover
        pytest.fail(f"{case.id}: mutation stayed source-valid for {mutation.constraint} at {mutation.path}")

    adapter = load_generated_payload_adapter(case, generated_model_cache)
    with pytest.raises(ValidationError):
        adapter.validate_python(mutation.payload)


@pytest.mark.parametrize(("backend", "case"), BACKEND_ACCEPTANCE_CASES)
def test_generated_payload_backend_accepts_representative_schema_payloads(
    backend: PayloadBackend,
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
) -> None:
    """Check each backend case with fresh Hypothesis settings, avoiding parametrization parent chains."""

    @settings(
        database=None,
        deadline=None,
        derandomize=True,
        max_examples=MAX_EXAMPLES,
        suppress_health_check=[
            HealthCheck.filter_too_much,
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    @given(data=st.data())
    def check_acceptance(data: st.DataObject) -> None:
        """Non-default backends should accept source-valid payloads."""
        payload = data.draw(payload_strategy(case), label=f"{backend.value}:{case.id}:valid")
        validate_with_source_schema(case, payload)
        runtime = load_generated_payload_runtime(case, generated_model_cache, backend)
        runtime.validate_python(payload)

    check_acceptance()


@pytest.mark.parametrize(("backend", "case"), BACKEND_REJECTION_CASES)
@settings(
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=MAX_EXAMPLES,
    suppress_health_check=[
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(data=st.data())
def test_generated_payload_backend_rejects_representative_schema_invalid_payloads(
    backend: PayloadBackend,
    case: SchemaCase,
    generated_model_cache: dict[str, Any],
    data: st.DataObject,
) -> None:
    """Representative runtime-validation backends should reject supported near-miss payloads."""
    payload = data.draw(payload_strategy(case), label=f"{backend.value}:{case.id}:valid")
    validate_with_source_schema(case, payload)
    mutations = invalid_payload_mutations(case, payload, backend)
    assume(bool(mutations))

    mutation = data.draw(st.sampled_from(mutations), label=f"{backend.value}:{case.id}:invalid")
    if source_schema_validator(case).is_valid(mutation.payload):  # pragma: no cover
        pytest.fail(f"{case.id}: mutation stayed source-valid for {mutation.constraint} at {mutation.path}")

    runtime = load_generated_payload_runtime(case, generated_model_cache, backend)
    runtime.assert_rejects_python(mutation.payload)


LITERAL_DATA_PATH = Path(__file__).parents[1] / "data" / "payloads" / "literal_witnesses_schemas.json"


LITERAL_SCHEMAS = json.loads(LITERAL_DATA_PATH.read_text(encoding="utf-8"))


LITERAL_CASES = {
    name: SchemaCase(name, "jsonschema", LITERAL_DATA_PATH, schema, schema, ".json")
    for name, schema in LITERAL_SCHEMAS.items()
}


@pytest.mark.parametrize("name", LITERAL_CASES)
@settings(
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=20,
    suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
)
@given(data=st.data())
def test_literal_source_payloads(name: str, data: st.DataObject) -> None:
    """Sample literal intersections, nonliteral regexes, nested schemas and ordinary primitives."""
    case = LITERAL_CASES[name]
    with assert_inputs_not_mutated({"schema": case.source_schema}):
        validate_with_source_schema(case, data.draw(payload_strategy(case)))


def test_literal_source_witnesses() -> None:
    """The unchanged source proves both literal orders and rejects incomplete intersections."""
    witnesses = json.loads(LITERAL_DATA_PATH.with_name("literal_witnesses_values.json").read_text(encoding="utf-8"))[
        "unicode"
    ]
    with assert_inputs_not_mutated({"schema": LITERAL_CASES["unicode"].source_schema, "witnesses": witnesses}):
        for payload in witnesses["valid"]:
            validate_with_source_schema(LITERAL_CASES["unicode"], payload)
        for payload in witnesses["invalid"]:
            with pytest.raises(JsonSchemaValidationError):
                validate_with_source_schema(LITERAL_CASES["unicode"], payload)


PAYLOAD_UNION_DATA_PATH = Path(__file__).parents[1] / "data" / "payloads" / "union_capabilities_schemas.json"


PAYLOAD_UNION_SCHEMAS = json.loads(PAYLOAD_UNION_DATA_PATH.read_text(encoding="utf-8"))


PAYLOAD_UNION_CASES = {
    name: SchemaCase(name, "jsonschema", PAYLOAD_UNION_DATA_PATH, schema, schema, ".json")
    for name, schema in PAYLOAD_UNION_SCHEMAS.items()
}


@pytest.mark.parametrize("name", ["permissive_union", "required_union", "required_allof"])
@settings(
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=20,
    suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_union_source_payloads(name: str, generated_model_cache: dict[str, Any], data: st.DataObject) -> None:
    """Preserve source-valid payloads through actual generated model validation and dumping."""
    case = PAYLOAD_UNION_CASES[name]
    with assert_inputs_not_mutated({"schema": case.source_schema}):
        payload = data.draw(payload_strategy(case))
        validate_with_source_schema(case, payload)
        adapter = load_generated_payload_adapter(case, generated_model_cache)
        validated = adapter.validate_python(payload)
        validate_with_source_schema(case, adapter.dump_python(validated, mode="json", exclude_unset=True))


def test_union_rejection_capabilities() -> None:
    """A permissive alternative cannot promise rejection of a required-field deletion."""
    with assert_inputs_not_mutated({"schemas": PAYLOAD_UNION_SCHEMAS}):
        capabilities = {name: sorted(rejection_constraint_ids(case)) for name, case in PAYLOAD_UNION_CASES.items()}
        assert_output(
            json.dumps(capabilities, indent=2) + "\n",
            PAYLOAD_UNION_DATA_PATH.parents[1] / "expected" / "payloads" / "union_capabilities.txt",
        )


def test_union_native_witnesses(generated_model_cache: dict[str, Any]) -> None:
    """Missing optional branch fields remain valid while actual object type violations fail."""
    from jsonschema import ValidationError as SourceValidationError

    witnesses = json.loads(
        PAYLOAD_UNION_DATA_PATH.with_name("union_capabilities_values.json").read_text(encoding="utf-8")
    )["permissive_union"]
    case = PAYLOAD_UNION_CASES["permissive_union"]
    adapter = load_generated_payload_adapter(case, generated_model_cache)
    with assert_inputs_not_mutated({"schema": case.source_schema, "witnesses": witnesses}):
        for payload in witnesses["valid"]:
            validate_with_source_schema(case, payload)
            adapter.validate_python(payload)
        for payload in witnesses["invalid"]:
            with pytest.raises(ValidationError):
                adapter.validate_python(payload)
            with pytest.raises(SourceValidationError):
                validate_with_source_schema(case, payload)


def test_allof_payload_strategy_classification() -> None:
    """Keep impossible intersections out of acceptance generation while retaining valid controls."""
    names = json.loads((DATA_PATH / "payloads/allof_payload_strategy_cases.json").read_text())
    payloads = json.loads((DATA_PATH / "payloads/allof_payload_strategy_values.json").read_text())
    results = {}
    for name in names:
        schema = json.loads((JSON_SCHEMA_DATA_PATH / "numeric_allof_types" / f"{name}.json").read_text())
        validator = Draft7Validator(schema)
        with assert_inputs_not_mutated({"schema": schema}):
            results[name] = {
                "empty_strategy": from_schema(deepcopy(schema)).is_empty,
                "classification": _schema_exclusion_reason(schema),
                "source_acceptance": [validator.is_valid(payload) for payload in payloads],
            }
    assert_output(
        json.dumps(results, indent=2) + "\n", EXPECTED_MAIN_PATH / "allof_payload_strategy_classification.txt"
    )


def test_payload_generation_reports_real_schema_failure(tmp_path: Path) -> None:
    """Keep actual generation errors visible when checking expected warnings."""
    source = JSON_SCHEMA_DATA_PATH / "payload_generation_missing_reference.json"
    schema = json.loads(source.read_text())
    case = SchemaCase(
        id="jsonschema/payload_generation_missing_reference.json",
        input_file_type="jsonschema",
        source_path=source,
        source_schema=schema,
        codegen_schema=schema,
        temp_input_suffix=".json",
    )
    with pytest.raises(PayloadAdapterError) as caught:
        generate_payload_runtime(
            case, GeneratedModelCache({"base": tmp_path, "adapters": {}}), PayloadBackend.PYDANTIC_V2
        )
    assert_output(str(caught.value) + "\n", EXPECTED_MAIN_PATH / "payload_generation_missing_reference.txt")


RUNTIME_COMPATIBILITY_PATH = DATA_PATH / "payloads/runtime_compatibility"
RUNTIME_COMPATIBILITY_EXPECTED = EXPECTED_MAIN_PATH / "runtime_compatibility"


@pytest.mark.parametrize(
    ("case_id", "payloads"),
    json.loads((RUNTIME_COMPATIBILITY_PATH / "null_aliases.json").read_text()).items(),
)
def test_payload_runtime_null_aliases(tmp_path: Path, case_id: str, payloads: dict[str, Any]) -> None:
    """Load actual assignment-based null aliases and reuse their cached runtime."""
    backend = PayloadBackend.MSGSPEC
    case = replace(
        SCHEMA_CASE_BY_ID[case_id],
        backend_extra_args={backend: ("--target-python-version", "3.10")},
    )
    cache = GeneratedModelCache({"base": tmp_path, "adapters": {}})
    with assert_inputs_not_mutated({"schema": case.source_schema, "payloads": payloads}):
        validate_with_source_schema(case, payloads["valid"])
        runtime = generate_payload_runtime(case, cache, backend)
        result = {
            "value": runtime.validate_python(payloads["valid"]),
            "cached": generate_payload_runtime(case, cache, backend) is runtime,
            "acceptance_exclusion": backend_acceptance_exclusion_reason(case, backend),
        }
        for payload in payloads["invalid"]:
            with pytest.raises(JsonSchemaValidationError):
                validate_with_source_schema(case, payload)
            runtime.assert_rejects_python(payload)
        assert_output(json.dumps(result, indent=2) + "\n", RUNTIME_COMPATIBILITY_EXPECTED / "null_aliases.txt")


@pytest.mark.parametrize("backend", [PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS])
@pytest.mark.parametrize(
    ("case_id", "payload"),
    json.loads((RUNTIME_COMPATIBILITY_PATH / "union_cases.json").read_text()).items(),
)
def test_payload_runtime_union_options(
    backend: PayloadBackend, case_id: str, payload: Any, generated_model_cache: dict[str, Any]
) -> None:
    """Keep valid array branches in the configured Pydantic model and dataclass matrix."""
    case = SCHEMA_CASE_BY_ID[case_id]
    with assert_inputs_not_mutated({"schema": case.source_schema, "payload": payload}):
        validate_with_source_schema(case, payload)
        runtime = generate_payload_runtime(case, generated_model_cache, backend)
        runtime.validate_python(payload)
        assert_output(
            json.dumps({"options": case.backend_extra_args[backend], "validated": payload}, indent=2) + "\n",
            RUNTIME_COMPATIBILITY_EXPECTED / f"union_{'field' if case_id.endswith('field.json') else 'root'}.txt",
        )


def test_payload_runtime_msgspec_gaps(generated_model_cache: dict[str, Any]) -> None:
    """Prove classified msgspec gaps with source-valid payloads and real generated modules."""
    payloads = json.loads((RUNTIME_COMPATIBILITY_PATH / "msgspec_gaps.json").read_text())
    results = {}
    for case_id, fixture in payloads.items():
        case = SCHEMA_CASE_BY_ID[case_id]
        payload = fixture["valid"]
        with assert_inputs_not_mutated({"schema": case.source_schema, "payload": payload}):
            validate_with_source_schema(case, payload)
            runtime = generate_payload_runtime(case, generated_model_cache, PayloadBackend.MSGSPEC)
            with pytest.raises(TypeError, match=fixture["error"]) as caught:
                runtime.validate_python(payload)
            results[case_id] = {
                "acceptance": backend_acceptance_exclusion_reason(case, PayloadBackend.MSGSPEC),
                "rejection": backend_rejection_exclusion_reason(case, PayloadBackend.MSGSPEC),
                "error": type(caught.value).__name__,
            }
    assert_output(json.dumps(results, indent=2) + "\n", RUNTIME_COMPATIBILITY_EXPECTED / "msgspec_gaps.txt")


def test_payload_runtime_legacy_alias_policy(tmp_path: Path) -> None:
    """Gate unresolved assignment aliases on generated syntax and verify the supported syntax."""
    case = SCHEMA_CASE_BY_ID["jsonschema/compound_property_names/ref_then_any.json"]
    backend = PayloadBackend.MSGSPEC
    results = {
        target.value: _msgspec_type_statement_exclusion_reason(case, target)
        for target in (PythonVersion.PY_311, PythonVersion.PY_312)
    }
    runtime = generate_payload_runtime(case, GeneratedModelCache({"base": tmp_path, "adapters": {}}), backend)
    payload = json.loads((RUNTIME_COMPATIBILITY_PATH / "legacy_alias.json").read_text())
    validate_with_source_schema(case, payload)
    if PythonVersion(PAYLOAD_TARGET_PYTHON_VERSION).has_type_statement:
        runtime.validate_python(payload)
    else:
        with pytest.raises(TypeError, match="ForwardRef"):
            runtime.validate_python(payload)
    assert_output(json.dumps(results, indent=2) + "\n", RUNTIME_COMPATIBILITY_EXPECTED / "legacy_alias.txt")


def test_payload_runtime_version_policies() -> None:
    """Retain modern Pydantic coverage while classifying old-runtime and bare-alias limits."""
    results = {}
    for case_id in json.loads((RUNTIME_COMPATIBILITY_PATH / "version_cases.json").read_text()):
        case = SCHEMA_CASE_BY_ID[case_id]
        results[case_id] = {
            version: {
                "acceptance": _pydantic_v2_legacy_runtime_exclusion_reason(
                    case, PayloadBackend.PYDANTIC_V2, Version(version)
                ),
                "round_trip": _pydantic_v2_legacy_runtime_exclusion_reason(
                    case,
                    PayloadBackend.PYDANTIC_V2,
                    Version(version),
                    PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS,
                ),
            }
            for version in ("2.0.3", "2.5.0")
        }
        results[case_id]["dataclass"] = backend_acceptance_exclusion_reason(case, PayloadBackend.PYDANTIC_V2_DATACLASS)
    assert_output(json.dumps(results, indent=2) + "\n", RUNTIME_COMPATIBILITY_EXPECTED / "versions.txt")


@pytest.mark.parametrize(
    ("case_id", "payload"),
    json.loads((RUNTIME_COMPATIBILITY_PATH / "version_cases.json").read_text()).items(),
)
def test_payload_runtime_pydantic_version_witnesses(
    case_id: str, payload: Any, generated_model_cache: dict[str, Any]
) -> None:
    """Verify old-runtime failures and restored round trips with actual Pydantic adapters."""
    case = SCHEMA_CASE_BY_ID[case_id]
    validate_with_source_schema(case, payload)
    if _pydantic_v2_legacy_runtime_exclusion_reason(case, PayloadBackend.PYDANTIC_V2):
        with pytest.raises(PayloadAdapterError, match="look-around"):
            generate_payload_runtime(case, generated_model_cache, PayloadBackend.PYDANTIC_V2)
        return
    adapter = load_generated_payload_adapter(case, generated_model_cache)
    validated = adapter.validate_python(payload)
    if _pydantic_v2_legacy_runtime_exclusion_reason(
        case,
        PayloadBackend.PYDANTIC_V2,
        exclusion_groups=PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUSION_GROUPS,
    ):
        with pytest.raises(UserWarning, match="serializer warnings"):
            adapter.dump_python(validated, mode="json")
        return
    validate_with_source_schema(case, adapter.dump_python(validated, mode="json"))


def test_payload_runtime_dataclass_lookaround_gap(tmp_path: Path) -> None:
    """A bare dataclass alias has no regex-engine config even on modern Pydantic."""
    case = SCHEMA_CASE_BY_ID["jsonschema/root_alias_constraints/lookaround.json"]
    with pytest.raises(PayloadAdapterError, match="look-around"):
        generate_payload_runtime(
            case, GeneratedModelCache({"base": tmp_path, "adapters": {}}), PayloadBackend.PYDANTIC_V2_DATACLASS
        )


@pytest.mark.parametrize(
    ("name", "fixture"),
    json.loads((RUNTIME_COMPATIBILITY_PATH / "alias_fallback.json").read_text()).items(),
)
def test_payload_runtime_alias_fallback(tmp_path: Path, name: str, fixture: dict[str, Any]) -> None:
    """Keep single-class fallback and missing/ambiguous diagnostics after recognizing null aliases."""
    backend = PayloadBackend.MSGSPEC
    case = replace(
        SCHEMA_CASE_BY_ID[fixture["schema"]],
        backend_extra_args={backend: ("--class-name", "Alternate", "--target-python-version", "3.10")},
    )
    cache = GeneratedModelCache({"base": tmp_path, "adapters": {}})
    if name == "single":
        runtime = generate_payload_runtime(case, cache, backend)
        validate_with_source_schema(case, fixture["payload"])
        runtime.validate_python(fixture["payload"])
        result = runtime.payload_type.__name__
    else:
        with pytest.raises(PayloadAdapterError) as caught:
            generate_payload_runtime(case, cache, backend)
        result = str(caught.value)
    assert_output(result + "\n", RUNTIME_COMPATIBILITY_EXPECTED / f"fallback_{name}.txt")
