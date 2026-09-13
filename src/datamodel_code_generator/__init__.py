"""Main module for datamodel-code-generator.

Provides the main `generate()` function and related enums/exceptions for generating
Python data models (Pydantic, dataclasses, TypedDict, msgspec) from various schema formats.
"""

from __future__ import annotations

import contextlib
import gc
import os
import sys
import threading
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from functools import lru_cache as _lru_cache
from pathlib import Path
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    TypeAlias,
    TypeVar,
    cast,
)
from urllib.parse import ParseResult

from datamodel_code_generator._process_state import PROCESS_STATE_LOCK
from datamodel_code_generator._shared_types import (
    DefaultPutDict,
    LiteralType,
    _CollapseRootModelsRecursionError,
)
from datamodel_code_generator._source import (
    _clear_parser_source_data_cache as _clear_parser_source_data_cache,
)
from datamodel_code_generator._source import (
    _has_protobuf_declaration,
    _is_json_text,
    _is_protobuf_text,
    _is_xml_text,
    enable_parsed_source_cache,
    load_data,
    load_data_from_path,
    load_yaml,
    load_yaml_dict,
    load_yaml_dict_from_path,
)
from datamodel_code_generator._source import (
    _is_parsed_source_cache_enabled as _is_parsed_source_cache_enabled,
)
from datamodel_code_generator._source import (
    _load_parser_source_data_from_path_bytes as _load_parser_source_data_from_path_bytes,
)
from datamodel_code_generator._source import (
    _parser_source_data_cache as _parser_source_data_cache,
)
from datamodel_code_generator._source import (
    _read_parser_source_data_from_path as _read_parser_source_data_from_path,
)
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    MAX_VERSION,
    MIN_VERSION,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    AsyncAPIVersion,
    ClassNameAffixScope,
    CollapseRootModelsNameStrategy,
    CustomFileHeaderMode,
    DataclassArguments,
    DataModelType,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    GraphQLScope,
    HTTPBackend,
    InputFileType,
    InputModelRefStrategy,
    JsonSchemaVersion,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIScope,
    OpenAPIVersion,
    ProtobufVersion,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    TargetPydanticVersion,
    VersionMode,
    XMLSchemaVersion,
    _is_pydantic_version_at_least,
)

# Pydantic 2.5 cannot build schemas from stdlib TypeAliasType on Python 3.12.
if sys.version_info >= (3, 14):
    from typing import TypeAliasType
else:
    from typing_extensions import TypeAliasType

if sys.version_info >= (3, 11):
    from typing import Unpack
else:
    from typing_extensions import Unpack

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import (
        DEFAULT_FORMATTERS,
        DateClassType,
        DatetimeClassType,
        PythonVersion,
        PythonVersionMin,
    )
    from datamodel_code_generator._parser_context import ParserSourceContext
    from datamodel_code_generator._publication import PublicationAnchor
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._types import (
        AsyncAPIParserConfigDict,
        AvroParserConfigDict,
        GraphQLParserConfigDict,
        JSONSchemaParserConfigDict,
        OpenAPIParserConfigDict,
        ParserConfigDict,
        ProtobufParserConfigDict,
        XMLSchemaParserConfigDict,
    )
    from datamodel_code_generator._types.generate_config_dict import GenerateConfigDict
    from datamodel_code_generator.config import (
        GenerateConfig,
        ParserConfig,
    )
    from datamodel_code_generator.model import DataModelSet
    from datamodel_code_generator.model_metadata import ModelMetadata
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

T = TypeVar("T")
ParserConfigT = TypeVar("ParserConfigT", bound="ParserConfig")

# Preserve the historical private facade identity for repr and pickling compatibility.
_CollapseRootModelsRecursionError.__module__ = __name__

YamlScalar: TypeAlias = str | int | float | bool | None
YamlValue = TypeAliasType("YamlValue", "dict[str, YamlValue] | list[YamlValue] | YamlScalar")


# Measured on 2026-09-02: threshold0=100_000 made warm runs 4% to 11% faster
# without increasing tracemalloc peaks. Higher thresholds or gc.disable() raised peaks by 17%.
_GC_YOUNG_THRESHOLD: int = 100_000
_gc_tuning_lock = threading.Lock()
# This one-slot holder keeps the process-wide scope count mutable without allocating state per call.
_gc_tuning_depth: list[int] = [0]
_gc_saved_threshold: tuple[int, int, int]


@contextlib.contextmanager
def _tuned_gc() -> Iterator[None]:
    """Raise the young-generation GC threshold while one generation run is in progress.

    Process-global by nature: only the outermost concurrent caller changes and restores the
    threshold, nested or parallel calls are counted, and a host that disabled GC is left alone.
    """
    global _gc_saved_threshold  # noqa: PLW0603

    with _gc_tuning_lock:
        match _gc_tuning_depth[0]:
            case 0 if gc.isenabled():
                _gc_saved_threshold = gc.get_threshold()
                gc.set_threshold(_GC_YOUNG_THRESHOLD, *_gc_saved_threshold[1:])
                _gc_tuning_depth[0] = 1
            case 0:
                # Negative depth records scopes entered while the host disabled GC.
                _gc_tuning_depth[0] = -1
            case depth:
                _gc_tuning_depth[0] = depth + (1 if depth > 0 else -1)
    try:
        yield
    finally:
        with _gc_tuning_lock:
            match _gc_tuning_depth[0]:
                case 1:
                    _gc_tuning_depth[0] = 0
                    gc.set_threshold(*_gc_saved_threshold)
                case depth:
                    _gc_tuning_depth[0] = depth - (1 if depth > 0 else -1)


for _public_source_export in (
    enable_parsed_source_cache,
    load_data,
    load_data_from_path,
    load_yaml,
    load_yaml_dict,
    load_yaml_dict_from_path,
):
    _public_source_export.__module__ = __name__
del _public_source_export

if TYPE_CHECKING:
    _SchemaVersion = TypeVar(
        "_SchemaVersion",
        JsonSchemaVersion,
        OpenAPIVersion,
        AsyncAPIVersion,
        XMLSchemaVersion,
        ProtobufVersion,
    )
    _GenerationInput: TypeAlias = Path | str | ParseResult | Mapping[str, Any] | list[Any]
    _ParserResults: TypeAlias = str | dict[tuple[str, ...], Result]
    _ParserSource: TypeAlias = str | Path | list[Path] | ParseResult | dict[str, Any]
    _PythonTypeExpressions: TypeAlias = Mapping[str, PythonTypeExpr]
    _StagedArtifact: TypeAlias = tuple[Path, Path, PublicationAnchor, Path, Path]
    _PreparedGenerationInput: TypeAlias = tuple[
        GenerateConfig,
        _GenerationInput,
        str | None,
        InputFileType,
        DataclassArguments,
        Mapping[str, Any] | None,
        Path | None,
        tuple[Path, bytes] | None,
        bool,
        RemoteReferenceLock | None,
    ]
    _PreparedParser: TypeAlias = tuple[
        DataModelSet,
        _ParserSource,
        bool,
        ParserConfigDict,
        _PythonTypeExpressions | None,
    ]
    _ParsedGeneration: TypeAlias = tuple[_ParserResults, ModelMetadata | None, DataModelSet, bool]


GeneratedModules: TypeAlias = dict[tuple[str, ...], str]
"""Type alias for multiple generated modules.

Maps module path tuples (e.g., ("models", "user.py")) to generated code strings.
Returned by generate() when output=None and multiple modules are generated.
"""


def _apply_generate_config_preset(config: GenerateConfig) -> GenerateConfig:
    """Return a generate config with preset defaults applied."""
    preset_name = config.preset
    if preset_name is None:
        return config

    from datamodel_code_generator.preset import (  # noqa: PLC0415
        PresetConfigValue,
        PresetContext,
        PresetError,
        resolve_preset_config_updates,
    )

    try:
        preset_config = resolve_preset_config_updates(
            preset_name,
            context=PresetContext(
                input_file_type=config.input_file_type,
                output_model_type=config.output_model_type,
                target_python_version=config.target_python_version,
            ),
            use_annotated=config.use_annotated,
            explicit_fields=config.model_fields_set,
        )
    except PresetError as e:
        raise Error(str(e)) from e

    if preset_config.target_python_version is not None:
        config = config.model_copy(update={"target_python_version": preset_config.target_python_version})

    updates: dict[str, PresetConfigValue] = {}
    for item in preset_config.items:
        updates[item.field_name] = item.applied_value

    if updates:
        config = config.model_copy(update=updates)
    if preset_config.force_field_constraints:
        return config.model_copy(update={"field_constraints": True})
    return config


DEFAULT_BASE_CLASS: str = "pydantic.BaseModel"


@_lru_cache(maxsize=256)
def cached_path_exists(path: Path) -> bool:
    """Check if a path exists with LRU caching.

    Caches the result of Path.exists() to reduce filesystem I/O
    when checking the same path multiple times (e.g., custom template directories).

    Note: This cache is safe for CLI usage where files don't change during execution.
    """
    return path.exists()


def get_version() -> str:
    """Return the version embedded by the build backend."""
    from datamodel_code_generator._version import __version__  # noqa: PLC0415

    return __version__


def enable_debug_message() -> None:  # pragma: no cover
    """Enable debug tracing with pysnooper."""
    global _pysnooper_default_state_set  # noqa: PLW0603
    try:
        import pysnooper  # noqa: PLC0415

        pysnooper.tracer.DISABLED = False
        _pysnooper_default_state_set = True
    except ImportError as err:
        msg = "Please run `$pip install 'datamodel-code-generator[debug]'` to use debug option"
        raise Exception(msg) from err  # noqa: TRY002


DEFAULT_MAX_VARIABLE_LENGTH: int = 100


_pysnooper_default_state_set: bool = False


def snooper_to_methods() -> Callable[..., Any]:
    """Class decorator to add pysnooper tracing to all methods."""

    def inner(cls: type[T]) -> type[T]:
        global _pysnooper_default_state_set  # noqa: PLW0603
        try:
            import pysnooper  # noqa: PLC0415
        except ImportError:
            return cls

        # Ensure tracing is disabled by default (only enabled via --debug flag)
        if not _pysnooper_default_state_set:
            pysnooper.tracer.DISABLED = True
            _pysnooper_default_state_set = True

        import inspect  # noqa: PLC0415

        methods = inspect.getmembers(cls, predicate=inspect.isfunction)
        for name, method in methods:
            snooper_method = pysnooper.snoop(max_variable_length=DEFAULT_MAX_VARIABLE_LENGTH)(method)
            setattr(cls, name, snooper_method)
        return cls

    return inner


@contextlib.contextmanager
def chdir(path: Path | None) -> Iterator[None]:
    """Change working directory and return to previous on exit."""
    if path is None:
        yield
        return
    with PROCESS_STATE_LOCK:
        prev_cwd = Path.cwd()
        try:
            os.chdir(path if path.is_dir() else path.parent)
            yield
        finally:
            os.chdir(prev_cwd)


def _absolute_generation_path(path: Path | None, base_path: Path) -> Path | None:
    """Resolve a relative generation path against an explicit base."""
    if path is None or path.is_absolute():
        return path
    return base_path / path


def _path_list_base_path(input_paths: list[Path], caller_cwd: Path) -> Path:
    """Choose a list-input base without changing caller-relative module paths."""
    if all(path.is_relative_to(caller_cwd) for path in input_paths):
        return caller_cwd
    return Path(os.path.commonpath(path.parent for path in input_paths))


def _settings_path_from(base_path: Path, settings_path: Path | None) -> Path:
    """Resolve formatter settings from one explicit context root."""
    if settings_path is None:
        return base_path
    if settings_path.is_absolute():
        return settings_path
    return base_path / settings_path


def _output_context_path(output: Path | None, caller_cwd: Path) -> Path:
    """Return the directory observed by legacy output-context consumers."""
    if output is None:
        return caller_cwd
    return (output if output.is_dir() else output.parent).resolve()


