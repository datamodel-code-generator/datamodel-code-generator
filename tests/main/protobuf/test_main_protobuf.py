"""Tests for Protocol Buffers code generation."""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

import black
import msgspec
import pytest
import yaml
from google.protobuf import descriptor_pool, json_format, message_factory
from pydantic import ValidationError

from datamodel_code_generator import DataModelType, Error, InputFileType, SchemaParseError, generate, infer_input_type
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.parser.protobuf import WELL_KNOWN_SCHEMAS, ProtobufParser, convert_protobuf_schema_data
from tests.conftest import assert_mutable_copy_is_isolated, assert_output
from tests.main.conftest import (
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    DATA_PATH,
    EXPECTED_PROTOBUF_PATH,
    PROTOBUF_DATA_PATH,
    _generated_model,
    _model_json_validator,
    assert_input_file_type,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.protobuf.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def test_main_protobuf_complex_proto3(output_file: Path) -> None:
    """Generate models from a proto3 schema with imports, service, and well-known types."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "complex_proto3.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=["--use-field-description"],
        assert_func=assert_file_content,
        expected_file="complex_proto3.py",
        importable_module_name="generated_protobuf",
        importable_module_attribute="ExampleShopV1Order",
    )


@pytest.mark.parametrize(("output_model_type", "expected_name"), BACKEND_GOLDEN_CASES)
def test_main_protobuf_output_model_types(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Generate representative Protocol Buffers models across supported output backends."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "complex_proto3.proto",
        output_path=output_file,
        input_file_type="protobuf",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/complex_proto3_{expected_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--output-model-type",
            output_model_type,
            "--set-default-enum-member",
            "--use-field-description",
        ],
        force_exec_validation=True,
        importable_module_name=f"generated_protobuf_{expected_name}",
        importable_module_attribute="ExampleShopV1Order",
    )
    if output_model_type not in {
        DataModelType.PydanticV2BaseModel.value,
        DataModelType.MsgspecStruct.value,
    }:
        return
    with _generated_model(output_file, f"default_protobuf_{expected_name}", "ExampleShopV1Order") as model:
        match model().status:
            case Enum(name="STATUS_UNSPECIFIED"):
                pass
            case value:  # pragma: no cover
                pytest.fail(f"Expected enum member default, got {value!r}")


def test_main_protobuf_infer_input_file_type(output_file: Path) -> None:
    """Infer Protocol Buffers input and generate a model."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "proto3_optional.proto",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file="proto3_optional.py",
    )


def test_main_protobuf_proto2_schema_version(output_file: Path) -> None:
    """Generate models from proto2 with required, optional, repeated, and defaults."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "proto2.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=["--schema-version", "proto2"],
        assert_func=assert_file_content,
        expected_file="proto2.py",
    )


def test_main_protobuf_proto3_optional_schema_version(output_file: Path) -> None:
    """Generate models from proto3 optional fields with explicit schema version."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "proto3_optional.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=["--schema-version", "proto3"],
        assert_func=assert_file_content,
        expected_file="proto3_optional.py",
    )


def test_main_protobuf_well_known_wrappers(output_file: Path) -> None:
    """Generate models for all wrapper well-known types and Empty."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "well_known_wrappers.proto",
        output_path=output_file,
        input_file_type="protobuf",
        assert_func=assert_file_content,
        expected_file="well_known_wrappers.py",
    )


def _mutate_first_any_of_schema(value: object) -> None:
    any_of = cast("list[dict[str, Any]]", cast("dict[str, Any]", value)["anyOf"])
    any_of[0]["x-mutated"] = True


def test_convert_protobuf_schema_data_isolates_well_known_schema_templates() -> None:
    """Keep converted well-known schemas independent from module-level templates."""
    original_templates = deepcopy(WELL_KNOWN_SCHEMAS)
    proto = (PROTOBUF_DATA_PATH / "well_known_wrappers.proto").read_text(encoding="utf-8")

    try:
        converted = convert_protobuf_schema_data(proto)
        properties = converted["definitions"]["example__wkt__WrapperBucket"]["properties"]
        assert_mutable_copy_is_isolated(
            original=WELL_KNOWN_SCHEMAS["google.protobuf.StringValue"],
            copied=properties["string_value"],
            mutate_copied=_mutate_first_any_of_schema,
            label="Protobuf StringValue well-known schema",
        )
    finally:
        WELL_KNOWN_SCHEMAS.clear()
        WELL_KNOWN_SCHEMAS.update(original_templates)


def test_convert_protobuf_schema_data_preserves_yaml_safe_non_finite_defaults() -> None:
    """Keep public Protocol Buffers conversion results serializable by PyYAML."""
    proto = (PROTOBUF_DATA_PATH / "spec_proto2.proto").read_text(encoding="utf-8")
    converted_schemas = (
        convert_protobuf_schema_data(proto),
        ProtobufParser(proto).convert_to_json_schema_data(),
    )

    assert_output(
        "".join(
            yaml.safe_dump(
                [
                    schema["definitions"]["example__spec__proto2__SpecProto2"]["properties"][field_name]["default"]
                    for field_name in ("pos_inf", "neg_inf", "not_a_number")
                ],
                sort_keys=False,
            )
            for schema in converted_schemas
        ),
        EXPECTED_PROTOBUF_PATH / "converted_non_finite_defaults.txt",
    )


def test_protobuf_parser_renders_non_finite_defaults() -> None:
    """Render source-safe non-finite defaults through the internal parser path."""
    parser = ProtobufParser(DATA_PATH / "parser/protobuf/non_finite_defaults.proto")

    assert_output(
        f"{parser.parse(format_=False)}\n",
        DATA_PATH / "expected/parser/protobuf/non_finite_defaults.py",
    )


def test_main_protobuf_spec_proto3(output_file: Path) -> None:
    """Generate models for proto3 constructs from the language specification."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "spec_proto3.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=["--schema-version", "proto3", "--use-field-description"],
        assert_func=assert_file_content,
        expected_file="spec_proto3.py",
    )


