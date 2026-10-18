"""Tests for Avro schema code generation."""

from __future__ import annotations

import json
import operator
from decimal import Inexact, Rounded, localcontext
from pathlib import Path
from typing import cast

import pytest

from datamodel_code_generator import (
    DataModelType,
    DatetimeClassType,
    DefaultValueType,
    DefaultValueTypeWarning,
    Error,
    InputFileType,
    generate,
    load_data,
)
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter, PythonVersion, is_supported_in_black
from datamodel_code_generator.parser.avro import convert_avro_schema_data
from tests.conftest import assert_inputs_not_mutated, assert_mutable_copy_is_isolated, assert_output
from tests.main.avro.conftest import assert_file_content
from tests.main.conftest import (
    AVRO_DATA_PATH,
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    CURRENT_PYTHON_VERSION,
    LEGACY_BLACK_SKIP,
    _generated_model,
    assert_generated_model_json_validation,
    get_current_version_args,
    run_generate_file_and_assert,
    run_main_and_assert,
)

_CURRENT_PYTHON_VERSION = PythonVersion(CURRENT_PYTHON_VERSION)
_SKIP_BLACK = pytest.mark.skipif(
    not is_supported_in_black(_CURRENT_PYTHON_VERSION),
    reason=f"Installed black doesn't support Python {CURRENT_PYTHON_VERSION}",
)


_EXPECTED_VERSION_DIR = (
    "py314"
    if _CURRENT_PYTHON_VERSION.has_native_deferred_annotations
    else f"py{CURRENT_PYTHON_VERSION.replace('.', '')}"
)


def _expected_file(expected_file: str) -> str:
    return f"{_EXPECTED_VERSION_DIR}/{expected_file}"


def _append_mutation_marker(value: object) -> None:
    cast("list[object]", value).append("__mutated__")


def test_convert_avro_schema_data_isolates_raw_lists() -> None:
    """Keep converted Avro metadata and enum lists independent from raw schema input."""
    raw_schema = cast("dict[str, object]", load_data((AVRO_DATA_PATH / "constructs.avsc").read_text(encoding="utf-8")))
    converted = convert_avro_schema_data(raw_schema)
    raw_id_field = next(
        field for field in cast("list[dict[str, object]]", raw_schema["fields"]) if field["name"] == "id"
    )
    raw_status_field = next(
        field for field in cast("list[dict[str, object]]", raw_schema["fields"]) if field["name"] == "status"
    )
    raw_status_type = cast("dict[str, object]", raw_status_field["type"])
    definitions = cast("dict[str, dict[str, object]]", converted["definitions"])
    record_schema = definitions["User"]
    field_schema = cast("dict[str, object]", record_schema["properties"])["id"]
    enum_schema = converted["definitions"]["Status"]

    assert_mutable_copy_is_isolated(
        original=raw_schema["aliases"],
        copied=record_schema["x-avro-aliases"],
        mutate_copied=_append_mutation_marker,
        label="Avro record aliases",
    )
    assert_mutable_copy_is_isolated(
        original=raw_id_field["aliases"],
        copied=field_schema["x-avro-aliases"],
        mutate_copied=_append_mutation_marker,
        label="Avro field aliases",
    )
    assert_mutable_copy_is_isolated(
        original=raw_status_type["symbols"],
        copied=enum_schema["enum"],
        mutate_copied=_append_mutation_marker,
        label="Avro enum symbols",
    )


@_SKIP_BLACK
def test_main_avro_constructs(output_file: Path) -> None:
    """Generate models for Avro primitive, complex, named, union, default, and logical types."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "constructs.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=_expected_file("constructs.py"),
        extra_args=get_current_version_args("--use-field-description"),
        force_exec_validation=True,
    )


def test_main_avro_fixed_reference_default(output_file: Path) -> None:
    """Decode defaults for both inline and previously declared fixed schemas."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "fixed_reference_default.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file="fixed_reference_default.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("output_model_type", "backend"),
    [
        (DataModelType.PydanticV2BaseModel, "pydantic_v2"),
        (DataModelType.DataclassesDataclass, "dataclass"),
        (DataModelType.MsgspecStruct, "msgspec"),
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_avro_container_bytes_defaults(
    output_file: Path, output_model_type: DataModelType, backend: str, entrypoint: str
) -> None:
    """Preserve bytes, fixed leaves, strings, order, scope, and isolated defaults through generation."""
    input_path = AVRO_DATA_PATH / "container_bytes_defaults.avsc"
    expected_file = f"container_bytes_defaults_{backend}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="avro",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=[
                "--target-python-version",
                "3.10",
                "--output-model-type",
                output_model_type.value,
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.Avro,
            assert_func=assert_file_content,
            expected_file=expected_file,
            output_model_type=output_model_type,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
            use_annotated=output_model_type == DataModelType.MsgspecStruct,
            field_constraints=output_model_type == DataModelType.MsgspecStruct,
        )
    with _generated_model(
        output_file, f"generated_container_defaults_{backend}_{entrypoint}", "ContainerDefaults"
    ) as model:
        first = model()
        first.array.append(b"changed")
        first.mapping["z"] = b"changed"
        first.nested[0]["z"].append(b"changed")
        rendered = repr(model())
    assert_output(f"{rendered}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/container_bytes_defaults_{backend}.txt")


