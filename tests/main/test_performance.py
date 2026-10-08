"""Performance tests for large schemas and complex code generation scenarios.

These tests are designed to measure code generation performance with:
- Large number of models (500+)
- Duplicate/similar names requiring disambiguation
- Complex reference chains and circular references
- Deeply nested structures
- Multiple file inputs
- Large OpenAPI specifications

Tests are marked with @pytest.mark.perf for exclusion from regular CI runs.
Core tests are also marked with @pytest.mark.benchmark for CodSpeed integration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from itertools import islice, permutations
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import (
    DataModelType,
    Formatter,
    GenerateConfig,
    InputFileType,
    ModuleSplitMode,
    YamlValue,
    generate,
    load_pyproject_config,
)
from datamodel_code_generator.model.msgspec import DataModelField as MsgspecDataModelField
from datamodel_code_generator.model.msgspec import DataTypeManager as MsgspecDataTypeManager
from datamodel_code_generator.model.msgspec import Struct as MsgspecStruct
from datamodel_code_generator.model.pydantic_v2.base_model import BaseModel as PydanticV2BaseModel
from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField as PydanticV2DataModelField
from datamodel_code_generator.model.pydantic_v2.base_model import _construct_parser_simple_field
from datamodel_code_generator.reference import PydanticFieldNameResolver, Reference
from datamodel_code_generator.types import DataType
from tests.conftest import TARGET_PYTHON
from tests.main.conftest import _generated_model

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

PERFORMANCE_DATA_PATH: Path = Path(__file__).parent.parent / "data" / "performance"
EXPECTED_STARTUP_MEASUREMENT_CASES = {
    "import-package",
    "import-arguments",
    "import-main",
    "import-config",
    "cli-version",
    "cli-help",
    "cli-schema-generation",
    "cli-schema-structured-output",
}
needs_target_python = pytest.mark.skipif(
    sys.version_info < TARGET_PYTHON, reason="The server and client targets need Python 3.11 or later"
)


@pytest.fixture(scope="module")
def pyproject_config_directory(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Prepare project settings outside the configuration-loading benchmark."""
    project = tmp_path_factory.mktemp("pyproject-config-benchmark")
    source = PERFORMANCE_DATA_PATH.parent / "pyproject_config" / "profiles.toml"
    shutil.copyfile(source, project / "pyproject.toml")
    return project


@pytest.fixture(scope="module", params=["mapping", "config"])
def pyproject_config_overrides(request: pytest.FixtureRequest) -> Any:
    """Prepare both accepted override representations outside measured calls."""
    overrides = {"output": None, "class_name": "Benchmark"}
    return GenerateConfig(**overrides) if request.param == "config" else overrides


@pytest.mark.perf
@pytest.mark.benchmark
@pytest.mark.parametrize("profile", [None, "multiple"])
def test_perf_load_pyproject_config(
    pyproject_config_directory: Path, pyproject_config_overrides: Any, profile: str | None
) -> None:
    """Measure shared project discovery, profile resolution, and override validation."""
    load_pyproject_config(pyproject_config_directory, profile, overrides=pyproject_config_overrides)


@pytest.fixture(scope="module")
def openapi_large_yaml_text() -> str:
    """Read the YAML constructor benchmark input outside the measured call."""
    return (PERFORMANCE_DATA_PATH / "openapi_large.yaml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def simple_pydantic_v2_data_types() -> list[DataType]:
    """Prepare normalized types outside the field-construction benchmark."""
    return [DataType(type="str") for _ in range(5000)]


@pytest.fixture(scope="module")
def plain_pydantic_v2_fields(simple_pydantic_v2_data_types: list[DataType]) -> list[PydanticV2DataModelField]:
    """Prepare parser-style plain Pydantic v2 fields outside render-plan timing."""
    fields = [
        _construct_parser_simple_field(name=f"field_{index}", data_type=data_type, required=True)
        for index, data_type in enumerate(simple_pydantic_v2_data_types)
    ]
    PydanticV2BaseModel(
        reference=Reference(path="PydanticFieldRenderPlanPerformance", name="PydanticFieldRenderPlanPerformance"),
        fields=fields,
    )
    return fields


@pytest.fixture(scope="module")
def ordinary_pydantic_field_names() -> tuple[str, ...]:
    """Prepare resolver inputs outside the name-resolution benchmark."""
    return tuple(f"field_{index}" for index in range(5000))


@pytest.fixture(scope="module")
def simple_msgspec_unset_fields() -> list[MsgspecDataModelField]:
    """Prepare parser-style msgspec fields outside the measured rendering call."""
    data_type = MsgspecDataTypeManager().data_type
    fields = [
        MsgspecDataModelField(name=f"field_{index}", data_type=data_type(type="str"), required=False)
        for index in range(5000)
    ]
    MsgspecStruct(
        reference=Reference(path="MsgspecPerformance", name="MsgspecPerformance"),
        fields=fields,
    )
    return fields


@pytest.fixture(scope="module")
def false_reference_performance_schema() -> dict[str, YamlValue]:
    """Prepare repeated local false references outside CodSpeed's measured call."""
    field_count = 500
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "FalseReferencePerformance",
        "type": "object",
        "properties": {
            f"value_{index}": {
                "anyOf": [
                    {"$ref": "#/$defs/Never"},
                    {"$ref": "#/$defs/Value"},
                ]
            }
            for index in range(field_count)
        },
        "$defs": {
            "Never": False,
            "Value": {"type": "string"},
        },
    }


