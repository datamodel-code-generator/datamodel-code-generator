"""Main module for datamodel-code-generator CLI."""

from __future__ import annotations

import sys

match sys.argv:
    # Fast path for --version (avoid importing heavy modules)
    case [_, "--version" | "-V"]:
        from datamodel_code_generator import get_version

        sys.stdout.write(f"datamodel-codegen {get_version()}\n")
        sys.exit(0)
    # Fast path for --help (avoid importing heavy modules)
    case [_, "--help" | "-h"]:  # pragma: no cover
        from datamodel_code_generator.arguments import arg_parser

        arg_parser.print_help()
        sys.exit(0)

match sys.argv:
    case [_, "--output-format-json-schema", schema_output_name] if schema_output_name in {
        "config",
        "generation",
        "model-metadata",
        "structured-output",
    }:
        pass
    case [_, schema_output_option] if schema_output_option.startswith("--output-format-json-schema=") and (
        schema_output_name := schema_output_option.partition("=")[2]
    ) in {"config", "generation", "model-metadata", "structured-output"}:
        pass
    case _:
        schema_output_name = None

match schema_output_name:
    case "config":
        from datamodel_code_generator.json_config import json_config_json_schema

        sys.stdout.write(f"{json_config_json_schema()}\n")
        sys.exit(0)
    case "generation":  # pragma: no cover
        from datamodel_code_generator._structured_output import generation_output_json_schema

        sys.stdout.write(f"{generation_output_json_schema()}\n")
        sys.exit(0)
    case "model-metadata":
        from datamodel_code_generator.model_metadata import model_metadata_json_schema

        sys.stdout.write(f"{model_metadata_json_schema()}\n")
        sys.exit(0)
    case "structured-output":  # pragma: no cover
        from datamodel_code_generator._structured_output import structured_output_json_schema

        sys.stdout.write(f"{structured_output_json_schema()}\n")
        sys.exit(0)

# Fast path for Agent Skill installation (avoid importing generation dependencies)
if any(arg == "--install-skill" or arg.startswith("--install-skill=") for arg in sys.argv[1:]):  # pragma: no cover
    from datamodel_code_generator._agent_skill_cli import run_agent_skill_installer

    sys.exit(run_agent_skill_installer(sys.argv[1:]))

# Fast path for prompt helper outputs
if any(
    arg.startswith(("--generate-prompt", "--output-format-json-schema=")) or arg == "--output-format-json-schema"
    for arg in sys.argv[1:]
):  # pragma: no cover
    from datamodel_code_generator.arguments import arg_parser, namespace

    vars(namespace).clear()
    namespace.no_color = False
    arg_parser.parse_args(namespace=namespace)
    if namespace.output_format_json_schema == "generate-prompt":
        from datamodel_code_generator.prompt import generate_prompt_json_schema

        sys.stdout.write(f"{generate_prompt_json_schema()}\n")
        sys.exit(0)
    if namespace.output_format_json_schema == "config":
        from datamodel_code_generator.json_config import json_config_json_schema

        sys.stdout.write(f"{json_config_json_schema()}\n")
        sys.exit(0)
    if namespace.output_format_json_schema == "generation":
        from datamodel_code_generator._structured_output import generation_output_json_schema

        sys.stdout.write(f"{generation_output_json_schema()}\n")
        sys.exit(0)
    if namespace.output_format_json_schema == "model-metadata":
        from datamodel_code_generator.model_metadata import model_metadata_json_schema

        sys.stdout.write(f"{model_metadata_json_schema()}\n")
        sys.exit(0)
    if namespace.output_format_json_schema == "structured-output":
        from datamodel_code_generator._structured_output import structured_output_json_schema

        sys.stdout.write(f"{structured_output_json_schema()}\n")
        sys.exit(0)
    if namespace.output_format_json_schema is None and namespace.generate_prompt is not None:
        from datamodel_code_generator.prompt import generate_prompt

        help_text = arg_parser.format_help()
        prompt_output = generate_prompt(namespace, help_text, arg_parser)
        sys.stdout.write(f"{prompt_output}\n")
        sys.exit(0)

import difflib
import json
import os
import shlex
import shutil
import signal
import tempfile
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import redirect_stdout, suppress
from datetime import datetime, timezone
from enum import Enum, IntEnum
from functools import lru_cache
from keyword import iskeyword
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, Optional, TypeAlias, Union, cast
from urllib.parse import ParseResult, urlparse

from datamodel_code_generator import (
    _SINGLE_MODULE_OUTPUT_DIRECTORY_ERROR,
    AllExportsScope,
    ClassNameAffixScope,
    DataModelType,
    Error,
    InputFileType,
    InputModelRefStrategy,
    InvalidClassNameError,
    NamingStrategy,
    OpenAPIScope,
    ReuseScope,
    _validate_alias_generator,
    _validate_generation_path_conflicts,
    _validate_output_datetime_class,
    enable_debug_message,
    generate,
)
from datamodel_code_generator._format_types import Formatter, PythonVersion
from datamodel_code_generator.arguments import arg_parser, namespace
from datamodel_code_generator.deprecations import render_deprecations, warn_deprecated
from datamodel_code_generator.enums import StrictTypes
from datamodel_code_generator.util import load_toml


def __getattr__(name: str) -> Any:
    """Load the deferred config model without adding it to no-lock imports."""
    if name == "Config":
        return _get_config_class()
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


if TYPE_CHECKING:
    from argparse import Namespace
    from collections import defaultdict

    from datamodel_code_generator._publication import (
        PublicationAnchor as _PublicationAnchor,
    )
    from datamodel_code_generator._publication import (
        StagedFile as _StagedFile,
    )
    from datamodel_code_generator._publication import (
        StagingDirectory as _StagingDirectory,
    )
    from datamodel_code_generator._structured_output import (
        CheckDifferencePayload,
        CommandOutputKind,
        GeneratedFilePayload,
    )
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.json_config import JsonConfigFieldName, JsonConfigSource
    from datamodel_code_generator.validators import ModelValidators
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    Config = cast("Any", object)
    ValidatorsConfigValue: TypeAlias = Mapping[str, ModelValidators]
else:
    ValidatorsConfigValue: TypeAlias = Mapping[str, Any]

# Options that should be excluded from pyproject.toml config generation
EXCLUDED_CONFIG_OPTIONS: frozenset[str] = frozenset({
    "check",
    "diff_against",
    "generate_pyproject_config",
    "generate_cli_command",
    "generate_prompt",
    "ignore_pyproject",
    "install_skill",
    "skill_scope",
    "overwrite_skill",
    "profile",
    "job",
    "all_jobs",
    "version",
    "help",
    "debug",
    "no_color",
    "output_format",
    "output_format_json_schema",
    "disable_warnings",
    "list_deprecations",
    "list_experimental",
    "watch",
    "watch_delay",
    "generate_server",
    "target_config",
    "target_output",
    "diagnostics_json",
})

ORIGINAL_FIELD_NAME_DELIMITER_ERROR = "`--original-field-name-delimiter` can not be used without `--snake-case-field`."
SENSITIVE_COMMAND_OPTIONS: frozenset[str] = frozenset({"--http-headers", "--http-query-parameters"})
REDACTED_COMMAND_ARGUMENT = "<redacted>"
BATCH_UNSAFE_CLI_FIELDS: frozenset[str] = frozenset({"input", "input_model", "output", "url"})
BATCH_COMMAND_ONLY_CONFIG_FIELDS: frozenset[str] = frozenset({"list_deprecations", "list_experimental"})
BATCH_CONFIG_CONTEXT_FIELDS: frozenset[str] = frozenset({"use_annotated", "use_specialized_enum"})
BATCH_OUTER_CONFIG_FIELDS: frozenset[str] = frozenset({"watch", "watch_delay"})
_TARGET_OPTIONS: tuple[tuple[str, str], ...] = (
    ("target_config", "--target-config"),
    ("target_output", "--target-output"),
    ("diagnostics_json", "--diagnostics-json"),
)
_TARGET_EXCLUSIVE: tuple[tuple[str, str], ...] = (
    ("install_skill", "--install-skill"),
    ("generate_prompt", "--generate-prompt"),
    ("generate_pyproject_config", "--generate-pyproject-config"),
    ("generate_cli_command", "--generate-cli-command"),
    ("output_format_json_schema", "--output-format-json-schema"),
    ("list_deprecations", "--list-deprecations"),
    ("list_experimental", "--list-experimental"),
)


class Exit(IntEnum):
    """Exit reasons."""

    OK = 0
    DIFF = 1
    ERROR = 2
    KeyboardInterrupt = 3


def sig_int_handler(_: int, __: Any) -> None:  # pragma: no cover
    """Handle SIGINT signal gracefully."""
    sys.exit(Exit.OK)