@pytest.mark.parametrize("fixture_name", ["container_bytes_defaults.avsc", "bytes_default_controls.yaml"])
def test_avro_bytes_defaults_raw_input_unchanged(output_file: Path, fixture_name: str) -> None:
    """Keep raw default objects intact, including already decoded bytes from YAML."""
    input_path = AVRO_DATA_PATH / fixture_name
    raw_schema = load_data(input_path.read_text(encoding="utf-8"))
    with assert_inputs_not_mutated({"schema": raw_schema}):
        generate(
            input_=raw_schema,
            output=output_file,
            input_file_type=InputFileType.Avro,
            input_filename=fixture_name,
            output_model_type=DataModelType.PydanticV2BaseModel,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        )
    assert_file_content(output_file, f"{input_path.stem}_pydantic_v2.py")


@pytest.mark.parametrize(
    ("output_model_type", "backend", "datetime_class"),
    [
        (DataModelType.PydanticV2BaseModel, "pydantic_v2", None),
        (DataModelType.DataclassesDataclass, "dataclass", None),
        (DataModelType.MsgspecStruct, "msgspec", None),
        (DataModelType.TypingTypedDict, "typed_dict", None),
        (DataModelType.DataclassesDataclass, "dataclass_datetime", DatetimeClassType.Datetime),
        (DataModelType.MsgspecStruct, "msgspec_datetime", DatetimeClassType.Datetime),
        (DataModelType.TypingTypedDict, "typed_dict_datetime", DatetimeClassType.Datetime),
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("fixture", ["temporal_defaults", "temporal_defaults_simple"])
def test_avro_temporal_defaults(
    output_file: Path,
    output_model_type: DataModelType,
    backend: str,
    datetime_class: DatetimeClassType | None,
    entrypoint: str,
    fixture: str,
) -> None:
    """Preserve temporal units, timezone, precision, nested defaults and backend type policies."""
    input_path = AVRO_DATA_PATH / f"{fixture}.avsc"
    expected_file = f"{fixture}_{backend}.py"
    # Keep the nested constructor regression independent of formatter layout differences.
    builtin = fixture == "temporal_defaults"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="avro",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=[
                "--target-python-version",
                "3.10",
                "--output-model-type",
                output_model_type.value,
                "--disable-timestamp",
                *(["--formatters", "builtin"] if builtin else []),
                *(["--output-datetime-class", datetime_class.value] if datetime_class else []),
            ],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.Avro,
            assert_func=assert_file_content,
            expected_file=expected_file,
            output_model_type=output_model_type,
            output_datetime_class=datetime_class,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            **({"formatters": [Formatter.BUILTIN]} if builtin else {}),
            use_annotated=output_model_type == DataModelType.MsgspecStruct,
            field_constraints=output_model_type == DataModelType.MsgspecStruct,
        )
    if output_model_type == DataModelType.TypingTypedDict:
        return
    runtime_file = AVRO_DATA_PATH.parent / f"expected/main/avro/{fixture}_{backend}.txt"
    with _generated_model(output_file, f"generated_{fixture}_{backend}_{entrypoint}", "TemporalDefaults") as model:
        assert_output(f"{model()!r}\n", runtime_file)
        if output_model_type == DataModelType.PydanticV2BaseModel:
            model.model_config["validate_default"] = True
            model.model_rebuild(force=True)
            assert_output(f"{model()!r}\n", runtime_file)


def test_avro_temporal_defaults_preserve_raw_input(output_file: Path) -> None:
    """Keep integer defaults untouched through raw API generation and public conversion."""
    raw_schema = load_data((AVRO_DATA_PATH / "temporal_defaults.avsc").read_text(encoding="utf-8"))
    with assert_inputs_not_mutated({"schema": raw_schema}):
        generate(
            raw_schema,
            output=output_file,
            input_file_type=InputFileType.Avro,
            input_filename="temporal_defaults.avsc",
            disable_timestamp=True,
            target_python_version=PythonVersion.PY_310,
            formatters=[Formatter.BUILTIN],
        )
        converted = convert_avro_schema_data(raw_schema)
    assert_file_content(output_file, "temporal_defaults_pydantic_v2.py")
    rendered = "\n".join(f"{name}: {field['default']!r}" for name, field in converted["properties"].items())
    assert_output(f"{rendered}\n", AVRO_DATA_PATH.parent / "expected/main/avro/temporal_defaults_converted.txt")


@pytest.mark.parametrize(
    ("output_model_type", "backend"),
    [
        (DataModelType.PydanticV2BaseModel, "pydantic_v2"),
        (DataModelType.DataclassesDataclass, "dataclass"),
        (DataModelType.MsgspecStruct, "msgspec"),
    ],
)
def test_avro_mapped_logical_defaults(output_file: Path, output_model_type: DataModelType, backend: str) -> None:
    """Preserve physical defaults when temporal, decimal and duration formats are remapped."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "mapped_logical_defaults.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=f"mapped_logical_defaults_{backend}.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type.value,
            "--collapse-root-models",
            "--type-mappings",
            "date=integer",
            "time=integer",
            "date-time=integer",
            "date-time-local=integer",
            "decimal=binary",
            "duration=binary",
        ],
        force_exec_validation=True,
    )
    runtime_file = AVRO_DATA_PATH.parent / "expected/main/avro/mapped_logical_defaults.txt"
    with _generated_model(output_file, f"mapped_defaults_{backend}", "MappedDefaults") as model:
        assert_output(f"{model()!r}\n", runtime_file)
        if output_model_type == DataModelType.PydanticV2BaseModel:
            model.model_config["validate_default"] = True
            model.model_rebuild(force=True)
            assert_output(f"{model()!r}\n", runtime_file)


@pytest.mark.parametrize(
    "case",
    json.loads((AVRO_DATA_PATH / "logical_default_overrides.json").read_text(encoding="utf-8")),
    ids=operator.itemgetter("name"),
)
def test_avro_overridden_logical_defaults(output_file: Path, capsys: pytest.CaptureFixture[str], case: dict) -> None:
    """Preserve physical overrides alongside logical defaults and revalidate their Python dumps."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / f"{case['fixture']}.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=f"overridden_logical_defaults_{case['name']}.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--type-overrides",
            json.dumps(case["overrides"]),
            *case["args"],
        ],
        force_exec_validation=True,
        capsys=capsys,
        assert_no_stderr=True,
    )
    for model_name in [case["model"], *case.get("auxiliary_models", [])]:
        suffix = "" if model_name == case["model"] else f"_{model_name}"
        runtime_file = (
            AVRO_DATA_PATH.parent / f"expected/main/avro/overridden_logical_defaults_{case['name']}{suffix}.txt"
        )
        with _generated_model(output_file, f"overridden_logical_defaults_{case['name']}", model_name) as model:
            instance = model()
            assert_output(f"{instance!r}\n{instance.model_dump(by_alias=True)!r}\n", runtime_file)
            instance = model.model_validate(instance.model_dump(by_alias=True))
            assert_output(f"{instance!r}\n{instance.model_dump(by_alias=True)!r}\n", runtime_file)
            model.model_config["validate_default"] = True
            model.model_rebuild(force=True)
            instance = model()
            assert_output(f"{instance!r}\n{instance.model_dump(by_alias=True)!r}\n", runtime_file)


