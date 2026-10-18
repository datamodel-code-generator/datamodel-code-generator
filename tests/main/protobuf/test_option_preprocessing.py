"""Check lexical option preprocessing against the original Protobuf descriptors."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import grpc_tools
import msgspec
import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from grpc_tools import protoc
from pydantic import TypeAdapter

from datamodel_code_generator import DataModelType, InputFileType
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_output, stored_expected
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_PROTOBUF_PATH,
    _generated_model,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.protobuf.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_PATH = DATA_PATH / "protobuf_option_preprocessing"
BUILTIN_FORMATTER_CONFIG = DATA_PATH / "templates/builtin_boolean_wrapping/88.toml"
EXPECTED_PATH = EXPECTED_PROTOBUF_PATH / "option_preprocessing"
SCHEMAS = ["plain", "comments", "single", "double", "custom", "rpc"]


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("input_type", [InputFileType.Auto, InputFileType.Protobuf])
@pytest.mark.parametrize("entry", ["cli", "path", "text"])
def test_protobuf_option_generation(
    schema: str,
    backend: DataModelType,
    input_type: InputFileType,
    entry: str,
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve defaults, descriptions, and declaration order across entry points."""
    input_path = SOURCE_PATH / f"{schema}.proto"
    expected = f"option_preprocessing/{schema}_{backend.name}.py"
    options: dict[str, Any] = {
        "custom_file_header": "# Protobuf option regression",
        "output_model_type": backend,
        "use_field_description": True,
        "field_constraints": True,
        "use_annotated": True,
    }
    formatter_args = []
    if schema == "comments" and backend == DataModelType.MsgspecStruct:
        options.update(formatters=[Formatter.BUILTIN], builtin_format_line_length=88)
        formatter_args = ["--formatters", "builtin"]
    match entry:
        case "cli":
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=input_type.value,
                extra_args=[
                    "--custom-file-header",
                    "# Protobuf option regression",
                    "--output-model-type",
                    backend.value,
                    "--use-field-description",
                    "--field-constraints",
                    "--use-annotated",
                    *formatter_args,
                ],
                copy_files=[(BUILTIN_FORMATTER_CONFIG, output_file.parent / "pyproject.toml")]
                if formatter_args
                else None,
                assert_func=assert_file_content,
                expected_file=expected,
            )
        case "path":
            run_generate_file_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=input_type,
                assert_func=assert_file_content,
                expected_file=expected,
                **options,
            )
        case _:
            monkeypatch.chdir(SOURCE_PATH)
            run_generate_and_assert(
                input_=input_path.read_text(),
                input_file_type=input_type,
                expected_file=EXPECTED_PROTOBUF_PATH / expected.replace(".py", "_text.py"),
                **options,
            )
            return
    suffix = "unset_runtime" if backend in {DataModelType.TypingTypedDict, DataModelType.MsgspecStruct} else "runtime"
    runtime_path = EXPECTED_PATH / f"{schema}_{suffix}.txt"
    payload = json.loads(stored_expected(runtime_path).read_text()) if backend == DataModelType.TypingTypedDict else {}
    with _generated_model(output_file, "protobuf_options", "Payload") as model:
        if backend == DataModelType.MsgspecStruct:
            result = msgspec.to_builtins(msgspec.convert(payload, type=model))
        else:
            adapter = TypeAdapter(model)
            result = adapter.dump_python(adapter.validate_python(payload))
        assert_output(json.dumps(result, indent=2) + "\n", runtime_path)


@pytest.mark.parametrize("schema", SCHEMAS)
def test_protobuf_option_compiler_oracle(schema: str, tmp_path: Path) -> None:
    """Compile the untouched inputs with real protoc and record semantic defaults."""
    from pathlib import Path

    input_path = SOURCE_PATH / f"{schema}.proto"
    output_path = tmp_path / "schema.pb"
    result = protoc.main([
        "protoc",
        f"--proto_path={SOURCE_PATH}",
        f"--proto_path={Path(cast('str', grpc_tools.__file__)).parent / '_proto'}",
        f"--descriptor_set_out={output_path}",
        "--include_source_info",
        str(input_path),
    ])
    assert_output(str(result), EXPECTED_PATH / "protoc_exit.txt")
    descriptor = FileDescriptorSet.FromString(output_path.read_bytes()).file[0]
    if schema == "rpc":
        assert_output(
            json.dumps({service.name: [method.name for method in service.method] for service in descriptor.service})
            + "\n",
            EXPECTED_PATH / "rpc_services.txt",
        )
    assert_output(
        json.dumps(
            {
                "messages": {
                    message.name: [[field.name, field.default_value] for field in message.field]
                    for message in descriptor.message_type
                },
                "comments": [
                    [list(location.path), location.leading_comments, location.trailing_comments]
                    for location in descriptor.source_code_info.location
                    if location.leading_comments or location.trailing_comments
                ],
            },
            indent=2,
        )
        + "\n",
        EXPECTED_PATH / f"{schema}_descriptor.txt",
    )


@pytest.mark.parametrize("schema", ["field", "statement"])
def test_protobuf_incomplete_options(schema: str, output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Leave incomplete options for the compiler to reject through the CLI."""
    run_main_and_assert(
        input_path=SOURCE_PATH / "invalid" / f"{schema}.proto",
        output_path=output_file,
        input_file_type="protobuf",
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stderr_contains="Invalid Protocol Buffers schema",
    )
