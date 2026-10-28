"""Runtime and output coverage for structured defaults through msgspec aliases."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import msgspec
import pytest
from typing_extensions import TypeAliasType

from datamodel_code_generator import DataModelType, InputFileType, PythonVersion
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_GRAPHQL_PATH,
    GRAPHQL_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("use_alias_type", [False, True])
@pytest.mark.parametrize(
    "target",
    [
        PythonVersion.PY_310,
        pytest.param(
            PythonVersion.PY_312,
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="Runtime requires Python type statements"),
        ),
    ],
)
def test_msgspec_alias_structured_defaults(
    entrypoint: str, target: PythonVersion, use_alias_type: bool, output_file: Path
) -> None:
    """Construct real Struct defaults through aliases and preserve explicit validation."""
    input_path = JSON_SCHEMA_DATA_PATH / "msgspec_alias_defaults.json"
    # Black 22 cannot format PEP 695 type statements; keep their native runtime coverage.
    suffix = "_type" if use_alias_type and target == PythonVersion.PY_310 else ""
    expected = f"msgspec_alias_defaults_{target.value.replace('.', '_')}{suffix}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=[
                "--output-model-type",
                "msgspec.Struct",
                "--use-type-alias",
                "--target-python-version",
                target.value,
                "--disable-timestamp",
                *(["--formatters", "builtin"] if target == PythonVersion.PY_312 else []),
                *(["--use-type-alias-type"] if use_alias_type else []),
            ],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file=expected,
            output_model_type=DataModelType.MsgspecStruct,
            use_type_alias=True,
            use_type_alias_type=use_alias_type,
            target_python_version=target,
            disable_timestamp=True,
            **({"formatters": [Formatter.BUILTIN]} if target == PythonVersion.PY_312 else {}),
        )

    payloads = json.loads((DATA_PATH / "payloads" / "msgspec_alias_defaults_runtime.json").read_text())
    with _generated_model(output_file, "msgspec_alias_defaults_runtime", "AliasDefaults") as model:
        if use_alias_type and target == PythonVersion.PY_310 and TypeAliasType.__module__ == "typing_extensions":
            # msgspec supports native aliases, but not the unchanged typing_extensions backport.
            with pytest.raises(TypeError, match="Type 'ItemAlias' is not supported"):
                msgspec.convert(payloads["default"], type=model)
            return
        first = msgspec.convert(payloads["default"], type=model)
        second = msgspec.convert(payloads["default"], type=model)
        explicit = msgspec.convert(payloads["explicit"], type=model)
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(payloads["invalid"], type=model)
        actual = {
            "defaults": msgspec.to_builtins(first),
            "object_types": {name: type(getattr(first, name)).__name__ for name in payloads["objects"]},
            "list_item_types": {
                name: [type(value).__name__ for value in getattr(first, name)] for name in payloads["lists"]
            },
            "explicit": msgspec.to_builtins(explicit),
        }
        first.aliased.id = 99
        first.alias_list[0].id = 99
        first.empty_alias_list.append(first.aliased)
        actual["independent_defaults"] = msgspec.to_builtins(second)
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_JSON_SCHEMA_PATH / "msgspec_alias_defaults_runtime.txt")


@pytest.mark.parametrize(
    "output_model_type",
    [DataModelType.MsgspecStruct, DataModelType.PydanticV2BaseModel, DataModelType.DataclassesDataclass],
)
def test_msgspec_alias_default_controls(output_model_type: DataModelType, output_file: Path) -> None:
    """Keep direct refs, primitive aliases, empty factories and other backends byte-identical."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_alias_default_controls.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file=f"msgspec_alias_default_controls_{output_model_type.value.replace('.', '_')}.py",
        output_model_type=output_model_type,
        use_type_alias=True,
        target_python_version=PythonVersion.PY_310,
        disable_timestamp=True,
    )


def test_msgspec_recursive_alias_default(output_file: Path) -> None:
    """Keep recursive non-model alias defaults unchanged without looping during generation."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_alias_defaults_recursive.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="msgspec_alias_defaults_recursive.py",
        output_model_type=DataModelType.MsgspecStruct,
        use_type_alias=True,
        target_python_version=PythonVersion.PY_310,
        disable_timestamp=True,
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("use_alias_type", [False, True])
def test_msgspec_graphql_scalar_alias_defaults(entrypoint: str, use_alias_type: bool, output_file: Path) -> None:
    """Scalar aliases with no model fields retain their original default factories."""
    expected = EXPECTED_GRAPHQL_PATH / f"msgspec_scalar_alias_defaults_{use_alias_type}.py"
    input_path = GRAPHQL_DATA_PATH / "msgspec_scalar_alias_defaults.graphql"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="graphql",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=[
                "--output-model-type",
                "msgspec.Struct",
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                *(["--use-type-alias-type"] if use_alias_type else []),
            ],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.GraphQL,
            assert_func=assert_file_content,
            expected_file=expected,
            output_model_type=DataModelType.MsgspecStruct,
            target_python_version=PythonVersion.PY_310,
            use_type_alias_type=use_alias_type,
            disable_timestamp=True,
        )
    payloads = json.loads((DATA_PATH / "payloads" / "msgspec_scalar_alias_defaults.json").read_text())
    with _generated_model(output_file, "msgspec_scalar_alias_defaults_runtime", "ScalarDefaults") as model:
        if use_alias_type and TypeAliasType.__module__ == "typing_extensions":
            with pytest.raises(TypeError, match="Type 'Int' is not supported"):
                msgspec.convert(payloads["default"], type=model)
            return
        default = msgspec.convert(payloads["default"], type=model)
        explicit = msgspec.convert(payloads["explicit"], type=model)
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(payloads["invalid"], type=model)
        actual = {"defaults": msgspec.to_builtins(default), "explicit": msgspec.to_builtins(explicit)}
    assert_output(
        f"{json.dumps(actual, indent=2)}\n", EXPECTED_GRAPHQL_PATH / "msgspec_scalar_alias_defaults_runtime.txt"
    )