@pytest.fixture(scope="module")
def local_reference_file_cache_performance_input(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Prepare repeated local file fragments outside CodSpeed's measured call."""
    schema_directory = tmp_path_factory.mktemp("local-reference-file-cache")
    (schema_directory / "shared.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "First": {"type": "object", "properties": {"name": {"type": "string"}}},
                "Second": {"type": "object", "properties": {"count": {"type": "integer"}}},
            },
        }),
        encoding="utf-8",
    )
    (schema_directory / "root.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "LocalReferenceFileCachePerformance",
            "type": "object",
            "properties": {
                f"value_{index}": {"$ref": f"shared.json#/$defs/{'First' if index % 2 else 'Second'}"}
                for index in range(500)
            },
            "required": [f"value_{index}" for index in range(500)],
        }),
        encoding="utf-8",
    )
    return schema_directory / "root.json"


@pytest.fixture(scope="module")
def ordinary_array_constraint_performance_schema() -> dict[str, object]:
    """Prepare ordinary arrays without derived item constraints outside CodSpeed's measured call."""
    field_count = 4000
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "OrdinaryArrayConstraintPerformance",
        "type": "object",
        "properties": {f"value_{index}": {"type": "array"} for index in range(field_count)},
    }


@pytest.fixture(scope="module")
def unique_items_performance_schema() -> dict[str, object]:
    """Prepare collapsed uniqueItems references outside the measured call."""
    field_count = 500
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "UniqueItemsPerformance",
        "type": "object",
        "properties": {f"value_{index}": {"$ref": "#/$defs/UniqueValues"} for index in range(field_count)},
        "$defs": {
            "UniqueValues": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "integer"},
            }
        },
    }


@pytest.fixture(scope="module")
def scalar_root_performance_schema() -> dict[str, object]:
    """Prepare unshared scalar root references outside the measured call."""
    field_count = 500
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ScalarRootPerformance",
        "type": "object",
        "properties": {f"value_{index}": {"$ref": "#/$defs/Value"} for index in range(field_count)},
        "$defs": {"Value": {"type": "string"}},
    }


@pytest.fixture(scope="module")
def unique_items_runtime_model(tmp_path_factory: pytest.TempPathFactory) -> Generator[Any, None, None]:
    """Generate and import one validated RootModel outside the runtime benchmarks."""
    output_path = tmp_path_factory.mktemp("unique-items-runtime") / "model.py"
    generate(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "UniqueItemsRuntime",
            "type": "array",
            "uniqueItems": True,
            "items": {},
        },
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[],
        generate_schema_validators=True,
    )
    with _generated_model(output_path, "unique_items_runtime", "UniqueItemsRuntime") as model:
        yield model


@pytest.fixture(scope="module")
def unique_items_nested_model(tmp_path_factory: pytest.TempPathFactory) -> Generator[Any, None, None]:
    """Generate and import nested models outside the ownership benchmark."""
    output_path = tmp_path_factory.mktemp("unique-items-nested") / "model.py"
    generate(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Parent",
            "type": "object",
            "properties": {
                "child": {
                    "type": "object",
                    "properties": {
                        "values": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"type": "integer"},
                        }
                    },
                    "required": ["values"],
                }
            },
            "required": ["child"],
        },
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[],
        generate_schema_validators=True,
    )
    with _generated_model(output_path, "unique_items_nested", "Parent") as model:
        yield model


@pytest.fixture(scope="module")
def pattern_properties_runtime_model(tmp_path_factory: pytest.TempPathFactory) -> Generator[Any, None, None]:
    """Generate and import pattern/additional dispatch outside the runtime benchmark."""
    output_path = tmp_path_factory.mktemp("pattern-properties-runtime") / "model.py"
    generate(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "PatternPropertiesRuntime",
            "type": "object",
            "patternProperties": {"^pattern-": {"type": "integer"}},
            "additionalProperties": {"type": "string"},
        },
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[],
        generate_schema_validators=True,
    )
    with _generated_model(output_path, "pattern_properties_runtime", "PatternPropertiesRuntime") as model:
        yield model


@pytest.fixture(scope="module")
def unique_items_scalar_payload() -> list[int]:
    """Prepare 10,000 unique primitive values outside the measured runtime call."""
    return list(range(10_000))