def _uses_legacy_process_state(config: GenerateConfig) -> bool:
    """Return whether generation may observe the process working directory."""
    return bool(
        config.custom_template_dir or getattr(config, "custom_class_name_generator", None) or config.custom_formatters
    )


def is_openapi(data: Mapping[str, Any]) -> bool:
    """Check if the data dict is an OpenAPI specification."""
    return "openapi" in data


def is_asyncapi(data: Mapping[str, Any]) -> bool:
    """Check if the data dict is an AsyncAPI specification."""
    return "asyncapi" in data


JSON_SCHEMA_URLS: tuple[str, ...] = (
    "http://json-schema.org/",
    "https://json-schema.org/",
)


def is_schema(data: dict) -> bool:
    """Check if the data dict is a JSON Schema."""
    schema = data.get("$schema")
    if isinstance(schema, str) and any(schema.startswith(u) for u in JSON_SCHEMA_URLS):  # pragma: no cover
        return True
    if isinstance(data.get("type"), str):
        return True
    if any(
        isinstance(data.get(o), list)
        for o in (
            "allOf",
            "anyOf",
            "oneOf",
        )
    ):
        return True
    return isinstance(data.get("properties"), dict)


RAW_DATA_TYPES: list[InputFileType] = [
    InputFileType.Json,
    InputFileType.Yaml,
    InputFileType.Dict,
    InputFileType.CSV,
    InputFileType.GraphQL,
]


class Error(Exception):
    """Base exception for datamodel-code-generator errors."""

    def __init__(self, message: str) -> None:
        """Initialize with message."""
        self.message: str = message

    def __str__(self) -> str:
        """Return string representation."""
        return self.message


def _normalized_absolute_path(path: Path, *, resolve_aliases: bool = False) -> Path:
    """Return a normalized absolute path, resolving aliases only when needed."""
    expanded_path = path.expanduser()
    if resolve_aliases:
        return expanded_path.resolve(strict=False)
    return Path(os.path.abspath(expanded_path))  # noqa: PTH100


def _validate_generation_path_conflicts(  # noqa: PLR0912
    input_: _GenerationInput,
    output: Path | None,
    model_metadata: Path | None,
    lockfile: Path | None = None,
) -> None:
    if output is None and model_metadata is None and lockfile is None:
        return

    targets = [
        (
            label,
            path,
            _normalized_absolute_path(path),
            _normalized_absolute_path(path, resolve_aliases=True),
        )
        for label, path in (
            ("Output", output),
            ("Model metadata", model_metadata),
            ("Remote lock", lockfile),
        )
        if path is not None
    ]

    for target_index, (label, _, absolute_path, resolved_path) in enumerate(targets):
        for other_label, _, other_absolute_path, resolved_other_path in targets[target_index + 1 :]:
            same_path = absolute_path == other_absolute_path
            if not same_path:
                same_path = resolved_path == resolved_other_path or (
                    absolute_path.exists()
                    and other_absolute_path.exists()
                    and absolute_path.samefile(other_absolute_path)
                )
            if same_path:
                if (label, other_label) == ("Output", "Model metadata"):
                    msg = f"Output and model metadata paths must be different: {absolute_path}"
                else:
                    msg = f"{label} and {other_label} paths must be different: {absolute_path}"
                raise Error(msg)
            if "Remote lock" not in {label, other_label}:
                continue
            paths_overlap = (
                absolute_path.is_relative_to(other_absolute_path)
                or other_absolute_path.is_relative_to(absolute_path)
                or resolved_path.is_relative_to(resolved_other_path)
                or resolved_other_path.is_relative_to(resolved_path)
            )
            if paths_overlap:
                msg = f"{label} and {other_label} paths must not overlap: {absolute_path}"
                raise Error(msg)

    match input_:
        case Path() as input_path:
            input_paths = (input_path,)
        case [Path(), *_] as input_paths:
            pass
        case _:
            return

    for input_path in input_paths:
        absolute_input = _normalized_absolute_path(input_path)
        resolved_input = _normalized_absolute_path(input_path, resolve_aliases=True)
        input_is_directory = absolute_input.is_dir() or resolved_input.is_dir()
        for label, _, target, resolved_target in targets:
            if input_is_directory:
                if label == "Remote lock" and (
                    target.is_relative_to(absolute_input) or resolved_target.is_relative_to(resolved_input)
                ):
                    msg = f"{label} path must not be inside an input directory: {target}"
                    raise Error(msg)
                continue
            target_exists = target.exists()
            if target == absolute_input and input_path.exists():
                msg = f"{label} path must not overwrite an input path: {target}"
                raise Error(msg)
            if target_exists and target.samefile(input_path):
                msg = f"{label} path must not overwrite an input path: {target}"
                raise Error(msg)


class DanglingRefWarning(UserWarning):
    """Warn that a local JSON pointer target was not found."""


class DefaultValueTypeWarning(UserWarning):
    """Warn that a generated default is still serialized instead of its runtime type."""


class InvalidClassNameError(Error):
    """Raised when a schema title cannot be converted to a valid Python class name."""

    def __init__(self, class_name: str) -> None:
        """Initialize with class name."""
        self.class_name = class_name
        message = f"title={class_name!r} is invalid class name."
        super().__init__(message=message)


def _validate_output_datetime_class(
    output_model_type: DataModelType, output_datetime_class: DatetimeClassType | None
) -> None:
    if output_datetime_class is None:
        return

    from datamodel_code_generator._format_types import DatetimeClassType  # noqa: PLC0415

    if output_datetime_class is DatetimeClassType.Datetime:
        return
    if output_model_type in {DataModelType.DataclassesDataclass, DataModelType.TypingTypedDict}:
        msg = f'`--output-datetime-class` only allows "datetime" for `--output-model-type` {output_model_type.value}'
        raise Error(msg)


def _validate_alias_generator(output_model_type: DataModelType, alias_generator: AliasGenerator | None) -> None:
    if alias_generator is None:
        return
    if output_model_type is DataModelType.PydanticV2BaseModel:
        return
    msg = "`--alias-generator` is only supported for `--output-model-type pydantic_v2.BaseModel`"
    raise Error(msg)


def _apply_missing_sentinel_config(config: GenerateConfig) -> GenerateConfig:
    if not config.use_missing_sentinel:
        return config

    if config.output_model_type is not DataModelType.PydanticV2BaseModel:
        msg = "`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel`"
        raise Error(msg)

    match target_version := config.target_pydantic_version:
        case None:
            return config.model_copy(update={"target_pydantic_version": TargetPydanticVersion.V2_12})
        case _ if _is_pydantic_version_at_least(target_version, TargetPydanticVersion.V2_12):
            return config
        case _:
            target_version_value = (
                target_version.value if isinstance(target_version, TargetPydanticVersion) else target_version
            )
            msg = (
                "`--use-missing-sentinel` requires "
                f"`--target-pydantic-version {TargetPydanticVersion.V2_12.value}` or later; "
                f"got {target_version_value!r}"
            )
            raise Error(msg)
    raise AssertionError  # pragma: no cover


class InvalidFileFormatError(Error):
    """Raised when the input file format is invalid or cannot be parsed."""

    def __init__(
        self,
        original_error: Exception,
        input_file_type: InputFileType | None = None,
        *,
        source: str | Path | None = None,
    ) -> None:
        """Initialize with original error, input file type, and source context."""
        self.original_error = original_error
        self.input_file_type = input_file_type
        self.source = source
        error_detail = f"{type(original_error).__name__}: {original_error}"
        source_detail = f" at {source}" if source is not None else ""
        if input_file_type is not None:
            message = f"Invalid file format for {input_file_type.value}{source_detail}: {error_detail}"
        else:
            message = f"Invalid file format{source_detail}: {error_detail}"
        super().__init__(message=message)


class SchemaParseError(Error):
    """Raised when an error occurs during schema parsing with path context."""

    def __init__(
        self,
        message: str,
        path: list[str] | None = None,
        original_error: Exception | None = None,
    ) -> None:
        """Initialize with message, schema path, and optional original error."""
        self.path = path or []
        self.original_error = original_error
        full_message = self._format_message(message)
        super().__init__(message=full_message)

    def _format_message(self, message: str) -> str:
        """Format message with schema path context."""
        if self.path:
            path_str = "/".join(self.path)
            return f"Error at schema path '{path_str}': {message}"
        return message


class SchemaFetchError(Error):
    """Raised when fetching a remote schema fails (HTTP error, unexpected content type)."""


_COMMENT_ONLY_HEADER_FAST_PATH_LIMIT = 4096


def get_first_file(path: Path) -> Path:  # pragma: no cover
    """Find and return the first file in a path (file or directory)."""
    if path.is_file():
        return path
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_file():
                return child
    msg = f"No file found in: {path}"
    raise FileNotFoundError(msg)


def _find_future_import_insertion_point(header: str) -> int:  # noqa: PLR0911, PLR0912, PLR0915
    """Find the future-import position without crossing into target-version syntax.

    ``generate_tokens`` uses the running Python tokenizer; it is not a parser for
    the requested target Python version. Scan only the leading header boundary
    and never consume later statements, which may use newer target-only syntax.

    The bounded fast path recognizes only physical blank and comment lines. It
    must stay target-syntax agnostic; all statement-shaped input belongs to the
    conservative tokenizer path below.
    """
    header_size = len(header)
    if header_size <= _COMMENT_ONLY_HEADER_FAST_PATH_LIMIT:
        first_content = 0
        while first_content < header_size and header[first_content] in " \t\f\r\n":
            first_content += 1
        if first_content == header_size:
            return header_size
        # Keep the speculative scan bounded: a comment-prefixed code header
        # must not pay an unbounded second pass before runtime tokenization.
        # Do not use splitlines(): its extra Unicode boundaries are not the
        # physical CR/LF boundaries normalized by the tokenizer adapter.
        if header[first_content] == "#" and all(
            not (content := line.lstrip(" \t\f")) or content.startswith("#")
            for line in header.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ):
            return header_size

    import io  # noqa: PLC0415
    import tokenize  # noqa: PLC0415

    line_end_positions = [0]

    def line_end_pos(line_num: int) -> int:
        return line_end_positions[line_num]

    def is_docstring_token(token: tokenize.TokenInfo) -> bool:
        quote_index = min((index for quote in "\"'" if (index := token.string.find(quote)) >= 0), default=0)
        return token.string[:quote_index].lower() in {"", "r", "u"}

    statement_start_line: int | None = None
    statement_end_line = 0
    parenthesis_depth = 0
    has_docstring = False
    trailing_semicolon_end: tuple[int, int] | None = None
    with io.StringIO(header, newline="") as source:

        def readline() -> str:
            line = source.readline()
            if not line:
                return ""
            line_end_positions.append(line_end_positions[-1] + len(line))
            if line.endswith("\r\n"):
                return f"{line[:-2]}\n"
            if line.endswith("\r"):
                return f"{line[:-1]}\n"
            return line

        try:
            for token in tokenize.generate_tokens(readline):  # pragma: no branch
                match token.type:
                    case tokenize.COMMENT | tokenize.NL:
                        continue
                    case _ if trailing_semicolon_end and token.type not in {tokenize.NEWLINE, tokenize.ENDMARKER}:
                        semicolon_line, semicolon_column = trailing_semicolon_end
                        if token.start[0] == semicolon_line:
                            return line_end_pos(semicolon_line - 1) + semicolon_column
                        return 0
                    case tokenize.STRING:
                        statement_start_line = statement_start_line or token.start[0]
                        if not is_docstring_token(token):
                            return line_end_pos(statement_start_line - 1)
                        has_docstring = True
                        statement_end_line = token.end[0]
                    case tokenize.OP if token.string == "(":
                        statement_start_line = statement_start_line or token.start[0]
                        if has_docstring:
                            return line_end_pos(statement_start_line - 1)
                        parenthesis_depth += 1
                    case tokenize.OP if token.string == ")" and parenthesis_depth:
                        parenthesis_depth -= 1
                        statement_end_line = token.end[0]
                    case tokenize.OP if token.string == ";" and has_docstring and not parenthesis_depth:
                        trailing_semicolon_end = token.end
                        statement_end_line = token.end[0]
                    case tokenize.NEWLINE | tokenize.ENDMARKER:
                        if statement_start_line is None:
                            if token.type == tokenize.ENDMARKER:
                                return len(header)
                            continue  # pragma: no cover  # Blank physical lines tokenize as NL.
                        if has_docstring and not parenthesis_depth:
                            statement_end_line = max(statement_end_line, token.start[0])
                            # This is the header boundary. Do not request another token:
                            # later statements may use syntax only the target runtime accepts.
                            break
                        return line_end_pos(statement_start_line - 1)
                    case _:
                        statement_start_line = statement_start_line or token.start[0]
                        return line_end_pos(statement_start_line - 1)
        except (SyntaxError, tokenize.TokenError):
            return 0

    pos = line_end_pos(statement_end_line)
    while pos < len(header):
        cr_index = header.find("\r", pos)
        lf_index = header.find("\n", pos)
        if cr_index < 0:
            line_break = lf_index
        elif lf_index < 0:
            line_break = cr_index
        else:
            line_break = min(cr_index, lf_index)
        content_end = len(header) if line_break < 0 else line_break
        if header[pos:content_end].strip():
            break
        if line_break < 0:
            return len(header)
        pos = line_break + (2 if header.startswith("\r\n", line_break) else 1)
    return pos