def test_main_protobuf_spec_proto2(output_file: Path) -> None:
    """Generate models for proto2 constructs from the language specification."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "spec_proto2.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=["--schema-version", "proto2"],
        assert_func=assert_file_content,
        expected_file="spec_proto2.py",
    )


def test_main_protobuf_custom_template_non_finite_raw(output_file: Path) -> None:
    """Keep non-finite defaults valid in legacy raw custom templates."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "spec_proto2.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=[
            "--schema-version",
            "proto2",
            "--custom-template-dir",
            str(DATA_PATH / "templates_non_finite_raw"),
        ],
        assert_func=assert_file_content,
        expected_file="custom_template_non_finite_raw.py",
        force_exec_validation=True,
    )


def test_main_protobuf_edition_2023_schema_version(output_file: Path) -> None:
    """Infer and generate models for edition 2023 syntax with explicit schema version."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "edition_2023.proto",
        output_path=output_file,
        extra_args=["--schema-version", "2023"],
        assert_func=assert_file_content,
        expected_file="edition_2023.py",
    )


def test_generate_api_protobuf(output_file: Path) -> None:
    """Generate Protocol Buffers models through the public generate() API."""
    run_generate_file_and_assert(
        input_path=PROTOBUF_DATA_PATH / "complex_proto3.proto",
        output_path=output_file,
        input_file_type=InputFileType.Protobuf,
        assert_func=assert_file_content,
        expected_file="complex_proto3_generate.py",
    )


def test_generate_api_protobuf_returns_code() -> None:
    """Return generated code from generate() when output is not provided."""
    result = generate(
        PROTOBUF_DATA_PATH / "proto3_optional.proto",
        input_file_type=InputFileType.Protobuf,
        disable_timestamp=True,
    )

    assert_output(result, EXPECTED_PROTOBUF_PATH / "generate_returns_code.py")


def test_generate_api_protobuf_from_text() -> None:
    """Generate Protocol Buffers models from in-memory .proto text."""
    result = generate(
        'syntax = "proto3";\npackage text.input;\nmessage TextInput { string value = 1; }\n',
        input_file_type=InputFileType.Protobuf,
        disable_timestamp=True,
    )

    assert_output(result, EXPECTED_PROTOBUF_PATH / "text_input.py")


def test_generate_api_protobuf_definition_key_collision() -> None:
    """Keep distinct protobuf symbols when legacy definition keys would collide."""
    result = generate(
        PROTOBUF_DATA_PATH.parent / "protobuf_collision",
        input_file_type=InputFileType.Protobuf,
        disable_timestamp=True,
    )

    assert_output(result, EXPECTED_PROTOBUF_PATH / "collision.py")


def test_generate_api_protobuf_from_path_list() -> None:
    """Generate Protocol Buffers models from a list of .proto file paths."""
    run_generate_and_assert(
        input_=cast(Any, [(PROTOBUF_DATA_PATH / "spec_proto3.proto").resolve()]),  # noqa: TC006
        input_file_type=InputFileType.Protobuf,
        disable_timestamp=True,
        expected_file=EXPECTED_PROTOBUF_PATH / "spec_proto3_list_input.py",
        assert_input_unchanged=True,
    )


def test_generate_api_protobuf_rejects_dict_input() -> None:
    """Reject mapping input because Protocol Buffers requires .proto text."""
    with pytest.raises(Error, match="Dict input is not supported for protobuf"):
        generate({"message": "Invalid"}, input_file_type=InputFileType.Protobuf)


def test_infer_input_file_type_from_message_declaration() -> None:
    """Infer Protocol Buffers from a schema body without an explicit syntax declaration."""
    assert_input_file_type(infer_input_type("message WithoutSyntax { string id = 1; }\n"), InputFileType.Protobuf)


def test_main_protobuf_directory_output_importable(output_dir: Path) -> None:
    """Generate a package from imported .proto files and import the result."""
    expected_directory = (
        EXPECTED_PROTOBUF_PATH / "directory_black_lt_24"
        if int(black.__version__.split(".")[0]) < 24
        else EXPECTED_PROTOBUF_PATH / "directory"
    )
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH,
        output_path=output_dir,
        input_file_type="protobuf",
        extra_args=["--module-split-mode", "single"],
        expected_directory=expected_directory,
        force_exec_validation=True,
        importable_module_name="generated_protobuf",
        importable_module_file="__init__.py",
    )


def test_main_protobuf_parse_error(capsys: pytest.CaptureFixture[str], tmp_path: Path, output_file: Path) -> None:
    """Report invalid Protocol Buffers syntax through the CLI."""
    input_path = tmp_path / "invalid.proto"
    input_path.write_text('syntax = "proto3";\nmessage Broken { string id = 1\n', encoding="utf-8")

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="protobuf",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid Protocol Buffers schema",
        output_should_not_exist=True,
    )


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("absolute", [False, True])
def test_protobuf_weak_import_cannot_escape_temporary_directory(
    absolute: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject weak imports that would write outside their temporary sandbox."""
    temp_root = tmp_path / "temporary"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    import_path = str(victim) if absolute else "../../victim.txt"
    input_path = tmp_path / "malicious.proto"
    input_path.write_text(f'syntax = "proto3";\nimport weak "{import_path}";\nmessage Example {{}}\n', encoding="utf-8")

    with pytest.raises(SchemaParseError, match="Invalid Protocol Buffers weak import path"):
        ProtobufParser(input_path)._compile_descriptor_set()

    assert victim.read_text(encoding="utf-8") == "preserve me"