@pytest.fixture(scope="module")
def unique_items_nested_object_payload() -> list[dict[str, object]]:
    """Prepare 1,000 unique nested objects outside the measured runtime call."""
    return [
        {
            "id": index,
            "label": f"item-{index}",
            "metadata": {"rank": index, "values": [index, index + 1]},
        }
        for index in range(1_000)
    ]


@pytest.fixture(scope="module")
def unique_items_nested_model_payload(unique_items_scalar_payload: list[int]) -> dict[str, object]:
    """Reuse the prepared scalar values in one nested child payload."""
    return {"child": {"values": unique_items_scalar_payload}}


@pytest.fixture(scope="module")
def unique_items_permuted_object_payload() -> list[dict[str, int]]:
    """Prepare key-associated permutations that expose weak object fingerprints."""
    keys = tuple(str(index) for index in range(8))
    return [dict(zip(keys, values, strict=True)) for values in islice(permutations(range(8)), 10_000)]


@pytest.fixture(scope="module")
def pattern_properties_runtime_payload() -> dict[str, object]:
    """Prepare many pattern and additional keys outside the measured runtime call."""
    return {
        **{f"pattern-{index}": index for index in range(1_000)},
        **{f"extra-{index}": str(index) for index in range(1_000)},
    }


def _build_inherited_required_performance_schema(
    *,
    base_first: bool,
    partial_override: bool,
) -> dict[str, object]:
    """Build a reusable OpenAPI stress case outside the measured generation call."""
    field_names = [f"field_{index:02d}" for index in range(25)]
    properties: dict[str, object] = {}
    for index, field_name in enumerate(field_names):
        match index % 5:
            case 0:
                field_schema: dict[str, object] = {"$ref": "#/components/schemas/PerfItem"}
            case 1:
                field_schema = {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/PerfItem"},
                }
            case 2:
                field_schema = {
                    "type": "object",
                    "required": ["code"],
                    "properties": {"code": {"type": "string", "minLength": 1}},
                }
            case 3:
                field_schema = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["code"],
                        "properties": {"code": {"type": "string", "minLength": 1}},
                    },
                }
            case _:
                field_schema = {
                    "anyOf": [
                        {"$ref": "#/components/schemas/PerfItem"},
                        {"type": "string"},
                    ]
                }
        properties[field_name] = field_schema

    base_schema = {
        "type": "object",
        "properties": properties,
    }
    derived_schemas: dict[str, object] = {}
    for derived_index in range(80):
        derived_schema: dict[str, object] = {
            "allOf": [{"$ref": "#/components/schemas/PerfBase"}],
            "required": field_names,
        }
        if partial_override:
            derived_schema["properties"] = {
                field_name: (
                    {}
                    if (derived_index + field_index) % 2 == 0
                    else {"description": "Partial inherited performance annotation"}
                )
                for field_index, field_name in enumerate(field_names)
            }
        derived_schemas[f"PerfDerived{derived_index:02d}"] = derived_schema
    item_schema = {
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string", "minLength": 1}},
    }
    schemas = (
        {"PerfItem": item_schema, "PerfBase": base_schema, **derived_schemas}
        if base_first
        else {**derived_schemas, "PerfBase": base_schema, "PerfItem": item_schema}
    )
    return {
        "openapi": "3.0.0",
        "info": {"title": "Inherited required performance", "version": "1.0"},
        "paths": {},
        "components": {"schemas": schemas},
    }


@pytest.fixture(scope="module")
def inherited_required_performance_schemas() -> dict[tuple[str, bool], dict[str, object]]:
    """Build inherited schemas during fixture setup, outside CodSpeed's measured call."""
    return {
        (component_order, partial_override): _build_inherited_required_performance_schema(
            base_first=component_order == "base-first",
            partial_override=partial_override,
        )
        for component_order in ("base-first", "derived-first")
        for partial_override in (False, True)
    }


