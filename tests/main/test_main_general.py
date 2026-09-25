"""General integration tests for main code generation functionality."""

from __future__ import annotations

import ast
import inspect
import json
import platform
import shutil
import sys
import tokenize
import warnings
from argparse import ArgumentTypeError, BooleanOptionalAction, Namespace
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, ClassVar, cast

import black
import pytest
from inline_snapshot import snapshot
from packaging import version

import datamodel_code_generator
from datamodel_code_generator import (
    _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT,
    AllExportsScope,
    CustomFileHeaderMode,
    DanglingRefWarning,
    DataModelType,
    Error,
    GeneratedModules,
    HTTPBackend,
    InputFileType,
    SchemaParseError,
    _create_parser_config,
    _create_typed_parser_config,
    _find_future_import_insertion_point,
    _generate_config_values,
    chdir,
    generate,
    snooper_to_methods,
)
from datamodel_code_generator.__main__ import (
    Config,
    Exit,
    _create_config,
    _generation_config,
    run_generate_from_config,
)
from datamodel_code_generator.arguments import _dataclass_arguments, arg_parser
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.deprecations import DEPRECATIONS, Deprecation
from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersion
from datamodel_code_generator.model.pydantic_v2 import UnionMode
from datamodel_code_generator.parser import LiteralType
from datamodel_code_generator.parser.mcp import convert_mcp_tools_to_jsonschema
from datamodel_code_generator.parser.openapi import OpenAPIParser
from tests.conftest import (
    HttpxGetMockFactory,
    MockHttpxResponse,
    assert_directory_content,
    assert_generate_wrote_file,
    assert_generated_file_matches_output,
    assert_generated_modules_output,
    assert_httpx_get_kwargs,
    assert_inputs_not_mutated,
    assert_mutable_copy_is_isolated,
    assert_no_uncommented_generated_code,
    assert_output,
    assert_runtime_import_package,
    assert_warnings_contain,
    assert_warnings_do_not_contain,
    create_assert_file_content,
    freeze_time,
    validate_generated_code,
)
from tests.main.conftest import (
    DATA_PATH,
    DEFAULT_VALUES_DATA_PATH,
    EXPECTED_MAIN_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    PYTHON_DATA_PATH,
    TIMESTAMP,
    _optional_test_parsed_source_cache,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
    run_main_with_args,
    run_main_with_system_exit,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

assert_file_content = create_assert_file_content(EXPECTED_MAIN_PATH)
BLACK_VERSION = version.parse(black.__version__)
BLACK_LT_233 = version.parse("23.3.0") > BLACK_VERSION
BLACK_LT_24 = version.parse("24.0.0") > BLACK_VERSION
CLI_OPTION_SUGGESTIONS_PATH = EXPECTED_MAIN_PATH / "cli_option_suggestions"


class _GenerateParseAbort(BaseException):
    """Test-only parse abort that is not an Exception subclass."""


@pytest.mark.allow_direct_assert
def test_collapse_root_models_retry_reraises_second_recursion_error(mocker: MockerFixture) -> None:
    """Retry root-model collapsing exactly once and expose the second recursion error."""
    assert datamodel_code_generator._CollapseRootModelsRecursionError.__module__ == "datamodel_code_generator"
    retry_error = RecursionError("retry parse recursion")

    def raise_collapse_recursion(*_: Any, **__: Any) -> None:
        try:
            raise retry_error
        except RecursionError as exc:
            raise datamodel_code_generator._CollapseRootModelsRecursionError from exc

    parse_with_disposal = mocker.patch.object(
        datamodel_code_generator,
        "_parse_with_disposal",
        side_effect=raise_collapse_recursion,
    )

    with pytest.raises(RecursionError, match="retry parse recursion") as exc_info:
        generate(
            {"type": "object"},
            input_file_type=InputFileType.JsonSchema,
            collapse_root_models=True,
        )

    assert exc_info.value is retry_error
    assert parse_with_disposal.call_count == 2


@pytest.mark.allow_direct_assert
def test_collapse_root_models_retry_normalizes_sentinel_without_cause(mocker: MockerFixture) -> None:
    """Never expose the private retry sentinel when an abnormal retry loses its cause."""
    initial_error = datamodel_code_generator._CollapseRootModelsRecursionError()
    initial_error.__cause__ = RecursionError("initial parse recursion")
    parse_with_disposal = mocker.patch.object(
        datamodel_code_generator,
        "_parse_with_disposal",
        side_effect=[initial_error, datamodel_code_generator._CollapseRootModelsRecursionError("retry recursion")],
    )

    with pytest.raises(RecursionError, match="retry recursion") as exc_info:
        generate(
            {"type": "object"},
            input_file_type=InputFileType.JsonSchema,
            collapse_root_models=True,
        )

    assert type(exc_info.value) is RecursionError
    assert parse_with_disposal.call_count == 2


@freeze_time("2019-07-26")
def test_collapse_root_models_retry_preserves_circular_schema_output(mocker: MockerFixture) -> None:
    """Retry a real circular-root schema without changing its generated output."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    original_parse = JsonSchemaParser.parse
    fail_initial_parse = True
    initial_error = RecursionError()

    def parse_after_initial_recursion(parser: JsonSchemaParser, *args: Any, **kwargs: Any) -> Any:
        nonlocal fail_initial_parse
        if fail_initial_parse:
            fail_initial_parse = False
            raise datamodel_code_generator._CollapseRootModelsRecursionError from initial_error
        return original_parse(parser, *args, **kwargs)

    mocker.patch.object(JsonSchemaParser, "parse", autospec=True, side_effect=parse_after_initial_recursion)

    result = generate(
        JSON_SCHEMA_DATA_PATH / "collapse_root_models_self_reference.json",
        input_file_type=InputFileType.JsonSchema,
        collapse_root_models=True,
    )

    assert_output(
        f"{cast('str', result)}\n",
        EXPECTED_MAIN_PATH / "jsonschema" / "jsonschema_collapse_root_models_self_reference.py",
    )


def test_parser_collects_empty_model_metadata() -> None:
    """Collect an empty metadata payload when parsing emits no models."""
    from datamodel_code_generator.model_metadata import dump_model_metadata
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        JSON_SCHEMA_DATA_PATH / "const_null.json",
        formatters=[],
        skip_root_model=True,
    )
    try:
        assert_output(
            f"{parser.parse(collect_model_metadata=True, format_=False)!r}\n",
            EXPECTED_MAIN_PATH / "empty_model_metadata_modules.txt",
        )
        assert_output(
            f"{dump_model_metadata(parser.model_metadata)}\n",
            EXPECTED_MAIN_PATH / "empty_model_metadata_map.txt",
        )
    finally:
        parser._dispose()


def test_parser_run_context_preserves_subclass_lifecycle_hooks(tmp_path: Path) -> None:
    """Keep custom parser hooks and output stable under facade-managed run settings."""
    from datamodel_code_generator.parser.base import ParserRunContext
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    class RunContextJsonSchemaParser(JsonSchemaParser):
        events: list[str]

        def parse_raw(self) -> None:
            self.events.append(f"parse:{self._source_path_for_diagnostics()}")
            warnings.warn("custom parse warning", stacklevel=1)
            super().parse_raw()

        def _report_parse_diagnostics(self) -> None:
            self.events.append(f"diagnostics:{self._source_path_for_diagnostics()}")
            warnings.warn("custom diagnostic warning", stacklevel=1)
            super()._report_parse_diagnostics()

        def _build_code_formatter(
            self,
            settings_path: Path | None,
            *,
            is_multi_module_output: bool,
        ) -> CodeFormatter:
            formatter_cwd = self.run_context.formatter_cwd
            self.events.append(f"formatter:{formatter_cwd.name if formatter_cwd is not None else None}")
            return super()._build_code_formatter(
                settings_path,
                is_multi_module_output=is_multi_module_output,
            )

        def _dispose(self) -> None:
            self.events.append("dispose")
            super()._dispose()

    input_path = JSON_SCHEMA_DATA_PATH / "person.json"
    formatter_cwd = tmp_path / "formatter-root"
    formatter_cwd.mkdir()
    parser = RunContextJsonSchemaParser(
        input_path,
        base_path=input_path.parent,
        builtin_format_line_length=88,
        formatters=[Formatter.BUILTIN],
        use_standard_collections=True,
        use_union_operator=True,
    )
    parser.events = []
    parser.configure_run_context(
        diagnostic_source_path=Path("diagnostic-person.json"),
        formatter_cwd=formatter_cwd,
        preserve_circular_root_models=True,
        suppress_parse_warnings=True,
    )
    parser.events.append(f"preserve:{parser.run_context.preserve_circular_root_models}")
    with warnings.catch_warnings(record=True) as warning_records:
        warnings.simplefilter("always")
        generated = parser.parse()
    parser.dispose()
    parser.configure_run_context()
    parser.events.append(f"default:{parser.run_context == ParserRunContext()}")
    parser._diagnostic_source_path = Path("legacy.json")
    parser._formatter_cwd = formatter_cwd
    parser._preserve_circular_root_models = True
    legacy_diagnostic_source_path = parser._diagnostic_source_path
    legacy_formatter_cwd = parser._formatter_cwd
    parser.events.append(
        "legacy:"
        f"{legacy_diagnostic_source_path.name if legacy_diagnostic_source_path is not None else None},"
        f"{legacy_formatter_cwd.name if legacy_formatter_cwd is not None else None},"
        f"{parser._preserve_circular_root_models}"
    )

    assert_output(
        "# generated by datamodel-codegen:\n#   filename:  person.json\n\n" + generated,
        EXPECTED_MAIN_PATH / "person.py",
    )
    assert_output(
        "\n".join(parser.events) + "\n",
        EXPECTED_MAIN_PATH / "parser_run_context_lifecycle.txt",
    )
    assert_warnings_do_not_contain(
        warning_records,
        "custom parse warning",
        "custom diagnostic warning",
    )


def test_parser_retains_builtin_import_cache_and_invalidates_custom_cache() -> None:
    """Retain built-in caches while keeping the legacy custom-model invalidation contract."""
    from datamodel_code_generator.model import DataModel, get_data_model_types
    from datamodel_code_generator.model.pydantic_v2 import BaseModel
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    class CacheProbeJsonSchemaParser(JsonSchemaParser):
        cache_reuse_manifest: tuple[str, ...] = ()
        module_processing_calls: int = 0

        def _process_single_module(self, module_: Any, models: list[Any], *args: Any, **kwargs: Any) -> Any:
            self.module_processing_calls += 1
            for model in models:
                _ = model.imports
            cached_imports = tuple(model.__dict__[model._IMPORTS_CACHE_KEY] for model in models)
            context = super()._process_single_module(module_, models, *args, **kwargs)
            self.cache_reuse_manifest += tuple(
                f"{model.class_name}:"
                f"{'retained' if cached is model.__dict__.get(model._IMPORTS_CACHE_KEY) else 'invalidated'}"
                for cached, model in zip(cached_imports, context.models, strict=True)
            )
            return context

    class CacheAwareBaseModel(BaseModel):
        def clear_imports_cache(self) -> None:
            cache_state = "cached" if self._IMPORTS_CACHE_KEY in self.__dict__ else "empty"
            history = self.__dict__.setdefault("cache_clear_history", [])
            history.append(cache_state)
            if getattr(self, "_internal_template_data", None) is not None:
                self._set_internal_template_data("class_body_lines", [f"cache_clear_history = {history!r}"])
            super().clear_imports_cache()

    class InjectingJsonSchemaParser(JsonSchemaParser):
        def _create_data_model(
            self,
            model_type: type[DataModel] | None = None,
            **kwargs: Any,
        ) -> DataModel:
            model_type = {
                None: CacheAwareBaseModel,
                self.data_model_type: CacheAwareBaseModel,
            }.get(model_type, model_type)
            return super()._create_data_model(model_type, **kwargs)

    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    input_path = JSON_SCHEMA_DATA_PATH / "field_has_same_name.json"
    parser_options = {
        "base_path": input_path.parent,
        "data_model_root_type": model_types.root_model,
        "data_model_field_type": model_types.field_model,
        "data_type_manager_type": model_types.data_type_manager,
        "dump_resolve_reference_action": model_types.dump_resolve_reference_action,
        "formatters": [Formatter.BUILTIN],
        "target_python_version": PythonVersion.PY_311,
    }
    parser = CacheProbeJsonSchemaParser(input_path, **parser_options)
    assert_output(parser.parse(), EXPECTED_MAIN_PATH / "builtin_import_cache_retention.py")
    assert_output(
        f"{parser.module_processing_calls}\n",
        EXPECTED_MAIN_PATH / "process_single_module_single_call.txt",
    )
    assert_output(
        "\n".join(parser.cache_reuse_manifest) + "\n",
        EXPECTED_MAIN_PATH / "builtin_import_cache_retention.txt",
    )

    input_path = JSON_SCHEMA_DATA_PATH / "unique_items_unhashable_default.json"
    unhashable_default_parser = CacheProbeJsonSchemaParser(
        input_path,
        **{
            **parser_options,
            "collapse_reuse_models": True,
            "reuse_model": True,
            "use_unique_items_as_set": True,
        },
    )
    assert_output(
        unhashable_default_parser.parse(),
        EXPECTED_MAIN_PATH / "builtin_import_cache_unique_items_unhashable.py",
    )
    assert_output(
        f"{unhashable_default_parser.module_processing_calls}\n",
        EXPECTED_MAIN_PATH / "process_single_module_single_call.txt",
    )
    assert_output(
        "\n".join(unhashable_default_parser.cache_reuse_manifest) + "\n",
        EXPECTED_MAIN_PATH / "builtin_import_cache_unique_items_unhashable.txt",
    )

    input_path = DATA_PATH / "performance" / "large_models.json"
    parser = CacheProbeJsonSchemaParser(
        input_path,
        **{**parser_options, "base_path": input_path.parent},
    )
    large_models = cast(
        "dict[tuple[str, ...], Any]",
        parser.parse(module_split_mode=datamodel_code_generator.ModuleSplitMode.Single),
    )

    assert_output(
        large_models["model499.py",].body,
        EXPECTED_MAIN_PATH / "module_split_large_models_model499.py",
    )
    assert_output(
        f"{type(large_models).__name__}\n{len(large_models)}\nmodel499.py\n",
        EXPECTED_MAIN_PATH / "module_split_large_models_manifest.txt",
    )
    assert_output(
        f"{parser.module_processing_calls}\n",
        EXPECTED_MAIN_PATH / "process_single_module_large_models_calls.txt",
    )

    input_path = JSON_SCHEMA_DATA_PATH / "person.json"
    custom_parser = JsonSchemaParser(
        input_path,
        data_model_type=CacheAwareBaseModel,
        **parser_options,
    )
    assert_output(
        custom_parser.parse(),
        EXPECTED_MAIN_PATH / "custom_import_cache_invalidation.py",
    )

    injected_parser = InjectingJsonSchemaParser(input_path, **parser_options)
    assert_output(
        injected_parser.parse(),
        EXPECTED_MAIN_PATH / "custom_import_cache_invalidation.py",
    )

    input_path = JSON_SCHEMA_DATA_PATH / "unique_items_enum_set.json"
    unique_items_parser = JsonSchemaParser(
        input_path,
        data_model_type=CacheAwareBaseModel,
        **{**parser_options, "use_unique_items_as_set": True},
    )
    assert_output(
        unique_items_parser.parse(),
        EXPECTED_MAIN_PATH / "custom_import_cache_unique_items.py",
    )

    alias_input_path = JSON_SCHEMA_DATA_PATH / "alias_import_alias" / "date.schema.json"
    alias_parser = JsonSchemaParser(
        alias_input_path,
        data_model_type=CacheAwareBaseModel,
        **{**parser_options, "base_path": alias_input_path.parent},
    )
    assert_output(
        alias_parser.parse(),
        EXPECTED_MAIN_PATH / "custom_import_cache_alias_invalidation.py",
    )

    generic_input_path = JSON_SCHEMA_DATA_PATH / "extra_fields.json"
    generic_parser = JsonSchemaParser(
        generic_input_path,
        data_model_type=CacheAwareBaseModel,
        extra_fields="forbid",
        use_generic_base_class=True,
        **{**parser_options, "base_path": generic_input_path.parent},
    )
    assert_output(
        generic_parser.parse(),
        EXPECTED_MAIN_PATH / "custom_import_cache_generic_base.py",
    )


def test_parser_preserves_cross_module_external_import_cache_hook() -> None:
    """Do not invoke an external model's cache hook from another built-in module."""
    from datamodel_code_generator.model import DataModel, get_data_model_types
    from datamodel_code_generator.model.msgspec import Struct
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    class ProbeStruct(Struct):
        clear_history: ClassVar[list[str]] = []

        def clear_imports_cache(self) -> None:
            state = "cached" if self._IMPORTS_CACHE_KEY in self.__dict__ else "empty"
            name = getattr(self, "class_name", "") or "?"
            self.clear_history.append(f"{name}:{state}")
            super().clear_imports_cache()

    class SelectiveExternalStructParser(JsonSchemaParser):
        def _create_data_model(
            self,
            model_type: type[DataModel] | None = None,
            **kwargs: Any,
        ) -> DataModel:
            reference = kwargs.get("reference")
            if (
                reference is not None
                and reference.name == "Type1"
                and (model_type is None or model_type is self.data_model_type)
            ):
                model_type = ProbeStruct
            return super()._create_data_model(model_type, **kwargs)

    model_types = get_data_model_types(
        DataModelType.MsgspecStruct,
        target_python_version=PythonVersion.PY_311,
    )
    input_path = JSON_SCHEMA_DATA_PATH / "discriminator_with_external_reference"
    parser = SelectiveExternalStructParser(
        input_path,
        base_path=input_path,
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=model_types.dump_resolve_reference_action,
        formatters=[Formatter.BUILTIN],
        target_python_version=PythonVersion.PY_311,
    )
    modules = cast("dict[tuple[str, ...], Any]", parser.parse())

    assert_generated_modules_output(modules, EXPECTED_MAIN_PATH / "custom_import_cache_cross_module")
    assert_output(
        "\n".join(ProbeStruct.clear_history) + "\n",
        EXPECTED_MAIN_PATH / "custom_import_cache_cross_module_history.txt",
    )
    assert_output(
        "\n".join("/".join(module) for module in modules) + "\n",
        EXPECTED_MAIN_PATH / "custom_import_cache_cross_module_order.txt",
    )


def test_parser_rechecks_external_enum_after_module_materialization() -> None:
    """Keep the external hook fallback when an inherited enum replaces a built-in wrapper."""
    from datamodel_code_generator import ModuleSplitMode
    from datamodel_code_generator.model import get_data_model_types
    from datamodel_code_generator.model.enum import Enum
    from datamodel_code_generator.parser.openapi import OpenAPIParser, OpenAPIScope

    class ProbeEnum(Enum):
        clear_history: ClassVar[list[str]] = []

        def clear_imports_cache(self) -> None:
            state = "cached" if self._IMPORTS_CACHE_KEY in self.__dict__ else "empty"
            name = getattr(self, "class_name", "") or "?"
            self.clear_history.append(f"{name}:{state}")
            super().clear_imports_cache()

    class SelectiveExternalEnumParser(OpenAPIParser):
        def _get_enum_model_class(self, type_: Any, enum_values: list[Any]) -> tuple[type[Enum], Any]:
            _, remaining_type = super()._get_enum_model_class(type_, enum_values)
            return ProbeEnum, remaining_type

    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    input_path = OPEN_API_DATA_PATH / "nested_enum.json"
    parser = SelectiveExternalEnumParser(
        input_path,
        base_path=input_path.parent,
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=model_types.dump_resolve_reference_action,
        formatters=[Formatter.BUILTIN],
        openapi_scopes=[OpenAPIScope.Schemas],
        target_python_version=PythonVersion.PY_311,
    )
    modules = cast(
        "dict[tuple[str, ...], Any]",
        parser.parse(module_split_mode=ModuleSplitMode.Single),
    )

    assert_generated_modules_output(modules, EXPECTED_MAIN_PATH / "custom_import_cache_inherited_enum")
    assert_output(
        "\n".join(ProbeEnum.clear_history) + "\n",
        EXPECTED_MAIN_PATH / "custom_import_cache_inherited_enum_history.txt",
    )
    assert_output(
        "\n".join("/".join(module) for module in modules) + "\n",
        EXPECTED_MAIN_PATH / "custom_import_cache_inherited_enum_order.txt",
    )


CLI_E2E_COVERED_GENERATE_KWARGS = {
    "emit_model_metadata",
    "infer_union_variant_names",
    "model_name_map",
}


def _generate_call_keyword_source(value: ast.expr) -> str:
    match value:
        case ast.Attribute(value=ast.Name(id="config")):
            return f"config.{value.attr}"
        case ast.Name():
            return value.id
    return ast.unparse(value)


def _run_generate_from_config_generate_kwargs() -> list[tuple[str, str]]:
    source = inspect.getsource(run_generate_from_config)
    module = ast.parse(source)
    function = module.body[0]
    if not isinstance(function, ast.FunctionDef):  # pragma: no cover
        msg = "run_generate_from_config source did not parse to a function"
        raise TypeError(msg)

    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "generate"
    ]
    if len(calls) != 1:  # pragma: no cover
        msg = f"Expected one generate() call, found {len(calls)}"
        raise AssertionError(msg)

    return [
        (keyword.arg, _generate_call_keyword_source(keyword.value))
        for keyword in calls[0].keywords
        if keyword.arg is not None and keyword.arg not in CLI_E2E_COVERED_GENERATE_KWARGS
    ]


def _run_generate_from_config_model_copy_updates() -> list[tuple[str, str]]:
    source = inspect.getsource(_generation_config)
    module = ast.parse(source)
    function = module.body[0]
    if not isinstance(function, ast.FunctionDef):  # pragma: no cover
        msg = "_generation_config source did not parse to a function"
        raise TypeError(msg)

    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "model_copy"
    ]
    if len(calls) != 1:  # pragma: no cover
        msg = f"Expected one model_copy() call, found {len(calls)}"
        raise AssertionError(msg)

    update_keyword = next((keyword for keyword in calls[0].keywords if keyword.arg == "update"), None)
    if update_keyword is None or not isinstance(update_keyword.value, ast.Dict):  # pragma: no cover
        msg = "model_copy(update=...) did not use a dict literal"
        raise AssertionError(msg)

    updates: list[tuple[str, str]] = []
    for key, value in zip(update_keyword.value.keys, update_keyword.value.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):  # pragma: no cover
            msg = f"Unexpected model_copy update key: {ast.unparse(key) if key is not None else None}"
            raise TypeError(msg)
        updates.append((key.value, _generate_call_keyword_source(value)))
    return updates