def test_main_protobuf_invalid_schema_version(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Report invalid Protocol Buffers schema version values through the CLI."""
    run_main_and_assert(
        input_path=PROTOBUF_DATA_PATH / "proto3_optional.proto",
        output_path=output_file,
        input_file_type="protobuf",
        extra_args=["--schema-version", "draft-07"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid Protobuf version",
        output_should_not_exist=True,
    )


def test_generate_api_protobuf_strict_schema_version_warning(output_file: Path) -> None:
    """Warn when explicit Protocol Buffers schema version conflicts with file syntax in strict mode."""
    run_generate_file_and_assert(
        input_path=PROTOBUF_DATA_PATH / "proto3_optional.proto",
        output_path=output_file,
        input_file_type=InputFileType.Protobuf,
        assert_func=assert_file_content,
        expected_file="proto3_optional_strict_proto2.py",
        schema_version="proto2",
        schema_version_mode="strict",
        expected_warnings=["declares proto3"],
    )


def test_main_protobuf_empty_directory_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, output_file: Path
) -> None:
    """Report an empty Protocol Buffers input directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    run_main_and_assert(
        input_path=empty_dir,
        output_path=output_file,
        input_file_type="protobuf",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="No .proto files found in input",
        output_should_not_exist=True,
    )


REFERENCED_STANDARD_CASES = ["nullvalue", "standard_enum", "standard_message", "unused_standard", "user_namespace"]
REFERENCED_STANDARD_INVALID = {"unused_standard": "user_namespace"}
REFERENCED_STANDARD_BACKENDS = [
    DataModelType.PydanticV2BaseModel,
    DataModelType.PydanticV2Dataclass,
    DataModelType.DataclassesDataclass,
    DataModelType.TypingTypedDict,
    DataModelType.MsgspecStruct,
]


@pytest.mark.parametrize("name", REFERENCED_STANDARD_CASES)
def test_native_referenced_standard_types(name: str) -> None:
    """Confirm actual protoc descriptors and ProtoJSON accept and reject the external cases."""
    path = DATA_PATH / "protobuf_referenced_standard" / f"{name}.proto"
    descriptors, _ = ProtobufParser(path)._compile_descriptor_set()
    pool = descriptor_pool.DescriptorPool()
    for descriptor in descriptors.file:
        pool.Add(descriptor)
    full_name = "google.protobuf.Payload" if name == "user_namespace" else "Payload"
    model = message_factory.GetMessageClass(pool.FindMessageTypeByName(full_name))
    payloads = DATA_PATH / "payloads/protobuf_referenced_standard"
    value = json_format.Parse((payloads / f"{name}_valid.json").read_text(), model())
    with pytest.raises(json_format.ParseError):
        json_format.Parse(
            (payloads / f"{REFERENCED_STANDARD_INVALID.get(name, name)}_invalid.json").read_text(), model()
        )
    if name == "nullvalue":
        json_format.Parse((payloads / "nullvalue_null.json").read_text(), model())
        assert_output(
            json.dumps(json_format.MessageToDict(value), indent=2) + "\n",
            EXPECTED_PROTOBUF_PATH / "referenced_standard/native_nullvalue.txt",
        )


@pytest.mark.parametrize("name", REFERENCED_STANDARD_CASES)
def test_referenced_standard_definition_order(name: str) -> None:
    """Materialize exactly the referenced closure and preserve defaults and comments."""
    path = DATA_PATH / "protobuf_referenced_standard" / f"{name}.proto"
    schema = convert_protobuf_schema_data(path.read_text(), base_path=path.parent)
    assert_output(json.dumps(schema, indent=2) + "\n", EXPECTED_PROTOBUF_PATH / "referenced_standard" / f"{name}.txt")


@pytest.mark.parametrize("name", REFERENCED_STANDARD_CASES)
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_referenced_standard_generation(name: str, strict: bool, entrypoint: str, output_file: Path) -> None:
    """Resolve real CLI/API references while preserving normal output and validation."""
    path = DATA_PATH / "protobuf_referenced_standard" / f"{name}.proto"
    expected = f"referenced_standard/{name}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=path,
            output_path=output_file,
            input_file_type="protobuf",
            extra_args=["--disable-timestamp", "--use-field-description", *(["--strict-refs"] if strict else [])],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=path,
            output_path=output_file,
            input_file_type=InputFileType.Protobuf,
            strict_refs=strict,
            disable_timestamp=True,
            use_field_description=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )
    class_name = "GoogleProtobufPayload" if name == "user_namespace" else "Payload"
    payloads = DATA_PATH / "payloads/protobuf_referenced_standard"
    with _generated_model(output_file, f"standard_{name}_{entrypoint}_{strict}", class_name) as model:
        validate = _model_json_validator(model)
        validate((payloads / f"{name}_valid.json").read_text())
        with pytest.raises(ValidationError):
            validate((payloads / f"{REFERENCED_STANDARD_INVALID.get(name, name)}_invalid.json").read_text())


