"""CLI argument definitions for datamodel-codegen.

Defines the ArgumentParser and all command-line options organized into groups:
base options, typing customization, field customization, model customization,
template customization, OpenAPI-specific options, and general options.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser, ArgumentTypeError, BooleanOptionalAction, Namespace, RawDescriptionHelpFormatter
from operator import attrgetter
from pathlib import Path
from typing import TYPE_CHECKING, cast

from datamodel_code_generator._format_types import DateClassType, DatetimeClassType, Formatter, PythonVersion
from datamodel_code_generator._shared_types import LiteralType
from datamodel_code_generator.deprecations import deprecation_message
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    ClassNameAffixScope,
    CollapseRootModelsNameStrategy,
    CustomFileHeaderMode,
    DataclassArguments,
    DataModelType,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    HTTPBackend,
    InputFileType,
    InputModelRefStrategy,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIScope,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    StrictTypes,
    TargetPydanticVersion,
    UnionMode,
    VersionMode,
)
from datamodel_code_generator.preset_names import PRESET_NAMES

if TYPE_CHECKING:
    from argparse import Action
    from collections.abc import Iterable, Sequence

DEFAULT_ENCODING = "utf-8"

namespace = Namespace(no_color=False)


def _dataclass_arguments(value: str) -> DataclassArguments:
    """Parse JSON string and validate it as DataclassArguments."""
    try:
        result = json.loads(value)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON: {e}"
        raise ArgumentTypeError(msg) from e
    if not isinstance(result, dict):
        msg = f"Expected a JSON dictionary, got {type(result).__name__}"
        raise ArgumentTypeError(msg)
    valid_keys = set(DataclassArguments.__annotations__.keys())
    invalid_keys = set(result.keys()) - valid_keys
    if invalid_keys:
        msg = f"Invalid keys: {invalid_keys}. Valid keys are: {valid_keys}"
        raise ArgumentTypeError(msg)
    for key, val in result.items():
        if not isinstance(val, bool):
            msg = f"Expected bool for '{key}', got {type(val).__name__}"
            raise ArgumentTypeError(msg)
    return cast("DataclassArguments", result)


def _external_ref_mapping(value: str) -> str:
    """Validate FILE_PATH=PYTHON_PACKAGE mapping format."""
    if "=" not in value:
        msg = (
            f"Invalid --external-ref-mapping format: {value!r}. "
            "Expected FILE_PATH=PYTHON_PACKAGE (e.g., '../common/schema.yaml=mypackage.models')"
        )
        raise ArgumentTypeError(msg)
    file_path, python_package = value.split("=", maxsplit=1)
    if not file_path.strip() or not python_package.strip():
        msg = f"Invalid --external-ref-mapping format: {value!r}. Both FILE_PATH and PYTHON_PACKAGE must be non-empty."
        raise ArgumentTypeError(msg)
    return value


def _json_value_or_file(value: str) -> dict[str, object]:
    """Parse a JSON value or load it from a JSON file path."""
    from datamodel_code_generator.json_config import JsonConfigError, validate_json_value_or_file  # noqa: PLC0415

    try:
        return validate_json_value_or_file(value)
    except JsonConfigError as e:
        msg = str(e)
        raise ArgumentTypeError(msg) from e


class SortingHelpFormatter(RawDescriptionHelpFormatter):
    """Help formatter that sorts arguments, adds color to section headers, and preserves epilog formatting."""

    def _bold_cyan(self, text: str) -> str:  # noqa: PLR6301
        """Wrap text in ANSI bold cyan escape codes."""
        return f"\x1b[36;1m{text}\x1b[0m"

    def add_arguments(self, actions: Iterable[Action]) -> None:
        """Add arguments sorted by option strings."""
        actions = sorted(actions, key=attrgetter("option_strings"))
        super().add_arguments(actions)

    def start_section(self, heading: str | None) -> None:
        """Start a section with optional colored heading."""
        return super().start_section(heading if namespace.no_color or not heading else self._bold_cyan(heading))


class SuggestingArgumentParser(ArgumentParser):
    """Argument parser that suggests close matches for unknown option names."""

    def parse_args(self, args: Iterable[str] | None = None, namespace: Namespace | None = None) -> Namespace:
        """Parse arguments and suggest close matches for unknown option names."""
        parsed_args, unknown_arguments = self.parse_known_args(args, namespace)
        if not unknown_arguments:
            return cast("Namespace", parsed_args)
        return self.error(self._unrecognized_arguments_message(unknown_arguments))

    def _unrecognized_arguments_message(self, unknown_arguments: Sequence[str]) -> str:
        """Format an unknown-argument message with close option-name matches."""
        message = f"unrecognized arguments: {' '.join(unknown_arguments)}"
        suggestions: list[str] = []
        for unknown_argument in unknown_arguments:
            match unknown_argument:
                case "--":
                    break
                case option_name if option_name.startswith("-"):
                    option_name = option_name.partition("=")[0]
                case _:
                    continue

            from difflib import get_close_matches  # noqa: PLC0415

            if (matches := get_close_matches(option_name, self._option_string_actions, n=1, cutoff=0.7)) and (
                suggestion := matches[0]
            ) not in suggestions:
                suggestions.append(suggestion)
        if not suggestions:
            return message
        return f"{message}\nDid you mean: {', '.join(suggestions)}?"


arg_parser = SuggestingArgumentParser(
    usage="\n  datamodel-codegen [options]",
    description="Generate Python data models from schema definitions or structured data\n\n"
    "For detailed usage, see: https://datamodel-code-generator.koxudaxi.dev",
    epilog="Documentation: https://datamodel-code-generator.koxudaxi.dev\n"
    "Agent skill: https://datamodel-code-generator.koxudaxi.dev/coding-agent-skill/\n"
    "GitHub: https://github.com/datamodel-code-generator/datamodel-code-generator",
    formatter_class=SortingHelpFormatter,
    add_help=False,
)

base_options = arg_parser.add_argument_group("Options")
typing_options = arg_parser.add_argument_group("Typing customization")
field_options = arg_parser.add_argument_group("Field customization")
model_options = arg_parser.add_argument_group("Model customization")
extra_fields_model_options = model_options.add_mutually_exclusive_group()
template_options = arg_parser.add_argument_group("Template customization")
openapi_options = arg_parser.add_argument_group("OpenAPI-only options")
graphql_options = arg_parser.add_argument_group("GraphQL-only options")
general_options = arg_parser.add_argument_group("General options")

# ======================================================================================
# Base options for input/output
# ======================================================================================
base_options.add_argument(
    "--allow-remote-refs",
    help="Allow fetching remote $ref references over HTTP/HTTPS. "
    "Currently remote fetching is allowed by default but emits a deprecation warning. "
    "Pass --allow-remote-refs to opt in without warning, "
    "or --no-allow-remote-refs to block remote fetching. "
    "In a future version, remote fetching will be disabled by default.",
    action=BooleanOptionalAction,
    default=None,
)
base_options.add_argument(
    "--strict-refs",
    help="Treat unresolved local $ref JSON pointers as errors instead of generating fallback Any models.",
    action="store_true",
    default=None,
)
base_options.add_argument(
    "--allow-private-network",
    help=(
        "Allow HTTP(S) schema requests to private, loopback, link-local, or otherwise non-public network hosts. "
        "By default these targets are blocked to reduce server-side request forgery (SSRF) risk. "
        "If a trusted internal schema endpoint is blocked, verify the URL and pass this option; otherwise use a "
        "local schema file or public endpoint. "
        "Pass --no-allow-private-network to override a configuration file that enables it."
    ),
    action=BooleanOptionalAction,
    default=None,
)
base_options.add_argument(
    "--http-backend",
    choices=[backend.value for backend in HTTPBackend],
    default=None,
    help=(
        "Select the HTTP client backend. 'auto' (default) selects stable HTTPX when its client module is installed "
        "and only selects experimental HTTPX2 when that module is absent. 'httpx' and 'httpx2' require that exact "
        "backend. Explicit selections and paired dependency errors do not fall back."
    ),
)
base_options.add_argument(
    "--http-headers",
    nargs="+",
    metavar="HTTP_HEADER",
    help='Set headers in HTTP requests to the remote host. (example: "Authorization: Basic dXNlcjpwYXNz")',
)
base_options.add_argument(
    "--http-query-parameters",
    nargs="+",
    metavar="HTTP_QUERY_PARAMETERS",
    help='Set query parameters in HTTP requests to the remote host. (example: "ref=branch")',
)
base_options.add_argument(
    "--http-ignore-tls",
    help="Disable verification of the remote host's TLS certificate",
    action="store_true",
    default=None,
)
base_options.add_argument(
    "--http-timeout",
    type=float,
    default=None,
    help="Timeout in seconds for HTTP requests to remote hosts (default: 30)",
)
base_options.add_argument(
    "--http-local-ref-path",
    help=(
        "Resolve HTTP(S) JSON Schema $ref URLs from a local directory instead of fetching them. "
        "URLs are mapped under the directory by host and path; extensionless refs also try '.json'."
    ),
)
base_options.add_argument(
    "--lockfile",
    help=(
        "Select the remote reference integrity lock file (experimental). "
        "An existing selected lock is verified automatically; "
        "a missing selected lock is ignored unless --locked is used. "
        "The default is datamodel-codegen.lock beside the discovered pyproject.toml, or in the invocation working "
        "directory when no project is found. Explicit relative paths resolve from the invocation working directory."
    ),
)
remote_lock_options = base_options.add_mutually_exclusive_group()
remote_lock_options.add_argument(
    "--update-lock",
    action="store_true",
    default=None,
    help="Create or atomically update the selected remote lock after generation (experimental).",
)
remote_lock_options.add_argument(
    "--locked",
    action="store_true",
    default=None,
    help="Require an existing remote lock and validate each fetched resource against it (experimental).",
)
base_options.add_argument(
    "--input",
    help="Input file/directory (default: stdin)",
)
base_options.add_argument(
    "--input-file-type",
    help=(
        "Input file type (default: auto). "
        "Use 'jsonschema', 'openapi', 'asyncapi', 'graphql', 'mcp-tools', 'xmlschema', 'protobuf', or 'avro' "
        "for schema definitions. "
        "Use 'json', 'yaml', or 'csv' for raw sample data to infer a schema automatically."
    ),
    choices=[i.value for i in InputFileType],
)
base_options.add_argument(
    "--external-ref-mapping",
    nargs="+",
    metavar="FILE_PATH=PYTHON_PACKAGE",
    type=_external_ref_mapping,
    help="Map external $ref file paths to Python import packages instead of generating duplicate classes. "
    'Accepts one or more mappings after a single flag. Format: "path/to/schema.yaml=mypackage.models". '
    "When a $ref points to a mapped file, an import statement is generated instead of a class definition.",
)
base_options.add_argument(
    "--output",
    help="Output file (default: stdout)",
)
base_options.add_argument(
    "--emit-model-metadata",
    type=Path,
    help="Write a separate JSON map from source schema references to generated models and fields.",
)
base_options.add_argument(
    "--output-model-type",
    help="Output model type (default: pydantic_v2.BaseModel)",
    choices=[i.value for i in DataModelType],
)
base_options.add_argument(
    "--preset",
    help=(
        "Apply an immutable built-in option preset. "
        "Preset names include the target Python version so generated syntax is pinned."
    ),
    choices=PRESET_NAMES,
)
base_options.add_argument(
    "--url",
    help=(
        "Input file URL. `--input` is ignored when `--url` is used. "
        "For HTTP(S), datamodel-code-generator[http] remains the stable HTTPX backend and is not deprecated, while "
        "datamodel-code-generator[httpx2] is experimental. The default --http-backend auto policy selects stable "
        "HTTPX when its client module is installed and selects HTTPX2 only when that module is absent. Select "
        "--http-backend httpx2 to require the experimental backend. Explicit selections and paired dependency "
        "errors do not fall back."
    ),
)
base_options.add_argument(
    "--input-model",
    action="append",
    help="Python import path or file path to a Pydantic v2 model or schema dict "
    "(e.g., 'mypackage.module:ClassName', './models.py:ClassName', or 'mypackage.schemas:SCHEMA_DICT'). "
    "Can be specified multiple times for related models with inheritance. "
    "For dict input, --input-file-type is required. "
    "Cannot be used with --input or --url.",
    metavar="MODULE_OR_PATH:NAME",
)
base_options.add_argument(
    "--input-model-ref-strategy",
    help="Strategy for referenced types in --input-model. "
    "'regenerate-all': Regenerate all types. "
    "'reuse-foreign': Reuse types from different families (Enum, etc.), regenerate same-family. "
    "'reuse-all': Reuse all referenced types via import. "
    "If not specified, defaults to regenerate-all behavior.",
    choices=[s.value for s in InputModelRefStrategy],
    default=None,
)

# ======================================================================================
# Customization options for generated models
# ======================================================================================
extra_fields_model_options.add_argument(
    "--allow-extra-fields",
    help=f"Deprecated: {deprecation_message('cli.allow-extra-fields')}",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--allow-population-by-field-name",
    help="Allow population by field name",
    action=BooleanOptionalAction,
    default=None,
)
model_options.add_argument(
    "--class-name",
    help="Set class name of root model",
    default=None,
)
model_options.add_argument(
    "--model-name-map",
    help="Rename generated model classes by schema ref or current generated class name "
    "using a JSON object or JSON file.",
    type=_json_value_or_file,
    default=None,
)
model_options.add_argument(
    "--allow-leading-underscore-class-name",
    help="Allow an explicitly specified root class name to start with an underscore",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--class-name-prefix",
    help="Prefix to add to generated class names (e.g., 'Api' produces 'ApiUser'). "
    "Does not apply to root model when --class-name is specified.",
    type=str,
    default=None,
)
model_options.add_argument(
    "--class-name-suffix",
    help="Suffix to add to generated class names (e.g., 'Schema' produces 'UserSchema'). "
    "Does not apply to root model when --class-name is specified.",
    type=str,
    default=None,
)
model_options.add_argument(
    "--class-name-affix-scope",
    help="Scope for applying --class-name-prefix/--class-name-suffix. "
    "'all': Apply to all classes including enums (default). "
    "'models': Apply only to model classes. "
    "'enums': Apply only to enum classes.",
    choices=[s.value for s in ClassNameAffixScope],
    default=None,
)
model_options.add_argument(
    "--collapse-root-models",
    action=BooleanOptionalAction,
    default=None,
    help="Models generated with a root-type field will be merged into the models using that root-type model",
)
model_options.add_argument(
    "--collapse-root-models-name-strategy",
    help="Strategy for naming when collapsing root models that reference other models. "
    "'child': Keep inner model's name (default). 'parent': Use wrapper's name for inner model. "
    "Requires --collapse-root-models to be set.",
    choices=[s.value for s in CollapseRootModelsNameStrategy],
    default=None,
)
model_options.add_argument(
    "--collapse-reuse-models",
    action="store_true",
    default=None,
    help="When used with --reuse-model, collapse duplicate models by replacing references instead of creating "
    "empty inheritance subclasses. This eliminates 'class Foo(Bar): pass' patterns",
)
model_options.add_argument(
    "--skip-root-model",
    action="store_true",
    default=None,
    help="Skip generating the model for the root schema element",
)
model_options.add_argument(
    "--use-root-model-sequence-interface",
    action="store_true",
    default=None,
    help="Make non-null sequence-like Pydantic v2 RootModel classes implement collections.abc.Sequence by adding "
    "Sequence[T] inheritance and root-delegating __iter__, __getitem__, and __len__ methods",
)
model_options.add_argument(
    "--disable-appending-item-suffix",
    help="Disable appending `Item` suffix to model name in an array",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--disable-timestamp",
    help="Disable timestamp on file headers",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--enable-faux-immutability",
    help="Enable faux immutability",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--enable-version-header",
    help="Enable package version on file headers",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--enable-command-header",
    help="Enable command-line options on file headers for reproducibility",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--enable-generated-header-marker",
    help="Enable @generated marker on file headers",
    action="store_true",
    default=None,
)
extra_fields_model_options.add_argument(
    "--extra-fields",
    help="Set the generated models to allow, forbid, or ignore extra fields.",
    choices=["allow", "ignore", "forbid"],
    default=None,
)
model_options.add_argument(
    "--keep-model-order",
    help="Keep generated models' order",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--keyword-only",
    help="Defined models as keyword only (for example dataclass(kw_only=True)).",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--frozen-dataclasses",
    help="Generate frozen dataclasses (dataclass(frozen=True)). Only applies to dataclass output.",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--dataclass-arguments",
    type=_dataclass_arguments,
    default=None,
    help=(
        "Custom dataclass arguments as a JSON dictionary, "
        'e.g. \'{"frozen": true, "kw_only": true}\'. '
        "Overrides --frozen-dataclasses and similar flags."
    ),
)
model_options.add_argument(
    "--reuse-model",
    help="Reuse models on the field when a module has the model with the same content",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--reuse-scope",
    help="Scope for model reuse deduplication: module (per-file, default) or tree (cross-file with shared module). "
    "Only effective when --reuse-model is set.",
    choices=[s.value for s in ReuseScope],
    default=None,
)
model_options.add_argument(
    "--shared-module-name",
    help=f'Name of the shared module for --reuse-scope=tree (default: "{DEFAULT_SHARED_MODULE_NAME}"). '
    f'Use this option if your schema has a file named "{DEFAULT_SHARED_MODULE_NAME}".',
    default=None,
)
model_options.add_argument(
    "--target-python-version",
    help="target python version",
    choices=[v.value for v in PythonVersion],
)
model_options.add_argument(
    "--target-pydantic-version",
    help="Target Pydantic version for generated code. "
    "'2': Pydantic 2.0+ compatible (default, uses populate_by_name). "
    "'2.11': Pydantic 2.11+ (uses validate_by_name). "
    "'2.12': Pydantic 2.12+ (supports MISSING sentinel).",
    choices=[v.value for v in TargetPydanticVersion],
    default=None,
)
model_options.add_argument(
    "--alias-generator",
    help="Pydantic v2 BaseModel alias generator to use in ConfigDict. "
    "Matching generated aliases are omitted from individual Field() calls.",
    choices=[a.value for a in AliasGenerator],
    default=None,
)
model_options.add_argument(
    "--treat-dot-as-module",
    help="Treat dotted schema names as module paths, creating nested directory structures (e.g., 'foo.bar.Model' "
    "becomes 'foo/bar.py'). Use --no-treat-dot-as-module to keep dots in names as underscores for single-file output.",
    action=BooleanOptionalAction,
    default=None,
)
model_options.add_argument(
    "--strict-dotted-module-names",
    help="Only infer dotted schema names as module paths when every segment is a canonical Python identifier. "
    "This applies only to automatic inference and does not override --treat-dot-as-module or "
    "--no-treat-dot-as-module.",
    action=BooleanOptionalAction,
    default=None,
)
model_options.add_argument(
    "--use-generic-base-class",
    help="Generate a shared base class with model configuration (e.g., extra='forbid') "
    "instead of repeating the configuration in each model. Keeps code DRY.",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--use-schema-description",
    help="Use schema description to populate class docstring",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--use-title-as-name",
    help="use titles as class names of models",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--infer-union-variant-names",
    help="Infer inline oneOf/anyOf branch model names from literal discriminator-style fields",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--use-pendulum",
    help="use pendulum instead of datetime",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--use-standard-primitive-types",
    help="Use Python standard library types for string formats (UUID, IPv4Address, etc.) "
    "instead of str. Affects dataclass, msgspec, TypedDict output. "
    "Pydantic already uses these types by default.",
    action=BooleanOptionalAction,
    default=None,
)
model_options.add_argument(
    "--use-exact-imports",
    help='import exact types instead of modules, for example: "from .foo import Bar" instead of '
    '"from . import foo" with "foo.Bar"',
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--output-datetime-class",
    help="Choose Datetime class between AwareDatetime, NaiveDatetime, PastDatetime, FutureDatetime or datetime. "
    "Each output model has its default mapping (for example pydantic: datetime, dataclass: str, ...)",
    choices=[i.value for i in DatetimeClassType],
    default=None,
)
model_options.add_argument(
    "--output-date-class",
    help="Choose Date class between PastDate, FutureDate or date. (Pydantic v2 only) "
    "Each output model has its default mapping.",
    choices=[i.value for i in DateClassType],
    default=None,
)
model_options.add_argument(
    "--parent-scoped-naming",
    help=f"Deprecated: {deprecation_message('cli.parent-scoped-naming')}",
    action="store_true",
    default=None,
)
model_options.add_argument(
    "--naming-strategy",
    help="Strategy for generating unique model names when duplicates occur. "
    "'numbered' (default): Append numeric suffix (Address, Address1, Address2). "
    "Simple but names don't indicate context. "
    "'parent-prefixed': Prefix with parent model name using underscore "
    "(Company_Address, Company_Employee_Address for nested). Names show hierarchy. "
    "'full-path': Similar to parent-prefixed but joins with CamelCase "
    "(CompanyAddress, CompanyEmployeeAddress). More readable for deep nesting. "
    "'primary-first': Keep clean names for primary definitions (in /definitions/ or "
    "/components/schemas/), only add suffix to inline/nested duplicates.",
    choices=[s.value for s in NamingStrategy],
    default=None,
)
model_options.add_argument(
    "--duplicate-name-suffix",
    help="JSON mapping of type to suffix for resolving duplicate name conflicts. "
    'Example: \'{"model": "Schema"}\' changes Address1 to AddressSchema. '
    "Keys: 'model' (for classes), 'enum' (for enums), 'default' (fallback). "
    "When not specified, uses numeric suffix (Address1, Address2).",
    type=str,
    default=None,
)
model_options.add_argument(
    "--all-exports-scope",
    help="Generate __all__ in __init__.py with re-exports. "
    "'children': export from direct child modules only. "
    "'recursive': export from all descendant modules.",
    choices=[s.value for s in AllExportsScope],
    default=None,
)
model_options.add_argument(
    "--all-exports-collision-strategy",
    help="Strategy for name collisions when using --all-exports-scope=recursive. "
    "'error': raise an error (default). "
    "'minimal-prefix': add module prefix only to colliding names. "
    "'full-prefix': add full module path prefix to colliding names.",
    choices=[s.value for s in AllExportsCollisionStrategy],
    default=None,
)
model_options.add_argument(
    "--module-split-mode",
    help="Split generated models into separate files. 'single': generate one file per model class.",
    choices=[m.value for m in ModuleSplitMode],
    default=None,
)

# ======================================================================================
# Typing options for generated models
# ======================================================================================
typing_options.add_argument(
    "--base-class",
    help="Base Class (default: pydantic.BaseModel)",
    type=str,
)
typing_options.add_argument(
    "--base-class-map",
    help="Model-specific base class mapping (JSON or JSON file path). "
    'Example: \'{"MyModel": "custom.BaseA", "OtherModel": "custom.BaseB"}\'. '
    "Priority: base-class-map > customBasePath (in schema) > base-class.",
    type=_json_value_or_file,
    default=None,
)
typing_options.add_argument(
    "--enum-field-as-literal",
    help="Parse enum field as literal. "
    "all: all enum field type are Literal. "
    "one: field type is Literal when an enum has only one possible value. "
    "none: always use Enum class (never convert to Literal)",
    choices=[lt.value for lt in LiteralType],
    default=None,
)
typing_options.add_argument(
    "--enum-field-as-literal-map",
    help="Per-field override for enum/literal generation (JSON or JSON file path). "
    "Format: JSON object mapping field names to 'literal' or 'enum'. "
    'Example: \'{"status": "literal", "priority": "enum"}\'. '
    "Overrides --enum-field-as-literal for matched fields.",
    type=_json_value_or_file,
    default=None,
)
typing_options.add_argument(
    "--ignore-enum-constraints",
    help="Ignore enum constraints and use the base type (e.g., str, int) instead of generating Enum classes",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--field-constraints",
    help="Use field constraints and not con* annotations",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--set-default-enum-member",
    help=f"Deprecated: {deprecation_message('cli.set-default-enum-member')}",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--strict-types",
    help="Use strict types",
    choices=[t.value for t in StrictTypes],
    nargs="+",
)
typing_options.add_argument(
    "--use-annotated",
    help="Use typing.Annotated for Field(). Also, `--field-constraints` option will be enabled. "
    "Will become default for Pydantic v2 in a future version.",
    action=BooleanOptionalAction,
    default=None,
)
typing_options.add_argument(
    "--use-serialize-as-any",
    help="Use pydantic.SerializeAsAny for fields with types that have subtypes (Pydantic v2 only)",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-generic-container-types",
    help="Use generic container types for type hinting (typing.Sequence, typing.Mapping). "
    "If `--use-standard-collections` option is set, then import from collections.abc instead of typing",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-non-positive-negative-number-constrained-types",
    help="Use the Non{Positive,Negative}{FloatInt} types instead of the corresponding con* constrained types.",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-decimal-for-multiple-of",
    help="Use condecimal instead of confloat for float/number fields with multipleOf constraint "
    "(Pydantic only). Avoids floating-point precision issues in validation.",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-one-literal-as-default",
    help="Use one literal as default value for one literal field",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-enum-values-in-discriminator",
    help="Use enum member literals in discriminator fields instead of string literals",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-standard-collections",
    help="Use standard collections for type hinting (list, dict). Default: enabled",
    action=BooleanOptionalAction,
    default=None,
)
typing_options.add_argument(
    "--use-object-type",
    help="Use object instead of Any for unspecified JSON Schema object and array values",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-subclass-enum",
    help="Define generic Enum class as subclass with field type when enum has type (int, float, bytes, str)",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-specialized-enum",
    help="Use specialized Enum class (StrEnum, IntEnum). Requires --target-python-version 3.11+",
    action=BooleanOptionalAction,
    default=None,
)
typing_options.add_argument(
    "--use-union-operator",
    help="Use | operator for Union type (PEP 604). Default: enabled",
    action=BooleanOptionalAction,
    default=None,
)
typing_options.add_argument(
    "--use-unique-items-as-set",
    help="define field type as `set` when the field attribute has `uniqueItems`",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-tuple-for-fixed-items",
    help="Generate tuple types for arrays with items array syntax when minItems equals maxItems equals items length",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-tuple-for-fixed-length-arrays",
    help="Generate tuple types for fixed-length arrays with a single items schema",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-total-false-for-typed-dict",
    help="Generate TypedDict with total=False and mark required fields with Required",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-closed-typed-dict",
    help="Generate TypedDict with PEP 728 closed=True/extra_items for additionalProperties constraints. "
    "Use --no-use-closed-typed-dict for type checkers that don't yet support PEP 728 (e.g., mypy).",
    action=BooleanOptionalAction,
    default=None,
)
typing_options.add_argument(
    "--allof-merge-mode",
    help="Mode for field merging in allOf schemas. "
    "'constraints': merge only constraints (minItems, maxItems, pattern, etc.) from parent (default). "
    "'all': merge constraints plus annotations (default, examples) from parent. "
    "'none': do not merge any fields from parent properties.",
    choices=[m.value for m in AllOfMergeMode],
    default=None,
)
typing_options.add_argument(
    "--allof-class-hierarchy",
    help="How to map allOf references to class hierarchies. "
    "'if-no-conflict': only create subclasses when parent class has no conflicting property definition. "
    "'always': always create subclasses. ",
    choices=[m.value for m in AllOfClassHierarchy],
    default=None,
)
typing_options.add_argument(
    "--use-type-alias",
    help="Use TypeAlias instead of root models (experimental)",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-type-alias-type",
    help="Use TypeAliasType for type aliases on Python 3.10 and 3.11 (implies --use-type-alias; experimental)",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--use-root-model-type-alias",
    help="Use type alias format for RootModel (e.g., Foo = RootModel[Bar]) "
    "instead of class inheritance (Pydantic v2 only)",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--disable-future-imports",
    help="Disable __future__ imports",
    action="store_true",
    default=None,
)
typing_options.add_argument(
    "--import-overrides",
    help="Override modules for generated imports by symbol name. "
    "Format: JSON object mapping symbols to module paths. "
    'Example: \'{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}\'.',
    type=str,
    default=None,
)
typing_options.add_argument(
    "--type-mappings",
    help="Override default type mappings. "
    'Format: "type+format=target" (e.g., "string+binary=string" to map binary format to string type) '
    'or "format=target" (e.g., "binary=string"). '
    "Can be specified multiple times.",
    nargs="+",
    type=str,
    default=None,
)
typing_options.add_argument(
    "--type-overrides",
    help="Replace schema model types with custom Python types. "
    "Format: JSON object mapping model names to Python import paths. "
    'Model-level: \'{"CustomType": "my_app.types.MyType"}\' replaces all references. '
    'Scoped: \'{"User.field": "my_app.Type"}\' replaces specific field only.',
    type=str,
    default=None,
)

# ======================================================================================
# Customization options for generated model fields
# ======================================================================================
field_options.add_argument(
    "--capitalise-enum-members",
    "--capitalize-enum-members",
    help="Capitalize field names on enum",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--empty-enum-field-name",
    help="Set field name when enum value is empty (default:  `_`)",
    default=None,
)
field_options.add_argument(
    "--field-extra-keys",
    help="Add extra keys to field parameters",
    type=str,
    nargs="+",
)
field_options.add_argument(
    "--field-extra-keys-without-x-prefix",
    help="Add extra keys with `x-` prefix to field parameters. The extra keys are stripped of the `x-` prefix.",
    type=str,
    nargs="+",
)
field_options.add_argument(
    "--field-include-all-keys",
    help="Add all keys to field parameters",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--model-extra-keys",
    help="Add extra keys from schema extensions (x-* fields) to model_config json_schema_extra",
    type=str,
    nargs="+",
)
field_options.add_argument(
    "--model-extra-keys-without-x-prefix",
    help="Add extra keys with `x-` prefix to model_config json_schema_extra. "
    "The extra keys are stripped of the `x-` prefix.",
    type=str,
    nargs="+",
)
field_options.add_argument(
    "--force-optional",
    help="Force optional for required fields",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--original-field-name-delimiter",
    help="Set delimiter to convert to snake case. This option only can be used with --snake-case-field (default: `_` )",
    default=None,
)
field_options.add_argument(
    "--remove-special-field-name-prefix",
    help="Remove field name prefix if it has a special meaning e.g. underscores",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--snake-case-field",
    help="Change camel-case field name to snake-case",
    action=BooleanOptionalAction,
    default=None,
)
field_options.add_argument(
    "--special-field-name-prefix",
    help="Set field name prefix when first character can't be used as Python field name (default:  `field`)",
    default=None,
)
field_options.add_argument(
    "--strict-nullable",
    help="Treat default field as a non-nullable field",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--strip-default-none",
    help="Strip default None on fields",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-default",
    help="Use default value even if a field is required",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-default-kwarg",
    action="store_true",
    help="Use `default=` instead of a positional argument for Fields that have default values.",
    default=None,
)
field_options.add_argument(
    "--use-missing-sentinel",
    help="Use pydantic.experimental.missing_sentinel.MISSING for optional fields without defaults (Pydantic v2.12+).",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-field-description",
    help="Use schema description to populate field docstring",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-field-description-example",
    help="Use schema example to populate field docstring",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-attribute-docstrings",
    help="Set use_attribute_docstrings=True in Pydantic v2 ConfigDict",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-inline-field-description",
    help="Use schema description to populate field docstring as inline docstring",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-single-line-docstring",
    help="Use single-line docstrings when the content fits on one line",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--union-mode",
    help="Union mode for only pydantic v2 field",
    choices=[u.value for u in UnionMode],
    default=None,
)
field_options.add_argument(
    "--no-alias",
    help="""Do not add a field alias. E.g., if --snake-case-field is used along with a base class, which has an
            alias_generator""",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--use-serialization-alias",
    help="Use serialization_alias instead of alias for field aliasing (Pydantic v2 only). "
    "This allows setting values using the Pythonic field name while serializing to the original name.",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--serialization-aliases",
    help="Serialization alias mapping as inline JSON or a JSON file path for Pydantic v2. "
    "Format: {'<schema_field>': '<serialization_alias>'}. "
    "Supports hierarchical formats: "
    "Flat: {'name': 'fullName'} applies to all occurrences. "
    "Scoped: {'User.name': 'fullName'} applies to specific class.",
    type=str,
)
field_options.add_argument(
    "--use-frozen-field",
    help="Use Field(frozen=True) for readOnly fields (Pydantic v2).",
    action=BooleanOptionalAction,
    default=None,
)
field_options.add_argument(
    "--use-default-factory-for-optional-nested-models",
    help="Use default_factory for optional nested model fields instead of None default. "
    "E.g., `field: Model | None = Field(default_factory=Model)` instead of `field: Model | None = None`",
    action="store_true",
    default=None,
)
field_options.add_argument(
    "--field-type-collision-strategy",
    help="Strategy for handling field name and type name collisions (Pydantic v2 only). "
    "'rename-field': rename field with suffix and add alias (default). "
    "'rename-type': rename type class with suffix to preserve field name.",
    choices=[s.value for s in FieldTypeCollisionStrategy],
    default=None,
)

# ======================================================================================
# Options for templating output
# ======================================================================================
template_options.add_argument(
    "--aliases",
    help="Alias mapping as inline JSON or a JSON file path for renaming fields. "
    "Format: {'<schema_field>': '<python_name>'} - the schema field name becomes the Pydantic alias. "
    "Supports hierarchical formats: "
    "Flat: {'id': 'id_'} applies to all occurrences. "
    "Scoped: {'User.name': 'user_name'} applies to specific class. "
    "Priority: scoped > flat. "
    "Multiple aliases (Pydantic v2 only): {'field': ['alt1', 'alt2']} uses AliasChoices for validation. "
    "Example: {'User.name': 'user_name', 'id': 'id_'} generates `id_: ... = Field(alias='id')`.",
    type=str,
)
template_options.add_argument(
    "--default-values",
    help="Default value overrides as inline JSON or a JSON file path. "
    "Supports hierarchical formats: "
    "Flat: {'field': value} applies to all occurrences. "
    "Scoped: {'ClassName.field': value} applies to specific class. "
    "Priority: scoped > flat. "
    "Note: Scoped keys use the generated class name for JSON Schema/OpenAPI. "
    "Required fields remain required unless --use-default is also specified. "
    "Example: {'User.status': 'active', 'page': 1, 'limit': 10}",
    type=str,
)
template_options.add_argument(
    "--deserialize-default-values",
    help="Deserialize serialized schema defaults for selected generated types. Supported values: decimal, enum.",
    choices=[default_value_type.value for default_value_type in DefaultValueType],
    nargs="+",
    default=None,
)
template_options.add_argument(
    "--no-deserialize-default-values",
    dest="deserialize_default_values",
    help="Disable schema default value deserialization.",
    action="store_const",
    const=(),
    default=None,
)
template_options.add_argument(
    "--custom-file-header",
    help="Custom file header",
    type=str,
    default=None,
)
template_options.add_argument(
    "--custom-file-header-path",
    help="Custom file header file path",
    default=None,
    type=str,
)
template_options.add_argument(
    "--custom-file-header-mode",
    help="How to combine a custom file header with the generated header (default: replace)",
    choices=[mode.value for mode in CustomFileHeaderMode],
    default=None,
)
template_options.add_argument(
    "--custom-template-dir",
    help="Custom template directory",
    type=str,
)
template_options.add_argument(
    "--encoding",
    help=f"The encoding of input and output (default: {DEFAULT_ENCODING})",
    default=None,
)
template_options.add_argument(
    "--extra-template-data",
    help="Extra template data for output models as inline JSON or a JSON file path. "
    "For OpenAPI and Jsonschema the keys are the spec path of the object, or the name of the object if you want to "
    "apply the template data to multiple objects with the same name. "
    "If you are using another input file type (e.g. GraphQL), the key is the name of the object. "
    "The value is a dictionary of the template data to add.",
    type=str,
)
template_options.add_argument(
    "--validators",
    help="Validators configuration as inline JSON or a JSON file path. "
    "Defines field validators for Pydantic v2 models. "
    "Keys are model names, values contain validator definitions with field, function, and mode.",
    type=str,
)
template_options.add_argument(
    "--generate-schema-validators",
    help="Generate Pydantic v2 model validators for JSON Schema rules that cannot be represented as type hints "
    "(experimental).",
    action="store_true",
    default=None,
)
template_options.add_argument(
    "--schema-validator-type",
    help="Select the schema-derived runtime validator backend. "
    "'pydantic-v2' generates Pydantic v2 model validators (experimental).",
    choices=[i.value for i in SchemaValidatorType],
    default=None,
)
template_options.add_argument(
    "--schema-validator-base-class-name",
    help="Set the generated shared Pydantic v2 schema runtime validator base class name.",
    type=str,
    default=None,
)
template_options.add_argument(
    "--use-type-checking-imports",
    help="Allow Ruff to move typing-only imports into TYPE_CHECKING blocks. "
    "By default this stays enabled, except for multi-module Ruff formatting of modular Pydantic output "
    "where referenced models stay imported at runtime. "
    "Use --no-use-type-checking-imports to force runtime imports.",
    action=BooleanOptionalAction,
    default=None,
)
template_options.add_argument(
    "--use-double-quotes",
    action="store_true",
    default=None,
    help="Model generated with double quotes. Single quotes or "
    "your black config skip_string_normalization value will be used without this option.",
)
template_options.add_argument(
    "--wrap-string-literal",
    help="Wrap string literal by using black `experimental-string-processing` option (require black 20.8b0 or later)",
    action="store_true",
    default=None,
)
base_options.add_argument(
    "--additional-imports",
    help='Custom imports for output (delimited list input). For example "datetime.date,datetime.datetime"',
    type=str,
    default=None,
)
base_options.add_argument(
    "--class-decorators",
    help="Custom decorators for generated model classes (delimited list input). "
    'For example "@dataclass_json(letter_case=LetterCase.CAMEL)". '
    'The "@" prefix is optional and will be added automatically if missing.',
    type=str,
    default=None,
)
base_options.add_argument(
    "--formatters",
    help="Formatters (current default: black, isort; future default: builtin). "
    "For Ruff projects, use --formatters ruff-check ruff-format "
    "(install with pip install 'datamodel-code-generator[ruff]'). "
    "Use --formatters builtin when no external formatter is used or generation speed takes priority. "
    "Keep --formatters black isort to preserve existing formatting. "
    "New 20260909 presets include builtin; explicit formatters override presets. "
    "The future builtin default reduces installation dependencies and version constraints. "
    "Black/isort remain required today; declare [black,isort] extras for their later optional installation. "
    "Review custom-template output with builtin. "
    "Details: https://datamodel-code-generator.koxudaxi.dev/formatter-behavior/",
    choices=[f.value for f in Formatter],
    nargs="+",
    default=None,
)
base_options.add_argument(
    "--custom-formatters",
    help="List of modules with custom formatter (delimited list input).",
    type=str,
    default=None,
)
template_options.add_argument(
    "--custom-formatters-kwargs",
    help="Custom formatter kwargs as inline JSON or a JSON file path.",
    type=str,
)

# ======================================================================================
# Options specific to OpenAPI input schemas
# ======================================================================================
openapi_options.add_argument(
    "--openapi-scopes",
    help="Scopes of OpenAPI model generation (default: schemas)",
    choices=[o.value for o in OpenAPIScope],
    nargs="+",
    default=None,
)
openapi_options.add_argument(
    "--use-operation-id-as-name",
    help="use operation id of OpenAPI as class names of models",
    action="store_true",
    default=None,
)
openapi_options.add_argument(
    "--include-path-parameters",
    help="Include path parameters in generated parameter models in addition to query parameters (Only OpenAPI)",
    action="store_true",
    default=None,
)
openapi_options.add_argument(
    "--openapi-include-paths",
    nargs="+",
    metavar="PATTERN",
    help=(
        "Include only OpenAPI paths matching fnmatch patterns. "
        "Use wildcards: '*' matches any chars, '?' matches single char. "
        "Example: '/users/*' '/products'. "
        "Requires '--openapi-scopes' to include 'paths'."
    ),
    default=None,
)
openapi_options.add_argument(
    "--openapi-include-info-version",
    help="Emit OpenAPI info.version as OPENAPI_INFO_VERSION in generated models",
    action="store_true",
    default=None,
)
openapi_options.add_argument(
    "--validation",
    help=f"Deprecated: {deprecation_message('cli.validation')}",
    action="store_true",
    default=None,
)
openapi_options.add_argument(
    "--read-only-write-only-model-type",
    help="Model generation for readOnly/writeOnly fields: "
    "'request-response' = Request/Response models only (no base model), "
    "'all' = Base + Request + Response models.",
    choices=[e.value for e in ReadOnlyWriteOnlyModelType],
    default=None,
)
openapi_options.add_argument(
    "--use-status-code-in-response-name",
    help="Include HTTP status code in response model names (e.g., ResourceGetResponse200, ResourceGetResponseDefault)",
    action="store_true",
    default=None,
)
# ======================================================================================
# Schema version options (for both JSON Schema and OpenAPI)
# ======================================================================================
base_options.add_argument(
    "--schema-version",
    help="Schema version. Valid values depend on input type: "
    "JsonSchema: auto, draft-04, draft-06, draft-07, 2019-09, 2020-12. "
    "OpenAPI: auto, 3.0, 3.1, 3.2. "
    "AsyncAPI: auto, 2.0, 3.0. "
    "XMLSchema: auto, 1.0, 1.1. "
    "Protobuf: auto, proto2, proto3, 2023. "
    "(default: auto - detected from $schema, openapi/asyncapi field, XML Schema versioning attributes, "
    "or Protobuf syntax/edition)",
    default=None,
)
base_options.add_argument(
    "--schema-version-mode",
    help="Schema version validation mode. "
    "'lenient': accept all features regardless of version (default). "
    "'strict': warn on features outside declared/detected version.",
    choices=[m.value for m in VersionMode],
    default=None,
)

# ======================================================================================
# Options specific to GraphQL input schemas
# ======================================================================================
graphql_options.add_argument(
    "--graphql-no-typename",
    help="Exclude __typename field from generated GraphQL models. "
    "Useful when using generated models for GraphQL mutations.",
    action="store_true",
    default=None,
)

# ======================================================================================
# General options
# ======================================================================================
general_options.add_argument(
    "--check",
    action="store_true",
    default=None,
    help="Verify generated files are up-to-date without modifying them. "
    "Exits with code 1 if differences found, 0 if up-to-date. "
    "Useful for CI to ensure generated code is committed.",
)
general_options.add_argument(
    "--diff-against",
    metavar="BASELINE_INPUT",
    help=(
        "Generate BASELINE_INPUT and the current --input into temporary outputs, then show the generated-code diff "
        "from baseline to current. "
        "Requires --input and --output; --output is a virtual output path that selects file or directory layout "
        "and is never modified. Exits with code 1 when generated outputs differ."
    ),
)
general_options.add_argument(
    "--debug",
    help="show debug message (require \"debug\". `$ pip install 'datamodel-code-generator[debug]'`)",
    action="store_true",
    default=None,
)
general_options.add_argument(
    "--disable-warnings",
    help="disable warnings",
    action="store_true",
    default=None,
)
general_options.add_argument(
    "--list-deprecations",
    help="List registered deprecations and scheduled breaking changes, then exit.",
    nargs="?",
    const="table",
    choices=["table", "json", "markdown"],
    metavar="{table,json,markdown}",
    default=None,
)
general_options.add_argument(
    "--list-experimental",
    help="List registered experimental features and their compatibility notes, then exit.",
    nargs="?",
    const="table",
    choices=["table", "json", "markdown"],
    metavar="{table,json,markdown}",
    default=None,
)
general_options.add_argument(
    "-h",
    "--help",
    action="help",
    default="==SUPPRESS==",
    help="show this help message and exit",
)
general_options.add_argument(
    "--no-color",
    action="store_true",
    default=False,
    help="disable colorized output",
)
general_options.add_argument(
    "--output-format",
    choices=["text", "json"],
    default=None,
    help="Format for command output (default: text). Use json for structured output when supported.",
)
general_options.add_argument(
    "--fail-on-multi-module-stdout",
    action="store_true",
    default=None,
    help="Return an error instead of concatenating multiple generated modules in text stdout. "
    "This does not affect single-module, JSON, or file output.",
)
general_options.add_argument(
    "--output-format-json-schema",
    choices=["config", "generate-prompt", "generation", "model-metadata", "structured-output"],
    default=None,
    metavar="{config,generate-prompt,generation,model-metadata,structured-output}",
    help="Output JSON Schema for the selected JSON output or JSON configuration format and exit.",
)
general_options.add_argument(
    "--generate-pyproject-config",
    action="store_true",
    default=None,
    help="Generate pyproject.toml configuration from the provided CLI arguments and exit",
)
general_options.add_argument(
    "--generate-cli-command",
    action="store_true",
    default=None,
    help="Generate CLI command from pyproject.toml configuration and exit",
)
general_options.add_argument(
    "--generate-prompt",
    type=str,
    nargs="?",
    const="",
    default=None,
    metavar="QUESTION",
    help=(
        "Generate a prompt for consulting LLMs about CLI options. "
        "Optionally provide your question as an argument. "
        "Pipe to CLI tools (e.g., `| claude -p`, `| codex exec`) "
        "or copy to clipboard (e.g., `| pbcopy`, `| xclip`) for web LLM chats."
    ),
)
general_options.add_argument(
    "--ignore-pyproject",
    action="store_true",
    default=False,
    help="Ignore pyproject.toml configuration",
)
general_options.add_argument(
    "--install-skill",
    choices=["codex", "claude-code"],
    default=None,
    metavar="{codex,claude-code}",
    help=(
        "Install the bundled datamodel-code-generator Agent Skill and exit (experimental). "
        "Use --skill-scope to select a project or personal installation."
    ),
)
general_options.add_argument(
    "--skill-scope",
    choices=["project", "user"],
    default=None,
    metavar="{project,user}",
    help=(
        "Installation scope for --install-skill (default: project). "
        "Project installs use .agents/skills or .claude/skills; user installs use the matching home directory."
    ),
)
general_options.add_argument(
    "--overwrite-skill",
    action="store_true",
    default=False,
    help="Replace an existing regular skill directory when used with --install-skill.",
)
general_options.add_argument(
    "--profile",
    help="Use a named profile from pyproject.toml [tool.datamodel-codegen.profiles.<name>]",
    default=None,
)
job_selection_options = general_options.add_mutually_exclusive_group()
job_selection_options.add_argument(
    "--job",
    action="append",
    default=None,
    metavar="NAME",
    help="Run a named job from pyproject.toml [tool.datamodel-codegen.jobs.<name>] (experimental). Can be repeated.",
)
job_selection_options.add_argument(
    "--all-jobs",
    action="store_true",
    default=None,
    help="Run every named job from pyproject.toml in declaration order (experimental).",
)
general_options.add_argument(
    "--watch",
    action="store_true",
    default=None,
    help="Watch input file(s) for changes and regenerate output automatically",
)
general_options.add_argument(
    "--watch-delay",
    type=float,
    default=None,
    help="Debounce delay in seconds for watch mode (default: 0.5)",
)
general_options.add_argument(
    "--version",
    action="store_true",
    help="show version",
)

__all__ = [
    "DEFAULT_ENCODING",
    "arg_parser",
    "namespace",
]