def _format_file_header(
    header_prefix: str,
    header_suffix: str | None,
    filename: str | None,
) -> str:
    """Format a per-file header, skipping all work for replace mode."""
    if header_suffix is None:
        return header_prefix
    safe_filename = filename.replace("\n", " ").replace("\r", " ") if filename else ""
    return f"{header_prefix}{safe_filename}{header_suffix}"


def _build_file_header_parts(custom_file_header: str | None, config: GenerateConfig) -> tuple[str, str | None]:
    """Build shared file-header parts, using a None suffix for replace mode."""
    match config.custom_file_header_mode:
        case CustomFileHeaderMode.Replace if custom_file_header:
            return custom_file_header, None

    generated_marker = "@generated" if config.enable_generated_header_marker else "generated"
    header_prefix = f"""\
# {generated_marker} by datamodel-codegen:
#   filename:  """
    header_suffix = ""
    if not config.disable_timestamp:
        timestamp = config._generation_timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat()  # noqa: SLF001
        header_suffix += f"\n#   timestamp: {timestamp}"
    if config.enable_version_header:
        header_suffix += f"\n#   version:   {get_version()}"
    if config.enable_command_header and config.command_line:
        safe_command_line = config.command_line.replace("\n", " ").replace("\r", " ")
        header_suffix += f"\n#   command:   {safe_command_line}"
    if custom_file_header:
        custom_header = custom_file_header.rstrip("\r\n")
        header_prefix = f"{custom_header}\n#\n{header_prefix}"
    return header_prefix, header_suffix


def _extract_leading_future_imports(body: str, future_imports: str) -> tuple[str, str]:
    """Extract generated future imports after leading comments or a module docstring."""
    future_start = 0
    while (
        not body.startswith("from __future__ import ", future_start)
        and (line_end := body.find("\n", future_start)) >= 0
    ):
        if (leading_line := body[future_start:line_end].lstrip()) and not leading_line.startswith("#"):
            future_start = _find_future_import_insertion_point(body)
            break
        future_start = line_end + 1
    if not body.startswith("from __future__ import ", future_start):
        return body, future_imports

    future_end = future_start
    while body.startswith("from __future__ import ", future_end):
        future_end = body.find("\n", future_end) + 1 or len(body)
    if not future_imports:
        future_imports = body[future_start:future_end].rstrip()
    body_without_future = (f"{body[:future_start]}{body[future_end:]}" if future_start else body[future_end:]).lstrip(
        "\n"
    )
    return body_without_future, future_imports


def _build_module_content(
    body: str,
    header: str,
    *,
    has_custom_file_header: bool,
    future_imports: str = "",
) -> str:
    """Build module content by combining header and body.

    Handles future imports extraction and placement when custom_file_header is provided.
    """
    if not body:
        return header
    if not has_custom_file_header:
        return f"{header}\n\n{body.rstrip()}"

    # Custom formatters may add comments or a module docstring before the import.
    body_without_future, extracted_future = _extract_leading_future_imports(body, future_imports)

    if not extracted_future:
        return f"{header}\n\n{body.rstrip()}"

    return f"{_build_header_with_future_imports(header, extracted_future)}\n\n{body_without_future.rstrip()}"


def _build_header_with_future_imports(header: str, future_imports: str) -> str:
    """Insert future imports at the same boundary when rendering or recognizing output."""
    insertion_point = _find_future_import_insertion_point(header)
    header_before = header[:insertion_point].rstrip()
    header_after = header[insertion_point:].strip()
    if header_after:
        prefix = f"{header_before}\n" if header_before else ""
        return prefix + future_imports + "\n\n" + header_after
    prefix = f"{header_before}\n\n" if header_before else ""
    return prefix + future_imports


@_lru_cache(maxsize=1)
def _get_internal_parser_config_model() -> type[Any]:
    """Return the historical permissive parser config for private callers."""
    from pydantic import BaseModel, ConfigDict  # noqa: PLC0415

    class _InternalParserConfig(BaseModel):
        model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    return _InternalParserConfig


_INTERNAL_PARSER_CONFIG_DEFAULTS: dict[str, Any] = {
    "allow_responses_without_content": False,
    "apply_default_values_for_required_fields": False,
    "base_path": None,
    "custom_class_name_generator": None,
    "default_field_extras": None,
    "default_value_overrides": None,
    "defer_formatting": False,
    "dump_resolve_reference_action": None,
    "force_optional_for_required_fields": False,
    "known_third_party": None,
    "remote_text_cache": None,
    "repair_invalid_dotted_stdout": False,
    "forced_invalid_dotted_stdout_repair_modules": (),
    "target_date_class": None,
    "target_datetime_class": None,
}


def _generate_config_values(generate_config: GenerateConfig) -> dict[str, Any]:
    values = vars(generate_config).copy()
    if not (fields := getattr(type(generate_config), "model_fields", None)):
        return values

    values.update({
        field_name: getattr(generate_config, field_name) for field_name in fields if field_name not in values
    })
    if (remote_lock := generate_config.remote_lock) is not None:
        values["remote_lock"] = remote_lock
    return values


def _warn_if_input_string_points_to_existing_path(
    input_: _GenerationInput,
) -> None:
    match input_:
        case str() as input_text if input_text and "\n" not in input_text and "\r" not in input_text:
            pass
        case _:
            return
    try:
        path = Path(input_text).expanduser()
        path_exists = path.exists()
    except (OSError, RuntimeError, ValueError):
        return
    if not path_exists:
        return

    import warnings  # noqa: PLC0415

    with contextlib.suppress(Warning):
        warnings.warn(
            "`input_` strings are treated as schema text. "
            "The value also resolves to an existing path; pass a `Path` object to read it as a file.",
            stacklevel=3,
        )


@contextlib.contextmanager
def _warn_on_input_string_path_failure(
    input_: _GenerationInput,
) -> Iterator[None]:
    try:
        yield
    except Exception:
        _warn_if_input_string_points_to_existing_path(input_)
        raise


def _create_parser_source_context(
    values: Mapping[str, Any],
    generate_config: GenerateConfig,
) -> ParserSourceContext:
    """Build one immutable source policy from validated generation options."""
    from datamodel_code_generator._parser_context import ParserSourceContext  # noqa: PLC0415

    remote_lock = generate_config.remote_lock
    response_observer = remote_lock.record_response if remote_lock is not None else None
    return ParserSourceContext(
        base_path=values.get("base_path"),
        encoding=values.get("encoding", "utf-8"),
        directory_input_filter=generate_config._directory_input_filter,  # noqa: SLF001
        remote_text_cache=values.get("remote_text_cache"),
        allow_remote_refs=values.get("allow_remote_refs"),
        strict_refs=values.get("strict_refs", False),
        allow_private_network=values.get("allow_private_network", False),
        http_backend=values.get("http_backend", HTTPBackend.AUTO),
        http_headers=values.get("http_headers"),
        http_local_ref_path=values.get("http_local_ref_path"),
        http_ignore_tls=values.get("http_ignore_tls", False),
        http_query_parameters=values.get("http_query_parameters"),
        http_timeout=values.get("http_timeout"),
        external_ref_mapping=values.get("external_ref_mapping"),
        remote_response_observer=response_observer,
    )


def _create_parser_config(
    generate_config: GenerateConfig,
    additional_options: ParserConfigDict,
) -> Any:
    """Create the lightweight config for already-validated generation options."""
    values = {**_INTERNAL_PARSER_CONFIG_DEFAULTS, **_generate_config_values(generate_config)}
    values.update(dict(additional_options))
    parser_config = _get_internal_parser_config_model().model_construct(**values)
    return _configure_parser_config_context(parser_config, values, generate_config)


def _configure_parser_config_context(
    parser_config: ParserConfigT,
    values: Mapping[str, Any],
    generate_config: GenerateConfig,
) -> ParserConfigT:
    """Attach neutral source and retry policies without importing typed config models."""
    parser_config._source_context = _create_parser_source_context(values, generate_config)  # noqa: SLF001
    if type(parser_config) is _get_internal_parser_config_model():
        return parser_config
    parser_config._repair_invalid_dotted_stdout = values.get("repair_invalid_dotted_stdout", False)  # noqa: SLF001
    parser_config._forced_invalid_dotted_stdout_repair_modules = values.get(  # noqa: SLF001
        "forced_invalid_dotted_stdout_repair_modules", ()
    )
    return parser_config


def _create_typed_parser_config(
    generate_config: GenerateConfig,
    parser_config_type: type[ParserConfigT],
    additional_options: ParserConfigDict,
) -> ParserConfigT:
    """Create a parser config from GenerateConfig with additional options.

    ``generate_config`` is already validated by the CLI or public generate()
    entrypoint. Public Parser(..., **options) still validates separately.

    Only fields declared by the selected parser config are copied.  Generation
    and CLI-only options remain owned by ``GenerateConfig`` instead of leaking
    into an ``extra=allow`` parser bag.
    """
    values = {**_INTERNAL_PARSER_CONFIG_DEFAULTS, **_generate_config_values(generate_config)}
    values.update(dict(additional_options))
    # ``extra="forbid"`` makes model_construct ignore non-parser generation
    # fields without allocating a second filtered dictionary.
    parser_config = parser_config_type.model_construct(**values)
    return _configure_parser_config_context(parser_config, values, generate_config)


_SchemaVersions: TypeAlias = tuple[
    JsonSchemaVersion | None,
    OpenAPIVersion | None,
    AsyncAPIVersion | None,
    XMLSchemaVersion | None,
    ProtobufVersion | None,
]


def _parse_schema_version(enum_type: type[_SchemaVersion], schema_version: str, label: str) -> _SchemaVersion:
    try:
        return enum_type(schema_version)
    except ValueError:
        valid = [v.value for v in enum_type]
        msg = f"Invalid {label} version: {schema_version}. Valid values: {valid}"
        raise Error(msg) from None