def test_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test debug flag functionality."""
    with pytest.raises(expected_exception=SystemExit):
        run_main_with_args(["--debug", "--help"])

    # Simulate pysnooper not being installed by making import fail
    monkeypatch.setitem(sys.modules, "pysnooper", None)
    with pytest.raises(expected_exception=SystemExit):
        run_main_with_args(["--debug", "--help"])


@pytest.mark.allow_direct_assert
def test_snooper_to_methods_without_pysnooper(mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test snooper_to_methods function without pysnooper installed."""
    # Simulate pysnooper not being installed by making import fail
    monkeypatch.setitem(sys.modules, "pysnooper", None)
    mock = mocker.Mock()
    assert snooper_to_methods()(mock) == mock


@pytest.mark.parametrize(argnames="no_color", argvalues=[False, True])
def test_show_help(no_color: bool, capsys: pytest.CaptureFixture[str]) -> None:
    """Test help output with and without color."""
    args = ["--no-color"] if no_color else []
    args += ["--help"]

    run_main_with_system_exit(
        args,
        expected_code=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / "help" / ("no_color.txt" if no_color else "color.txt"),
    )


@pytest.mark.parametrize(
    ("args", "expected_stderr_name"),
    [
        (["--output-model-tipe"], "output_model_type.txt"),
        (["--output-model-tipe=pydantic_v2.BaseModel"], "output_model_type_with_value.txt"),
        (["--output-model-tipe", "--input-file-tipe"], "multiple_options.txt"),
        (["--output-model-tipe", "--output-model-tipe"], "deduplicated_option.txt"),
        (["--not-a-valid-option"], "unknown_option.txt"),
        (["not-an-option"], "positional_argument.txt"),
        (["--", "--output-model-tipe"], "option_terminator.txt"),
        (["--output-model-type"], "missing_option_value.txt"),
    ],
    ids=[
        "close-match",
        "close-match-with-value",
        "multiple-options",
        "deduplicated-option",
        "no-match",
        "positional",
        "option-terminator",
        "missing-value",
    ],
)
def test_invalid_cli_option_suggestions(
    args: list[str], expected_stderr_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Suggest close option names without changing unrelated parser errors."""
    run_main_with_system_exit(args, expected_code=Exit.ERROR)
    assert_output(
        capsys
        .readouterr()
        .err.replace("usage: \n", "usage:\n")
        .replace(f"{arg_parser.prog}: error:", "pytest: error:"),
        CLI_OPTION_SUGGESTIONS_PATH / expected_stderr_name,
    )


def test_show_help_when_no_input(mocker: MockerFixture) -> None:
    """Test help display when no input is provided."""
    print_help_mock = mocker.patch("datamodel_code_generator.__main__.arg_parser.print_help")
    isatty_mock = mocker.patch("sys.stdin.isatty", return_value=True)
    run_main_with_args([], expected_exit=Exit.ERROR)
    isatty_mock.assert_called()
    print_help_mock.assert_called()


@pytest.mark.cli_doc(
    options=["--preset"],
    option_description="""Apply an immutable built-in option preset.

The `standard-py312-20260619` preset enables the recommended modern Python output style for
new projects. The preset name pins generated Python syntax and backports.""",
    input_schema="jsonschema/person.json",
    cli_args=["--preset", "standard-py312-20260619"],
    golden_output="main/standard_preset_pydantic_v2.py",
    related_options=["--target-python-version"],
    primary=True,
)
@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support Python 3.12 target version")
@freeze_time(TIMESTAMP)
def test_standard_preset_pydantic_v2(output_file: Path) -> None:
    """Generate Pydantic v2 output using the standard preset."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--preset", "standard-py312-20260619"],
        assert_func=assert_file_content,
        expected_file="standard_preset_pydantic_v2.py",
    )


@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support Python 3.12 target version")
@freeze_time(TIMESTAMP)
def test_generate_standard_preset_public_api_kwargs(output_file: Path) -> None:
    """Generate Pydantic v2 output using preset through public generate() kwargs."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        preset="standard-py312-20260619",
        assert_func=assert_file_content,
        expected_file="standard_preset_pydantic_v2.py",
    )


@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support Python 3.12 target version")
@freeze_time(TIMESTAMP)
def test_generate_standard_preset_public_api_config(output_file: Path) -> None:
    """Generate Pydantic v2 output using preset through GenerateConfig."""
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        preset="standard-py312-20260619",
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=88,
    )
    generate(input_=JSON_SCHEMA_DATA_PATH / "person.json", config=config)
    assert_file_content(output_file, "standard_preset_pydantic_v2.py")


def test_run_generate_from_config_json_mapping_file_like(output_file: Path) -> None:
    """Generate from Config when JSON mapping options are file-like objects."""
    config = Config(
        input_file_type=InputFileType.JsonSchema,
        base_class_map=cast(
            "Any",
            StringIO(json.dumps({"Person": "custom.bases.PersonBase", "Animal": "custom.bases.AnimalBase"})),
        ),
    )
    run_generate_from_config(
        config=config,
        input_=JSON_SCHEMA_DATA_PATH / "base_class_map.json",
        output=output_file,
        extra_template_data=None,
        aliases=None,
        serialization_aliases=None,
        command_line=None,
        custom_formatters_kwargs=None,
    )
    assert_file_content(output_file, "jsonschema/base_class_map.py")


@freeze_time(TIMESTAMP)
def test_generate_standard_preset_public_api_explicit_options(output_file: Path) -> None:
    """Generate output when every preset option is already explicit."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        preset="standard-py310-20260619",
        target_python_version=PythonVersion.PY_310,
        use_standard_collections=True,
        use_union_operator=True,
        use_annotated=False,
        enum_field_as_literal=LiteralType.One,
        use_subclass_enum=True,
        collapse_root_models=True,
        strict_nullable=True,
        set_default_enum_member=True,
        disable_timestamp=True,
        snake_case_field=True,
        allow_population_by_field_name=True,
        use_frozen_field=True,
        assert_func=assert_file_content,
        expected_file="standard_preset_no_use_annotated.py",
    )


def test_generate_standard_preset_public_api_reports_unknown_preset() -> None:
    """Unknown public API presets raise the same user-facing error type as CLI presets."""
    with pytest.raises(Error, match="Unknown preset: 'unknown-preset'"):
        generate(
            input_=JSON_SCHEMA_DATA_PATH / "person.json",
            input_file_type=InputFileType.JsonSchema,
            preset="unknown-preset",
        )


@freeze_time(TIMESTAMP)
def test_standard_preset_uses_literal_for_single_value_enum(output_file: Path) -> None:
    """The standard preset renders single-value enum fields as Literal."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_literal_typed_dict.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--preset", "standard-py310-20260619"],
        assert_func=assert_file_content,
        expected_file="standard_preset_enum_literal_one.py",
    )


@freeze_time(TIMESTAMP)
def test_standard_preset_no_snake_case_cli_override(output_file: Path) -> None:
    """CLI --no-* flags override preset-supplied options."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--preset",
            "standard-py310-20260619",
            "--no-snake-case-field",
        ],
        assert_func=assert_file_content,
        expected_file=(
            "standard_preset_no_snake_case_black_lt_24.py" if BLACK_LT_24 else "standard_preset_no_snake_case.py"
        ),
    )


@freeze_time(TIMESTAMP)
def test_standard_preset_no_use_annotated_cli_override(output_file: Path) -> None:
    """CLI --no-use-annotated overrides the preset and keeps field constraints off."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--preset",
            "standard-py310-20260619",
            "--no-use-annotated",
        ],
        assert_func=assert_file_content,
        expected_file="standard_preset_no_use_annotated.py",
    )


@freeze_time(TIMESTAMP)
def test_standard_preset_allows_original_field_name_delimiter_after_merge(output_file: Path) -> None:
    """Preset-supplied snake-case conversion is visible to final validation."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--preset",
            "standard-py310-20260619",
            "--original-field-name-delimiter",
            "-",
        ],
        assert_func=assert_file_content,
        expected_file="standard_preset_pydantic_v2.py",
    )


@freeze_time(TIMESTAMP)
def test_standard_preset_cli_overrides_pyproject_option(output_file: Path, tmp_path: Path) -> None:
    """CLI --preset overrides pyproject values unless the same option is explicit on CLI."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "person.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--preset", "standard-py310-20260619"],
            assert_func=assert_file_content,
            expected_file="standard_preset_pydantic_v2.py",
            copy_files=[
                (DATA_PATH / "config" / "pyproject_standard_preset_cli_override.toml", tmp_path / "pyproject.toml")
            ],
        )


@freeze_time(TIMESTAMP)
def test_standard_preset_pyproject_uses_final_output_model_type(output_file: Path, tmp_path: Path) -> None:
    """Pyproject preset adapters resolve after CLI output-model-type overrides."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "person.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "dataclasses.dataclass"],
            assert_func=assert_file_content,
            expected_file="standard_preset_dataclass.py",
            copy_files=[(DATA_PATH / "config" / "pyproject_standard_preset.toml", tmp_path / "pyproject.toml")],
        )


@freeze_time(TIMESTAMP)
@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support Python 3.12 target version")
def test_standard_preset_msgspec_struct(output_file: Path) -> None:
    """Generate msgspec.Struct output using the standard preset."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--preset",
            "standard-py312-20260619",
            "--output-model-type",
            "msgspec.Struct",
        ],
        assert_func=assert_file_content,
        expected_file=(
            "standard_preset_msgspec_struct_black_lt_24.py" if BLACK_LT_24 else "standard_preset_msgspec_struct.py"
        ),
    )


@freeze_time(TIMESTAMP)
@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support Python 3.12 target version")
def test_standard_preset_typed_dict(output_file: Path) -> None:
    """Generate TypedDict output using the standard preset."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--preset",
            "standard-py312-20260619",
            "--output-model-type",
            "typing.TypedDict",
        ],
        assert_func=assert_file_content,
        expected_file="standard_preset_typed_dict.py",
    )


@freeze_time(TIMESTAMP)
def test_standard_preset_target_py310_does_not_force_specialized_enum(output_file: Path) -> None:
    """The standard preset does not force StrEnum when target Python is 3.10."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "string_enum.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--preset", "standard-py310-20260619"],
        assert_func=assert_file_content,
        expected_file="standard_preset_string_enum_py310.py",
    )


@freeze_time(TIMESTAMP)
def test_standard_preset_accepts_matching_target_python_version(output_file: Path) -> None:
    """Explicit target Python version is accepted when it matches the preset name."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--preset", "standard-py310-20260619", "--target-python-version", "3.10"],
        assert_func=assert_file_content,
        expected_file="standard_preset_pydantic_v2.py",
    )


@freeze_time(TIMESTAMP)
def test_practical_preset_pydantic_v2(output_file: Path) -> None:
    """Generate Pydantic v2 output using the practical preset."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "practical_preset.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--preset", "practical-py310-20260619"],
        assert_func=assert_file_content,
        expected_file="practical_preset_pydantic_v2.py",
    )


@freeze_time(TIMESTAMP)
def test_practical_preset_keeps_homogeneous_fixed_length_arrays_as_lists(output_file: Path) -> None:
    """Keep existing practical presets independent from the new tuple option."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "fixed_length_array_tuples.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--preset", "practical-py310-20260619"],
        assert_func=assert_file_content,
        expected_file="openapi/fixed_length_array_tuples_practical_preset.py",
        force_exec_validation=True,
    )


def test_standard_preset_requires_matching_target_python_version(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preset Python target must match the explicit target Python version."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--preset", "standard-py312-20260619", "--target-python-version", "3.10"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=(
            "--preset standard-py312-20260619 targets Python 3.12; current --target-python-version is 3.10."
        ),
        file_should_not_exist=output_file,
    )


def test_standard_preset_reports_unknown_pyproject_preset(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Unknown pyproject presets fail with a CLI error instead of leaking resolver exceptions."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "person.json",
            output_path=output_file,
            input_file_type="jsonschema",
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains=(
                "Unknown preset: 'unknown-preset'. Available presets: "
                "standard-py310-20260619, standard-py311-20260619, standard-py312-20260619, "
                "standard-py313-20260619, standard-py314-20260619, practical-py310-20260619, "
                "practical-py311-20260619, practical-py312-20260619, practical-py313-20260619, "
                "practical-py314-20260619"
            ),
            file_should_not_exist=output_file,
            copy_files=[(DATA_PATH / "config" / "pyproject_unknown_preset.toml", tmp_path / "pyproject.toml")],
        )


def test_generated_pydantic_v2_model_accepts_runtime_value(output_file: Path) -> None:
    """Generated Pydantic v2 model validates a schema-valid payload at runtime."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pydantic_v2_runtime_value.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema.value,
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="generated_person",
        model_name="Person",
        valid_json='{"name": "Alice", "age": 30}',
        invalid_json='{"name": "Alice", "age": -1}',
        expected_error_type="greater_than_equal",
        expected_attribute_path=("name",),
        expected_attribute_value="Alice",
    )


def test_list_deprecations(capsys: pytest.CaptureFixture[str]) -> None:
    """List registered deprecations without requiring an input schema."""
    run_main_with_args(
        ["--list-deprecations"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / "list_deprecations.txt",
        assert_no_stderr=True,
    )


def test_list_deprecations_json(capsys: pytest.CaptureFixture[str]) -> None:
    """List registered deprecations as JSON."""
    run_main_with_args(
        ["--list-deprecations", "json"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / "list_deprecations_json.txt",
        assert_no_stderr=True,
    )


@pytest.mark.parametrize(
    ("format_", "expected_file"),
    [
        ("table", "list_scheduled_deprecations.txt"),
        ("json", "list_scheduled_deprecations_json.txt"),
        ("markdown", "list_scheduled_deprecations_markdown.txt"),
    ],
)
def test_list_scheduled_deprecations(
    format_: str,
    expected_file: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List scheduled entries with their status through every CLI format."""
    scheduled = Deprecation(
        id="test.scheduled-change",
        kind="cli-option",
        target="--scheduled-change",
        message="--scheduled-change will be deprecated.",
        warning_since="8.0.0",
        removal_version=None,
        replacement="--replacement",
        status="scheduled",
    )
    monkeypatch.setitem(DEPRECATIONS, scheduled.id, scheduled)

    run_main_with_args(
        ["--list-deprecations", format_],
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / expected_file,
        assert_no_stderr=True,
    )


def test_list_experimental(capsys: pytest.CaptureFixture[str]) -> None:
    """List registered experimental features without requiring an input schema."""
    run_main_with_args(
        ["--list-experimental"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / "list_experimental.txt",
        assert_no_stderr=True,
    )


def test_list_experimental_json(capsys: pytest.CaptureFixture[str]) -> None:
    """List registered experimental features as JSON."""
    run_main_with_args(
        ["--list-experimental", "json"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / "list_experimental_json.txt",
        assert_no_stderr=True,
    )


@pytest.mark.allow_direct_assert
def test_no_args_has_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No argument should have a default value set because it would override pyproject.toml values.

    Default values are set in __main__.Config class.
    """
    namespace = Namespace()
    monkeypatch.setattr("datamodel_code_generator.__main__.namespace", namespace)
    run_main_with_args([], expected_exit=Exit.ERROR)
    for field in Config.get_fields():
        assert getattr(namespace, field, None) is None


def test_cli_pyproject_ignores_generate_only_options(output_file: Path, tmp_path: Path) -> None:
    """CLI pyproject config should keep ignoring API-only generate() options."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "force_optional_required.json",
            output_path=output_file,
            assert_func=assert_file_content,
            expected_file="jsonschema/cli_pyproject_ignores_generate_only_options.py",
            copy_files=[(DATA_PATH / "config" / "pyproject_generate_only_options.toml", tmp_path / "pyproject.toml")],
        )


@pytest.mark.allow_direct_assert
def test_create_config_empty_pyproject_uses_single_validated_cli_config() -> None:
    """An empty pyproject config can validate the final CLI config directly."""
    config = _create_config(
        {},
        {
            "input": str(JSON_SCHEMA_DATA_PATH / "force_optional_required.json"),
            "use_annotated": True,
        },
    )

    assert config.input == (JSON_SCHEMA_DATA_PATH / "force_optional_required.json").resolve()
    assert config.field_constraints is True


@pytest.mark.allow_direct_assert
def test_create_config_http_backend_uses_auto_and_allows_explicit_overrides() -> None:
    """HTTP backend policy is shared by defaults, pyproject, and CLI overrides."""
    assert _create_config({}, {}).http_backend is HTTPBackend.AUTO
    assert _create_config({"http_backend": "httpx2"}, {}).http_backend is HTTPBackend.HTTPX2
    assert _create_config({"http_backend": "httpx2"}, {"http_backend": "httpx"}).http_backend is HTTPBackend.HTTPX


@pytest.mark.allow_direct_assert
def test_create_config_pyproject_branch_keeps_input_source_override() -> None:
    """Non-empty pyproject config keeps the existing Config.merge_args ordering."""
    config = _create_config(
        {"input": str(JSON_SCHEMA_DATA_PATH / "force_optional_required.json"), "validation": True},
        {"url": "https://example.com/schema.json"},
    )

    assert config.input is None
    assert config.url is not None
    assert config.url.geturl() == "https://example.com/schema.json"
    assert config.validation is True


@pytest.mark.parametrize(
    ("config_name", "args"),
    [
        pytest.param("pyproject_msgspec_implicit.toml", (), id="single"),
        pytest.param("pyproject_msgspec_implicit_job.toml", ("--all-jobs",), id="batch"),
    ],
)
def test_pyproject_msgspec_derives_constraints_from_final_config(
    config_name: str,
    args: tuple[str, ...],
    tmp_path: Path,
) -> None:
    """Pyproject and batch msgspec settings preserve constraints from a nested working directory."""
    project_path = tmp_path / "project"
    nested_path = project_path / "nested"
    nested_path.mkdir(parents=True)
    shutil.copyfile(
        JSON_SCHEMA_DATA_PATH / "msgspec_array_length_constraints.json",
        project_path / "msgspec_array_length_constraints.json",
    )
    shutil.copyfile(DATA_PATH / "config" / config_name, project_path / "pyproject.toml")

    with chdir(nested_path):
        run_main_with_args(args)

    assert_file_content(project_path / "model.py", "jsonschema/msgspec_array_length_constraints_use_annotated.py")


def test_cli_output_model_override_does_not_keep_pyproject_msgspec_defaults(tmp_path: Path) -> None:
    """A CLI backend override does not retain implicit msgspec Annotated output."""
    project_path = tmp_path / "project"
    nested_path = project_path / "nested"
    nested_path.mkdir(parents=True)
    shutil.copyfile(
        JSON_SCHEMA_DATA_PATH / "msgspec_array_length_constraints.json",
        project_path / "msgspec_array_length_constraints.json",
    )
    shutil.copyfile(DATA_PATH / "config" / "pyproject_msgspec_implicit.toml", project_path / "pyproject.toml")

    with chdir(nested_path):
        run_main_with_args(["--output-model-type", "pydantic_v2.BaseModel"])

    assert_file_content(project_path / "model.py", "pyproject_msgspec_cli_pydantic.py")


@pytest.mark.parametrize(
    "config_name",
    [
        "pyproject_msgspec_use_annotated.toml",
        "pyproject_msgspec_use_annotated_field_constraints.toml",
    ],
)
def test_cli_no_use_annotated_clears_only_implicit_field_constraints(config_name: str, tmp_path: Path) -> None:
    """An explicit CLI opt-out preserves only explicitly configured constraints."""
    project_path = tmp_path / "project"
    nested_path = project_path / "nested"
    nested_path.mkdir(parents=True)
    shutil.copyfile(
        JSON_SCHEMA_DATA_PATH / "msgspec_array_length_constraints.json",
        project_path / "msgspec_array_length_constraints.json",
    )
    shutil.copyfile(DATA_PATH / "config" / config_name, project_path / "pyproject.toml")

    with chdir(nested_path):
        run_main_with_args(["--no-use-annotated"])

    assert_file_content(project_path / "model.py", "pyproject_msgspec_no_annotated.py")


def test_cli_relative_output_remains_relative_to_the_invocation_directory(tmp_path: Path) -> None:
    """Only pyproject-origin paths use the pyproject directory as their base."""
    project_path = tmp_path / "project"
    nested_path = project_path / "nested"
    nested_path.mkdir(parents=True)
    shutil.copyfile(
        JSON_SCHEMA_DATA_PATH / "msgspec_array_length_constraints.json",
        project_path / "msgspec_array_length_constraints.json",
    )
    shutil.copyfile(DATA_PATH / "config" / "pyproject_msgspec_implicit.toml", project_path / "pyproject.toml")

    with chdir(nested_path):
        run_main_with_args(["--output", "cli-model.py"])

    assert_file_content(nested_path / "cli-model.py", "jsonschema/msgspec_array_length_constraints_use_annotated.py")


def test_cli_without_pyproject_keeps_absolute_input_and_output_paths(
    monkeypatch: pytest.MonkeyPatch, output_file: Path
) -> None:
    """A CLI invocation outside a pyproject continues to use its supplied paths."""
    monkeypatch.chdir(output_file.parent)

    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp"],
        assert_func=assert_file_content,
        expected_file="person.py",
    )


@pytest.mark.allow_direct_assert
def test_generate_config_values_supports_non_pydantic_config() -> None:
    """Non-Pydantic config-like objects keep the public generate(config=...) fallback."""

    class ConfigLike:
        pass

    config_like = ConfigLike()
    config_like.dynamic = "value"

    assert _generate_config_values(cast("Any", config_like)) == {"dynamic": "value"}


@pytest.mark.allow_direct_assert
def test_internal_parser_config_model_copy_supports_deep_update() -> None:
    """The private compatibility config keeps its model-copy contract."""
    nested = {"items": [1]}
    config = _create_parser_config(
        GenerateConfig(),
        cast("Any", {"name": "before", "nested": nested}),
    )
    plain_copy = config.model_copy()
    copied = config.model_copy(update={"name": "after"}, deep=True)
    retry_copy = config.model_copy(
        update={
            "repair_invalid_dotted_stdout": False,
            "forced_invalid_dotted_stdout_repair_modules": (("models",),),
        }
    )
    nested["items"].append(2)

    assert plain_copy.name == "before"
    assert copied.name == "after"
    assert copied.nested == {"items": [1]}
    assert retry_copy.repair_invalid_dotted_stdout is False
    assert retry_copy.forced_invalid_dotted_stdout_repair_modules == (("models",),)
    assert config._source_context is not None
    assert config._source_context.encoding == "utf-8"