@pytest.mark.parametrize(
    ("mappings", "expected_file", "runtime_file"),
    [
        (
            ("date=date", "time=time"),
            "temporal_defaults_simple_pydantic_v2.py",
            "temporal_defaults_simple_pydantic_v2.txt",
        ),
        (
            ("date=string", "time=string"),
            "temporal_defaults_mapped_strings.py",
            "temporal_defaults_simple_dataclass.txt",
        ),
    ],
)
def test_avro_mapped_temporal_strings(
    output_file: Path, mappings: tuple[str, str], expected_file: str, runtime_file: str
) -> None:
    """Retain logical defaults for matching formats and explicit string output."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "temporal_defaults_simple.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--type-mappings",
            *mappings,
        ],
        force_exec_validation=True,
    )
    with _generated_model(output_file, "mapped_temporal_defaults", "TemporalDefaults") as model:
        model.model_config["validate_default"] = True
        model.model_rebuild(force=True)
        assert_output(f"{model()!r}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{runtime_file}")


def test_main_avro_temporal_default_controls(output_file: Path) -> None:
    """Leave ordinary defaults, unsupported annotations and temporal fields without defaults unchanged."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "temporal_default_controls.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file="temporal_default_controls.py",
        extra_args=["--target-python-version", "3.10", "--disable-timestamp", "--formatters", "builtin"],
        force_exec_validation=True,
    )


def test_main_avro_temporal_defaults_unused_by_typed_dict(output_file: Path) -> None:
    """Do not normalize unused defaults for backends which do not emit them."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "invalid_temporal_default_nanos_precision.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file="temporal_defaults_unused_typed_dict.py",
        extra_args=[
            "--output-model-type",
            DataModelType.TypingTypedDict.value,
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("type_overrides", [{}, {"Other.value": "builtins.int"}])
@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("date_min", "Avro date default is outside the Python date range"),
        ("date_max", "Avro date default is outside the Python date range"),
        ("date_huge", "Avro date default is outside the Python date range"),
        ("time_millis_negative", "Avro time-millis default must be within a single day"),
        ("time_millis_next_day", "Avro time-millis default must be within a single day"),
        ("time_micros_negative", "Avro time-micros default must be within a single day"),
        ("time_micros_next_day", "Avro time-micros default must be within a single day"),
        ("timestamp_min", "Avro timestamp-micros default is outside the Python datetime range"),
        ("timestamp_max", "Avro timestamp-micros default is outside the Python datetime range"),
        ("local_huge", "Avro local-timestamp-millis default is outside the Python datetime range"),
        ("nanos_precision", "Avro timestamp-nanos default cannot be represented exactly at microsecond precision"),
        ("local_nanos_precision", "Avro local-timestamp-nanos default cannot be represented exactly"),
    ],
)
def test_main_avro_unrepresentable_temporal_default(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    fixture: str,
    message: str,
    type_overrides: dict[str, str],
) -> None:
    """Report only temporal range and exactness errors through the real CLI."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / f"invalid_temporal_default_{fixture}.avsc",
        output_path=output_file,
        input_file_type="avro",
        expected_exit=Exit.ERROR,
        extra_args=["--type-overrides", json.dumps(type_overrides)],
        capsys=capsys,
        expected_stderr_contains=message,
    )