def _resolve_schema_versions(input_file_type: InputFileType, schema_version: str | None) -> _SchemaVersions:
    if not schema_version or schema_version == "auto":
        return None, None, None, None, None

    match input_file_type:
        case InputFileType.Avro:
            msg = "--schema-version is not supported for avro because Avro schemas do not carry a version marker"
            raise Error(msg)
        case InputFileType.GraphQL:
            msg = f"--schema-version is not supported for {input_file_type.value}"
            raise Error(msg)
        case InputFileType.OpenAPI:
            return None, _parse_schema_version(OpenAPIVersion, schema_version, "OpenAPI"), None, None, None
        case InputFileType.AsyncAPI:
            return None, None, _parse_schema_version(AsyncAPIVersion, schema_version, "AsyncAPI"), None, None
        case InputFileType.XMLSchema:
            return None, None, None, _parse_schema_version(XMLSchemaVersion, schema_version, "XML Schema"), None
        case InputFileType.Protobuf:
            return None, None, None, None, _parse_schema_version(ProtobufVersion, schema_version, "Protobuf")
        case _:
            return _parse_schema_version(JsonSchemaVersion, schema_version, "JSON Schema"), None, None, None, None


def _openapi_shared_options(config: GenerateConfig) -> OpenAPIParserConfigDict:
    return {
        "openapi_scopes": config.openapi_scopes,
        "include_path_parameters": config.include_path_parameters,
        "use_status_code_in_response_name": config.use_status_code_in_response_name,
        "openapi_include_paths": config.openapi_include_paths,
        "openapi_include_info_version": config.openapi_include_info_version,
    }


def _normalize_raw_input(  # noqa: PLR0912, PLR0915
    input_: _GenerationInput,
    input_text: str | None,
    input_file_type: InputFileType,
    config: GenerateConfig,
) -> str | None:
    if input_file_type not in RAW_DATA_TYPES or input_file_type == InputFileType.GraphQL:
        return input_text

    import json  # noqa: PLC0415

    try:
        if isinstance(input_, Path) and input_.is_dir():  # pragma: no cover
            msg = f"Input must be a file for {input_file_type}"
            raise Error(msg)  # noqa: TRY301
        obj: Any
        if input_file_type == InputFileType.CSV:
            import csv  # noqa: PLC0415

            def get_header_and_first_line(csv_file: IO[str]) -> dict[str, Any]:
                csv_reader = csv.DictReader(csv_file)
                if csv_reader.fieldnames is None:
                    msg = "CSV file has no header row"
                    raise ValueError(msg)  # noqa: TRY301
                try:
                    first_row = next(csv_reader)
                except StopIteration:
                    msg = "CSV file has no data rows"
                    raise ValueError(msg) from None
                return {key: value for key, value in first_row.items() if key is not None}

            if isinstance(input_, Path):
                with input_.open(encoding=config.encoding) as f:
                    obj = get_header_and_first_line(f)
            else:
                import io  # noqa: PLC0415

                obj = get_header_and_first_line(io.StringIO(input_text))
        elif input_file_type == InputFileType.Yaml:
            if isinstance(input_, Path):
                obj = load_yaml(input_.read_text(encoding=config.encoding))
            else:  # pragma: no cover
                assert input_text is not None
                obj = load_yaml(input_text)
        elif input_file_type == InputFileType.Json:
            if isinstance(input_, Path):
                obj = json.loads(input_.read_text(encoding=config.encoding))
            else:
                assert input_text is not None
                obj = json.loads(input_text)
        elif input_file_type == InputFileType.Dict:
            import ast  # noqa: PLC0415

            if isinstance(input_, Path):
                obj = ast.literal_eval(input_.read_text(encoding=config.encoding))
            elif isinstance(input_, Mapping):
                obj = input_
            else:
                assert input_text is not None
                obj = ast.literal_eval(input_text)
        else:  # pragma: no cover
            msg = f"Unsupported input file type: {input_file_type}"
            raise Error(msg)  # noqa: TRY301

        from genson import SchemaBuilder  # noqa: PLC0415

        builder = SchemaBuilder()
        builder.add_object(obj)
        return json.dumps(builder.to_schema())
    except Error:
        raise
    except Exception as exc:
        raise InvalidFileFormatError(exc, input_file_type) from exc


def _convert_mcp_tools(
    input_: _GenerationInput,
    input_text: str | None,
    config: GenerateConfig,
    remote_text_cache: DefaultPutDict[str, str],
) -> tuple[Mapping[str, Any] | None, InputFileType, bool]:
    import json  # noqa: PLC0415

    from datamodel_code_generator.parser.mcp import convert_mcp_tools_to_jsonschema  # noqa: PLC0415

    def load_mcp_tools_text(text: str) -> Any:
        if _is_json_text(text):
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(text)
        return load_yaml(text)

    def load_mcp_tools_data() -> Any:
        match input_:
            case Mapping() | list():
                return input_
            case Path():
                return load_mcp_tools_text(input_.read_text(encoding=config.encoding))
        assert input_text is not None
        return load_mcp_tools_text(input_text)

    try:
        mcp_tools_jsonschema = convert_mcp_tools_to_jsonschema(load_mcp_tools_data())
    except Error:
        raise
    except Exception as exc:
        raise InvalidFileFormatError(exc, InputFileType.MCPTools) from exc

    if isinstance(input_, ParseResult) and (input_url := input_.geturl()):
        remote_text_cache[input_url] = json.dumps(mcp_tools_jsonschema)
        source_override = None
    else:
        source_override = mcp_tools_jsonschema
    return source_override, InputFileType.JsonSchema, True


def _validate_mapping_input(
    input_: _GenerationInput,
    input_file_type: InputFileType,
) -> None:
    """Reject mapping inputs for formats that require text or a file."""
    if not isinstance(input_, Mapping):
        return

    match input_file_type:
        case InputFileType.Auto:
            msg = (
                "input_file_type=Auto is not supported for dict input. "
                "Please specify input_file_type explicitly (e.g., InputFileType.JsonSchema)."
            )
        case InputFileType.GraphQL:
            msg = "Dict input is not supported for GraphQL. GraphQL requires text input (SDL format)."
        case InputFileType.XMLSchema:
            msg = "Dict input is not supported for xmlschema. Provide XSD text, file path, or URL input."
        case InputFileType.Protobuf:
            msg = "Dict input is not supported for protobuf. Provide .proto text, file path, or URL input."
        case InputFileType.Json | InputFileType.Yaml | InputFileType.CSV:
            msg = (
                f"Dict input is not supported for {input_file_type.value}. "
                f"Use InputFileType.Dict to generate schema from dict data, "
                f"or provide text/file input for {input_file_type.value} format."
            )
        case _:
            return
    raise Error(msg)


def _uses_pydantic_v2_schema_validator(config: GenerateConfig) -> bool:
    if (schema_validator_type := config.schema_validator_type) is None:
        return False

    match schema_validator_type:
        case SchemaValidatorType.PydanticV2:
            if config.output_model_type == DataModelType.PydanticV2BaseModel:
                return True
            msg = "schema_validator_type='pydantic-v2' is only supported for pydantic_v2.BaseModel"
            raise Error(msg)
    msg = f"Unsupported schema_validator_type: {schema_validator_type.value}"  # pragma: no cover
    raise Error(msg)  # pragma: no cover


def _prepare_parser_common_options(  # noqa: PLR0912, PLR0913, PLR0917
    input_: _GenerationInput,
    input_text: str | None,
    input_file_type: InputFileType,
    source_override: Mapping[str, Any] | None,
    config: GenerateConfig,
    extra_template_data: defaultdict[str, dict[str, Any]] | None,
    dataclass_arguments: DataclassArguments,
    *,
    skip_root_model: bool,
    remote_text_cache: DefaultPutDict[str, str],
) -> _PreparedParser:
    if config.union_mode is not None:
        if config.output_model_type == DataModelType.PydanticV2BaseModel:
            default_field_extras = {"union_mode": config.union_mode}
        else:  # pragma: no cover
            msg = "union_mode is only supported for pydantic_v2.BaseModel"
            raise Error(msg)
    else:
        default_field_extras = None

    generate_schema_validators = _uses_pydantic_v2_schema_validator(config)

    from datamodel_code_generator.model import get_data_model_types  # noqa: PLC0415

    data_model_types = get_data_model_types(
        config.output_model_type,
        config.target_python_version,
        use_type_alias=config.use_type_alias,
        use_type_alias_type=config.use_type_alias_type,
        use_root_model_type_alias=config.use_root_model_type_alias,
        include_graphql_models=input_file_type == InputFileType.GraphQL,
    )

    python_type_expressions: _PythonTypeExpressions | None = None
    if source_override is not None:
        source = dict(source_override)
    elif isinstance(input_, Mapping) and input_file_type not in RAW_DATA_TYPES:
        from datamodel_code_generator.input_model_result import LoadedInputModelSchema  # noqa: PLC0415

        if isinstance(input_, LoadedInputModelSchema):
            source = dict(input_.schema)
            python_type_expressions = input_.legacy_python_type_expressions
        else:
            source = dict(input_)
    else:
        source = input_text or input_
        assert not isinstance(source, Mapping)

    defer_formatting = config.output is not None and not config.output.suffix

    target_datetime_class = config.output_datetime_class
    if target_datetime_class is None:
        from datamodel_code_generator._format_types import DatetimeClassType  # noqa: PLC0415

        match input_file_type:
            case InputFileType.GraphQL:
                target_datetime_class = DatetimeClassType.Datetime
            case InputFileType.XMLSchema:
                target_datetime_class = None
            case _:
                target_datetime_class = DatetimeClassType.Awaredatetime

    additional_options: ParserConfigDict = {
        "data_model_type": data_model_types.data_model,
        "data_model_root_type": data_model_types.root_model,
        "data_model_field_type": data_model_types.field_model,
        "data_type_manager_type": data_model_types.data_type_manager,
        "dump_resolve_reference_action": data_model_types.dump_resolve_reference_action,
        "extra_template_data": extra_template_data,
        "serialization_aliases": config.serialization_aliases,
        "model_name_map": config.model_name_map,
        "generate_schema_validators": generate_schema_validators,
        "schema_validator_base_class_name": config.schema_validator_base_class_name,
        "base_path": input_.parent if isinstance(input_, Path) and input_.is_file() else None,
        "remote_text_cache": remote_text_cache,
        "known_third_party": data_model_types.known_third_party,
        "default_field_extras": default_field_extras,
        "target_datetime_class": target_datetime_class,
        "target_date_class": config.output_date_class,
        "dataclass_arguments": dataclass_arguments,
        "defer_formatting": defer_formatting,
        "use_type_checking_imports": config.use_type_checking_imports,
        "use_single_line_docstring": config.use_single_line_docstring,
        "enum_field_as_literal": (
            config.enum_field_as_literal
            if config.enum_field_as_literal is not None
            else (LiteralType.All if config.output_model_type == DataModelType.TypingTypedDict else None)
        ),
        "use_missing_sentinel": config.use_missing_sentinel,
        "set_default_enum_member": (
            True if config.output_model_type == DataModelType.DataclassesDataclass else config.set_default_enum_member
        ),
        "use_object_type": config.use_object_type,
        "skip_root_model": skip_root_model,
        "use_root_model_sequence_interface": config.use_root_model_sequence_interface,
    }
    return data_model_types, source, defer_formatting, additional_options, python_type_expressions