@pytest.mark.parametrize("backend", REFERENCED_STANDARD_BACKENDS)
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_nullvalue_enum_backends(backend: DataModelType, strict: bool, entrypoint: str, output_file: Path) -> None:
    """Preserve enum names and backend null handling without claiming ProtoJSON serialization."""
    path = DATA_PATH / "protobuf_referenced_standard/nullvalue.proto"
    expected = f"referenced_standard/nullvalue_{backend.name}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=path,
            output_path=output_file,
            input_file_type="protobuf",
            extra_args=[
                "--disable-timestamp",
                "--output-model-type",
                backend.value,
                *(["--strict-refs"] if strict else []),
            ],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=path,
            output_path=output_file,
            input_file_type=InputFileType.Protobuf,
            strict_refs=strict,
            output_model_type=backend,
            disable_timestamp=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )
    payloads = DATA_PATH / "payloads/protobuf_referenced_standard"
    with _generated_model(output_file, f"nullvalue_{backend.name}_{entrypoint}_{strict}", "Payload") as model:
        valid = (payloads / "nullvalue_valid.json").read_text()
        invalid = (payloads / "nullvalue_invalid.json").read_text()
        null = (payloads / "nullvalue_null.json").read_text()
        if backend == DataModelType.MsgspecStruct:
            result = msgspec.json.decode(valid, type=model)
            with pytest.raises(msgspec.ValidationError):
                msgspec.json.decode(null, type=model)
            with pytest.raises(msgspec.ValidationError):
                msgspec.json.decode(invalid, type=model)
        else:
            validate = _model_json_validator(model)
            result = validate(valid)
            if backend == DataModelType.TypingTypedDict:
                with pytest.raises(ValidationError):
                    validate(null)
            else:
                validate(null)
            with pytest.raises(ValidationError):
                validate(invalid)
        value = result["value"] if isinstance(result, dict) else result.value
        assert_output(
            json.dumps(value.value if isinstance(value, Enum) else value) + "\n",
            EXPECTED_PROTOBUF_PATH / "referenced_standard/enum_value.txt",
        )