def _run_python(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.perf
@pytest.mark.benchmark
@pytest.mark.parametrize(
    ("component_order", "partial_override"),
    [
        pytest.param("base-first", False, id="required-base-first"),
        pytest.param("derived-first", False, id="required-derived-first"),
        pytest.param("base-first", True, id="partial-base-first"),
        pytest.param("derived-first", True, id="partial-derived-first"),
    ],
)
def test_perf_inherited_required_fields(
    component_order: str,
    inherited_required_performance_schemas: dict[tuple[str, bool], dict[str, object]],
    *,
    partial_override: bool,
) -> None:
    """Guard direct/deferred required inheritance and partial override performance."""
    result = generate(
        inherited_required_performance_schemas[component_order, partial_override],
        input_file_type=InputFileType.OpenAPI,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[],
        disable_timestamp=True,
    )
    assert isinstance(result, str)
    assert "class PerfDerived79(PerfBase):" in result
    assert "field_24: PerfItem" in result


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_false_reference_validation(
    false_reference_performance_schema: dict[str, YamlValue],
) -> None:
    """Track literal-false local reference handling without formatter work."""
    result = generate(
        false_reference_performance_schema,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[],
        disable_timestamp=True,
    )
    assert isinstance(result, str)
    assert "class FalseReferencePerformance(BaseModel):" in result
    assert result.endswith("    value_499: Value | None = None")


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_local_reference_file_resolution(local_reference_file_cache_performance_input: Path) -> None:
    """Track repeated local file fragment resolution without formatter work."""
    result = generate(
        local_reference_file_cache_performance_input,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[],
        disable_timestamp=True,
    )
    assert isinstance(result, str)
    assert "class LocalReferenceFileCachePerformance(BaseModel):" in result
    assert result.endswith("    value_499: First")


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_ordinary_array_constraint_generation(
    ordinary_array_constraint_performance_schema: dict[str, object],
) -> None:
    """Track ordinary arrays that do not need derived item constraints."""
    result = generate(
        ordinary_array_constraint_performance_schema,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[],
        disable_timestamp=True,
    )

    assert isinstance(result, str)
    assert "class OrdinaryArrayConstraintPerformance(BaseModel):" in result
    assert result.endswith("    value_3999: list[Any] | None = None")


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_unique_items_schema_validators(
    unique_items_performance_schema: dict[str, object],
) -> None:
    """Track collapsed uniqueItems reference generation without formatter work."""
    result = generate(
        unique_items_performance_schema,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        collapse_root_models=True,
        disable_timestamp=True,
        formatters=[],
        generate_schema_validators=True,
    )
    assert isinstance(result, str)
    assert "class UniqueItemsPerformance(_JsonSchemaRuntimeValidationBase):" in result
    assert "__json_schema_unique_items__" in result


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_unique_items_collapsed_builtin(
    unique_items_performance_schema: dict[str, object],
) -> None:
    """Track collapsed root-model replacement with the builtin formatter."""
    result = generate(
        unique_items_performance_schema,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        collapse_root_models=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    assert isinstance(result, str)
    assert "class UniqueItemsPerformance(BaseModel):" in result
    assert result.endswith("    value_499: list[int] | None = None")


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_scalar_root_collapsed_builtin(
    scalar_root_performance_schema: dict[str, object],
) -> None:
    """Track incremental root-reference counts with the builtin formatter."""
    result = generate(
        scalar_root_performance_schema,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        collapse_root_models=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    assert isinstance(result, str)
    assert "class ScalarRootPerformance(BaseModel):" in result
    assert result.endswith("    value_499: str | None = None")


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_unique_items_runtime_scalar_validation(
    unique_items_runtime_model: Any,
    unique_items_scalar_payload: list[int],
) -> None:
    """Benchmark validation of 10,000 unique scalar JSON values only."""
    unique_items_runtime_model.model_validate(unique_items_scalar_payload)


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_unique_items_runtime_nested_object_validation(
    unique_items_runtime_model: Any,
    unique_items_nested_object_payload: list[dict[str, object]],
) -> None:
    """Benchmark validation of 1,000 unique nested JSON objects only."""
    unique_items_runtime_model.model_validate(unique_items_nested_object_payload)


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_unique_items_nested_model_validation(
    unique_items_nested_model: Any,
    unique_items_nested_model_payload: dict[str, object],
) -> None:
    """Benchmark one child-owned validation without repeating it on the parent."""
    unique_items_nested_model.model_validate(unique_items_nested_model_payload)


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_unique_items_permuted_object_validation(
    unique_items_runtime_model: Any,
    unique_items_permuted_object_payload: list[dict[str, int]],
) -> None:
    """Benchmark distinct objects whose key/value associations are permutations."""
    unique_items_runtime_model.model_validate(unique_items_permuted_object_payload)


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_pattern_properties_adapter_reuse(
    pattern_properties_runtime_model: Any,
    pattern_properties_runtime_payload: dict[str, object],
) -> None:
    """Benchmark reused adapters across many pattern and additional keys."""
    pattern_properties_runtime_model.model_validate(pattern_properties_runtime_payload)


@pytest.mark.perf
@pytest.mark.parametrize(
    ("args", "expected_text"),
    [
        (["-m", "datamodel_code_generator.__main__", "--version"], "datamodel-codegen "),
        (["-m", "datamodel_code_generator.__main__", "--help"], "usage:"),
        (["-m", "datamodel_code_generator.__main__", "--list-deprecations"], "Since"),
    ],
)
def test_perf_cli_fast_path_subprocesses(args: list[str], expected_text: str) -> None:
    """Performance smoke: CLI utility paths work in a fresh process."""
    result = _run_python(args)
    assert expected_text in result.stdout


@pytest.mark.perf
@pytest.mark.parametrize(
    "schema_option",
    [
        "--output-format-json-schema=generation",
        "--output-format-json-schema=structured-output",
    ],
)
def test_perf_cli_schema_fast_path_subprocesses(schema_option: str) -> None:
    """Performance smoke: CLI schema utility paths return valid JSON in a fresh process."""
    result = _run_python(["-m", "datamodel_code_generator.__main__", schema_option])
    schema = json.loads(result.stdout)
    assert "$schema" in schema
    assert "properties" in schema or "$defs" in schema


@pytest.mark.perf
def test_perf_startup_measurement_script() -> None:
    """Performance smoke: startup measurement script emits comparable metrics."""
    result = _run_python(["scripts/measure_startup.py", "--runs", "1", "--json"])
    payload = json.loads(result.stdout)
    cases = payload["cases"]
    case_names = {case["name"] for case in cases}
    assert case_names >= EXPECTED_STARTUP_MEASUREMENT_CASES
    for case in cases:
        assert case["runs"] == 1
        assert case["median_ms"] >= 0
        assert case["importtime_top"]


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_simple_pydantic_v2_field_construction(simple_pydantic_v2_data_types: list[DataType]) -> None:
    """Benchmark parser-owned construction without a timing threshold."""
    fields = [
        _construct_parser_simple_field(name=f"field_{index}", data_type=data_type)
        for index, data_type in enumerate(simple_pydantic_v2_data_types)
    ]
    assert len(fields) == len(simple_pydantic_v2_data_types)
    assert fields[-1].name == "field_4999"


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_empty_pydantic_v2_field_render_plans(plain_pydantic_v2_fields: list[PydanticV2DataModelField]) -> None:
    """Benchmark shared empty Field() plans for parser-created Pydantic v2 fields."""
    rendered_fields = [str(field) for field in plain_pydantic_v2_fields]

    assert len(rendered_fields) == len(plain_pydantic_v2_fields)
    assert not rendered_fields[-1]


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_pydantic_field_name_resolution(ordinary_pydantic_field_names: tuple[str, ...]) -> None:
    """Benchmark ordinary Pydantic field-name resolution without parser work."""
    resolver = PydanticFieldNameResolver()
    resolved_name = ""
    for field_name in ordinary_pydantic_field_names:
        resolved_name = resolver.get_valid_name(field_name)

    assert resolved_name == "field_4999"


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_simple_msgspec_unset_field_rendering(
    simple_msgspec_unset_fields: list[MsgspecDataModelField],
) -> None:
    """Benchmark simple unset annotations and imports without a timing threshold."""
    rendered = [(field.type_hint, field.imports) for field in simple_msgspec_unset_fields]

    assert len(rendered) == len(simple_msgspec_unset_fields)
    assert rendered[-1][0] == "Union[str, UnsetType]"
    assert tuple(import_.import_ for import_ in rendered[-1][1]) == ("UnsetType", "Union", "UNSET")


@pytest.mark.perf
def test_perf_large_models(tmp_path: Path) -> None:
    """Performance test: Generate 500 models from a single schema.

    This tests the parser's ability to handle a large number of model definitions
    and the code generator's performance with many models.
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated all 500 models
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_large_models_single_module_stdout() -> None:
    """Benchmark the 500-model modular stdout path without formatting overhead."""
    result = generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        module_split_mode=ModuleSplitMode.Single,
        output=None,
        formatters=[],
        disable_timestamp=True,
    )

    assert isinstance(result, dict)
    assert len(result) == 502
    assert ("model499.py",) in result


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_large_models_pydantic_v2(tmp_path: Path) -> None:
    """Performance test: Generate 500 Pydantic v2 models."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_large_models_pydantic_v2_builtin(tmp_path: Path) -> None:
    """Performance test: Generate 500 Pydantic v2 models with the built-in formatter."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_large_models_pydantic_v2_noformat(tmp_path: Path) -> None:
    """Performance test: Generate 500 Pydantic v2 models without formatting."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[],
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_large_models_pydantic_v2_builtin_double_quotes(tmp_path: Path) -> None:
    """Benchmark built-in string normalization when generated output has no quote candidates."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        use_double_quotes=True,
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.parametrize(
    ("formatter_case", "formatters"),
    [
        ("default", None),
        ("none", []),
        ("builtin", [Formatter.BUILTIN]),
        ("ruff", [Formatter.RUFF_FORMAT, Formatter.RUFF_CHECK]),
    ],
)
def test_perf_formatter_matrix_large_models_pydantic_v2(
    tmp_path: Path,
    formatter_case: str,
    formatters: list[Formatter] | None,
) -> None:
    """Performance test: compare formatter cost against no-formatter parser/render cost."""
    if formatter_case == "ruff" and shutil.which("ruff") is None:
        pytest.skip("ruff executable is not available")

    output_file = tmp_path / "output.py"
    options = {
        "input_file_type": InputFileType.JsonSchema,
        "output": output_file,
        "output_model_type": DataModelType.PydanticV2BaseModel,
    }
    if formatters is not None:
        options["formatters"] = formatters

    generate(PERFORMANCE_DATA_PATH / "large_models.json", **options)
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
def test_perf_large_models_dataclass(tmp_path: Path) -> None:
    """Performance test: Generate 500 dataclass models."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.DataclassesDataclass,
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.benchmark
@pytest.mark.parametrize(
    "use_total_false_for_typed_dict",
    [
        pytest.param(False, id="legacy-requiredness"),
        pytest.param(True, id="total-false-requiredness"),
    ],
)
def test_perf_large_models_typed_dict(tmp_path: Path, *, use_total_false_for_typed_dict: bool) -> None:
    """Performance test: Generate 500 TypedDict models with both requiredness strategies."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.TypingTypedDict,
        use_total_false_for_typed_dict=use_total_false_for_typed_dict,
        formatters=[],
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500
    assert ("total=False" in content) is use_total_false_for_typed_dict


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_duplicate_names(tmp_path: Path) -> None:
    """Performance test: Handle 300 models with duplicate/similar names.

    This tests the name disambiguation logic when many models have similar names
    requiring prefixes or suffixes to avoid collisions.
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "duplicate_names.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated Container models
    assert content.count("class Container") >= 100


@pytest.mark.perf
def test_perf_duplicate_names_multiple_files(tmp_path: Path) -> None:
    """Performance test: Handle duplicate names with multiple file output."""
    output_dir = tmp_path / "models"
    generate(
        input_=PERFORMANCE_DATA_PATH / "duplicate_names.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_dir,
    )
    # Check output directory was created
    assert output_dir.exists()


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_complex_refs(tmp_path: Path) -> None:
    """Performance test: Handle 200 models with complex reference chains.

    This tests the reference resolution logic with:
    - Circular references
    - Deep reference chains
    - Self-references
    - Cross-references between models
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "complex_refs.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated Node models
    assert content.count("class Node") >= 200


@pytest.mark.perf
def test_perf_complex_refs_collapse_root(tmp_path: Path) -> None:
    """Performance test: Complex refs with collapse-root-models enabled."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "complex_refs.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        collapse_root_models=True,
    )
    content = output_file.read_text()
    assert "class Node" in content


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_deep_nested(tmp_path: Path) -> None:
    """Performance test: Handle deeply nested structures (50 levels deep).

    This tests:
    - Deep nesting resolution
    - Wide models with many fields
    - Combined deep and wide structures
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "deep_nested.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated Level and WideModel classes
    assert content.count("class Level") >= 50
    assert content.count("class WideModel") >= 100


@pytest.mark.perf
def test_perf_deep_nested_use_annotated(tmp_path: Path) -> None:
    """Performance test: Deep nested with use-annotated and field-constraints enabled."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "deep_nested.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        use_annotated=True,
        field_constraints=True,
    )
    content = output_file.read_text()
    assert "class Level" in content


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_multiple_files_input(tmp_path: Path) -> None:
    """Performance test: Process 50 schema files (500 models total).

    This tests:
    - Directory input handling
    - Multiple file parsing
    - Cross-file model coordination
    """
    output_dir = tmp_path / "models"
    generate(
        input_=PERFORMANCE_DATA_PATH / "multiple_files",
        input_file_type=InputFileType.JsonSchema,
        output=output_dir,
    )
    # Verify output directory was created with models
    assert output_dir.exists()
    py_files = list(output_dir.glob("**/*.py"))
    assert len(py_files) >= 1
    # Count total Module models across all files
    total_models = 0
    for py_file in py_files:
        content = py_file.read_text()
        total_models += content.count("class Module")
    assert total_models >= 500


@pytest.mark.perf
def test_perf_multiple_files_to_multiple_outputs(tmp_path: Path) -> None:
    """Performance test: Multiple input files to multiple output files."""
    output_dir = tmp_path / "models"
    generate(
        input_=PERFORMANCE_DATA_PATH / "multiple_files",
        input_file_type=InputFileType.JsonSchema,
        output=output_dir,
    )
    # Check multiple output files were created
    assert output_dir.exists()
    py_files = list(output_dir.glob("**/*.py"))
    assert len(py_files) >= 1


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_load_yaml_openapi_large(openapi_large_yaml_text: str) -> None:
    """Benchmark production YAML loading without including fixture I/O."""
    from datamodel_code_generator._source import load_yaml

    load_yaml(openapi_large_yaml_text)


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_openapi_large(tmp_path: Path) -> None:
    """Performance test: Large OpenAPI spec with 300 schemas and 900 endpoints.

    This tests:
    - OpenAPI parsing performance
    - Path/endpoint processing
    - Component schema handling
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "openapi_large.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated Entity models
    assert content.count("class Entity") >= 300


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_openapi_large_pydantic_v2_builtin(tmp_path: Path) -> None:
    """Track built-in formatting performance for a large OpenAPI document."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "openapi_large.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
    )
    content = output_file.read_text()
    assert content.count("class Entity") >= 300