@pytest.mark.parametrize(
    ("output_model_type", "backend"),
    [
        (DataModelType.PydanticV2BaseModel, "pydantic_v2"),
        (DataModelType.PydanticV2Dataclass, "pydantic_dataclass"),
        (DataModelType.DataclassesDataclass, "dataclass"),
        (DataModelType.MsgspecStruct, "msgspec"),
        (DataModelType.TypingTypedDict, "typed_dict"),
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("fixture", ["decimal_defaults", "decimal_defaults_simple"])
@pytest.mark.parametrize("deserialize", [False, True])
def test_avro_decimal_defaults(
    output_file: Path,
    output_model_type: DataModelType,
    backend: str,
    entrypoint: str,
    fixture: str,
    deserialize: bool,
) -> None:
    """Preserve signed coefficients and scale despite restrictive decimal contexts."""
    input_path = AVRO_DATA_PATH / f"{fixture}.avsc"
    expected_file = f"{fixture}_{backend}.py"
    builtin = fixture == "decimal_defaults"
    with localcontext() as context:
        context.prec = 2
        context.capitals = 0
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        if entrypoint == "cli":
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type="avro",
                assert_func=assert_file_content,
                expected_file=expected_file,
                extra_args=[
                    "--target-python-version",
                    "3.10",
                    "--disable-timestamp",
                    "--output-model-type",
                    output_model_type.value,
                    *(["--formatters", "builtin"] if builtin else []),
                    *(["--deserialize-default-values", "decimal"] if deserialize else []),
                ],
                force_exec_validation=True,
            )
        else:
            run_generate_file_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=InputFileType.Avro,
                assert_func=assert_file_content,
                expected_file=expected_file,
                output_model_type=output_model_type,
                target_python_version=PythonVersion.PY_310,
                disable_timestamp=True,
                **({"formatters": [Formatter.BUILTIN]} if builtin else {}),
                deserialize_default_values=[DefaultValueType.Decimal] if deserialize else [],
                use_annotated=output_model_type == DataModelType.MsgspecStruct,
                field_constraints=output_model_type == DataModelType.MsgspecStruct,
            )
        if output_model_type == DataModelType.TypingTypedDict:
            return
        with _generated_model(output_file, f"generated_{fixture}_{backend}_{entrypoint}", "DecimalDefaults") as model:
            assert_output(f"{model()!r}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{fixture}_{backend}.txt")
            if output_model_type == DataModelType.PydanticV2BaseModel:
                model.model_config["validate_default"] = True
                model.model_rebuild(force=True)
                assert_output(f"{model()!r}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{fixture}_{backend}.txt")


@pytest.mark.parametrize("fixture", ["decimal_defaults.avsc", "decimal_defaults_raw.yaml"])
def test_avro_decimal_defaults_preserve_raw_input(output_file: Path, fixture: str) -> None:
    """Normalize JSON strings and raw YAML bytes without changing their input objects."""
    raw_schema = load_data((AVRO_DATA_PATH / fixture).read_text(encoding="utf-8"))
    with localcontext() as context, assert_inputs_not_mutated({"schema": raw_schema}):
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        generate(
            raw_schema,
            output=output_file,
            input_file_type=InputFileType.Avro,
            input_filename=fixture,
            disable_timestamp=True,
            target_python_version=PythonVersion.PY_310,
            formatters=[Formatter.BUILTIN],
        )
        converted = convert_avro_schema_data(raw_schema)
    assert_file_content(
        output_file, "decimal_defaults_pydantic_v2.py" if fixture.endswith("avsc") else "decimal_defaults_raw.py"
    )
    rendered = "\n".join(f"{name}: {field['default']!r}" for name, field in converted["properties"].items())
    assert_output(f"{rendered}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{Path(fixture).stem}_converted.txt")