def _build_parser(  # noqa: PLR0911, PLR0913
    input_file_type: InputFileType,
    source: Any,
    config: GenerateConfig,
    additional_options: ParserConfigDict,
    data_model_types: DataModelSet,
    *,
    jsonschema_version: JsonSchemaVersion | None,
    openapi_version: OpenAPIVersion | None,
    asyncapi_version: AsyncAPIVersion | None,
    xmlschema_version: XMLSchemaVersion | None,
    protobuf_version: ProtobufVersion | None,
    python_type_expressions: _PythonTypeExpressions | None = None,
) -> Any:
    match input_file_type:
        case InputFileType.OpenAPI:
            from datamodel_code_generator.parser.openapi import OpenAPIParser  # noqa: PLC0415

            openapi_additional_options: OpenAPIParserConfigDict = {
                **_openapi_shared_options(config),
                "openapi_version": openapi_version,
                **additional_options,
            }
            parser_config = _create_parser_config(config, openapi_additional_options)
            return OpenAPIParser(source=source, config=parser_config)
        case InputFileType.AsyncAPI:
            from datamodel_code_generator.parser.asyncapi import AsyncAPIParser  # noqa: PLC0415

            asyncapi_additional_options: AsyncAPIParserConfigDict = {
                **_openapi_shared_options(config),
                "asyncapi_version": asyncapi_version,
                **additional_options,
            }
            parser_config = _create_parser_config(config, asyncapi_additional_options)
            return AsyncAPIParser(source=source, config=parser_config)
        case InputFileType.XMLSchema:
            from datamodel_code_generator.parser.xmlschema import XMLSchemaParser  # noqa: PLC0415

            xmlschema_additional_options: XMLSchemaParserConfigDict = {
                "xmlschema_version": xmlschema_version,
                **additional_options,
            }
            parser_config = _create_parser_config(config, xmlschema_additional_options)
            return XMLSchemaParser(source=source, config=parser_config)
        case InputFileType.Protobuf:
            from datamodel_code_generator.parser.protobuf import ProtobufParser  # noqa: PLC0415

            protobuf_additional_options: ProtobufParserConfigDict = {
                **additional_options,
                "protobuf_version": protobuf_version,
                "skip_root_model": True,
            }
            parser_config = _create_parser_config(config, protobuf_additional_options)
            return ProtobufParser(source=source, config=parser_config)
        case InputFileType.Avro:
            from datamodel_code_generator.parser.avro import AvroParser  # noqa: PLC0415

            avro_additional_options: AvroParserConfigDict = {**additional_options}
            parser_config = _create_parser_config(config, avro_additional_options)
            return AvroParser(source=source, config=parser_config)
        case InputFileType.GraphQL:
            from datamodel_code_generator.parser.graphql import GraphQLParser  # noqa: PLC0415

            graphql_additional_options: GraphQLParserConfigDict = {
                "data_model_scalar_type": data_model_types.scalar_model,
                "data_model_union_type": data_model_types.union_model,
                **additional_options,
            }
            parser_config = _create_parser_config(config, graphql_additional_options)
            return GraphQLParser(source=source, config=parser_config)
        case _:
            from datamodel_code_generator.parser.jsonschema import JsonSchemaParser  # noqa: PLC0415

            jsonschema_additional_options: JSONSchemaParserConfigDict = {
                "jsonschema_version": jsonschema_version,
                **additional_options,
            }
            parser_config = _create_parser_config(config, jsonschema_additional_options)
            if python_type_expressions is not None:
                return JsonSchemaParser._from_python_type_expressions(  # noqa: SLF001
                    source=source,
                    python_type_expressions=python_type_expressions,
                    config=parser_config,
                )
            return JsonSchemaParser(source=source, config=parser_config)
    msg = f"Unsupported input file type: {input_file_type}"
    raise Error(msg)


def _emit_stdout_results(
    results: _ParserResults,
    input_filename: str | None,
    header_prefix: str,
    header_suffix: str | None,
    *,
    has_custom_file_header: bool,
) -> str | GeneratedModules:
    """Render generated results for callers that do not provide an output path."""
    if isinstance(results, str):
        effective_header = _format_file_header(header_prefix, header_suffix, input_filename)
        return _build_module_content(
            results,
            effective_header,
            has_custom_file_header=has_custom_file_header,
        )

    generated: GeneratedModules = {}
    for name, result in sorted(results.items()):
        source_filename = str(result.source.as_posix() if result.source else input_filename)
        effective_header = _format_file_header(header_prefix, header_suffix, source_filename)
        generated[name] = _build_module_content(
            result.body,
            effective_header,
            has_custom_file_header=has_custom_file_header,
            future_imports=result.future_imports,
        )
    return generated


_SINGLE_MODULE_OUTPUT_DIRECTORY_ERROR = "Single-module output requires a file path, not a directory"
_MODEL_METADATA_OUTPUT_DIRECTORY_ERROR = "Model metadata output requires a file path, not a directory"


def _ensure_file_output_path(path: Path, error_message: str) -> None:
    """Reject an existing directory where a generated file is required."""
    if not path.is_dir():
        return
    raise Error(error_message)


def _write_results_to_output(  # noqa: PLR0913
    results: _ParserResults,
    output: Path,
    config: GenerateConfig,
    *,
    input_filename: str | None,
    header_prefix: str,
    header_suffix: str | None,
    has_custom_file_header: bool,
) -> None:
    """Write one file or a sorted collection of generated modules to disk."""
    if isinstance(results, str):
        _ensure_file_output_path(output, _SINGLE_MODULE_OUTPUT_DIRECTORY_ERROR)
        modules: dict[Path, tuple[str, str, str | None]] = {output: (results, "", input_filename)}
    else:
        if output.suffix:
            msg = "Modular references require an output directory, not a file"
            raise Error(msg)
        modules = {
            output.joinpath(*name): (
                result.body,
                result.future_imports,
                str(result.source.as_posix() if result.source else input_filename),
            )
            for name, result in sorted(results.items())
        }

    for path, (body, future_imports, filename) in modules.items():
        if not path.parent.exists():
            path.parent.mkdir(parents=True)

        effective_header = _format_file_header(header_prefix, header_suffix, filename)
        with path.open("wt", encoding=config.encoding) as file:
            if has_custom_file_header and body:
                file.write(
                    _build_module_content(
                        body,
                        effective_header,
                        has_custom_file_header=True,
                        future_imports=future_imports,
                    )
                    + "\n"
                )
            else:
                file.write(effective_header)
                if body:
                    file.write("\n\n")
                    file.write(body.rstrip())
                file.write("\n")


def _format_deferred_output(
    output: Path,
    config: GenerateConfig,
    data_model_types: DataModelSet,
    settings_path: Path,
) -> None:
    """Apply deferred Ruff formatting only when a Ruff formatter is configured."""
    if not config.formatters:
        return

    from datamodel_code_generator._format_types import Formatter  # noqa: PLC0415
    from datamodel_code_generator.format import CodeFormatter, resolve_use_type_checking_imports  # noqa: PLC0415

    if Formatter.RUFF_CHECK not in config.formatters and Formatter.RUFF_FORMAT not in config.formatters:
        return

    effective_use_type_checking_imports = resolve_use_type_checking_imports(
        config.use_type_checking_imports,
        is_multi_module_output=True,
        formatters=config.formatters,
        requires_runtime_imports_with_ruff_check=(data_model_types.data_model.REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK),
    )
    code_formatter = CodeFormatter(
        config.target_python_version,
        settings_path,
        config.wrap_string_literal,
        skip_string_normalization=not config.use_double_quotes,
        known_third_party=data_model_types.known_third_party,
        custom_formatters=config.custom_formatters,
        custom_formatters_kwargs=config.custom_formatters_kwargs,
        encoding=config.encoding,
        formatters=config.formatters,
        builtin_format_line_length=config.builtin_format_line_length,
        use_type_checking_imports=effective_use_type_checking_imports,
        defer_formatting=True,
    )
    code_formatter.format_directory(output)


def _emit_results(  # noqa: PLR0913
    results: _ParserResults,
    input_: _GenerationInput,
    input_filename: str | None,
    custom_file_header: str | None,
    config: GenerateConfig,
    *,
    defer_formatting: bool,
    data_model_types: DataModelSet,
    settings_path: Path,
) -> str | GeneratedModules | None:
    if not input_filename:  # pragma: no cover
        match input_:
            case str():
                input_filename = "<stdin>"
            case ParseResult():
                input_filename = input_.geturl()
            case Path():
                input_filename = input_.name
            case _:
                # input_ might be a dict object provided directly, and missing a name field
                input_filename = getattr(input_, "name", "<dict>")
    if not results:
        msg = "Models not found in the input data"
        raise Error(msg)

    if custom_file_header is None and (custom_file_header_path := config.custom_file_header_path):
        try:
            custom_file_header = custom_file_header_path.read_text(encoding=config.encoding)
        except (OSError, UnicodeDecodeError) as e:
            msg = f"Unable to read custom file header {custom_file_header_path}: {e}"
            raise Error(msg) from e

    has_custom_file_header = bool(custom_file_header)
    header_prefix, header_suffix = _build_file_header_parts(custom_file_header, config)

    output = config.output
    if output is None:
        return _emit_stdout_results(
            results,
            input_filename,
            header_prefix,
            header_suffix,
            has_custom_file_header=has_custom_file_header,
        )

    _write_results_to_output(
        results,
        output,
        config,
        input_filename=input_filename,
        header_prefix=header_prefix,
        header_suffix=header_suffix,
        has_custom_file_header=has_custom_file_header,
    )
    if defer_formatting:
        _format_deferred_output(output, config, data_model_types, settings_path)

    return None


def _write_model_metadata(metadata_path: Path, metadata: ModelMetadata | None, encoding: str) -> None:
    from datamodel_code_generator.model_metadata import dump_model_metadata  # noqa: PLC0415

    _ensure_file_output_path(metadata_path, _MODEL_METADATA_OUTPUT_DIRECTORY_ERROR)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(f"{dump_model_metadata(metadata)}\n", encoding=encoding)


def generate(
    input_: Path | str | ParseResult | Mapping[str, Any] | list[Any],
    *,
    config: GenerateConfig | None = None,
    **options: Unpack[GenerateConfigDict],
) -> str | GeneratedModules | None:
    """Generate Python data models from schema definitions or structured data.

    This is the main entry point for code generation. Supports OpenAPI, AsyncAPI,
    JSON Schema, GraphQL, XML Schema, Protocol Buffers, Avro, and raw data formats
    (JSON, YAML, Dict, CSV) as input.

    HTTP(S) URL inputs and references select their backend lazily on first use. The default ``HTTPBackend.AUTO``
    policy selects stable `httpx` when its client module is installed and selects experimental `httpx2` only when
    that module is absent. Explicit choices and paired dependency errors do not fall back. File URL reference
    joining uses a dependency-free local fast path.

    Args:
        input_: The input source (Path file input, string content, URL, dict,
            list of file paths, or MCP tools list when input_file_type is
            InputFileType.MCPTools).
        config: A GenerateConfig object with all options. Cannot be used together with **options.
        **options: Individual options matching GenerateConfig fields. Cannot be used together with config.

    Returns:
        - When output is a Path: None (writes to file system)
        - When output is None and single module: str (generated code)
        - When output is None and multiple modules: GeneratedModules (dict mapping
          module path tuples to generated code strings)

    Raises:
        ValueError: If both config and **options are provided.
    """
    if config is not None and options:
        msg = "Cannot specify both 'config' and keyword arguments. Use one or the other."
        raise ValueError(msg)

    if config is None:
        from datamodel_code_generator.config import GenerateConfig as _GenerateConfig  # noqa: PLC0415
        from datamodel_code_generator.config import _rebuild_generate_config  # noqa: PLC0415

        _rebuild_generate_config()
        config = _GenerateConfig.model_validate(options)
    from pydantic import VERSION as PYDANTIC_VERSION  # ruff: ignore[import-outside-top-level]

    from datamodel_code_generator.deprecations import warn_legacy_dependency  # ruff: ignore[import-outside-top-level]

    warn_legacy_dependency("dependency.pydantic-runtime-minimum", PYDANTIC_VERSION, (2, 8, 2))
    config = _apply_generate_config_preset(config)
    config = _apply_missing_sentinel_config(config)

    if (metadata_path := config.emit_model_metadata) is not None:
        _ensure_file_output_path(metadata_path, _MODEL_METADATA_OUTPUT_DIRECTORY_ERROR)

    atomic_remote_update = (
        config.update_lock
        and not config.remote_lock_resolved
        and (config.output is not None or config.emit_model_metadata is not None)
    )
    if config.output is not None and _uses_legacy_process_state(config):
        with PROCESS_STATE_LOCK:
            return (_generate_with_atomic_remote_update if atomic_remote_update else _generate)(
                input_, config, Path.cwd(), use_output_cwd=atomic_remote_update or config.output is not None
            )
    with PROCESS_STATE_LOCK:
        caller_cwd = Path.cwd()
    return (_generate_with_atomic_remote_update if atomic_remote_update else _generate)(
        input_, config, caller_cwd, use_output_cwd=False
    )


