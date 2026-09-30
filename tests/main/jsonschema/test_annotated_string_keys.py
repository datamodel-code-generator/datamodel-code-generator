"""Preserve annotated dictionary-key constraints through public entrypoints."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, Formatter, InputFileType, PythonVersion
from datamodel_code_generator.format import CodeFormatter
from datamodel_code_generator.model.pydantic_v2.version import PYDANTIC_VERSION_TUPLE
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_validation,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

PAYLOADS = DATA_PATH / "payloads/annotated_string_keys"


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatter", ["builtin", "external"])
@pytest.mark.parametrize(
    ("case", "backend"),
    [
        pytest.param(
            case,
            backend,
            id=f"{case['name']}_{backend}",
            marks=pytest.mark.skipif(
                tuple(case.get("minimum_pydantic_version", [0, 0, 0])) > PYDANTIC_VERSION_TUPLE,
                reason="Installed Pydantic does not support this schema or target version",
            ),
        )
        for case in json.loads((PAYLOADS / "cases.json").read_text())
        for backend in case.get("backends", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
    ],
)
def test_annotated_string_keys(
    output_file: Path, entrypoint: str, formatter: str, case: dict[str, Any], backend: str
) -> None:
    """Check exact output and native constraints without mocking code generation."""
    source = JSON_SCHEMA_DATA_PATH / "annotated_string_keys" / f"{case['schema']}.json"
    expected = f"annotated_string_keys/{case['name']}_{backend}_{formatter}.py"
    if PYDANTIC_VERSION_TUPLE < (2, 1, 0):
        expected = f"pydantic20/{expected}"
    formatters = ["builtin"] if formatter == "builtin" else ["black", "isort"]
    options: dict[str, Any] = {"field_constraints": True, "use_field_description": True, **case["options"]}
    match entrypoint:
        case "cli":
            args = ["--output-model-type", backend, "--disable-timestamp", "--formatters", *formatters]
            for key, value in options.items():
                if value is False:
                    continue
                args.append("--" + key.replace("_", "-"))
                match value:
                    case list():
                        args.extend(value)
                    case True:
                        continue
                    case _:
                        args.append(str(value))
            run_main_and_assert(
                input_path=source,
                output_path=output_file,
                input_file_type="jsonschema",
                extra_args=args,
                assert_func=assert_file_content,
                expected_file=expected,
                force_exec_validation=True,
            )
        case _:
            run_generate_file_and_assert(
                input_path=source,
                output_path=output_file,
                input_file_type=InputFileType.JsonSchema,
                output_model_type=DataModelType(backend),
                disable_timestamp=True,
                formatters=[Formatter(value) for value in formatters],
                assert_func=assert_file_content,
                expected_file=expected,
                **options,
            )
    payloads = json.loads((PAYLOADS / f"{case['schema']}.json").read_text())
    for invalid in payloads["invalid"]:
        assert_generated_model_json_validation(
            output_file,
            module_name="annotated_string_keys",
            model_name="SomeSpec",
            valid_json=json.dumps(invalid.get("valid", payloads["valid"])),
            invalid_json=json.dumps(invalid["value"]),
            expected_error_type=invalid.get(f"{backend}_error", invalid["error"]),
        )


@pytest.mark.parametrize("formatters", [[Formatter.BUILTIN], [Formatter.BLACK, Formatter.ISORT]])
def test_annotated_alias_formatting(output_file: Path, formatters: list[Formatter]) -> None:
    """Keep aliased annotations loadable across compact, expanded and union layouts."""
    source = (DATA_PATH / "python/annotated_string_key_aliases.py").read_text()
    formatter = CodeFormatter(PythonVersion.PY_310, formatters=formatters, builtin_format_line_length=88)
    output_file.write_text(formatter.format_code(source), encoding="utf-8")
    assert_file_content(output_file, "annotated_string_keys/formatter_alias.py")
    payloads = json.loads((PAYLOADS / "formatter_alias.json").read_text())
    for invalid in payloads["invalid"]:
        assert_generated_model_json_validation(
            output_file,
            module_name="annotated_alias_formatting",
            model_name="SomeSpec",
            valid_json=json.dumps(payloads["valid"]),
            invalid_json=json.dumps(invalid["value"]),
            expected_error_type=invalid["error"],
        )