@pytest.fixture(params=["client", "fastapi"])
def target_render(request: pytest.FixtureRequest, tmp_path: Path, target_size: str) -> Callable[[], Any]:
    """Prepare source and target configurations before benchmark timing."""
    from tests.data.python.target_performance import target_render_call

    return target_render_call(request.param, target_size, tmp_path)


@pytest.fixture(params=["small", "large"])
def target_size(request: pytest.FixtureRequest) -> str:
    """Select representative small and large OpenAPI inputs."""
    return request.param


@needs_target_python
@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_target_render(target_render: Callable[[], Any]) -> None:
    """Measure client and FastAPI model/package rendering without configuration or fixture setup."""
    target_render()


@pytest.fixture(params=["sync", "async"])
def client_calls(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Callable[[], Any]]:
    """Generate and import the shipped runtime, and prepare clients before timing their public calls."""
    from tests.data.python.target_performance import generated_client_calls

    with generated_client_calls(tmp_path) as calls:
        yield calls[request.param]


@needs_target_python
@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_client_calls(client_calls: Callable[[], Any]) -> None:
    """Measure JSON request encoding and typed response decoding in 200 sync or async public calls."""
    client_calls()


@pytest.mark.perf
def test_perf_openapi_large_strict_types(tmp_path: Path) -> None:
    """Performance test: Large OpenAPI with strict types enabled."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "openapi_large.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        strict_types=[
            "str",
            "int",
            "float",
            "bool",
        ],
    )
    content = output_file.read_text()
    assert "class Entity" in content


@pytest.mark.perf
def test_perf_openapi_large_field_constraints(tmp_path: Path) -> None:
    """Performance test: Large OpenAPI with field constraints enabled."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "openapi_large.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        field_constraints=True,
    )
    content = output_file.read_text()
    assert "class Entity" in content