@pytest.mark.parametrize(
    ("fixture", "expected_file", "extra_args", "warning"),
    [
        (
            "decimal_default_controls",
            "decimal_default_controls.py",
            [],
            "10 Decimal default values were emitted as serialized data",
        ),
        *(
            (
                "decimal_defaults_simple",
                "decimal_defaults_logical_override_mapping.py",
                [
                    "--type-overrides",
                    '{"DecimalDefaults.positive":"decimal.Decimal"}',
                    "--type-mappings",
                    "decimal=binary",
                    *(["--deserialize-default-values", "decimal"] if deserialize else []),
                ],
                "1 Decimal default value could not be deserialized"
                if deserialize
                else "1 Decimal default value was emitted as serialized data",
            )
            for deserialize in (False, True)
        ),
    ],
)
def test_main_avro_decimal_default_controls(
    output_file: Path, fixture: str, expected_file: str, extra_args: list[str], warning: str
) -> None:
    """Keep ordinary bytes, unsupported metadata and default-free logical schemas unchanged."""
    with pytest.warns(DefaultValueTypeWarning, match=warning):
        run_main_and_assert(
            input_path=AVRO_DATA_PATH / f"{fixture}.avsc",
            output_path=output_file,
            input_file_type="avro",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=[
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                "--formatters",
                "builtin",
                *extra_args,
            ],
            force_exec_validation=True,
        )


def test_main_avro_decimal_defaults_unused_by_typed_dict(output_file: Path) -> None:
    """Do not reject unrepresentable decimal defaults when no default is emitted."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "invalid_decimal_default_scale.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file="invalid_decimal_default_scale.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--output-model-type",
            DataModelType.TypingTypedDict.value,
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("scale", "Avro decimal default scale is outside the Python Decimal range"),
        ("unicode", "Avro bytes and fixed defaults must contain only code points from 0 through 255"),
        ("fixed_short", "Avro fixed decimal default must contain exactly 2 bytes: 1"),
        ("fixed_long", "Avro fixed decimal default must contain exactly 2 bytes: 3"),
        ("fixed_reference", "Avro fixed decimal default must contain exactly 2 bytes: 1"),
        ("precision", "Avro decimal default exceeds precision 2: 300"),
        ("negative_precision", "Avro decimal default exceeds precision 2: -300"),
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_main_avro_unrepresentable_decimal_default(
    output_file: Path, capsys: pytest.CaptureFixture[str], fixture: str, message: str, entrypoint: str
) -> None:
    """Report invalid encoded defaults and unrepresentable scales through CLI/API."""
    input_path = AVRO_DATA_PATH / f"invalid_decimal_default_{fixture}.avsc"
    if entrypoint == "api":
        with pytest.raises(Error, match=message):
            generate(input_path, input_file_type=InputFileType.Avro, output=output_file)
        return
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="avro",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=message,
    )


@pytest.mark.parametrize(
    ("output_model_type", "backend"),
    [
        (DataModelType.PydanticV2BaseModel, "pydantic_v2"),
        (DataModelType.PydanticV2Dataclass, "pydantic_dataclass"),
        (DataModelType.DataclassesDataclass, "dataclass"),
        (DataModelType.MsgspecStruct, "msgspec"),
        (DataModelType.TypingTypedDict, "typed_dict"),
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("fixture", ["duration_defaults", "duration_defaults_simple"])
@pytest.mark.parametrize("deserialize", [False, True])
def test_avro_duration_defaults(
    output_file: Path,
    output_model_type: DataModelType,
    backend: str,
    entrypoint: str,
    fixture: str,
    deserialize: bool,
) -> None:
    """Preserve fixed milliseconds, named/container defaults and backend policies."""
    input_path = AVRO_DATA_PATH / f"{fixture}.avsc"
    expected_file = f"{fixture}_{backend}.py"
    builtin = fixture == "duration_defaults"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="avro",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=[
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                "--output-model-type",
                output_model_type.value,
                *(["--formatters", "builtin"] if builtin else []),
                *(["--deserialize-default-values", "decimal"] if deserialize else []),
            ],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.Avro,
            assert_func=assert_file_content,
            expected_file=expected_file,
            output_model_type=output_model_type,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            **({"formatters": [Formatter.BUILTIN]} if builtin else {}),
            deserialize_default_values=[DefaultValueType.Decimal] if deserialize else [],
            use_annotated=output_model_type == DataModelType.MsgspecStruct,
            field_constraints=output_model_type == DataModelType.MsgspecStruct,
        )
    if output_model_type == DataModelType.TypingTypedDict:
        return
    with _generated_model(output_file, f"generated_{fixture}_{backend}_{entrypoint}", "DurationDefaults") as model:
        assert_output(f"{model()!r}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{fixture}_{backend}.txt")
        if output_model_type == DataModelType.PydanticV2BaseModel:
            model.model_config["validate_default"] = True
            model.model_rebuild(force=True)
            assert_output(f"{model()!r}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{fixture}_{backend}.txt")


@pytest.mark.parametrize("fixture", ["duration_defaults.avsc", "duration_defaults_raw.yaml"])
def test_avro_duration_defaults_preserve_raw_input(output_file: Path, fixture: str) -> None:
    """Normalize JSON strings and raw YAML bytes without changing their input objects."""
    raw_schema = load_data((AVRO_DATA_PATH / fixture).read_text(encoding="utf-8"))
    with assert_inputs_not_mutated({"schema": raw_schema}):
        generate(
            raw_schema,
            output=output_file,
            input_file_type=InputFileType.Avro,
            input_filename=fixture,
            disable_timestamp=True,
            target_python_version=PythonVersion.PY_310,
            formatters=[Formatter.BUILTIN],
        )
        converted = convert_avro_schema_data(raw_schema)
    assert_file_content(
        output_file, "duration_defaults_pydantic_v2.py" if fixture.endswith("avsc") else "duration_defaults_raw.py"
    )
    rendered = "\n".join(f"{name}: {field['default']!r}" for name, field in converted["properties"].items())
    assert_output(f"{rendered}\n", AVRO_DATA_PATH.parent / f"expected/main/avro/{Path(fixture).stem}_converted.txt")


def test_main_avro_duration_default_controls(output_file: Path) -> None:
    """Keep ordinary bytes, unsupported metadata and default-free logical schemas unchanged."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "duration_default_controls.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file="duration_default_controls.py",
        extra_args=["--target-python-version", "3.10", "--disable-timestamp", "--formatters", "builtin"],
        force_exec_validation=True,
    )