@pytest.mark.allow_direct_assert
def test_create_parser_config_filters_generation_fields_and_freezes_source_context() -> None:
    """Parser config owns declared fields while source policy stays in a typed context."""
    from dataclasses import FrozenInstanceError

    from datamodel_code_generator._parser_context import ParserSourceContext
    from datamodel_code_generator.config import JSONSchemaParserConfig

    config = GenerateConfig(output=Path("generated.py"), encoding="utf-16", http_timeout=3.5)
    parser_config = _create_typed_parser_config(
        config,
        JSONSchemaParserConfig,
        cast("Any", {"schema_version_mode": None}),
    )
    source_context = parser_config._source_context

    assert isinstance(source_context, ParserSourceContext)
    assert parser_config.http_timeout == pytest.approx(3.5)
    assert not hasattr(parser_config, "output")
    assert source_context.encoding == "utf-16"
    assert not hasattr(source_context, "__dict__")
    attribute = "encoding"
    with pytest.raises(FrozenInstanceError):
        setattr(source_context, attribute, "utf-8")


@pytest.mark.allow_direct_assert
def test_boolean_optional_option_sets_are_pinned() -> None:
    """Pin BooleanOptionalAction destinations used by CLI command generation."""
    boolean_optional_dests = [
        action.dest for action in arg_parser._actions if isinstance(action, BooleanOptionalAction)
    ]

    assert boolean_optional_dests == snapshot([
        "allow_remote_refs",
        "allow_private_network",
        "allow_population_by_field_name",
        "collapse_root_models",
        "treat_dot_as_module",
        "strict_dotted_module_names",
        "use_standard_primitive_types",
        "use_annotated",
        "use_standard_collections",
        "use_specialized_enum",
        "use_union_operator",
        "use_closed_typed_dict",
        "snake_case_field",
        "use_frozen_field",
        "use_type_checking_imports",
    ])


@pytest.mark.allow_direct_assert
def test_run_generate_from_config_generate_kwargs_are_pinned() -> None:
    """Pin the validated CLI Config values overlaid before generate() runs."""
    assert _run_generate_from_config_generate_kwargs() == snapshot([
        ("input_", "input_"),
        ("config", "cast('Any', generation_config)"),
    ])
    assert _run_generate_from_config_model_copy_updates() == snapshot([
        ("input_filename", "input_filename"),
        ("output", "output"),
        ("preset", "None"),
        ("extra_template_data", "extra_template_data"),
        ("aliases", "aliases"),
        ("serialization_aliases", "serialization_aliases"),
        ("command_line", "command_line"),
        ("apply_default_values_for_required_fields", "config.use_default"),
        ("force_optional_for_required_fields", "config.force_optional"),
        ("custom_formatters_kwargs", "custom_formatters_kwargs"),
        ("settings_path", "settings_path"),
        ("validators", "validators"),
        ("default_value_overrides", "default_value_overrides"),
    ])


@pytest.mark.allow_direct_assert
def test_generate_call_keyword_source_uses_unparse_fallback() -> None:
    """Non-trivial generate() keyword expressions are represented by AST source."""
    expression = ast.parse("generate(value=1 + 2)").body[0]
    if not isinstance(expression, ast.Expr):  # pragma: no cover
        raise TypeError
    call = expression.value
    if not isinstance(call, ast.Call):  # pragma: no cover
        raise TypeError

    assert _generate_call_keyword_source(call.keywords[0].value) == "1 + 2"


@pytest.mark.allow_direct_assert
@pytest.mark.skipif(platform.system() == "Windows", reason="text-mode writes use CRLF on Windows")
def test_generated_file_line_endings_are_lf_with_single_trailing_newline(output_file: Path) -> None:
    """Generated files use LF line endings and exactly one trailing newline."""
    result = generate(
        {"type": "object", "properties": {"name": {"type": "string"}}},
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        disable_timestamp=True,
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )

    assert result is None
    content = output_file.read_bytes()
    assert b"\r" not in content
    assert content.endswith(b"\n")
    assert not content.endswith(b"\n\n")


def test_space_and_special_characters_dict(output_file: Path) -> None:
    """Test dict input with space and special characters."""
    run_main_and_assert(
        input_path=PYTHON_DATA_PATH / "space_and_special_characters_dict.py",
        output_path=output_file,
        input_file_type="dict",
        assert_func=assert_file_content,
    )


def test_space_and_special_characters_dict_stdin(monkeypatch: pytest.MonkeyPatch, output_file: Path) -> None:
    """Test dict stdin input is parsed as a Python literal."""
    run_main_and_assert(
        stdin_path=PYTHON_DATA_PATH / "space_and_special_characters_dict.py",
        output_path=output_file,
        monkeypatch=monkeypatch,
        input_file_type="dict",
        assert_func=assert_file_content,
    )


@freeze_time("2024-12-14")
def test_direct_input_dict(tmp_path: Path) -> None:
    """Test direct dict input code generation."""
    output_file = tmp_path / "output.py"
    generate(
        {"foo": 1, "bar": {"baz": 2}},
        input_file_type=InputFileType.Dict,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        snake_case_field=True,
    )
    assert_file_content(output_file)


@pytest.mark.parametrize(
    ("input_file", "expected_file"),
    [
        ("direct_tool.json", "mcp_tools/direct_tool.py"),
        ("tools_list.json", "mcp_tools/tools_list.py"),
        ("server_definition.json", "mcp_tools/server_definition.py"),
        ("schema_tool_definitions.json", "mcp_tools/schema_tool_definitions.py"),
        ("top_level_list.json", "mcp_tools/top_level_list.py"),
        ("mcp_servers.json", "mcp_tools/mcp_servers.py"),
        ("servers_list.json", "mcp_tools/servers_list.py"),
        ("top_level_tool_definitions.json", "mcp_tools/top_level_tool_definitions.py"),
        ("definitions_ref.json", "mcp_tools/definitions_ref.py"),
    ],
)
def test_mcp_tools(input_file: str, expected_file: str, output_file: Path) -> None:
    """Generate models from MCP tool schema profiles."""
    run_main_and_assert(
        input_path=DATA_PATH / "mcp_tools" / input_file,
        output_path=output_file,
        input_file_type="mcp-tools",
        assert_func=assert_file_content,
        expected_file=expected_file,
    )


def test_mcp_tools_dangling_local_ref(output_file: Path) -> None:
    """Warn for an unresolved local ref while preserving MCP tool output."""
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref"):
        run_main_and_assert(
            input_path=DATA_PATH / "mcp_tools" / "external_ref.json",
            output_path=output_file,
            input_file_type="mcp-tools",
            assert_func=assert_file_content,
            expected_file="mcp_tools/external_ref.py",
        )


@pytest.mark.parametrize(
    "input_name",
    [
        "nested_definition_references",
        "nested_keyword_references",
        "boolean_definition_references",
        "dynamic_definition_references",
    ],
)
def test_mcp_tools_hoisted_definition_references_conversion(input_name: str) -> None:
    """Preserve references to renamed definitions while hoisting MCP schemas."""
    source = json.loads((DATA_PATH / "mcp_tools" / f"{input_name}.json").read_text())
    converted = convert_mcp_tools_to_jsonschema(source)
    assert_output(
        f"{json.dumps(converted, indent=2)}\n",
        EXPECTED_MAIN_PATH / "mcp_tools" / f"{input_name}.txt",
    )


def test_mcp_tools_hoisted_definition_references_cli(output_file: Path) -> None:
    """Generate strict MCP models with renamed nested definitions through the CLI."""
    run_main_and_assert(
        input_path=DATA_PATH / "mcp_tools" / "nested_definition_references.json",
        output_path=output_file,
        input_file_type="mcp-tools",
        assert_func=assert_file_content,
        expected_file="mcp_tools/nested_definition_references.py",
        extra_args=["--strict-refs", "--disable-timestamp"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="mcp_nested_definition_references_cli",
        model_name="DemoToolInput",
        valid_json='{"outer":{"inner":"ok"},"escaped":1,"node":{}}',
        invalid_json='{"outer":{"inner":1},"escaped":1,"node":{}}',
        expected_error_type="string_type",
    )


def test_mcp_tools_hoisted_definition_references_api(output_file: Path) -> None:
    """Generate strict MCP models with renamed nested definitions through the API."""
    run_generate_file_and_assert(
        input_path=DATA_PATH / "mcp_tools" / "nested_definition_references.json",
        output_path=output_file,
        input_file_type=InputFileType.MCPTools,
        assert_func=assert_file_content,
        expected_file="mcp_tools/nested_definition_references.py",
        strict_refs=True,
        disable_timestamp=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="mcp_nested_definition_references_api",
        model_name="DemoToolInput",
        valid_json='{"outer":{"inner":"ok"},"escaped":1,"node":{}}',
        invalid_json='{"outer":{"inner":1},"escaped":1,"node":{}}',
        expected_error_type="string_type",
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_mcp_tools_hoisted_keyword_references(entrypoint: str, output_file: Path) -> None:
    """Keep schemas below keyword-shaped property names when hoisting definitions."""
    input_path = DATA_PATH / "mcp_tools" / "nested_keyword_references.json"
    expected_file = "mcp_tools/nested_keyword_references.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="mcp-tools",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=["--strict-refs", "--disable-timestamp"],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.MCPTools,
            assert_func=assert_file_content,
            expected_file=expected_file,
            strict_refs=True,
            disable_timestamp=True,
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"mcp_hoisted_keyword_references_{entrypoint}",
        model_name="NestedInput",
        valid_json='{"outer":{"default":"ok","const":"ok","enum":"ok","examples":"ok"}}',
        invalid_json='{"outer":{"default":1}}',
        expected_error_type="string_type",
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_mcp_tools_dynamic_definition_references(entrypoint: str, output_file: Path) -> None:
    """Resolve dynamic pointers to hoisted definitions without dropping their types."""
    input_path = DATA_PATH / "mcp_tools" / "dynamic_definition_references.json"
    expected_file = "mcp_tools/dynamic_definition_references.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="mcp-tools",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=["--strict-refs", "--disable-timestamp"],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.MCPTools,
            assert_func=assert_file_content,
            expected_file=expected_file,
            strict_refs=True,
            disable_timestamp=True,
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"mcp_dynamic_definition_references_{entrypoint}",
        model_name="DynamicInput",
        valid_json=(DATA_PATH / "mcp_tools" / "dynamic_valid.json").read_text(),
        invalid_json=(DATA_PATH / "mcp_tools" / "dynamic_invalid.json").read_text(),
        expected_error_type="string_type",
    )


@pytest.mark.parametrize(
    "input_name",
    [
        "referenced_false_definition",
        "referenced_false_output_definition",
        "referenced_nested_false_definition",
        "referenced_false_dynamic_definition",
        "negated_false_definition",
    ],
)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_mcp_tools_referenced_false_definitions(input_name: str, entrypoint: str, output_file: Path) -> None:
    """Keep valid tool inputs usable when an optional or negated definition is false."""
    input_path = DATA_PATH / "mcp_tools" / f"{input_name}.json"
    expected_file = f"mcp_tools/{input_name}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="mcp-tools",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=["--disable-timestamp", "--formatters", "builtin"],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.MCPTools,
            assert_func=assert_file_content,
            expected_file=expected_file,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        )
    payload = json.loads((DATA_PATH / "mcp_tools/boolean_definition_payloads.json").read_text())[input_name]
    assert_generated_model_json_validation(
        output_file,
        module_name=f"mcp_{input_name}_{entrypoint}",
        model_name=payload["model"],
        valid_json=json.dumps(payload["valid"]),
        invalid_json=json.dumps(payload["invalid"]),
        expected_error_type=payload["error"],
    )


@pytest.mark.parametrize("input_name", ["schema_value_references", "schema_reference_positions"])
def test_mcp_tools_schema_value_references_conversion(input_name: str) -> None:
    """Keep schema-instance values unchanged while normalizing MCP definitions."""
    source = json.loads((DATA_PATH / "mcp_tools" / f"{input_name}.json").read_text())
    with assert_inputs_not_mutated({"source": source}):
        converted = convert_mcp_tools_to_jsonschema(source)
    assert_output(
        f"{json.dumps(converted, indent=2)}\n",
        EXPECTED_MAIN_PATH / "mcp_tools" / f"{input_name}.txt",
    )


def test_mcp_tools_schema_value_references_isolated() -> None:
    """Keep converted instance values independently mutable from the source."""
    source = json.loads((DATA_PATH / "mcp_tools" / "schema_value_references.json").read_text())
    converted = convert_mcp_tools_to_jsonschema(source)
    assert_mutable_copy_is_isolated(
        original=source["inputSchema"]["properties"]["item"]["default"],
        copied=converted["$defs"]["SchemaValuesInput"]["properties"]["item"]["default"],
        mutate_copied=lambda value: value["nested"].append({"$ref": "changed"}),
        label="MCP default payload",
    )


@pytest.mark.parametrize("input_name", ["schema_value_references", "schema_value_references_compact"])
def test_mcp_tools_schema_value_references_cli(output_file: Path, input_name: str) -> None:
    """Keep MCP schema-instance values in strict CLI-generated model defaults."""
    run_main_and_assert(
        input_path=DATA_PATH / "mcp_tools" / f"{input_name}.json",
        output_path=output_file,
        input_file_type="mcp-tools",
        assert_func=assert_file_content,
        expected_file=f"mcp_tools/{input_name}.py",
        extra_args=[
            "--strict-refs",
            "--disable-timestamp",
            *(["--formatters", "builtin"] if input_name == "schema_value_references" else []),
        ],
    )
    source = json.loads((DATA_PATH / "mcp_tools" / f"{input_name}.json").read_text())
    for model_name, field_name in (
        ("SchemaValuesInput", "item"),
        ("SchemaValuesInput", "sequence"),
        ("SchemaValuesOutput", "item"),
        ("SchemaValuesOutput", "sequence"),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"mcp_schema_value_references_cli_{model_name}",
            model_name=model_name,
            valid_json="{}",
            invalid_json=json.dumps({field_name: ["not-an-object"] if field_name == "sequence" else "not-an-object"}),
            expected_error_type="dict_type",
            expected_attribute_path=(field_name,),
            expected_attribute_value=source["outputSchema" if model_name == "SchemaValuesOutput" else "inputSchema"][
                "properties"
            ][field_name]["default"],
        )


@pytest.mark.parametrize("input_name", ["schema_value_references", "schema_value_references_compact"])
def test_mcp_tools_schema_value_references_api(output_file: Path, input_name: str) -> None:
    """Keep MCP schema-instance values in strict API-generated model defaults."""
    run_generate_file_and_assert(
        input_path=DATA_PATH / "mcp_tools" / f"{input_name}.json",
        output_path=output_file,
        input_file_type=InputFileType.MCPTools,
        assert_func=assert_file_content,
        expected_file=f"mcp_tools/{input_name}.py",
        strict_refs=True,
        disable_timestamp=True,
        **({"formatters": [Formatter.BUILTIN]} if input_name == "schema_value_references" else {}),
    )
    source = json.loads((DATA_PATH / "mcp_tools" / f"{input_name}.json").read_text())
    for model_name, field_name in (
        ("SchemaValuesInput", "item"),
        ("SchemaValuesInput", "sequence"),
        ("SchemaValuesOutput", "item"),
        ("SchemaValuesOutput", "sequence"),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"mcp_schema_value_references_api_{model_name}",
            model_name=model_name,
            valid_json="{}",
            invalid_json=json.dumps({field_name: ["not-an-object"] if field_name == "sequence" else "not-an-object"}),
            expected_error_type="dict_type",
            expected_attribute_path=(field_name,),
            expected_attribute_value=source["outputSchema" if model_name == "SchemaValuesOutput" else "inputSchema"][
                "properties"
            ][field_name]["default"],
        )


@pytest.mark.parametrize(argnames="input_kind", argvalues=["mapping", "list", "string"])
def test_mcp_tools_generate_direct_input(input_kind: str, output_file: Path) -> None:
    """Generate MCP tool models from direct generate() input values."""
    input_file = "direct_tool.json"
    expected_file = "mcp_tools/direct_tool.py"
    match input_kind:
        case "list":
            input_file = "top_level_list.json"
            expected_file = "mcp_tools/top_level_list.py"
    input_text = (DATA_PATH / "mcp_tools" / input_file).read_text(encoding="utf-8")
    input_: object = json.loads(input_text) if input_kind in {"mapping", "list"} else input_text

    generate(
        input_=input_,
        input_file_type=InputFileType.MCPTools,
        input_filename=input_file,
        output=output_file,
    )
    assert_file_content(output_file, expected_file)


def test_mcp_tools_url_preserves_relative_ref_context(
    mock_httpx_get: HttpxGetMockFactory,
    output_file: Path,
) -> None:
    """Resolve MCP inputSchema relative refs from the original URL."""
    base_url = "https://example.com/mcp/"
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse(f"{base_url}tools.json", DATA_PATH / "mcp_tools" / "remote_relative_ref.json"),
        MockHttpxResponse(f"{base_url}common.json", DATA_PATH / "mcp_tools" / "remote_common.json"),
    )
    run_main_with_args([
        "--url",
        f"{base_url}tools.json",
        "--input-file-type",
        "mcp-tools",
        "--output",
        str(output_file),
    ])
    assert_file_content(output_file, "mcp_tools/remote_relative_ref.py")
    assert_httpx_get_kwargs(
        httpx_get_mock,
        expected_urls=[f"{base_url}tools.json", f"{base_url}common.json"],
        call_count=2,
    )


@pytest.mark.parametrize(
    ("input_file", "expected_stderr_contains"),
    [
        ("invalid_top_level_list.json", "Invalid MCP tools document: top-level list contains a non-tool item"),
        ("no_tools.json", "Invalid MCP tools document: no tool definitions were found"),
        ("non_mapping_definitions.json", "Invalid MCP tools document: no tool definitions were found"),
        ("scalar.json", "Invalid MCP tools document: no tool definitions were found"),
        ("missing_tool_name.json", "MCP tool name must be a string"),
        ("missing_input_schema.json", "MCP tool 'only_output' is missing inputSchema"),
        ("invalid_input_schema.json", "MCP tool 'bad_schema' inputSchema must be a JSON Schema object"),
        ("invalid_output_schema.json", "MCP tool 'bad_output' outputSchema must be a JSON Schema object"),
        ("invalid_tool_title.json", "MCP tool 'bad_title' title must be a string"),
        ("invalid_json.json", "Invalid file format for mcp-tools"),
    ],
)
def test_mcp_tools_invalid(
    input_file: str,
    expected_stderr_contains: str,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject invalid MCP tool schema profile input through the CLI path."""
    run_main_and_assert(
        input_path=DATA_PATH / "mcp_tools" / input_file,
        output_path=output_file,
        input_file_type="mcp-tools",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
        file_should_not_exist=output_file,
    )


@freeze_time(TIMESTAMP)
@pytest.mark.parametrize(
    ("keyword_only", "target_python_version", "expected_file"),
    [
        (False, PythonVersion.PY_310, "frozen_dataclasses.py"),
        (True, PythonVersion.PY_310, "frozen_dataclasses_keyword_only.py"),
    ],
)
def test_frozen_dataclasses(
    output_file: Path,
    keyword_only: bool,
    target_python_version: PythonVersion,
    expected_file: str,
) -> None:
    """Test --frozen-dataclasses flag functionality."""
    run_generate_file_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file=expected_file,
        output_model_type=DataModelType.DataclassesDataclass,
        frozen_dataclasses=True,
        keyword_only=keyword_only,
        target_python_version=target_python_version,
    )


@pytest.mark.cli_doc(
    options=["--frozen-dataclasses"],
    option_description="""Generate frozen dataclasses with optional keyword-only fields.

The `--frozen-dataclasses` flag generates dataclass instances that are immutable
(frozen=True). Combined with `--keyword-only` (Python 3.10+), all fields become
keyword-only arguments.""",
    input_schema="jsonschema/simple_frozen_test.json",
    cli_args=["--output-model-type", "dataclasses.dataclass", "--frozen-dataclasses"],
    golden_output="frozen_dataclasses.py",
    related_options=["--keyword-only", "--output-model-type"],
)
@freeze_time(TIMESTAMP)
@pytest.mark.parametrize(
    ("extra_args", "expected_file"),
    [
        (["--output-model-type", "dataclasses.dataclass", "--frozen-dataclasses"], "frozen_dataclasses.py"),
        (
            [
                "--output-model-type",
                "dataclasses.dataclass",
                "--frozen-dataclasses",
                "--keyword-only",
                "--target-python-version",
                "3.10",
            ],
            "frozen_dataclasses_keyword_only.py",
        ),
    ],
)
def test_frozen_dataclasses_command_line(output_file: Path, extra_args: list[str], expected_file: str) -> None:
    """Generate frozen dataclasses with optional keyword-only fields.

    The `--frozen-dataclasses` flag generates dataclass instances that are immutable
    (frozen=True). Combined with `--keyword-only` (Python 3.10+), all fields become
    keyword-only arguments.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=extra_args,
    )


@freeze_time(TIMESTAMP)
def test_class_decorators(output_file: Path) -> None:
    """Test --class-decorators flag functionality."""
    run_generate_file_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="class_decorators_dataclass.py",
        output_model_type=DataModelType.DataclassesDataclass,
        class_decorators=["@dataclass_json"],
        additional_imports=["dataclasses_json.dataclass_json"],
    )


@pytest.mark.cli_doc(
    options=["--class-decorators"],
    option_description="""Add custom decorators to generated model classes.

The `--class-decorators` option adds custom decorators to all generated model classes.
This is useful for integrating with serialization libraries like `dataclasses_json`.

Use with `--additional-imports` to add the required imports for the decorators.
The `@` prefix is optional and will be added automatically if missing.""",
    input_schema="jsonschema/simple_frozen_test.json",
    cli_args=[
        "--output-model-type",
        "dataclasses.dataclass",
        "--class-decorators",
        "@dataclass_json",
        "--additional-imports",
        "dataclasses_json.dataclass_json",
    ],
    golden_output="class_decorators_dataclass.py",
    related_options=["--additional-imports", "--output-model-type"],
)
@freeze_time(TIMESTAMP)
def test_class_decorators_command_line(output_file: Path) -> None:
    """Add custom decorators to generated model classes.

    The `--class-decorators` option adds custom decorators to all generated model classes.
    This is useful for integrating with serialization libraries like `dataclasses_json`.

    Use with `--additional-imports` to add the required imports for the decorators.
    The `@` prefix is optional and will be added automatically if missing.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="class_decorators_dataclass.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--class-decorators",
            "@dataclass_json",
            "--additional-imports",
            "dataclasses_json.dataclass_json",
        ],
    )


@freeze_time(TIMESTAMP)
def test_class_decorators_without_at_prefix(output_file: Path) -> None:
    """Test --class-decorators auto-adds @ prefix when missing."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="class_decorators_dataclass.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--class-decorators",
            "dataclass_json",
            "--additional-imports",
            "dataclasses_json.dataclass_json",
        ],
    )