def _redact_command_args(args: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    redact_values = False
    for arg in args:
        match arg:
            case option if option in SENSITIVE_COMMAND_OPTIONS:
                redacted.append(option)
                redact_values = True
            case option_value if (option := option_value.partition("="))[1] and option[0] in SENSITIVE_COMMAND_OPTIONS:
                redacted.append(f"{option[0]}={REDACTED_COMMAND_ARGUMENT}")
                redact_values = False
            case option if redact_values and option.startswith("-"):
                redacted.append(option)
                redact_values = False
            case _ if redact_values:
                redacted.append(REDACTED_COMMAND_ARGUMENT)
            case _:
                redacted.append(arg)
    return redacted


def _command_header(args: Sequence[str]) -> str:
    return shlex.join(["datamodel-codegen", *_redact_command_args(args)])


signal.signal(signal.SIGINT, sig_int_handler)


def _get_black() -> Any:
    from datamodel_code_generator.format import _get_black as get_black  # noqa: PLC0415

    return get_black()


def is_supported_in_black(python_version: PythonVersion) -> bool:
    """Return whether the installed black supports the target Python version."""
    from datamodel_code_generator.format import is_supported_in_black as supported  # noqa: PLC0415

    return supported(python_version)


def is_url(ref: str) -> bool:
    """Check if a reference string is a URL (HTTP, HTTPS, or file scheme)."""
    # Keep this local so importing the CLI does not import reference.py and its model stack.
    return ref.startswith(("https://", "http://", "file://"))


_HttpKeyValuePair: TypeAlias = tuple[str, str]
_HttpKeyValueInput: TypeAlias = str | _HttpKeyValuePair
_HttpSeparator: TypeAlias = Literal[":", "="]
_HttpItemErrorName: TypeAlias = Literal["http header", "http query parameter"]
_HttpValueErrorName: TypeAlias = Literal["http_headers", "http_query_parameters"]
_RawConfigValue: TypeAlias = (
    str
    | bool
    | int
    | float
    | Path
    | ParseResult
    | Enum
    | Sequence[str]
    | Sequence[StrictTypes]
    | Sequence[OpenAPIScope]
    | Sequence[tuple[str, str]]
    | Mapping[str, Any]
    | Mapping[str, str]
    | Mapping[str, str | list[str]]
)


def _validate_http_key_value_options(
    value: Any,
    *,
    separator: _HttpSeparator,
    item_error_name: _HttpItemErrorName,
    value_error_name: _HttpValueErrorName,
) -> list[_HttpKeyValuePair] | None:
    if value is None:  # pragma: no cover
        return None

    def validate_each_item(each_item: _HttpKeyValueInput) -> _HttpKeyValuePair:
        if isinstance(each_item, str):  # pragma: no cover
            try:
                field_name, field_value = each_item.split(separator, maxsplit=1)
                return field_name, field_value.lstrip()
            except ValueError as exc:
                msg = f"Invalid {item_error_name}: {each_item!r}"
                raise Error(msg) from exc
        return each_item  # pragma: no cover

    if isinstance(value, list):
        return [validate_each_item(cast("_HttpKeyValueInput", each_item)) for each_item in value]
    msg = f"Invalid {value_error_name} value: {value!r}"  # pragma: no cover
    raise Error(msg)  # pragma: no cover


@lru_cache(maxsize=1)
def _get_config_class() -> type[Config]:
    from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator  # noqa: PLC0415
    from typing_extensions import Self  # noqa: PLC0415

    from datamodel_code_generator.base_config import BaseGenerateConfig  # noqa: PLC0415

    class Config(BaseGenerateConfig):  # noqa: PLR0904
        """Configuration model for code generation."""

        model_config = ConfigDict(
            extra="ignore",
            arbitrary_types_allowed=True,
            protected_namespaces=(),
            defer_build=True,
        )

        def get(self, item: str) -> Any:  # pragma: no cover
            """Get attribute value by name."""
            return getattr(self, item)

        def __getitem__(self, item: str) -> Any:  # pragma: no cover
            """Get item by key."""
            return self.get(item)

        @classmethod
        def get_fields(cls) -> dict[str, Any]:
            """Get model fields."""
            return cls.model_fields

        @field_validator(
            "aliases",
            "serialization_aliases",
            "extra_template_data",
            "custom_formatters_kwargs",
            "default_values",
            "base_class_map",
            "model_name_map",
            "enum_field_as_literal_map",
            "duplicate_name_suffix",
            "import_overrides",
            "type_overrides",
            mode="before",
        )
        @classmethod
        def validate_json_config(cls, value: Any, info: ValidationInfo) -> Any:
            """Load and validate JSON configuration values from inline JSON or file paths."""
            if value is None:  # pragma: no cover
                return value
            from datamodel_code_generator.json_config import JsonConfigError, load_json_config_field  # noqa: PLC0415

            field_name: JsonConfigFieldName = cast("JsonConfigFieldName", info.field_name)
            try:
                json_config_source: JsonConfigSource = cast("JsonConfigSource", value)
                return load_json_config_field(field_name, json_config_source)
            except JsonConfigError as e:
                raise Error(str(e)) from e

        @field_validator("validators", mode="before")
        @classmethod
        def validate_validators_config(cls, value: Any) -> Any:
            """Validate validators lazily only when the option is present."""
            if value is None:
                return None

            from datamodel_code_generator.json_config import JsonConfigError, load_json_config_field  # noqa: PLC0415

            try:
                return load_json_config_field("validators", cast("JsonConfigSource", value))
            except JsonConfigError as e:
                raise Error(str(e)) from e

        @field_validator(
            "input",
            "output",
            "diff_against",
            "custom_template_dir",
            "custom_file_header_path",
            "http_local_ref_path",
            mode="before",
        )
        @classmethod
        def validate_path(cls, value: Any) -> Path | None:
            """Validate and resolve path."""
            if value is None or isinstance(value, Path):
                return value  # pragma: no cover
            return Path(value).expanduser().resolve()

        @field_validator("url", mode="before")
        @classmethod
        def validate_url(cls, value: Any) -> ParseResult | None:
            """Validate and parse URL."""
            if isinstance(value, str) and is_url(value):  # pragma: no cover
                return urlparse(value)
            if value is None:  # pragma: no cover
                return None
            msg = f"Unsupported URL scheme. Supported: http, https, file. --input={value}"  # pragma: no cover
            raise Error(msg)  # pragma: no cover

        # Pydantic 1.5.1 doesn't support each_item=True correctly
        @field_validator("http_headers", mode="before")
        @classmethod
        def validate_http_headers(cls, value: Any) -> list[tuple[str, str]] | None:
            """Validate HTTP headers."""
            return _validate_http_key_value_options(
                value,
                separator=":",
                item_error_name="http header",
                value_error_name="http_headers",
            )

        @field_validator("http_query_parameters", mode="before")
        @classmethod
        def validate_http_query_parameters(cls, value: Any) -> list[tuple[str, str]] | None:
            """Validate HTTP query parameters."""
            return _validate_http_key_value_options(
                value,
                separator="=",
                item_error_name="http query parameter",
                value_error_name="http_query_parameters",
            )

        @model_validator(mode="before")
        @classmethod
        def split_additional_imports(cls, values: dict[str, Any]) -> dict[str, Any]:
            """Validate and split additional imports."""
            match values.get("additional_imports"):
                case str() as additional_imports:
                    values["additional_imports"] = [
                        import_path for item in additional_imports.split(",") if (import_path := item.strip())
                    ]
            return values

        @model_validator(mode="before")
        @classmethod
        def validate_custom_formatters(cls, values: dict[str, Any]) -> dict[str, Any]:
            """Validate and split custom formatters."""
            custom_formatters = values.get("custom_formatters")
            if custom_formatters is not None:
                values["custom_formatters"] = custom_formatters.split(",")
            return values

        @model_validator(mode="before")
        @classmethod
        def validate_naming_strategy_migration(cls, values: dict[str, Any]) -> dict[str, Any]:
            """Migrate deprecated --parent-scoped-naming to --naming-strategy."""
            if values.get("parent_scoped_naming") and not values.get("naming_strategy"):
                values["naming_strategy"] = NamingStrategy.ParentPrefixed
                warn_deprecated("cli.parent-scoped-naming", stacklevel=2)
            return values

        @model_validator(mode="before")
        @classmethod
        def validate_allow_extra_fields_migration(cls, values: dict[str, Any]) -> dict[str, Any]:
            """Migrate deprecated --allow-extra-fields to --extra-fields."""
            if values.get("allow_extra_fields") and not values.get("extra_fields"):
                values["extra_fields"] = "allow"
                warn_deprecated("cli.allow-extra-fields", stacklevel=2)
            return values

        @model_validator(mode="before")
        @classmethod
        def validate_class_decorators(cls, values: dict[str, Any]) -> dict[str, Any]:
            """Validate and split class decorators, adding @ prefix if missing."""
            class_decorators = values.get("class_decorators")
            if class_decorators is not None:
                decorators = []
                for raw_decorator in class_decorators.split(","):
                    stripped = raw_decorator.strip()
                    if stripped:
                        if not stripped.startswith("@"):
                            stripped = f"@{stripped}"
                        decorators.append(stripped)
                values["class_decorators"] = decorators
            return values

        @model_validator(mode="before")
        @classmethod
        def validate_external_ref_mapping(cls, values: dict[str, Any]) -> dict[str, Any]:
            """Parse external_ref_mapping from list of KEY=VALUE strings to dict."""
            raw = values.get("external_ref_mapping")
            if raw is not None and isinstance(raw, list):
                mapping: dict[str, str] = {}
                for item in raw:
                    if not isinstance(item, str) or "=" not in item:
                        msg = (
                            f"Invalid --external-ref-mapping format: {item!r}. "
                            "Expected FILE_PATH=PYTHON_PACKAGE (e.g., '../common/schema.yaml=mypackage.models')"
                        )
                        raise Error(msg)
                    file_path, python_package = item.split("=", maxsplit=1)
                    file_path = file_path.strip()
                    python_package = python_package.strip()
                    if not file_path or not python_package:
                        msg = (
                            f"Invalid --external-ref-mapping format: {item!r}. "
                            "Both FILE_PATH and PYTHON_PACKAGE must be non-empty."
                        )
                        raise Error(msg)
                    mapping[file_path] = python_package
                values["external_ref_mapping"] = mapping
            return values

        __validate_custom_file_header_err: ClassVar[str] = (
            "`--custom_file_header_path` can not be used with `--custom_file_header`."
        )
        __validate_keyword_only_err: ClassVar[str] = (
            f"`--keyword-only` requires `--target-python-version` {PythonVersion.PY_310.value} or higher."
        )

        __validate_all_exports_collision_strategy_err: ClassVar[str] = (
            "`--all-exports-collision-strategy` can only be used with `--all-exports-scope=recursive`."
        )

        @model_validator(mode="after")
        def validate_output_datetime_class(self: Self) -> Self:
            """Validate output datetime class compatibility."""
            _validate_output_datetime_class(self.output_model_type, self.output_datetime_class)
            return self

        __validate_original_field_name_delimiter_err: ClassVar[str] = ORIGINAL_FIELD_NAME_DELIMITER_ERROR

        @model_validator(mode="after")
        def validate_alias_generator(self: Self) -> Self:
            """Validate alias generator compatibility."""
            _validate_alias_generator(self.output_model_type, self.alias_generator)
            return self

        def validate_original_field_name_delimiter(self) -> None:
            """Validate original field name delimiter requires snake case after preset merging."""
            if self.original_field_name_delimiter is not None and not self.snake_case_field:
                raise Error(self.__validate_original_field_name_delimiter_err)

        @model_validator(mode="after")
        def validate_custom_file_header(self: Self) -> Self:
            """Validate custom file header options are mutually exclusive."""
            if self.custom_file_header is not None and self.custom_file_header_path is not None:
                raise Error(self.__validate_custom_file_header_err)
            return self

        @model_validator(mode="after")
        def validate_keyword_only(self: Self) -> Self:
            """Validate keyword-only compatibility with target Python version."""
            output_model_type: DataModelType = self.output_model_type
            python_target: PythonVersion = self.target_python_version
            if (
                self.keyword_only
                and output_model_type == DataModelType.DataclassesDataclass
                and not python_target.has_kw_only_dataclass
            ):
                raise Error(self.__validate_keyword_only_err)  # pragma: no cover
            return self

        @model_validator(mode="after")
        def validate_root(self: Self) -> Self:
            """Validate root model configuration."""
            if self.use_annotated:
                self.field_constraints = True
            return self

        @model_validator(mode="after")
        def validate_all_exports_collision_strategy(self: Self) -> Self:
            """Validate all_exports_collision_strategy requires recursive scope."""
            if self.all_exports_collision_strategy is not None and self.all_exports_scope != AllExportsScope.Recursive:
                raise Error(self.__validate_all_exports_collision_strategy_err)
            return self

        @field_validator("input_model", mode="before")
        @classmethod
        def coerce_input_model_to_list(cls, v: str | list[str] | None) -> list[str] | None:
            """Convert string input_model to list for backwards compatibility."""
            if isinstance(v, str):
                return [v]
            return v

        @field_validator("class_name_affix_scope", mode="before")
        @classmethod
        def validate_class_name_affix_scope(cls, v: str | ClassNameAffixScope | None) -> ClassNameAffixScope:
            """Convert string to ClassNameAffixScope enum."""
            if v is None:  # pragma: no cover
                return ClassNameAffixScope.All
            if isinstance(v, str):
                return ClassNameAffixScope(v)
            return v  # pragma: no cover

        @field_validator("schema_validator_base_class_name")
        @classmethod
        def validate_schema_validator_base_class_name(cls, v: str | None) -> str | None:
            """Validate schema validator base class name."""
            if v is None:  # pragma: no cover
                return v
            if not v.isidentifier() or iskeyword(v):
                msg = f"--schema-validator-base-class-name '{v}' is not a valid Python identifier"
                raise Error(msg)
            return v

        input: Optional[Union[Path, str]] = None  # noqa: UP007, UP045
        input_model: Optional[list[str]] = None  # noqa: UP045
        input_model_ref_strategy: Optional[InputModelRefStrategy] = None  # noqa: UP045
        input_file_type: InputFileType = InputFileType.Auto
        output_model_type: DataModelType = DataModelType.PydanticV2BaseModel
        output: Optional[Path] = None  # noqa: UP045
        check: bool = False
        diff_against: Optional[Path] = None  # noqa: UP045
        repair_invalid_dotted_stdout: bool = Field(default=False, exclude=True)
        forced_invalid_dotted_stdout_repair_modules: tuple[tuple[str, ...], ...] = Field(default=(), exclude=True)
        debug: bool = False
        disable_warnings: bool = False
        extra_template_data: Mapping[str, dict[str, Any]] | None = None
        validators: Optional[ValidatorsConfigValue] = None  # noqa: UP045
        aliases: Optional[Mapping[str, str | list[str]]] = None  # noqa: UP045
        serialization_aliases: Optional[Mapping[str, str]] = None  # noqa: UP045
        default_values: Optional[Mapping[str, Any]] = None  # noqa: UP045
        use_default: bool = False
        force_optional: bool = False
        url: Optional[ParseResult] = None  # noqa: UP045
        strict_types: list[StrictTypes] = Field(default_factory=list)
        openapi_scopes: Optional[list[OpenAPIScope]] = Field(default_factory=lambda: [OpenAPIScope.Schemas])  # noqa: UP045
        custom_formatters_kwargs: Optional[dict[str, str]] = None  # noqa: UP045
        watch: bool = False
        watch_delay: float = 0.5
        list_deprecations: Optional[str] = None  # noqa: UP045
        list_experimental: Optional[str] = None  # noqa: UP045

        def merge_args(self, args: Namespace) -> None:
            """Merge command-line arguments into config."""
            set_args = _prepare_cli_config_args(_explicit_config_args(args))
            explicit_input_sources = {
                field_name for field_name in ("input", "url", "input_model") if field_name in set_args
            }

            if explicit_input_sources:
                for field_name in {"input", "url", "input_model"} - explicit_input_sources:
                    setattr(self, field_name, None)

            parsed_args = Config.model_validate(set_args)
            # These switches are mutually exclusive at the command line, but a
            # pyproject value has already been applied to ``self``. An explicit
            # CLI mode must replace (rather than combine with) that lower
            # precedence mode.
            if "update_lock" in set_args:
                self.locked = False
            elif "locked" in set_args:
                self.update_lock = False
            for field_name in set_args:
                setattr(self, field_name, getattr(parsed_args, field_name))

    Config.__qualname__ = "Config"
    globals()["Config"] = Config
    return Config


def _explicit_config_args(args: Namespace) -> dict[str, _RawConfigValue]:
    """Return command-line values that explicitly target Config fields."""
    config_class = _get_config_class()
    return {field: value for field in config_class.get_fields() if (value := getattr(args, field, None)) is not None}


def _prepare_cli_config_args(set_args: Mapping[str, _RawConfigValue]) -> dict[str, _RawConfigValue]:
    """Apply validation-time CLI config values before merging."""
    if not set_args:
        return {}

    prepared_args = dict(set_args)
    if prepared_args.get("use_annotated"):
        prepared_args["field_constraints"] = True

    if prepared_args.get("use_type_alias_type"):
        prepared_args["use_type_alias"] = True

    return prepared_args


def _create_config(
    pyproject_config: Mapping[str, Any],
    cli_config_args: Mapping[str, _RawConfigValue],
) -> Config:
    """Create the final CLI config while preserving pyproject/CLI validation order."""
    config_class = _get_config_class()
    if not pyproject_config:
        return config_class.model_validate(_prepare_cli_config_args(cli_config_args))

    from argparse import Namespace as ArgNamespace  # noqa: PLC0415

    config = config_class.model_validate(pyproject_config)
    cli_namespace = ArgNamespace(**cli_config_args)
    config.merge_args(cli_namespace)
    return config


def _apply_implicit_cli_config_values(
    config: Config,
    pyproject_config: Mapping[str, _RawConfigValue],
    cli_config_args: Mapping[str, _RawConfigValue],
) -> None:
    """Apply CLI defaults after pyproject, command-line, and preset values have merged."""
    explicit_fields = {field.replace("-", "_") for field in pyproject_config} | set(cli_config_args)
    if config.output_model_type is DataModelType.MsgspecStruct and "use_annotated" not in explicit_fields:
        config.use_annotated = True

    if "field_constraints" in explicit_fields:
        return
    config.field_constraints = config.use_annotated


def _apply_preset(
    config: Config,
    pyproject_config: Mapping[str, _RawConfigValue],
    cli_config_args: Mapping[str, _RawConfigValue],
) -> None:
    """Apply the selected preset to the final CLI/pyproject config."""
    preset_from_cli = "preset" in cli_config_args
    if preset_from_cli:
        preset_value = cli_config_args["preset"]
        if not isinstance(preset_value, str):  # pragma: no cover
            msg = f"--preset must be a string, got {preset_value!r}"
            raise Error(msg)
        preset_name = preset_value
    else:
        preset_name = config.preset
    if preset_name is None:
        return

    explicit_fields = set(cli_config_args) if preset_from_cli else set(pyproject_config) | set(cli_config_args)
    explicit_fields.discard("preset")

    from datamodel_code_generator.preset import (  # noqa: PLC0415
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
            explicit_fields=explicit_fields,
        )
    except PresetError as e:
        raise Error(str(e)) from e

    if preset_config.target_python_version is not None:
        config.target_python_version = preset_config.target_python_version

    for item in preset_config.items:
        setattr(config, item.field_name, item.applied_value)

    if preset_config.force_field_constraints:
        config.field_constraints = True


def _validate_final_config(config: Config) -> None:
    """Validate invariants that depend on CLI, pyproject, and preset merging."""
    config.validate_original_field_name_delimiter()


def _extract_additional_imports(extra_template_data: defaultdict[str, dict[str, Any]]) -> list[str]:
    """Extract additional_imports from extra_template_data entries."""
    additional_imports: list[str] = []
    for type_data in extra_template_data.values():
        if "additional_imports" in type_data:
            imports = type_data.pop("additional_imports")
            if isinstance(imports, str):
                if imports.strip():  # pragma: no branch
                    additional_imports.append(imports.strip())
            elif isinstance(imports, list):  # pragma: no branch
                additional_imports.extend(item.strip() for item in imports if isinstance(item, str) and item.strip())
    if not additional_imports:
        return additional_imports

    from datamodel_code_generator.base_config import _validate_additional_import_paths  # noqa: PLC0415

    return _validate_additional_import_paths(additional_imports) or []


def _resolve_profile_extends(
    profiles: Mapping[str, Any],
    profile_name: str,
    visited: set[str] | None = None,
) -> dict[str, Any]:
    """Resolve profile inheritance via extends key."""
    if visited is None:
        visited = set()

    if profile_name in visited:
        chain = " -> ".join(visited) + f" -> {profile_name}"
        msg = f"Circular extends detected: {chain}"
        raise Error(msg)

    if profile_name not in profiles:
        available = list(profiles.keys()) if profiles else "none"
        msg = f"Extended profile '{profile_name}' not found in pyproject.toml. Available profiles: {available}"
        raise Error(msg)

    visited.add(profile_name)
    profile = profiles[profile_name]
    if not isinstance(profile, Mapping):
        msg = f"Profile '{profile_name}' must be a table"
        raise Error(msg)
    extends = profile.get("extends")

    if not extends:
        return dict(profile.items())

    if not isinstance(extends, str | list) or (
        isinstance(extends, list) and not all(isinstance(parent, str) for parent in extends)
    ):
        msg = f"Profile '{profile_name}' extends must be a string or list of strings"
        raise Error(msg)
    parents = [extends] if isinstance(extends, str) else extends
    result: dict[str, Any] = {}

    for parent in parents:
        if parent == profile_name:
            msg = f"Profile '{profile_name}' cannot extend itself"
            raise Error(msg)
        parent_config = _resolve_profile_extends(profiles, parent, visited.copy())
        result.update(parent_config)

    result.update({k: v for k, v in profile.items() if k != "extends"})
    return result


def _find_datamodel_codegen_project_config_with_path(source: Path) -> tuple[Path, Mapping[str, Any]] | None:
    """Return the closest datamodel-codegen TOML table and its pyproject path."""
    current_path = source
    while current_path != current_path.parent:
        pyproject_path = current_path / "pyproject.toml"
        if pyproject_path.is_file():
            pyproject_toml = load_toml(pyproject_path)
            tool_config = pyproject_toml.get("tool", {}).get("datamodel-codegen")
            if isinstance(tool_config, Mapping):
                return pyproject_path, tool_config

        if (current_path / ".git").exists():  # pragma: no cover
            break
        current_path = current_path.parent
    return None


def _get_pyproject_toml_config_with_path(
    source: Path,
    profile: str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Return resolved project config together with the project file that supplied it."""
    if (project_config := _find_datamodel_codegen_project_config_with_path(source)) is not None:
        pyproject_path, tool_config = project_config
        base_config: dict[str, Any] = {
            key: value for key, value in tool_config.items() if key not in {"jobs", "profiles"}
        }

        if profile:
            profiles = tool_config.get("profiles", {})
            if not isinstance(profiles, Mapping):
                msg = "[tool.datamodel-codegen.profiles] must be a table"
                raise Error(msg)
            if profile not in profiles:
                available = list(profiles.keys()) if profiles else "none"
                msg = f"Profile '{profile}' not found in pyproject.toml. Available profiles: {available}"
                raise Error(msg)
            resolved_profile = _resolve_profile_extends(profiles, profile)
            base_config.update(resolved_profile)

        return _normalize_pyproject_config(base_config), pyproject_path

    if profile:
        msg = f"Profile '{profile}' requested but no [tool.datamodel-codegen] section found in pyproject.toml"
        raise Error(msg)

    return {}, None


class JobPlan(NamedTuple):
    """An isolated configuration ready for one named batch job."""

    name: str
    config: Config
    pyproject_context: dict[str, Any]
    raw_config: dict[str, Any]
    cli_config_args: dict[str, _RawConfigValue]
    pyproject_path: Path
    resolved_output_root: Path | None
    resolved_output_parent: Path | None
    resolved_model_metadata_root: Path | None
    resolved_model_metadata_parent: Path | None


class BatchPlan(NamedTuple):
    """A selected job set plus CLI/base-level scheduler settings."""

    jobs: tuple[JobPlan, ...]
    watch: bool
    watch_delay: float
    pyproject_path: Path


class _StagedJobPlan(NamedTuple):
    """One batch job with its generated artifacts redirected to staging."""

    plan: JobPlan
    config: Config
    output: Path | None
    staged_output: Path | None
    resolved_output_root: Path | None
    model_metadata: Path | None
    staged_model_metadata: Path | None
    resolved_model_metadata_root: Path | None
    output_anchor: _PublicationAnchor | None
    model_metadata_anchor: _PublicationAnchor | None
    staging_contexts: tuple[tempfile.TemporaryDirectory[str], ...]


class _RemoteLockPlan(NamedTuple):
    """The effective remote-lock policy for one resolved generation config."""

    path: Path
    canonical_path: Path
    literal_path: Path
    policy: Literal["inactive", "verify", "locked", "update"]

    @property
    def active(self) -> bool:
        return self.policy != "inactive"


def _remote_lock_plan(config: Config, pyproject_path: Path | None) -> _RemoteLockPlan:
    """Resolve the project-relative default lock path without importing lock machinery."""
    default_parent = pyproject_path.parent if pyproject_path is not None else Path.cwd()
    path = config.lockfile or default_parent / "datamodel-codegen.lock"
    literal_path = Path(os.path.abspath(path.expanduser()))  # noqa: PTH100
    canonical_path = literal_path.resolve(strict=False)
    policy: Literal["inactive", "verify", "locked", "update"]
    if config.update_lock:
        policy = "update"
    elif config.locked:
        policy = "locked"
    elif canonical_path.is_file():
        policy = "verify"
    else:
        policy = "inactive"
    return _RemoteLockPlan(canonical_path, canonical_path, literal_path, policy)


def _paths_alias_or_overlap(first: Path, second: Path) -> bool:
    """Treat hard links and parent/child artifacts as one unsafe publication target."""
    return _paths_overlap_or_samefile(first, second)


def _is_read_only_remote_lock_policy(policy: _RemoteLockPlan) -> bool:
    """Return whether a lock policy never replaces the lock artifact."""
    return policy.policy in {"verify", "locked"}


def _validate_remote_lock_policies(
    planned_entries: Sequence[tuple[tuple[str, Config, Path | None], _RemoteLockPlan]],
) -> None:
    """Reject ambiguous aliases and incompatible shared lock policies."""
    for index, ((first_name, _, _), first_plan) in enumerate(planned_entries):
        for (second_name, _, _), second_plan in planned_entries[index + 1 :]:
            if first_plan.canonical_path == second_plan.canonical_path:
                if not first_plan.active and not second_plan.active:
                    continue
                both_read_only = _is_read_only_remote_lock_policy(first_plan) and _is_read_only_remote_lock_policy(
                    second_plan
                )
                if first_plan.literal_path != second_plan.literal_path and not both_read_only:
                    msg = (
                        f"Remote lock paths for '{first_name}' and '{second_name}' are aliases with ambiguous "
                        f"replacement semantics: {first_plan.literal_path} and {second_plan.literal_path}"
                    )
                    raise Error(msg)
                if first_plan.policy != second_plan.policy and not both_read_only:
                    msg = (
                        f"Remote lock policy conflict for {first_plan.canonical_path}: "
                        f"'{first_name}' uses {first_plan.policy} and '{second_name}' uses {second_plan.policy}"
                    )
                    raise Error(msg)
                continue
            if _paths_alias_or_overlap(first_plan.canonical_path, second_plan.canonical_path):
                msg = (
                    f"Remote lock paths for '{first_name}' and '{second_name}' overlap: "
                    f"{first_plan.canonical_path} and {second_plan.canonical_path}"
                )
                raise Error(msg)


def _validate_remote_lock_artifacts(
    planned_entries: Sequence[tuple[tuple[str, Config, Path | None], _RemoteLockPlan]],
) -> None:
    """Reject locks that overlap any source or generated artifact."""
    artifacts: list[tuple[str, str, Path]] = []
    for (name, config, _), _plan in planned_entries:
        for kind, path in (
            ("input", config.input if isinstance(config.input, Path) else None),
            ("diff input", config.diff_against),
            ("output", config.output),
            ("model metadata", config.emit_model_metadata),
        ):
            if path is not None:
                artifacts.append((name, kind, path.expanduser().resolve(strict=False)))
    for (name, _, _), plan in planned_entries:
        if not plan.active:
            continue
        for artifact_name, artifact_kind, artifact_path in artifacts:
            if not _paths_alias_or_overlap(plan.canonical_path, artifact_path):
                continue
            if (
                artifact_kind == "input"
                and artifact_path.is_dir()
                and plan.canonical_path.is_relative_to(artifact_path)
            ):
                msg = f"Remote lock path must not be inside an input directory: {plan.canonical_path}"
                raise Error(msg)
            msg = (
                f"Remote lock for '{name}' ({plan.canonical_path}) overlaps {artifact_kind} "
                f"for '{artifact_name}': {artifact_path}"
            )
            raise Error(msg)


def _validate_remote_lock_preflight(
    entries: Sequence[tuple[str, Config, Path | None]],
    plans: Sequence[_RemoteLockPlan],
) -> None:
    """Reject all lock/artifact aliases before opening a lock or fetching a schema."""
    planned_entries = tuple(zip(entries, plans, strict=True))
    _validate_remote_lock_policies(planned_entries)
    _validate_remote_lock_artifacts(planned_entries)


class _RemoteLockTransaction:
    """One command or one watch-cycle set of shared remote lock collectors."""

    def __init__(
        self,
        collectors: Mapping[Path, Any],
        anchors: Mapping[Path, _PublicationAnchor],
        staging_contexts: Mapping[Path, _StagingDirectory],
        publishable_paths: set[Path],
    ) -> None:
        self._collectors = dict(collectors)
        self._anchors = dict(anchors)
        self._staging_contexts = dict(staging_contexts)
        self._publishable_paths = publishable_paths

    @classmethod
    def open(
        cls,
        entries: Sequence[tuple[str, Config, Path | None]],
        plans: Sequence[_RemoteLockPlan],
    ) -> _RemoteLockTransaction | None:
        """Preflight and lazily open one collector per canonical effective lock path."""
        plans = tuple(plans)
        if len(entries) != len(plans):  # pragma: no cover - all callers create plans from entries
            msg = "Remote lock plan count does not match its generation entries"
            raise Error(msg)
        if not any(plan.active for plan in plans):
            return None
        _validate_remote_lock_preflight(entries, plans)
        from datamodel_code_generator.remote_lock import RemoteLockError, RemoteReferenceLock  # noqa: PLC0415

        collectors: dict[Path, Any] = {}
        anchors: dict[Path, _PublicationAnchor] = {}
        staging_contexts: dict[Path, _StagingDirectory] = {}
        publishable_paths = {
            plan.canonical_path
            for (_, config, _), plan in zip(entries, plans, strict=True)
            if plan.policy == "update" and not config.check
        }
        try:
            for plan in plans:
                if not plan.active or plan.canonical_path in collectors:
                    continue
                collectors[plan.canonical_path] = RemoteReferenceLock.open(
                    plan.path,
                    update=plan.policy == "update",
                    locked=plan.policy in {"verify", "locked"},
                )
                if plan.canonical_path in publishable_paths:
                    from datamodel_code_generator._publication import (  # noqa: PLC0415
                        StagingDirectory,
                        publication_anchor,
                    )

                    anchor = publication_anchor(plan.canonical_path.parent)
                    anchors[plan.canonical_path] = anchor
                    staging_contexts[plan.canonical_path] = StagingDirectory.create(
                        anchor,
                        prefix=".datamodel-codegen-lock-",
                    )
        except (OSError, RemoteLockError) as exc:
            from datamodel_code_generator._publication import close_anchor  # noqa: PLC0415

            for context in staging_contexts.values():
                with suppress(OSError):
                    context.cleanup()
            for anchor in anchors.values():
                with suppress(OSError):
                    close_anchor(anchor)
            raise Error(str(exc)) from exc
        return cls(collectors, anchors, staging_contexts, publishable_paths)

    def collector_for(self, plan: _RemoteLockPlan) -> Any | None:
        """Return the collector selected by an immutable preflight plan."""
        return self._collectors.get(plan.canonical_path)

    def staged_files(self) -> tuple[_StagedFile, ...]:
        """Stage each updating lock once for the common publication journal."""
        files: list[_StagedFile] = []
        try:
            for path, collector in self._collectors.items():
                if path not in self._publishable_paths:
                    continue
                staging_context = self._staging_contexts[path]
                staged_file = cast("_StagedFile", collector.stage(staging_context))
                files.append(staged_file._replace(anchor=self._anchors[path]))
        except Exception as exc:
            with suppress(OSError):
                self.discard()
            msg = f"Unable to stage remote lock update: {exc}"
            raise Error(msg) from exc
        return tuple(files)

    def mark_committed(self) -> None:
        """Commit collector state after publication; final cleanup remains owned by the caller."""
        for collector in self._collectors.values():
            collector.mark_committed()

    def discard(self) -> None:
        """Discard pending lock staging and release every transaction resource."""
        cleanup_error: OSError | None = None
        for collector in self._collectors.values():
            try:
                collector.discard_stage()
            except OSError as exc:  # noqa: PERF203 - every collector must get its cleanup opportunity
                cleanup_error = cleanup_error or exc
        try:
            self._close_anchors()
        except OSError as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise cleanup_error

    def _close_anchors(self) -> None:
        """Release private staging and update-only destination handles exactly once."""
        from datamodel_code_generator._publication import close_anchor  # noqa: PLC0415

        cleanup_error: OSError | None = None
        for context in self._staging_contexts.values():
            try:
                context.cleanup()
            except OSError as exc:  # noqa: PERF203 - every staged lock must be cleaned before reporting failure
                cleanup_error = cleanup_error or exc
        self._staging_contexts.clear()
        for anchor in self._anchors.values():
            try:
                close_anchor(anchor)
            except OSError as exc:  # noqa: PERF203 - every anchor must be released before reporting failure
                cleanup_error = cleanup_error or exc
        self._anchors.clear()
        if cleanup_error is not None:
            raise cleanup_error


class _UnresolvedRemoteLocks:
    """Private marker that lets nested batch calls preserve a no-lock snapshot."""


_UNRESOLVED_REMOTE_LOCKS = _UnresolvedRemoteLocks()


def _normalize_pyproject_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Convert TOML option spelling to Config field spelling."""
    normalized = {key.replace("-", "_"): value for key, value in config.items()}
    if "capitalize_enum_members" in normalized and "capitalise_enum_members" not in normalized:  # pragma: no cover
        normalized["capitalise_enum_members"] = normalized.pop("capitalize_enum_members")
    return normalized


_PYPROJECT_RELATIVE_PATH_FIELDS = frozenset({
    "custom_file_header_path",
    "custom_template_dir",
    "diff_against",
    "emit_model_metadata",
    "http_local_ref_path",
    "input",
    "lockfile",
    "output",
})


def _resolve_pyproject_relative_paths(config: dict[str, Any], pyproject_path: Path | None) -> dict[str, Any]:
    """Resolve only pyproject-relative paths from the pyproject directory."""
    if pyproject_path is None:
        return config

    config_directory = pyproject_path.parent
    updates = {
        field_name: config_directory / resolved_path
        for field_name in _PYPROJECT_RELATIVE_PATH_FIELDS
        if isinstance(path := config.get(field_name), str | Path)
        and not (resolved_path := Path(path).expanduser()).is_absolute()
    }
    if not updates:
        return config
    return {**config, **updates}


def _validate_job_watch_settings(name: str, config: Mapping[str, Any]) -> None:
    """Keep the persistent scheduler outside profile and job configs."""
    if fields := BATCH_OUTER_CONFIG_FIELDS & _normalize_pyproject_config(config).keys():
        options = ", ".join(f"--{field.replace('_', '-')}" for field in sorted(fields))
        msg = (
            f"{options} cannot be used in Job '{name}'; define watch settings at the CLI "
            "or [tool.datamodel-codegen] base level"
        )
        raise Error(msg)


def _get_job_config(  # noqa: PLR0913
    *,
    name: str,
    job: Mapping[str, Any],
    base_config: Mapping[str, Any],
    profiles: Mapping[str, Any],
    cli_config_args: Mapping[str, _RawConfigValue],
    pyproject_path: Path,
) -> JobPlan:
    """Resolve one job as base < profile < job < safe CLI options."""
    if not job.get("input") or not job.get("output"):
        msg = f"Job '{name}' must define both 'input' and 'output'"
        raise Error(msg)
    profile_name = job.get("profile")
    if profile_name is not None and not isinstance(profile_name, str):
        msg = f"Job '{name}' profile must be a string"
        raise Error(msg)

    resolved_config = dict(base_config)
    if profile_name is not None:
        if profile_name not in profiles:
            available = list(profiles.keys()) if profiles else "none"
            msg = f"Profile '{profile_name}' not found for job '{name}'. Available profiles: {available}"
            raise Error(msg)
        resolved_profile = _resolve_profile_extends(profiles, profile_name)
        _validate_job_watch_settings(name, resolved_profile)
        resolved_config.update(resolved_profile)

    _validate_job_watch_settings(name, job)

    # Every job has its own required file input. It must supersede alternate
    # input sources inherited from the base config or its selected profile.
    for alternate_source in ("url", "input_model", "input-model"):
        resolved_config.pop(alternate_source, None)
    resolved_config.update({key: value for key, value in job.items() if key != "profile"})
    normalized_config = _resolve_pyproject_relative_paths(_normalize_pyproject_config(resolved_config), pyproject_path)
    if any(source in normalized_config for source in ("input_model", "url")):
        msg = f"Job '{name}' only supports an 'input' file; use a separate job for each input source"
        raise Error(msg)
    if any(
        "\0" in os.fspath(path)
        for path in (
            normalized_config.get("input"),
            normalized_config.get("output"),
            normalized_config.get("emit_model_metadata"),
        )
        if isinstance(path, str | Path)
    ):
        msg = f"Job '{name}' contains a null path character"
        raise ValueError(msg)
    config = _create_config(normalized_config, cli_config_args)
    _apply_preset(config, normalized_config, cli_config_args)
    _apply_implicit_cli_config_values(config, normalized_config, cli_config_args)
    _validate_final_config(config)
    if command_only_fields := [
        field_name for field_name in sorted(BATCH_COMMAND_ONLY_CONFIG_FIELDS) if getattr(config, field_name)
    ]:
        options = ", ".join(f"--{field_name.replace('_', '-')}" for field_name in command_only_fields)
        msg = f"Job '{name}' cannot use {options}; jobs must generate code"
        raise Error(msg)

    context = {key: normalized_config[key] for key in BATCH_CONFIG_CONTEXT_FIELDS if key in normalized_config}
    return JobPlan(
        name=name,
        config=config,
        pyproject_context=context,
        raw_config=normalized_config,
        cli_config_args=dict(cli_config_args),
        pyproject_path=pyproject_path,
        resolved_output_root=cast("Path", config.output).expanduser().resolve(strict=False),
        resolved_output_parent=cast("Path", config.output).expanduser().parent.resolve(strict=False),
        resolved_model_metadata_root=(
            config.emit_model_metadata.expanduser().resolve(strict=False)
            if config.emit_model_metadata is not None
            else None
        ),
        resolved_model_metadata_parent=(
            config.emit_model_metadata.expanduser().parent.resolve(strict=False)
            if config.emit_model_metadata is not None
            else None
        ),
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    """Return whether two output artifacts share a path or ancestor."""
    return first == second or first in second.parents or second in first.parents


def _paths_overlap_or_samefile(first: Path, second: Path) -> bool:
    """Return whether paths overlap or existing files refer to the same inode."""
    if _paths_overlap(first, second):
        return True
    try:
        return first.exists() and second.exists() and first.samefile(second)
    except OSError:  # pragma: no cover - a raced path is handled by generation or publication
        return False


def _preflight_job_plans(plans: Sequence[JobPlan]) -> None:
    """Validate all selected jobs before any job starts generation."""
    artifacts: list[tuple[str, str, Path]] = []
    inputs: list[tuple[str, Path]] = []
    for plan in plans:
        config = plan.config
        if config.output is None:  # pragma: no cover - guarded by the TOML validation above
            msg = f"Job '{plan.name}' cannot write to stdout"
            raise Error(msg)
        if not isinstance(config.input, Path) or not config.input.exists():
            msg = f"Job '{plan.name}' input does not exist: {config.input}"
            raise Error(msg)
        _validate_generation_path_conflicts(config.input, config.output, config.emit_model_metadata)
        inputs.append((plan.name, config.input.expanduser().resolve()))
        artifacts.append((plan.name, "output", cast("Path", plan.resolved_output_root)))
        if (model_metadata := config.emit_model_metadata) is not None:
            if plan.resolved_model_metadata_root is None:  # pragma: no cover - set when metadata is configured
                msg = f"Job '{plan.name}' cannot resolve model metadata output: {model_metadata}"
                raise Error(msg)
            artifacts.append((plan.name, "model metadata", plan.resolved_model_metadata_root))

    for index, first_artifact in enumerate(artifacts):
        first_job, first_kind, first_path = first_artifact
        for second_job, second_kind, second_path in artifacts[index + 1 :]:
            if not _paths_overlap_or_samefile(first_path, second_path):
                continue
            msg = (
                f"Jobs '{first_job}' ({first_kind}: {first_path}) and '{second_job}' "
                f"({second_kind}: {second_path}) have overlapping output paths"
            )
            raise Error(msg)

    for artifact_job, artifact_kind, artifact_path in artifacts:
        for input_job, input_path in inputs:
            if artifact_job == input_job or not _paths_overlap_or_samefile(artifact_path, input_path):
                continue
            msg = (
                f"Job '{artifact_job}' ({artifact_kind}: {artifact_path}) overlaps input for job "
                f"'{input_job}': {input_path}"
            )
            raise Error(msg)


def _reject_batch_input_diff(plans: Sequence[JobPlan]) -> None:
    """Reject two-input comparison before batch staging or watch startup."""
    if (diff_plan := next((plan for plan in plans if plan.config.diff_against is not None), None)) is None:
        return
    msg = "--diff-against cannot be used with --job or --all-jobs "
    msg += f"(resolved for Job '{diff_plan.name}'); compare one profile or input at a time"
    raise Error(msg)


def _plan_jobs(args: Namespace) -> BatchPlan:
    """Load and preflight the selected pyproject jobs in declaration order."""
    from pydantic import ValidationError  # noqa: PLC0415

    try:
        return _plan_jobs_unchecked(args)
    except Error:
        raise
    except (OSError, ValidationError, ValueError) as e:
        msg = f"Invalid batch job configuration: {e}"
        raise Error(msg) from e


def _batch_outer_settings(
    tool_config: Mapping[str, Any], cli_config_args: Mapping[str, _RawConfigValue]
) -> tuple[bool, float]:
    """Resolve scheduler-only settings without adding them to inner jobs."""
    values = {
        **{
            key.replace("-", "_"): value
            for key, value in tool_config.items()
            if key.replace("-", "_") in BATCH_OUTER_CONFIG_FIELDS
        },
        **{key: value for key, value in cli_config_args.items() if key in BATCH_OUTER_CONFIG_FIELDS},
    }
    if not values:
        return False, 0.5
    outer_config = Config.model_validate(values)
    return outer_config.watch, outer_config.watch_delay


def _selected_jobs(args: Namespace, jobs: Mapping[Any, Any]) -> frozenset[Any]:
    """Validate and return the requested job names."""
    if args.all_jobs and args.job:
        msg = "--all-jobs cannot be used with --job"
        raise Error(msg)
    selected_names = tuple(jobs) if args.all_jobs else tuple(args.job or ())
    if unknown_jobs := [name for name in selected_names if name not in jobs]:
        available = ", ".join(jobs)
        msg = f"Job '{unknown_jobs[0]}' not found in pyproject.toml. Available jobs: {available or 'none'}"
        raise Error(msg)
    return frozenset(selected_names)


def _record_raw_batch_watch_dependencies(args: Namespace, dependencies: WatchDependencies) -> None:
    """Keep the latest invalid batch plan observable until it can be replanned."""
    try:
        project_config = _find_datamodel_codegen_project_config_with_path(Path.cwd())
    except (OSError, ValueError):
        return
    if project_config is None:
        return

    pyproject_path, tool_config = project_config
    dependencies.begin_raw_attempt()
    dependencies.add_recovery_file(pyproject_path)
    jobs = tool_config.get("jobs")
    if not isinstance(jobs, Mapping):
        return

    _record_raw_job_config_dependencies(tool_config, pyproject_path.parent, dependencies)
    profiles = tool_config.get("profiles")
    selected_names = jobs if args.all_jobs else args.job or ()
    for name in selected_names:
        job = jobs.get(name)
        if not isinstance(job, Mapping):
            continue
        if isinstance(profiles, Mapping) and isinstance(profile_name := job.get("profile"), str):
            _record_raw_profile_dependencies(profile_name, profiles, pyproject_path.parent, dependencies, set())
        _record_raw_job_config_dependencies(job, pyproject_path.parent, dependencies)


def _record_raw_profile_dependencies(
    profile_name: str,
    profiles: Mapping[Any, Any],
    base_path: Path,
    dependencies: WatchDependencies,
    seen_profiles: set[str],
) -> None:
    """Collect one raw profile chain without reproducing validation failures."""
    if profile_name in seen_profiles:
        return
    seen_profiles.add(profile_name)
    profile = profiles.get(profile_name)
    if not isinstance(profile, Mapping):
        return
    match profile.get("extends"):
        case str() as parent:
            _record_raw_profile_dependencies(parent, profiles, base_path, dependencies, seen_profiles)
        case [*parents] if all(isinstance(parent, str) for parent in parents):
            for parent in parents:
                _record_raw_profile_dependencies(parent, profiles, base_path, dependencies, seen_profiles)
    _record_raw_job_config_dependencies(profile, base_path, dependencies)


def _record_raw_job_config_dependencies(
    config: Mapping[Any, Any], base_path: Path, dependencies: WatchDependencies
) -> None:
    """Register raw local inputs and JSON option files for failed-plan recovery."""
    from datamodel_code_generator.watch_dependencies import _JSON_CONFIG_FIELDS  # noqa: PLC0415

    for raw_name, raw_value in config.items():
        if not isinstance(raw_name, str) or raw_name.replace("-", "_") not in (_JSON_CONFIG_FIELDS | {"input"}):
            continue
        if not isinstance(raw_value, str | Path):
            continue
        path = Path(raw_value)
        dependencies.add_recovery_file(path if path.is_absolute() else base_path / path)


def _plan_jobs_unchecked(args: Namespace) -> BatchPlan:
    """Load and preflight selected jobs after the command-level validation."""
    if args.ignore_pyproject:
        msg = "--ignore-pyproject cannot be used with --job or --all-jobs"
        raise Error(msg)
    if args.profile:
        msg = "--profile cannot be used with --job or --all-jobs; set profile in each job instead"
        raise Error(msg)
    if args.generate_cli_command:
        msg = "--generate-cli-command cannot be used with --job or --all-jobs"
        raise Error(msg)

    command_only_cli_options = [
        field_name
        for field_name in sorted(BATCH_COMMAND_ONLY_CONFIG_FIELDS)
        if getattr(args, field_name, None) is not None
    ]
    if command_only_cli_options:
        options = ", ".join(f"--{field_name.replace('_', '-')}" for field_name in command_only_cli_options)
        msg = f"{options} cannot be used with --job or --all-jobs; jobs must generate code"
        raise Error(msg)

    cli_config_args = _explicit_config_args(args)
    if unsafe_fields := BATCH_UNSAFE_CLI_FIELDS & cli_config_args.keys():
        options = ", ".join(f"--{field.replace('_', '-')}" for field in sorted(unsafe_fields))
        msg = f"{options} cannot be used with --job or --all-jobs; define it in each job"
        raise Error(msg)

    project_config = _find_datamodel_codegen_project_config_with_path(Path.cwd())
    if project_config is None:
        msg = "No [tool.datamodel-codegen] section found in pyproject.toml"
        raise Error(msg)
    pyproject_path, tool_config = project_config
    jobs = tool_config.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        msg = "No jobs found in [tool.datamodel-codegen.jobs]"
        raise Error(msg)
    profiles = tool_config.get("profiles", {})
    if not isinstance(profiles, Mapping):
        msg = "[tool.datamodel-codegen.profiles] must be a table"
        raise Error(msg)

    selected = _selected_jobs(args, jobs)
    base_config = {
        key: value
        for key, value in tool_config.items()
        if key not in {"jobs", "profiles"} and key.replace("-", "_") not in BATCH_OUTER_CONFIG_FIELDS
    }
    watch, watch_delay = _batch_outer_settings(tool_config, cli_config_args)
    for field_name in BATCH_OUTER_CONFIG_FIELDS:
        cli_config_args.pop(field_name, None)

    plans: list[JobPlan] = []
    for name, job in jobs.items():
        if name not in selected:
            continue
        if not isinstance(name, str) or not isinstance(job, Mapping):
            msg = f"Job '{name}' must be a table"
            raise Error(msg)
        plans.append(
            _get_job_config(
                name=name,
                job=job,
                base_config=base_config,
                profiles=profiles,
                cli_config_args=cli_config_args,
                pyproject_path=pyproject_path,
            )
        )
    _reject_batch_input_diff(plans)
    _preflight_job_plans(plans)
    return BatchPlan(tuple(plans), watch, watch_delay, pyproject_path)


TomlValue: TypeAlias = str | bool | int | float | list["TomlValue"] | tuple["TomlValue", ...]


def _json_ready(value: Any) -> Any:
    from pydantic import BaseModel  # noqa: PLC0415

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _pyproject_toml_value(key: str, value: object) -> TomlValue:
    from datamodel_code_generator.json_config import JsonConfigSpecs  # noqa: PLC0415

    if key not in JsonConfigSpecs.by_field_name or not isinstance(value, Mapping):
        return cast("TomlValue", value)
    return json.dumps(_json_ready(value), ensure_ascii=False, separators=(",", ":"))


def _format_toml_value(value: TomlValue) -> str:
    """Format a Python value as a TOML value string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int | float):
        return str(value)
    formatted_items = [_format_toml_value(item) for item in value]
    return f"[{', '.join(formatted_items)}]"


def _pyproject_config_data(args: Namespace) -> dict[str, TomlValue]:
    """Return pyproject.toml configuration data from CLI arguments."""
    config_data: dict[str, TomlValue] = {}
    args_dict: dict[str, object] = vars(args)
    for key, value in sorted(args_dict.items()):
        if value is None:
            continue
        if key in EXCLUDED_CONFIG_OPTIONS:
            continue

        config_data[key.replace("_", "-")] = _pyproject_toml_value(key, value)
    return config_data


def _format_pyproject_config(config_data: Mapping[str, TomlValue]) -> str:
    lines: list[str] = ["[tool.datamodel-codegen]"]
    for key, value in config_data.items():
        lines.append(f"{key} = {_format_toml_value(value)}")
    return "\n".join(lines) + "\n"


def generate_pyproject_config(args: Namespace) -> str:
    """Generate pyproject.toml [tool.datamodel-codegen] section from CLI arguments."""
    return _format_pyproject_config(_pyproject_config_data(args))


def _normalize_line_endings(text: str) -> str:
    """Normalize line endings to LF for cross-platform comparison."""
    return text.replace("\r\n", "\n")


class OutputComparisonOptions(NamedTuple):
    """Formatting context for a generated-output comparison."""

    is_directory_output: bool
    input_diff: bool = False
    single_file_display_path: str | None = None

    @property
    def fromfile_suffix(self) -> str:
        """Return the unified-diff label suffix for the baseline output."""
        return " (baseline input)" if self.input_diff else ""

    @property
    def tofile_suffix(self) -> str:
        """Return the unified-diff label suffix for the current output."""
        return " (current input)" if self.input_diff else " (expected)"


OutputComparisonPolicy: TypeAlias = tuple[
    Literal["added", "missing"],
    str,
    str,
    Literal["extra", "removed"],
    str,
]


def _output_comparison_policy(*, input_diff: bool) -> OutputComparisonPolicy:
    """Return shared missing and extra reporting values for one comparison."""
    if input_diff:
        missing_message_suffix = "generated only from current input"
        return (
            "added",
            missing_message_suffix,
            missing_message_suffix,
            "removed",
            "generated only from baseline input",
        )
    return (
        "missing",
        "should be generated",
        "file does not exist but should be generated",
        "extra",
        "no longer generated",
    )


def _compare_single_file(
    generated_path: Path,
    actual_path: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> tuple[bool, list[str]]:
    """Compare generated file content with existing file.

    Returns:
        Tuple of (has_differences, diff_lines)
        - has_differences: True if files differ
        - diff_lines: List of diff lines for output
    """
    generated_content = _normalize_line_endings(generated_path.read_text(encoding=encoding))

    display_path = comparison.single_file_display_path or actual_path.as_posix()
    actual_content = _normalize_line_endings(actual_path.read_text(encoding=encoding))

    if generated_content == actual_content:
        return False, []

    diff_lines = list(
        difflib.unified_diff(
            actual_content.splitlines(keepends=True),
            generated_content.splitlines(keepends=True),
            fromfile=f"{display_path}{comparison.fromfile_suffix}",
            tofile=f"{display_path}{comparison.tofile_suffix}",
        )
    )
    return True, diff_lines


class DirectoryChangedFile(NamedTuple):
    """One changed file found while comparing generated and existing directories."""

    path: str
    diff_lines: list[str]


def _compare_directories(
    generated_dir: Path,
    actual_dir: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> tuple[list[DirectoryChangedFile], list[str], list[str]]:
    """Compare generated directory with existing directory."""
    changed_files: list[DirectoryChangedFile] = []

    generated_files = {path.relative_to(generated_dir) for path in generated_dir.rglob("*.py")}

    actual_files: set[Path] = set()
    if actual_dir.exists():
        for path in actual_dir.rglob("*.py"):
            if "__pycache__" not in path.parts:
                actual_files.add(path.relative_to(actual_dir))

    missing_files = [rel_path.as_posix() for rel_path in sorted(generated_files - actual_files)]
    extra_files = [rel_path.as_posix() for rel_path in sorted(actual_files - generated_files)]

    for rel_path in sorted(generated_files & actual_files):
        generated_content = _normalize_line_endings((generated_dir / rel_path).read_text(encoding=encoding))
        actual_content = _normalize_line_endings((actual_dir / rel_path).read_text(encoding=encoding))
        if generated_content != actual_content:
            changed_files.append(
                DirectoryChangedFile(
                    path=rel_path.as_posix(),
                    diff_lines=list(
                        difflib.unified_diff(
                            actual_content.splitlines(keepends=True),
                            generated_content.splitlines(keepends=True),
                            fromfile=f"{rel_path.as_posix()}{comparison.fromfile_suffix}",
                            tofile=f"{rel_path.as_posix()}{comparison.tofile_suffix}",
                        )
                    ),
                )
            )

    return changed_files, missing_files, extra_files


class OutputComparison(NamedTuple):
    """Generated-file differences shared by --check and --diff-against."""

    differences: list[CheckDifferencePayload]
    content: str


def _compare_generated_single_file(
    generated_output: Path,
    actual_output: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> OutputComparison:
    """Build one single-file comparison report."""
    from datamodel_code_generator._structured_output import CheckDifferencePayload  # noqa: PLC0415

    path = comparison.single_file_display_path or actual_output.as_posix()
    if not actual_output.exists():
        missing_kind, _, single_file_missing_message_suffix, _, _ = _output_comparison_policy(
            input_diff=comparison.input_diff
        )
        message = f"{missing_kind.upper()}: {path} ({single_file_missing_message_suffix})"
        return OutputComparison(
            differences=[CheckDifferencePayload(kind=missing_kind, path=path, message=message)],
            content=f"{message}\n" if comparison.input_diff else message,
        )

    diff_found, diff_lines = _compare_single_file(generated_output, actual_output, encoding, comparison)
    if not diff_found:
        return OutputComparison(differences=[], content="")

    diff_text = "".join(diff_lines)
    return OutputComparison(
        differences=[CheckDifferencePayload(kind="changed", path=path, diff=diff_text)], content=diff_text
    )


def _compare_generated_directories(
    generated_output: Path,
    actual_output: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> OutputComparison:
    """Build one directory manifest comparison report."""
    from datamodel_code_generator._structured_output import CheckDifferencePayload  # noqa: PLC0415

    differences: list[CheckDifferencePayload] = []
    content_parts: list[str] = []
    changed_files, missing_files, extra_files = _compare_directories(
        generated_output,
        actual_output,
        encoding,
        comparison,
    )
    missing_kind, missing_message_suffix, _, extra_kind, extra_message_suffix = _output_comparison_policy(
        input_diff=comparison.input_diff
    )
    for changed_file in changed_files:
        diff_text = "".join(changed_file.diff_lines)
        differences.append(CheckDifferencePayload(kind="changed", path=changed_file.path, diff=diff_text))
        content_parts.append(diff_text)
    for missing in missing_files:
        message = f"{missing_kind.upper()}: {missing} ({missing_message_suffix})"
        differences.append(CheckDifferencePayload(kind=missing_kind, path=missing, message=message))
        content_parts.append(f"{message}\n")
    for extra in extra_files:
        message = f"{extra_kind.upper()}: {extra} ({extra_message_suffix})"
        differences.append(CheckDifferencePayload(kind=extra_kind, path=extra, message=message))
        content_parts.append(f"{message}\n")
    return OutputComparison(differences=differences, content="".join(content_parts))


def _compare_generated_outputs(
    generated_output: Path,
    actual_output: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> OutputComparison:
    """Compare formatted generated output without retaining full file manifests in memory."""
    if comparison.is_directory_output:
        return _compare_generated_directories(generated_output, actual_output, encoding, comparison)
    return _compare_generated_single_file(generated_output, actual_output, encoding, comparison)


def _format_cli_value(value: str | list[str]) -> str:
    """Format a value for CLI argument."""
    if isinstance(value, list):
        return " ".join(f'"{v}"' if " " in v else v for v in value)
    return f'"{value}"' if " " in value else value


@lru_cache(maxsize=1)
def _negative_boolean_options() -> dict[str, str]:
    """Index parser-defined negative flags only when command generation needs them."""
    from argparse import BooleanOptionalAction  # noqa: PLC0415

    negative_options: dict[str, str] = {}
    for action in arg_parser._actions:  # noqa: SLF001
        if not isinstance(action, BooleanOptionalAction):
            continue
        for option in action.option_strings:
            if option.startswith("--no-"):
                negative_options[action.dest] = option
    return negative_options


def generate_cli_command(config: dict[str, TomlValue]) -> str:
    """Generate CLI command from pyproject.toml configuration."""
    parts: list[str] = ["datamodel-codegen"]

    for key, value in sorted(config.items()):
        if key in EXCLUDED_CONFIG_OPTIONS:
            continue

        cli_key = key.replace("_", "-")

        if isinstance(value, bool):
            if value:
                parts.append(f"--{cli_key}")
            elif negative_option := _negative_boolean_options().get(key):
                parts.append(negative_option)
        elif isinstance(value, list):
            parts.extend((f"--{cli_key}", _format_cli_value(cast("list[str]", value))))
        else:
            parts.extend((f"--{cli_key}", _format_cli_value(str(value))))

    return " ".join(parts)


def _hyphenated_config_data(config: Mapping[str, Any]) -> dict[str, Any]:
    return {key.replace("_", "-"): _json_ready(value) for key, value in sorted(config.items())}


def _generated_module_path(module: tuple[str, ...]) -> str:
    return Path(*module).as_posix()


def _generated_files_from_result(result: str | Mapping[tuple[str, ...], str]) -> list[GeneratedFilePayload]:
    from datamodel_code_generator._structured_output import GeneratedFilePayload  # noqa: PLC0415

    if isinstance(result, str):
        return [GeneratedFilePayload(path=None, content=result)]
    return [
        GeneratedFilePayload(path=_generated_module_path(module), content=content)
        for module, content in sorted(result.items())
    ]


def _generated_files_from_output(
    output: Path, encoding: str, *, display_output: Path | None = None
) -> list[GeneratedFilePayload]:
    from datamodel_code_generator._structured_output import GeneratedFilePayload  # noqa: PLC0415

    if output.is_file():
        return [
            GeneratedFilePayload(
                path=(display_output or output).name,
                content=output.read_text(encoding=encoding),
            )
        ]
    return [
        GeneratedFilePayload(path=path.relative_to(output).as_posix(), content=path.read_text(encoding=encoding))
        for path in sorted(output.rglob("*.py"))
        if path.is_file()
    ]


def _structured_output_path(output: Path | str | None) -> str | None:
    if isinstance(output, Path):
        return output.as_posix()
    return output


def _generation_output_json(files: list[GeneratedFilePayload], output: Path | str | None = None) -> str:
    from datamodel_code_generator._structured_output import generation_output_json  # noqa: PLC0415

    return generation_output_json(files, output=_structured_output_path(output))


def _command_output_json(
    kind: CommandOutputKind,
    content: str,
    *,
    config: dict[str, Any] | None = None,
    items: list[dict[str, Any]] | None = None,
    arguments: list[str] | None = None,
) -> str:
    from datamodel_code_generator._structured_output import command_output_json  # noqa: PLC0415

    return command_output_json(kind, content, config=config, items=items, arguments=arguments)


def _check_output_json(
    *,
    success: bool,
    content: str,
    differences: list[CheckDifferencePayload],
    kind: Literal["check", "input-diff"] = "check",
) -> str:
    from datamodel_code_generator._structured_output import check_output_json  # noqa: PLC0415

    return check_output_json(success=success, content=content, differences=differences, kind=kind)


def _write_comparison_output(
    comparison: OutputComparison,
    output_format: str | None,
    *,
    kind: Literal["check", "input-diff"] = "check",
) -> None:
    """Write a common comparison report in text or structured JSON."""
    if output_format == "json":
        sys.stdout.write(
            _check_output_json(
                success=not comparison.differences,
                content=comparison.content,
                differences=comparison.differences,
                kind=kind,
            )
            + "\n"
        )
        return
    sys.stdout.write(comparison.content)


def _generation_output_json_schema() -> str:
    from datamodel_code_generator._structured_output import generation_output_json_schema  # noqa: PLC0415

    return generation_output_json_schema()


def _model_metadata_json_schema() -> str:
    from datamodel_code_generator.model_metadata import model_metadata_json_schema  # noqa: PLC0415

    return model_metadata_json_schema()


def _structured_output_json_schema() -> str:
    from datamodel_code_generator._structured_output import structured_output_json_schema  # noqa: PLC0415

    return structured_output_json_schema()


def _copy_generated_output(generated_output: Path, actual_output: Path, *, is_directory_output: bool) -> None:
    if is_directory_output:
        for generated_file in sorted(generated_output.rglob("*")):
            if not generated_file.is_file():
                continue
            target = actual_output / generated_file.relative_to(generated_output)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(generated_file, target)
        return

    actual_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(generated_output, actual_output)


def _write_generated_result(
    result: str | Mapping[tuple[str, ...], str],
    output_format: str | None,
    *,
    fail_on_multi_module_stdout: bool = False,
) -> Exit | None:
    if isinstance(result, str):
        if output_format == "json":
            result = _generation_output_json(_generated_files_from_result(result))
        sys.stdout.write(result + "\n")
        return None

    match output_format:
        case "json":
            sys.stdout.write(_generation_output_json(_generated_files_from_result(result)) + "\n")
        case _ if fail_on_multi_module_stdout and len(result) > 1:
            sys.stderr.write(
                "Error: Multiple modules were generated. Use --output <directory> to write them as files "
                "or --output-format json for structured stdout.\n"
            )
            return Exit.ERROR
        case _:
            for content in result.values():
                sys.stdout.write(content + "\n")
    return None


def run_generate_from_config(  # noqa: PLR0913, PLR0917
    config: Config,
    input_: Path | str | ParseResult | Mapping[str, Any],
    output: Path | None,
    extra_template_data: defaultdict[str, dict[str, Any]] | None,
    aliases: Mapping[str, str | list[str]] | None,
    serialization_aliases: Mapping[str, str] | None,
    command_line: str | None,
    custom_formatters_kwargs: dict[str, str] | None,
    settings_path: Path | None = None,
    validators: Mapping[str, ModelValidators] | None = None,
    default_value_overrides: Mapping[str, Any] | None = None,
    input_filename: str | None = None,
    generation_timestamp: str | None = None,
    logical_output: Path | None = None,
) -> str | Mapping[tuple[str, ...], str] | None:
    """Run code generation with the given config and parameters."""
    generation_config = _generation_config(
        config,
        output=output,
        extra_template_data=extra_template_data,
        aliases=aliases,
        serialization_aliases=serialization_aliases,
        command_line=command_line,
        custom_formatters_kwargs=custom_formatters_kwargs,
        settings_path=settings_path,
        validators=validators,
        default_value_overrides=default_value_overrides,
        input_filename=input_filename,
        generation_timestamp=generation_timestamp,
        logical_output=logical_output,
    )
    return generate(
        input_=input_,
        config=cast("Any", generation_config),  # ty: ignore[redundant-cast]
    )


def _generation_config(  # noqa: PLR0913
    config: Config,
    *,
    output: Path | None,
    extra_template_data: defaultdict[str, dict[str, Any]] | None,
    aliases: Mapping[str, str | list[str]] | None,
    serialization_aliases: Mapping[str, str] | None,
    command_line: str | None,
    custom_formatters_kwargs: dict[str, str] | None,
    settings_path: Path | None = None,
    validators: Mapping[str, ModelValidators] | None = None,
    default_value_overrides: Mapping[str, Any] | None = None,
    input_filename: str | None = None,
    generation_timestamp: str | None = None,
    logical_output: Path | None = None,
) -> Config:
    """Return the settings one generation runs with: the CLI config with its run values and no preset."""
    generation_config = config.model_copy(
        update={
            "input_filename": input_filename,
            "output": output,
            "preset": None,
            "extra_template_data": extra_template_data,
            "aliases": aliases,
            "serialization_aliases": serialization_aliases,
            "command_line": command_line,
            "apply_default_values_for_required_fields": config.use_default,
            "force_optional_for_required_fields": config.force_optional,
            "custom_formatters_kwargs": custom_formatters_kwargs,
            "settings_path": settings_path,
            "validators": validators,
            "default_value_overrides": default_value_overrides,
        }
    )
    if generation_timestamp is not None:
        generation_config._generation_timestamp = generation_timestamp  # noqa: SLF001
    if logical_output is not None:
        generation_config._logical_output = logical_output  # noqa: SLF001
    return generation_config


def _template_data(config: Config) -> defaultdict[str, dict[str, Any]] | None:
    """Return the extra template data, moving the additional imports it declares into the config."""
    if config.extra_template_data is None:
        return None
    extra_template_data = cast("defaultdict[str, dict[str, Any]]", config.extra_template_data)
    if additional_imports := _extract_additional_imports(extra_template_data):
        config.additional_imports = [*(config.additional_imports or ()), *additional_imports]
    return extra_template_data


def _target_usage_error(namespace: Namespace) -> str | None:
    """Return why the target options of a command line cannot run together, if they cannot."""
    selected = vars(namespace)
    if "generate_server" not in selected:
        if options := [flag for name, flag in _TARGET_OPTIONS if name in selected]:
            return f"{', '.join(options)} can only be used with --generate-server"
        return None
    if "target_config" not in selected:
        return "--generate-server requires --target-config"
    if modes := [flag for name, flag in _TARGET_EXCLUSIVE if selected.get(name) not in {None, False}]:
        return f"--generate-server cannot be used with {', '.join(modes)}"
    return None


def _target_settings(config: Config, args: Sequence[str], pyproject_path: Path | None) -> tuple[GenerateConfig, Path]:
    """Return the model settings a generation target runs with, and the lock file they name."""
    from datamodel_code_generator.config import GenerateConfig  # noqa: PLC0415

    lockfile = config.lockfile or _remote_lock_plan(config, pyproject_path).literal_path
    settings = _generation_config(
        config,
        output=config.output,
        extra_template_data=_template_data(config),
        aliases=config.aliases,
        serialization_aliases=config.serialization_aliases,
        command_line=_command_header(args) if config.enable_command_header else None,
        custom_formatters_kwargs=config.custom_formatters_kwargs,
        settings_path=config.output,
        validators=config.validators,
        default_value_overrides=config.default_values,
    )
    values = vars(settings) | {"lockfile": lockfile}
    fields = GenerateConfig.model_fields
    return GenerateConfig.model_construct(**{name: values[name] for name in fields if name in values}), lockfile


def _run_target(args: Sequence[str], namespace: Namespace, config: Config | None, pyproject_path: Path | None) -> Exit:
    """Hand the selected generation target to its runner, which reports every diagnostic."""
    from datamodel_code_generator._target_cli import run_target  # noqa: PLC0415

    return Exit(run_target(args, namespace, config, pyproject_path))


def _staging_directory_for(target: Path) -> tempfile.TemporaryDirectory[str]:
    """Create an on-disk staging directory on the target artifact's filesystem."""
    staging_parent = Path(os.path.abspath(target.expanduser())).parent  # noqa: PTH100
    while not staging_parent.exists():
        staging_parent = staging_parent.parent
    return tempfile.TemporaryDirectory(prefix=".datamodel-codegen-", dir=staging_parent)


def _stage_job_plan(plan: JobPlan) -> _StagedJobPlan:
    """Redirect a write-mode job's artifacts to private, same-filesystem staging paths."""
    if plan.config.check:
        return _StagedJobPlan(plan, plan.config, None, None, None, None, None, None, None, None, ())

    output = plan.config.output
    from datamodel_code_generator._publication import publication_anchor  # noqa: PLC0415

    contexts: list[tempfile.TemporaryDirectory[str]] = []
    anchors: list[_PublicationAnchor] = []
    try:
        staged_output: Path | None = None
        output_anchor: _PublicationAnchor | None = None
        updates: dict[str, Path] = {}
        if output is not None:
            output_context = _staging_directory_for(output)
            contexts.append(output_context)
            staged_output = Path(output_context.name) / (output.name or "output")
            updates["output"] = staged_output
            output_anchor = publication_anchor(
                cast("Path", plan.resolved_output_root)
                if output.is_dir()
                else cast("Path", plan.resolved_output_parent)
            )
            anchors.append(output_anchor)
        model_metadata = plan.config.emit_model_metadata
        staged_model_metadata: Path | None = None
        model_metadata_anchor: _PublicationAnchor | None = None
        if model_metadata is not None:
            metadata_context = _staging_directory_for(model_metadata)
            contexts.append(metadata_context)
            staged_model_metadata = Path(metadata_context.name) / (model_metadata.name or "model-metadata.json")
            updates["emit_model_metadata"] = staged_model_metadata
            model_metadata_anchor = publication_anchor(cast("Path", plan.resolved_model_metadata_parent))
            anchors.append(model_metadata_anchor)

        staged_config = plan.config.model_copy(update=updates)
        staged_config._logical_model_metadata = model_metadata  # noqa: SLF001
        return _StagedJobPlan(
            plan,
            staged_config,
            output,
            staged_output,
            plan.resolved_output_root,
            model_metadata,
            staged_model_metadata,
            plan.resolved_model_metadata_root,
            output_anchor,
            model_metadata_anchor,
            tuple(contexts),
        )
    except OSError as exc:
        cleanup_errors = _cleanup_staging_resources(contexts, anchors)
        if cleanup_error := _staging_cleanup_error(exc, cleanup_errors):
            raise cleanup_error from exc
        raise


def _stage_job_plans(plans: Sequence[JobPlan]) -> tuple[_StagedJobPlan, ...]:
    """Stage every write-mode job, removing earlier staging if preparation fails."""
    staged_plans: list[_StagedJobPlan] = []
    try:
        for plan in plans:
            staged_plans.append(_stage_job_plan(plan))  # noqa: PERF401
    except OSError as exc:
        cleanup_errors = _cleanup_staged_job_plans(staged_plans)
        if cleanup_error := _staging_cleanup_error(exc, cleanup_errors):
            raise cleanup_error from exc
        raise
    return tuple(staged_plans)


def _cleanup_staging_resources(
    staging_contexts: Iterable[tempfile.TemporaryDirectory[str]],
    anchors: Iterable[_PublicationAnchor | None],
) -> tuple[OSError, ...]:
    """Attempt every private staging cleanup and retain each cleanup failure."""
    cleanup_errors = [
        cleanup_error
        for context in staging_contexts
        if (cleanup_error := _cleanup_staging_context(context)) is not None
    ]
    cleanup_errors.extend(
        cleanup_error for anchor in anchors if (cleanup_error := _close_staging_anchor(anchor)) is not None
    )
    return tuple(cleanup_errors)


def _cleanup_staging_context(context: tempfile.TemporaryDirectory[str]) -> OSError | None:
    """Clean one temporary directory while retaining its cleanup failure."""
    try:
        context.cleanup()
    except OSError as exc:
        return exc
    return None


def _close_staging_anchor(anchor: _PublicationAnchor | None) -> OSError | None:
    """Release one directory descriptor while retaining its cleanup failure."""
    if anchor is None or anchor.directory_fd is None:
        return None
    try:
        os.close(anchor.directory_fd)
    except OSError as exc:
        return exc
    return None


def _staging_cleanup_error(primary: OSError | None, cleanup_errors: Sequence[OSError]) -> OSError | None:
    """Combine cleanup diagnostics without discarding the original staging failure."""
    if not cleanup_errors:
        return None
    cleanup_message = "; ".join(str(error) for error in cleanup_errors)
    if primary is None:
        return OSError(f"could not clean batch output staging: {cleanup_message}")
    return OSError(f"{primary}; additionally, could not clean batch output staging: {cleanup_message}")


def _cleanup_staged_job_plans(staged_plans: Sequence[_StagedJobPlan]) -> tuple[OSError, ...]:
    """Remove every private batch staging resource after a batch result is known."""
    return _cleanup_staging_resources(
        (context for staged_plan in staged_plans for context in staged_plan.staging_contexts),
        (
            anchor
            for staged_plan in staged_plans
            for anchor in (staged_plan.output_anchor, staged_plan.model_metadata_anchor)
        ),
    )


def _cleanup_job_transaction(
    staged_plans: Sequence[_StagedJobPlan], remote_locks: _RemoteLockTransaction | None
) -> tuple[OSError, ...]:
    """Release every output and lock resource after a batch result is known."""
    cleanup_errors = _cleanup_staged_job_plans(staged_plans)
    if remote_locks is None:
        return cleanup_errors
    try:
        remote_locks.discard()
    except OSError as exc:
        return (*cleanup_errors, exc)
    return cleanup_errors


def _staged_files(staged_plan: _StagedJobPlan) -> Iterator[_StagedFile]:
    """Return staged files paired with their final targets without removing directory extras."""
    from datamodel_code_generator._publication import StagedFile  # noqa: PLC0415

    if staged_plan.staged_output is not None and staged_plan.output is not None:
        if staged_plan.staged_output.is_file():
            yield StagedFile(
                staged_plan.staged_output,
                staged_plan.output,
                cast("Path", staged_plan.plan.resolved_output_parent) / staged_plan.output.name,
                staged_plan.output_anchor,
            )
        else:
            for generated_file in sorted(staged_plan.staged_output.rglob("*")):
                if generated_file.is_file():
                    relative_path = generated_file.relative_to(staged_plan.staged_output)
                    yield StagedFile(
                        generated_file,
                        staged_plan.output / relative_path,
                        cast("Path", staged_plan.resolved_output_root) / relative_path,
                        staged_plan.output_anchor,
                    )
    if staged_plan.staged_model_metadata is not None and staged_plan.model_metadata is not None:
        yield StagedFile(
            staged_plan.staged_model_metadata,
            staged_plan.model_metadata,
            cast("Path", staged_plan.plan.resolved_model_metadata_parent) / staged_plan.model_metadata.name,
            staged_plan.model_metadata_anchor,
        )


def _publish_staged_files(files: Iterable[tuple[Path, Path] | _StagedFile]) -> None:
    """Load the publication journal only when an artifact must be published."""
    from datamodel_code_generator._publication import publish_staged_files  # noqa: PLC0415

    publish_staged_files(files)


def _publish_staged_job_plans(staged_plans: Sequence[_StagedJobPlan]) -> None:
    """Publish every generated batch artifact after all jobs have completed successfully."""
    _publish_staged_files(file for staged_plan in staged_plans for file in _staged_files(staged_plan))


def _validate_staged_job_plans(staged_plans: Sequence[_StagedJobPlan]) -> None:
    """Reject generated files whose concrete paths escape their preflighted output roots."""
    for staged_plan in staged_plans:
        for file in _staged_files(staged_plan):
            concrete_target = file.target.expanduser().resolve(strict=False)
            artifact_root = (
                staged_plan.resolved_model_metadata_root
                if file.staged_file == staged_plan.staged_model_metadata
                else staged_plan.resolved_output_root
            )
            if artifact_root is None:  # pragma: no cover - staging always records metadata roots
                continue
            if concrete_target != artifact_root and artifact_root not in concrete_target.parents:
                msg = (
                    f"Job '{staged_plan.plan.name}' generated file escapes its output path: "
                    f"{file.target} resolves outside {artifact_root}"
                )
                raise Error(msg)


def _run_jobs(
    args: Sequence[str],
    plans: Sequence[JobPlan],
    *,
    remote_lock_plans: Sequence[_RemoteLockPlan] | None = None,
) -> Exit:
    """Run batch jobs transactionally while retaining the direct --check fast path."""
    try:
        entries = tuple((plan.name, plan.config, plan.pyproject_path) for plan in plans)
        remote_lock_plans = (
            tuple(remote_lock_plans)
            if remote_lock_plans is not None
            else tuple(_remote_lock_plan(config, pyproject_path) for _, config, pyproject_path in entries)
        )
        remote_locks = _RemoteLockTransaction.open(entries, remote_lock_plans)
    except Error as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return Exit.ERROR
    try:
        staged_plans = _stage_job_plans(plans)
    except OSError as exc:
        if remote_locks is not None:
            with suppress(OSError):
                remote_locks.discard()
        print(f"Error: could not prepare batch output staging: {exc}", file=sys.stderr)  # noqa: T201
        return Exit.ERROR

    try:
        match namespace.output_format:
            case "json":
                result = _run_jobs_json(args, staged_plans, remote_locks, remote_lock_plans)
            case _:
                result = _run_jobs_text(args, staged_plans, remote_locks, remote_lock_plans)
    except BaseException:
        if cleanup_error := _staging_cleanup_error(None, _cleanup_job_transaction(staged_plans, remote_locks)):
            print(f"Error: {cleanup_error}", file=sys.stderr)  # noqa: T201
        raise

    if cleanup_error := _staging_cleanup_error(None, _cleanup_job_transaction(staged_plans, remote_locks)):
        print(f"Error: {cleanup_error}", file=sys.stderr)  # noqa: T201
        return Exit.ERROR
    return result


def _run_watched_jobs(
    args: Sequence[str],
    batch_plan: BatchPlan,
    dependencies: WatchDependencies,
) -> Exit:
    """Collect one full batch graph and publish it only with a successful cycle."""
    dependencies.configure_many(
        (
            plan.config,
            {**plan.raw_config, **plan.cli_config_args},
            plan.pyproject_path,
        )
        for plan in batch_plan.jobs
    )
    remote_lock_plans, remote_lock_intent = dependencies._apply_remote_lock_plans(  # noqa: SLF001
        _remote_lock_plan(job.config, job.pyproject_path) for job in batch_plan.jobs
    )
    for plan in remote_lock_plans:
        dependencies.add_file(plan.canonical_path)
        if plan.policy == "update":
            dependencies.exclude_file(plan.canonical_path)
    with dependencies.generation() as generation:
        result = _run_jobs(args, batch_plan.jobs, remote_lock_plans=remote_lock_plans)
        generation.failed = result is not Exit.OK
    if result is Exit.OK:
        dependencies._commit_remote_lock_intent(remote_lock_intent)  # noqa: SLF001
    else:
        dependencies._merge_remote_lock_intent(remote_lock_intent)  # noqa: SLF001
    return result


def _publish_or_error(
    staged_plans: Sequence[_StagedJobPlan],
    remote_locks: _RemoteLockTransaction | None = None,
) -> Exit | None:
    """Publish staged batch output, reporting an unrecoverable filesystem failure."""
    try:
        _validate_staged_job_plans(staged_plans)
        if remote_locks is None:
            _publish_staged_job_plans(staged_plans)
        else:
            lock_files = remote_locks.staged_files()
            if lock_files:
                output_files = tuple(file for staged_plan in staged_plans for file in _staged_files(staged_plan))
                _publish_staged_files((*output_files, *lock_files))
                remote_locks.mark_committed()
            else:
                _publish_staged_job_plans(staged_plans)
    except (Error, OSError) as exc:
        print(f"Error: could not publish batch output: {exc}", file=sys.stderr)  # noqa: T201
        return Exit.ERROR
    return None


def _run_jobs_text(
    args: Sequence[str],
    staged_plans: Sequence[_StagedJobPlan],
    remote_locks: _RemoteLockTransaction | None,
    remote_lock_plans: Sequence[_RemoteLockPlan],
) -> Exit:
    """Run text-mode jobs without buffering their regular CLI output."""
    exit_code = Exit.OK
    for staged_plan, remote_lock_plan in zip(staged_plans, remote_lock_plans, strict=True):
        result = _main(
            args,
            start_watch=False,
            _batch_config=staged_plan.config,
            _batch_pyproject_context=staged_plan.plan.pyproject_context,
            _batch_pyproject_path=staged_plan.plan.pyproject_path,
            _batch_original_output=staged_plan.output,
            _batch_output_is_staged=staged_plan.staged_output is not None,
            _remote_locks=remote_locks,
            _bound_remote_lock_plan=remote_lock_plan,
        )
        if result is Exit.ERROR:
            return result
        if result is Exit.DIFF:
            exit_code = Exit.DIFF
    if exit_code is Exit.OK and (publish_error := _publish_or_error(staged_plans, remote_locks)) is not None:
        return publish_error
    return exit_code


def _write_batch_json_spool(spool: Any) -> None:
    """Write a batch JSON document from validated, line-delimited job payloads."""
    sys.stdout.write('{\n  "version": 1,\n  "format": "json",\n  "kind": "batch",\n  "jobs": [')
    for index, line in enumerate(spool):
        if index:
            sys.stdout.write(",")
        sys.stdout.write("\n")
        rendered = json.dumps(json.loads(line), indent=2, ensure_ascii=False)
        sys.stdout.write("\n".join(f"    {rendered_line}" for rendered_line in rendered.splitlines()))
    sys.stdout.write("\n  ]\n}\n")


def _run_jobs_json(
    args: Sequence[str],
    staged_plans: Sequence[_StagedJobPlan],
    remote_locks: _RemoteLockTransaction | None,
    remote_lock_plans: Sequence[_RemoteLockPlan],
) -> Exit:
    """Spool validated job payloads until generation and publication both succeed."""
    from pydantic import ValidationError  # noqa: PLC0415

    from datamodel_code_generator._structured_output import BatchJobPayload  # noqa: PLC0415

    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as batch_spool:
            exit_code = Exit.OK
            for staged_plan, remote_lock_plan in zip(staged_plans, remote_lock_plans, strict=True):
                with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
                    with redirect_stdout(output):
                        result = _main(
                            args,
                            start_watch=False,
                            _batch_config=staged_plan.config,
                            _batch_pyproject_context=staged_plan.plan.pyproject_context,
                            _batch_pyproject_path=staged_plan.plan.pyproject_path,
                            _batch_original_output=staged_plan.output,
                            _batch_output_is_staged=staged_plan.staged_output is not None,
                            _remote_locks=remote_locks,
                            _bound_remote_lock_plan=remote_lock_plan,
                        )
                    if result is Exit.ERROR:
                        return result
                    if result is Exit.DIFF:
                        exit_code = Exit.DIFF
                    output.seek(0)
                    try:
                        payload = json.load(output)
                    except json.JSONDecodeError:
                        print(  # noqa: T201
                            f"Error: Job '{staged_plan.plan.name}' returned invalid JSON batch output.",
                            file=sys.stderr,
                        )
                        return Exit.ERROR
                    try:
                        job_payload = BatchJobPayload(name=staged_plan.plan.name, result=payload)
                    except ValidationError:
                        output.seek(0)
                        raw_output = output.read().strip()
                        context = (
                            f"kind {payload['kind']!r}"
                            if isinstance(payload, Mapping) and "kind" in payload
                            else f"raw JSON {raw_output!r}"
                        )
                        print(  # noqa: T201
                            f"Error: Job '{staged_plan.plan.name}' returned unsupported JSON batch output ({context}); "
                            "expected a generation or check payload.",
                            file=sys.stderr,
                        )
                        return Exit.ERROR
                    json.dump(
                        job_payload.model_dump(mode="json"), batch_spool, ensure_ascii=False, separators=(",", ":")
                    )
                    batch_spool.write("\n")

            if exit_code is Exit.OK and (publish_error := _publish_or_error(staged_plans, remote_locks)) is not None:
                return publish_error
            batch_spool.seek(0)
            _write_batch_json_spool(batch_spool)
            return exit_code
    except OSError as exc:
        print(f"Error: could not spool batch JSON output: {exc}", file=sys.stderr)  # noqa: T201
        return Exit.ERROR


def _single_job_plan(config: Config, pyproject_path: Path | None) -> JobPlan:
    """Adapt one command config to the existing staged-publication machinery."""
    output = config.output
    return JobPlan(
        name="command",
        config=config,
        pyproject_context={},
        raw_config={},
        cli_config_args={},
        pyproject_path=pyproject_path or Path.cwd() / "pyproject.toml",
        resolved_output_root=output.expanduser().resolve(strict=False) if output is not None else None,
        resolved_output_parent=output.expanduser().parent.resolve(strict=False) if output is not None else None,
        resolved_model_metadata_root=(
            config.emit_model_metadata.expanduser().resolve(strict=False)
            if config.emit_model_metadata is not None
            else None
        ),
        resolved_model_metadata_parent=(
            config.emit_model_metadata.expanduser().parent.resolve(strict=False)
            if config.emit_model_metadata is not None
            else None
        ),
    )


def _run_single_remote_transaction(  # noqa: PLR0913, PLR0917
    args: Sequence[str],
    config: Config,
    pyproject_config: Mapping[str, Any],
    pyproject_path: Path | None,
    remote_locks: _RemoteLockTransaction,
    remote_lock_plan: _RemoteLockPlan,
    *,
    dependencies: WatchDependencies | None,
) -> Exit:
    """Generate one command through staging so lock and output publication share one journal."""
    staged_plans: tuple[_StagedJobPlan, ...] = ()
    exit_code = Exit.ERROR
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_spool:
            with redirect_stdout(stdout_spool):
                if config.output is not None or config.emit_model_metadata is not None:
                    staged_plans = _stage_job_plans((_single_job_plan(config, pyproject_path),))
                    staged_plan = staged_plans[0]
                    result = _main(
                        args,
                        start_watch=False,
                        dependencies=dependencies,
                        _batch_config=staged_plan.config,
                        _batch_pyproject_context=pyproject_config,
                        _batch_pyproject_path=pyproject_path,
                        _batch_original_output=staged_plan.output,
                        _batch_output_is_staged=staged_plan.staged_output is not None,
                        _remote_locks=remote_locks,
                        _bound_remote_lock_plan=remote_lock_plan,
                    )
                else:
                    result = _main(
                        args,
                        start_watch=False,
                        dependencies=dependencies,
                        _batch_config=config,
                        _batch_pyproject_context=pyproject_config,
                        _batch_pyproject_path=pyproject_path,
                        _remote_locks=remote_locks,
                        _bound_remote_lock_plan=remote_lock_plan,
                    )
            match result:
                case Exit.ERROR:
                    exit_code = result
                case Exit.DIFF:
                    stdout_spool.seek(0)
                    shutil.copyfileobj(stdout_spool, sys.stdout)
                    exit_code = result
                case _ if config.check:
                    stdout_spool.seek(0)
                    shutil.copyfileobj(stdout_spool, sys.stdout)
                    exit_code = Exit.OK
                case _:
                    if (publish_error := _publish_or_error(staged_plans, remote_locks)) is not None:
                        exit_code = publish_error
                    else:
                        stdout_spool.seek(0)
                        shutil.copyfileobj(stdout_spool, sys.stdout)
                        exit_code = Exit.OK
    except OSError as exc:
        print(f"Error: could not prepare command output staging: {exc}", file=sys.stderr)  # noqa: T201
        exit_code = Exit.ERROR
    finally:
        cleanup_errors = _cleanup_staged_job_plans(staged_plans)
        try:
            remote_locks.discard()
        except OSError as exc:
            cleanup_errors += (exc,)
        if cleanup_errors:
            cleanup_message = "; ".join(str(error) for error in cleanup_errors)
            print(  # noqa: T201
                f"Error: could not clean up command transaction: {cleanup_message}",
                file=sys.stderr,
            )
            exit_code = Exit.ERROR
    return exit_code


class GenerationRunContext(NamedTuple):
    """Shared immutable generation arguments for a two-input comparison."""

    generation_timestamp: str
    config: Config
    extra_template_data: defaultdict[str, dict[str, Any]] | None
    aliases: Mapping[str, str | list[str]] | None
    serialization_aliases: Mapping[str, str] | None
    command_line: str | None
    custom_formatters_kwargs: dict[str, str] | None
    settings_path: Path | None
    validators: Mapping[str, ModelValidators] | None
    default_value_overrides: Mapping[str, Any] | None

    def run(
        self,
        input_: Path | str | ParseResult | Mapping[str, Any],
        output: Path | None,
        *,
        input_filename: str,
    ) -> str | Mapping[tuple[str, ...], str] | None:
        """Generate one input with the shared resolved configuration."""
        return run_generate_from_config(
            config=self.config,
            input_=input_,
            output=output,
            extra_template_data=self.extra_template_data,
            aliases=self.aliases,
            serialization_aliases=self.serialization_aliases,
            command_line=self.command_line,
            custom_formatters_kwargs=self.custom_formatters_kwargs,
            settings_path=self.settings_path,
            validators=self.validators,
            default_value_overrides=self.default_value_overrides,
            input_filename=input_filename,
            generation_timestamp=self.generation_timestamp,
            logical_output=self.config.output,
        )


def _diff_against_validation_error(config: Config, namespace: Namespace) -> str | None:
    """Return the first incompatible --diff-against configuration, if any."""
    if config.diff_against is None:
        return None
    incompatible_options = (
        (
            config.url is not None,
            "Error: --diff-against cannot be used with --url; use local --input and --diff-against paths",
        ),
        (bool(config.input_model), "Error: --diff-against cannot be used with --input-model"),
        (config.input is None, "Error: --diff-against requires --input with the current local schema path"),
        (
            config.output is None,
            "Error: --diff-against requires --output as a virtual output path to select file or directory layout",
        ),
        (config.check, "Error: --diff-against and --check cannot be used together"),
        (config.watch, "Error: --diff-against and --watch cannot be used together"),
        (
            config.emit_model_metadata is not None,
            "Error: --diff-against cannot be used with --emit-model-metadata",
        ),
        (
            bool(namespace.fail_on_multi_module_stdout),
            "Error: --diff-against cannot be used with --fail-on-multi-module-stdout",
        ),
    )
    return next((message for is_incompatible, message in incompatible_options if is_incompatible), None)


def _main(  # noqa: PLR0911, PLR0912, PLR0914, PLR0915
    args: Sequence[str] | None = None,
    *,
    start_watch: bool,
    dependencies: WatchDependencies | None = None,
    _batch_config: Config | None = None,
    _batch_pyproject_context: Mapping[str, Any] | None = None,
    _batch_pyproject_path: Path | None = None,
    _batch_original_output: Path | None = None,
    _batch_output_is_staged: bool = False,
    _remote_locks: _RemoteLockTransaction | _UnresolvedRemoteLocks | None = _UNRESOLVED_REMOTE_LOCKS,
    _bound_remote_lock_plan: _RemoteLockPlan | None = None,
) -> Exit:
    """Execute datamodel code generation from command-line arguments."""
    vars(namespace).clear()
    namespace.no_color = False

    if "_ARGCOMPLETE" in os.environ:  # pragma: no cover
        import argcomplete  # noqa: PLC0415

        argcomplete.autocomplete(arg_parser)

    if args is None:  # pragma: no cover
        args = sys.argv[1:]

    arg_parser.parse_args(args, namespace=namespace)

    if not namespace.version and (target_usage := _target_usage_error(namespace)) is not None:
        print(f"Error: {target_usage}", file=sys.stderr)  # noqa: T201
        return Exit.ERROR

    if (agent := namespace.install_skill) is not None:
        from datamodel_code_generator._agent_skill_cli import install_agent_skill_command  # noqa: PLC0415

        return Exit(
            install_agent_skill_command(
                agent,
                namespace.skill_scope or "project",
                overwrite=namespace.overwrite_skill,
            )
        )
    if namespace.skill_scope is not None or namespace.overwrite_skill:
        print("Error: --skill-scope and --overwrite-skill require --install-skill", file=sys.stderr)  # noqa: T201
        return Exit.ERROR

    if namespace.version:
        from datamodel_code_generator import get_version  # noqa: PLC0415

        print(get_version())  # noqa: T201
        sys.exit(0)

    if namespace.output_format_json_schema == "generate-prompt":
        from datamodel_code_generator.prompt import generate_prompt_json_schema  # noqa: PLC0415

        print(generate_prompt_json_schema())  # noqa: T201
        return Exit.OK
    if namespace.output_format_json_schema == "config":
        from datamodel_code_generator.json_config import json_config_json_schema  # noqa: PLC0415

        print(json_config_json_schema())  # noqa: T201
        return Exit.OK
    if namespace.output_format_json_schema == "generation":
        print(_generation_output_json_schema())  # noqa: T201
        return Exit.OK
    if namespace.output_format_json_schema == "model-metadata":
        print(_model_metadata_json_schema())  # noqa: T201
        return Exit.OK
    if namespace.output_format_json_schema == "structured-output":
        print(_structured_output_json_schema())  # noqa: T201
        return Exit.OK

    if namespace.generate_pyproject_config:
        config_data = _pyproject_config_data(namespace)
        config_output = _format_pyproject_config(config_data)
        if namespace.output_format == "json":
            print(  # noqa: T201
                _command_output_json(
                    "pyproject-config",
                    config_output,
                    config={"tool": {"datamodel-codegen": _json_ready(config_data)}},
                )
            )
        else:
            print(config_output)  # noqa: T201
        return Exit.OK

    if namespace.generate_prompt is not None:
        from datamodel_code_generator.prompt import generate_prompt  # noqa: PLC0415

        help_text = arg_parser.format_help()
        prompt_output = generate_prompt(namespace, help_text, arg_parser)
        print(prompt_output)  # noqa: T201
        return Exit.OK

    if _batch_config is None and (namespace.job or namespace.all_jobs):
        if "generate_server" in vars(namespace):
            return _run_target(args, namespace, None, None)
        try:
            batch_plan = _plan_jobs(namespace)
        except Error as e:
            if dependencies is not None:
                _record_raw_batch_watch_dependencies(namespace, dependencies)
            print(str(e), file=sys.stderr)  # noqa: T201
            return Exit.ERROR
        if not batch_plan.watch:
            return _run_jobs(args, batch_plan.jobs)
        if any(plan.config.check for plan in batch_plan.jobs):
            print("Error: --watch and --check cannot be used together", file=sys.stderr)  # noqa: T201
            return Exit.ERROR
        if namespace.output_format == "json":
            print("Error: --output-format json cannot be used with --watch", file=sys.stderr)  # noqa: T201
            return Exit.ERROR

        from datamodel_code_generator.watch_dependencies import WatchDependencies  # noqa: PLC0415

        watch_dependencies = dependencies or WatchDependencies()
        result = _run_watched_jobs(args, batch_plan, watch_dependencies)
        if result is not Exit.OK or not start_watch:
            return result
        try:
            from datamodel_code_generator.watch import watch_and_regenerate  # noqa: PLC0415

            return watch_and_regenerate(
                batch_plan.jobs[0].config,
                dependencies=watch_dependencies,
                regenerate=lambda: _main(
                    args,
                    start_watch=False,
                    dependencies=watch_dependencies,
                ),
                watch_path=batch_plan.pyproject_path,
                watch_delay=batch_plan.watch_delay,
            )
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)  # noqa: T201
            return Exit.ERROR

    # Handle --ignore-pyproject and --profile options
    if _batch_config is not None:
        pyproject_config = dict(_batch_pyproject_context or {})
        pyproject_path = _batch_pyproject_path
    elif namespace.ignore_pyproject:
        pyproject_config: dict[str, Any] = {}
        pyproject_path = None
    else:
        try:
            pyproject_config, pyproject_path = _get_pyproject_toml_config_with_path(
                Path.cwd(), profile=namespace.profile
            )
        except Error as e:
            print(e.message, file=sys.stderr)  # noqa: T201
            return Exit.ERROR

    if namespace.generate_cli_command:
        if not pyproject_config:
            print(  # noqa: T201
                "No [tool.datamodel-codegen] section found in pyproject.toml",
                file=sys.stderr,
            )
            return Exit.ERROR
        command_output = generate_cli_command(pyproject_config)
        if namespace.output_format == "json":
            print(  # noqa: T201
                _command_output_json(
                    "cli-command",
                    command_output,
                    config=_hyphenated_config_data(pyproject_config),
                    arguments=shlex.split(command_output),
                )
            )
        else:
            print(command_output)  # noqa: T201
        return Exit.OK

    pyproject_config = _resolve_pyproject_relative_paths(pyproject_config, pyproject_path)

    cli_config_args: dict[str, _RawConfigValue] = {}
    if _batch_config is not None:
        # Generation adjusts a few Config fields while resolving templates and
        # stdout behaviour. Each batch job therefore receives a fresh copy.
        config = _batch_config.model_copy(deep=True)
        merged_config_values = dict(pyproject_config)
    else:
        cli_config_args = _explicit_config_args(namespace)
        merged_config_values = {**pyproject_config, **cli_config_args}
        if dependencies is not None:
            dependencies.stage_raw_config(merged_config_values)
        try:
            config = _create_config(pyproject_config, cli_config_args)
            _apply_preset(config, pyproject_config, cli_config_args)
            _apply_implicit_cli_config_values(config, pyproject_config, cli_config_args)
            _validate_final_config(config)
        except Error as e:
            print(str(e), file=sys.stderr)  # noqa: T201
            return Exit.ERROR
        except Exception as e:
            from pydantic import ValidationError  # noqa: PLC0415

            if not isinstance(e, ValidationError):
                raise
            print(f"Invalid configuration: {e}", file=sys.stderr)  # noqa: T201
            return Exit.ERROR

    watch_dependencies = dependencies
    if config.watch:
        from datamodel_code_generator.watch_dependencies import WatchDependencies  # noqa: PLC0415

        watch_dependencies = watch_dependencies or WatchDependencies()
        watch_dependencies.configure(
            config,
            config_values=merged_config_values,
        )

    if config.list_deprecations:
        content = render_deprecations(cast("Any", config.list_deprecations))
        if namespace.output_format == "json":
            print(  # noqa: T201
                _command_output_json(
                    "deprecations",
                    content,
                    items=json.loads(render_deprecations("json")),
                )
            )
        else:
            print(content, end="")  # noqa: T201
        return Exit.OK

    if config.list_experimental:
        from datamodel_code_generator.experimental import render_experimental_features  # noqa: PLC0415

        content = render_experimental_features(cast("Any", config.list_experimental))
        if namespace.output_format == "json":
            print(  # noqa: T201
                _command_output_json(
                    "experimental",
                    content,
                    items=json.loads(render_experimental_features("json")),
                )
            )
        else:
            print(content, end="")  # noqa: T201
        return Exit.OK

    if diff_error := _diff_against_validation_error(config, namespace):
        print(diff_error, file=sys.stderr)  # noqa: T201
        return Exit.ERROR

    if not config.input and not config.url and not config.input_model and sys.stdin.isatty():
        print(  # noqa: T201
            "Not Found Input: require `stdin` or arguments `--input`, `--url`, or `--input-model`",
            file=sys.stderr,
        )
        arg_parser.print_help()
        return Exit.ERROR

    if config.input_model and (config.input or config.url):
        print(  # noqa: T201
            "Error: --input-model cannot be used with --input or --url",
            file=sys.stderr,
        )
        return Exit.ERROR

    if config.url and config.allow_remote_refs is None:
        config.allow_remote_refs = True

    if config.check and config.output is None:
        print(  # noqa: T201
            "Error: --check cannot be used with stdout output (no --output specified)",
            file=sys.stderr,
        )
        return Exit.ERROR
    if config.check and config.emit_model_metadata is not None:
        print(  # noqa: T201
            "Error: --check cannot be used with --emit-model-metadata",
            file=sys.stderr,
        )
        return Exit.ERROR

    if "generate_server" in vars(namespace):
        return _run_target(args, namespace, config, pyproject_path)

    if config.watch and config.check:
        print(  # noqa: T201
            "Error: --watch and --check cannot be used together",
            file=sys.stderr,
        )
        return Exit.ERROR

    if config.watch and namespace.output_format == "json":
        print(  # noqa: T201
            "Error: --output-format json cannot be used with --watch",
            file=sys.stderr,
        )
        return Exit.ERROR

    if config.watch and config.input_model:
        print(  # noqa: T201
            "Error: --watch cannot be used with --input-model",
            file=sys.stderr,
        )
        return Exit.ERROR

    if config.watch and (config.input is None or is_url(str(config.input))):
        print(  # noqa: T201
            "Error: --watch requires --input file path (not URL or stdin)",
            file=sys.stderr,
        )
        return Exit.ERROR

    lock_plan = _bound_remote_lock_plan or _remote_lock_plan(config, pyproject_path)
    remote_lock_intent: set[Path] | None = None
    if config.watch and watch_dependencies is not None and _bound_remote_lock_plan is None:
        (lock_plan,), remote_lock_intent = watch_dependencies._apply_remote_lock_plans((lock_plan,))  # noqa: SLF001
    active_lockfile = lock_plan.canonical_path if lock_plan.active else None
    if config.watch and watch_dependencies is not None:
        watch_dependencies.add_file(lock_plan.canonical_path)
        if lock_plan.policy == "update":
            watch_dependencies.exclude_file(lock_plan.canonical_path)
    remote_transaction_owner = False
    if _remote_locks is _UNRESOLVED_REMOTE_LOCKS:
        try:
            _remote_locks = _RemoteLockTransaction.open((("command", config, pyproject_path),), (lock_plan,))
        except Error as e:
            if remote_lock_intent is not None and watch_dependencies is not None:
                watch_dependencies._merge_remote_lock_intent(remote_lock_intent)  # noqa: SLF001
            print(str(e), file=sys.stderr)  # noqa: T201
            return Exit.ERROR
        remote_transaction_owner = _remote_locks is not None
    remote_locks = cast("_RemoteLockTransaction | None", _remote_locks)

    def finish_watch_remote_lock_intent(result: Exit) -> Exit:
        """Commit successful lock intent, retaining failed verified candidates for recovery."""
        if remote_lock_intent is None or watch_dependencies is None:
            return result
        if result is Exit.OK:
            watch_dependencies._commit_remote_lock_intent(remote_lock_intent)  # noqa: SLF001
        else:
            watch_dependencies._merge_remote_lock_intent(remote_lock_intent)  # noqa: SLF001
        return result

    try:
        _validate_generation_path_conflicts(
            config.input or config.url or {},
            config.output,
            config.emit_model_metadata,
            active_lockfile,
        )
    except Error as e:
        if remote_transaction_owner and remote_locks is not None:
            with suppress(OSError):
                remote_locks.discard()
        if remote_lock_intent is not None and watch_dependencies is not None:
            watch_dependencies._merge_remote_lock_intent(remote_lock_intent)  # noqa: SLF001
        print(str(e), file=sys.stderr)  # noqa: T201
        return Exit.ERROR
    if remote_transaction_owner and remote_locks is not None:
        if config.watch and start_watch:
            result = _run_single_remote_transaction(
                args,
                config,
                pyproject_config,
                pyproject_path,
                remote_locks,
                lock_plan,
                dependencies=watch_dependencies,
            )
            if result is not Exit.OK:
                return finish_watch_remote_lock_intent(result)
            finish_watch_remote_lock_intent(result)
            try:
                from datamodel_code_generator.watch import watch_and_regenerate  # noqa: PLC0415

                return watch_and_regenerate(
                    config,
                    dependencies=watch_dependencies,
                    regenerate=lambda: _main(args, start_watch=False, dependencies=watch_dependencies),
                )
            except Exception as e:  # noqa: BLE001
                print(str(e), file=sys.stderr)  # noqa: T201
                return Exit.ERROR
        result = _run_single_remote_transaction(
            args,
            config,
            pyproject_config,
            pyproject_path,
            remote_locks,
            lock_plan,
            dependencies=watch_dependencies,
        )
        return finish_watch_remote_lock_intent(result)
    config.resolve_remote_lock(None if remote_locks is None else remote_locks.collector_for(lock_plan))
    uses_black_formatter = config.formatters is None or Formatter.BLACK in config.formatters
    if uses_black_formatter and not is_supported_in_black(config.target_python_version):  # pragma: no cover
        print(  # noqa: T201
            f"Installed black doesn't support Python version {config.target_python_version.value}.\n"
            f"You have to install a newer black.\n"
            f"Installed black version: {_get_black().__version__}",
            file=sys.stderr,
        )
        return Exit.ERROR

    if config.debug:  # pragma: no cover
        enable_debug_message()

    if config.disable_warnings:
        warnings.simplefilter("ignore")

    if (
        config.output_model_type in {DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass}
        and not config.use_annotated
        and namespace.use_annotated is None
        and pyproject_config.get("use_annotated") is None
    ):
        warn_deprecated(
            "behavior.pydantic-v2-use-annotated-default",
            details=(
                "The current default (use_annotated=False) generates constrained types like "
                "'conint(ge=1, le=365)' which are discouraged in Pydantic v2."
            ),
            stacklevel=1,
        )

    if config.reuse_scope == ReuseScope.Tree and not config.reuse_model:
        print(  # noqa: T201
            "Warning: --reuse-scope=tree has no effect without --reuse-model",
            file=sys.stderr,
        )

    if config.collapse_root_models_name_strategy and not config.collapse_root_models:
        print(  # noqa: T201
            "Error: --collapse-root-models-name-strategy requires --collapse-root-models",
            file=sys.stderr,
        )
        return Exit.ERROR

    if (
        config.use_specialized_enum
        and namespace.use_specialized_enum is not False  # CLI didn't disable it
        and (namespace.use_specialized_enum is True or pyproject_config.get("use_specialized_enum") is True)
        and not config.target_python_version.has_strenum
    ):
        print(  # noqa: T201
            f"Error: --use-specialized-enum requires --target-python-version 3.11 or later.\n"
            f"Current target version: {config.target_python_version.value}\n"
            f"StrEnum is only available in Python 3.11+.",
            file=sys.stderr,
        )
        return Exit.ERROR

    try:
        extra_template_data = _template_data(config)
    except Error as e:
        print(str(e), file=sys.stderr)  # noqa: T201
        return finish_watch_remote_lock_intent(Exit.ERROR)

    aliases = config.aliases
    serialization_aliases = config.serialization_aliases
    default_value_overrides = config.default_values
    custom_formatters_kwargs = config.custom_formatters_kwargs
    validators_config = config.validators

    writes_json_output_file = (
        namespace.output_format == "json"
        and config.output is not None
        and not config.check
        and not _batch_output_is_staged
        and config.diff_against is None
    )
    if config.check or writes_json_output_file or config.diff_against is not None:
        config_output = cast("Path", config.output)
        is_directory_output = not config_output.suffix
        temp_context: tempfile.TemporaryDirectory[str] | None = tempfile.TemporaryDirectory()
        temp_dir = Path(temp_context.name)
        if is_directory_output:
            generate_output_root = temp_dir / "new" if config.diff_against else temp_dir
            generate_output: Path | None = generate_output_root / config_output.name
            compare_output = temp_dir / "old" / config_output.name if config.diff_against else None
        else:
            generate_output = temp_dir / ("new.py" if config.diff_against else "output.py")
            compare_output = temp_dir / "old.py" if config.diff_against else None
    else:
        temp_context = None
        generate_output = config.output
        is_directory_output = False
        compare_output = None

    repair_invalid_dotted_stdout = (
        generate_output is None
        and namespace.output_format != "json"
        and namespace.fail_on_multi_module_stdout is not True
        and config.treat_dot_as_module is None
        and not config.strict_dotted_module_names
        and config.module_split_mode is None
        and not config.generate_schema_validators
        and not config.use_generic_base_class
        and getattr(config, "custom_class_name_generator", None) is None
        and config.custom_template_dir is None
        and not config.custom_formatters
        and config.custom_file_header is None
        and config.custom_file_header_path is None
        and config.extra_template_data is None
        and not config.additional_imports
    )
    config.repair_invalid_dotted_stdout = repair_invalid_dotted_stdout

    def cleanup_and_return(exit_code: Exit) -> Exit:
        if temp_context is not None:
            temp_context.cleanup()
        return finish_watch_remote_lock_intent(exit_code)

    try:
        input_: Path | str | ParseResult | Mapping[str, Any]
        if config.input_model:
            from datamodel_code_generator.input_model import Error as InputModelError  # noqa: PLC0415
            from datamodel_code_generator.input_model import (  # noqa: PLC0415
                _load_model_schema_with_python_type_expressions,
            )

            try:
                input_ = _load_model_schema_with_python_type_expressions(
                    config.input_model,
                    config.input_file_type,
                    config.input_model_ref_strategy,
                    config.output_model_type,
                )
            except InputModelError as e:
                raise Error(str(e)) from e
            if config.input_file_type == InputFileType.Auto:
                config.input_file_type = InputFileType.JsonSchema
        else:
            input_ = config.url or config.input or sys.stdin.read()

        if writes_json_output_file:
            _validate_generation_path_conflicts(
                input_,
                config.output,
                config.emit_model_metadata,
                active_lockfile,
            )

        if compare_output is not None:
            comparison_context = GenerationRunContext(
                generation_timestamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                config=config,
                extra_template_data=extra_template_data,
                aliases=aliases,
                serialization_aliases=serialization_aliases,
                command_line=_command_header(args) if config.enable_command_header else None,
                custom_formatters_kwargs=custom_formatters_kwargs,
                settings_path=_batch_original_output or config.output,
                validators=validators_config,
                default_value_overrides=default_value_overrides,
            )
            comparison_context.run(cast("Path", config.diff_against), compare_output, input_filename="<input>")
            result = comparison_context.run(input_, generate_output, input_filename="<input>")
        elif watch_dependencies is None:
            result = run_generate_from_config(
                config=config,
                input_=input_,
                output=generate_output,
                extra_template_data=extra_template_data,
                aliases=aliases,
                serialization_aliases=serialization_aliases,
                command_line=_command_header(args) if config.enable_command_header else None,
                custom_formatters_kwargs=custom_formatters_kwargs,
                settings_path=_batch_original_output or config.output,
                validators=validators_config,
                default_value_overrides=default_value_overrides,
                logical_output=_batch_original_output,
            )
        else:
            with watch_dependencies.generation():
                result = run_generate_from_config(
                    config=config,
                    input_=input_,
                    output=generate_output,
                    extra_template_data=extra_template_data,
                    aliases=aliases,
                    serialization_aliases=serialization_aliases,
                    command_line=_command_header(args) if config.enable_command_header else None,
                    custom_formatters_kwargs=custom_formatters_kwargs,
                    settings_path=_batch_original_output or config.output,
                    validators=validators_config,
                    default_value_overrides=default_value_overrides,
                    logical_output=_batch_original_output,
                )
    except InvalidClassNameError as e:
        print(f"{e} You have to set `--class-name` option", file=sys.stderr)  # noqa: T201
        return cleanup_and_return(Exit.ERROR)
    except UnicodeDecodeError as e:
        print(f"Unable to decode input using encoding {config.encoding!r}: {e}", file=sys.stderr)  # noqa: T201
        return cleanup_and_return(Exit.ERROR)
    except Error as e:
        print(str(e), file=sys.stderr)  # noqa: T201
        return cleanup_and_return(Exit.ERROR)
    except Exception as e:  # noqa: BLE001
        from datamodel_code_generator.remote_lock import RemoteLockError  # noqa: PLC0415

        if isinstance(e, RemoteLockError):
            print(str(e), file=sys.stderr)  # noqa: T201
            return cleanup_and_return(Exit.ERROR)
        import traceback  # noqa: PLC0415

        print(traceback.format_exc(), file=sys.stderr)  # noqa: T201
        return cleanup_and_return(Exit.ERROR)

    if (
        config.output is not None
        and config.output.is_dir()
        and generate_output is not None
        and generate_output.is_file()
    ):
        print(_SINGLE_MODULE_OUTPUT_DIRECTORY_ERROR, file=sys.stderr)  # noqa: T201
        return cleanup_and_return(Exit.ERROR)

    if writes_json_output_file and generate_output is not None and config.output is not None:
        _copy_generated_output(generate_output, config.output, is_directory_output=is_directory_output)

    if generate_output is None and result is not None:
        if (
            write_error := _write_generated_result(
                result,
                namespace.output_format,
                fail_on_multi_module_stdout=namespace.fail_on_multi_module_stdout is True,
            )
        ) is not None:
            return cleanup_and_return(write_error)
    elif (
        namespace.output_format == "json"
        and generate_output is not None
        and not config.check
        and config.diff_against is None
    ):
        display_output = _batch_original_output or (config.output if writes_json_output_file else None)
        sys.stdout.write(
            _generation_output_json(
                _generated_files_from_output(generate_output, config.encoding, display_output=display_output),
                output=_batch_original_output or config.output,
            )
            + "\n"
        )

    if config.diff_against is not None and compare_output is not None and generate_output is not None:
        comparison = _compare_generated_outputs(
            generate_output,
            compare_output,
            config.encoding,
            OutputComparisonOptions(
                is_directory_output=is_directory_output,
                input_diff=True,
                single_file_display_path=cast("Path", config.output).name,
            ),
        )
        _write_comparison_output(comparison, namespace.output_format, kind="input-diff")
        return cleanup_and_return(Exit.DIFF if comparison.differences else Exit.OK)

    if config.check and config.output is not None and generate_output is not None:
        comparison = _compare_generated_outputs(
            generate_output,
            config.output,
            config.encoding,
            OutputComparisonOptions(is_directory_output=is_directory_output),
        )
        _write_comparison_output(comparison, namespace.output_format)
        return cleanup_and_return(Exit.DIFF if comparison.differences else Exit.OK)

    if config.watch and start_watch:
        try:
            from datamodel_code_generator.watch import watch_and_regenerate  # noqa: PLC0415

            return cleanup_and_return(
                watch_and_regenerate(
                    config,
                    dependencies=watch_dependencies,
                    regenerate=lambda: _main(
                        args,
                        start_watch=False,
                        dependencies=watch_dependencies,
                    ),
                )
            )
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)  # noqa: T201
            return cleanup_and_return(Exit.ERROR)

    return cleanup_and_return(Exit.OK)


def main(args: Sequence[str] | None = None) -> Exit:
    """Execute datamodel code generation from command-line arguments."""
    from datamodel_code_generator.deprecations import (  # ruff: ignore[import-outside-top-level]
        cli_migration_warning_scope,
    )

    with cli_migration_warning_scope():
        return _main(args, start_watch=True)


if __name__ == "__main__":
    sys.exit(main())
