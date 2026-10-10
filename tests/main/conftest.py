"""Shared fixtures and utilities for main integration tests."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import shutil
import sys
import textwrap
import time
import warnings
from argparse import Namespace
from collections.abc import Callable, Collection, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal, get_type_hints

import black
import pytest
from packaging import version
from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import TypeAdapter, ValidationError
from pydantic.errors import PydanticUndefinedAnnotation

from datamodel_code_generator import (
    DataModelType,
    GenerateConfig,
    InputFileType,
    TargetPydanticVersion,
    enable_parsed_source_cache,
    generate,
)
from datamodel_code_generator.__main__ import Exit, main
from datamodel_code_generator.arguments import arg_parser
from datamodel_code_generator.format import Formatter, PythonVersion, is_supported_in_black
from tests.conftest import (
    AssertFileContent,
    _infer_expected_file,
    _validation_stats,
    assert_directory_content,
    assert_generate_wrote_file,
    assert_inputs_not_mutated,
    assert_output,
    assert_warnings_contain,
    freeze_time,
    validate_generated_code,
)
from tests.main._builtin_parity import (
    _assert_builtin_cli_formatter_parity,
    _assert_builtin_generate_formatter_parity,
    _BuiltinCliFormatterParityContext,
)

InputFileTypeLiteral = Literal[
    "auto",
    "openapi",
    "asyncapi",
    "jsonschema",
    "mcp-tools",
    "xmlschema",
    "protobuf",
    "avro",
    "json",
    "yaml",
    "dict",
    "csv",
    "graphql",
]
CopyFilesMapping = Sequence[tuple[Path, Path]]

_TEST_DEFAULT_FORMATTER_ENV = "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER"
_BUILTIN_FORMATTER_VALUE = "builtin"
_BUILTIN_FORMATTER_LINE_LENGTH = 88
_BUILTIN_FORMATTER_CONFIG = "[tool.datamodel-codegen]\nbuiltin-format-line-length = 88\n"
_CLI_FORMATTER_RELATED_OPTIONS = frozenset({
    "--custom-formatters",
    "--custom-formatters-kwargs",
    "--formatters",
    "--profile",
    "--skip-string-normalization",
    "--use-double-quotes",
    "--wrap-string-literal",
})
_NON_GENERATION_CLI_OPTIONS = frozenset({
    "--debug",
    "--generate-cli-command",
    "--generate-prompt",
    "--generate-pyproject-config",
    "--help",
    "--list-deprecations",
    "--list-experimental",
    "--output-format",
    "--output-format-json-schema",
    "--version",
    "--watch",
})
_API_FORMATTER_RELATED_OPTIONS = frozenset({
    "builtin_format_line_length",
    "config",
    "custom_formatters",
    "custom_formatters_kwargs",
    "formatters",
    "settings_path",
    "use_double_quotes",
    "use_type_checking_imports",
    "wrap_string_literal",
})


def _uses_builtin_test_default_formatter() -> bool:
    return os.environ.get(_TEST_DEFAULT_FORMATTER_ENV) == _BUILTIN_FORMATTER_VALUE


def _uses_external_test_default_formatter() -> bool:
    return not _uses_builtin_test_default_formatter()


MSGSPEC_LEGACY_BLACK_SKIP = pytest.mark.skipif(
    _uses_external_test_default_formatter()
    and sys.version_info[:2] == (3, 12)
    and version.parse(black.__version__) < version.parse("24.0.0"),
    reason="msgspec.Struct formatting differs with python3.12 + black < 24",
)

LEGACY_BLACK_SKIP = pytest.mark.skipif(
    _uses_external_test_default_formatter() and version.parse(black.__version__) < version.parse("24.0.0"),
    reason="Type annotation formatting differs with black < 24",
)

BLACK_PY313_SKIP = pytest.mark.skipif(
    _uses_external_test_default_formatter() and not is_supported_in_black(PythonVersion.PY_313),
    reason=f"Installed black ({black.__version__}) doesn't support Python 3.13",
)

BLACK_PY314_SKIP = pytest.mark.skipif(
    _uses_external_test_default_formatter() and not is_supported_in_black(PythonVersion.PY_314),
    reason=f"Installed black ({black.__version__}) doesn't support Python 3.14",
)

BACKEND_GOLDEN_TARGET_ARGS = ("--target-python-version", "3.10")
BACKEND_GOLDEN_CASES = (
    pytest.param(DataModelType.PydanticV2BaseModel.value, "pydantic_v2_BaseModel", id="pydantic-v2"),
    pytest.param(DataModelType.DataclassesDataclass.value, "dataclasses_dataclass", id="dataclass"),
    pytest.param(DataModelType.TypingTypedDict.value, "typing_TypedDict", id="typed-dict"),
    pytest.param(
        DataModelType.MsgspecStruct.value,
        "msgspec_Struct",
        id="msgspec",
        marks=MSGSPEC_LEGACY_BLACK_SKIP,
    ),
)

CURRENT_PYTHON_VERSION = f"{sys.version_info[0]}.{sys.version_info[1]}"
"""Current Python version as string (e.g., '3.13')."""

TARGET_PYDANTIC_VERSION_CASES = (
    pytest.param(None, id="target-unset"),
    pytest.param("2", id="target-2"),
    pytest.param("2.12", id="target-2.12"),
)


def target_pydantic_args(target_pydantic_version: str | None) -> list[str]:
    """Return CLI arguments selecting a target Pydantic version, or none for the default."""
    return [] if target_pydantic_version is None else ["--target-pydantic-version", target_pydantic_version]


def target_pydantic_expected_suffix(target_pydantic_version: str | None) -> str:
    """Name the expected output for a target; an unset target shares the newest target's output."""
    return "target_2" if target_pydantic_version == "2" else "target_2_12"


UNSET_TARGET_PYDANTIC_VERSION = TargetPydanticVersion.V2_12.value
"""The oldest Pydantic an unset target runs on: the newest forms, but MISSING keeps its experimental import."""

EXPERIMENTAL_MISSING_IMPORT_WARNING = pytest.mark.filterwarnings(
    "ignore:The `MISSING` sentinel is no longer experimental:DeprecationWarning"
)
"""Allow Pydantic 2.14's deprecation of the experimental MISSING import that targets below 2.14 generate."""


def installed_pydantic_runs_target(target_pydantic_version: str | None) -> bool:
    """Return whether the installed Pydantic can import code generated for the target."""
    return version.parse(PYDANTIC_VERSION) >= version.parse(target_pydantic_version or UNSET_TARGET_PYDANTIC_VERSION)


DATA_PATH: Path = Path(__file__).parent.parent / "data"
EXPECTED_MAIN_PATH: Path = DATA_PATH / "expected" / "main"

