"""Deferred CLI configuration validation shared with project configuration loading."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from functools import lru_cache
from keyword import iskeyword
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Optional, TypeAlias, Union, cast
from urllib.parse import ParseResult, urlparse

from datamodel_code_generator import Error, _validate_alias_generator, _validate_output_datetime_class
from datamodel_code_generator._format_types import PythonVersion
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import (
    AllExportsScope,
    ClassNameAffixScope,
    DataModelType,
    InputFileType,
    InputModelRefStrategy,
    NamingStrategy,
    OpenAPIScope,
    StrictTypes,
)

if TYPE_CHECKING:
    from argparse import Namespace
    from collections import defaultdict

    from datamodel_code_generator.json_config import JsonConfigFieldName, JsonConfigSource
    from datamodel_code_generator.validators import ModelValidators

    ValidatorsConfigValue: TypeAlias = Mapping[str, ModelValidators]
else:
    ValidatorsConfigValue: TypeAlias = Mapping[str, Any]

Config = cast("Any", object)
ORIGINAL_FIELD_NAME_DELIMITER_ERROR = "`--original-field-name-delimiter` can not be used without `--snake-case-field`."

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


def is_url(ref: str) -> bool:
    """Check if a reference string is a URL (HTTP, HTTPS, or file scheme)."""
    # Keep this local so importing the CLI does not import reference.py and its model stack.
    return ref.startswith(("https://", "http://", "file://"))


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
            "server_handler_modes",
            "server_body_modes",
            "server_primary_responses",
            "server_operation_names",
            "server_router_names",
            "server_parameter_names",
            "client_resource_names",
            "client_operations",
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

        @field_validator("client_protocols", mode="before")
        @classmethod
        def validate_client_protocols(cls, value: Any) -> Any:
            """Load the client helpers from inline JSON or a JSON file path, refusing deep nesting before parsing."""
            if not isinstance(value, str | Path):
                return value
            from datamodel_code_generator.json_config import JsonConfigError, validate_json_value_or_file  # noqa: PLC0415

            try:
                return validate_json_value_or_file(str(value), option_name="--client-protocols", max_depth=64)
            except JsonConfigError as e:
                raise Error(str(e)) from e

        @field_validator(
            "input",
            "output",
            "diff_against",
            "custom_template_dir",
            "custom_file_header_path",
            "http_local_ref_path",
            "server_output",
            "client_output",
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
                    file_path, python_package = item.rsplit("=", maxsplit=1)
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

        __validate_target_selection_err: ClassVar[str] = (
            "--generate-server and --generate-client cannot be used together"
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
        def validate_target_selection(self: Self) -> Self:
            """Validate that one run selects at most one generation target."""
            if self.generate_server is not None and self.generate_client is not None:
                raise Error(self.__validate_target_selection_err)
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
        generate_server: Optional[Literal["fastapi"]] = None  # noqa: UP045
        server_output: Optional[Path] = None  # noqa: UP045
        server_package: Optional[str] = None  # noqa: UP045
        server_model_package: Optional[str] = None  # noqa: UP045
        server_layout: Optional[Literal["routers", "single"]] = None  # noqa: UP045
        server_handler_mode: Optional[Literal["sync", "async"]] = None  # noqa: UP045
        server_handler_modes: Optional[Mapping[str, Literal["sync", "async"]]] = None  # noqa: UP045
        server_include_request: Optional[bool] = None  # noqa: UP045
        server_body_mode: Optional[Literal["typed", "request"]] = None  # noqa: UP045
        server_body_modes: Optional[Mapping[str, Literal["typed", "request"]]] = None  # noqa: UP045
        server_primary_responses: Optional[Mapping[str, Any]] = None  # noqa: UP045
        server_operation_names: Optional[Mapping[str, str]] = None  # noqa: UP045
        server_router_names: Optional[Mapping[str, str]] = None  # noqa: UP045
        server_parameter_names: Optional[Mapping[str, Mapping[str, str]]] = None  # noqa: UP045
        generate_client: Optional[Literal["httpx2"]] = None  # noqa: UP045
        client_output: Optional[Path] = None  # noqa: UP045
        client_package: Optional[str] = None  # noqa: UP045
        client_model_package: Optional[str] = None  # noqa: UP045
        client_signature_style: Optional[Literal["explicit", "unpack"]] = None  # noqa: UP045
        client_body_arguments: Optional[Literal["body", "both"]] = None  # noqa: UP045
        client_resource_names: Optional[Mapping[str, str]] = None  # noqa: UP045
        client_operations: Optional[Mapping[str, Mapping[str, Any]]] = None  # noqa: UP045
        client_default_base_url: Optional[str] = None  # noqa: UP045
        client_server_base_url: Optional[str] = None  # noqa: UP045
        client_protocols: Optional[Mapping[str, Any]] = None  # noqa: UP045

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
            if "generate_server" in set_args:
                self.generate_client = None
            elif "generate_client" in set_args:
                self.generate_server = None
            for field_name in set_args:
                setattr(self, field_name, getattr(parsed_args, field_name))

    Config.__qualname__ = "Config"
    Config.__module__ = "datamodel_code_generator.__main__"
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


def _template_data(config: Config) -> defaultdict[str, dict[str, Any]] | None:
    """Return the extra template data, moving the additional imports it declares into the config."""
    if config.extra_template_data is None:
        return None
    extra_template_data = cast("defaultdict[str, dict[str, Any]]", config.extra_template_data)
    if additional_imports := _extract_additional_imports(extra_template_data):
        config.additional_imports = [*(config.additional_imports or ()), *additional_imports]
    return extra_template_data