@pytest.mark.perf
def test_perf_combined_large_models_with_formatting(tmp_path: Path) -> None:
    """Performance test: Large models with all formatting options.

    This tests the full pipeline including:
    - Parsing
    - Code generation
    - Import sorting
    - Code formatting
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "large_models.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        use_standard_collections=True,
        use_union_operator=True,
        use_annotated=True,
        field_constraints=True,
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 500


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_all_options_enabled(tmp_path: Path) -> None:
    """Performance test: Large schema with many options enabled.

    This stress tests the code generator with multiple features enabled simultaneously.
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "deep_nested.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        use_standard_collections=True,
        use_union_operator=True,
        use_annotated=True,
        field_constraints=True,
        collapse_root_models=True,
        use_field_description=True,
        use_default_kwarg=True,
    )
    content = output_file.read_text()
    assert "class" in content


# Real-world style schemas based on popular APIs


@pytest.mark.perf
def test_perf_kubernetes_style(tmp_path: Path) -> None:
    """Performance test: Kubernetes-style schema with 300+ definitions.

    Tests patterns common in Kubernetes APIs:
    - Deep metadata/spec/status patterns
    - Many resource types with similar structures
    - CRD-like custom resources
    - Complex allOf compositions
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "kubernetes_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated core K8s types
    assert "ObjectMeta" in content
    assert "class" in content


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_kubernetes_style_pydantic_v2(tmp_path: Path) -> None:
    """Performance test: Kubernetes-style schema with Pydantic v2."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "kubernetes_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert "ObjectMeta" in content