def _generate_with_atomic_remote_update(  # noqa: PLR0912, PLR0914, PLR0915
    input_: _GenerationInput,
    config: GenerateConfig,
    caller_cwd: Path,
    *,
    use_output_cwd: bool,
) -> str | GeneratedModules | None:
    """Generate into private staging and publish output, metadata, and update lock together."""
    import tempfile  # noqa: PLC0415

    from datamodel_code_generator._publication import (  # noqa: PLC0415
        StagedFile,
        StagingDirectory,
        close_anchor,
        publication_anchor,
        publish_staged_files,
    )
    from datamodel_code_generator.remote_lock import RemoteReferenceLock  # noqa: PLC0415

    path_updates = {
        field: absolute_path
        for field in ("output", "emit_model_metadata")
        if (absolute_path := _absolute_generation_path(getattr(config, field), caller_cwd))
        is not getattr(config, field)
    }
    if path_updates:
        config = config.model_copy(update=path_updates)
    lockfile = config.lockfile.expanduser() if config.lockfile is not None else caller_cwd / "datamodel-codegen.lock"
    if not lockfile.is_absolute():
        lockfile = caller_cwd / lockfile
    canonical_lockfile = lockfile.resolve(strict=False)
    _validate_generation_path_conflicts(input_, config.output, config.emit_model_metadata, canonical_lockfile)
    remote_lock = RemoteReferenceLock.open(canonical_lockfile, update=True, locked=False)
    contexts: list[tempfile.TemporaryDirectory[str]] = []
    anchors: list[PublicationAnchor] = []
    lock_anchor: PublicationAnchor | None = None
    lock_staging: StagingDirectory | None = None
    try:
        staged_updates: dict[str, Path] = {}
        staged_artifacts: list[_StagedArtifact] = []
        if (output := config.output) is not None:
            output_parent = Path(os.path.abspath(output.expanduser())).parent  # noqa: PTH100
            while not output_parent.exists():
                output_parent = output_parent.parent
            output_context = tempfile.TemporaryDirectory(prefix=".datamodel-codegen-", dir=output_parent)
            contexts.append(output_context)
            staged_output = Path(output_context.name) / (output.name or "output")
            if output.is_dir():
                staged_output.mkdir()
            staged_updates["output"] = staged_output
            resolved_output_root = output.expanduser().resolve(strict=False)
            resolved_output_parent = output.parent.expanduser().resolve(strict=False)
            output_anchor = publication_anchor(resolved_output_root if output.is_dir() else resolved_output_parent)
            anchors.append(output_anchor)
            staged_artifacts.append((
                staged_output,
                output,
                output_anchor,
                resolved_output_root,
                resolved_output_parent,
            ))
        if (metadata := config.emit_model_metadata) is not None:
            metadata_parent = Path(os.path.abspath(metadata.expanduser())).parent  # noqa: PTH100
            while not metadata_parent.exists():
                metadata_parent = metadata_parent.parent
            metadata_context = tempfile.TemporaryDirectory(prefix=".datamodel-codegen-", dir=metadata_parent)
            contexts.append(metadata_context)
            staged_metadata = Path(metadata_context.name) / (metadata.name or "model-metadata.json")
            staged_updates["emit_model_metadata"] = staged_metadata
            resolved_metadata_parent = metadata.parent.expanduser().resolve(strict=False)
            metadata_anchor = publication_anchor(resolved_metadata_parent)
            anchors.append(metadata_anchor)
            staged_artifacts.append((
                staged_metadata,
                metadata,
                metadata_anchor,
                resolved_metadata_parent,
                resolved_metadata_parent,
            ))
        lock_anchor = publication_anchor(canonical_lockfile.parent)
        lock_staging = StagingDirectory.create(lock_anchor, prefix=".datamodel-codegen-lock-")
        staged_config = config.model_copy(update=staged_updates)
        staged_config._logical_output = config.output  # noqa: SLF001
        staged_config._logical_model_metadata = config.emit_model_metadata  # noqa: SLF001
        staged_config.resolve_remote_lock(remote_lock)
        generated = _generate(input_, staged_config, caller_cwd, use_output_cwd=use_output_cwd)
        publication_files: list[StagedFile] = []
        for (
            staged_artifact,
            target_artifact,
            anchor,
            resolved_artifact_root,
            resolved_artifact_parent,
        ) in staged_artifacts:
            if staged_artifact.is_file():
                publication_files.append(
                    StagedFile(
                        staged_artifact,
                        target_artifact,
                        resolved_artifact_parent / target_artifact.name,
                        anchor,
                    )
                )
            elif staged_artifact.exists():
                for staged_file in filter(Path.is_file, sorted(staged_artifact.rglob("*"))):
                    relative_path = staged_file.relative_to(staged_artifact)
                    target_file = target_artifact / relative_path
                    publication_files.append(
                        StagedFile(
                            staged_file,
                            target_file,
                            resolved_artifact_root / relative_path,
                            anchor,
                        )
                    )
        staged_lock = cast("StagedFile", remote_lock.stage(lock_staging))
        publication_files.append(staged_lock._replace(anchor=lock_anchor))
        publish_staged_files(publication_files)
        remote_lock.mark_committed()
    except BaseException:
        with contextlib.suppress(OSError):
            remote_lock.discard_stage()
        raise
    else:
        return generated
    finally:
        if lock_staging is not None:
            with contextlib.suppress(OSError):
                lock_staging.cleanup()
        if lock_anchor is not None:
            with contextlib.suppress(OSError):
                close_anchor(lock_anchor)
        for anchor in anchors:
            with contextlib.suppress(OSError):
                close_anchor(anchor)
        for context in contexts:
            with contextlib.suppress(OSError):
                context.cleanup()


def _prepare_generation_config(config: GenerateConfig, caller_cwd: Path) -> tuple[GenerateConfig, Path, Path]:
    """Resolve configuration paths before any process-relative generation work."""
    caller_path_updates = {
        field: absolute_path
        for field in ("output", "emit_model_metadata", "custom_file_header_path", "custom_template_dir")
        if (absolute_path := _absolute_generation_path(getattr(config, field), caller_cwd))
        is not getattr(config, field)
    }
    if caller_path_updates:
        config = config.model_copy(update=caller_path_updates)
    logical_output = _absolute_generation_path(config._logical_output, caller_cwd)  # noqa: SLF001
    output_context_path = _output_context_path(logical_output or config.output, caller_cwd)
    if (http_local_ref_path := _absolute_generation_path(config.http_local_ref_path, output_context_path)) is not (
        config.http_local_ref_path
    ):
        config = config.model_copy(update={"http_local_ref_path": http_local_ref_path})

    _validate_output_datetime_class(config.output_model_type, config.output_datetime_class)
    _validate_alias_generator(config.output_model_type, config.alias_generator)
    emit_settings_path = _settings_path_from(caller_cwd, config.settings_path)
    return config, output_context_path, emit_settings_path


def _prepare_generation_extra_template_data(config: GenerateConfig) -> defaultdict[str, dict[str, Any]] | None:
    """Preserve facade dictionary defaults; the parser owns the nested copy."""
    data = config.extra_template_data
    if isinstance(data, defaultdict) and data.default_factory is not dict:
        return defaultdict(dict, data)
    return data


def _build_generation_parser(  # noqa: PLR0913, PLR0917
    input_: _GenerationInput,
    input_file_type: InputFileType,
    parser_source: _ParserSource,
    config: GenerateConfig,
    parser_options: ParserConfigDict,
    data_model_types: DataModelSet,
    schema_versions: _SchemaVersions,
    diagnostic_source_path: Path | None,
    *,
    prefetched_source: tuple[Path, bytes] | None = None,
    formatter_cwd: Path | None = None,
    preserve_circular_root_models: bool = False,
    suppress_parse_warnings: bool = False,
    reference_cache: Any | None = None,
    python_type_expressions: _PythonTypeExpressions | None = None,
) -> Any:
    """Build one fresh parser using the caller's reference-resolution base."""
    jsonschema_version, openapi_version, asyncapi_version, xmlschema_version, protobuf_version = schema_versions
    with _warn_on_input_string_path_failure(input_):
        parser = _build_parser(
            input_file_type,
            parser_source,
            config,
            parser_options,
            data_model_types,
            jsonschema_version=jsonschema_version,
            openapi_version=openapi_version,
            asyncapi_version=asyncapi_version,
            xmlschema_version=xmlschema_version,
            protobuf_version=protobuf_version,
            python_type_expressions=python_type_expressions,
        )
    if reference_cache is not None and hasattr(parser, "remote_object_cache"):
        parser.remote_object_cache = reference_cache
    parser.configure_run_context(
        diagnostic_source_path=diagnostic_source_path,
        prefetched_source=prefetched_source,
        formatter_cwd=formatter_cwd,
        preserve_circular_root_models=preserve_circular_root_models,
        suppress_parse_warnings=suppress_parse_warnings,
    )
    return parser


def _build_generation_retry_parser(  # noqa: PLR0913, PLR0917
    input_: _GenerationInput,
    input_text: str | None,
    input_file_type: InputFileType,
    source_override: Mapping[str, Any] | None,
    config: GenerateConfig,
    extra_template_data: defaultdict[str, dict[str, Any]] | None,
    dataclass_arguments: DataclassArguments,
    schema_versions: _SchemaVersions,
    diagnostic_source_path: Path | None,
    remote_text_cache: DefaultPutDict[str, str],
    reference_cache: Any | None,
    base_path: Path,
    *,
    skip_root_model: bool,
    prefetched_source: tuple[Path, bytes] | None = None,
    formatter_cwd: Path | None = None,
    preserve_circular_root_models: bool = False,
    suppress_parse_warnings: bool = False,
) -> tuple[Any, DataModelSet, bool]:
    """Build one fresh parser for an internal compatibility retry."""
    data_model_types, parser_source, defer_formatting, parser_options, python_type_expressions = (
        _prepare_parser_common_options(
            input_,
            input_text,
            input_file_type,
            source_override,
            config,
            extra_template_data,
            dataclass_arguments,
            skip_root_model=skip_root_model,
            remote_text_cache=remote_text_cache,
        )
    )
    parser_options["base_path"] = base_path
    parser = _build_generation_parser(
        input_,
        input_file_type,
        parser_source,
        config,
        parser_options,
        data_model_types,
        schema_versions,
        diagnostic_source_path,
        prefetched_source=prefetched_source,
        formatter_cwd=formatter_cwd,
        preserve_circular_root_models=preserve_circular_root_models,
        suppress_parse_warnings=suppress_parse_warnings,
        reference_cache=reference_cache,
        python_type_expressions=python_type_expressions,
    )
    return parser, data_model_types, defer_formatting