def test_main_avro_duration_defaults_unused_by_typed_dict(output_file: Path) -> None:
    """Skip unused duration defaults, including unsupported calendar components."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "invalid_duration_default_months.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file="invalid_duration_default_months.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            "--output-model-type",
            DataModelType.TypingTypedDict.value,
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("months", "months=1, days=0"),
        ("days", "months=0, days=1"),
        ("calendar_maximum", "months=4294967295, days=4294967295"),
        ("short", "Avro duration default requires a fixed size of 12 and exactly 12 encoded bytes"),
        ("long", "Avro duration default requires a fixed size of 12 and exactly 12 encoded bytes"),
        ("size", "Avro duration default requires a fixed size of 12 and exactly 12 encoded bytes"),
        ("unicode", "Avro bytes and fixed defaults must contain only code points from 0 through 255"),
    ],
)
def test_main_avro_unrepresentable_duration_default(
    output_file: Path, capsys: pytest.CaptureFixture[str], fixture: str, message: str
) -> None:
    """Report invalid encodings and unsupported calendar components through the real CLI."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / f"invalid_duration_default_{fixture}.avsc",
        output_path=output_file,
        input_file_type="avro",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=message,
    )


@pytest.mark.parametrize(("output_model_type", "expected_name"), BACKEND_GOLDEN_CASES)
def test_main_avro_output_model_types(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Generate representative Avro models across supported output backends."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "constructs.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/constructs_{expected_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--output-model-type",
            output_model_type,
            "--use-field-description",
        ],
        force_exec_validation=True,
        importable_module_name=f"generated_avro_{expected_name}",
        importable_module_attribute="User",
    )
    if output_model_type != DataModelType.MsgspecStruct.value:
        return
    import msgspec

    with _generated_model(output_file, f"decoded_avro_{expected_name}", "User") as model:
        msgspec.json.Decoder(type=model)


@_SKIP_BLACK
def test_main_avro_infer_input_file_type(output_file: Path) -> None:
    """Infer Avro schema input and generate a model."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "constructs.avsc",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file=_expected_file("constructs.py"),
        extra_args=get_current_version_args("--use-field-description"),
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_main_avro_namespace_collisions(output_file: Path) -> None:
    """Resolve Avro fullname collisions by namespace."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "namespace_collisions.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=_expected_file("namespace_collisions.py"),
        extra_args=get_current_version_args(),
        force_exec_validation=True,
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_avro_namespace_reference_precedence(output_file: Path, entrypoint: str) -> None:
    """Resolve local record, enum, fixed and recursive references before names in the null namespace."""
    input_path = AVRO_DATA_PATH / "namespace_reference_precedence.avsc"
    expected_file = "namespace_reference_precedence.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="avro",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=[
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.Avro,
            assert_func=assert_file_content,
            expected_file=expected_file,
            output_model_type=DataModelType.PydanticV2BaseModel,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"generated_namespace_references_{entrypoint}",
        model_name="Container",
        valid_json=(AVRO_DATA_PATH / "namespace_reference_precedence.valid.json").read_text(encoding="utf-8"),
        invalid_json=(AVRO_DATA_PATH / "namespace_reference_precedence.invalid.json").read_text(encoding="utf-8"),
        expected_error_type="missing",
        expected_attribute_path=("chosen", "local_value"),
        expected_attribute_value=3,
        expected_repr_path=AVRO_DATA_PATH.parent / "expected/main/avro/namespace_reference_precedence.txt",
    )


@_SKIP_BLACK
def test_main_avro_official_spec_long_list(output_file: Path) -> None:
    """Generate the recursive LongList record from the Apache Avro specification."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "official_long_list.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=_expected_file("official_long_list.py"),
        extra_args=get_current_version_args(),
        force_exec_validation=True,
    )


@_SKIP_BLACK
def test_main_avro_single_value_enum_as_const_keeps_converted_enums(output_file: Path) -> None:
    """Keep one-symbol Avro enums as Enum classes, as Avro is converted rather than read as a JSON Schema document."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "spec_matrix.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=_expected_file("spec_matrix.py"),
        extra_args=get_current_version_args("--use-field-description", "--use-single-value-enum-as-const"),
    )


