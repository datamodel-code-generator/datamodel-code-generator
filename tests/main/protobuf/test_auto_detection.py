"""Exercise declaration detection through generation and the Protobuf compiler."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import msgspec
import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from grpc_tools import protoc
from pydantic import TypeAdapter

from datamodel_code_generator import DataModelType, Error, InputFileType, generate, infer_input_type
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_PROTOBUF_PATH,
    _generated_model,
    assert_input_file_type,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.protobuf.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path

PROTOBUF_AUTO_PATH = DATA_PATH / "protobuf_auto_detection"
SCHEMAS = tuple(path.stem for path in sorted(PROTOBUF_AUTO_PATH.glob("*.proto")))
PAYLOAD_PATH = DATA_PATH / "payloads" / "protobuf_auto_detection"
CONTROLS = json.loads((PAYLOAD_PATH / "controls.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("input_type", [InputFileType.Auto, InputFileType.Protobuf])
@pytest.mark.parametrize("entry", ["cli", "path", "text"])
def test_protobuf_auto_generation(
    schema: str,
    backend: DataModelType,
    input_type: InputFileType,
    entry: str,
    output_file: Path,
) -> None:
    """Auto and explicit inputs retain identical Python bytes and runtime values."""
    input_path = PROTOBUF_AUTO_PATH / f"{schema}.proto"
    expected = f"auto_detection/{schema}_{backend.name}.py"
    options = {"custom_file_header": "# Protobuf detection regression", "output_model_type": backend}
    match entry:
        case "cli":
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=input_type.value,
                extra_args=[
                    "--custom-file-header",
                    "# Protobuf detection regression",
                    "--output-model-type",
                    backend.value,
                ],
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
            run_generate_and_assert(
                input_=input_path.read_text(encoding="utf-8"),
                input_file_type=input_type,
                expected_file=EXPECTED_PROTOBUF_PATH / expected.replace(".py", "_text.py"),
                **options,
            )
            return
    payload = json.loads((PAYLOAD_PATH / "runtime.txt").read_text(encoding="utf-8"))
    with _generated_model(output_file, "protobuf_auto", "M") as model:
        if backend == DataModelType.MsgspecStruct:
            result = msgspec.to_builtins(msgspec.convert(payload, type=model))
        else:
            adapter = TypeAdapter(model)
            result = adapter.dump_python(adapter.validate_python(payload))
        assert_output(
            json.dumps(result, indent=2) + "\n", DATA_PATH / "expected/payloads/protobuf_auto_detection/runtime.txt"
        )


@pytest.mark.parametrize("schema", SCHEMAS)
def test_protobuf_auto_compiler_oracle(schema: str, tmp_path: Path) -> None:
    """Compile each original source directly without generator preprocessing."""
    input_path = PROTOBUF_AUTO_PATH / f"{schema}.proto"
    output_path = tmp_path / "schema.pb"
    result = protoc.main([
        "protoc",
        f"--proto_path={input_path.parent}",
        f"--descriptor_set_out={output_path}",
        str(input_path),
    ])
    assert_output(str(result), EXPECTED_PROTOBUF_PATH / "auto_detection" / "protoc_exit.txt")
    descriptor = FileDescriptorSet.FromString(output_path.read_bytes()).file[0]
    assert_output(
        json.dumps(
            {
                "syntax": descriptor.syntax,
                "edition": descriptor.edition,
                "messages": {
                    message.name: [[field.name, field.default_value] for field in message.field]
                    for message in descriptor.message_type
                },
            },
            indent=2,
        )
        + "\n",
        EXPECTED_PROTOBUF_PATH / "auto_detection" / f"{schema}_descriptor.txt",
    )


@pytest.mark.parametrize("control", list(CONTROLS))
@pytest.mark.parametrize("input_type", [InputFileType.Auto, None])
@pytest.mark.parametrize("entry", ["cli", "path", "text"])
def test_protobuf_auto_other_formats(
    control: str, input_type: InputFileType | None, entry: str, output_file: Path
) -> None:
    """Declaration-like data in JSON, YAML and CSV retains its previous format."""
    input_path = PAYLOAD_PATH / f"{control}.txt"
    expected_type = InputFileType(CONTROLS[control])
    assert_input_file_type(infer_input_type(input_path.read_text(encoding="utf-8")), expected_type)
    selected_type = input_type or expected_type
    expected = f"auto_detection/control_{control}.py"
    match entry:
        case "cli":
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=selected_type.value,
                extra_args=["--custom-file-header", "# Protobuf detection regression"],
                assert_func=assert_file_content,
                expected_file=expected,
            )
        case "path":
            run_generate_file_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=selected_type,
                custom_file_header="# Protobuf detection regression",
                assert_func=assert_file_content,
                expected_file=expected,
            )
        case _:
            run_generate_and_assert(
                input_=input_path.read_text(encoding="utf-8"),
                input_file_type=selected_type,
                custom_file_header="# Protobuf detection regression",
                expected_file=EXPECTED_PROTOBUF_PATH / expected.replace(".py", "_text.py"),
            )


@pytest.mark.parametrize("schema", ["plain", "comments", "single", "edition_single"])
def test_protobuf_auto_inference_imports(schema: str) -> None:
    """Declaration inference does not load the compiler, parsers or model backends."""
    result = subprocess.check_output(
        [
            sys.executable,
            str(PAYLOAD_PATH / "infer_imports.py"),
            str(PROTOBUF_AUTO_PATH / f"{schema}.proto"),
        ],
        text=True,
    )
    assert_output(result, EXPECTED_PROTOBUF_PATH / "auto_detection" / "inference_imports.txt")


@pytest.mark.parametrize("schema", ["invalid_yaml", "unknown_scalar"])
@pytest.mark.parametrize("entry", ["cli", "path", "text"])
def test_protobuf_auto_preserves_inference_errors(
    schema: str, entry: str, output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unrecognized text still reports the existing inference error without compiling."""
    input_path = PAYLOAD_PATH / f"{schema}.txt"
    if entry == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="auto",
            expected_exit=Exit.ERROR,
            output_should_not_exist=True,
            capsys=capsys,
            expected_stderr_contains="Can't infer input file type",
        )
    else:
        with pytest.raises(Error, match="Can't infer input file type"):
            generate(
                input_path if entry == "path" else input_path.read_text(encoding="utf-8"),
                input_file_type=InputFileType.Auto,
            )