PYTHON_DATA_PATH: Path = DATA_PATH / "python"
OPEN_API_DATA_PATH: Path = DATA_PATH / "openapi"
ASYNC_API_DATA_PATH: Path = DATA_PATH / "asyncapi"
JSON_SCHEMA_DATA_PATH: Path = DATA_PATH / "jsonschema"
GRAPHQL_DATA_PATH: Path = DATA_PATH / "graphql"
XML_SCHEMA_DATA_PATH: Path = DATA_PATH / "xmlschema"
PROTOBUF_DATA_PATH: Path = DATA_PATH / "protobuf"
AVRO_DATA_PATH: Path = DATA_PATH / "avro"
JSON_DATA_PATH: Path = DATA_PATH / "json"
CSV_DATA_PATH: Path = DATA_PATH / "csv"
YAML_DATA_PATH: Path = DATA_PATH / "yaml"
ALIASES_DATA_PATH: Path = DATA_PATH / "aliases"
DEFAULT_VALUES_DATA_PATH: Path = DATA_PATH / "default_values"

EXPECTED_OPENAPI_PATH: Path = EXPECTED_MAIN_PATH / "openapi"
EXPECTED_ASYNC_API_PATH: Path = EXPECTED_MAIN_PATH / "asyncapi"
EXPECTED_JSON_SCHEMA_PATH: Path = EXPECTED_MAIN_PATH / "jsonschema"
EXPECTED_GRAPHQL_PATH: Path = EXPECTED_MAIN_PATH / "graphql"
EXPECTED_XML_SCHEMA_PATH: Path = EXPECTED_MAIN_PATH / "xmlschema"
EXPECTED_PROTOBUF_PATH: Path = EXPECTED_MAIN_PATH / "protobuf"
EXPECTED_AVRO_PATH: Path = EXPECTED_MAIN_PATH / "avro"
EXPECTED_JSON_PATH: Path = EXPECTED_MAIN_PATH / "json"
EXPECTED_CSV_PATH: Path = EXPECTED_MAIN_PATH / "csv"

TIMESTAMP = "1985-10-26T01:21:00-07:00"
DEFAULT_FREEZE_TIME = "2019-07-26"


@pytest.fixture(autouse=True)
def reset_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset argument namespace before each test."""
    # Template caches are safe to keep because custom-template tests use fixed directories and keyed caches.
    namespace_ = Namespace(no_color=False)
    monkeypatch.setattr("datamodel_code_generator.__main__.namespace", namespace_)
    monkeypatch.setattr("datamodel_code_generator.arguments.namespace", namespace_)


@pytest.fixture(autouse=True)
def auto_freeze_time() -> Generator[None, None, None]:
    """Auto-freeze time for all tests in main/ directory."""
    with freeze_time(DEFAULT_FREEZE_TIME):
        yield


@pytest.fixture
def output_file(tmp_path: Path) -> Path:
    """Return standard output file path."""
    return tmp_path / "output.py"


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Return standard output directory path."""
    return tmp_path / "model"


@pytest.fixture
def extreme_large_schema(tmp_path: Path) -> Path:  # pragma: no cover - perf-only fixture
    """Generate a deterministic large schema with 2000 models."""
    definitions: dict[str, object] = {}
    schema: dict[str, object] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ExtremeLargeSchema",
        "definitions": definitions,
    }
    for i in range(2000):
        definitions[f"Model{i:04d}"] = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "value": {"type": "number"},
                "active": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
                "ref_prev": {"$ref": f"#/definitions/Model{max(0, i - 1):04d}"},
            },
            "required": ["id", "name"],
        }
    schema["$ref"] = "#/definitions/Model1999"

    schema_file = tmp_path / "extreme_large.json"
    schema_file.write_text(json.dumps(schema), encoding="utf-8")
    return schema_file


def get_current_version_args(*extra_args: str) -> list[str]:
    """Create CLI args list with --target-python-version set to current version.

    This is a convenience function for tests that want to use the current
    Python version to enable exec() validation.

    Example:
        run_main_and_assert(
            ...,
            extra_args=get_current_version_args("--use-field-description"),
        )
    """
    return ["--target-python-version", CURRENT_PYTHON_VERSION, *extra_args]


def _copy_files(copy_files: CopyFilesMapping | None) -> None:
    """Copy files from source to destination paths."""
    if copy_files is None:
        return
    for src, dst in copy_files:
        shutil.copy(src, dst)


def _assert_exit_code(return_code: Exit, expected_exit: Exit, context: str) -> None:
    """Assert exit code matches expected value."""
    if return_code != expected_exit:  # pragma: no cover
        pytest.fail(f"Expected exit code {expected_exit!r}, got {return_code!r}\n{context}")


def _assert_captured_output(
    capsys: pytest.CaptureFixture[str] | None,
    *,
    expected_stdout_path: Path | None = None,
    expected_stderr: str | None = None,
    expected_stderr_contains: str | None = None,
    assert_no_stderr: bool = False,
) -> None:
    if not any((
        expected_stdout_path is not None,
        expected_stderr is not None,
        expected_stderr_contains is not None,
        assert_no_stderr,
    )):
        return
    if capsys is None:  # pragma: no cover
        pytest.fail("capsys is required when captured output assertions are set")
    captured = capsys.readouterr()
    if expected_stdout_path is not None:
        assert_output(captured.out, expected_stdout_path)
    if expected_stderr is not None and captured.err != expected_stderr:  # pragma: no cover
        pytest.fail(f"Expected stderr:\n{expected_stderr}\n\nActual stderr:\n{captured.err}")
    if expected_stderr_contains is not None and expected_stderr_contains not in captured.err:  # pragma: no cover
        pytest.fail(f"Expected stderr to contain: {expected_stderr_contains!r}\n\nActual stderr:\n{captured.err}")
    if assert_no_stderr and captured.err:  # pragma: no cover
        pytest.fail(f"Expected no stderr, but got:\n{captured.err}")


def _assert_file_does_not_exist(path: Path) -> None:
    if path.exists():  # pragma: no cover
        pytest.fail(f"File should not exist: {path}")