@_SKIP_BLACK
def test_main_avro_spec_matrix(output_file: Path) -> None:
    """Generate models for Avro specification schema forms and edge-case attributes."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "spec_matrix.avsc",
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=_expected_file("spec_matrix.py"),
        extra_args=get_current_version_args("--use-field-description"),
        force_exec_validation=True,
    )
    required_values = {
        "ascendingOrder": "ascending",
        "descendingOrder": "descending",
        "sameKindNamedUnion": {"a": "record"},
        "arrayOfRecords": [],
        "mapOfEnums": {},
        "emptyNamespace": b"ok",
        "fullnameIgnoresNamespace": {"understanding": "YES", "understandingRef": "NO"},
        "fullnameReference": {"understanding": "NO", "understandingRef": "YES"},
        "complexTypeNameReuse": {"value": "array"},
        "unknownLogicalType": 1,
        "precisionOnlyDecimal": "1.25",
    }
    with _generated_model(output_file, "generated_avro_defaults", "SpecMatrix") as model:
        first = model(**required_values)
        second = model(**required_values)
        first.arrayDefault.append(2)
        first.mapDefault["b"] = 2
        rendered = "\n".join(
            f"{name}: {type(value).__name__} = {value!r}"
            for name in (
                "nullDefault",
                "booleanDefault",
                "bytesDefault",
                "recordDefault",
                "enumDefault",
                "arrayDefault",
                "mapDefault",
                "fixedDefault",
                "nullableAfterValue",
            )
            if (value := getattr(second, name)) is not ...
        )
    assert_output(f"{rendered}\n", AVRO_DATA_PATH.parent / "expected/main/avro/default_values.txt")


@pytest.mark.parametrize(
    ("fixture_name", "explicit_type"),
    [
        ("root_string.avsc", False),
        ("root_type_object.avsc", False),
        ("root_type_union.avsc", True),
        ("root_map.avsc", False),
        ("root_array.avsc", True),
        ("root_enum.avsc", True),
        ("root_fixed.avsc", True),
        ("root_fixed_uuid.avsc", True),
        pytest.param("root_union_named.avsc", False, marks=LEGACY_BLACK_SKIP),
        ("name_suffix_collision.avsc", True),
        ("namespace_null_collision.avsc", True),
    ],
)
@_SKIP_BLACK
def test_main_avro_schema_declaration_forms(output_file: Path, fixture_name: str, explicit_type: bool) -> None:
    """Generate importable code for Avro schema string/object/array declaration forms."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / fixture_name,
        output_path=output_file,
        input_file_type="avro" if explicit_type else None,
        assert_func=assert_file_content,
        expected_file=_expected_file(f"schema_declaration_forms/{Path(fixture_name).stem}.py"),
        extra_args=get_current_version_args(),
        force_exec_validation=True,
    )


OFFICIAL_SCHEMA_PASS_FIXTURES = tuple(sorted((AVRO_DATA_PATH / "official_schema_pass").glob("*.avsc")))


@pytest.mark.parametrize("fixture_path", OFFICIAL_SCHEMA_PASS_FIXTURES, ids=lambda path: path.stem)
@_SKIP_BLACK
def test_main_avro_official_c_schema_pass_corpus(output_file: Path, fixture_path: Path) -> None:
    """Generate importable code for Apache Avro release-1.12.1 C schema pass fixtures."""
    run_main_and_assert(
        input_path=fixture_path,
        output_path=output_file,
        input_file_type="avro",
        assert_func=assert_file_content,
        expected_file=_expected_file(f"official_schema_pass/{fixture_path.stem}.py"),
        extra_args=get_current_version_args(),
        force_exec_validation=True,
    )