@freeze_time(TIMESTAMP)
def test_class_decorators_with_empty_entries(output_file: Path) -> None:
    """Test --class-decorators filters out empty entries from comma-separated list."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="class_decorators_dataclass.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--class-decorators",
            "@dataclass_json, ,",
            "--additional-imports",
            "dataclasses_json.dataclass_json",
        ],
    )


@freeze_time(TIMESTAMP)
@pytest.mark.parametrize(
    ("output_model_type", "expected_file"),
    [
        ("pydantic_v2.BaseModel", "class_decorators_pydantic_v2_BaseModel.py"),
        ("pydantic_v2.dataclass", "class_decorators_pydantic_v2_dataclass.py"),
        ("dataclasses.dataclass", "class_decorators_dataclasses_dataclass.py"),
        ("msgspec.Struct", "class_decorators_msgspec_Struct.py"),
        # Note: TypedDict is excluded because its template doesn't support decorators
    ],
    ids=[
        "pydantic_v2",
        "pydantic_v2_dataclass",
        "dataclasses",
        "msgspec",
    ],
)
def test_class_decorators_all_output_types(output_file: Path, output_model_type: str, expected_file: str) -> None:
    """Test --class-decorators works with all output model types that support decorators."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "simple_frozen_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--class-decorators",
            "@my_decorator",
            "--additional-imports",
            "my_module.my_decorator",
        ],
    )


@freeze_time(TIMESTAMP)
def test_use_attribute_docstrings(tmp_path: Path) -> None:
    """Test --use-attribute-docstrings flag functionality."""
    output_file = tmp_path / "output.py"
    generate(
        DATA_PATH / "jsonschema" / "use_attribute_docstrings_test.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        use_field_description=True,
        use_attribute_docstrings=True,
    )
    assert_file_content(output_file)


@freeze_time(TIMESTAMP)
@pytest.mark.cli_doc(
    options=["--use-attribute-docstrings"],
    option_description="""Generate field descriptions as attribute docstrings instead of Field descriptions.

The `--use-attribute-docstrings` flag places field descriptions in Python docstring
format (PEP 224 attribute docstrings) rather than in Field(..., description=...).
This provides better IDE support for hovering over attributes. Requires
`--use-field-description` to be enabled.""",
    input_schema="jsonschema/use_attribute_docstrings_test.json",
    cli_args=[
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--use-field-description",
        "--use-attribute-docstrings",
    ],
    golden_output="use_attribute_docstrings.py",
    related_options=["--use-field-description"],
)
def test_use_attribute_docstrings_command_line(output_file: Path) -> None:
    """Generate field descriptions as attribute docstrings instead of Field descriptions.

    The `--use-attribute-docstrings` flag places field descriptions in Python docstring
    format (PEP 224 attribute docstrings) rather than in Field(..., description=...).
    This provides better IDE support for hovering over attributes. Requires
    `--use-field-description` to be enabled.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "use_attribute_docstrings_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="use_attribute_docstrings.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-field-description",
            "--use-attribute-docstrings",
        ],
    )


@pytest.mark.allow_direct_assert
def test_filename_with_newline_injection(tmp_path: Path) -> None:
    """Test that filenames with newlines cannot inject code into generated files."""
    schema_content = """{"type": "object", "properties": {"name": {"type": "string"}}}"""

    malicious_filename = """schema.json
# INJECTED CODE:
import os
os.system('echo INJECTED')
# END INJECTION"""

    output_path = tmp_path / "output.py"

    generate(
        input_=schema_content,
        input_filename=malicious_filename,
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
    )

    generated_content = output_path.read_text()

    assert "#   filename:  schema.json # INJECTED CODE: import os" in generated_content, (
        "Filename not properly sanitized"
    )

    assert_no_uncommented_generated_code(
        generated_content,
        forbidden_starts=("import os",),
        forbidden_contains=("os.system",),
    )

    compile(generated_content, str(output_path), "exec")


def test_filename_with_various_control_characters(tmp_path: Path) -> None:
    """Test that various control characters in filenames are properly sanitized."""
    schema_content = """{"type": "object", "properties": {"test": {"type": "string"}}}"""

    test_cases = [
        ("newline", "schema.json\nimport os; os.system('echo INJECTED')"),
        ("carriage_return", "schema.json\rimport os; os.system('echo INJECTED')"),
        ("crlf", "schema.json\r\nimport os; os.system('echo INJECTED')"),
        ("tab_newline", "schema.json\t\nimport os; os.system('echo TAB')"),
        ("form_feed", "schema.json\f\nimport os; os.system('echo FF')"),
        ("vertical_tab", "schema.json\v\nimport os; os.system('echo VT')"),
        ("unicode_line_separator", "schema.json\u2028import os; os.system('echo U2028')"),
        ("unicode_paragraph_separator", "schema.json\u2029import os; os.system('echo U2029')"),
        ("multiple_newlines", "schema.json\n\n\nimport os; os.system('echo MULTI')"),
        ("mixed_characters", "schema.json\n\r\t\nimport os; os.system('echo MIXED')"),
    ]

    for _test_name, malicious_filename in test_cases:
        output_path = tmp_path / "output.py"

        generate(
            input_=schema_content,
            input_filename=malicious_filename,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
        )

        generated_content = output_path.read_text()

        assert_no_uncommented_generated_code(
            generated_content,
            forbidden_starts=("import ",),
            forbidden_contains=("os.system",),
        )

        compile(generated_content, str(output_path), "exec")


def test_generate_with_nonexistent_file(tmp_path: Path) -> None:
    """Test that generating from a nonexistent file raises an error."""
    nonexistent_file = tmp_path / "nonexistent.json"
    output_file = tmp_path / "output.py"

    with pytest.raises(Error, match="File not found"):
        generate(
            input_=nonexistent_file,
            output=output_file,
        )


def test_generate_with_invalid_file_format(tmp_path: Path) -> None:
    """Test that generating from an invalid file format raises an error."""
    invalid_file = tmp_path / "invalid.txt"
    invalid_file.write_text("this is not valid json or yaml or anything")
    output_file = tmp_path / "output.py"

    with pytest.raises(Error, match="Invalid file format"):
        generate(
            input_=invalid_file,
            output=output_file,
        )


def test_schema_parse_error_includes_path(tmp_path: Path) -> None:
    """Test that schema parse errors include the schema path context."""
    invalid_schema = tmp_path / "invalid_schema.json"
    invalid_schema.write_text("""{
        "type": "object",
        "properties": {
            "myField": {
                "type": "integer",
                "minimum": "not_a_number"
            }
        }
    }""")
    output_file = tmp_path / "output.py"

    with pytest.raises(SchemaParseError, match="Error at schema path"):
        generate(
            input_=invalid_schema,
            output=output_file,
        )


def test_schema_parse_error_includes_nested_path(tmp_path: Path) -> None:
    """Test that schema parse errors include nested schema path context."""
    invalid_schema = tmp_path / "invalid_nested_schema.json"
    invalid_schema.write_text("""{
        "$defs": {
            "MyModel": {
                "type": "object",
                "properties": {
                    "nestedField": {
                        "type": "number",
                        "maximum": "invalid_value"
                    }
                }
            }
        },
        "type": "object",
        "properties": {
            "ref": {"$ref": "#/$defs/MyModel"}
        }
    }""")
    output_file = tmp_path / "output.py"

    with pytest.raises(SchemaParseError, match=r"\$defs/MyModel"):
        generate(
            input_=invalid_schema,
            output=output_file,
        )


@pytest.mark.allow_direct_assert
def test_schema_parse_error_original_error(tmp_path: Path) -> None:
    """Test that SchemaParseError preserves the original error."""
    invalid_schema = tmp_path / "invalid_schema.json"
    invalid_schema.write_text("""{
        "type": "integer",
        "minimum": "not_a_number"
    }""")
    output_file = tmp_path / "output.py"

    with pytest.raises(SchemaParseError) as exc_info:
        generate(
            input_=invalid_schema,
            output=output_file,
        )

    assert exc_info.value.original_error is not None
    assert exc_info.value.path is not None


@pytest.mark.allow_direct_assert
def test_schema_parse_error_without_path() -> None:
    """Test SchemaParseError message formatting without path."""
    error = SchemaParseError("Test error message")
    assert error.message == "Test error message"
    assert error.path == []
    assert error.original_error is None


def test_generate_cli_command_with_no_use_specialized_enum(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with use-specialized-enum = false."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
use-specialized-enum = false
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_MAIN_PATH / "generate_cli_command" / "no_use_specialized_enum.txt",
        )


def test_generate_cli_command_with_false_boolean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with regular boolean set to false (should be skipped)."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
reuse-model = false
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_MAIN_PATH / "generate_cli_command" / "regular_false_boolean.txt",
        )


def test_generate_cli_command_with_true_boolean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with boolean set to true."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
snake-case-field = true
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_MAIN_PATH / "generate_cli_command" / "true_boolean.txt",
        )


def test_generate_cli_command_with_list_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with list option."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
strict-types = ["str", "int"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_MAIN_PATH / "generate_cli_command" / "list_option.txt",
        )


def test_generate_pyproject_config_json_mapping_option_preserves_value(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON mapping CLI values should not be serialized as key-only TOML lists."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--base-class-map",
            (DATA_PATH / "config" / "base_class_map.json").read_text(encoding="utf-8"),
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_PATH / "generate_pyproject_config" / "json_mapping_option.txt",
        assert_no_stderr=True,
    )


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("json_str", "expected"),
    [
        ('{"frozen": true, "slots": true}', {"frozen": True, "slots": True}),
        ("{}", {}),
    ],
)
def test_dataclass_arguments_valid(json_str: str, expected: dict) -> None:
    """Test that valid JSON is parsed correctly."""
    assert _dataclass_arguments(json_str) == expected


@pytest.mark.parametrize(
    ("json_str", "match"),
    [
        ("not-valid-json", "Invalid JSON:"),
        ("[1, 2, 3]", "Expected a JSON dictionary, got list"),
        ('"just a string"', "Expected a JSON dictionary, got str"),
        ("42", "Expected a JSON dictionary, got int"),
        ('{"invalid_key": true}', "Invalid keys:"),
        ('{"frozen": "not_bool"}', "Expected bool for 'frozen', got str"),
    ],
)
def test_dataclass_arguments_invalid(json_str: str, match: str) -> None:
    """Test that invalid input raises ArgumentTypeError."""
    with pytest.raises(ArgumentTypeError, match=match):
        _dataclass_arguments(json_str)


@pytest.mark.cli_doc(
    options=["--import-overrides"],
    option_description="""Override modules for generated imports by symbol name.

The mapping applies to imports in generated output regardless of their original module, except for
`__future__` imports. This is useful for routing typing helpers through a project compatibility module
across Python target versions.
""",
    input_schema="jsonschema/datetime.json",
    cli_args=[
        "--output-model-type",
        "typing.TypedDict",
        "--target-python-version",
        "3.10",
        "--import-overrides",
        '{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}',
    ],
    golden_output="main/import_overrides.py",
    primary=True,
)
@freeze_time(TIMESTAMP)
def test_import_overrides(output_file: Path) -> None:
    """Route generated typing imports through a compatibility module."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "datetime.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--target-python-version",
            "3.10",
            "--import-overrides",
            '{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}',
        ],
        skip_code_validation=True,
    )
    validate_generated_code(output_file.read_text(encoding="utf-8"), str(output_file))


@freeze_time(TIMESTAMP)
def test_generate_import_overrides(output_file: Path) -> None:
    """Route generated imports through the public generate API."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "datetime.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="import_overrides.py",
        output_model_type=DataModelType.TypingTypedDict,
        target_python_version=PythonVersion.PY_310,
        import_overrides={
            "NotRequired": "my_project.typing_compat",
            "TypedDict": "my_project.typing_compat",
        },
    )


@freeze_time(TIMESTAMP)
def test_import_overrides_preserve_alias(output_file: Path) -> None:
    """Preserve generated import aliases while replacing their modules."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        extra_args=[
            "--use-generic-base-class",
            "--allow-population-by-field-name",
            "--import-overrides",
            str(DATA_PATH / "config" / "import_overrides.json"),
        ],
    )


@freeze_time(TIMESTAMP)
def test_import_overrides_apply_to_additional_imports(output_file: Path) -> None:
    """Override explicitly included imports without rewriting future imports."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="import_overrides_additional_import.py",
        extra_args=[
            "--additional-imports",
            "typing_extensions.TypeAlias",
            "--import-overrides",
            '{"TypeAlias": "typing", "annotations": "custom.future"}',
        ],
    )


def test_generate_config_accepts_additional_imports(output_file: Path) -> None:
    """Keep documented bare and dotted imports available through the public config API."""
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        additional_imports=["collections"],
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        output=output_file,
    )
    generate(JSON_SCHEMA_DATA_PATH / "person.json", config=config)
    assert_file_content(output_file, "additional_imports_generate_config.py")


@pytest.mark.parametrize(
    ("import_path", "expected_paths"),
    [
        (" collections.deque ", ["collections.deque"]),
        (" café.モジュール ", ["café.モジュール"]),
        (None, None),
    ],
)
@pytest.mark.allow_direct_assert
def test_generate_config_normalizes_valid_additional_imports(
    import_path: str | None,
    expected_paths: list[str] | None,
) -> None:
    """Accept whitespace-padded and Unicode Python identifiers through GenerateConfig."""
    additional_imports = None if import_path is None else [import_path]
    assert GenerateConfig(additional_imports=additional_imports).additional_imports == expected_paths


@pytest.mark.parametrize(
    "import_path",
    [
        "from.collections",
        "collections.deque; INJECTION_MARKER = 1",
        "collections.deque\nINJECTION_MARKER = 1",
    ],
)
def test_generate_config_rejects_invalid_additional_imports(import_path: str) -> None:
    """Reject non-import syntax before a public config can generate source."""
    with pytest.raises(Error, match="additional_imports must be a Python import path composed of identifiers"):
        GenerateConfig(additional_imports=[import_path])


def test_generate_revalidates_mutated_additional_imports() -> None:
    """Retain import-path validation when a caller mutates a public config object."""
    config = GenerateConfig(input_file_type=InputFileType.JsonSchema)
    config.additional_imports = ["collections.deque\nINJECTION_MARKER = 1"]
    with pytest.raises(Error, match="additional_imports must be a Python import path composed of identifiers"):
        generate(JSON_SCHEMA_DATA_PATH / "person.json", config=config)


def test_main_rejects_additional_import_injection(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject CLI import values that would otherwise escape the generated import block."""
    invalid_import_path = (DATA_PATH / "config" / "additional_imports_invalid.txt").read_text(encoding="utf-8").strip()
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--additional-imports", invalid_import_path],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(capsys.readouterr().err, EXPECTED_MAIN_PATH / "additional_imports_invalid.txt")


def test_main_rejects_additional_import_injection_in_extra_template_data(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject extra template data import values before source generation starts."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--extra-template-data",
            str(DATA_PATH / "config" / "additional_imports_injection_extra_template_data.json"),
        ],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(capsys.readouterr().err, EXPECTED_MAIN_PATH / "additional_imports_invalid.txt")


@pytest.mark.cli_doc(
    options=["--type-overrides"],
    option_description="""Replace schema model types with custom Python types via JSON mapping.

This option is useful for importing models from external libraries (like `geojson-pydantic`)
instead of generating them.

**Override Formats:**

| Format | Description |
|--------|-------------|
| `{"ModelName": "package.Type"}` | Model-level: Skip generation; replace field and inheritance refs |
| `{"Model.field": "package.Type"}` | Scoped: Override only specific field in specific model |

!!! note "Model-level overrides skip generation"
    When you specify a model-level override (without a dot in the key), the generator will
    **skip generating that model entirely** and import it from the specified package instead.
    References to that model are replaced in field annotations and `allOf` inheritance base classes.

**Common Use Cases:**

| Use Case | Example Override |
|----------|------------------|
| GeoJSON types | `{"Feature": "geojson_pydantic.Feature"}` |
| Custom datetime | `{"Timestamp": "pendulum.DateTime"}` |
| MongoDB ObjectId | `{"ObjectId": "bson.ObjectId"}` |
| Custom validators | `{"Email": "my_app.types.ValidatedEmail"}` |
""",
    input_schema="jsonschema/type_overrides_test.json",
    cli_args=["--type-overrides", '{"CustomType": "my_app.types.CustomType"}'],
    golden_output="main/type_overrides_model_level.py",
    primary=True,
)
@freeze_time(TIMESTAMP)
def test_type_overrides_model_level(output_file: Path) -> None:
    """Replace schema model types with custom Python types via JSON mapping."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        extra_args=[
            "--type-overrides",
            '{"CustomType": "my_app.types.CustomType"}',
        ],
    )


@freeze_time(TIMESTAMP)
def test_type_overrides_model_level_from_file(output_file: Path, tmp_path: Path) -> None:
    """Replace schema model types from a JSON file mapping."""
    mapping_path = tmp_path / "type_overrides.json"
    mapping_path.write_text(json.dumps({"CustomType": "my_app.types.CustomType"}), encoding="utf-8")
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="type_overrides_model_level.py",
        extra_args=[
            "--type-overrides",
            str(mapping_path),
        ],
    )


@pytest.mark.cli_doc(
    options=["--type-overrides"],
    option_description="""Replace schema model types with custom Python types via JSON mapping.""",
    input_schema="jsonschema/type_overrides_external_lib.json",
    cli_args=[
        "--type-overrides",
        '{"Feature": "geojson_pydantic.Feature", "FeatureCollection": "geojson_pydantic.FeatureCollection"}',
    ],
    golden_output="main/type_overrides_external_lib.py",
)
@freeze_time(TIMESTAMP)
def test_type_overrides_external_lib(output_file: Path) -> None:
    """Test --type-overrides with external library types like geojson-pydantic."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_external_lib.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        extra_args=[
            "--type-overrides",
            '{"Feature": "geojson_pydantic.Feature", "FeatureCollection": "geojson_pydantic.FeatureCollection"}',
        ],
    )


@freeze_time(TIMESTAMP)
def test_type_overrides_scoped(output_file: Path) -> None:
    """Test --type-overrides with scoped override replaces specific field only."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_scoped.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        extra_args=[
            "--type-overrides",
            '{"User.address": "my_app.Address"}',
        ],
    )


@pytest.mark.parametrize(
    ("collapse_args", "expected_file"),
    [
        pytest.param([], "reuse_type_overrides.py", id="inherit"),
        pytest.param(["--collapse-reuse-models"], "reuse_type_overrides_collapsed.py", id="collapse"),
    ],
)
def test_type_overrides_preserved_during_module_reuse(
    collapse_args: list[str],
    expected_file: str,
    output_file: Path,
) -> None:
    """Keep scoped and model-level overrides outside module reuse optimizations."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "reuse_type_overrides.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--reuse-model",
            *collapse_args,
            "--type-overrides",
            '{"B.value": "datetime.date", "D": "datetime.datetime"}',
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )


@freeze_time(TIMESTAMP)
def test_type_overrides_nested_types(output_file: Path) -> None:
    """Test --type-overrides with nested types like List[CustomType]."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_nested.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        extra_args=[
            "--type-overrides",
            '{"Tag": "my_app.Tag"}',
        ],
    )


@freeze_time(TIMESTAMP)
def test_type_overrides_model_level_base_class(
    output_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test model-level --type-overrides replaces base class references."""
    package_dir = tmp_path / "my_app"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "models.py").write_text(
        "from __future__ import annotations\n\n"
        "from pydantic import BaseModel\n\n\n"
        "class Base(BaseModel):\n"
        "    id: int | None = None\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    module_names = ("my_app", "my_app.models")
    for module_name in module_names:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    try:
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_base_class.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="type_overrides_base_class.py",
            extra_args=[
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--formatters",
                "builtin",
                "--type-overrides",
                '{"Base": "my_app.models.Base"}',
            ],
            importable_module_name="generated_type_overrides_base_class",
            importable_module_attribute="Holder",
        )
    finally:
        for module_name in module_names:
            sys.modules.pop(module_name, None)


@freeze_time(TIMESTAMP)
def test_type_overrides_model_level_dict_key(output_file: Path) -> None:
    """Test model-level --type-overrides replaces dict key references."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "type_overrides_dict_key.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="type_overrides_dict_key.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--type-overrides",
            '{"Key": "my_app.keys.Key"}',
        ],
    )


def test_skip_root_model(tmp_path: Path) -> None:
    """Test --skip-root-model flag functionality using generate()."""
    output_file = tmp_path / "output.py"
    generate(
        DATA_PATH / "jsonschema" / "skip_root_model_test.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        skip_root_model=True,
    )
    assert_file_content(output_file, "skip_root_model.py")


@pytest.mark.cli_doc(
    options=["--skip-root-model"],
    option_description="""Skip generation of root model when schema contains nested definitions.

The `--skip-root-model` flag prevents generating a model for the root schema object
when the schema primarily contains reusable definitions. This is useful when the root
object is just a container for $defs and not a meaningful model itself.""",
    input_schema="jsonschema/skip_root_model_test.json",
    cli_args=["--output-model-type", "pydantic_v2.BaseModel", "--skip-root-model"],
    golden_output="skip_root_model.py",
)
def test_skip_root_model_command_line(output_file: Path) -> None:
    """Skip generation of root model when schema contains nested definitions.

    The `--skip-root-model` flag prevents generating a model for the root schema object
    when the schema primarily contains reusable definitions. This is useful when the root
    object is just a container for $defs and not a meaningful model itself.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "skip_root_model_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="skip_root_model.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--skip-root-model"],
    )


@pytest.mark.cli_doc(
    options=["--check"],
    option_description="""Verify generated code matches existing output without modifying files.

The `--check` flag compares the generated output with existing files and exits with
a non-zero status if they differ. Useful for CI/CD validation to ensure schemas
and generated code stay in sync. Works with both single files and directory outputs.""",
    input_schema="jsonschema/person.json",
    cli_args=["--disable-timestamp", "--check"],
    golden_output="person.py",
)
def test_check_file_matches(output_file: Path) -> None:
    """Verify generated code matches existing output without modifying files.

    The `--check` flag compares the generated output with existing files and exits with
    a non-zero status if they differ. Useful for CI/CD validation to ensure schemas
    and generated code stay in sync. Works with both single files and directory outputs.
    """
    input_path = DATA_PATH / "jsonschema" / "person.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp"],
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.OK,
    )


def test_check_file_does_not_exist(tmp_path: Path) -> None:
    """Test --check returns DIFF when file does not exist."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=tmp_path / "nonexistent.py",
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.DIFF,
    )