def _assert_python_module_importable(path: Path, module_name: str, attribute: str | None = None) -> None:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:  # pragma: no cover
        pytest.fail(f"Unable to load generated module from {path}")
    if spec.loader is None:  # pragma: no cover
        pytest.fail(f"Unable to load generated module loader from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        if attribute is not None and not hasattr(module, attribute):  # pragma: no cover
            pytest.fail(f"Expected generated module {module_name!r} to define {attribute!r}")
    finally:
        _forget_generated_package(module_name)


def _forget_generated_package(package_name: str) -> dict[str, Any]:
    """Remove a generated package and every module below it from the module cache, returning what it removed."""
    module_prefix = f"{package_name}."
    return {
        name: sys.modules.pop(name)
        for name in [name for name in sys.modules if name == package_name or name.startswith(module_prefix)]
    }


@contextmanager
def _generated_package_module(output_path: Path, module_path: str) -> Generator[Any, None, None]:
    """Temporarily import a generated package module without leaking module cache state."""
    package_name = output_path.name
    module_name = f"{package_name}.{module_path}" if module_path else package_name
    previous_modules = _forget_generated_package(package_name)

    parent_directory = str(output_path.parent)
    sys.path.insert(0, parent_directory)
    importlib.invalidate_caches()
    try:
        yield importlib.import_module(module_name)
    finally:
        sys.path.remove(parent_directory)
        _forget_generated_package(package_name)
        sys.modules.update(previous_modules)


def _assert_generated_package_model_validation(
    output_path: Path,
    *,
    module_path: str,
    model_name: str,
    data: Mapping[str, Any],
) -> None:
    """Assert a generated package model can validate runtime data."""
    with _generated_package_module(output_path, module_path) as module:
        model = getattr(module, model_name)
        validate = getattr(model, "model_validate", None)
        if not callable(validate):  # pragma: no cover
            pytest.fail(f"Expected generated model {model_name!r} to define model_validate")
        validate(data)


@cache
def _get_valid_cli_options() -> frozenset[str]:
    """Get all valid CLI option names from arg_parser."""
    valid_options: set[str] = set()
    for action in arg_parser._actions:
        valid_options.update(action.option_strings)
    return frozenset(valid_options)


def _validate_extra_args(extra_args: Sequence[str] | None) -> None:
    """Validate that all option-like arguments in extra_args are valid CLI options."""
    if extra_args is None:
        return
    valid_cli_options = _get_valid_cli_options()
    invalid_args: list[str] = [
        arg
        for arg in extra_args
        if (
            (arg.startswith("--") and "=" not in arg)
            or (arg.startswith("-") and not arg.startswith("--") and len(arg) == 2)
        )
        and arg not in valid_cli_options
    ]
    if invalid_args:  # pragma: no cover
        pytest.fail(f"Invalid CLI options in extra_args: {invalid_args}. Valid options: {sorted(valid_cli_options)}")


def _has_formatter_related_cli_options(args: Sequence[str]) -> bool:
    return any(arg.split("=", maxsplit=1)[0] in _CLI_FORMATTER_RELATED_OPTIONS for arg in args)


def _has_formatter_related_copy_files(copy_files: CopyFilesMapping | None) -> bool:
    return copy_files is not None and any(dst.name == "pyproject.toml" for _, dst in copy_files)


def _has_formatter_related_settings_path(output_path: Path | None) -> bool:
    if output_path is None:
        return False
    settings_path = output_path if output_path.is_dir() else output_path.parent
    for path in (settings_path, *settings_path.parents):
        pyproject_toml = path / "pyproject.toml"
        if pyproject_toml.is_file():
            return True
    return False


def _builtin_default_formatter_config_path(output_path: Path) -> Path:
    if output_path.is_dir():
        return output_path / "pyproject.toml"
    return output_path.parent / "pyproject.toml"


@contextmanager
def _builtin_default_formatter_config(output_path: Path | None, *, enabled: bool) -> Generator[None]:
    if not enabled or output_path is None:
        yield
        return
    pyproject_toml = _builtin_default_formatter_config_path(output_path)
    if pyproject_toml.is_file():
        yield
        return
    pyproject_toml.write_text(_BUILTIN_FORMATTER_CONFIG, encoding="utf-8")
    try:
        yield
    finally:
        pyproject_toml.unlink(missing_ok=True)


def _should_use_builtin_default_cli_formatter(
    args: Sequence[str],
    *,
    copy_files: CopyFilesMapping | None = None,
    output_path: Path | None = None,
    is_generation_command: bool = True,
) -> bool:
    args_list = list(args)
    return not (
        not is_generation_command
        or not _uses_builtin_test_default_formatter()
        or _has_formatter_related_cli_options(args_list)
        or _has_formatter_related_copy_files(copy_files)
        or _has_formatter_related_settings_path(output_path)
    )


def _is_main_generation_command(args: Sequence[str]) -> bool:
    return any(arg.split("=", maxsplit=1)[0] in {"--input", "--url"} for arg in args) and not any(
        arg.split("=", maxsplit=1)[0] in _NON_GENERATION_CLI_OPTIONS for arg in args
    )


def _get_cli_output_path(args: Sequence[str]) -> Path | None:
    args_list = list(args)
    for index, arg in enumerate(args_list):
        if arg == "--output" and index + 1 < len(args_list):
            return Path(args_list[index + 1])
        if arg.startswith("--output="):
            return Path(arg.split("=", maxsplit=1)[1])
    return None


def _default_formatter_cli_args(
    args: Sequence[str],
    *,
    copy_files: CopyFilesMapping | None = None,
    output_path: Path | None = None,
    is_generation_command: bool = True,
) -> list[str]:
    args_list = list(args)
    if not _should_use_builtin_default_cli_formatter(
        args_list,
        copy_files=copy_files,
        output_path=output_path,
        is_generation_command=is_generation_command,
    ):
        return args_list
    return [*args_list, "--formatters", _BUILTIN_FORMATTER_VALUE]


def _default_formatter_generate_options(
    generate_kwargs: dict[str, Any], *, output_path: Path | None = None
) -> dict[str, Any]:
    output = output_path or generate_kwargs.get("output")
    if (
        not _uses_builtin_test_default_formatter()
        or any(key in generate_kwargs for key in _API_FORMATTER_RELATED_OPTIONS)
        or (isinstance(output, Path) and _has_formatter_related_settings_path(output))
    ):
        return generate_kwargs
    return {
        **generate_kwargs,
        "formatters": [Formatter.BUILTIN],
        "builtin_format_line_length": _BUILTIN_FORMATTER_LINE_LENGTH,
    }


@contextmanager
def _enable_test_parsed_source_cache() -> Generator[None, None, None]:
    restore = enable_parsed_source_cache()
    try:
        yield
    finally:
        restore()


@contextmanager
def _optional_test_parsed_source_cache(enabled: bool) -> Generator[None, None, None]:
    if not enabled:
        yield
        return
    with _enable_test_parsed_source_cache():
        yield


def _clear_model_template_cache() -> None:
    from datamodel_code_generator.model import base as model_base

    model_base._clear_custom_template_caches()
    for cached in (
        model_base.get_template,
        model_base._get_template_with_absolute_path,
        model_base._get_environment_with_absolute_path,
    ):
        cached.cache_clear()


@contextmanager
def _optional_model_template_cache_isolation(enabled: bool) -> Generator[None, None, None]:
    if not enabled:
        yield
        return
    _clear_model_template_cache()
    try:
        yield
    finally:
        _clear_model_template_cache()


def _extend_args(
    args: list[str],
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    input_file_type: InputFileTypeLiteral | None = None,
    extra_args: Sequence[str] | None = None,
    copy_files: CopyFilesMapping | None = None,
) -> bool:
    """Extend args with optional input_path, output_path, input_file_type and extra_args."""
    if input_path is not None:
        args.extend(["--input", str(input_path)])
    if output_path is not None:
        args.extend(["--output", str(output_path)])
    if input_file_type is not None:
        args.extend(["--input-file-type", input_file_type])
    _validate_extra_args(extra_args)
    if extra_args is not None:
        args.extend(extra_args)
    use_builtin_default = _should_use_builtin_default_cli_formatter(
        args,
        copy_files=copy_files,
        output_path=output_path,
    )
    if use_builtin_default:
        args.extend(("--formatters", _BUILTIN_FORMATTER_VALUE))
    return use_builtin_default


def _run_main(
    input_path: Path,
    output_path: Path,
    input_file_type: InputFileTypeLiteral | None = None,
    *,
    extra_args: Sequence[str] | None = None,
    copy_files: CopyFilesMapping | None = None,
) -> Exit:
    """Execute main() with standard arguments (internal use)."""
    _copy_files(copy_files)
    args: list[str] = []
    use_builtin_default = _extend_args(
        args,
        input_path=input_path,
        output_path=output_path,
        input_file_type=input_file_type,
        extra_args=extra_args,
        copy_files=copy_files,
    )
    with (
        _enable_test_parsed_source_cache(),
        _builtin_default_formatter_config(output_path, enabled=use_builtin_default),
    ):
        return main(args)


def _builtin_cli_formatter_parity_context() -> _BuiltinCliFormatterParityContext:
    return _BuiltinCliFormatterParityContext(
        run_main=_run_main,
        extend_args=_extend_args,
        copy_files=_copy_files,
        assert_exit_code=_assert_exit_code,
    )


def _run_main_url(
    url: str,
    output_path: Path,
    input_file_type: InputFileTypeLiteral | None = None,
    *,
    extra_args: Sequence[str] | None = None,
) -> Exit:
    """Execute main() with URL input (internal use)."""
    args = ["--url", url]
    use_builtin_default = _extend_args(
        args, output_path=output_path, input_file_type=input_file_type, extra_args=extra_args
    )
    with (
        _enable_test_parsed_source_cache(),
        _builtin_default_formatter_config(output_path, enabled=use_builtin_default),
    ):
        return main(args)


def run_main_with_args(
    args: Sequence[str],
    *,
    expected_exit: Exit = Exit.OK,
    capsys: pytest.CaptureFixture[str] | None = None,
    expected_stdout_path: Path | None = None,
    expected_stderr: str | None = None,
    expected_stderr_contains: str | None = None,
    assert_no_stderr: bool = False,
    use_parsed_source_cache: bool = True,
    use_builtin_default_formatter: bool = True,
    isolate_model_template_cache: bool = False,
) -> Exit:
    """Execute main() with custom arguments.

    Args:
        args: Command line arguments to pass to main()
        expected_exit: Expected exit code (default: Exit.OK)
        capsys: pytest capsys fixture for capturing output assertions
        expected_stdout_path: Path to file with expected stdout content
        expected_stderr: Exact expected stderr
        expected_stderr_contains: Expected stderr substring
        assert_no_stderr: Assert stderr is empty
        use_parsed_source_cache: Enable the process-local parsed source cache for generation-style commands
        use_builtin_default_formatter: Add the test-suite builtin formatter default when applicable
        isolate_model_template_cache: Clear cached Jinja model templates before and after execution

    Returns:
        Exit code from main()
    """
    __tracebackhide__ = True
    output_path = _get_cli_output_path(args)
    is_generation_command = _is_main_generation_command(args)
    use_builtin_default = use_builtin_default_formatter and _should_use_builtin_default_cli_formatter(
        args,
        output_path=output_path,
        is_generation_command=is_generation_command,
    )
    main_args = [*args, "--formatters", _BUILTIN_FORMATTER_VALUE] if use_builtin_default else list(args)
    with (
        _optional_test_parsed_source_cache(use_parsed_source_cache),
        _optional_model_template_cache_isolation(isolate_model_template_cache),
        _builtin_default_formatter_config(output_path, enabled=use_builtin_default),
    ):
        return_code = main(main_args)
    _assert_exit_code(return_code, expected_exit, f"Args: {args}")
    _assert_captured_output(
        capsys,
        expected_stdout_path=expected_stdout_path,
        expected_stderr=expected_stderr,
        expected_stderr_contains=expected_stderr_contains,
        assert_no_stderr=assert_no_stderr,
    )
    return return_code


def run_main_with_system_exit(
    args: Sequence[str],
    *,
    expected_code: int | Exit,
    capsys: pytest.CaptureFixture[str] | None = None,
    expected_stdout_path: Path | None = None,
    expected_stderr: str | None = None,
    expected_stderr_contains: str | None = None,
    assert_no_stderr: bool = False,
) -> None:
    """Execute main() expecting argparse/SystemExit and assert captured output."""
    __tracebackhide__ = True
    output_path = _get_cli_output_path(args)
    is_generation_command = _is_main_generation_command(args)
    use_builtin_default = _should_use_builtin_default_cli_formatter(
        args,
        output_path=output_path,
        is_generation_command=is_generation_command,
    )
    main_args = [*args, "--formatters", _BUILTIN_FORMATTER_VALUE] if use_builtin_default else list(args)
    with (
        pytest.raises(SystemExit) as exc_info,
        _enable_test_parsed_source_cache(),
        _builtin_default_formatter_config(output_path, enabled=use_builtin_default),
    ):
        main(main_args)
    if exc_info.value.code != expected_code:  # pragma: no cover
        pytest.fail(f"Expected SystemExit code {expected_code!r}, got {exc_info.value.code!r}\nArgs: {args}")
    _assert_captured_output(
        capsys,
        expected_stdout_path=expected_stdout_path,
        expected_stderr=expected_stderr,
        expected_stderr_contains=expected_stderr_contains,
        assert_no_stderr=assert_no_stderr,
    )


def assert_watchfiles_module(result: object) -> None:
    """Assert watchfiles dependency resolution returned a usable module."""
    __tracebackhide__ = True
    if result is None or not hasattr(result, "watch"):  # pragma: no cover
        pytest.fail("Expected watchfiles module with a watch attribute")


def assert_input_file_type(result: object, expected: InputFileType) -> None:
    """Assert input type inference selected the expected input file type."""
    __tracebackhide__ = True
    if result != expected:  # pragma: no cover
        pytest.fail(f"Expected input file type {expected!r}, got {result!r}")


def assert_path_cache_evicts_lru_entries(
    loader: Callable[[Path, str], object],
    first_path: Path,
    second_path: Path,
    *,
    encoding: str = "utf-8",
) -> None:
    """Assert a path-based LRU cache remains valid while evicting older entries."""
    __tracebackhide__ = True
    first_value = loader(first_path, encoding)
    if loader(first_path, encoding) != first_value:
        pytest.fail(f"Expected cached value for {first_path} to stay stable")

    second_value = loader(second_path, encoding)
    if loader(second_path, encoding) == second_value:
        return

    pytest.fail(f"Expected cached value for {second_path} to stay stable")


def run_watch_and_assert(config: Any, *, expected_exit: Exit = Exit.OK) -> None:
    """Run watch mode with the standard no-op callback arguments and assert its exit code."""
    __tracebackhide__ = True
    from datamodel_code_generator.watch import watch_and_regenerate

    return_code = watch_and_regenerate(config, regenerate=lambda: Exit.OK)
    _assert_exit_code(return_code, expected_exit, f"Watch config: {config!r}")


def run_generate_file_and_assert(
    *,
    input_path: Path,
    output_path: Path,
    input_file_type: InputFileType | None = None,
    assert_func: AssertFileContent | None = None,
    expected_file: str | Path | None = None,
    expected_directory: Path | None = None,
    transform: Callable[[str], str] | None = None,
    expected_warnings: Sequence[str] | None = None,
    unchanged_inputs: Mapping[str, object] | None = None,
    **generate_kwargs: Any,
) -> None:
    """Execute generate() for a path input and assert file or directory output."""
    __tracebackhide__ = True

    input_: Path = input_path
    if input_path.is_absolute():
        try:
            input_ = input_path.relative_to(Path.cwd())
        except ValueError:
            input_ = input_path
        else:
            assert not input_.is_absolute()

    generate_options: dict[str, Any] = {
        "output": output_path,
        **_default_formatter_generate_options(generate_kwargs, output_path=output_path),
    }
    if input_file_type is not None:
        generate_options["input_file_type"] = input_file_type

    with _enable_test_parsed_source_cache(), assert_inputs_not_mutated(unchanged_inputs):
        if expected_warnings is None:
            generate(
                input_=input_,
                **generate_options,
            )
        else:
            with warnings.catch_warnings(record=True) as warning_records:
                warnings.simplefilter("always")
                generate(
                    input_=input_,
                    **generate_options,
                )
            assert_warnings_contain(warning_records, *expected_warnings)

    if expected_directory is not None:
        assert_directory_content(output_path, expected_directory)
    else:
        if expected_file is None:
            frame = inspect.currentframe()
            assert frame is not None
            assert frame.f_back is not None
            expected_file = _infer_expected_file(frame.f_back.f_code.co_name)
            del frame

        assert assert_func is not None
        assert_func(output_path, expected_file, transform=transform)
    with _enable_test_parsed_source_cache(), assert_inputs_not_mutated(unchanged_inputs):
        _assert_builtin_generate_formatter_parity(
            input_=input_,
            output_path=output_path,
            generate_options=generate_options,
            expected_warnings=expected_warnings,
        )


def run_generate_and_assert(
    *,
    input_: Any,
    expected_file: Path | None = None,
    expected_error: type[Exception] | None = None,
    expected_error_match: str | None = None,
    assert_input_unchanged: bool = False,
    unchanged_inputs: Mapping[str, object] | None = None,
    **generate_kwargs: Any,
) -> None:
    """Execute generate() and assert returned text, written text, or an expected error."""
    __tracebackhide__ = True

    guarded_inputs = dict(unchanged_inputs or {})
    if assert_input_unchanged:
        guarded_inputs["input_"] = input_

    options = _default_formatter_generate_options(generate_kwargs)
    with _enable_test_parsed_source_cache(), assert_inputs_not_mutated(guarded_inputs or None):
        if expected_error is not None:
            with pytest.raises(expected_error, match=expected_error_match) as error:
                generate(input_=input_, **options)
            if expected_file is not None:
                assert_output(f"{error.value}\n", expected_file)
            return
        result = generate(input_=input_, **options)
    if isinstance(config := generate_kwargs.get("config"), GenerateConfig):
        output_path, encoding = config.output, config.encoding
    else:
        output_path, encoding = generate_kwargs.get("output"), generate_kwargs.get("encoding", "utf-8")
    if output_path is not None:
        assert_generate_wrote_file(result, output_path)
        result = output_path.read_text(encoding=encoding)
    if not isinstance(result, str):  # pragma: no cover
        pytest.fail(f"Expected generate() to return str, got {type(result).__name__}")
    assert expected_file is not None
    assert_output(result, expected_file)


def run_main_and_assert(  # noqa: PLR0912
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    input_file_type: InputFileTypeLiteral | None = None,
    extra_args: Sequence[str] | None = None,
    expected_exit: Exit = Exit.OK,
    # Output verification options (use one)
    assert_func: AssertFileContent | None = None,
    expected_file: str | Path | None = None,
    expected_output: str | None = None,
    expected_directory: Path | None = None,
    output_to_expected: Sequence[tuple[str, str | Path]] | None = None,
    file_should_not_exist: Path | Sequence[Path] | None = None,
    output_should_not_exist: bool = False,
    # Verification options
    ignore_whitespace: bool = False,
    transform: Callable[[str], str] | None = None,
    # Capture options
    capsys: pytest.CaptureFixture[str] | None = None,
    expected_stdout_path: Path | None = None,
    expected_stderr: str | None = None,
    expected_stderr_contains: str | None = None,
    assert_no_stderr: bool = False,
    # Other options
    copy_files: CopyFilesMapping | None = None,
    # stdin options
    stdin_path: Path | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    # Code validation options
    skip_code_validation: bool = False,
    force_exec_validation: bool = False,
    importable_module_name: str | None = None,
    importable_module_file: str | Path | None = None,
    importable_module_attribute: str | None = None,
    runtime_validation_module: str | None = None,
    runtime_validation_model_name: str | None = None,
    runtime_validation_data: Mapping[str, Any] | None = None,
) -> None:
    """Execute main() and assert output.

    This is the unified helper function for testing file-based input.

    Input options:
        input_path: Path to input schema file
        stdin_path: Path to file that will be used as stdin (requires monkeypatch)
        monkeypatch: pytest monkeypatch fixture for mocking stdin

    Output options:
        output_path: Path to output file/directory (None for stdout-only tests)

    Common options:
        input_file_type: Type of input file (openapi, jsonschema, graphql, etc.)
        extra_args: Additional CLI arguments
        expected_exit: Expected exit code (default: Exit.OK)
        copy_files: Files to copy before running

    Output verification (use one):
        assert_func + expected_file: Compare with expected file using assert function
        expected_output: Compare with string directly
        expected_directory: Compare entire directory
        output_to_expected: Compare multiple files
        file_should_not_exist: Assert a file does NOT exist
        output_should_not_exist: Assert output_path does NOT exist

    Verification modifiers:
        ignore_whitespace: Ignore whitespace when comparing (for expected_output)
        transform: Transform output before comparison

    Capture verification:
        capsys: pytest capsys fixture
        expected_stdout_path: Compare stdout with file
        expected_stderr: Assert exact stderr match
        expected_stderr_contains: Assert stderr contains string
        assert_no_stderr: Assert stderr is empty

    Code validation options:
        skip_code_validation: Skip all code validation (compile and exec)
        force_exec_validation: Run exec() even when target Python version differs from
            the test environment (only effective when target <= runtime). This catches
            runtime errors that would otherwise be missed. Has no effect when target >
            runtime since compile is skipped in that case.
        importable_module_name: Import output_path as this module name
        importable_module_file: Relative file under output_path to import
        importable_module_attribute: Assert imported module defines this attribute
        runtime_validation_module: Relative generated package module to import
        runtime_validation_model_name: Model class name to validate at runtime
        runtime_validation_data: Data passed to model_validate on the generated model
    """
    __tracebackhide__ = True
    runtime_validation_options = (
        runtime_validation_module,
        runtime_validation_model_name,
        runtime_validation_data,
    )
    if any(option is not None for option in runtime_validation_options) and not all(
        option is not None for option in runtime_validation_options
    ):  # pragma: no cover
        pytest.fail(
            "runtime_validation_module, runtime_validation_model_name, and runtime_validation_data "
            "must be passed together"
        )

    # Handle stdin input
    if stdin_path is not None:
        if monkeypatch is None:  # pragma: no cover
            pytest.fail("monkeypatch is required when using stdin_path")
        _copy_files(copy_files)
        monkeypatch.setattr("sys.stdin", stdin_path.open(encoding="utf-8"))
        args: list[str] = []
        use_builtin_default = _extend_args(
            args,
            output_path=output_path,
            input_file_type=input_file_type,
            extra_args=extra_args,
            copy_files=copy_files,
        )
        with (
            _enable_test_parsed_source_cache(),
            _builtin_default_formatter_config(output_path, enabled=use_builtin_default),
        ):
            return_code = main(args)
    # Handle stdout-only output (no output_path)
    elif output_path is None:
        if input_path is None:  # pragma: no cover
            pytest.fail("input_path is required when output_path is None")
        _copy_files(copy_files)
        args = []
        use_builtin_default = _extend_args(
            args,
            input_path=input_path,
            input_file_type=input_file_type,
            extra_args=extra_args,
            copy_files=copy_files,
        )
        with (
            _enable_test_parsed_source_cache(),
            _builtin_default_formatter_config(output_path, enabled=use_builtin_default),
        ):
            return_code = main(args)
    # Standard file input
    else:
        if input_path is None:  # pragma: no cover
            pytest.fail("input_path is required")
        return_code = _run_main(input_path, output_path, input_file_type, extra_args=extra_args, copy_files=copy_files)

    _assert_exit_code(return_code, expected_exit, f"Input: {input_path}")

    _assert_captured_output(
        capsys,
        expected_stdout_path=expected_stdout_path,
        expected_stderr=expected_stderr,
        expected_stderr_contains=expected_stderr_contains,
        assert_no_stderr=assert_no_stderr,
    )

    output_verification_modes = (
        int(assert_func is not None and output_to_expected is None)
        + int(expected_output is not None)
        + int(expected_directory is not None)
        + int(output_to_expected is not None)
        + int(file_should_not_exist is not None)
        + int(output_should_not_exist)
    )
    if output_verification_modes > 1:  # pragma: no cover
        pytest.fail(
            "Output verification options are mutually exclusive; use exactly one of "
            "standalone assert_func, expected_output, expected_directory, output_to_expected, "
            "file_should_not_exist, or output_should_not_exist"
        )

    if output_should_not_exist:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using output_should_not_exist")
        _assert_file_does_not_exist(output_path)
    if file_should_not_exist is not None:
        match file_should_not_exist:
            case Path() as missing_path:
                _assert_file_does_not_exist(missing_path)
            case missing_paths:
                for missing_path in missing_paths:
                    _assert_file_does_not_exist(missing_path)

    # Skip output verification if expected_exit is not OK
    if expected_exit != Exit.OK:
        return
    if output_should_not_exist or file_should_not_exist is not None:  # pragma: no cover
        return

    # Output verification
    if expected_directory is not None:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using expected_directory")
        assert_directory_content(output_path, expected_directory)
    elif output_to_expected is not None:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using output_to_expected")
        if assert_func is None:  # pragma: no cover
            pytest.fail("assert_func is required when using output_to_expected")
        for output_relative, exp_file in output_to_expected:
            assert_func(output_path / output_relative, exp_file)
    elif expected_output is not None:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using expected_output")
        actual_output = output_path.read_text(encoding="utf-8")
        if ignore_whitespace:
            if "".join(actual_output.split()) != "".join(expected_output.split()):  # pragma: no cover
                pytest.fail(
                    f"Output mismatch (ignoring whitespace)\nExpected:\n{expected_output}\n\nActual:\n{actual_output}"
                )
        elif actual_output != expected_output:  # pragma: no cover
            pytest.fail(f"Output mismatch\nExpected:\n{expected_output}\n\nActual:\n{actual_output}")
    elif assert_func is not None:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using assert_func")
        if expected_file is None:
            frame = inspect.currentframe()
            assert frame is not None
            assert frame.f_back is not None
            expected_file = _infer_expected_file(frame.f_back.f_code.co_name)
            del frame
        assert_func(output_path, expected_file, transform=transform)

    with _enable_test_parsed_source_cache():
        _assert_builtin_cli_formatter_parity(
            input_path=input_path,
            output_path=output_path,
            input_file_type=input_file_type,
            extra_args=extra_args,
            copy_files=copy_files,
            stdin_path=stdin_path,
            monkeypatch=monkeypatch,
            context=_builtin_cli_formatter_parity_context(),
        )

    if output_path is not None and not skip_code_validation:
        _validate_output_files(output_path, extra_args, force_exec_validation=force_exec_validation)
    if importable_module_name is not None:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using importable_module_name")
        importable_path = output_path
        if importable_module_file is not None:
            importable_path = output_path / importable_module_file
        _assert_python_module_importable(importable_path, importable_module_name, importable_module_attribute)
    if runtime_validation_module is not None:
        if output_path is None:  # pragma: no cover
            pytest.fail("output_path is required when using runtime_validation_module")
        if _should_skip_compile(extra_args):
            return
        runtime_validation_model = runtime_validation_model_name
        runtime_validation_payload = runtime_validation_data
        if runtime_validation_model is None or runtime_validation_payload is None:  # pragma: no cover
            pytest.fail(
                "runtime_validation_module, runtime_validation_model_name, and runtime_validation_data "
                "must be passed together"
            )
        _assert_generated_package_model_validation(
            output_path,
            module_path=runtime_validation_module,
            model_name=runtime_validation_model,
            data=runtime_validation_payload,
        )


def _get_argument_value(arguments: Sequence[str] | None, argument_name: str) -> str | None:
    """Extract argument value from arguments."""
    if arguments is None:
        return None
    argument_list = list(arguments)
    for index, argument in enumerate(argument_list):
        if argument == argument_name and index + 1 < len(argument_list):
            return argument_list[index + 1]
    return None


def _parse_target_version(extra_arguments: Sequence[str] | None) -> tuple[int, int] | None:
    """Parse target Python version from arguments."""
    if (target_version := _get_argument_value(extra_arguments, "--target-python-version")) is None:
        return None
    try:
        return tuple(int(part) for part in target_version.split("."))  # type: ignore[return-value]
    except ValueError:  # pragma: no cover
        return None


def _should_skip_compile(extra_arguments: Sequence[str] | None) -> bool:
    """Check if compile should be skipped when target version > runtime version."""
    if (target_version := _parse_target_version(extra_arguments)) is None:
        return False
    return target_version > sys.version_info[:2]


def _should_skip_exec(extra_arguments: Sequence[str] | None, *, force_exec: bool = False) -> bool:
    """Check if exec should be skipped based on model type and Python version.

    Args:
        extra_arguments: CLI arguments passed to the test.
        force_exec: If True, skip version mismatch check and allow exec on current Python version.
            This only works when target version <= runtime version (older target on newer runtime).
            When target > runtime, compile will be skipped entirely regardless of this flag.
    """
    if (target_version := _parse_target_version(extra_arguments)) is None:
        return True
    if not force_exec and target_version != sys.version_info[:2]:
        return True
    return _get_argument_value(extra_arguments, "--base-class") is not None


def _validate_output_files(
    output_path: Path,
    extra_arguments: Sequence[str] | None = None,
    *,
    force_exec_validation: bool = False,
) -> None:
    """Validate generated Python files by compiling/executing them.

    Args:
        output_path: Path to output file or directory to validate.
        extra_arguments: CLI arguments passed to the test.
        force_exec_validation: If True, run exec even when target Python version differs from
            the test environment (only when target <= runtime). This helps catch runtime errors
            that would otherwise be missed. Has no effect when target > runtime since compile
            is skipped in that case.
    """
    if _should_skip_compile(extra_arguments):
        return
    should_exec = not _should_skip_exec(extra_arguments, force_exec=force_exec_validation)
    if output_path.is_file() and output_path.suffix == ".py":
        validate_generated_code(
            output_path.read_text(encoding="utf-8"),
            str(output_path),
            do_exec=should_exec and not force_exec_validation,
        )
        if should_exec and force_exec_validation:
            _import_generated_output(output_path)
        return

    if not output_path.is_dir():  # pragma: no cover
        return
    for python_file in output_path.rglob("*.py"):
        validate_generated_code(python_file.read_text(encoding="utf-8"), str(python_file), do_exec=False)
    if should_exec:
        _import_generated_output(output_path)


def _generated_output_import_code(output_path: Path) -> str:
    return textwrap.dedent(
        f"""
        import importlib.util
        import sys
        from pathlib import Path

        imported_packages = []


        def _import_file(path, module_name):
            parent_directory = str(path.parent)
            sys.path.insert(0, parent_directory)
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Unable to load generated module from {{path}}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            finally:
                if parent_directory in sys.path:
                    sys.path.remove(parent_directory)
                sys.modules.pop(module_name, None)


        def _import_package(parent_directory, package_name):
            package_path = parent_directory / package_name
            parent_directory_value = str(parent_directory)
            imported_modules = []
            imported_packages.append(package_name)
            sys.path.insert(0, parent_directory_value)
            try:
                spec = importlib.util.spec_from_file_location(
                    package_name,
                    package_path / "__init__.py",
                    submodule_search_locations=[str(package_path)],
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"Unable to load generated package {{package_path}}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[package_name] = module
                imported_modules.append(package_name)
                spec.loader.exec_module(module)

                for python_file in package_path.rglob("*.py"):
                    if python_file.name == "__init__.py":
                        continue
                    relative_path = python_file.relative_to(package_path)
                    module_name = f"{{package_name}}.{{'.'.join(relative_path.with_suffix('').parts)}}"
                    submodule_spec = importlib.util.spec_from_file_location(module_name, python_file)
                    if submodule_spec is None or submodule_spec.loader is None:
                        raise ImportError(f"Unable to load generated module from {{python_file}}")
                    submodule = importlib.util.module_from_spec(submodule_spec)
                    sys.modules[module_name] = submodule
                    imported_modules.append(module_name)
                    submodule_spec.loader.exec_module(submodule)
            finally:
                if parent_directory_value in sys.path:
                    sys.path.remove(parent_directory_value)
                for module_name in reversed(imported_modules):
                    sys.modules.pop(module_name, None)


        output_path = Path({str(output_path.absolute())!r})
        if output_path.is_file():
            _import_file(output_path, "_datamodel_codegen_generated_output")
        elif (output_path / "__init__.py").exists():
            _import_package(output_path.parent, output_path.name)
        else:
            for directory in output_path.iterdir():
                if directory.is_dir() and (directory / "__init__.py").exists():
                    _import_package(output_path, directory.name)
        """
    )


def _get_concurrent_interpreters_module() -> Any | None:
    try:
        from concurrent import interpreters
    except ImportError:
        return None
    return interpreters


_SUBINTERPRETER_UNSUPPORTED: list[None] = []


def _is_subinterpreter_unsupported_import_error(exception: BaseException) -> bool:
    messages = [str(exception)]
    if (excinfo := getattr(exception, "excinfo", None)) is not None:
        messages.append(str(excinfo))
    for message in messages:
        match message:
            case str() if (
                "does not support loading in subinterpreter" in message
                or "does not support loading in subinterpreters" in message
            ):
                return True
            case _:
                continue
    return False


def _try_import_generated_output_in_subinterpreter(output_path: Path) -> bool:
    if _SUBINTERPRETER_UNSUPPORTED:
        return False

    interpreters = _get_concurrent_interpreters_module()
    if interpreters is None:
        return False

    interpreter = interpreters.create()
    try:
        interpreter.exec(_generated_output_import_code(output_path))
    except Exception as exception:
        if _is_subinterpreter_unsupported_import_error(exception):
            # Keep import validation active when an extension dependency cannot run in subinterpreters.
            _SUBINTERPRETER_UNSUPPORTED.append(None)
            return False
        raise
    finally:
        interpreter.close()
    return True


def _import_generated_output(output_path: Path) -> None:
    start_time = time.perf_counter()
    try:
        if not _try_import_generated_output_in_subinterpreter(output_path):
            namespace: dict[str, Any] = {}
            try:
                exec(_generated_output_import_code(output_path), namespace)
            finally:
                for package_name in namespace.get("imported_packages", ()):
                    _forget_generated_package(package_name)
        _validation_stats.record_exec(time.perf_counter() - start_time)
    except Exception as exception:  # pragma: no cover
        _validation_stats.record_error(str(output_path), f"{type(exception).__name__}: {exception}")
        raise


@contextmanager
def _generated_model(output_path: Path, module_name: str, model_name: str) -> Generator[Any, None, None]:
    spec = importlib.util.spec_from_file_location(module_name, output_path)
    if spec is None or spec.loader is None:  # pragma: no cover
        pytest.fail(f"Unable to load generated module from {output_path}", pytrace=False)

    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield getattr(module, model_name)
    finally:
        if previous_module is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module


def _model_json_validator(model: Any) -> Callable[[str], Any]:
    """Return a JSON validation callable for a generated Pydantic model or dataclass."""
    if callable(validate_json := getattr(model, "model_validate_json", None)):
        return validate_json
    if not is_dataclass(model):
        return TypeAdapter(model).validate_json
    try:
        return TypeAdapter(model).validate_json
    except PydanticUndefinedAnnotation:
        pass

    module_namespace = vars(sys.modules[model.__module__])
    for candidate in tuple(module_namespace.values()):
        if not (isinstance(candidate, type) and candidate.__module__ == model.__module__ and is_dataclass(candidate)):
            continue
        resolved_annotations = get_type_hints(
            candidate,
            globalns=module_namespace,
            localns=module_namespace,
            include_extras=True,
        )
        candidate.__annotations__.update(resolved_annotations)
        for field in dataclass_fields(candidate):
            field.type = resolved_annotations.get(field.name, field.type)
    return TypeAdapter(model).validate_json


def _assert_model_json_invalid(validate: Callable[[Any], Any], invalid_data: Any, expected_error_type: str) -> None:
    """Assert that a generated model validator rejects invalid input with the expected error."""
    with pytest.raises(ValidationError) as exc_info:
        validate(invalid_data)
    errors = exc_info.value.errors()
    if not errors:  # pragma: no cover
        pytest.fail("Expected validation error but got an empty errors list", pytrace=False)
    actual_error_type = errors[0]["type"]
    if actual_error_type != expected_error_type:  # pragma: no cover
        pytest.fail(
            f"Expected validation error {expected_error_type!r}, got {actual_error_type!r}",
            pytrace=False,
        )


def assert_generated_model_json_validation(
    output_path: Path,
    *,
    module_name: str,
    model_name: str,
    valid_json: str,
    invalid_json: str,
    expected_error_type: str,
    expected_attribute_path: Sequence[str] = (),
    expected_attribute_value: Any = None,
    expected_keyword_only_fields: Collection[str] | None = None,
    expected_repr: str | None = None,
    invalid_keyword_arguments: Sequence[tuple[Mapping[str, Any], str]] = (),
) -> None:
    """Import a generated module and validate JSON data through a generated Pydantic model or dataclass."""
    with _generated_model(output_path, module_name, model_name) as model:
        validate_json = _model_json_validator(model)
        parsed = validate_json(valid_json)

        if expected_keyword_only_fields is not None:
            signature = inspect.signature(model)
            actual_keyword_only_fields = (
                {name for name, field in pydantic_fields.items() if field.kw_only}
                if (pydantic_fields := getattr(model, "__pydantic_fields__", None)) is not None
                else {
                    name
                    for name, parameter in signature.parameters.items()
                    if parameter.kind is inspect.Parameter.KEYWORD_ONLY
                }
            )
            if actual_keyword_only_fields != set(expected_keyword_only_fields):  # pragma: no cover
                pytest.fail(
                    f"Expected keyword-only fields {set(expected_keyword_only_fields)!r}, "
                    f"got {actual_keyword_only_fields!r}",
                    pytrace=False,
                )

        if expected_attribute_path:
            actual: Any = parsed
            for attribute in expected_attribute_path:
                match actual:
                    case Mapping() if attribute in actual:
                        actual = actual[attribute]
                    case _:
                        actual = getattr(actual, attribute)
            if actual != expected_attribute_value:  # pragma: no cover
                pytest.fail(
                    f"Expected {'.'.join(expected_attribute_path)} to be {expected_attribute_value!r}, got {actual!r}",
                    pytrace=False,
                )

        if expected_repr is not None and repr(parsed) != expected_repr:  # pragma: no cover
            pytest.fail(f"Expected repr {expected_repr!r}, got {parsed!r}", pytrace=False)

        _assert_model_json_invalid(validate_json, invalid_json, expected_error_type)
        for keyword_arguments, keyword_error_type in invalid_keyword_arguments:
            _assert_model_json_invalid(lambda values: model(**values), keyword_arguments, keyword_error_type)


def assert_generated_model_json_invalid(
    output_path: Path,
    *,
    module_name: str,
    model_name: str,
    invalid_json: str,
    expected_error_type: str,
) -> None:
    """Import a generated module and assert JSON data is rejected by a generated Pydantic model."""
    with _generated_model(output_path, module_name, model_name) as model:
        _assert_model_json_invalid(_model_json_validator(model), invalid_json, expected_error_type)


def run_main_url_and_assert(
    *,
    url: str,
    output_path: Path,
    input_file_type: InputFileTypeLiteral | None,
    assert_func: AssertFileContent,
    expected_file: str | Path,
    extra_args: Sequence[str] | None = None,
    transform: Callable[[str], str] | None = None,
    force_exec_validation: bool = False,
) -> None:
    """Execute main() with URL input and assert output.

    Args:
        url: URL to fetch schema from
        output_path: Path to output file
        input_file_type: Type of input file (openapi, jsonschema, graphql, etc.)
        assert_func: The assert_file_content function to use for verification
        expected_file: Expected output filename
        extra_args: Additional CLI arguments
        transform: Optional function to transform output before comparison
        force_exec_validation: Run exec() even when target Python version differs from
            the test environment (only effective when target <= runtime).
    """
    __tracebackhide__ = True
    return_code = _run_main_url(url, output_path, input_file_type, extra_args=extra_args)
    _assert_exit_code(return_code, Exit.OK, f"URL: {url}")
    assert_func(output_path, expected_file, transform=transform)

    _validate_output_files(output_path, extra_args, force_exec_validation=force_exec_validation)