def test_main_avro_schema_version_not_supported(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject explicit schema-version for Avro, which does not define an in-schema version marker."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / "constructs.avsc",
        output_path=output_file,
        input_file_type="avro",
        extra_args=["--schema-version", "1.12"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Avro schemas do not carry a version marker",
    )


@pytest.mark.parametrize(
    "type_overrides",
    [{}, {"BrokenField.value": "builtins.int", "BrokenFieldItem.value": "builtins.int"}],
)
@pytest.mark.parametrize(
    ("fixture_name", "expected_stderr_contains"),
    [
        ("invalid_schema_no_type.avsc", "Avro schema object requires a string, object, or union type"),
        ("invalid_schema_bad_name.avsc", "Avro record schema requires a string name"),
        ("invalid_schema_bad_fields.avsc", "Avro record fields must be a list"),
        ("invalid_schema_bad_field.avsc", "Avro record field requires a string name"),
        ("invalid_schema_field_item.avsc", "Avro record field requires a string name"),
        ("invalid_schema_recursive_default_fields.avsc", "Avro record field requires a string name"),
        ("invalid_schema_field_value.avsc", "Unsupported Avro schema value"),
        ("invalid_schema_duplicate.avsc", "Duplicate Avro named type"),
        ("invalid_schema_unknown_ref.avsc", "Unknown Avro named type reference"),
        ("invalid_schema_root_ref.avsc", "Unknown Avro named type reference"),
        ("invalid_schema_simple_ref.avsc", "Unknown Avro named type reference"),
        ("invalid_schema_unknown_simple_ref_in_namespace.avsc", "Unknown Avro named type reference"),
        ("invalid_schema_bad_enum_symbols.avsc", "Avro enum symbols must be a list of strings"),
        ("invalid_schema_duplicate_enum_symbol.avsc", "Duplicate Avro enum symbol"),
        ("invalid_schema_bad_enum_symbol_name.avsc", "Invalid Avro enum symbol"),
        ("invalid_schema_bad_fixed_size.avsc", "Avro fixed size must be an integer"),
        ("invalid_schema_duplicate_field_name.avsc", "Duplicate Avro record field name"),
        ("invalid_schema_bad_named_type_name.avsc", "Invalid Avro record name"),
        ("invalid_schema_bad_namespace.avsc", "Invalid Avro namespace"),
        ("invalid_schema_bad_field_name.avsc", "Invalid Avro record field name"),
        (
            "invalid_schema_bytes_default_unicode.avsc",
            "Avro bytes and fixed defaults must contain only code points from 0 through 255",
        ),
        (
            "invalid_schema_array_bytes_default_unicode.avsc",
            "Avro bytes and fixed defaults must contain only code points from 0 through 255",
        ),
        (
            "invalid_schema_map_bytes_default_unicode.avsc",
            "Avro bytes and fixed defaults must contain only code points from 0 through 255",
        ),
        (
            "invalid_schema_record_bytes_default_unicode.avsc",
            "Avro bytes and fixed defaults must contain only code points from 0 through 255",
        ),
        ("invalid_schema_primitive_name_reuse.avsc", "Avro primitive type names may not be redefined"),
        ("invalid_schema_union_bad_value.avsc", "Unsupported Avro union value"),
        ("invalid_schema_union_nested.avsc", "Avro unions may not immediately contain other unions"),
        ("invalid_schema_union_type_list.avsc", "Avro unions may not immediately contain other unions"),
        ("invalid_schema_union_duplicate.avsc", "Avro unions may not contain duplicate unnamed type"),
        ("invalid_schema_union_ref.avsc", "Unknown Avro named type reference"),
    ],
)
def test_main_avro_invalid_schema_errors(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    fixture_name: str,
    expected_stderr_contains: str,
    type_overrides: dict[str, str],
) -> None:
    """Report Avro parser errors through the normal CLI path."""
    run_main_and_assert(
        input_path=AVRO_DATA_PATH / fixture_name,
        output_path=output_file,
        input_file_type="avro",
        expected_exit=Exit.ERROR,
        extra_args=["--type-overrides", json.dumps(type_overrides)],
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
    )


@pytest.mark.parametrize(
    ("file_text", "expected_stderr_contains"),
    [
        ("[]", "Can't infer input file type"),
        ("[1]", "Can't infer input file type"),
        ('["Custom"]', "Can't infer input file type"),
        ('[{"not": "a schema"}]', "Can't infer input file type"),
    ],
)
def test_main_avro_auto_detection_rejects_non_avro_shapes(
    tmp_path: Path,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    file_text: str,
    expected_stderr_contains: str,
) -> None:
    """Avoid auto-detecting generic JSON/YAML shapes as Avro schemas."""
    input_path = tmp_path / "input.json"
    input_path.write_text(file_text, encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
    )


@pytest.mark.parametrize(
    ("file_text", "expected_stderr_contains"),
    [
        ('[["null", "string"]]', "Avro unions may not immediately contain other unions"),
        ('[{"type": ["null", "string"]}]', "Avro unions may not immediately contain other unions"),
        ('["null", "example.Ref"]', "Unknown Avro named type reference"),
        ('[{"type": "example.Ref"}]', "Unknown Avro named type reference"),
        ('{"type": "example.Ref"}', "Unknown Avro named type reference"),
    ],
)
def test_main_avro_auto_detection_routes_invalid_avro_shapes_to_avro_errors(
    tmp_path: Path,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    file_text: str,
    expected_stderr_contains: str,
) -> None:
    """Infer Avro-looking inputs before reporting Avro parser errors."""
    input_path = tmp_path / "input.avsc"
    input_path.write_text(file_text, encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
    )


@pytest.mark.parametrize(
    "file_text",
    [
        '{"type": ["null", "string"]}',
        '{"type": "record"}',
        '{"type": "enum", "name": "Broken"}',
        '{"type": "fixed", "name": "Broken"}',
        '{"type": 1}',
        '{"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}',
    ],
)
def test_main_avro_auto_detection_leaves_json_schema_shapes_as_json_schema(
    tmp_path: Path,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    file_text: str,
) -> None:
    """Do not route JSON Schema-looking inputs through the Avro parser."""
    input_path = tmp_path / "input.json"
    input_path.write_text(file_text, encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        capsys=capsys,
        expected_stderr_contains="The input file type was determined to be:",
    )