def test_check_file_differs(output_file: Path) -> None:
    """Test --check returns DIFF when file content differs."""
    output_file.write_text("# Different content\n", encoding="utf-8")
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.DIFF,
    )


def test_check_with_stdout_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --check with stdout output returns error."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=None,
        input_file_type="jsonschema",
        extra_args=["--check"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="--check cannot be used with stdout",
    )


def test_check_with_nonexistent_input(tmp_path: Path) -> None:
    """Test --check with nonexistent input file returns error."""
    run_main_and_assert(
        input_path=tmp_path / "nonexistent.json",
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        extra_args=["--check"],
        expected_exit=Exit.ERROR,
    )


def test_check_normalizes_line_endings(output_file: Path) -> None:
    """Test --check normalizes line endings (CRLF vs LF)."""
    input_path = DATA_PATH / "jsonschema" / "person.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp"],
    )
    content = output_file.read_text(encoding="utf-8")
    output_file.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.OK,
    )


def test_check_directory_matches(output_dir: Path) -> None:
    """Test --check returns OK when directory matches."""
    input_path = OPEN_API_DATA_PATH / "modular.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.OK,
    )


def test_check_directory_file_differs(output_dir: Path) -> None:
    """Test --check returns DIFF when a file in directory differs."""
    input_path = OPEN_API_DATA_PATH / "modular.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
    )
    py_files = list(output_dir.rglob("*.py"))
    py_files[0].write_text("# Modified content\n", encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.DIFF,
    )


def test_check_directory_missing_file(output_dir: Path) -> None:
    """Test --check returns DIFF when a generated file is missing."""
    input_path = OPEN_API_DATA_PATH / "modular.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
    )
    py_files = list(output_dir.rglob("*.py"))
    py_files[0].unlink()
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.DIFF,
    )


def test_check_directory_extra_file(output_dir: Path) -> None:
    """Test --check returns DIFF when an extra file exists."""
    input_path = OPEN_API_DATA_PATH / "modular.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
    )
    (output_dir / "extra_model.py").write_text("# Extra file\n", encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.DIFF,
    )


def test_check_directory_does_not_exist(tmp_path: Path) -> None:
    """Test --check returns DIFF when output directory does not exist."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=tmp_path / "nonexistent_model",
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.DIFF,
    )


def test_check_directory_ignores_pycache(output_dir: Path) -> None:
    """Test --check ignores __pycache__ directories in actual output."""
    input_path = OPEN_API_DATA_PATH / "modular.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
    )
    pycache_dir = output_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "module.cpython-313.pyc").write_bytes(b"fake bytecode")
    (pycache_dir / "extra.py").write_text("# should be ignored\n", encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.OK,
    )


def test_check_with_invalid_class_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --check cleans up temp directory when InvalidClassNameError occurs."""
    invalid_schema = tmp_path / "invalid.json"
    invalid_schema.write_text('{"title": "123InvalidName", "type": "object"}', encoding="utf-8")
    output_path = tmp_path / "output.py"
    run_main_and_assert(
        input_path=invalid_schema,
        output_path=output_path,
        input_file_type="jsonschema",
        extra_args=["--check"],
        expected_exit=Exit.ERROR,
        expected_stderr_contains="You have to set `--class-name` option",
        capsys=capsys,
    )


def test_check_with_invalid_file_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --check cleans up temp directory when Error occurs (invalid file format)."""
    invalid_file = tmp_path / "invalid.txt"
    invalid_file.write_text("This is not a valid schema format!!!", encoding="utf-8")
    output_path = tmp_path / "output.py"
    run_main_and_assert(
        input_path=invalid_file,
        output_path=output_path,
        extra_args=["--check"],
        expected_exit=Exit.ERROR,
        expected_stderr_contains="Invalid file format",
        capsys=capsys,
    )


@pytest.mark.cli_doc(
    options=["--all-exports-scope"],
    option_description="""Generate __all__ exports for child modules in __init__.py files.

The `--all-exports-scope=children` flag adds __all__ to each __init__.py containing
exports from direct child modules. This improves IDE autocomplete and explicit exports.
Use 'recursive' to include all descendant exports with collision handling.""",
    input_schema="openapi/modular.yaml",
    cli_args=["--all-exports-scope", "children"],
    golden_output="openapi/modular_all_exports_children",
    related_options=["--all-exports-collision-strategy"],
)
def test_all_exports_scope_children(output_dir: Path) -> None:
    """Generate __all__ exports for child modules in __init__.py files.

    The `--all-exports-scope=children` flag adds __all__ to each __init__.py containing
    exports from direct child modules. This improves IDE autocomplete and explicit exports.
    Use 'recursive' to include all descendant exports with collision handling.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--all-exports-scope", "children"],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "modular_all_exports_children",
    )


@pytest.mark.cli_doc(
    options=["--all-exports-collision-strategy"],
    option_description="""Handle name collisions when exporting recursive module hierarchies.

The `--all-exports-collision-strategy` flag determines how to resolve naming conflicts
when using `--all-exports-scope=recursive`. The 'minimal-prefix' strategy adds the
minimum module path prefix needed to disambiguate colliding names, while 'full-prefix'
uses the complete module path. Requires `--all-exports-scope=recursive`.""",
    input_schema="openapi/modular.yaml",
    cli_args=["--all-exports-scope", "recursive", "--all-exports-collision-strategy", "minimal-prefix"],
    golden_output="openapi/modular_all_exports_recursive",
    related_options=["--all-exports-scope"],
)
def test_all_exports_scope_recursive_with_collision(output_dir: Path) -> None:
    """Handle name collisions when exporting recursive module hierarchies.

    The `--all-exports-collision-strategy` flag determines how to resolve naming conflicts
    when using `--all-exports-scope=recursive`. The 'minimal-prefix' strategy adds the
    minimum module path prefix needed to disambiguate colliding names, while 'full-prefix'
    uses the complete module path. Requires `--all-exports-scope=recursive`.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "recursive",
            "--all-exports-collision-strategy",
            "minimal-prefix",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "modular_all_exports_recursive",
    )


def test_all_exports_scope_children_with_docstring_header(output_dir: Path) -> None:
    """Test --all-exports-scope=children with --custom-file-header containing docstring."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--all-exports-scope",
            "children",
            "--custom-file-header-path",
            str(DATA_PATH / "custom_file_header_docstring.txt"),
        ],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "modular_all_exports_children_docstring",
    )


def test_all_exports_scope_recursive_collision_avoided_by_renaming(output_dir: Path) -> None:
    """Test --all-exports-scope=recursive avoids collision through automatic class renaming.

    With circular import resolution, conflicting class names (e.g., foo.Tea and nested.foo.Tea)
    are automatically renamed (e.g., Tea and Tea_1) in _internal.py, so no collision error occurs.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--all-exports-scope", "recursive"],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "all_exports_scope_recursive_collision_avoided_by_renaming",
    )


def test_all_exports_collision_strategy_requires_recursive(
    output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test --all-exports-collision-strategy requires --all-exports-scope=recursive."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--all-exports-scope",
            "children",
            "--all-exports-collision-strategy",
            "minimal-prefix",
        ],
        expected_exit=Exit.ERROR,
        expected_stderr_contains="--all-exports-collision-strategy",
        capsys=capsys,
    )


def test_all_exports_scope_recursive_with_full_prefix(output_dir: Path) -> None:
    """Test --all-exports-scope=recursive with --all-exports-collision-strategy=full-prefix."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "recursive",
            "--all-exports-collision-strategy",
            "full-prefix",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "modular_all_exports_recursive_full_prefix",
    )


def test_all_exports_collision_resolved_successfully(output_dir: Path) -> None:
    """Test collision resolution successfully adds prefix when no local model conflict."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_collision_success.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "recursive",
            "--all-exports-collision-strategy",
            "minimal-prefix",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "all_exports_collision_success",
    )


@pytest.mark.parametrize(
    "strategy",
    ["minimal-prefix", "full-prefix"],
    ids=["minimal_prefix", "full_prefix"],
)
def test_all_exports_recursive_prefix_collision_with_local_model(
    output_dir: Path, strategy: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that prefix resolution raises error when renamed export collides with local model."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_prefix_collision.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--all-exports-scope",
            "recursive",
            "--all-exports-collision-strategy",
            strategy,
        ],
        expected_exit=Exit.ERROR,
        expected_stderr_contains="InputMessage",
        capsys=capsys,
    )


def test_all_exports_scope_recursive_jsonschema_multi_file(output_dir: Path) -> None:
    """Test --all-exports-scope=recursive with JSONSchema multi-file input."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "all_exports_multi_file",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "recursive",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "jsonschema" / "all_exports_multi_file",
    )


@pytest.mark.parametrize("layout", ["sibling", "child", "same"])
def test_custom_file_header_path_prepend_jsonschema_multi_file(output_dir: Path, layout: str) -> None:
    """Prepend a custom header while preserving per-file provenance and future imports."""
    input_path = output_dir.parent / "all_exports_multi_file"
    shutil.copytree(JSON_SCHEMA_DATA_PATH / input_path.name, input_path)
    output_path = {"sibling": output_dir, "child": input_path / "generated", "same": input_path}[layout]
    for run in range(2):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_path,
            input_file_type="jsonschema",
            extra_args=[
                "--disable-timestamp",
                "--all-exports-scope",
                "recursive",
                "--custom-file-header-path",
                str(DATA_PATH / "custom_file_header_with_docstring_and_import.txt"),
                "--custom-file-header-mode",
                "prepend",
                # Avoid helper-created config/parity outputs changing the overlapping layout.
                *(["--formatters", "builtin"] if layout != "sibling" else []),
            ],
            expected_directory=EXPECTED_MAIN_PATH / "jsonschema" / "custom_file_header_path_prepend_multi_file",
        )
        if run == 0:
            for module_path in output_path.rglob("*.py"):
                module_path.write_bytes(module_path.read_text(encoding="utf-8").replace("\n", "\r\n").encode())


def test_all_exports_recursive_local_model_collision_error(
    output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test --all-exports-scope=recursive raises error when child export collides with local model."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_local_collision.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--all-exports-scope",
            "recursive",
        ],
        expected_exit=Exit.ERROR,
        expected_stderr_contains="conflicts with a model in __init__.py",
        capsys=capsys,
    )


def test_all_exports_scope_children_no_child_exports(output_dir: Path) -> None:
    """Test --all-exports-scope=children when __init__.py has models but no direct child exports."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_no_child.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "children",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "all_exports_no_child",
    )


def test_all_exports_scope_children_with_local_models(output_dir: Path) -> None:
    """Test --all-exports-scope=children when __init__.py has both local models and child exports."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_exports_with_local_models.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "children",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "openapi" / "all_exports_with_local_models",
    )


def test_all_exports_scope_children_jsonschema_hyphenated_package(output_dir: Path) -> None:
    """Test --all-exports-scope=children with hyphenated JSON Schema directories."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "all_exports_hyphenated_directory",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--all-exports-scope",
            "children",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "jsonschema" / "all_exports_hyphenated_directory",
    )


def test_check_respects_pyproject_toml_settings(tmp_path: Path) -> None:
    """Test --check uses pyproject.toml formatter settings from output path.

    This test verifies that both generation and --check mode use the same
    pyproject.toml settings from the output directory. Without the fix,
    generation would use cwd's settings while --check would use output path's
    settings, causing a diff even with identical input.
    """
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text("[tool.black]\nline-length = 60\n", encoding="utf-8")

    input_json = tmp_path / "input.json"
    input_json.write_text(
        """{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Person",
  "type": "object",
  "properties": {
    "firstName": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100,
      "pattern": "^[A-Za-z]+$"
    }
  },
  "required": ["firstName"]
}""",
        encoding="utf-8",
    )

    output_file = tmp_path / "output.py"
    expected_output = """\
# generated by datamodel-codegen:
#   filename:  input.json

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class Person(BaseModel):
    firstName: Annotated[
        str,
        Field(
            max_length=100,
            min_length=1,
            pattern='^[A-Za-z]+$',
        ),
    ]
"""
    run_main_and_assert(
        input_path=input_json,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--use-annotated"],
        expected_output=expected_output,
    )

    run_main_and_assert(
        input_path=input_json,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--use-annotated", "--check"],
        expected_exit=Exit.OK,
    )


def test_use_specialized_enum_requires_python_311(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --use-specialized-enum requires --target-python-version 3.11+."""
    input_json = tmp_path / "input.json"
    input_json.write_text(
        '{"type": "string", "enum": ["A", "B"]}',
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_json,
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        extra_args=["--use-specialized-enum"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="--use-specialized-enum requires --target-python-version 3.11 or later",
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "22",
    reason="Installed black doesn't support StrEnum formatting",
)
def test_use_specialized_enum_with_python_311_ok(output_file: Path) -> None:
    """Test --use-specialized-enum works with --target-python-version 3.11."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "string_enum.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--use-specialized-enum", "--target-python-version", "3.11"],
        assert_func=assert_file_content,
        expected_file="use_specialized_enum_py311.py",
    )


def test_use_specialized_enum_pyproject_requires_python_311(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test use_specialized_enum in pyproject.toml requires target_python_version 3.11+."""
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text(
        "[tool.datamodel-codegen]\nuse_specialized_enum = true\n",
        encoding="utf-8",
    )

    input_json = tmp_path / "input.json"
    input_json.write_text(
        '{"type": "string", "enum": ["A", "B"]}',
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_json,
            output_path=tmp_path / "output.py",
            input_file_type="jsonschema",
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="--use-specialized-enum requires --target-python-version 3.11 or later",
        )


def test_use_specialized_enum_pyproject_override_with_cli(output_file: Path, tmp_path: Path) -> None:
    """Test --no-use-specialized-enum CLI can override pyproject.toml use_specialized_enum=true."""
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text(
        "[tool.datamodel-codegen]\nuse_specialized_enum = true\n",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "string_enum.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--no-use-specialized-enum"],
            assert_func=assert_file_content,
            expected_file="no_use_specialized_enum.py",
        )


def test_cli_input_overrides_pyproject_url(
    output_file: Path, tmp_path: Path, mock_httpx_get: HttpxGetMockFactory
) -> None:
    """Test --input takes precedence over pyproject.toml url setting."""
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text(
        '[tool.datamodel-codegen]\nurl = "https://example.com/schema.json"\ninput-file-type = "jsonschema"\n',
        encoding="utf-8",
    )

    cli_input = tmp_path / "cli.json"
    cli_input.write_text(
        '{"type": "object", "properties": {"from_input": {"type": "string"}}}',
        encoding="utf-8",
    )

    httpx_get_mock = mock_httpx_get()

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=cli_input.relative_to(tmp_path),
            output_path=output_file,
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file="cli_input_overrides_pyproject_url.py",
        )

    assert_httpx_get_kwargs(httpx_get_mock, called=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/schema.json",
        "http://2130706433/schema.json",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/schema.json",
    ],
)
def test_cli_url_blocks_unsafe_host(
    url: str,
    output_file: Path,
    mock_httpx_get: HttpxGetMockFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Block local and private network targets."""
    httpx_get_mock = mock_httpx_get()

    run_main_with_args(
        [
            "--url",
            url,
            "--output",
            str(output_file),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="--allow-private-network",
    )
    assert_httpx_get_kwargs(httpx_get_mock, called=False)


def test_cli_url_allows_unsafe_host_with_explicit_opt_in(
    output_file: Path,
    mock_httpx_get: HttpxGetMockFactory,
) -> None:
    """Allow trusted private network URL input only when explicitly requested."""
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse("http://127.0.0.1/schema.json", '{"type": "object", "title": "LocalSchema"}')
    )

    run_main_with_args(
        [
            "--url",
            "http://127.0.0.1/schema.json",
            "--output",
            str(output_file),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--allow-private-network",
        ],
    )
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="http://127.0.0.1/schema.json")


def test_cli_url_blocks_unsafe_host_with_explicit_opt_out(
    output_file: Path,
    tmp_path: Path,
    mock_httpx_get: HttpxGetMockFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow CLI to override a configuration file that permits private network requests."""
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text(
        "[tool.datamodel-codegen]\nallow-private-network = true\n",
        encoding="utf-8",
    )
    httpx_get_mock = mock_httpx_get()

    with chdir(tmp_path):
        run_main_with_args(
            [
                "--url",
                "http://127.0.0.1/schema.json",
                "--output",
                str(output_file),
                "--input-file-type",
                "jsonschema",
                "--disable-timestamp",
                "--no-allow-private-network",
            ],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="--allow-private-network",
        )
    assert_httpx_get_kwargs(httpx_get_mock, called=False)


def test_cli_url_blocks_redirect_to_unsafe_host(
    output_file: Path,
    mock_httpx_get: HttpxGetMockFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Validate redirect targets before fetching them."""
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse(
            "https://example.com/schema.json",
            "{}",
            status_code=302,
            headers={"location": "http://127.0.0.1/schema.json"},
        )
    )

    run_main_with_args(
        [
            "--url",
            "https://example.com/schema.json",
            "--output",
            str(output_file),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="--allow-private-network",
    )
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://example.com/schema.json")


def test_cli_url_overrides_pyproject_input(
    output_file: Path, tmp_path: Path, mock_httpx_get: HttpxGetMockFactory
) -> None:
    """Test --url takes precedence over pyproject.toml input setting."""
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text(
        '[tool.datamodel-codegen]\ninput = "config.json"\ninput-file-type = "jsonschema"\n',
        encoding="utf-8",
    )

    config_input = tmp_path / "config.json"
    config_input.write_text(
        '{"type": "object", "properties": {"from_config": {"type": "string"}}}',
        encoding="utf-8",
    )

    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse(
            "https://example.com/schema.json",
            JSON_SCHEMA_DATA_PATH / "cli_url_overrides_pyproject_input.json",
        )
    )

    with chdir(tmp_path):
        run_main_with_args([
            "--url",
            "https://example.com/schema.json",
            "--output",
            str(output_file),
            "--disable-timestamp",
        ])

    assert_file_content(output_file, "cli_url_overrides_pyproject_input.py")
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://example.com/schema.json")


@pytest.mark.cli_doc(
    options=["--module-split-mode"],
    option_description="""Split generated models into separate files, one per model class.

The `--module-split-mode=single` flag generates each model class in its own file,
named after the class in snake_case. Use with `--all-exports-scope=recursive` to
create an __init__.py that re-exports all models for convenient imports.""",
    input_schema="jsonschema/module_split_single/input.json",
    cli_args=["--module-split-mode", "single", "--all-exports-scope", "recursive", "--use-exact-imports"],
    golden_output="jsonschema/module_split_single",
    related_options=["--all-exports-scope", "--use-exact-imports"],
)
def test_module_split_mode_single(output_dir: Path) -> None:
    """Split generated models into separate files, one per model class.

    The `--module-split-mode=single` flag generates each model class in its own file,
    named after the class in snake_case. Use with `--all-exports-scope=recursive` to
    create an __init__.py that re-exports all models for convenient imports.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "module_split_single" / "input.json",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--module-split-mode",
            "single",
            "--all-exports-scope",
            "recursive",
            "--use-exact-imports",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "jsonschema" / "module_split_single",
    )


@pytest.mark.cli_doc(
    options=["--use-standard-primitive-types"],
    option_description="""Use Python standard library types for string formats instead of str.

The `--use-standard-primitive-types` flag configures the code generation to use
Python standard library types (UUID, IPv4Address, IPv6Address, Path) for corresponding
string formats instead of plain str. This affects dataclass, msgspec, and TypedDict
output types. Pydantic already uses these types by default.""",
    input_schema="jsonschema/use_standard_primitive_types.json",
    cli_args=[
        "--output-model-type",
        "dataclasses.dataclass",
        "--use-standard-primitive-types",
    ],
    golden_output="use_standard_primitive_types.py",
    related_options=["--output-model-type", "--output-datetime-class"],
)
@freeze_time(TIMESTAMP)
def test_use_standard_primitive_types(output_file: Path) -> None:
    """Use Python standard library types for string formats instead of str.

    The `--use-standard-primitive-types` flag configures the code generation to use
    Python standard library types (UUID, IPv4Address, IPv6Address, Path) for corresponding
    string formats instead of plain str. This affects dataclass, msgspec, and TypedDict
    output types. Pydantic already uses these types by default.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "use_standard_primitive_types.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--use-standard-primitive-types",
        ],
        expected_file=EXPECTED_MAIN_PATH / "use_standard_primitive_types.py",
    )


def test_format_code_fallback_on_error(tmp_path: Path, mocker: MockerFixture) -> None:
    """Test that code generation continues with unformatted output when formatting fails."""
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object", "properties": {"name": {"type": "string"}}}', encoding="utf-8")
    output = tmp_path / "output.py"

    def mock_format_code(_self: CodeFormatter, _code: str) -> str:
        msg = "mock error"
        raise black.InvalidInput(msg)

    mocker.patch.object(CodeFormatter, "format_code", mock_format_code)

    with pytest.warns(UserWarning, match="Failed to format code.*Emitting unformatted output"):
        generate(
            input_=schema,
            input_file_type=InputFileType.JsonSchema,
            output=output,
            disable_timestamp=True,
        )

    assert_file_content(output, "format_code_fallback_on_error.py")


def test_format_code_fallback_on_error_init_exports(tmp_path: Path, mocker: MockerFixture) -> None:
    """Test that __init__.py generation continues with unformatted output when formatting fails."""
    output_dir = tmp_path / "output"

    def mock_format_code(_self: CodeFormatter, _code: str) -> str:
        msg = "mock error"
        raise black.InvalidInput(msg)

    mocker.patch.object(CodeFormatter, "format_code", mock_format_code)

    with pytest.warns(UserWarning, match="Failed to format code.*Emitting unformatted output"):
        generate(
            input_=OPEN_API_DATA_PATH / "modular.yaml",
            input_file_type=InputFileType.OpenAPI,
            output=output_dir,
            disable_timestamp=True,
            all_exports_scope=AllExportsScope.Children,
        )

    assert_directory_content(output_dir, EXPECTED_MAIN_PATH / "openapi" / "format_code_fallback_on_error_init_exports")


def test_init_exports_without_formatting(tmp_path: Path) -> None:
    """Test that __init__.py exports work correctly when formatting is disabled."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    parser = OpenAPIParser(source=OPEN_API_DATA_PATH / "modular.yaml")
    results = parser.parse(
        format_=False,
        all_exports_scope=AllExportsScope.Children,
    )

    assert_generated_modules_output(results, EXPECTED_MAIN_PATH / "openapi" / "init_exports_without_formatting")


def test_generate_parent_scoped_naming_backward_compat(tmp_path: Path) -> None:
    """Test generate() with parent_scoped_naming=True triggers ModelResolver backward compat."""
    output_file = tmp_path / "output.py"
    generate(
        input_=JSON_SCHEMA_DATA_PATH / "naming_strategy" / "input.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        parent_scoped_naming=True,
    )
    assert_file_content(output_file, "generate_parent_scoped_naming_backward_compat.py")


def test_ruff_check_and_format_combined(output_file: Path) -> None:
    """Test ruff check and format run together in a pipeline for single file."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        output_path=output_file,
        extra_args=["--formatters", "ruff-check", "ruff-format", "--disable-timestamp"],
        assert_func=assert_file_content,
        expected_file="jsonschema/simple_string.py",
    )


def test_ruff_check_only(output_file: Path) -> None:
    """Test ruff check formatter alone for single file."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        output_path=output_file,
        extra_args=["--formatters", "ruff-check", "--disable-timestamp"],
        assert_func=assert_file_content,
        expected_file="jsonschema/simple_string.py",
    )