def _prepare_directory_input(input_: _GenerationInput, config: GenerateConfig) -> GenerateConfig:
    """Identify configured artifacts only when generation overlaps a directory input."""
    if not isinstance(input_, Path) or not input_.is_dir():
        return config

    from datamodel_code_generator._source import DirectoryInputFilter  # noqa: PLC0415

    output = config._logical_output or config.output  # noqa: SLF001
    metadata = config._logical_model_metadata or config.emit_model_metadata  # noqa: SLF001
    input_root = input_.resolve()
    files: set[Path] = set()
    output_directory = None
    for target, is_directory in ((output, output is not None and not output.suffix), (metadata, False)):
        if target is None:
            continue
        paths = (target.resolve(),) if is_directory else (target.parent.resolve() / target.name, target.resolve())
        for resolved in paths:
            if not resolved.is_relative_to(input_root):
                continue
            source_path = input_ / resolved.relative_to(input_root)
            if is_directory:
                output_directory = source_path
            else:
                files.add(source_path)
    if output_directory is None and not files:
        return config

    config = config.model_copy()
    headers = ["# generated by datamodel-codegen:", "# @generated by datamodel-codegen:"]
    if output_directory is not None:
        custom_header = config.custom_file_header
        if custom_header is None and (header_path := config.custom_file_header_path):
            try:
                custom_header = header_path.read_text(encoding=config.encoding)
            except (OSError, UnicodeDecodeError) as exc:
                msg = f"Unable to read custom file header {header_path}: {exc}"
                raise Error(msg) from exc
            config.custom_file_header = custom_header
        if custom_header:
            recognition_header = (
                custom_header.rstrip("\r\n")
                if config.custom_file_header_mode == CustomFileHeaderMode.Prepend
                else custom_header
            )
            headers.extend((
                recognition_header,
                _build_header_with_future_imports(recognition_header, "from __future__ import annotations"),
            ))
    config._directory_input_filter = DirectoryInputFilter(  # noqa: SLF001
        frozenset(files),
        output_directory,
        tuple(header.replace("\r\n", "\n").replace("\r", "\n") for header in headers),
    )
    return config


def _prepare_generation_input(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
    input_: _GenerationInput,
    config: GenerateConfig,
    caller_cwd: Path,
    remote_text_cache: DefaultPutDict[str, str],
    *,
    input_file_type: InputFileType,
    dataclass_arguments: DataclassArguments | None,
    skip_root_model: bool,
) -> _PreparedGenerationInput:
    """Normalize input, resolve the remote lock, and retain only parse-ready values."""
    if (
        isinstance(input_, list)
        and input_file_type != InputFileType.MCPTools
        and (not input_ or any(not isinstance(item, Path) for item in input_))
    ):
        msg = (  # pragma: no cover
            "List input is only supported for file path lists or input_file_type=InputFileType.MCPTools."
        )
        raise Error(msg)  # pragma: no cover

    match input_:
        case Path() as input_path if not input_path.is_absolute():
            input_ = (caller_cwd / input_path.expanduser()).resolve()
        case [Path(), *_] as input_paths if input_file_type != InputFileType.MCPTools:
            if any(not path.is_absolute() for path in input_paths):
                input_ = [
                    path if path.is_absolute() else (caller_cwd / path.expanduser()).resolve() for path in input_paths
                ]

    remote_lock = config.remote_lock
    owned_remote_lock: RemoteReferenceLock | None = None
    if config.remote_lock_resolved:
        _validate_generation_path_conflicts(
            input_,
            config.output,
            config.emit_model_metadata,
            getattr(remote_lock, "path", None),
        )
    else:
        default_lockfile = caller_cwd / "datamodel-codegen.lock"
        lockfile = config.lockfile.expanduser() if config.lockfile is not None else default_lockfile
        if not lockfile.is_absolute():
            lockfile = (caller_cwd / lockfile).resolve()
        use_remote_lock = config.update_lock or config.locked or lockfile.is_file()
        _validate_generation_path_conflicts(
            input_,
            config.output,
            config.emit_model_metadata,
            lockfile if use_remote_lock else None,
        )
        if use_remote_lock:
            config = config.model_copy()
            from datamodel_code_generator.remote_lock import RemoteReferenceLock  # noqa: PLC0415

            owned_remote_lock = RemoteReferenceLock.open(
                lockfile,
                update=config.update_lock,
                locked=config.locked,
            )
            remote_lock = owned_remote_lock
            config.resolve_remote_lock(remote_lock)
    response_observer = remote_lock.record_response if remote_lock is not None else None
    match input_:
        case str():
            input_text: str | None = input_
        case ParseResult():
            from datamodel_code_generator.http import DEFAULT_HTTP_TIMEOUT, get_body  # noqa: PLC0415

            timeout = config.http_timeout if config.http_timeout is not None else DEFAULT_HTTP_TIMEOUT
            input_text = remote_text_cache.get_or_put(
                input_.geturl(),
                default_factory=lambda url: get_body(
                    url,
                    config.http_headers,
                    config.http_ignore_tls,
                    config.http_query_parameters,
                    timeout,
                    allow_private_network=config.allow_private_network,
                    http_backend=config.http_backend,
                    response_observer=response_observer,
                    encoding=config.encoding,
                ),
            )
        case _:
            input_text = None

    if dataclass_arguments is None:
        dataclass_arguments = DataclassArguments()
        if config.frozen_dataclasses:
            dataclass_arguments["frozen"] = True
        if config.keyword_only:
            dataclass_arguments["kw_only"] = True

    _validate_mapping_input(input_, input_file_type)
    config = _prepare_directory_input(input_, config)
    source_override: Mapping[str, Any] | None = None
    diagnostic_source_path: Path | None = None
    prefetched_source: tuple[Path, bytes] | None = None
    if input_file_type == InputFileType.Auto:
        try:
            match input_:
                case Path() as input_path if (input_filter := config._directory_input_filter) is not None:  # noqa: SLF001
                    detected_input = next(input_filter.iter_files(input_path.rglob("*"), config.encoding), None)
                    if detected_input is None:
                        msg = f"No file found in: {input_path}"
                        raise FileNotFoundError(msg)  # noqa: TRY301
                    detected_input_path, input_data = detected_input
                    if input_data is None:
                        input_data = detected_input_path.read_bytes()
                    prefetched_source = (detected_input_path, input_data)
                    input_text_ = input_data.decode(config.encoding)
                case Path() as input_path:
                    input_text_ = get_first_file(input_path).read_text(encoding=config.encoding)
                case [Path(), *_] as input_paths:
                    detected_input_path = get_first_file(input_paths[0])
                    input_data = detected_input_path.read_bytes()
                    # Retain original bytes for parsing and content-based cache keys without another read.
                    prefetched_source = (detected_input_path, input_data)
                    input_text_ = input_data.decode(config.encoding)
                case _:
                    input_text_ = input_text
        except FileNotFoundError as exc:
            msg = f"File not found: {input_}"
            raise Error(msg) from exc

        try:
            with _warn_on_input_string_path_failure(input_):
                assert isinstance(input_text_, str)
                input_file_type = infer_input_type(input_text_)
        except Exception as exc:
            raise InvalidFileFormatError(exc) from exc
        else:
            print(  # noqa: T201
                inferred_message.format(input_file_type.value),
                file=sys.stderr,
            )
            # Reuse already-read text for single Path file to avoid re-reading
            # Only for OpenAPI/JsonSchema (RAW_DATA_TYPES are transformed by genson)
            if isinstance(input_, Path) and input_.is_file() and input_file_type not in RAW_DATA_TYPES:
                input_text = input_text_
                diagnostic_source_path = Path(input_.name)

    with _warn_on_input_string_path_failure(input_):
        input_text = _normalize_raw_input(input_, input_text, input_file_type, config)

    if input_file_type == InputFileType.MCPTools:
        with _warn_on_input_string_path_failure(input_):
            source_override, input_file_type, skip_root_model = _convert_mcp_tools(
                input_,
                input_text,
                config,
                remote_text_cache,
            )

    if isinstance(input_, ParseResult) and input_file_type not in RAW_DATA_TYPES:
        input_text = None
    return (
        config,
        input_,
        input_text,
        input_file_type,
        dataclass_arguments,
        source_override,
        diagnostic_source_path,
        prefetched_source,
        skip_root_model,
        owned_remote_lock,
    )


def _parse_with_disposal(
    input_: _GenerationInput,
    parser: Any,
    config: GenerateConfig,
    *,
    parser_settings_path: Path | None,
) -> _ParserResults:
    """Parse with one parser and dispose it if parsing fails."""
    try:
        with _warn_on_input_string_path_failure(input_):
            results = parser.parse(
                settings_path=parser_settings_path,
                disable_future_imports=config.disable_future_imports,
                all_exports_scope=config.all_exports_scope,
                all_exports_collision_strategy=config.all_exports_collision_strategy,
                module_split_mode=config.module_split_mode,
                collect_model_metadata=config.emit_model_metadata is not None,
            )
    except BaseException as exc:
        match exc:
            case _CollapseRootModelsRecursionError():
                parser.dispose()
            case _:
                with contextlib.suppress(BaseException):
                    parser.dispose()
        raise
    return results


def _parse_collapse_root_models_retry(
    input_: _GenerationInput,
    parser: Any,
    config: GenerateConfig,
    *,
    parser_settings_path: Path | None,
) -> _ParserResults:
    """Parse the compatibility retry without repeating user-visible warnings."""
    try:
        return _parse_with_disposal(
            input_,
            parser,
            config,
            parser_settings_path=parser_settings_path,
        )
    except _CollapseRootModelsRecursionError as retry_exc:
        match retry_exc.__cause__:
            case RecursionError() as retry_cause:
                public_error = retry_cause
            case _:
                public_error = RecursionError(str(retry_exc))
        raise public_error from None


def _parse_generation(  # noqa: PLR0913, PLR0914, PLR0917
    input_: _GenerationInput,
    input_text: str | None,
    input_file_type: InputFileType,
    source_override: Mapping[str, Any] | None,
    config: GenerateConfig,
    parser_source: _ParserSource,
    parser_options: ParserConfigDict,
    data_model_types: DataModelSet,
    defer_formatting: bool,  # noqa: FBT001
    extra_template_data: defaultdict[str, dict[str, Any]] | None,
    dataclass_arguments: DataclassArguments,
    *,
    python_type_expressions: _PythonTypeExpressions | None,
    skip_root_model: bool,
    schema_versions: _SchemaVersions,
    diagnostic_source_path: Path | None,
    prefetched_source: tuple[Path, bytes] | None,
    parser_settings_path: Path | None,
    use_output_cwd: bool,
    output_context_path: Path,
) -> _ParsedGeneration:
    """Parse, dispose, and retry narrow compatibility failures inside the output cwd."""
    # Phase 3: build before chdir so initial reference resolution keeps the caller's cwd.
    parser = _build_generation_parser(
        input_,
        input_file_type,
        parser_source,
        config,
        parser_options,
        data_model_types,
        schema_versions,
        diagnostic_source_path,
        prefetched_source=prefetched_source,
        formatter_cwd=None if use_output_cwd else output_context_path,
        python_type_expressions=python_type_expressions,
    )
    # Phase 4: initial parse, disposal, and the complete retry flow share the output cwd.
    with chdir(output_context_path if use_output_cwd else None):
        try:
            results = _parse_with_disposal(
                input_,
                parser,
                config,
                parser_settings_path=parser_settings_path,
            )
        except _CollapseRootModelsRecursionError:
            retry_remote_text_cache = parser.remote_text_cache
            retry_reference_cache = getattr(parser, "remote_object_cache", None)
            retry_base_path = parser.base_path
            del parser

            retry_extra_template_data = _prepare_generation_extra_template_data(config)
            parser, data_model_types, defer_formatting = _build_generation_retry_parser(
                input_,
                input_text,
                input_file_type,
                source_override,
                config,
                retry_extra_template_data,
                dataclass_arguments,
                schema_versions,
                diagnostic_source_path,
                retry_remote_text_cache,
                retry_reference_cache,
                retry_base_path,
                skip_root_model=skip_root_model,
                prefetched_source=prefetched_source,
                formatter_cwd=None if use_output_cwd else output_context_path,
                preserve_circular_root_models=True,
                suppress_parse_warnings=True,
            )
            extra_template_data = retry_extra_template_data
            results = _parse_collapse_root_models_retry(
                input_,
                parser,
                config,
                parser_settings_path=parser_settings_path,
            )
        model_metadata = parser.model_metadata
        if repair_modules := parser.invalid_dotted_stdout_repair_modules:
            legacy_inventory = parser.generated_model_inventory
            legacy_source_fingerprint = parser.source_data_fingerprint
            retry_remote_text_cache = parser.remote_text_cache
            retry_reference_cache = getattr(parser, "remote_object_cache", None)
            retry_base_path = parser.base_path
            preserve_circular_root_models = parser.run_context.preserve_circular_root_models
        parser.dispose()
        del parser

        if repair_modules:
            retry_config = config.model_copy(
                update={
                    "repair_invalid_dotted_stdout": False,
                    "forced_invalid_dotted_stdout_repair_modules": repair_modules,
                }
            )
            retry_completed = False
            with contextlib.suppress(Exception), warnings.catch_warnings():
                warnings.simplefilter("ignore", DanglingRefWarning)
                retry_parser, retry_data_model_types, retry_defer_formatting = _build_generation_retry_parser(
                    input_,
                    input_text,
                    input_file_type,
                    source_override,
                    retry_config,
                    extra_template_data,
                    dataclass_arguments,
                    schema_versions,
                    diagnostic_source_path,
                    retry_remote_text_cache,
                    retry_reference_cache,
                    retry_base_path,
                    skip_root_model=skip_root_model,
                    prefetched_source=prefetched_source,
                    formatter_cwd=None if use_output_cwd else output_context_path,
                    preserve_circular_root_models=preserve_circular_root_models,
                )
                retry_results = _parse_with_disposal(
                    input_,
                    retry_parser,
                    retry_config,
                    parser_settings_path=parser_settings_path,
                )
                retry_completed = True

            if retry_completed:
                # This is a compatibility repair: retain the completed legacy result if it cannot be proven safe.
                try:
                    if (
                        isinstance(retry_results, str)
                        and retry_results
                        and retry_parser.stdout_result_usable
                        and retry_parser.generated_model_inventory == legacy_inventory
                        and retry_parser.source_data_fingerprint == legacy_source_fingerprint
                    ):
                        results = retry_results
                        model_metadata = retry_parser.model_metadata
                        data_model_types = retry_data_model_types
                        defer_formatting = retry_defer_formatting
                finally:
                    retry_parser.dispose()
                del retry_parser

            del retry_reference_cache, retry_remote_text_cache
    return results, model_metadata, data_model_types, defer_formatting