@pytest.mark.perf
def test_perf_stripe_style(tmp_path: Path) -> None:
    """Performance test: Stripe-style schema with 100+ definitions.

    Tests patterns common in Stripe API:
    - Event/webhook patterns
    - Expandable references (oneOf string or object)
    - Many payment-related types
    - Nested billing details
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "stripe_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated core Stripe types
    assert "class" in content


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_stripe_style_pydantic_v2(tmp_path: Path) -> None:
    """Performance test: Stripe-style schema with Pydantic v2."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "stripe_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert "class" in content


@pytest.mark.perf
def test_perf_aws_style_openapi(tmp_path: Path) -> None:
    """Performance test: AWS-style OpenAPI with 350+ schemas.

    Tests patterns common in AWS APIs:
    - Many resource types across services
    - Request/Response patterns
    - ARN references
    - Paginated list operations
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "aws_style.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated AWS resource types
    assert "class" in content


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_aws_style_openapi_pydantic_v2(tmp_path: Path) -> None:
    """Performance test: AWS-style OpenAPI with Pydantic v2."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "aws_style.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert "class" in content


@pytest.mark.perf
def test_perf_graphql_style(tmp_path: Path) -> None:
    """Performance test: GraphQL-style schema with 150+ definitions.

    Tests patterns common in GraphQL APIs converted to JSON Schema:
    - Connection/Edge patterns for pagination
    - Node interface pattern
    - Input/Payload types for mutations
    - Union types (oneOf)
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "graphql_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    # Verify we generated GraphQL patterns
    assert "PageInfo" in content
    assert "class" in content


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_graphql_style_pydantic_v2(tmp_path: Path) -> None:
    """Performance test: GraphQL-style schema with Pydantic v2."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "graphql_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert "PageInfo" in content