def test_ruff_format_only(output_file: Path) -> None:
    """Test ruff format formatter alone for single file."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        output_path=output_file,
        extra_args=["--formatters", "ruff-format", "--disable-timestamp"],
        expected_output="""\
# generated by datamodel-codegen:
#   filename:  simple_string.json

from __future__ import annotations
from pydantic import BaseModel


class Model(BaseModel):
    s: str
""",
    )


def test_builtin_formatter_does_not_validate_black_support(output_file: Path, mocker: MockerFixture) -> None:
    """Test dependency-free formatter path does not require Black target support checks."""
    mocker.patch("datamodel_code_generator.__main__.is_supported_in_black", side_effect=AssertionError)

    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
        expected_output="""\
# generated by datamodel-codegen:
#   filename:  simple_string.json

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    s: str
""",
    )


def test_ruff_batch_formatting_directory(output_dir: Path) -> None:
    """Test ruff batch formatting for directory output (multiple files)."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "all_exports_multi_file",
        output_path=output_dir,
        extra_args=["--formatters", "ruff-check", "ruff-format", "--disable-timestamp"],
        expected_directory=EXPECTED_MAIN_PATH / "jsonschema" / "all_exports_multi_file_ruff",
        skip_code_validation=True,
    )


def test_type_checking_imports_default_to_runtime_imports_for_modular_pydantic_ruff(output_dir: Path) -> None:
    """Test modular Pydantic output keeps runtime imports by default when Ruff formats a directory."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "ruff-check",
            "ruff-format",
            "--disable-timestamp",
        ],
    )
    assert_runtime_import_package(output_dir, EXPECTED_MAIN_PATH / "openapi" / "no_use_type_checking_imports")


@pytest.mark.cli_doc(
    options=["--no-use-type-checking-imports"],
    option_description="""Keep generated model imports available at runtime when using Ruff fixes.

The `--no-use-type-checking-imports` flag prevents Ruff from moving generated model imports
into `TYPE_CHECKING` blocks. This is useful for modular Pydantic output where referenced
models need to be importable at runtime without calling `model_rebuild()` manually.
In the multi-module Pydantic + `ruff-check` case, runtime imports are preserved by default.
`--use-type-checking-imports` opts back into the old TYPE_CHECKING-only behavior, which can
require manual `model_rebuild()` calls for cross-module runtime references.""",
    input_schema="openapi/modular.yaml",
    cli_args=[
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--formatters",
        "ruff-check",
        "ruff-format",
        "--no-use-type-checking-imports",
        "--disable-timestamp",
    ],
    golden_output="openapi/no_use_type_checking_imports_internal.py",
    related_options=["--use-type-checking-imports", "--formatters", "--use-exact-imports"],
)
def test_no_use_type_checking_imports(output_dir: Path) -> None:
    """Keep generated model imports available at runtime when using Ruff fixes.

    The `--no-use-type-checking-imports` flag prevents Ruff from moving generated model imports
    into `TYPE_CHECKING` blocks. This is useful for modular Pydantic output where referenced
    models need to be importable at runtime without calling `model_rebuild()` manually.
    In the multi-module Pydantic + `ruff-check` case, runtime imports are preserved by default.
    `--use-type-checking-imports` opts back into the old TYPE_CHECKING-only behavior, which can
    require manual `model_rebuild()` calls for cross-module runtime references.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "ruff-check",
            "ruff-format",
            "--no-use-type-checking-imports",
            "--disable-timestamp",
        ],
    )
    assert_runtime_import_package(output_dir, EXPECTED_MAIN_PATH / "openapi" / "no_use_type_checking_imports")


def test_generate_multi_module_pydantic_ruff_defaults_to_runtime_imports() -> None:
    """Test generate() keeps runtime imports for multi-module Pydantic Ruff output."""
    result = generate(
        OPEN_API_DATA_PATH / "modular.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=None,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
        disable_timestamp=True,
    )

    assert_generated_modules_output(
        result,
        EXPECTED_MAIN_PATH / "openapi" / "generate_multi_module_pydantic_ruff_defaults_to_runtime_imports",
    )


@pytest.mark.cli_doc(
    options=["--use-type-checking-imports"],
    option_description="""Allow Ruff to move typing-only imports into TYPE_CHECKING blocks.

The `--use-type-checking-imports` flag explicitly re-enables Ruff's TYPE_CHECKING import moves
for multi-module Pydantic output where runtime imports might otherwise be preserved by default.""",
    input_schema="openapi/modular.yaml",
    cli_args=[
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--formatters",
        "ruff-check",
        "ruff-format",
        "--use-type-checking-imports",
        "--disable-timestamp",
    ],
    golden_output="openapi/use_type_checking_imports_internal.py",
    related_options=["--no-use-type-checking-imports", "--formatters", "--use-exact-imports"],
)
def test_use_type_checking_imports_for_multi_module_pydantic_ruff() -> None:
    """Allow Ruff to move typing-only imports into TYPE_CHECKING blocks.

    The `--use-type-checking-imports` flag explicitly re-enables Ruff's TYPE_CHECKING import moves
    for multi-module Pydantic output where runtime imports might otherwise be preserved by default.
    """
    result = generate(
        OPEN_API_DATA_PATH / "modular.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=None,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
        use_type_checking_imports=True,
        disable_timestamp=True,
    )

    assert_generated_modules_output(
        result,
        EXPECTED_MAIN_PATH / "openapi" / "use_type_checking_imports_for_multi_module_pydantic_ruff",
    )


def test_generate_returns_string_when_output_none() -> None:
    """Test that generate() returns str when output=None for single file."""
    json_schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="test.json",
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_when_output_none.py",
    )


def test_generate_accepts_path_input(output_file: Path) -> None:
    """Test generate() reads Path inputs as local schema files."""
    with chdir(output_file.parent):
        relative_root = output_file.parent.relative_to(output_file.parent)
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "person.json",
            output_path=output_file.relative_to(output_file.parent),
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
            settings_path=relative_root,
            http_local_ref_path=relative_root,
            assert_func=assert_file_content,
            expected_file="generate_accepts_path_input.py",
        )


@pytest.mark.parametrize("custom_formatters", [None, []], ids=["custom-unset", "custom-empty"])
def test_generate_with_empty_formatters(output_file: Path, custom_formatters: list[str] | None) -> None:
    """Skip formatter work when the explicit formatter list is empty."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        formatters=[],
        custom_formatters=custom_formatters,
        assert_func=assert_file_content,
        expected_file="generate_with_empty_formatters.py",
    )


def test_generate_does_not_mutate_extra_template_data() -> None:
    """Keep reusable GenerateConfig template data isolated per call."""
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        allow_extra_fields=True,
        extra_template_data={"#all#": {"unused_list": ["stable"], "unused_set": {"stable"}}},
        formatters=[],
    )

    with assert_inputs_not_mutated({"extra_template_data": config.extra_template_data}):
        generate(JSON_SCHEMA_DATA_PATH / "person.json", config=config)
        result = generate(
            JSON_SCHEMA_DATA_PATH / "person.json",
            config=config.model_copy(update={"allow_extra_fields": False}),
        )

    assert_output(f"{result}\n", EXPECTED_MAIN_PATH / "generate_with_empty_formatters.py")


@pytest.mark.allow_direct_assert
def test_copy_template_data_preserves_aliases_and_cycles() -> None:
    """Keep shared JSON-like values shared within only the detached copy."""
    from datamodel_code_generator._template_data import copy_template_data

    shared: list[object] = []
    source = {"first": shared, "second": shared}
    shared.append(source)

    copied = copy_template_data(source, {})

    assert copied is not source
    assert copied["first"] is copied["second"]
    assert copied["first"][0] is copied


@pytest.mark.allow_direct_assert
def test_deferred_config_rebuild_is_thread_safe() -> None:
    """Build shared deferred Pydantic config state once under concurrent use."""
    from pydantic import BaseModel as PydanticBaseModel
    from pydantic import ConfigDict

    from datamodel_code_generator.config import _rebuild_config_model

    # Keep the real rebuild active long enough for competing workers to reach the locked recheck.
    deferred_config = type(
        "_DeferredThreadConfig",
        (PydanticBaseModel,),
        {
            "__annotations__": {
                **{f"value_{index}": "DeferredValue" for index in range(4096)},
                "value": "DeferredValue",
            },
            **{f"value_{index}": 0 for index in range(4096)},
            "model_config": ConfigDict(defer_build=True),
        },
    )
    assert not deferred_config.__pydantic_complete__

    def rebuild_and_validate(value: int) -> object:
        _rebuild_config_model(deferred_config, {"DeferredValue": int})
        return deferred_config.model_validate({"value": value})

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(rebuild_and_validate, range(32)))

    assert deferred_config.__pydantic_complete__
    assert [result.value for result in results] == list(range(32))


@pytest.mark.allow_direct_assert
def test_generate_serializes_cwd_dependent_extensions(tmp_path: Path) -> None:
    """Keep cwd-dependent extension calls in their own output context."""
    first_entered = Event()
    second_started = Event()
    second_entered = Event()
    release_first = Event()
    observed_cwds: dict[str, set[Path]] = defaultdict(set)
    first_output = tmp_path / "first" / "model.py"
    second_output = tmp_path / "second" / "model.py"
    first_output.parent.mkdir()
    second_output.parent.mkdir()

    def first_name_generator(name: str) -> str:
        observed_cwds["first"].add(Path.cwd())
        first_entered.set()
        if not release_first.wait(timeout=5):  # pragma: no cover
            pytest.fail("Timed out waiting to release the first generator")
        return name

    def second_name_generator(name: str) -> str:
        observed_cwds["second"].add(Path.cwd())
        second_entered.set()
        return name

    def run_second_generation() -> str | GeneratedModules | None:
        second_started.set()
        return generate(
            **options,
            output=second_output,
            custom_class_name_generator=second_name_generator,
        )

    options = {
        "input_": JSON_SCHEMA_DATA_PATH / "person.json",
        "input_file_type": InputFileType.JsonSchema,
        "disable_timestamp": True,
        "formatters": [],
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            generate,
            **options,
            output=first_output,
            custom_class_name_generator=first_name_generator,
        )
        assert first_entered.wait(timeout=5)
        second = executor.submit(run_second_generation)
        assert second_started.wait(timeout=5)
        assert not second_entered.wait(timeout=0.1)
        release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert observed_cwds == {
        "first": {first_output.parent},
        "second": {second_output.parent},
    }
    assert_file_content(first_output, "generate_with_empty_formatters.py")
    assert_file_content(second_output, "generate_with_empty_formatters.py")


@pytest.mark.allow_direct_assert
def test_generate_does_not_capture_legacy_output_cwd(tmp_path: Path) -> None:
    """Resolve a normal request after a cwd-dependent request restores the caller context."""
    legacy_entered = Event()
    release_legacy = Event()
    normal_started = Event()
    normal_finished = Event()
    caller_cwd = Path.cwd()
    input_path = (JSON_SCHEMA_DATA_PATH / "person.json").relative_to(caller_cwd)
    legacy_output = tmp_path / "legacy" / "model.py"
    normal_output = tmp_path / "normal" / "model.py"
    legacy_output.parent.mkdir()
    normal_output.parent.mkdir()

    def wait_in_legacy_context(name: str) -> str:
        legacy_entered.set()
        if not release_legacy.wait(timeout=5):  # pragma: no cover
            pytest.fail("Timed out waiting to release legacy generation")
        return name

    def run_normal_generation() -> str | GeneratedModules | None:
        normal_started.set()
        try:
            return generate(
                input_path,
                output=normal_output,
                input_file_type=InputFileType.JsonSchema,
                disable_timestamp=True,
                formatters=[],
            )
        finally:
            normal_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            legacy = executor.submit(
                generate,
                input_path,
                output=legacy_output,
                input_file_type=InputFileType.JsonSchema,
                disable_timestamp=True,
                formatters=[],
                custom_class_name_generator=wait_in_legacy_context,
            )
            assert legacy_entered.wait(timeout=5)
            normal = executor.submit(run_normal_generation)
            assert normal_started.wait(timeout=5)
            assert not normal_finished.wait(timeout=0.1)
            release_legacy.set()
            legacy.result(timeout=5)
            normal.result(timeout=5)
    finally:
        release_legacy.set()

    assert Path.cwd() == caller_cwd
    assert_file_content(legacy_output, "generate_with_empty_formatters.py")
    assert_file_content(normal_output, "generate_with_empty_formatters.py")


def test_generate_resolves_relative_custom_template_dir_from_caller_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep API template paths anchored to the caller when output changes cwd."""
    project_root = Path(__file__).parents[2]
    relative_template_dir = Path("tests/data/templates_relative_dir")
    expected_file = EXPECTED_MAIN_PATH / "jsonschema" / "custom_template_relative.py"
    monkeypatch.chdir(project_root)

    generate_options = {
        "input_file_type": InputFileType.JsonSchema,
        "custom_template_dir": relative_template_dir,
    }
    with freeze_time(TIMESTAMP):
        run_generate_and_assert(
            input_=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
            expected_file=expected_file.with_name("custom_template_relative_api.txt"),
            **generate_options,
        )
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
            output_path=tmp_path / "relative" / "model.py",
            assert_func=assert_file_content,
            expected_file=expected_file,
            **generate_options,
        )
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
            output_path=tmp_path / "absolute" / "model.py",
            assert_func=assert_file_content,
            expected_file=expected_file,
            **{
                **generate_options,
                "custom_template_dir": project_root / relative_template_dir,
            },
        )
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
            output_path=tmp_path / "cli.py",
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=expected_file,
            force_exec_validation=True,
            extra_args=[
                "--custom-template-dir",
                str(relative_template_dir),
            ],
        )
    for output_path in (tmp_path / "relative" / "model.py", tmp_path / "absolute" / "model.py"):
        validate_generated_code(output_path.read_text(encoding="utf-8"), str(output_path), do_exec=True)


def test_generate_with_custom_formatter_and_empty_formatters(output_file: Path) -> None:
    """Keep custom formatting when the built-in formatter list is empty."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        formatters=[],
        custom_formatters=["tests.data.python.custom_formatters.add_comment"],
        assert_func=assert_file_content,
        expected_file="generate_with_custom_formatter_and_empty_formatters.py",
    )


def test_parser_formatter_builder_override_with_empty_formatters() -> None:
    """Keep subclass formatter-builder hooks active with an empty formatter list."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
    from tests.data.python.custom_formatters.add_comment import CodeFormatter as AddCommentFormatter

    class ConfiguringJsonSchemaParser(JsonSchemaParser):
        code_formatter_build_count = 0

        def _build_code_formatter(
            self,
            settings_path: Path | None,
            *,
            is_multi_module_output: bool,
        ) -> CodeFormatter:
            code_formatter = super()._build_code_formatter(
                settings_path,
                is_multi_module_output=is_multi_module_output,
            )
            code_formatter.custom_formatters.append(AddCommentFormatter(formatter_kwargs={}))
            self.code_formatter_build_count += 1
            return code_formatter

    parser = ConfiguringJsonSchemaParser(
        source=(JSON_SCHEMA_DATA_PATH / "person.json").resolve(),
        formatters=[],
    )
    assert_output(
        f"{parser.parse()}\n",
        EXPECTED_MAIN_PATH / "parser_formatter_builder_override_with_empty_formatters.py",
    )
    assert_output(
        f"{parser.code_formatter_build_count}\n",
        EXPECTED_MAIN_PATH / "parser_formatter_builder_override_with_empty_formatters_calls.txt",
    )


def test_parser_instance_formatter_builder_with_empty_formatters() -> None:
    """Keep an instance-injected formatter builder active with an empty formatter list."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
    from tests.data.python.custom_formatters.add_comment import CodeFormatter as AddCommentFormatter

    parser = JsonSchemaParser(
        source=(JSON_SCHEMA_DATA_PATH / "person.json").resolve(),
        formatters=[],
    )
    default_builder = parser._build_code_formatter
    code_formatter_build_count = 0

    def build_code_formatter(
        settings_path: Path | None,
        *,
        is_multi_module_output: bool,
    ) -> CodeFormatter:
        nonlocal code_formatter_build_count
        code_formatter = default_builder(
            settings_path,
            is_multi_module_output=is_multi_module_output,
        )
        code_formatter.custom_formatters.append(AddCommentFormatter(formatter_kwargs={}))
        code_formatter_build_count += 1
        return code_formatter

    parser._build_code_formatter = build_code_formatter  # type: ignore[method-assign]
    assert_output(
        f"{parser.parse()}\n",
        EXPECTED_MAIN_PATH / "parser_formatter_builder_override_with_empty_formatters.py",
    )
    assert_output(
        f"{code_formatter_build_count}\n",
        EXPECTED_MAIN_PATH / "parser_formatter_builder_override_with_empty_formatters_calls.txt",
    )


def test_generate_keeps_existing_path_string_input() -> None:
    """Test generate() keeps existing path strings as inline source text."""
    run_generate_and_assert(
        input_=str(JSON_SCHEMA_DATA_PATH / "person.json"),
        input_file_type=InputFileType.Yaml,
        input_filename="inline.yaml",
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_keeps_existing_path_string_input.py",
    )


def test_generate_keeps_non_path_string_input() -> None:
    """Test generate() keeps non-path strings as inline source text."""
    run_generate_and_assert(
        input_="name: Alice",
        input_file_type=InputFileType.Yaml,
        input_filename="inline.yaml",
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_keeps_non_path_string_input.py",
    )


def test_generate_warns_when_input_string_is_existing_path_on_failure(tmp_path: Path) -> None:
    """Test failed string input warns when the value is an existing path."""
    input_path = tmp_path / "schema.json"
    input_path.write_text("{", encoding="utf-8")

    with pytest.warns(UserWarning, match="Path"), pytest.raises(Error):
        generate(
            input_=str(input_path),
            input_file_type=InputFileType.Json,
            disable_timestamp=True,
            formatters=[],
        )


def test_generate_warning_does_not_mask_original_error_with_strict_warning_filter(tmp_path: Path) -> None:
    """Test strict warning filters do not replace the original generate error."""
    input_path = tmp_path / "schema.json"
    input_path.write_text("{", encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        with pytest.raises(Error):
            generate(
                input_=str(input_path),
                input_file_type=InputFileType.Json,
                disable_timestamp=True,
                formatters=[],
            )


def test_generate_does_not_warn_for_non_existing_path_string_on_failure(tmp_path: Path) -> None:
    """Test failed string input only warns for values resolving to existing paths."""
    invalid_input_path = tmp_path / "invalid.json"
    invalid_input_path.write_text("{", encoding="utf-8")

    failed_inputs: tuple[Path | str, ...] = (
        invalid_input_path,
        "not\njson",
        str(tmp_path / "missing.json"),
        "~this-user-should-not-exist-20260703/schema.json",
    )
    for failed_input in failed_inputs:
        with warnings.catch_warnings(record=True) as warning_records:
            warnings.simplefilter("always")
            with pytest.raises(Error):
                generate(
                    input_=failed_input,
                    input_file_type=InputFileType.Json,
                    disable_timestamp=True,
                    formatters=[],
                )
        assert_warnings_do_not_contain(
            warning_records,
            "pass a `Path` object to read it as a file",
        )


def test_generate_returns_string_with_pydantic_v2() -> None:
    """Test that generate() returns str for Pydantic v2 models."""
    json_schema = '{"type": "object", "properties": {"value": {"type": "number"}}}'
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="schema.json",
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_with_pydantic_v2.py",
    )


def test_generate_returns_string_with_dataclass() -> None:
    """Test that generate() returns str for dataclass models."""
    json_schema = '{"type": "object", "properties": {"value": {"type": "string"}}}'
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="data.json",
        output_model_type=DataModelType.DataclassesDataclass,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_with_dataclass.py",
    )


def test_generate_uses_multiline_docstring_by_default() -> None:
    """Test that schema descriptions keep the historical multi-line docstring format."""
    json_schema = '{"title": "Person", "description": "Person model", "type": "object", "properties": {}}'
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="schema.json",
        use_schema_description=True,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_uses_multiline_docstring_by_default.py",
    )


def test_generate_uses_single_line_docstring_when_enabled() -> None:
    """Test that schema descriptions use one-line docstrings when requested."""
    json_schema = '{"title": "Person", "description": "Person model", "type": "object", "properties": {}}'
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="schema.json",
        use_schema_description=True,
        use_single_line_docstring=True,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_uses_single_line_docstring_when_enabled.py",
    )


@pytest.mark.cli_doc(
    options=["--use-single-line-docstring"],
    option_description="""Emit short docstrings on a single line.

The `--use-single-line-docstring` flag formats docstrings that fit on one line
as compact single-line docstrings while keeping the historical multi-line
format as the default.""",
    input_schema="jsonschema/person.json",
    cli_args=["--use-field-description", "--use-single-line-docstring"],
    golden_output="main/use_single_line_docstring.py",
    related_options=["--use-schema-description", "--use-field-description"],
)
def test_main_use_single_line_docstring(output_file: Path) -> None:
    """Emit short docstrings on a single line."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file="use_single_line_docstring.py",
        extra_args=["--use-field-description", "--use-single-line-docstring"],
    )


def test_generate_returns_none_when_output_path_provided(tmp_path: Path) -> None:
    """Test that generate() returns None when output path is provided."""
    json_schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    output = tmp_path / "model.py"
    result = generate(
        json_schema,
        input_file_type=InputFileType.JsonSchema,
        output=output,
        disable_timestamp=True,
    )
    assert_generate_wrote_file(result, output)