def _emit_generation(  # noqa: PLR0913
    results: _ParserResults,
    input_: _GenerationInput,
    config: GenerateConfig,
    model_metadata: ModelMetadata | None,
    data_model_types: DataModelSet,
    *,
    input_filename: str | None,
    custom_file_header: str | None,
    defer_formatting: bool,
    settings_path: Path,
    owned_remote_lock: RemoteReferenceLock | None,
) -> str | GeneratedModules | None:
    """Emit generated artifacts and commit a generation-owned remote lock."""
    generated = _emit_results(
        results,
        input_,
        input_filename,
        custom_file_header,
        config,
        defer_formatting=defer_formatting,
        data_model_types=data_model_types,
        settings_path=settings_path,
    )
    if config.emit_model_metadata is not None:
        _write_model_metadata(config.emit_model_metadata, model_metadata, config.encoding)
    if owned_remote_lock is not None:
        owned_remote_lock.commit()
    return generated


def _generate(  # noqa: PLR0914
    input_: _GenerationInput,
    config: GenerateConfig,
    caller_cwd: Path,
    *,
    use_output_cwd: bool,
) -> str | GeneratedModules | None:
    """Generate models after capturing all process-relative state."""
    with _tuned_gc():
        config, output_context_path, emit_settings_path = _prepare_generation_config(config, caller_cwd)
        input_filename = config.input_filename
        input_file_type = config.input_file_type
        extra_template_data = _prepare_generation_extra_template_data(config)
        dataclass_arguments = config.dataclass_arguments
        skip_root_model = config.skip_root_model
        remote_text_cache: DefaultPutDict[str, str] = DefaultPutDict()
        (
            config,
            input_,
            input_text,
            input_file_type,
            dataclass_arguments,
            source_override,
            diagnostic_source_path,
            prefetched_source,
            skip_root_model,
            owned_remote_lock,
        ) = _prepare_generation_input(
            input_,
            config,
            caller_cwd,
            remote_text_cache,
            input_file_type=input_file_type,
            dataclass_arguments=dataclass_arguments,
            skip_root_model=skip_root_model,
        )
        data_model_types, source, defer_formatting, additional_options, python_type_expressions = (
            _prepare_parser_common_options(
                input_,
                input_text,
                input_file_type,
                source_override,
                config,
                extra_template_data,
                dataclass_arguments,
                skip_root_model=skip_root_model,
                remote_text_cache=remote_text_cache,
            )
        )
        if additional_options["base_path"] is None and not isinstance(source, Path):
            match input_:
                case [Path(), *_] as input_paths:
                    additional_options["base_path"] = _path_list_base_path(input_paths, caller_cwd)
                case _:
                    additional_options["base_path"] = caller_cwd
        schema_versions = _resolve_schema_versions(input_file_type, config.schema_version)
        parser_settings_path = (
            config.settings_path if use_output_cwd else _settings_path_from(output_context_path, config.settings_path)
        )
        results, model_metadata, data_model_types, defer_formatting = _parse_generation(
            input_,
            input_text,
            input_file_type,
            source_override,
            config,
            source,
            additional_options,
            data_model_types,
            defer_formatting,
            extra_template_data,
            dataclass_arguments,
            python_type_expressions=python_type_expressions,
            skip_root_model=skip_root_model,
            schema_versions=schema_versions,
            diagnostic_source_path=diagnostic_source_path,
            prefetched_source=prefetched_source,
            parser_settings_path=parser_settings_path,
            use_output_cwd=use_output_cwd,
            output_context_path=output_context_path,
        )
        del additional_options, extra_template_data
        return _emit_generation(
            results,
            input_,
            config,
            model_metadata,
            data_model_types,
            input_filename=input_filename,
            custom_file_header=config.custom_file_header,
            defer_formatting=defer_formatting,
            settings_path=emit_settings_path,
            owned_remote_lock=owned_remote_lock,
        )


def infer_input_type(text: str) -> InputFileType:  # noqa: PLR0911, PLR0912
    """Automatically detect the input file type from text content."""
    from datamodel_code_generator.util import get_yaml_parse_errors  # noqa: PLC0415

    if _is_xml_text(text):
        from datamodel_code_generator._xmlschema_detection import is_xml_schema_text  # noqa: PLC0415

        if is_xml_schema_text(text):
            return InputFileType.XMLSchema

    protobuf_declaration = _has_protobuf_declaration(text)

    try:
        data = load_yaml(text)
    except get_yaml_parse_errors() as exc:
        if not _is_json_text(text) and _looks_like_csv_text(text):
            return InputFileType.CSV
        if protobuf_declaration:
            return InputFileType.Protobuf
        msg = _infer_input_type_error_message(parse_error=exc)
        raise Error(msg) from exc
    if isinstance(data, dict):
        if is_asyncapi(data):
            return InputFileType.AsyncAPI
        if is_openapi(data):
            return InputFileType.OpenAPI
        from datamodel_code_generator._avro_detection import is_avro_schema_data  # noqa: PLC0415

        if is_avro_schema_data(data):
            return InputFileType.Avro
        if is_schema(data):
            return InputFileType.JsonSchema
        return InputFileType.Json
    if _is_protobuf_text(text):
        return InputFileType.Protobuf
    if isinstance(data, list):
        from datamodel_code_generator._avro_detection import is_avro_schema_data  # noqa: PLC0415

        if is_avro_schema_data(data):
            return InputFileType.Avro
    if isinstance(data, str):
        if _looks_like_csv_text(text):
            return InputFileType.CSV
        if protobuf_declaration:
            return InputFileType.Protobuf
        from datamodel_code_generator._avro_detection import is_avro_schema_data  # noqa: PLC0415

        if is_avro_schema_data(data):
            return InputFileType.Avro
    msg = _infer_input_type_error_message()
    raise Error(msg)


def _infer_input_type_error_message(*, parse_error: Exception | None = None) -> str:
    message = "Can't infer input file type from the input data."
    hint = "Please specify the input file type explicitly with --input-file-type option."
    if parse_error is None:
        return f"{message} {hint}"
    return f"{message} YAML parser error: {type(parse_error).__name__}: {parse_error}. {hint}"


_MIN_CSV_NON_EMPTY_LINES = 2


def _looks_like_csv_text(text: str) -> bool:
    comma_count: int | None = None
    matched_lines = 0
    for raw_line in text.splitlines():
        if not (line := raw_line.strip()):
            continue
        if (current_comma_count := line.count(",")) == 0:
            return False
        match comma_count:
            case None:
                comma_count = current_comma_count
            case _ if current_comma_count != comma_count:
                return False
        matched_lines += 1
    return matched_lines >= _MIN_CSV_NON_EMPTY_LINES


inferred_message = (
    "The input file type was determined to be: {}\nThis can be specified explicitly with the "
    "`--input-file-type` option."
)


def detect_xmlschema_version(source: Any) -> XMLSchemaVersion:
    """Detect XML Schema version from XSD 1.1 versioning attributes and constructs."""
    from datamodel_code_generator.parser.xmlschema import (  # noqa: PLC0415
        detect_xmlschema_version as _detect_xmlschema_version,
    )

    return _detect_xmlschema_version(source)


_LAZY_IMPORTS = {
    "clear_dynamic_models_cache": "datamodel_code_generator.dynamic",
    "detect_jsonschema_version": "datamodel_code_generator.parser.schema_version",
    "detect_openapi_version": "datamodel_code_generator.parser.schema_version",
    "generate_dynamic_models": "datamodel_code_generator.dynamic",
    "GenerateConfig": "datamodel_code_generator.config",
    "UnionMode": "datamodel_code_generator.enums",
    "CodeFormatter": "datamodel_code_generator.format",
    "DateClassType": "datamodel_code_generator._format_types",
    "DatetimeClassType": "datamodel_code_generator._format_types",
    "DEFAULT_FORMATTERS": "datamodel_code_generator._format_types",
    "Formatter": "datamodel_code_generator._format_types",
    "PythonVersion": "datamodel_code_generator._format_types",
    "PythonVersionMin": "datamodel_code_generator._format_types",
    "resolve_use_type_checking_imports": "datamodel_code_generator.format",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        import importlib  # noqa: PLC0415

        module = importlib.import_module(_LAZY_IMPORTS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "DEFAULT_FORMATTERS",
    "DEFAULT_SHARED_MODULE_NAME",
    "MAX_VERSION",
    "MIN_VERSION",
    "AliasGenerator",
    "AllExportsCollisionStrategy",
    "AllExportsScope",
    "AllOfClassHierarchy",
    "AllOfMergeMode",
    "AsyncAPIVersion",
    "ClassNameAffixScope",
    "CollapseRootModelsNameStrategy",
    "CustomFileHeaderMode",
    "DanglingRefWarning",
    "DateClassType",
    "DatetimeClassType",
    "DefaultPutDict",
    "DefaultValueType",
    "DefaultValueTypeWarning",
    "Error",
    "FieldTypeCollisionStrategy",
    "GeneratedModules",
    "GraphQLScope",
    "HTTPBackend",
    "InputFileType",
    "InputModelRefStrategy",
    "InvalidClassNameError",
    "InvalidFileFormatError",
    "JsonSchemaVersion",
    "LiteralType",
    "ModuleSplitMode",
    "NamingStrategy",
    "OpenAPIScope",
    "OpenAPIVersion",
    "ProtobufVersion",
    "PythonVersion",
    "PythonVersionMin",
    "ReadOnlyWriteOnlyModelType",
    "ReuseScope",
    "SchemaParseError",
    "SchemaValidatorType",
    "TargetPydanticVersion",
    "VersionMode",
    "XMLSchemaVersion",
    "clear_dynamic_models_cache",  # noqa: F822
    "detect_jsonschema_version",  # noqa: F822
    "detect_openapi_version",  # noqa: F822
    "detect_xmlschema_version",
    "enable_parsed_source_cache",
    "generate",
    "generate_dynamic_models",  # noqa: F822
]

__all__ += ["GenerateConfig"]