@pytest.mark.perf
def test_perf_graphql_style_typed_dict(tmp_path: Path) -> None:
    """Performance test: GraphQL-style schema with TypedDict."""
    output_file = tmp_path / "output.py"
    generate(
        input_=PERFORMANCE_DATA_PATH / "graphql_style.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.TypingTypedDict,
    )
    content = output_file.read_text()
    assert "PageInfo" in content


# =============================================================================
# Dynamically generated extreme-scale tests
# These tests generate schemas at runtime to avoid bloating the repository
# =============================================================================


@pytest.fixture
def massive_files_input(tmp_path: Path) -> Path:
    """Generate 200 separate schema files with cross-references."""
    import json

    input_dir = tmp_path / "massive_input"
    input_dir.mkdir()

    for i in range(200):
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": f"Schema{i:03d}",
            "definitions": {},
        }
        for j in range(20):
            model_name = f"Module{i:03d}Model{j:02d}"
            schema["definitions"][model_name] = {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "data": {"type": "object", "additionalProperties": True},
                },
                "required": ["id"],
            }
        schema["$ref"] = f"#/definitions/Module{i:03d}Model00"
        schema_file = input_dir / f"schema_{i:03d}.json"
        schema_file.write_text(json.dumps(schema))

    return input_dir


@pytest.fixture
def extreme_duplicate_names_schema(tmp_path: Path) -> Path:
    """Generate schema with 1000 models having highly similar/duplicate names."""
    import json

    schema: dict = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ExtremeDuplicateNames",
        "definitions": {},
    }

    prefixes = ["User", "Account", "Order", "Product", "Item", "Entity", "Record", "Data", "Info", "Detail"]
    suffixes = ["Request", "Response", "Input", "Output", "Model", "Schema", "Type", "DTO", "Payload", "Result"]

    idx = 0
    for prefix in prefixes:
        for suffix in suffixes:
            for variant in range(10):
                name = f"{prefix}{suffix}"
                if variant > 0:
                    name = f"{name}{variant}"
                schema["definitions"][f"def_{idx}_{name}"] = {
                    "title": name,
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "value": {"type": "string"},
                        "nested": {
                            "type": "object",
                            "properties": {
                                "inner_id": {"type": "integer"},
                            },
                        },
                    },
                }
                idx += 1

    schema["$ref"] = "#/definitions/def_0_UserRequest"

    schema_file = tmp_path / "extreme_duplicates.json"
    schema_file.write_text(json.dumps(schema))
    return schema_file


@pytest.mark.perf
@pytest.mark.benchmark
def test_perf_extreme_large_schema(tmp_path: Path, extreme_large_schema: Path) -> None:
    """Performance test: Extremely large schema with 2000 models.

    Tests the generator's ability to handle very large schemas that would be
    impractical to store in the repository.
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=extreme_large_schema,
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 2000


@pytest.mark.perf
def test_perf_extreme_large_schema_pydantic_v2(tmp_path: Path, extreme_large_schema: Path) -> None:
    """Performance test: Extremely large schema with Pydantic v2."""
    output_file = tmp_path / "output.py"
    generate(
        input_=extreme_large_schema,
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert content.count("class Model") >= 2000


@pytest.mark.perf
def test_perf_massive_files_input(tmp_path: Path, massive_files_input: Path) -> None:
    """Performance test: Process 200 separate schema files (4000 models total).

    Tests directory input handling with a very large number of files.
    """
    output_dir = tmp_path / "output"
    generate(
        input_=massive_files_input,
        input_file_type=InputFileType.JsonSchema,
        output=output_dir,
    )
    assert output_dir.exists()
    py_files = list(output_dir.glob("**/*.py"))
    assert len(py_files) >= 1


@pytest.mark.perf
def test_perf_massive_files_single_output(tmp_path: Path, massive_files_input: Path) -> None:
    """Performance test: Merge 200 schema files into output directory."""
    output_dir = tmp_path / "merged"
    generate(
        input_=massive_files_input,
        input_file_type=InputFileType.JsonSchema,
        output=output_dir,
    )
    assert output_dir.exists()
    py_files = list(output_dir.glob("**/*.py"))
    assert len(py_files) >= 1


@pytest.mark.perf
def test_perf_extreme_duplicate_names(tmp_path: Path, extreme_duplicate_names_schema: Path) -> None:
    """Performance test: Handle 1000 models with highly similar names.

    Tests the name disambiguation logic under extreme conditions.
    """
    output_file = tmp_path / "output.py"
    generate(
        input_=extreme_duplicate_names_schema,
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
    )
    content = output_file.read_text()
    assert content.count("class ") >= 1000


@pytest.mark.perf
def test_perf_extreme_duplicate_names_pydantic_v2(tmp_path: Path, extreme_duplicate_names_schema: Path) -> None:
    """Performance test: Extreme duplicate names with Pydantic v2."""
    output_file = tmp_path / "output.py"
    generate(
        input_=extreme_duplicate_names_schema,
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    content = output_file.read_text()
    assert content.count("class ") >= 1000