def test_generate_file_content_matches_return_value(tmp_path: Path) -> None:
    """Test that file content matches what would be returned with output=None."""
    json_schema = '{"type": "object", "properties": {"id": {"type": "integer"}}}'

    return_result = generate(
        json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="test.json",
        disable_timestamp=True,
    )

    output = tmp_path / "model.py"
    generate(
        json_schema,
        input_file_type=InputFileType.JsonSchema,
        input_filename="test.json",
        output=output,
        disable_timestamp=True,
    )
    assert_generated_file_matches_output(return_result, output)


def test_generate_returns_dict_for_multiple_modules(tmp_path: Path) -> None:
    """Test that generate() returns GeneratedModules dict for multiple modules."""
    main_schema = tmp_path / "main.json"
    main_schema.write_text(
        """{
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "address": {"$ref": "address.json#/definitions/Address"}
                }
            }
        }
    }"""
    )

    address_schema = tmp_path / "address.json"
    address_schema.write_text(
        """{
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"}
                }
            }
        }
    }"""
    )

    result = generate(
        tmp_path,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
    )

    assert_generated_modules_output(
        result,
        EXPECTED_MAIN_PATH / "generate_returns_dict_for_multiple_modules",
        transform=lambda output: output.replace(f"#   filename:  {tmp_path.name}", "#   filename:  <tmpdir>"),
    )

    with chdir(tmp_path):
        result = generate(
            [main_schema.relative_to(tmp_path), address_schema.relative_to(tmp_path)],
            input_file_type=InputFileType.JsonSchema,
            disable_timestamp=True,
        )
    assert_generated_modules_output(
        result,
        EXPECTED_MAIN_PATH / "generate_returns_dict_for_multiple_modules",
        transform=lambda output: output.replace("#   filename:  <dict>", "#   filename:  <tmpdir>"),
    )


def test_generate_path_lists_keep_caller_or_common_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Generate relative and external API path lists without changing module paths."""
    source_dir = JSON_SCHEMA_DATA_PATH / "multiple_files"
    list_api_expected_dir = EXPECTED_MAIN_PATH / "jsonschema" / "path_list_inputs_api"
    list_file_expected_dir = EXPECTED_MAIN_PATH / "jsonschema" / "path_list_inputs"
    directory_expected_dir = EXPECTED_MAIN_PATH / "jsonschema" / "multiple_files"
    source_names = ("file_d.json", "file_b.json", "file_a.json", "file_c.json")
    absolute_paths = [source_dir / name for name in source_names]
    formatters = [Formatter.BLACK, Formatter.ISORT]

    monkeypatch.chdir(source_dir)
    relative_paths = [Path(name) for name in source_names]
    for input_paths in (relative_paths, absolute_paths):
        with assert_inputs_not_mutated({"input_paths": input_paths}):
            modules = generate(input_paths, input_file_type=InputFileType.Auto, formatters=formatters)
        assert_generated_modules_output(modules, list_api_expected_dir)

    run_generate_and_assert(
        input_=Path("file_b.json"),
        input_file_type=InputFileType.Auto,
        expected_file=EXPECTED_MAIN_PATH / "jsonschema" / "path_list_single" / "file_b.py",
        formatters=formatters,
    )
    run_generate_and_assert(
        input_=(source_dir / "file_b.json").read_text(encoding="utf-8"),
        input_file_type=InputFileType.Auto,
        input_filename="file_b.json",
        expected_file=EXPECTED_MAIN_PATH / "jsonschema" / "path_list_single" / "file_b.py",
        formatters=formatters,
    )

    single_modules = generate([Path("file_b.json")], input_file_type=InputFileType.Auto, formatters=formatters)
    assert_generated_modules_output(
        single_modules,
        EXPECTED_MAIN_PATH / "jsonschema" / "path_list_single",
    )

    monkeypatch.chdir(tmp_path)
    with assert_inputs_not_mutated({"input_paths": absolute_paths}):
        modules = generate(absolute_paths, input_file_type=InputFileType.Auto, formatters=formatters)
    assert_generated_modules_output(modules, list_api_expected_dir)

    api_output = tmp_path / "api"
    with assert_inputs_not_mutated({"input_paths": absolute_paths}):
        generate(
            absolute_paths,
            input_file_type=InputFileType.JsonSchema,
            output=api_output,
            formatters=formatters,
        )
    assert_directory_content(api_output, list_file_expected_dir)

    run_main_and_assert(
        input_path=source_dir,
        output_path=tmp_path / "cli",
        input_file_type="jsonschema",
        expected_directory=directory_expected_dir,
    )


@pytest.mark.parametrize("use_cache", [False, True])
@pytest.mark.parametrize(("encoding", "newline"), [("utf-8", "\n"), ("utf-16", "\r\n")])
def test_generate_auto_path_list_reuses_detected_first_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, use_cache: bool, encoding: str, newline: str
) -> None:
    """Reuse Auto detection text while generating all real path-list sources."""
    reads: dict[str, int] = defaultdict(int)

    class CountingPath(type(Path())):
        """Count actual source opens without replacing file-system behavior."""

        def open(self, *args: Any, **kwargs: Any) -> Any:
            reads[self.name] += 1
            return super().open(*args, **kwargs)

    source_dir = JSON_SCHEMA_DATA_PATH / "multiple_files"
    source_names = ("file_d.json", "file_b.json", "file_a.json", "file_c.json")
    for name in source_names:
        text = (source_dir / name).read_text(encoding="utf-8")
        (tmp_path / name).write_bytes(text.replace("\n", newline).encode(encoding))
    input_paths = [CountingPath(tmp_path / name) for name in source_names]
    monkeypatch.chdir(tmp_path)
    with _optional_test_parsed_source_cache(use_cache), assert_inputs_not_mutated({"input_paths": input_paths}):
        for _ in range(3):
            reads.clear()
            modules = generate(
                input_paths,
                input_file_type=InputFileType.Auto,
                encoding=encoding,
                formatters=[Formatter.BLACK, Formatter.ISORT],
            )
            assert_generated_modules_output(
                modules,
                EXPECTED_MAIN_PATH / "jsonschema" / "path_list_inputs_api",
            )
            assert_output(
                f"{json.dumps(reads, indent=2)}\n",
                EXPECTED_MAIN_PATH / "jsonschema" / "path_list_input_reads.txt",
            )


def test_generate_modular_stdout_and_directory_match_fixture(output_dir: Path) -> None:
    """Keep API stdout and directory emission aligned with the modular fixture."""
    generate_options = {
        "input_file_type": InputFileType.OpenAPI,
    }
    expected_directory = EXPECTED_MAIN_PATH / "openapi" / "modular"

    with freeze_time(TIMESTAMP):
        generated = generate(OPEN_API_DATA_PATH / "modular.yaml", **generate_options)
        assert_generated_modules_output(generated, expected_directory, transform=lambda output: f"{output}\n")

        generate(OPEN_API_DATA_PATH / "modular.yaml", output=output_dir, **generate_options)
    assert_directory_content(output_dir, expected_directory)


def test_generate_multimodule_builtin_directory_matches_fixture(output_dir: Path) -> None:
    """Keep deferred non-Ruff directory output on its existing write-only path."""
    generate(
        JSON_SCHEMA_DATA_PATH / "all_exports_multi_file",
        input_file_type=InputFileType.JsonSchema,
        output=output_dir,
        formatters=[Formatter.BUILTIN],
        disable_timestamp=True,
        all_exports_scope=AllExportsScope.Recursive,
    )
    assert_directory_content(output_dir, EXPECTED_MAIN_PATH / "jsonschema" / "all_exports_multi_file")


def test_generate_builtin_string_normalization_matches_fixture(output_file: Path) -> None:
    """Keep double-quote normalization byte-compatible with the external fixture."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        formatters=[Formatter.BUILTIN],
        use_double_quotes=True,
        disable_timestamp=True,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_PATH / "jsonschema" / "person_use_double_quotes.py",
    )


def test_generate_builtin_string_normalization_module_split(output_dir: Path) -> None:
    """Keep double-quote normalization byte-compatible for every generated module."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "module_split_single" / "input.json",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--use-double-quotes",
            "--module-split-mode",
            "single",
            "--all-exports-scope",
            "recursive",
            "--use-exact-imports",
        ],
        expected_directory=EXPECTED_MAIN_PATH / "jsonschema" / "module_split_single",
    )


@pytest.mark.allow_direct_assert
def test_generated_modules_type_alias_is_exported() -> None:
    """Test that GeneratedModules is exported from the module."""
    assert GeneratedModules is not None


def test_generate_returns_string_with_custom_file_header() -> None:
    """Default to replacing the generated header when output=None."""
    json_schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    custom_header = "# Custom header\n# More comments"
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        custom_file_header=custom_header,
        enable_version_header=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_with_custom_file_header.py",
    )


@pytest.mark.cli_doc(
    options=["--custom-file-header-mode"],
    option_description="""Choose how a custom file header combines with generated provenance.

The `prepend` mode places a license or copyright header before the generated filename,
timestamp, version, and command metadata. The default `replace` mode preserves existing behavior.""",
    input_schema="jsonschema/simple_string.json",
    cli_args=[
        "--custom-file-header",
        "# Copyright {year}",
        "--custom-file-header-mode",
        "prepend",
        "--disable-timestamp",
        "--enable-version-header",
    ],
    golden_output="main/custom_file_header_prepend.py",
    related_options=["--custom-file-header", "--custom-file-header-path", "--enable-version-header"],
)
def test_main_custom_file_header_prepend(output_file: Path) -> None:
    """Choose how a custom file header combines with generated provenance."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="custom_file_header_prepend.py",
        extra_args=[
            "--custom-file-header",
            "# Copyright {year}",
            "--custom-file-header-mode",
            "prepend",
            "--disable-timestamp",
            "--enable-version-header",
        ],
        transform=lambda output: output.replace(
            f"#   version:   {datamodel_code_generator.get_version()}", "#   version:   0.0.0"
        ),
    )


def test_generate_returns_string_with_custom_file_header_prepend() -> None:
    """Prepend a custom header to provenance when output=None."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header="# SPDX-License-Identifier: MIT",
        custom_file_header_mode=CustomFileHeaderMode.Prepend,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_with_custom_file_header_prepend.py",
    )


def test_generate_returns_modules_with_custom_file_header_prepend() -> None:
    """Prepend custom and per-file provenance headers for output=None modules."""
    result = generate(
        JSON_SCHEMA_DATA_PATH / "all_exports_multi_file",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_with_docstring_and_import.txt",
        custom_file_header_mode=CustomFileHeaderMode.Prepend,
        disable_timestamp=True,
        all_exports_scope=AllExportsScope.Recursive,
    )

    assert_generated_modules_output(
        result,
        EXPECTED_MAIN_PATH / "jsonschema" / "custom_file_header_path_prepend_multi_file",
        transform=lambda output: f"{output}\n",
    )


def test_generate_custom_file_header_prepend_after_formatter_comment() -> None:
    """Extract future imports after comments added by a custom formatter."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        formatters=[],
        custom_formatters=["tests.data.python.custom_formatters.add_comment"],
        custom_file_header='"""Module docstring."""\n\nimport sys',
        custom_file_header_mode=CustomFileHeaderMode.Prepend,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_prepend_after_formatter_comment.py",
    )


def test_generate_custom_file_header_prepend_after_formatter_docstring() -> None:
    """Extract future imports after a module docstring added by a custom formatter."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        formatters=[],
        custom_formatters=["tests.data.python.custom_formatters.add_docstring"],
        custom_file_header='"""Module docstring."""\n\nimport sys',
        custom_file_header_mode=CustomFileHeaderMode.Prepend,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_prepend_after_formatter_docstring.py",
    )


def test_generate_custom_file_header_prepend_after_formatter_parenthesized_docstring() -> None:
    """Extract future imports after a parenthesized formatter docstring."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        formatters=[],
        custom_formatters=["tests.data.python.custom_formatters.add_parenthesized_docstring"],
        custom_file_header='"""Module docstring."""\n\nimport sys',
        custom_file_header_mode=CustomFileHeaderMode.Prepend,
        disable_timestamp=True,
        expected_file=(
            EXPECTED_MAIN_PATH / "generate_custom_file_header_prepend_after_formatter_parenthesized_docstring.py"
        ),
    )


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param("\n", 1, id="blank"),
        pytest.param("# comment\r\n", len("# comment\r\n"), id="comment-crlf"),
        pytest.param(
            "#!/usr/bin/env python\n# coding: utf-8\n",
            len("#!/usr/bin/env python\n# coding: utf-8\n"),
            id="shebang-encoding",
        ),
        pytest.param(
            ' \t\f# comment with """quotes""";\r\n# lf with \'quotes\';\n# cr with semicolon;\r\f# no newline',
            len(' \t\f# comment with """quotes""";\r\n# lf with \'quotes\';\n# cr with semicolon;\r\f# no newline'),
            id="comment-fast-path-mixed-newlines",
        ),
        pytest.param(
            "#" + "x" * (_COMMENT_ONLY_HEADER_FAST_PATH_LIMIT - 1),
            _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT,
            id="comment-fast-path-limit",
        ),
        pytest.param(
            "#" + "x" * _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT,
            _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT + 1,
            id="comment-over-fast-path-limit",
        ),
        pytest.param("# comment\r\nimport os\r\n", len("# comment\r\n"), id="comment-prefixed-statement"),
        pytest.param("r'''doc'''\r\n\r\nimport os\r\n", len("r'''doc'''\r\n\r\n"), id="raw-docstring-crlf"),
        pytest.param('"""doc"""\r\rimport os\r', len('"""doc"""\r\r'), id="docstring-cr"),
        pytest.param('\f"""doc"""\n\nimport os\n', len('\f"""doc"""\n\n'), id="docstring-form-feed"),
        pytest.param('"""doc"""\n  ', len('"""doc"""\n  '), id="docstring-trailing-blank-no-newline"),
        pytest.param("u'doc'\n\nimport os\n", len("u'doc'\n\n"), id="unicode-docstring"),
        pytest.param('"""doc""";\nimport os\n', len('"""doc""";\n'), id="semicolon-docstring"),
        pytest.param(
            '("""doc"""); # comment\nimport os\n',
            len('("""doc"""); # comment\n'),
            id="parenthesized-semicolon-docstring",
        ),
        pytest.param('"""doc"""; import os\n', len('"""doc""";'), id="semicolon-second-statement"),
        pytest.param(
            '"""doc"""; ' + "\\" + "\nimport os\n",
            0,
            id="semicolon-line-continuation",
        ),
        pytest.param("b'doc'\n", 0, id="bytes-expression"),
        pytest.param("f'doc'\n", 0, id="f-string-expression"),
        pytest.param('"doc"()\n', 0, id="string-postfix-call"),
        pytest.param('("doc")()\n', 0, id="parenthesized-string-postfix-call"),
        pytest.param("()\n", 0, id="expression"),
        pytest.param("import os\n", 0, id="statement"),
        pytest.param("(\n", 0, id="unclosed-opening-group"),
        pytest.param("'''unterminated", 0, id="malformed"),
    ],
)
def test_future_import_insertion_point_handles_tokenizer_boundaries(header: str, expected: int) -> None:
    """Blank, expression, and malformed headers have deterministic insertion points."""
    assert _find_future_import_insertion_point(header) == expected


@pytest.mark.allow_direct_assert
def test_future_import_insertion_point_handles_runtime_tokenizer_syntax_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat runtime-specific tokenizer SyntaxError as malformed external input."""

    def raise_syntax_error(_readline: Any) -> Any:
        raise SyntaxError

    monkeypatch.setattr(tokenize, "generate_tokens", raise_syntax_error)

    assert _find_future_import_insertion_point("(") == 0


def test_custom_file_header_prepend_preserves_future_import_text(output_file: Path) -> None:
    """Preserve future-import text inside generated schema descriptions."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "custom_file_header_schema_description_future.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="jsonschema/custom_file_header_schema_description_future.py",
        extra_args=[
            "--custom-file-header",
            "# SPDX-License-Identifier: MIT",
            "--custom-file-header-mode",
            "prepend",
            "--disable-timestamp",
            "--use-schema-description",
        ],
    )


def test_generate_returns_string_with_custom_file_header_and_code() -> None:
    """Test generate() with custom_file_header containing code after docstring."""
    json_schema = '{"type": "object", "properties": {"id": {"type": "integer"}}}'
    custom_header = '"""Module docstring."""\n\nimport sys'
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        custom_file_header=custom_header,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_with_custom_file_header_and_code.py",
    )


def test_generate_custom_file_header_preserves_runtime_target_tokenizer_boundary() -> None:
    """Keep 3.10/3.12/3.14 runtime tokenizers behind the target-syntax boundary."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_with_newer_target_syntax.txt",
        target_python_version=PythonVersion.PY_312,
        formatters=[],
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_with_newer_target_syntax.py",
    )


def test_generate_custom_file_header_does_not_treat_fstring_as_docstring() -> None:
    """Place future imports before an f-string expression."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_with_fstring.txt",
        target_python_version=PythonVersion.PY_312,
        formatters=[],
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_with_fstring.py",
    )


def test_generate_custom_file_header_does_not_treat_postfix_call_as_docstring() -> None:
    """Place future imports before a call on a parenthesized string expression."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_with_postfix_call.txt",
        formatters=[],
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_with_postfix_call.py",
    )


def test_generate_custom_file_header_with_form_feed() -> None:
    """Preserve a docstring preceded by form-feed whitespace."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_with_form_feed.txt",
        formatters=[],
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_with_form_feed.py",
    )


def test_generate_custom_file_header_comments_only_fast_path() -> None:
    """Preserve comment contents while placing future imports without tokenization."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_fast_path.txt",
        formatters=[],
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_comments_only.py",
    )


def test_generate_custom_file_header_with_parenthesized_docstring() -> None:
    """Place future imports after a parenthesized, concatenated docstring."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=InputFileType.JsonSchema,
        custom_file_header_path=DATA_PATH / "custom_file_header_with_parenthesized_docstring.txt",
        target_python_version=PythonVersion.PY_312,
        formatters=[],
        expected_file=EXPECTED_MAIN_PATH / "generate_custom_file_header_with_parenthesized_docstring.py",
    )


def test_generate_returns_string_with_custom_file_header_no_future() -> None:
    """Test generate() with custom_file_header when body has no future imports."""
    json_schema = '{"type": "object", "properties": {"id": {"type": "integer"}}}'
    custom_header = "# Custom header for legacy code"
    run_generate_and_assert(
        input_=json_schema,
        input_file_type=InputFileType.JsonSchema,
        custom_file_header=custom_header,
        disable_future_imports=True,
        expected_file=EXPECTED_MAIN_PATH / "generate_returns_string_with_custom_file_header_no_future.py",
    )


def test_generate_with_dict_jsonschema() -> None:
    """Test generate() with dict input as JsonSchema."""
    from tests.data.dict_input import jsonschema_dict

    run_generate_and_assert(
        input_=jsonschema_dict,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "dict_input" / "jsonschema.py",
        assert_input_unchanged=True,
    )


def test_generate_with_dict_jsonschema_boolean_exclusive_bounds_keeps_input_unchanged(tmp_path: Path) -> None:
    """Test boolean exclusive bounds in dict input are normalized without mutating the caller's schema."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "maximum": 10,
                "exclusiveMaximum": True,
                "minimum": 0,
                "exclusiveMinimum": False,
            },
        },
    }
    expected_file = tmp_path / "expected.py"
    expected_file.write_text(
        """# generated by datamodel-codegen:
#   filename:  <dict>

from __future__ import annotations

from pydantic import BaseModel, confloat


class Model(BaseModel):
    score: confloat(ge=0.0, lt=10.0) | None = None""",
        encoding="utf-8",
    )

    run_generate_and_assert(
        input_=schema,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        expected_file=expected_file,
        assert_input_unchanged=True,
    )


def test_generate_with_dict_openapi() -> None:
    """Test generate() with dict input as OpenAPI."""
    from tests.data.dict_input import openapi_dict

    run_generate_and_assert(
        input_=openapi_dict,
        input_file_type=InputFileType.OpenAPI,
        disable_timestamp=True,
        expected_file=EXPECTED_MAIN_PATH / "dict_input" / "openapi.py",
        assert_input_unchanged=True,
    )


def test_generate_with_dict_auto_raises_error() -> None:
    """Test generate() with dict input + Auto raises error."""
    from tests.data.dict_input import auto_error_dict

    with pytest.raises(Error, match="input_file_type=Auto is not supported for dict input"):
        generate(auto_error_dict, input_file_type=InputFileType.Auto)


def test_generate_with_dict_graphql_raises_error() -> None:
    """Test generate() with dict input + GraphQL raises error."""
    from tests.data.dict_input import graphql_error_dict

    with pytest.raises(Error, match="Dict input is not supported for GraphQL"):
        generate(graphql_error_dict, input_file_type=InputFileType.GraphQL)


def test_generate_with_dict_xmlschema_raises_error() -> None:
    """Test generate() with dict input + XML Schema raises error."""
    from tests.data.dict_input import auto_error_dict

    with pytest.raises(Error, match="Dict input is not supported for xmlschema"):
        generate(auto_error_dict, input_file_type=InputFileType.XMLSchema)


def test_generate_with_dict_openapi_validation_warns() -> None:
    """Test generate() with dict input + validation skips validation with warning."""
    import warnings

    from tests.data.dict_input import openapi_dict

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        run_generate_and_assert(
            input_=openapi_dict,
            input_file_type=InputFileType.OpenAPI,
            validation=True,
            disable_timestamp=True,
            expected_file=EXPECTED_MAIN_PATH / "dict_input" / "openapi.py",
        )
        # Check that both deprecated warning and dict input warning were raised
        assert_warnings_contain(w, "deprecated", "dict input")


@pytest.mark.parametrize(
    "input_file_type",
    [InputFileType.Json, InputFileType.Yaml, InputFileType.CSV],
    ids=["json", "yaml", "csv"],
)
def test_generate_with_dict_raw_data_types_raises_error(input_file_type: InputFileType) -> None:
    """Test generate() with dict input + Json/Yaml/CSV raises error."""
    from tests.data.dict_input import auto_error_dict

    with pytest.raises(Error, match=f"Dict input is not supported for {input_file_type.value}"):
        generate(auto_error_dict, input_file_type=input_file_type)


def test_generate_with_config_object(output_file: Path) -> None:
    """Test generate() with GenerateConfig object."""
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import StrictTypes

    GenerateConfig.model_rebuild(_types_namespace={"StrictTypes": StrictTypes, "UnionMode": UnionMode})
    config = GenerateConfig(
        input_filename="test.json",
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        use_schema_description=True,
        snake_case_field=True,
        field_constraints=True,
        extra_template_data={"Model": {"custom_key": "custom_value"}},
    )
    generate(
        input_='{"type": "object", "properties": {"userName": {"type": "string"}}}',
        config=config,
    )
    assert_file_content(output_file, "generate_with_config_object.py")


_EXTRA_TEMPLATE_COMMENT = "safe comment\rprint('PWNED')\nraise SystemExit(1)\vimport os\fexec('PWNED')"
_EXTRA_TEMPLATE_COMMENT_OBJECT_SCHEMA = """
{
  "title": "Model",
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    }
  }
}
"""
_EXTRA_TEMPLATE_COMMENT_ROOT_SCHEMA = """
{
  "title": "Model",
  "type": "string"
}
"""
_EXTRA_TEMPLATE_COMMENT_FORBIDDEN_STARTS = ("print(", "raise ", "import os", "exec(")
_BUILTIN_TEMPLATE_DATA_MARKER = "builtin_template_data_marker"
_BUILTIN_TEMPLATE_CONFIG_DATA_PATH = JSON_SCHEMA_DATA_PATH / "extra_data_builtin_template_config.json"
_BUILTIN_TEMPLATE_MSGSPEC_DATA_PATH = JSON_SCHEMA_DATA_PATH / "extra_data_builtin_template_msgspec.json"
_BUILTIN_TEMPLATE_RESERVED_DATA_PATH = JSON_SCHEMA_DATA_PATH / "extra_data_builtin_template_reserved.json"
_BUILTIN_TEMPLATE_TYPED_DICT_DATA_PATH = JSON_SCHEMA_DATA_PATH / "extra_data_builtin_template_typed_dict.json"
_BUILTIN_TEMPLATE_CUSTOM_DIR = DATA_PATH / "templates_builtin_template_data"
_BUILTIN_TEMPLATE_INCLUDE_ONLY_DIR = DATA_PATH / "templates_include_only"
_BUILTIN_TEMPLATE_EXPECTED_PATH = EXPECTED_MAIN_PATH / "template_data"


def _generate_with_extra_template_comment(input_: str, **generate_kwargs: Any) -> str:
    result = generate(
        input_=input_,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        extra_template_data=defaultdict(dict, {"Model": {"comment": _EXTRA_TEMPLATE_COMMENT}}),
        **generate_kwargs,
    )
    if not isinstance(result, str):  # pragma: no cover
        pytest.fail(f"Expected generate() to return str, got {type(result).__name__}")
    validate_generated_code(result, "<generated>")
    assert_no_uncommented_generated_code(
        result,
        forbidden_starts=_EXTRA_TEMPLATE_COMMENT_FORBIDDEN_STARTS,
    )
    return result


@pytest.mark.parametrize(
    ("input_", "generate_kwargs"),
    [
        (
            _EXTRA_TEMPLATE_COMMENT_OBJECT_SCHEMA,
            {"output_model_type": DataModelType.PydanticV2BaseModel},
        ),
        (
            _EXTRA_TEMPLATE_COMMENT_ROOT_SCHEMA,
            {"output_model_type": DataModelType.PydanticV2BaseModel},
        ),
        (
            _EXTRA_TEMPLATE_COMMENT_ROOT_SCHEMA,
            {
                "output_model_type": DataModelType.PydanticV2BaseModel,
                "use_root_model_type_alias": True,
            },
        ),
        (
            _EXTRA_TEMPLATE_COMMENT_ROOT_SCHEMA,
            {
                "output_model_type": DataModelType.PydanticV2BaseModel,
                "use_type_alias": True,
                "target_python_version": PythonVersion.PY_310,
            },
        ),
        (
            _EXTRA_TEMPLATE_COMMENT_ROOT_SCHEMA,
            {
                "output_model_type": DataModelType.TypingTypedDict,
                "target_python_version": PythonVersion.PY_310,
            },
        ),
    ],
)
def test_generate_extra_template_data_comment_is_safe(
    input_: str,
    generate_kwargs: dict[str, Any],
) -> None:
    """Ensure extra template comments cannot add Python statements."""
    _generate_with_extra_template_comment(input_, **generate_kwargs)


def test_main_extra_template_data_comment_is_safe(output_file: Path, tmp_path: Path) -> None:
    """Ensure CLI extra template comments cannot add Python statements."""
    input_path = tmp_path / "schema.json"
    input_path.write_text(_EXTRA_TEMPLATE_COMMENT_OBJECT_SCHEMA, encoding="utf-8")
    extra_template_data_path = tmp_path / "extra_template_data.json"
    extra_template_data_path.write_text(
        json.dumps({"Model": {"comment": _EXTRA_TEMPLATE_COMMENT}}),
        encoding="utf-8",
    )

    run_main_with_args(
        [
            "--input",
            str(input_path),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output_file),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--extra-template-data",
            str(extra_template_data_path),
        ],
    )

    generated_content = output_file.read_text(encoding="utf-8")
    validate_generated_code(generated_content, str(output_file))
    assert_no_uncommented_generated_code(
        generated_content,
        forbidden_starts=_EXTRA_TEMPLATE_COMMENT_FORBIDDEN_STARTS,
    )


def test_generate_builtin_template_data_is_non_executing() -> None:
    """Render public built-in template data as literals rather than Python source."""
    generated = generate(
        input_=JSON_SCHEMA_DATA_PATH / "person.json",
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        extra_template_data=json.loads(_BUILTIN_TEMPLATE_CONFIG_DATA_PATH.read_text(encoding="utf-8")),
    )
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected generate() to return str, got {type(generated).__name__}")
    validate_generated_code(generated, "<generated>")
    assert_output(f"{generated}\n", _BUILTIN_TEMPLATE_EXPECTED_PATH / "api_builtin_config.py")
    tree = ast.parse(generated)
    marker_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == _BUILTIN_TEMPLATE_DATA_MARKER for target in node.targets)
    ]
    if marker_assignments:  # pragma: no cover - assertion reports the exact source-safety regression
        pytest.fail(f"Generated AST contains injected marker assignments: {marker_assignments!r}")


@pytest.mark.parametrize(
    ("output_model_type", "template_data_path", "expected_file"),
    [
        pytest.param(
            "pydantic_v2.BaseModel",
            _BUILTIN_TEMPLATE_CONFIG_DATA_PATH,
            _BUILTIN_TEMPLATE_EXPECTED_PATH / "builtin_config.py",
            id="pydantic-config",
        ),
        pytest.param(
            "msgspec.Struct",
            _BUILTIN_TEMPLATE_MSGSPEC_DATA_PATH,
            _BUILTIN_TEMPLATE_EXPECTED_PATH / "builtin_msgspec.py",
            id="msgspec-base-class-kwargs",
        ),
        pytest.param(
            "typing.TypedDict",
            _BUILTIN_TEMPLATE_TYPED_DICT_DATA_PATH,
            _BUILTIN_TEMPLATE_EXPECTED_PATH / "builtin_typed_dict.py",
            id="typed-dict-extra-items",
        ),
    ],
)
def test_main_builtin_template_data_is_non_executing(
    output_file: Path,
    output_model_type: str,
    template_data_path: Path,
    expected_file: Path,
) -> None:
    """Keep built-in CLI template data literal-only with fixture-backed output."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--target-python-version",
            "3.10",
            "--extra-template-data",
            str(template_data_path),
        ],
        assert_func=assert_file_content,
        expected_file=expected_file,
    )


def test_main_rejects_reserved_builtin_template_data(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Reject code-bearing template context keys before built-in output is written."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--extra-template-data",
            str(_BUILTIN_TEMPLATE_RESERVED_DATA_PATH),
        ],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stderr_contains="class_body_lines is reserved",
    )


def test_main_partial_custom_template_dir_keeps_builtin_template_data_safe(
    capsys: pytest.CaptureFixture[str], output_file: Path
) -> None:
    """An include-only custom directory cannot opt a built-in root into raw context."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--custom-template-dir",
            str(_BUILTIN_TEMPLATE_INCLUDE_ONLY_DIR),
            "--extra-template-data",
            str(_BUILTIN_TEMPLATE_RESERVED_DATA_PATH),
        ],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stderr_contains="class_body_lines is reserved",
    )


def test_main_custom_template_data_remains_unrestricted(output_file: Path) -> None:
    """Trusted custom templates retain their longstanding raw-context contract."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--custom-template-dir",
            str(_BUILTIN_TEMPLATE_CUSTOM_DIR),
            "--extra-template-data",
            str(_BUILTIN_TEMPLATE_RESERVED_DATA_PATH),
        ],
        assert_func=assert_file_content,
        expected_file=_BUILTIN_TEMPLATE_EXPECTED_PATH / "custom_template.py",
    )


def test_generate_extra_template_data_comment_object_is_safe() -> None:
    """Built-in comments stringify extension objects before treating newlines as comments."""

    class Comment:
        def __str__(self) -> str:
            return "safe\n__import__('os').system('id')"

    generated = generate(
        input_=_EXTRA_TEMPLATE_COMMENT_OBJECT_SCHEMA,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
        extra_template_data=defaultdict(dict, {"Model": {"comment": Comment()}}),
    )
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected generate() to return str, got {type(generated).__name__}")
    validate_generated_code(generated, "<generated>")
    assert_no_uncommented_generated_code(generated, forbidden_starts=("__import__(",))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type statement requires Python 3.12+")
@pytest.mark.skipif(version.parse(black.__version__) < version.parse("23.3.0"), reason="black too old")
def test_generate_extra_template_data_comment_is_safe_for_type_statement() -> None:
    """Ensure type statement comments cannot add Python statements."""
    _generate_with_extra_template_comment(
        _EXTRA_TEMPLATE_COMMENT_ROOT_SCHEMA,
        output_model_type=DataModelType.PydanticV2BaseModel,
        use_type_alias=True,
        target_python_version=PythonVersion.PY_312,
    )


def test_generate_config_with_union_mode() -> None:
    """Test GenerateConfig with union_mode field."""
    config = GenerateConfig(
        output_model_type=DataModelType.PydanticV2BaseModel,
        union_mode=UnionMode.left_to_right,
        disable_timestamp=True,
    )
    run_generate_and_assert(
        input_='{"type": "object", "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}',
        config=config,
        expected_file=EXPECTED_MAIN_PATH / "generate_config_union_mode.py",
    )


def test_generate_with_config_and_kwargs_raises_error(output_file: Path) -> None:
    """Test generate() raises error when both config and kwargs are provided."""
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import StrictTypes

    GenerateConfig.model_rebuild(_types_namespace={"StrictTypes": StrictTypes, "UnionMode": UnionMode})
    config = GenerateConfig(
        input_filename="test.json",
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    # Passing both config and kwargs should raise ValueError
    with pytest.raises(ValueError, match="Cannot specify both 'config' and keyword arguments"):
        generate(
            input_='{"type": "object", "properties": {"name": {"type": "string"}}}',
            output=output_file,
            config=config,
            field_constraints=True,
        )


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("parse_error", [RuntimeError("parse failed"), _GenerateParseAbort("parse aborted")])
def test_generate_disposes_parser_when_parse_raises(parse_error: BaseException, mocker: MockerFixture) -> None:
    """Test generate() releases parser-owned references while preserving parse failures."""
    parser = mocker.Mock()
    parser.parse.side_effect = parse_error
    parser.dispose.side_effect = RuntimeError("dispose failed")
    mocker.patch.object(datamodel_code_generator, "_build_parser", return_value=parser)

    with pytest.raises(type(parse_error)) as exc_info:
        generate("{}", input_file_type=InputFileType.JsonSchema, formatters=[])

    assert exc_info.value is parse_error
    parser.dispose.assert_called_once_with()


def test_parser_with_config_and_options_raises_error() -> None:
    """Test Parser raises error when both config and options are provided."""
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    ParserConfig.model_rebuild(
        _types_namespace={
            "StrictTypes": StrictTypes,
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
        }
    )
    config = ParserConfig()
    with pytest.raises(ValueError, match="Cannot specify both 'config' and keyword arguments"):
        JsonSchemaParser(source="{}", config=config, field_constraints=True)


@pytest.mark.allow_direct_assert
def test_jsonschema_parser_with_explicit_target_datetime_class() -> None:
    """Test JsonSchemaParser with explicit target_datetime_class option."""
    from datamodel_code_generator.format import DatetimeClassType
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(source="{}", target_datetime_class=DatetimeClassType.Datetime)
    assert parser.data_type_manager.target_datetime_class == DatetimeClassType.Datetime


@pytest.mark.allow_direct_assert
def test_openapi_parser_with_explicit_wrap_string_literal() -> None:
    """Test OpenAPIParser with explicit wrap_string_literal option."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser(
        source='{"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0"}, "paths": {}}',
        wrap_string_literal=True,
    )
    assert parser.wrap_string_literal is True


@pytest.mark.allow_direct_assert
def test_graphql_parser_with_explicit_target_datetime_class() -> None:
    """Test GraphQLParser with explicit target_datetime_class option."""
    from datamodel_code_generator.format import DatetimeClassType
    from datamodel_code_generator.parser.graphql import GraphQLParser

    parser = GraphQLParser(source="type Query { id: ID }", target_datetime_class=DatetimeClassType.Awaredatetime)
    assert parser.data_type_manager.target_datetime_class == DatetimeClassType.Awaredatetime


@pytest.mark.allow_direct_assert
def test_jsonschema_parser_with_config_object() -> None:
    """Test JsonSchemaParser with ParserConfig object to cover config is not None branch."""
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.format import DatetimeClassType
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    ParserConfig.model_rebuild(
        _types_namespace={
            "StrictTypes": StrictTypes,
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
        }
    )
    config = ParserConfig(target_datetime_class=DatetimeClassType.Datetime)
    parser = JsonSchemaParser(source="{}", config=config)
    assert parser.data_type_manager.target_datetime_class == DatetimeClassType.Datetime


@pytest.mark.allow_direct_assert
def test_openapi_parser_with_config_object() -> None:
    """Test OpenAPIParser with OpenAPIParserConfig object to cover config is not None branch."""
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser.openapi import OpenAPIParser
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    OpenAPIParserConfig.model_rebuild(
        _types_namespace={
            "StrictTypes": StrictTypes,
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
        }
    )
    config = OpenAPIParserConfig(wrap_string_literal=True)
    parser = OpenAPIParser(
        source='{"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0"}, "paths": {}}',
        config=config,
    )
    assert parser.wrap_string_literal is True


@pytest.mark.allow_direct_assert
def test_graphql_parser_with_config_object() -> None:
    """Test GraphQLParser with GraphQLParserConfig object to cover config is not None branch."""
    from datamodel_code_generator.config import GraphQLParserConfig
    from datamodel_code_generator.format import DatetimeClassType
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser.graphql import GraphQLParser
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    GraphQLParserConfig.model_rebuild(
        _types_namespace={
            "StrictTypes": StrictTypes,
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
        }
    )
    config = GraphQLParserConfig(target_datetime_class=DatetimeClassType.Awaredatetime)
    parser = GraphQLParser(source="type Query { id: ID }", config=config)
    assert parser.data_type_manager.target_datetime_class == DatetimeClassType.Awaredatetime


def test_default_values_invalid_json(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --default-values with invalid JSON file returns error."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--default-values", str(DEFAULT_VALUES_DATA_PATH / "invalid_json.json")],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unable to load default values mapping",
        file_should_not_exist=output_file,
    )


def test_default_values_non_dict(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --default-values with non-dict JSON file returns error."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--default-values", str(DEFAULT_VALUES_DATA_PATH / "non_dict.json")],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unable to load default values mapping: must be a JSON object",
    )


def test_custom_formatters_kwargs_invalid(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --custom-formatters-kwargs with non-string values returns error."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--custom-formatters-kwargs",
            str(DEFAULT_VALUES_DATA_PATH / "invalid_formatters_kwargs.json"),
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unable to load custom_formatters_kwargs mapping: must be a JSON string mapping",
    )


def test_use_annotated_deprecation_warning_pydantic_v2(output_file: Path) -> None:
    """Test that deprecation warning is emitted for Pydantic v2 without --use-annotated."""
    with pytest.warns(DeprecationWarning, match=r"--use-annotated will be enabled by default for Pydantic v2"):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        )


def test_use_annotated_no_warning_with_flag(output_file: Path) -> None:
    """Test that no warning is emitted when --use-annotated is explicitly set."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"],
        )
    assert_warnings_do_not_contain(w, "--use-annotated will be enabled")


def test_use_annotated_no_warning_with_no_flag(output_file: Path) -> None:
    """Test that no warning is emitted when --no-use-annotated is explicitly set."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "simple_string.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--no-use-annotated"],
        )
    assert_warnings_do_not_contain(w, "--use-annotated will be enabled")


@pytest.mark.allow_direct_assert
def test_import_generate_config_from_top_level() -> None:
    """Test that GenerateConfig can be imported from top-level module."""
    from datamodel_code_generator import GenerateConfig as TopLevelGenerateConfig

    assert TopLevelGenerateConfig is not None
    assert TopLevelGenerateConfig is GenerateConfig


def test_generate_with_imported_config_from_top_level() -> None:
    """Test generate() with GenerateConfig imported from top-level."""
    config = datamodel_code_generator.GenerateConfig(class_name="TestModel", disable_timestamp=True)
    run_generate_and_assert(
        input_='{"type": "object"}',
        config=config,
        expected_file=EXPECTED_MAIN_PATH / "generate_with_imported_config_from_top_level.py",
    )


@pytest.mark.allow_direct_assert
def test_config_models_allow_internal_model_extra_options() -> None:
    """Test config models allow internal options with model_ prefixes."""
    from datamodel_code_generator.config import ParserConfig

    for config_model in (Config, GenerateConfig, ParserConfig):
        assert config_model.model_config["protected_namespaces"] == ()
        assert "model_extra_keys" in config_model.model_fields
        assert "model_extra_keys_without_x_prefix" in config_model.model_fields


@pytest.mark.allow_direct_assert
def test_all_exports_includes_generate_config() -> None:
    """Test that __all__ includes GenerateConfig."""
    assert "GenerateConfig" in datamodel_code_generator.__all__


@pytest.mark.parametrize(
    ("family", "target", "formatters"),
    [
        pytest.param(
            family,
            target,
            formatters,
            marks=pytest.mark.skipif(
                formatters == ["black", "isort"]
                and not datamodel_code_generator.format.is_supported_in_black(PythonVersion(f"3.{target[1:]}")),
                reason="Installed Black does not support this target",
            ),
        )
        for family in ("standard", "practical")
        for target in ("310", "311", "312", "313", "314")
        for formatters in ([], ["black", "isort"], ["ruff-check", "ruff-format"])
    ],
)
def test_builtin_preset_formatter_selection(
    family: str,
    target: str,
    formatters: list[str],
    output_file: Path,
) -> None:
    """New presets support builtin and explicit project formatter pipelines."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        run_main_with_args(
            [
                "--input",
                str(JSON_SCHEMA_DATA_PATH / "person.json"),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output_file),
                "--preset",
                f"{family}-py{target}-20260909",
                *(["--formatters", *formatters] if formatters else []),
            ],
            use_builtin_default_formatter=False,
        )
    assert_output(
        output_file.read_text(encoding="utf-8"),
        EXPECTED_MAIN_PATH / "formatter_policy" / f"{family}-{target}-{formatters[0] if formatters else 'builtin'}.py",
    )
    assert_output(
        "\n".join(str(item.message) for item in recorded if "Default formatters" in str(item.message)),
        EXPECTED_MAIN_PATH / "formatter_policy" / "no_warning.txt",
    )


@pytest.mark.parametrize("mode", ["implicit", "explicit", "old-preset", "new-preset", "disabled"])
@pytest.mark.parametrize("api", [False, True])
def test_formatter_policy_warning(mode: str, api: bool, output_file: Path) -> None:
    """Generation keeps its defaults and emits only the agreed short warning."""
    preset = {"old-preset": "standard-py310-20260826", "new-preset": "standard-py310-20260909"}.get(mode)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        if api:
            generate(
                JSON_SCHEMA_DATA_PATH / "person.json",
                input_file_type=InputFileType.JsonSchema,
                output=output_file,
                preset=preset,
                disable_timestamp=True,
                **(
                    {"formatters": [Formatter.BLACK, Formatter.ISORT]}
                    if mode == "explicit"
                    else {"formatters": []}
                    if mode == "disabled"
                    else {}
                ),
            )
        else:
            run_main_with_args(
                [
                    "--input",
                    str(JSON_SCHEMA_DATA_PATH / "person.json"),
                    "--input-file-type",
                    "jsonschema",
                    "--output",
                    str(output_file),
                    "--disable-timestamp",
                    *(["--preset", preset] if preset else []),
                    *(["--formatters", "black", "isort"] if mode == "explicit" else []),
                    *(["--disable-warnings"] if mode == "disabled" else []),
                ],
                use_builtin_default_formatter=False,
            )
    formatter_warnings = "\n".join(str(item.message) for item in recorded if "Default formatters" in str(item.message))
    assert_output(
        formatter_warnings,
        EXPECTED_MAIN_PATH
        / "formatter_policy"
        / ("warning.txt" if mode in {"implicit", "old-preset"} else "no_warning.txt"),
    )
    assert_output(
        output_file.read_text(encoding="utf-8"),
        EXPECTED_MAIN_PATH / "formatter_policy" / f"{mode}-{'api' if api else 'cli'}.py",
    )


@pytest.mark.parametrize("mode", ["builtin", "ruff", "black", "empty", "implicit"])
@pytest.mark.parametrize("override", [False, True])
def test_formatter_policy_pyproject(mode: str, override: bool, output_file: Path, tmp_path: Path) -> None:
    """Respect configured formatters, empty lists and CLI overrides without automatic Ruff selection."""
    shutil.copyfile(DATA_PATH / "config" / "formatter_policy" / f"{mode}.toml", tmp_path / "pyproject.toml")
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        run_main_with_args(
            [
                "--input",
                str(JSON_SCHEMA_DATA_PATH / "person.json"),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output_file),
                *(["--formatters", "ruff-check", "ruff-format"] if override else []),
            ],
            use_builtin_default_formatter=False,
        )
    assert_output(
        "\n".join(str(item.message) for item in recorded if "Default formatters" in str(item.message)),
        EXPECTED_MAIN_PATH
        / "formatter_policy"
        / ("warning.txt" if mode == "implicit" and not override else "no_warning.txt"),
    )
    assert_output(
        output_file.read_text(encoding="utf-8"),
        EXPECTED_MAIN_PATH / "formatter_policy" / f"pyproject-{mode}-{'override' if override else 'configured'}.py",
    )
