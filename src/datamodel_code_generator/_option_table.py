"""The command-line options of datamodel-codegen: one row per option, in the order the parser registers them.

``arguments.py`` adds each row to the argument parser, in its help section or its set of mutually exclusive options.
A row holds the flags of the option and the arguments argparse takes for it, written by the constructor of its form
(``toggle``, ``flag``, ``deprecated``, ``clear`` or ``value``), and the cells of its documentation, from which
``cli_options.py`` derives ``CLI_OPTION_META``.
"""

from __future__ import annotations

import json
from argparse import ArgumentTypeError, BooleanOptionalAction
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Unpack, cast, get_args

from datamodel_code_generator._config_fields import DEFAULT_ENCODING, DEFAULT_WATCH_DELAY
from datamodel_code_generator._format_types import DateClassType, DatetimeClassType, Formatter, PythonVersion
from datamodel_code_generator._json_output import JSON_SCHEMA_NAMES
from datamodel_code_generator._message_text import extra_requirement, install_command
from datamodel_code_generator._option_names import field_name, option_flags, split_external_ref_mapping
from datamodel_code_generator._records import record
from datamodel_code_generator._shared_types import LiteralType
from datamodel_code_generator.deprecations import DeprecationFormat, cli_deprecations
from datamodel_code_generator.enums import (
    DEFAULT_SHARED_MODULE_NAME,
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    ClassNameAffixScope,
    ClientAsyncAPIPerspective,
    ClientBodyArguments,
    ClientSignatureStyle,
    ClientType,
    CollapseRootModelsNameStrategy,
    ConstructorFieldOrder,
    CustomFileHeaderMode,
    DataclassArguments,
    DataModelType,
    DefaultValueType,
    ExtraFields,
    FieldTypeCollisionStrategy,
    HTTPBackend,
    InputFileType,
    InputModelRefStrategy,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIAsDefinitions,
    OpenAPIScope,
    OpenEnums,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    ServerBodyMode,
    ServerHandlerMode,
    ServerLayout,
    ServerType,
    StrictTypes,
    TargetPydanticVersion,
    UnionMode,
    VersionMode,
)
from datamodel_code_generator.preset_names import PRESET_NAMES

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._types.generate_config_dict import BaseGenerateConfig as BaseGenerateConfigDict


class OptionCategory(str, Enum):
    """Categories for organizing CLI options in documentation."""

    BASE = "Base Options"
    TYPING = "Typing Customization"
    FIELD = "Field Customization"
    MODEL = "Model Customization"
    TEMPLATE = "Template Customization"
    OPENAPI = "OpenAPI-only Options"
    GRAPHQL = "GraphQL-only Options"
    GENERAL = "General Options"
    TARGET = "Target Generation Options"


class OptionTopic(str, Enum):
    """Optional topics for linking CLI options to focused documentation areas."""

    MODEL_CUSTOMIZATION = "model-customization"
    TEMPLATE_CUSTOMIZATION = "template-customization"
    TYPING_CUSTOMIZATION = "typing-customization"
    OPENAPI = "openapi"


class OptionGroup(str, Enum):
    """Optional subgroups within CLI option topics."""

    MODEL_NAMING = "model-naming"
    MODEL_REUSE = "model-reuse"
    MODEL_SHAPE = "model-shape"
    ROOT_MODEL = "root-model"
    CUSTOM_TEMPLATES = "custom-templates"
    GENERATED_OUTPUT = "generated-output"
    IMPORTS = "imports"
    OUTPUT_FORMATTING = "output-formatting"
    COLLECTION_TYPES = "collection-types"
    TYPE_ALIAS = "type-alias"
    TYPE_MAPPING = "type-mapping"
    TYPE_SYNTAX = "type-syntax"
    OPENAPI_NAMING = "openapi-naming"
    OPENAPI_PATHS = "openapi-paths"
    OPENAPI_SCOPES = "openapi-scopes"
    READ_ONLY_WRITE_ONLY = "read-only-write-only"


@dataclass(frozen=True)
class CLIOptionRelation:
    """A documented relationship from one CLI option to another.

    ``when=None`` means the relation applies whenever the source option is enabled.
    """

    option: str
    value: Any = None
    when: Any = None
    message: str | None = None


class Section(Enum):
    """A help section of the command line, by its title, in the order the help lists them."""

    BASE = "Options"
    TYPING = "Typing customization"
    FIELD = "Field customization"
    MODEL = "Model customization"
    TEMPLATE = "Template customization"
    OPENAPI = "OpenAPI-only options"
    GRAPHQL = "GraphQL-only options"
    GENERAL = "General options"
    TARGET = "Target generation options (experimental)"


class Exclusive(Enum):
    """A set of options of which a command line gives one at most, and the help section of its options."""

    EXTRA_FIELDS = ("extra fields", Section.MODEL)
    TARGET_SELECTION = ("target selection", Section.TARGET)
    REMOTE_LOCK = ("remote lock", Section.BASE)
    JOB_SELECTION = ("job selection", Section.GENERAL)

    @property
    def section(self) -> Section:
        """The help section of the options of the set."""
        return self.value[1]


@record
class PresetJoin:
    """The dated presets an opt-in option joins, and the option group that documents it there.

    The presets of ``date`` set the config field of the option to its value in ``config``, written as ``PresetConfig``
    takes it, so the type checker reads the field and its value; the group takes the ``title`` and ``description`` the
    preset pages list, and applies only to the output model types, input file types and Python targets its cells name.
    """

    date: str
    config: BaseGenerateConfigDict
    title: str
    description: str
    output_model_types: frozenset[DataModelType] = frozenset()
    input_file_types: frozenset[InputFileType] = frozenset()
    requires_python_strenum: bool = False


@record
class Option:
    """A command-line option: its flags, where the parser adds it, the arguments argparse takes for it, and its cells.

    The cells are those of its documentation: its category, unset where it is that of its help section, its group
    and topic, the relations that start from its positive flag, and whether only its --no- form is documented;
    whether a config carries its value (``config``), false for the options of a command, such as ``--help``; and the
    dated presets it joins (``preset``).
    """

    flags: tuple[str, ...]
    place: Section | Exclusive
    arguments: Mapping[str, Any]
    category: OptionCategory | None = None
    group: OptionGroup | None = None
    topic: OptionTopic | None = None
    implies: tuple[CLIOptionRelation, ...] = ()
    requires: tuple[CLIOptionRelation, ...] = ()
    conflicts: tuple[CLIOptionRelation, ...] = ()
    meta_on_negative_only: bool = False
    config: bool = True
    preset: PresetJoin | None = None

    @property
    def section(self) -> Section:
        """The help section of the option."""
        return self.place if isinstance(self.place, Section) else self.place.section

    @property
    def dest(self) -> str:
        """The name argparse stores the value under: the ``dest`` given, or the field its first long flag names."""
        return self.arguments.get("dest") or field_name(next(f for f in self.flags if f.startswith("--"))[2:])


_CELLS: Final = frozenset({"implies", "requires", "conflicts", "meta_on_negative_only", "config", "preset"})


_DOC_CELLS: Final = {OptionCategory: "category", OptionGroup: "group", OptionTopic: "topic"}
Place = Section | Exclusive | tuple[Section | Exclusive, Unpack[tuple[OptionCategory | OptionGroup | OptionTopic, ...]]]


def _option(flags: tuple[str, ...], place: Place, arguments: Mapping[str, Any], **cells: Any) -> Option:
    """Return the row; a place written as a tuple also names the documentation category, group or topic."""
    if isinstance(place, tuple):
        place, *doc = place
        cells |= {_DOC_CELLS[type(cell)]: cell for cell in doc}
    return Option(flags, place, arguments, **cells)


def toggle(place: Place, *flags: str, help: str, **cells: Any) -> Option:  # noqa: A002
    """Return a flag with a --no- form for each of its flags, whose default is unset (None).

    An unset default lets a configuration file or a preset supply the value, which the --no- form turns off.
    """
    return _option(flags, place, {"action": BooleanOptionalAction, "default": None, "help": help}, **cells)


def flag(place: Place, *flags: str, help: str, default: bool | None = None, **cells: Any) -> Option:  # noqa: A002
    """Return a flag that is off unless given; an unset default (None) lets a configuration file supply it."""
    return _option(flags, place, {"action": "store_true", "default": default, "help": help}, **cells)


def deprecated(place: Place, name: str, **cells: Any) -> Option:
    """Return a deprecated flag, whose help is its message in the deprecation registry."""
    return flag(place, name, help=f"Deprecated: {_DEPRECATED_OPTIONS[name]}", **cells)


def clear(place: Place, name: str, *, help: str, **cells: Any) -> Option:  # noqa: A002
    """Return the --no- form of a list option, which sets it to no items, clearing what a configuration gives it."""
    dest = name.removeprefix("--no-").replace("-", "_")
    arguments = {"dest": dest, "action": "store_const", "const": (), "default": None, "help": help}
    return _option((name,), place, arguments, **cells)


def value(place: Place, *flags: str, help: str, **arguments: Any) -> Option:  # noqa: A002
    """Return an option that takes a value, with argparse's ``arguments``; unless given, its value is unset (None)."""
    cells = {name: arguments.pop(name) for name in _CELLS & arguments.keys()}
    return _option(flags, place, {"help": help, **arguments}, **cells)


def _relations(names: str, **fields: Any) -> tuple[CLIOptionRelation, ...]:
    """Build one relation with the same ``fields`` to each option in the space-separated ``names``."""
    return tuple(CLIOptionRelation(name, **fields) for name in names.split())


def _values(kind: type[Enum]) -> list[Any]:
    """Return the choices of an option that takes an enum: the values of its members, in their order."""
    return [member.value for member in kind]


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
    try:
        split_external_ref_mapping(value)
    except ValueError as error:
        raise ArgumentTypeError(str(error)) from None
    return value


def _json_value_or_file(value: str) -> dict[str, object]:
    """Parse a JSON value or load it from a JSON file path."""
    from datamodel_code_generator.json_config import JsonConfigError, validate_json_value_or_file  # noqa: PLC0415

    try:
        return validate_json_value_or_file(value)
    except JsonConfigError as e:
        msg = str(e)
        raise ArgumentTypeError(msg) from e


def _recommend_mode(value: str) -> str:
    """Accept a recommendation mode, quick or full; the option is documented without a value, which picks by size."""
    if value not in {"quick", "full"}:
        from datamodel_code_generator.python_literal import stable_repr  # noqa: PLC0415

        msg = f"unknown mode {stable_repr(value)}; use --recommend-options without a value"
        raise ArgumentTypeError(msg)
    return value


def _package_options(kind: str) -> tuple[Option, ...]:
    """Return the output directory and package options of the generated server or client package."""
    return tuple(
        value(Section.TARGET, f"--{kind}-{name}", help=help_text)
        for name, help_text in (
            ("output", f"Directory of the generated {kind} package (experimental)."),
            ("package", f"Import path of the generated {kind} package (experimental)."),
            ("model-package", "Import path of the models that --output generates (experimental)."),
        )
    )


def _list_options() -> tuple[Option, ...]:
    """Return the options that list a registry, deprecations or experimental features, then exit."""
    formats = list(get_args(DeprecationFormat))
    return tuple(
        value(
            Section.GENERAL,
            f"--list-{registry}",
            help=f"List registered {contents}, then exit.",
            nargs="?",
            const="table",
            choices=formats,
            metavar=f"{{{','.join(formats)}}}",
        )
        for registry, contents in (
            ("deprecations", "deprecations and scheduled breaking changes"),
            ("experimental", "experimental features and their compatibility notes"),
        )
    )


_C = OptionCategory
_G = OptionGroup
_T = OptionTopic
_DEPRECATED_OPTIONS = cli_deprecations()

OPTIONS: Final[tuple[Option, ...]] = (
    # Base options for input/output
    toggle(
        (Section.BASE, _C.GENERAL),
        "--allow-remote-refs",
        help="Allow fetching remote $ref references over HTTP/HTTPS. "
        "Currently remote fetching is allowed by default but emits a deprecation warning. "
        "Pass --allow-remote-refs to opt in without warning, "
        "or --no-allow-remote-refs to block remote fetching. "
        "In a future version, remote fetching will be disabled by default.",
    ),
    flag(
        (Section.BASE, _C.GENERAL),
        "--strict-refs",
        help="Treat unresolved local $ref JSON pointers as errors instead of generating fallback Any models.",
    ),
    toggle(
        (Section.BASE, _C.GENERAL),
        "--untrusted-input",
        help="Treat the input as a schema from an untrusted source (experimental): read no file or URL but the input, "
        "so every $ref and XML Schema include must name a location in the input; refuse the schema keywords whose "
        "values the generated module imports or evaluates as Python code (x-python-type, x-python-import, "
        "customTypePath, customBasePath), Protocol Buffers input, --validation and a schema nested more than 100 "
        "levels deep or holding more than 2,000,000 objects and arrays. Implies --no-allow-remote-refs. Pass "
        "--no-untrusted-input to override a configuration file that enables it.",
        implies=_relations("--allow-remote-refs", value=False),
    ),
    toggle(
        (Section.BASE, _C.GENERAL),
        "--allow-private-network",
        help="Allow HTTP(S) schema requests to private, loopback, link-local, or otherwise non-public network hosts. "
        "By default these targets are blocked to reduce server-side request forgery (SSRF) risk. "
        "If a trusted internal schema endpoint is blocked, verify the URL and pass this option; otherwise use a "
        "local schema file or public endpoint. "
        "Pass --no-allow-private-network to override a configuration file that enables it.",
    ),
    value(
        Section.BASE,
        "--http-backend",
        choices=_values(HTTPBackend),
        help="Select the HTTP client backend. 'auto' (default) selects stable HTTPX when its client module is "
        "installed and only selects experimental HTTPX2 when that module is absent. 'httpx' and 'httpx2' require that "
        "exact backend. Explicit selections and paired dependency errors do not fall back.",
    ),
    value(
        (Section.BASE, _C.GENERAL),
        "--http-headers",
        nargs="+",
        metavar="HTTP_HEADER",
        help='Set headers in HTTP requests to the remote host. (example: "Authorization: Basic dXNlcjpwYXNz")',
    ),
    value(
        (Section.BASE, _C.GENERAL),
        "--http-query-parameters",
        nargs="+",
        metavar="HTTP_QUERY_PARAMETERS",
        help='Set query parameters in HTTP requests to the remote host. (example: "ref=branch")',
    ),
    flag(
        (Section.BASE, _C.GENERAL),
        "--http-ignore-tls",
        help="Disable verification of the remote host's TLS certificate",
    ),
    value(
        (Section.BASE, _C.GENERAL),
        "--http-timeout",
        type=float,
        help="Timeout in seconds for HTTP requests to remote hosts (default: 30)",
    ),
    value(
        (Section.BASE, _C.GENERAL),
        "--http-local-ref-path",
        help="Resolve HTTP(S) JSON Schema $ref URLs from a local directory instead of fetching them. "
        "URLs are mapped under the directory by host and path; extensionless refs also try '.json'.",
    ),
    value(
        (Section.BASE, _C.GENERAL),
        "--lockfile",
        help="Select the remote reference integrity lock file (experimental). "
        "An existing selected lock is verified automatically; "
        "a missing selected lock is ignored unless --locked is used. "
        "The default is datamodel-codegen.lock beside the discovered pyproject.toml, or in the invocation working "
        "directory when no project is found. Explicit relative paths resolve from the invocation working directory.",
    ),
    flag(
        (Exclusive.REMOTE_LOCK, _C.GENERAL),
        "--update-lock",
        help="Create or atomically update the selected remote lock after generation (experimental).",
    ),
    flag(
        (Exclusive.REMOTE_LOCK, _C.GENERAL),
        "--locked",
        help="Require an existing remote lock and validate each fetched resource against it (experimental).",
    ),
    value(Section.BASE, "--input", help="Input file/directory (default: stdin)"),
    value(
        Section.BASE,
        "--input-file-type",
        help="Input file type (default: auto). "
        "Use 'jsonschema', 'openapi', 'asyncapi', 'graphql', 'mcp-tools', 'xmlschema', 'protobuf', or 'avro' "
        "for schema definitions. "
        "Use 'json', 'yaml', or 'csv' for raw sample data to infer a schema automatically; "
        "Genson infers it and will become optional: declare the genson extra.",
        choices=_values(InputFileType),
    ),
    value(
        Section.BASE,
        "--external-ref-mapping",
        nargs="+",
        metavar="FILE_PATH=PYTHON_PACKAGE",
        type=_external_ref_mapping,
        help="Map external $ref file paths to Python import packages instead of generating duplicate classes. "
        'Accepts one or more mappings after a single flag. Format: "path/to/schema.yaml=mypackage.models". '
        "When a $ref points to a mapped file, an import statement is generated instead of a class definition.",
    ),
    value(Section.BASE, "--output", help="Output file (default: stdout)"),
    value(
        Section.BASE,
        "--emit-model-metadata",
        type=Path,
        help="Write a separate JSON map from generated models and fields to the JSON Pointer locations of their source "
        "schemas; module and class name identify a model.",
    ),
    value(
        (Section.BASE, _C.MODEL, _G.MODEL_SHAPE),
        "--output-model-type",
        help="Output model type (default: pydantic_v2.BaseModel)",
        choices=_values(DataModelType),
        implies=_relations("--use-annotated", value=True, when=DataModelType.MsgspecStruct.value),
    ),
    value(
        Section.BASE,
        "--preset",
        help="Apply an immutable built-in option preset. "
        "Preset names include the target Python version so generated syntax is pinned.",
        choices=PRESET_NAMES,
    ),
    value(
        Section.BASE,
        "--url",
        help="Input file URL. `--input` is ignored when `--url` is used. "
        f"For HTTP(S), {extra_requirement('http')} remains the stable HTTPX backend and is not deprecated, while "
        f"{extra_requirement('httpx2')} is experimental. The default --http-backend auto policy selects stable "
        "HTTPX when its client module is installed and selects HTTPX2 only when that module is absent. Select "
        "--http-backend httpx2 to require the experimental backend. Explicit selections and paired dependency "
        "errors do not fall back.",
    ),
    value(
        Section.BASE,
        "--input-model",
        action="append",
        help="Python import path or file path to a Pydantic v2 model or schema dict "
        "(e.g., 'mypackage.module:ClassName', './models.py:ClassName', or 'mypackage.schemas:SCHEMA_DICT'). "
        "Can be specified multiple times for related models with inheritance. "
        "For dict input, --input-file-type is required. "
        "Cannot be used with --input or --url.",
        metavar="MODULE_OR_PATH:NAME",
    ),
    value(
        Section.BASE,
        "--input-model-ref-strategy",
        help="Strategy for referenced types in --input-model. "
        "'regenerate-all': Regenerate all types. "
        "'reuse-foreign': Reuse types from different families (Enum, etc.), regenerate same-family. "
        "'reuse-all': Reuse all referenced types via import. "
        "If not specified, defaults to regenerate-all behavior.",
        choices=_values(InputModelRefStrategy),
    ),
    toggle(
        Section.BASE,
        "--read-input-model-dataclass-fields-by-alias",
        help="With --input-model, read the fields of a Pydantic dataclass by their aliases, as those of a model, so a "
        "field with an alias keeps its Python type (deque[int], frozenset[str], ...) like one without; without it an "
        "aliased field of a Pydantic dataclass takes the type its JSON Schema spells (list[int]), as in 0.89.0",
    ),
    toggle(
        Section.BASE,
        "--reuse-every-reached-input-model-type",
        help="With --input-model-ref-strategy reuse-all or reuse-foreign, reach the types to reuse through the fields "
        "of every model kind, dataclasses, TypedDicts and msgspec Structs too, and import a type that the Python type "
        "of a field already imports instead of writing an unused copy of it; without it reuse descends through "
        "Pydantic models only and such a copy is written (ItemModel beside Mapping[str, Item]), as in 0.89.0",
    ),
    # Customization options for generated models
    deprecated(Exclusive.EXTRA_FIELDS, "--allow-extra-fields", implies=_relations("--extra-fields", value="allow")),
    toggle(Section.MODEL, "--allow-population-by-field-name", help="Allow population by field name"),
    value((Section.MODEL, _G.MODEL_NAMING), "--class-name", help="Set class name of root model"),
    value(
        (Section.MODEL, _G.MODEL_NAMING),
        "--model-name-map",
        help="Rename generated model classes by schema ref or current generated class name "
        "using a JSON object or JSON file.",
        type=_json_value_or_file,
    ),
    flag(
        (Section.MODEL, _G.MODEL_NAMING),
        "--allow-leading-underscore-class-name",
        help="Allow an explicitly specified root class name to start with an underscore",
    ),
    value(
        (Section.MODEL, _G.MODEL_NAMING),
        "--class-name-prefix",
        help="Prefix to add to generated class names (e.g., 'Api' produces 'ApiUser'). "
        "Does not apply to root model when --class-name is specified.",
        type=str,
    ),
    value(
        (Section.MODEL, _G.MODEL_NAMING),
        "--class-name-suffix",
        help="Suffix to add to generated class names (e.g., 'Schema' produces 'UserSchema'). "
        "Does not apply to root model when --class-name is specified.",
        type=str,
    ),
    value(
        (Section.MODEL, _G.MODEL_NAMING),
        "--class-name-affix-scope",
        help="Scope for applying --class-name-prefix/--class-name-suffix. "
        "'all': Apply to all classes including enums (default). "
        "'models': Apply only to model classes. "
        "'enums': Apply only to enum classes.",
        choices=_values(ClassNameAffixScope),
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--collapse-root-models",
        help="Models generated with a root-type field will be merged into the models using that root-type model",
    ),
    value(
        (Section.MODEL, _G.ROOT_MODEL),
        "--collapse-root-models-name-strategy",
        help="Strategy for naming when collapsing root models that reference other models. "
        "'child': Keep inner model's name (default). 'parent': Use wrapper's name for inner model. "
        "Requires --collapse-root-models to be set.",
        choices=_values(CollapseRootModelsNameStrategy),
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--collapse-shared-parent-wrappers",
        help="Under --collapse-root-models-name-strategy parent, make every model that references a wrapper reference "
        "the inner model that takes its name, as its first model does; without it, where "
        "--field-type-collision-strategy rename-type can rename the wrapper apart from the inner model in their "
        "module, the later models keep the wrapper",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--keep-constrained-root-models-in-every-container",
        help="Under --collapse-root-models with --field-constraints, keep a root model whose root field has "
        "constraints named inside any container of its holder, a set, frozen set, Sequence or Mapping as well as a "
        "list, dict or union, as `Field()` could not carry them there; without it such a root model is inlined into "
        "those containers and its constraints are lost, as in 0.89.0",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--keep-enum-literal-aliases",
        help="Under --collapse-root-models, keep a named enum that the output writes as a `Literal` type alias named "
        "at its uses (`Status = Literal['open', 'resolved']`, `a: Status`); without it every use repeats the "
        "`Literal`, as in 0.89.0",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--keep-reference-null-on-collapsed-root-models",
        help="Under --collapse-root-models, keep the null of a root model that admits null where a field, item or map "
        "value that names it takes its type (an OpenAPI 3.0 `nullable: true` root: `list[str] | None`), as the root "
        "model admits it; without it the inlined type rejects null, as in 0.89.0",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--keep-shared-members-on-collapsed-root-models",
        help="Under --collapse-root-models, keep each member of a root type that collapse inlined at several sites at "
        "every site when a root model that member names is collapsed later, as a recursive union reaches its plain "
        'member (`dict[str, Union["Entry", str]]`); without it those sites write the union without that member, or '
        "`Any` where no member is left, as in 0.90.0",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--collapse-constrained-root-models-as-annotated",
        help="Under --collapse-root-models with --use-annotated, inline a root model of a string with constraints into "
        "the union, list, dict or other container of its holder as `Annotated[str, Field(...)]`, which keeps the "
        "constraints on that member; without it such a root model stays named there, as in 0.90.0",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--remove-unused-models-beside-type-overrides",
        help="Remove the models that nothing uses, such as a root model --collapse-root-models inlined, also from a "
        "module a model-level --type-overrides key takes a model out of, as from every other module; without it they "
        "stay written there, as in 0.90.0",
    ),
    toggle(
        (Section.MODEL, _G.ROOT_MODEL),
        "--keep-self-referencing-array-items",
        help="Keep the items of an array whose items name the array itself (`Node = array of Node | Leaf`: "
        "`RootModel[list[Node | Leaf]]`), and serialize the root of a type list that lists such an array as any "
        "subclass under --use-serialize-as-any; msgspec output, which cannot decode such a type alias, keeps the bare "
        "`list`; without it the array is written as a bare `list`, as in 0.90.0",
    ),
    toggle(
        (Section.MODEL, _G.MODEL_REUSE),
        "--collapse-reuse-models",
        help="When used with --reuse-model, collapse duplicate models by replacing references instead of creating "
        "empty inheritance subclasses. This eliminates 'class Foo(Bar): pass' patterns",
    ),
    flag(
        (Section.MODEL, _G.ROOT_MODEL),
        "--skip-root-model",
        help="Skip generating the model for the root schema element",
    ),
    flag(
        (Section.MODEL, _G.ROOT_MODEL),
        "--use-root-model-sequence-interface",
        help="Make non-null sequence-like Pydantic v2 RootModel classes implement collections.abc.Sequence by adding "
        "Sequence[T] inheritance and root-delegating __iter__, __getitem__, and __len__ methods",
    ),
    flag(
        (Section.MODEL, _C.TEMPLATE),
        "--disable-appending-item-suffix",
        help="Disable appending `Item` suffix to model name in an array",
    ),
    toggle(
        (Section.MODEL, _G.MODEL_NAMING),
        "--singularize-last-word-of-item-names",
        help="Name the item model of an array by singularizing only the last word of its name (actions_caches gives "
        "ActionsCache, payment_status gives PaymentStatusItem); without it the whole name is singularized as one word "
        "(ActionsCach, PaymentStatu), as in 0.88.0",
    ),
    toggle(
        (Section.MODEL, _C.TEMPLATE, _G.GENERATED_OUTPUT),
        "--disable-timestamp",
        help="Disable timestamp on file headers",
    ),
    flag(Section.MODEL, "--enable-faux-immutability", help="Enable faux immutability"),
    flag(
        (Section.MODEL, _C.TEMPLATE, _G.GENERATED_OUTPUT),
        "--enable-version-header",
        help="Enable package version on file headers",
    ),
    flag(
        (Section.MODEL, _C.TEMPLATE, _G.GENERATED_OUTPUT),
        "--enable-command-header",
        help="Enable command-line options on file headers for reproducibility",
    ),
    flag(
        (Section.MODEL, _C.TEMPLATE, _G.GENERATED_OUTPUT),
        "--enable-generated-header-marker",
        help="Enable @generated marker on file headers",
    ),
    value(
        (Exclusive.EXTRA_FIELDS, _C.FIELD),
        "--extra-fields",
        help="Set the generated models to allow, forbid, or ignore extra fields.",
        choices=_values(ExtraFields),
    ),
    flag(Section.MODEL, "--keep-model-order", help="Keep generated models' order"),
    value(
        Section.MODEL,
        "--order-constructor-fields-by",
        help="Decide the order of constructor fields by each given value. final-names: order the constructor fields of "
        "dataclass and msgspec.Struct models after every field has its final name, so a field that overrides an "
        "inherited field renamed in another module is matched to it and keeps its place; without it the override reads "
        "as a new field after the inherited default and is written keyword-only (kw_only=True). written-defaults: "
        "order the fields of msgspec.Struct and Pydantic dataclass models by the defaults they write, so under "
        "--strip-default-none an optional field whose None default is stripped but still written (UNSET, or None for a "
        "type that admits None) goes after the required fields of a Struct with the other optional fields and stays "
        "positional after an inherited default, and a required pydantic.dataclass field redeclaring it is written "
        "Field(...) (a msgspec.Struct writes a bare annotation); without it the field keeps its place among the fields "
        "without a default, as before: it is keyword-only after an inherited default, a redeclaring field inherits its "
        "None default, and the fields are reordered only where a required field would then follow a default.",
        choices=_values(ConstructorFieldOrder),
        nargs="+",
    ),
    flag(Section.MODEL, "--keyword-only", help="Defined models as keyword only (for example dataclass(kw_only=True))."),
    flag(
        Section.MODEL,
        "--frozen-dataclasses",
        help="Generate frozen dataclasses (dataclass(frozen=True)). Only applies to dataclass output.",
    ),
    value(
        Section.MODEL,
        "--dataclass-arguments",
        type=_dataclass_arguments,
        help="Custom dataclass arguments as a JSON dictionary, "
        'e.g. \'{"frozen": true, "kw_only": true}\'. '
        "Overrides --frozen-dataclasses and similar flags.",
    ),
    toggle(
        Section.MODEL,
        "--keep-false-dataclass-arguments",
        help="Write every dataclass decorator argument set to false, such as eq=False from --dataclass-arguments "
        "'{\"eq\": false}', so the dataclass compares by identity as asked; without it an argument set to false is "
        "left out and takes its default (eq=True), as in 0.89.0",
    ),
    flag(
        Section.MODEL,
        "--use-msgspec-tagged-unions",
        help="msgspec.Struct output only: write a discriminated union as msgspec tagged structs as a whole. Every "
        "variant is tagged with the document's property name and its value, or none is and a warning says why. Without "
        "it the 0.83.0 form is kept, which tags only a variant whose discriminator is a single literal and keeps the "
        "other discriminators as Literal fields.",
    ),
    toggle(
        Section.MODEL,
        "--tag-msgspec-by-payload-name",
        help="msgspec.Struct output only: tag the structs of a discriminated union with the property name the document "
        "carries (for example 'pet-kind') instead of the Python name that property resolves to (pet_kind), which "
        "msgspec looks for in the payload. --use-msgspec-tagged-unions already tags by the document's property name. "
        "Without either option the Python name is kept.",
    ),
    toggle(
        Section.MODEL,
        "--use-msgspec-meta-for-map-keys",
        help="msgspec.Struct output only: write the keys of a map whose keys propertyNames or patternProperties "
        "constrain by pattern, minLength or maxLength as Annotated[str, Meta(...)], so msgspec checks them as Pydantic "
        "output does; without it the keys are str and every key is accepted, as in 0.88.0",
    ),
    toggle(
        Section.MODEL,
        "--use-msgspec-forbid-unknown-fields",
        help="msgspec.Struct output only: write forbid_unknown_fields=True on a Struct whose schema admits no property "
        "but its own (additionalProperties: false without patternProperties beside it, also in an inline allOf member, "
        "or unevaluatedProperties: false in an inline allOf member), so msgspec rejects the unknown fields the schema "
        "rejects, as Pydantic output does with extra='forbid'; --extra-fields forbid writes it on every Struct, also "
        "without --use-generic-base-class, and --extra-fields allow or ignore and --allow-extra-fields keep it open. "
        "Without it such a Struct ignores unknown fields, as in 0.89.0",
    ),
    toggle(
        Section.MODEL,
        "--merge-msgspec-str-like-union-members",
        help="msgspec.Struct output only: write one str in place of the str-like members of a union (str, UUID, the "
        "dates and times, Decimal, bytes, the str values of a Literal, and type aliases of them or of unions of only "
        "them) when it holds more than one of them, which msgspec refuses to decode; the union then accepts every "
        "string and decodes it as str, without the formats and constraints of those members, with a warning. A merged "
        "member that admits null leaves None in the union, and a merged Literal keeps its values that are no str. A "
        "union whose str-like members include an Enum of str values, a member typed by x-python-type, a member built "
        "for an integer or number schema (a number of format decimal, time-delta or date-time), whose JSON numbers "
        "one str would refuse, an alias of a union that mixes str-like and other members or an alias of a Literal of "
        "str and other values is left as it is, with its warning. Without it such a union is written as the schema "
        "gives it, with a warning, as in 0.99.0",
    ),
    flag(
        Section.MODEL,
        "--use-msgspec-nullable-enum-alias",
        help="msgspec.Struct output only: write an enum that lists null as an Enum without the None member plus an "
        "Enum | None type alias, so that msgspec can decode it. Without it a plain Enum keeps the 0.83.0 None member, "
        "which msgspec cannot decode; an IntEnum or --use-subclass-enum enum, which cannot hold it, is written without "
        "it and, as its one type rejects null, without the alias.",
    ),
    toggle(
        (Section.MODEL, _G.MODEL_REUSE),
        "--reuse-model",
        help="Reuse models on the field when a module has the model with the same content",
    ),
    value(
        (Section.MODEL, _G.MODEL_REUSE),
        "--reuse-scope",
        help="Scope for model reuse deduplication: module (per-file, default) or tree (cross-file with shared module). "
        "Only effective when --reuse-model is set.",
        choices=_values(ReuseScope),
    ),
    value(
        (Section.MODEL, _C.GENERAL, _G.MODEL_REUSE, _T.MODEL_CUSTOMIZATION),
        "--shared-module-name",
        help=f'Name of the shared module for --reuse-scope=tree (default: "{DEFAULT_SHARED_MODULE_NAME}"). '
        f'Use this option if your schema has a file named "{DEFAULT_SHARED_MODULE_NAME}".',
    ),
    value(
        (Section.MODEL, _G.MODEL_SHAPE),
        "--target-python-version",
        help="target python version",
        choices=_values(PythonVersion),
    ),
    value(
        (Section.MODEL, _G.MODEL_SHAPE),
        "--target-pydantic-version",
        help="Oldest Pydantic version the generated code must support. "
        "Unset: the newest supported feature forms, with populate_by_name config like '2' "
        "and the experimental MISSING import like '2.12'. "
        "'2': Pydantic 2.0.3+ compatible (uses populate_by_name; patterns that need Python's regex engine need 2.5+). "
        "'2.11': Pydantic 2.11+ (uses validate_by_name). "
        "'2.12': Pydantic 2.12+ (supports MISSING sentinel). "
        "'2.14': Pydantic 2.14+ (imports MISSING from pydantic; bytes patterns can select Python's regex engine; "
        "pydantic_v2.dataclass writes required Field(...) fields before the fields with defaults of the same class; "
        "a number schema keeps multipleOf on a timedelta). "
        "Constructs without an older form are written for every target, with a warning naming the Pydantic they need.",
        choices=_values(TargetPydanticVersion),
    ),
    value(
        Section.MODEL,
        "--alias-generator",
        help="Pydantic v2 BaseModel alias generator to use in ConfigDict. "
        "Matching generated aliases are omitted from individual Field() calls, "
        "except with --target-pydantic-version 2, which writes every alias.",
        choices=_values(AliasGenerator),
    ),
    toggle(
        Section.MODEL,
        "--pin-aliases-with-alias-generator",
        help="pydantic_v2.BaseModel and pydantic_v2.dataclass output: decide a field's alias by the alias generator "
        "its model applies, the one in its config or in the config it inherits. For a dataclass and a "
        "--target-pydantic-version older than 2.8, whose alias generators can differ from the generated aliases, write "
        "the generated alias on every field of a dataclass whose config names an alias generator. Without it a field "
        "keeps the alias it has without the generator, which the config then replaces.",
    ),
    toggle(
        (Section.MODEL, _C.TEMPLATE),
        "--treat-dot-as-module",
        help="Treat dotted schema names as module paths, creating nested directory structures (e.g., 'foo.bar.Model' "
        "becomes 'foo/bar.py'). Use --no-treat-dot-as-module to keep dots in names as underscores for single-file "
        "output.",
        meta_on_negative_only=True,
    ),
    toggle(
        (Section.MODEL, _C.TEMPLATE, _G.GENERATED_OUTPUT),
        "--strict-dotted-module-names",
        help="Only infer dotted schema names as module paths when every segment is a canonical Python identifier. "
        "This applies only to automatic inference and does not override --treat-dot-as-module or "
        "--no-treat-dot-as-module.",
    ),
    toggle(
        (Section.MODEL, _C.TEMPLATE, _G.IMPORTS),
        "--rename-submodules-hidden-by-classes",
        help="Rename a submodule named like a class of its package, such as pkg/Warn.py beside the class Warn of "
        "pkg/__init__.py, to the first free name with trailing underscores (pkg/Warn_.py), with the modules below it; "
        "the class keeps its name. Without it importing the submodule replaces the class in its package, so the models "
        "that use either fail to decode",
    ),
    flag(
        (Section.MODEL, _G.MODEL_SHAPE),
        "--use-generic-base-class",
        help="Generate a shared base class with model configuration (e.g., extra='forbid') "
        "instead of repeating the configuration in each model. Keeps code DRY.",
    ),
    toggle(
        (Section.MODEL, _G.MODEL_SHAPE),
        "--keep-bases-beside-generic-base-class",
        help="Keep the base classes a model is configured to inherit beside the generated base class of "
        "--use-generic-base-class (`class Tag(TagBase, BaseModel)`): the class --base-class-map, customBasePath or "
        "--base-class names, and the class --external-ref-mapping or x-python-import imports for a schema the model "
        "extends, as the model keeps a generated base (the generated base class is named `BaseModelModel` where a "
        "kept class is named `BaseModel`); without it the model inherits only the generated base class and the "
        "import of the class stays unused, as in 0.100.0.",
        preset=PresetJoin(
            "20261123",
            {"keep_bases_beside_generic_base_class": True},
            title="Configured bases beside the generic base class",
            description=(
                "Keep the classes a model is configured to inherit, from --base-class-map, customBasePath, "
                "--base-class or the class an --external-ref-mapping or x-python-import schema stands for, beside the "
                "generated base class of --use-generic-base-class, so the model keeps their members and their import "
                "is used."
            ),
        ),
    ),
    toggle(
        (Section.MODEL, _C.FIELD), "--use-schema-description", help="Use schema description to populate class docstring"
    ),
    toggle((Section.MODEL, _C.FIELD), "--use-title-as-name", help="use titles as class names of models"),
    flag(
        (Section.MODEL, _C.FIELD),
        "--infer-union-variant-names",
        help='Name inline oneOf/anyOf branch models after the value of their tag field (Pet + type: "cat" -> PetCat) '
        "instead of Pet1, Pet2",
    ),
    flag(
        (Section.MODEL, _C.FIELD),
        "--use-tag-as-union-variant-name",
        help="Name inline oneOf/anyOf branch models after their tag: what --infer-union-variant-names does, skipping "
        'tags whose class names collide, plus single-branch unions (type: "exec" -> LocalShellActionExec) and '
        'single-key branches ({"granular": ...} -> AskForApprovalGranular)',
    ),
    toggle(
        (Section.MODEL, _C.FIELD),
        "--infer-union-discriminators",
        help="Write a oneOf or anyOf that declares no discriminator as a discriminated union (Pydantic "
        "Field(discriminator=...), msgspec tagged structs) when every branch that is not null requires one property "
        "holding one string or integer value (a const or a one-value enum, also through allOf members and local "
        "$refs), with no value shared; in Pydantic output a union with a discriminator keyword keeps it. Valid "
        "payloads parse the same, except that a tag typed as a one-value enum class becomes its Literal value; with "
        "--use-one-literal-as-default a payload without the tag, which the schema requires, is rejected. An invalid "
        "payload reports the errors of the branch its tag names, or one tag error. Not applied to "
        "dataclasses.dataclass and typing.TypedDict output. Without it these unions are smart unions in Pydantic "
        "output, and msgspec output tags only the unions whose tags are written on the branches themselves, as in "
        "0.96.0",
    ),
    toggle(
        (Section.MODEL, _C.FIELD),
        "--name-allof-union-members-after-branches",
        help="Name the classes an allOf gives the branches of a oneOf or anyOf it holds after the branch: the holder "
        "and the branch's tag (with --infer-union-variant-names or --use-tag-as-union-variant-name), its $ref class or "
        "its position (EventCreated, or Event1), the branch-only base after the class (EventCreatedBase) and the "
        "shared part after the holder (EventBase); without it every class is numbered after the holder (Event1 to "
        "Event5), as in 0.89.0",
    ),
    flag((Section.MODEL, _C.TYPING, _G.TYPE_MAPPING), "--use-pendulum", help="use pendulum instead of datetime"),
    toggle(
        (Section.MODEL, _C.TYPING, _G.TYPE_MAPPING),
        "--use-standard-primitive-types",
        help="Use Python standard library types for string formats (UUID, IPv4Address, etc.) "
        "instead of str. Affects dataclass, msgspec, TypedDict output. "
        "Pydantic already uses these types by default.",
    ),
    toggle(
        (Section.MODEL, _C.TYPING, _G.TYPE_MAPPING),
        "--use-str-for-formats-msgspec-cannot-decode",
        help="Write str for the formats whose standard library class msgspec has no decoder for (ipv4, ipv6, "
        "ipv4-network, ipv6-network, path) under --use-standard-primitive-types, so msgspec.Struct output decodes a "
        "document that holds them; without it the Struct holds IPv4Address, IPv6Address, IPv4Network, IPv6Network "
        "and Path, as in 0.100.0, which msgspec decodes only with a dec_hook. Other output model types keep the "
        "classes.",
        preset=PresetJoin(
            "20261123",
            {"use_str_for_formats_msgspec_cannot_decode": True},
            title="str for the formats msgspec cannot decode",
            description=(
                "Under --use-standard-primitive-types, write str for ipv4, ipv6, ipv4-network, ipv6-network and path "
                "in msgspec.Struct output, whose msgspec has no decoder for IPv4Address, IPv6Address, IPv4Network, "
                "IPv6Network and Path, so a document that holds them decodes."
            ),
        ),
    ),
    flag(
        (Section.MODEL, _C.TEMPLATE, _G.IMPORTS),
        "--use-exact-imports",
        help='import exact types instead of modules, for example: "from .foo import Bar" instead of '
        '"from . import foo" with "foo.Bar"',
    ),
    value(
        (Section.MODEL, _C.TYPING, _G.TYPE_MAPPING),
        "--output-datetime-class",
        help="Choose Datetime class between AwareDatetime, NaiveDatetime, PastDatetime, FutureDatetime or datetime. "
        "Each output model has its default mapping (for example pydantic: datetime, dataclass: str, ...)",
        choices=_values(DatetimeClassType),
    ),
    value(
        (Section.MODEL, _C.TYPING, _G.TYPE_MAPPING),
        "--output-date-class",
        help="Choose Date class between PastDate, FutureDate or date. (Pydantic v2 only) "
        "Each output model has its default mapping.",
        choices=_values(DateClassType),
    ),
    deprecated(
        (Section.MODEL, _G.MODEL_NAMING),
        "--parent-scoped-naming",
        implies=_relations("--naming-strategy", value=NamingStrategy.ParentPrefixed.value),
    ),
    value(
        (Section.MODEL, _G.MODEL_NAMING),
        "--naming-strategy",
        help="Strategy for naming models and telling duplicate model names apart. 'numbered' (default): Append numeric "
        "suffix (Address, Address1, Address2). Simple but names don't indicate context. 'parent-prefixed': Prefix "
        "every nested model with the name of the model that holds it, joined in CamelCase, nested parents included "
        "(CompanyAddress, CompanyEmployeeHomeAddress), also where the name is unique; the model names are the same as "
        "with 'full-path'. 'full-path': Prefix every nested model with the names of the models that hold it, joined in "
        "CamelCase (CompanyAddress, CompanyEmployeeHomeAddress). 'primary-first': Keep clean names for primary "
        "definitions (in /definitions/ or /components/schemas/), only add suffix to inline/nested duplicates. "
        "'definitions-first': Give every definition (in /definitions/, /$defs/ or /components/schemas/ of any "
        "document) its own name before an inline model, a variant or another definition that asked for it first, which "
        "takes the name the definition would have taken (Package1); unlike 'primary-first', which renames only models "
        "of other files, as in 0.89.0. 'enclosing-model': Prefix every nested model with the final name of the model "
        "it is built for, once (IssueClosedIssue for the allOf property issue of IssueClosed, StreamEvent1 for the "
        "first unnamed branch of the union StreamEvent, PetCat for its branch tagged cat); a definition keeps its own "
        "name (Customer, not ModelCustomer); unlike 'full-path', which prefixes with the closest model registered "
        "above, as in 0.89.0.",
        choices=_values(NamingStrategy),
    ),
    value(
        (Section.MODEL, _G.MODEL_NAMING),
        "--duplicate-name-suffix",
        help="JSON mapping of type to suffix for resolving duplicate name conflicts. "
        'Example: \'{"model": "Schema"}\' changes Address1 to AddressSchema. '
        "Keys: 'model' (for classes), 'enum' (for enums), 'default' (fallback). "
        "When not specified, uses numeric suffix (Address1, Address2).",
        type=str,
    ),
    toggle(
        (Section.MODEL, _G.MODEL_NAMING),
        "--use-duplicate-name-suffix-for-every-collision",
        help="Give the --duplicate-name-suffix suffix also to a model renamed because its name is taken in its module, "
        "by another model or by a name the module imports (an inline Item beside a definition Item, a definition "
        "AnyUrl beside a uri field); without it such a model takes the suffix Model, as in 0.88.0",
    ),
    toggle(
        (Section.MODEL, _G.MODEL_NAMING),
        "--name-converted-payloads-by-message",
        help="AsyncAPI only: name a payload that an Avro or XML Schema converter gives no name of its own, such as an "
        "Avro string or array, after its message, as a JSON Schema payload is, and drop the placeholder title Model "
        "the converter gives it (Field(..., title='Model'), and the class Model under --use-title-as-name); without it "
        "the placeholder stays, as in 0.88.0",
    ),
    toggle(
        (Section.MODEL, _G.MODEL_SHAPE),
        "--merge-message-trait-headers",
        help="AsyncAPI only: give a message the headers of all its traits, merged in order as AsyncAPI 2 and 3 apply "
        "traits (a JSON Merge Patch each, a trait that is a $ref read as its target), and then its own headers, which "
        "win; without it the message's own headers, else those of its one inline trait that defines them, are its "
        "headers, and a second inline trait with headers is an error, as in 0.90.0",
    ),
    value(
        (Section.MODEL, _C.GENERAL),
        "--all-exports-scope",
        help="Generate __all__ in __init__.py with re-exports. "
        "'children': export from direct child modules only. "
        "'recursive': export from all descendant modules.",
        choices=_values(AllExportsScope),
    ),
    value(
        (Section.MODEL, _C.GENERAL),
        "--all-exports-collision-strategy",
        help="Strategy for name collisions when using --all-exports-scope=recursive. "
        "'error': raise an error (default). "
        "'minimal-prefix': add module prefix only to colliding names. "
        "'full-prefix': add full module path prefix to colliding names.",
        choices=_values(AllExportsCollisionStrategy),
    ),
    value(
        (Section.MODEL, _C.GENERAL),
        "--module-split-mode",
        help="Split generated models into separate files. 'single': generate one file per model class.",
        choices=_values(ModuleSplitMode),
    ),
    # Typing options for generated models
    value(
        (Section.TYPING, _C.MODEL, _G.MODEL_SHAPE),
        "--base-class",
        help="Base Class (default: pydantic.BaseModel)",
        type=str,
    ),
    value(
        (Section.TYPING, _C.MODEL, _G.MODEL_SHAPE),
        "--base-class-map",
        help="Model-specific base class mapping (JSON or JSON file path). "
        'Example: \'{"MyModel": "custom.BaseA", "OtherModel": "custom.BaseB"}\'. '
        "Priority: base-class-map > customBasePath (in schema) > base-class.",
        type=_json_value_or_file,
    ),
    value(
        Section.TYPING,
        "--enum-field-as-literal",
        help="Parse enum field as literal. "
        "all: all enum field type are Literal. "
        "one: field type is Literal when an enum has only one possible value. "
        "none: always use Enum class (never convert to Literal)",
        choices=_values(LiteralType),
    ),
    value(
        Section.TYPING,
        "--enum-field-as-literal-map",
        help="Per-field override for enum/literal generation (JSON or JSON file path). "
        "Format: JSON object mapping field names to 'literal' or 'enum'. "
        'Example: \'{"status": "literal", "priority": "enum"}\'. '
        "Overrides --enum-field-as-literal for matched fields.",
        type=_json_value_or_file,
    ),
    value(
        Section.TYPING,
        "--open-enums",
        help="Accept values an enum does not list. "
        "extensible: the enums whose schema marks them open (x-extensible-enum: true, or x-ms-enum with "
        "modelAsString: true); "
        "all: every enum of two or more values, except one whose schema closes it (x-extensible-enum: false, "
        "modelAsString: false); "
        "none: no enum, as without the option. "
        "An open enum is written as its Enum class on the base --open-enum-base-class-map names for that class, else "
        "as Literal[...] joined with its type (Literal['red', 'green'] | str), whatever the options say about Literals",
        choices=_values(OpenEnums),
    ),
    flag(
        Section.TYPING,
        "--ignore-enum-constraints",
        help="Ignore enum constraints and use the base type (e.g., str, int) instead of generating Enum classes",
    ),
    toggle(
        Section.TYPING,
        "--use-single-value-enum-as-const",
        help="Treat `enum` with a single value as `const` with that value, which JSON Schema defines as equal: "
        'oneOf/anyOf branches such as {"type": "string", "enum": ["low"]} merge into one Enum when there are two or '
        "more and all hold values of one JSON type, otherwise each branch is typed as written, and a schema with one "
        "enum value is typed Literal[...] instead of a one-member Enum, except one with `nullable: true` and a value "
        "other than null, which is read as written",
    ),
    toggle(
        Section.TYPING,
        "--read-const-beside-enum",
        help="Read a `const` beside an `enum` as JSON Schema does: a value whose schema names the enum is typed by the "
        "const that narrows it (`Literal[Enum.member]`, a Literal or None) instead of the enum class that lists the "
        "values the const excludes, and under --use-single-value-enum-as-const an `enum` that admits its `const` is "
        "read as that `const` instead of an Enum, except one with `nullable: true` and a value other than null",
    ),
    toggle(
        Section.TYPING,
        "--filter-literal-values",
        help="Keep only the values of a `const` or an `enum` that meet the `minLength`, `maxLength`, `pattern`, bound "
        "and `multipleOf` constraints beside them in its `Literal` or Enum, as JSON Schema validates them; a schema "
        "none of whose values meets them stays as written with a warning",
    ),
    toggle(
        Section.TYPING,
        "--intersect-allof-keywords",
        help="Combine the keywords of every part of an `allOf`, of a `$ref` with sibling keywords and of an inherited "
        "property override, as JSON Schema validates them: the tightest bounds, the `enum` values every part admits, "
        "the `type` every part admits, the conjunction of the `pattern`s, no null beside a `type` that excludes it, "
        "and the constraints of the root, item and map value positions of an `allOf`. Without it, the keyword of the "
        "last part wins where the parts disagree",
    ),
    toggle(
        Section.TYPING,
        "--intersect-combined-schema-keywords",
        help="Combine the keywords of every part of a combined schema as JSON Schema validates them, as "
        "--intersect-allof-keywords does (this option turns it on), and also: the keywords beside an `anyOf` or "
        "`oneOf` in each branch, the `properties` an inherited property override merges name by name, an override that "
        "narrows the inherited `type` (such as to `null`), the length of an inherited closed tuple under "
        "--allof-merge-mode none, the `$ref` siblings of an `allOf` member where the dialect reads them, an `allOf` "
        "member that names a `$ref` alias, the null branch of an `allOf` member's `anyOf` or `oneOf`, the `required` "
        "names an `allOf` adds to the one model it names at a field, an item or a branch, and no `enum` value that the "
        "merged `type` rejects. Without it these keep the output of 0.88.0",
    ),
    toggle(
        Section.TYPING,
        "--distribute-allof-over-unions",
        help="Read an `allOf` with the `anyOf` and `oneOf` it holds or sits beside as JSON Schema validates them: an "
        "`allOf` of several is one union variant per combination of one branch of each, also through a `$ref` member "
        "that names an `allOf` over a union, a `$ref` branch takes the `allOf` members beside its `anyOf` or `oneOf`, "
        "which an inline branch already takes, and the value keywords beside it where it names no object, and an "
        "OpenAPI request or response body with an `allOf` beside an `anyOf` or `oneOf` is that union as a schema of "
        "the components is; the value branches of those unions are read at a field and beside object branches too. "
        "Without it the branches of every `anyOf` and `oneOf` of an `allOf` join one union (the inline branches of a "
        "second replacing those of the first), a `$ref` member naming an `allOf` over a union is dropped, a `$ref` "
        "branch takes only the `properties` and `required` beside it, a body drops the union, and value branches are "
        "read only where the `allOf` would be a model without fields, as in 0.89.0",
    ),
    toggle(
        Section.TYPING,
        "--distribute-allof-over-object-branches",
        help="Give an `allOf` over an `anyOf` or `oneOf` one union variant per object branch, also where a branch is "
        "itself an `anyOf` or `oneOf` (named by a `$ref` or written there), an `allOf` or a `$ref` with sibling "
        "keywords over one, or an alias of an object schema, and have each variant require every name a part of the "
        "`allOf` requires, typed as the field it takes from its branch (allOf: [Pet, {required: [name]}] with Pet a "
        "oneOf of Cat and Dog that declare name optional). Without it such a branch gives a variant of the other "
        "members alone, or none, and warns, and a variant takes the name as its bases hold it, optional or Any, as in "
        "0.100.0",
        preset=PresetJoin(
            "20261124",
            {"distribute_allof_over_object_branches": True},
            title="allOf distributed over object branches",
            description=(
                "Give an allOf over an anyOf or oneOf one union variant per object branch, also where a branch is "
                "itself an anyOf or oneOf, named by a $ref or written there, and have each variant declare every name "
                "a part of the allOf requires as a required field, typed as its branch declares it, so a variant needs "
                "such a name, and output types that validate values reject one its branch rejects."
            ),
        ),
    ),
    toggle(
        Section.TYPING,
        "--intersect-merged-allof-members",
        help="Read the `allOf` members that are merged into one schema keyword by keyword, as JSON Schema validates "
        "them: a property that several merged object members declare takes the `type`, null and `multipleOf` every "
        "declaration admits (a `true` declaration adds nothing) and the members' own `allOf` members are merged too; a "
        "named `allOf` of a `$ref` and value members keeps the `enum` and `type` every member admits; a `$ref` to a "
        "map beside members that add only property counts or `propertyNames` stays that map with them; and a length or "
        "`pattern` beside a `$ref` to base64 text is checked on the text where the output checks base64 constraints "
        "on the text. Without it the later member's keyword wins, another member's `enum` is dropped, the map becomes "
        "a model without fields and the keyword is checked on the decoded value, as in 0.100.0",
        preset=PresetJoin(
            "20261123",
            {"intersect_merged_allof_members": True},
            title="Merged allOf members intersected",
            description=(
                "Read the allOf members the generator merges into one schema keyword by keyword, as JSON Schema "
                "validates them: a property several merged object members declare takes the type, null and multipleOf "
                "they all admit and the members' own allOf members, a named allOf of a $ref and value members keeps "
                "the enum of every member, a map $ref beside property counts stays a map with the counts, and in "
                "Pydantic v2 output a length or pattern beside a $ref to base64 text is checked on the text."
            ),
        ),
    ),
    toggle(
        Section.TYPING,
        "--read-allof-parents-as-their-models",
        help="Read each allOf parent as its model holds it: a parent written as a $ref beside sibling keywords with "
        "the schema its $ref names, so a model written before that parent types and requires an inherited property as "
        "it does when written after it; every property some parent requires stays required where a parent the model "
        "also inherits it from declares it optional (allOf: [PetBase, Named] with only Named requiring name); and an "
        "allOf of a $ref alias and a member that adds only a type or required names reads the alias as "
        "--intersect-combined-schema-keywords reads alias members; without it such properties can be Any or optional "
        "and such a model a dict, as in 0.100.0",
        preset=PresetJoin(
            "20261123",
            {"read_allof_parents_as_their_models": True},
            title="allOf parents read as their models",
            description=(
                "Read each allOf parent as its model holds it: a parent written as a $ref beside sibling keywords with "
                "the schema its $ref names, so a model written before that parent types and requires an inherited "
                "property as it does after it; keep every property some parent requires required where another parent "
                "declares it optional; and read an alias member beside a member that adds only a type or required "
                "names as alias members are read elsewhere, so such a model keeps the fields of the schema the alias "
                "names."
            ),
        ),
    ),
    toggle(
        Section.TYPING,
        "--read-allof-parents-in-mro-order",
        help="Read the allOf parents of every inherited schema in the order of the class bases (the MRO), so a "
        "property that several bases declare takes the nearer base's constraints and description, as the class does, "
        "also where an allOf lists a base before its own descendant (allOf: [A, B] with B = allOf: [A, ...]); without "
        "it the parents of an inner allOf are read in document order and the property can take the farther base's "
        "looser constraints, as in 0.90.0",
    ),
    toggle(
        Section.TYPING,
        "--read-inherited-property-schemas-in-mro-order",
        help="With --intersect-combined-schema-keywords, give a property that a model only makes required the schema "
        "its allOf parents declare, read in the order of the class bases from the allOf members the model's own fields "
        "are read from (an alias member followed to the schema it names, the keywords beside a $ref member read with "
        "it), so a model written before its parents takes the field it takes when written after them; without it the "
        "declaration is looked up in the allOf members as written, and under --intersect-combined-schema-keywords such "
        "a field can lose its description or its constraints or become Any, as in 0.96.0",
    ),
    flag((Section.TYPING, _C.FIELD), "--field-constraints", help="Use field constraints and not con* annotations"),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--omit-redundant-constraints",
        help="Do not write a constraint again beside a type that already enforces it: the bound of a zero-bound class "
        "such as NonNegativeInt, and the length and number constraints a const or enum Literal meets by its values",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--read-length-keywords-by-type",
        help="Read minLength, maxLength and pattern for strings only and minItems, maxItems and uniqueItems for arrays "
        "only, as JSON Schema does, so a maxLength beside an array leaves the list unbounded and a maxItems beside a "
        "string leaves the string unbounded; without it a declared array writes minLength/maxLength and a declared "
        "string minItems/maxItems as their length bounds, as in 0.88.0",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--keep-string-keywords-beside-formats",
        help="Keep the minLength, maxLength and pattern beside a string format that Pydantic output writes as a class "
        "no constr call constrains: a `format: email` takes them in `Annotated[EmailStr, Field(...)]`, "
        "a `format: byte` writes them in front of the base64 decoding, so Pydantic 2.10 or later checks them on the "
        "text, also in the field forms (--field-constraints, --use-annotated), and a `format: uri` is the plain "
        "string they check, as AnyUrl takes no pattern; an output that writes a date or UUID format as `str`, as "
        "msgspec does, also keeps the keywords an `allOf` member adds beside a `$ref` to it. Without it these "
        "keywords are left out, silently or with a warning, as in 0.100.0",
        preset=PresetJoin(
            "20261123",
            {"keep_string_keywords_beside_formats": True},
            title="String keywords kept beside formats",
            description=(
                "Keep the minLength, maxLength and pattern beside a string format that Pydantic output writes as a "
                "class no constr call constrains: an email takes them in Annotated[EmailStr, Field(...)], a byte "
                "writes them in front of the base64 decoding and a uri is the plain string they check; an output that "
                "writes a date or UUID format as str, as msgspec does, keeps the keywords an allOf member adds beside "
                "a $ref to it."
            ),
        ),
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--read-object-keywords-by-type",
        help="Read the keywords that constrain objects only (properties, patternProperties, additionalProperties, "
        "propertyNames, unevaluatedProperties, required, minProperties, maxProperties) beside an `anyOf` or `oneOf` as "
        "JSON Schema does: a branch that admits no object, such as a string `type`, a `const`, an `enum` or an `allOf` "
        "of such parts, does not take them, so it stays that type instead of becoming a map or a model, and under "
        "--generate-schema-validators a site with such a branch is a union rather than one object model; without it "
        "every branch takes them, as in 0.93.0",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--read-msgspec-union-bounds-exactly",
        help="msgspec.Struct output only: decide whether the int member of a union with a number member drops its Meta "
        "bounds on the bounds the schema writes, exactly; without it a number member's bound past 2**53 is read as the "
        "float it is written as, so the int member can keep a bound that rejects an integer the number member accepts, "
        "as in 0.88.0",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--use-site-models-for-constrained-refs",
        help="Give an allOf of $ref members whose other parts add constraints, an enum or a type its own model of the "
        "values all parts admit, instead of the referenced type, which leaves the added assertions out",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--use-bounds-for-float-const",
        help="Type a finite float const, which no Literal holds, as a float whose minimum and maximum both equal it, "
        "written as the output writes number bounds (confloat, Field() or Annotated for Pydantic, Meta for msgspec), "
        "so other numbers are rejected; output that checks no bounds, such as dataclasses and TypedDict, keeps float "
        "with a warning; without it the const is typed float, which accepts any number, as in 0.93.0",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--use-one-model-per-ref-site",
        help="Give an object $ref with sibling keywords one model per place it is written, which every unchanged copy "
        "of that place shares, and parse such chains without nesting; without it each path that reaches the place gets "
        "a model of its own (N(N-1)/2 site models for a chain of N definitions), as in 0.89.0.",
    ),
    deprecated((Section.TYPING, _C.FIELD), "--set-default-enum-member"),
    value(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--strict-types",
        help="Use strict types",
        choices=_values(StrictTypes),
        nargs="+",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_SYNTAX),
        "--use-annotated",
        help="Use typing.Annotated for Field(). Also, `--field-constraints` option will be enabled. "
        "Will become default for Pydantic v2 in a future version.",
    ),
    flag(
        (Section.TYPING, _C.MODEL),
        "--use-serialize-as-any",
        help="Use pydantic.SerializeAsAny for fields with types that have subtypes (Pydantic v2 only)",
    ),
    toggle(
        (Section.TYPING, _C.MODEL),
        "--use-serialize-as-any-for-every-subclass",
        help="Under --use-serialize-as-any, wrap a field in SerializeAsAny when any subclass of its model in the "
        "generated output adds fields: at any depth, after --reuse-model or --collapse-reuse-models merges the model, "
        "and where the field names the model through its module; without it only a field whose model has a direct "
        "subclass is wrapped, as in 0.88.0 (Pydantic v2 only)",
    ),
    flag(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-generic-container-types",
        help="Use generic container types for type hinting (typing.Sequence, typing.Mapping). "
        "If `--use-standard-collections` option is set, then import from collections.abc instead of typing",
    ),
    flag(
        Section.TYPING,
        "--use-non-positive-negative-number-constrained-types",
        help="Use the Non{Positive,Negative}{FloatInt} types instead of the corresponding con* constrained types.",
    ),
    flag(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-decimal-for-multiple-of",
        help="Use condecimal instead of confloat for float/number fields with multipleOf constraint "
        "(Pydantic only). Avoids floating-point precision issues in validation.",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-decimal-in-field-forms",
        help="Under --use-decimal-for-multiple-of, also write Decimal for a number with multipleOf in the field forms "
        "(--field-constraints, --use-annotated), with the constraints in Field(), and make the Decimal strict under "
        "--strict-types float (Pydantic only)",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-encoded-str-for-constrained-base64",
        help="Write a format: byte string with pattern, minLength or maxLength in the field forms "
        "(--field-constraints, --use-annotated) as Annotated[str, Field(...), EncodedStr(encoder=Base64Encoder)], so "
        "Pydantic 2.10 or later checks the constraints on the base64 text, as the schema does; without it "
        "Annotated[Base64Str, Field(...)] and an optional Base64Str with Field(...) check them on the decoded text, as "
        "in 0.88.0 (Pydantic v2 only)",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-strict-str-for-base64",
        help="Under --strict-types str, write a format: byte string as the StrictStr that EncodedStr decodes "
        "(Annotated[StrictStr, EncodedStr(encoder=Base64Encoder)]), so Pydantic 2.10 or later refuses bytes for it as "
        "for any other strict string; without it the string is Base64Str, or the str of "
        "--use-encoded-str-for-constrained-base64, which accept bytes, as in 0.90.0 (Pydantic v2 only)",
    ),
    flag(
        (Section.TYPING, _C.MODEL),
        "--use-one-literal-as-default",
        help="Use one literal as default value for one literal field",
    ),
    flag(
        (Section.TYPING, _C.FIELD),
        "--use-enum-values-in-discriminator",
        help="Use enum member literals in discriminator fields instead of string literals",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--merge-shared-variant-tags",
        help="Give a variant that several discriminated unions tag differently (for example one by a mapping key, one "
        "by the schema name) every tag they give it, so each union decodes the documents its discriminator selects; "
        "such a union also accepts the tags the other unions give the variant. A variant keeps the tag of the first "
        "union, as without the option (as in 0.88.0), where a union listing it would then give two of its variants one "
        "tag, and msgspec Structs, which take one tag, keep it too.",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--use-unmapped-variant-tag-values",
        help="Give a variant of a discriminated union that a mapping names, and whose tag property admits several "
        "values (an enum or a Literal), also the values no mapping key and no other variant claim, beside its mapping "
        "keys, so the union decodes the documents of the variant that carry them (a value another variant also admits "
        "stays refused); without it the variant takes only its mapping keys and the union refuses the other values (as "
        "in 0.97.0). A msgspec Struct keeps its one tag.",
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--read-mapping-keys-by-schema-tag-type",
        help="Read the keys of a discriminator mapping as values of the type the schema declares for the tag property "
        "(an integer enum's key '2' is the integer 2), also where an earlier discriminated union already wrote the tag "
        "field of the variant or of the base it inherits it from, so a key reads alike whichever union comes first; "
        "without it such a key is read against the Literal the earlier union wrote, or stays text (as in 0.100.0).",
        preset=PresetJoin(
            "20261123",
            {"read_mapping_keys_by_schema_tag_type": True},
            title="Mapping keys by the schema's tag type",
            description=(
                "Read the keys of a discriminator mapping as values of the type the schema declares for the tag "
                "property, such as the integer 2 for the key '2' of an integer enum, also where an earlier "
                "discriminated union wrote the tag field of the variant or of its base, so the key reads alike "
                "whichever union comes first."
            ),
        ),
    ),
    toggle(
        (Section.TYPING, _C.FIELD),
        "--use-root-types-for-mixed-union-map-keys",
        help="Type the RootModel members of a map key that is a union with a non-RootModel member (`propertyNames` "
        "`anyOf` of an enum, or of a `str` with a `maxLength`, and a RootModel) by their root types, as the key of a "
        "RootModel is typed (`dict[str | UUID, int]`); without it such a key keeps the RootModel class, which "
        "Pydantic cannot hash, so a key only the RootModel matches raises `TypeError`, and the other member reads "
        "the keys it matches, as in 0.100.0. A key whose members are all RootModels is typed by the root types "
        "with or without the option",
        preset=PresetJoin(
            "20261123",
            {"use_root_types_for_mixed_union_map_keys": True},
            title="Root types for mixed union map keys",
            description=(
                "Type the RootModel members of a map key that is a union with a non-RootModel member, such as a "
                "propertyNames anyOf of an enum, or of a string with a maxLength, and a RootModel, by their root "
                "types, so a key that only the RootModel matches is accepted, and a key that both match parses as its "
                "root type, here a UUID for JSON input, as it does where the key names the RootModel alone. A Python "
                "dict input keeps such a key as a str."
            ),
        ),
    ),
    toggle(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-standard-collections",
        help="Use standard collections for type hinting (list, dict). Default: enabled",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-object-type",
        help="Use object instead of Any for unspecified JSON Schema object and array values",
    ),
    value(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--untyped-value-type",
        help="Write each value the schema leaves untyped ({}, true, a schema whose keywords give it no type, the "
        "values of an object without properties and the items of an array without items) as this type instead of Any: "
        "pydantic.JsonValue (Pydantic 2.5 or later), object, Any or the dotted import path of a type of your own, "
        "which the models import; the structure is as without the option. --type-overrides, x-python-type and "
        "x-python-import keep their types, and msgspec.Struct output keeps Any, which msgspec decodes into JSON "
        "values. Without it untyped values are Any, and object in containers under --use-object-type, as in 0.96.0",
        metavar="TYPE",
        type=str,
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-typed-nullable-map-values",
        help="Type the values of nullable maps, schemas whose type array lists object alone or with null, such as "
        "[object, null] or [object], with additionalProperties, as their schema says, with a model for object values. "
        "Without it the 0.83.0 form is kept, dict[str, Any] | None, or dict[str, Any] without null.",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-typed-map-values-beside-other-types",
        help="With --use-typed-nullable-map-values, also type the map values of type arrays that list object beside "
        "other types, such as [object, string], as dict[str, int] | str instead of dict[str, Any] | str",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--read-unevaluated-properties",
        help="Read a schema-valued unevaluatedProperties as additionalProperties where no applicator evaluates the "
        "object in place, so it types the extra keys of a model and makes an object without properties a map",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--read-kubernetes-extensions",
        help="Read the Kubernetes schema extensions as the keywords they stand for: x-kubernetes-int-or-string (and "
        "the int-or-string format) as an integer or a string, x-kubernetes-preserve-unknown-fields beside properties "
        "as additionalProperties true, and an x-kubernetes-group-version-kind as the const and default of the "
        "apiVersion and kind it names; without it they are ignored, as in 0.96.0",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-single-dict-for-object-maps",
        help="Type an object map, an object without properties whose keys patternProperties or propertyNames "
        "constrain, as one dict whose keys are those of every key class and whose value is the union of their values, "
        "beside the null and the other types of its type array",
    ),
    toggle(
        (Section.TYPING, _C.MODEL),
        "--use-subclass-enum",
        help="Define generic Enum class as subclass with field type when enum has type (int, float, bytes, str)",
    ),
    value(
        (Section.TYPING, _C.MODEL),
        "--open-enum-base-class-map",
        help="Base classes that open the Enum classes of open enums (--open-enums), by the class the enum is written "
        'on (JSON or JSON file path). Keys: Enum, StrEnum, IntEnum. Example: \'{"StrEnum": "mypkg.OpenStrEnum", '
        '"IntEnum": "mypkg.OpenIntEnum"}\'. Each is your member-less subclass of that class whose _missing_ keeps a '
        "value it does not list; a value type before Enum (--use-subclass-enum) stays: (str, mypkg.OpenEnum). An open "
        "enum whose class has no entry, or that is written as a Literal, is written as Literal[...] joined with its "
        "type",
        type=_json_value_or_file,
    ),
    toggle(
        Section.TYPING,
        "--use-specialized-enum",
        help="Use specialized Enum class (StrEnum, IntEnum). Requires --target-python-version 3.11+",
    ),
    toggle(
        Section.TYPING,
        "--keep-enum-member-values",
        help="Write an enum as a plain Enum where the value base of --use-specialized-enum or --use-subclass-enum "
        "would change a member value, such as 2.5 in an IntEnum; without it the base follows the schema type, as in "
        "0.86.0",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_SYNTAX),
        "--use-union-operator",
        help="Use | operator for Union type (PEP 604). Default: enabled",
    ),
    toggle(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-unique-items-as-set",
        help="define field type as `set` when the field attribute has `uniqueItems`",
    ),
    toggle(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-unique-items-as-set-by-value",
        help="Under --use-unique-items-as-set, decide each set once the item types are final (after "
        "--collapse-root-models, --type-overrides and every module) and write a set only where every value of its "
        "items hashes by value (scalars, enums, tuples and frozen sets of them, frozen models); an array of models "
        "that hash only by identity, of Any or of containers stays a list. Deduplication and --reuse-model also keep "
        "apart models whose arrays differ in uniqueItems; without it the sets are decided before root models collapse, "
        "set item models hash by identity and such models merge, as in 0.89.0",
    ),
    toggle(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--read-set-item-fields-in-class-order",
        help="Under --use-unique-items-as-set-by-value, also read the fields a set item model takes in the order of "
        "its class bases (C3) when deciding whether its instances hash: where a base two levels up and a later direct "
        "base both declare a field and the class takes the one that does not hash, the uniqueItems array stays a "
        "list; without it only the field the nearest base declares is read and such an array is a set whose items "
        "fail to hash, as in 0.100.0",
        preset=PresetJoin(
            "20261123",
            {"read_set_item_fields_in_class_order": True},
            title="Set item fields read in class order",
            description=(
                "Under --use-unique-items-as-set-by-value, also read the fields a set item model takes in the order of "
                "its class bases, so a uniqueItems array of models whose class takes an inherited field that does not "
                "hash stays a list instead of a set those items cannot be added to."
            ),
        ),
    ),
    toggle(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-tuple-for-fixed-items",
        help="Generate tuple types for arrays with items array syntax when minItems equals maxItems equals items "
        "length",
    ),
    toggle(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-tuple-for-closed-arrays",
        help="Generate tuple types for positional arrays that no other item can follow: a closed prefixItems tail "
        "(items, unevaluatedItems or additionalItems false) whose length the length keywords fix, so that each "
        "position keeps its own type",
    ),
    flag(
        (Section.TYPING, _G.COLLECTION_TYPES),
        "--use-tuple-for-fixed-length-arrays",
        help="Generate tuple types for fixed-length arrays with a single items schema",
    ),
    flag(
        Section.TYPING,
        "--use-total-false-for-typed-dict",
        help="Generate TypedDict with total=False and mark required fields with Required",
    ),
    toggle(
        Section.TYPING,
        "--use-closed-typed-dict",
        help="Generate TypedDict with PEP 728 closed=True/extra_items for additionalProperties constraints. "
        "Use --no-use-closed-typed-dict for type checkers that don't yet support PEP 728 (e.g., mypy).",
    ),
    value(
        Section.TYPING,
        "--allof-merge-mode",
        help="Mode for field merging in allOf schemas. "
        "'constraints': merge only constraints (minItems, maxItems, pattern, etc.) from parent (default). "
        "'all': merge constraints plus annotations (default, examples) from parent. "
        "'none': do not merge any fields from parent properties.",
        choices=_values(AllOfMergeMode),
    ),
    value(
        Section.TYPING,
        "--allof-class-hierarchy",
        help="How to map allOf references to class hierarchies. "
        "'if-no-conflict': only create subclasses when parent class has no conflicting property definition. "
        "'always': always create subclasses. ",
        choices=_values(AllOfClassHierarchy),
    ),
    flag(
        (Section.TYPING, _G.TYPE_ALIAS), "--use-type-alias", help="Use TypeAlias instead of root models (experimental)"
    ),
    flag(
        (Section.TYPING, _G.TYPE_ALIAS),
        "--use-type-alias-type",
        help="Use TypeAliasType for type aliases on Python 3.10 and 3.11 (implies --use-type-alias; experimental)",
    ),
    flag(
        (Section.TYPING, _G.TYPE_ALIAS),
        "--use-root-model-type-alias",
        help="Use type alias format for RootModel (e.g., Foo = RootModel[Bar]) "
        "instead of class inheritance (Pydantic v2 only)",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_ALIAS),
        "--use-validate-default-for-root-model-type-aliases",
        help="Under --use-root-model-type-alias, write the default of a field whose type names a root model written as "
        "an alias (`Tags = RootModel[list[str]]`) with validate_default=True, as for a root model class, so Pydantic "
        "validates it into the root model; without it the default stays the raw value, such as a list instead of a "
        "`Tags`, as in 0.97.0 (Pydantic v2 only)",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_ALIAS),
        "--use-plain-alias-for-recursive-models",
        help="Pydantic output only: write a type alias whose whole value is a model that names itself as `Alias: "
        "TypeAlias = Node`, which Pydantic builds for every model that uses it; without it the alias is "
        '`TypeAliasType("Alias", Node)` (or `type Alias = Node`) as in 0.89.0, which Pydantic 2 refuses once two '
        "models use it. The alias then has no entry of its own in `model_json_schema()` `$defs`",
    ),
    flag((Section.TYPING, _G.IMPORTS), "--disable-future-imports", help="Disable __future__ imports"),
    value(
        (Section.TYPING, _G.IMPORTS),
        "--import-overrides",
        help="Override modules for generated imports by symbol name. "
        "Format: JSON object mapping symbols to module paths. "
        'Example: \'{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}\'.',
        type=str,
    ),
    value(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--type-mappings",
        help="Override default type mappings. "
        'Format: "type+format=target" (e.g., "string+binary=string" to map binary format to string type) '
        'or "format=target" (e.g., "binary=string"). '
        "Can be specified multiple times.",
        nargs="+",
        type=str,
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--read-format-as-annotation",
        help="Read the format of a schema as an annotation, as JSON Schema does: a string, integer or number schema "
        "takes the type its type keyword gives (str, int, float), with its own length, pattern and bound keywords, so "
        "every value the schema accepts validates, a string or integer dumps as written (a number as the float it "
        "parses to, as without a format), and an unknown format gives no warning; a format that a --type-mappings "
        "entry names for the schema's type keeps that mapping, and format: binary stays bytes. Without it each known "
        "format selects a type (EmailStr, AwareDatetime, UUID, Decimal, SecretStr, Base64Str, ...), as in 0.96.0",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--read-uuid6-uuid7-uuid8-formats",
        help="Read the string formats uuid6, uuid7 and uuid8 (RFC 9562) as the UUID versions they name, as format "
        "uuid4 is read: Pydantic output types them as UUID6, UUID7 and UUID8 (uuid.UUID for a "
        "--target-pydantic-version before 2.11, which lacks them), and the other outputs as uuid.UUID under "
        "--use-standard-primitive-types, else "
        "str; without it each is an unknown format, which gives a warning and str, as in 0.99.0",
    ),
    value(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--type-overrides",
        help="Replace schema model types with custom Python types. "
        "Format: JSON object mapping model names to Python import paths. "
        'Model-level: \'{"CustomType": "my_app.types.MyType"}\' replaces all references. '
        'Scoped: \'{"User.field": "my_app.Type"}\' replaces specific field only.',
        type=str,
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--type-overrides-replace-whole-type",
        help="Make every --type-overrides value of a field replace the field's whole type: a class path replaces an "
        "object map itself, as an annotation already does (dict for builtins.dict, not dict[str, dict]), and the item "
        "counts of an array and the property counts of a map it replaces stay only where the new type counts the same "
        "(a sequence its items, a mapping class its keys), with a warning otherwise; without it a class path keeps the "
        "dict of a map around the class and every override keeps those counts whatever its type, as in 0.89.0",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-x-python-type-as-written",
        help="Write a field's x-python-type as given: a union spelled Optional[...] or Union[...], such as "
        "Optional[Literal['a', 'b']], types the field as one spelled X | Y already does, and a container class "
        "compatible with the schema, such as tuple, MutableSequence or MutableMapping, is written around the schema's "
        "item, key and value types; without it such a union is ignored where the schema declares one type, and the "
        "field takes the schema's type, and such a container is written as list, Sequence, set, frozenset or Mapping, "
        "as in 0.90.0",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-x-python-import-for-every-ref",
        help="Type every reference to a schema with x-python-import by the class it imports, as a $ref to it at a "
        "field is: also an allOf of it beside keywords that add no field where a JSON pointer into another document or "
        "a whole-document $ref names it, and under --read-only-write-only-model-type request-response a model does not "
        "split for the readOnly or writeOnly properties of such a schema, nor for those its allOf bases reach through "
        "one; without it such a reference keeps the model of its schema and those models split, as in 0.96.0",
    ),
    toggle(
        (Section.TYPING, _G.TYPE_MAPPING),
        "--use-bytes-for-content-media-type",
        help="Type a string of an OpenAPI document that has a contentMediaType and no contentEncoding as bytes, as a "
        "format: binary string is: OpenAPI 3.1 writes raw binary content so, and the generated clients and servers "
        "already send and receive it as a file; without it the string is str, as in 0.90.0",
    ),
    # Customization options for generated model fields
    flag(Section.FIELD, *option_flags("capitalise_enum_members"), help="Capitalize field names on enum"),
    toggle(
        Section.FIELD,
        *option_flags("capitalise_numbered_enum_members"),
        help="Under --capitalise-enum-members, capitalize also the number a member takes when its capitalized name is "
        "taken (A_1 instead of a_1 for the members 'a' and 'A'); without it the numbered name stays as written, as in "
        "0.88.0",
    ),
    value(Section.FIELD, "--empty-enum-field-name", help="Set field name when enum value is empty (default:  `_`)"),
    value(Section.FIELD, "--field-extra-keys", help="Add extra keys to field parameters", type=str, nargs="+"),
    value(
        Section.FIELD,
        "--field-extra-keys-without-x-prefix",
        help="Add extra keys with `x-` prefix to field parameters. The extra keys are stripped of the `x-` prefix.",
        type=str,
        nargs="+",
    ),
    flag(Section.FIELD, "--field-include-all-keys", help="Add all keys to field parameters"),
    value(
        (Section.FIELD, _C.MODEL),
        "--model-extra-keys",
        help="Add extra keys from schema extensions (x-* fields) to model_config json_schema_extra",
        type=str,
        nargs="+",
    ),
    value(
        (Section.FIELD, _C.MODEL),
        "--model-extra-keys-without-x-prefix",
        help="Add extra keys with `x-` prefix to model_config json_schema_extra. "
        "The extra keys are stripped of the `x-` prefix.",
        type=str,
        nargs="+",
    ),
    flag((Section.FIELD, _C.MODEL), "--force-optional", help="Force optional for required fields"),
    value(
        Section.FIELD,
        "--original-field-name-delimiter",
        help="Set delimiter to convert to snake case. This option only can be used with --snake-case-field (default: "
        "`_` )",
    ),
    flag(
        Section.FIELD,
        "--remove-special-field-name-prefix",
        help="Remove field name prefix if it has a special meaning e.g. underscores",
    ),
    toggle(Section.FIELD, "--snake-case-field", help="Change camel-case field name to snake-case"),
    value(
        Section.FIELD,
        "--special-field-name-prefix",
        help="Set field name prefix when first character can't be used as Python field name (default:  `field`)",
    ),
    toggle((Section.FIELD, _C.MODEL), "--strict-nullable", help="Treat default field as a non-nullable field"),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--omit-redundant-reference-null",
        help="Do not write None again where a field, item or member references a model that holds null itself (a root "
        "model or type alias whose type admits None, or an enum with a None member), so that a null parses to that "
        "model",
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--keep-reference-null-on-merged-models",
        help="Keep the null that the schema of a field, item or member admits where the model it references is merged "
        "into an equal model that rejects null (deduplication of equal models, --reuse-model, --collapse-reuse-models, "
        "--reuse-scope tree), so a `type: [object, null]` property keeps `| None`; without it the reference takes the "
        "null of the model it names after the merge, as in 0.88.0",
    ),
    flag(
        (Section.FIELD, _C.MODEL),
        "--strip-default-none",
        help="Strip default None on fields (no effect on msgspec.Struct: it defaults an omitted field to UNSET, and to "
        "None only when the type admits None)",
    ),
    flag((Section.FIELD, _C.MODEL), "--use-default", help="Use default value even if a field is required"),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--keep-defaults-of-inherited-required-fields",
        help="With --use-default, keep the default a schema gives a property that only a schema it inherits from "
        "(allOf) lists as required, as --use-default keeps that of a property the schema lists itself (`a: str = "
        "'x'`); without it the field is written without its default, as in 0.90.0",
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--omit-incomplete-model-defaults",
        help="Write a property whose object default lacks required fields of its model (`default: {}`) without that "
        "default (`spec: PodSpec | None = None`), so input that leaves the property out is valid; without it the "
        "default is written and validated, which rejects such input, as in 0.90.0",
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--use-default-kwarg",
        help="Use `default=` instead of a positional argument for Fields that have default values.",
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--apply-field-options-to-every-field",
        help="Apply the field options to every kind of field: a GraphQL field, a property whose schema is `true` or "
        "`false`, a required name without a property and the tag field a discriminator appends take the field options "
        "the other fields take, such as --use-default-kwarg, --use-annotated and "
        "--use-default-factory-for-optional-nested-models; without it they leave them out, as in 0.90.0",
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--apply-default-values-to-true-properties",
        help="Give a property whose schema is `true` the default that --default-values gives it, in the class that "
        "declares it and in a child that restates an inherited property as `true` (`name: str | None = 'rex'` for "
        "`Dog.name`); without it such a property takes no default, as in 0.100.0",
        preset=PresetJoin(
            "20261124",
            {"apply_default_values_to_true_properties": True},
            title="Default values of true properties",
            description=(
                "Under --default-values, give a property whose schema is true the default its Class.field or field "
                "key gives it, also where a child restates an inherited property as true, as a property whose schema "
                "is {} takes it."
            ),
        ),
    ),
    flag(
        (Section.FIELD, _C.MODEL),
        "--use-missing-sentinel",
        help="Use pydantic.experimental.missing_sentinel.MISSING for optional fields without defaults (Pydantic "
        "v2.12+), or pydantic.MISSING with --target-pydantic-version 2.14 or later, which --generate-server and "
        "--generate-client require with this option. Supports pydantic_v2.BaseModel and pydantic_v2.dataclass, whose "
        "server and client generation requires it.",
        implies=_relations("--target-pydantic-version", value=TargetPydanticVersion.V2_12.value),
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--read-nullable-beside-type-list",
        help="Write the null that `nullable: true` declares beside a `type` list with an object into the root model of "
        "that schema under --strict-nullable or --use-missing-sentinel, as a declared field does; without it only a "
        "`type` list holding null makes that root admit None",
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--keep-null-of-nullable-items",
        help="Write the null that `nullable: true` declares under --strict-nullable on an array item, a tuple item or "
        "a map value of any form (a nested array, a map, an `allOf` of one `$ref`), as it is written for a scalar or "
        "an object (`list[list[float] | None]`); without it those items reject null, as in 0.100.0. The option adds no "
        "null to an enum or a `const`; an enum that lists null admits it with or without the option",
        preset=PresetJoin(
            "20261123",
            {"keep_null_of_nullable_items": True},
            title="Null kept on nullable items",
            description=(
                "Under --strict-nullable, write the null that nullable: true declares on an array item, a tuple item, "
                "a map value of any form, a nested array, a map or an allOf of one $ref included, as it is written for "
                "a scalar or an object. The option adds no null to an enum or a const; an enum that lists null admits "
                "it with or without the option."
            ),
        ),
    ),
    toggle(
        (Section.FIELD, _C.MODEL),
        "--keep-field-defaults-off-branch-roots",
        help="Leave the default of a property off the root models extracted from the branches of its `anyOf` or "
        "`oneOf`, which then require their value; without it each such root takes the property's default",
    ),
    toggle(Section.FIELD, "--use-field-description", help="Use schema description to populate field docstring"),
    toggle(Section.FIELD, "--use-field-description-example", help="Use schema example to populate field docstring"),
    flag(
        Section.FIELD, "--use-attribute-docstrings", help="Set use_attribute_docstrings=True in Pydantic v2 ConfigDict"
    ),
    flag(
        Section.FIELD,
        "--use-inline-field-description",
        help="Use schema description to populate field docstring as inline docstring",
    ),
    toggle(
        Section.FIELD,
        "--use-single-line-docstring",
        help="Use single-line docstrings when the content fits on one line",
    ),
    value(
        (Section.FIELD, _C.MODEL),
        "--union-mode",
        help="Union mode for only pydantic v2 field",
        choices=_values(UnionMode),
    ),
    flag(
        Section.FIELD,
        "--no-alias",
        help="""Do not add a field alias. E.g., if --snake-case-field is used along with a base class, which has an
            alias_generator""",
    ),
    flag(
        Section.FIELD,
        "--use-serialization-alias",
        help="Use serialization_alias instead of alias for field aliasing (Pydantic v2 only). "
        "This allows setting values using the Pythonic field name while serializing to the original name.",
    ),
    value(
        Section.FIELD,
        "--serialization-aliases",
        help="Serialization alias mapping as inline JSON or a JSON file path for Pydantic v2. "
        "Format: {'<schema_field>': '<serialization_alias>'}. "
        "Supports hierarchical formats: "
        "Flat: {'name': 'fullName'} applies to all occurrences. "
        "Scoped: {'User.name': 'fullName'} applies to specific class.",
        type=str,
    ),
    toggle(
        (Section.FIELD, _C.MODEL, _G.READ_ONLY_WRITE_ONLY, _T.OPENAPI),
        "--use-frozen-field",
        help="Use Field(frozen=True) for readOnly fields (Pydantic v2).",
    ),
    flag(
        (Section.FIELD, _C.MODEL),
        "--use-default-factory-for-optional-nested-models",
        help="Use default_factory for optional nested model fields instead of None default. "
        "E.g., `field: Model | None = Field(default_factory=Model)` instead of `field: Model | None = None`",
    ),
    value(
        Section.FIELD,
        "--field-type-collision-strategy",
        help="Strategy for handling field name and type name collisions (Pydantic v2 only). "
        "'rename-field': rename field with suffix and add alias (default). "
        "'rename-type': rename type class with suffix to preserve field name.",
        choices=_values(FieldTypeCollisionStrategy),
    ),
    toggle(
        Section.FIELD,
        "--rename-redeclared-fields-hiding-types",
        help="Give a field that a subclass redeclares the name its base's field was given where that field is "
        "renamed because it is named like its type (`Pet_1` with `alias='Pet'`), so the subclass holds one field for "
        "the wire key; without it the redeclared field keeps its name beside the inherited renamed one, so two "
        "fields read the key, as in 0.100.0",
        preset=PresetJoin(
            "20261123",
            {"rename_redeclared_fields_hiding_types": True},
            title="Redeclared fields renamed with their base",
            description=(
                "Give a field that a subclass redeclares the name its base's field was given where that field is "
                "renamed because it is named like its type, so the subclass holds one field for the key instead of its "
                "own beside the inherited renamed one."
            ),
        ),
    ),
    toggle(
        Section.FIELD,
        "--rename-fields-hiding-types-from-type-checkers",
        help="Tell a field that the class body assigns (a default or `field(...)`) and that is named like the type "
        "it is annotated with from that type for type checkers, which read the annotation as the field: msgspec.Struct "
        "renames the field and keeps the wire name in `name=`, dataclasses.dataclass, which has no alias, renames "
        "the type, as --field-type-collision-strategy rename-type does for Pydantic; it also gives a field "
        "that a subclass redeclares the name of the renamed field, as --rename-redeclared-fields-hiding-types "
        "does. Without it these outputs keep the field and the type names, as in 0.100.0, and type checkers "
        "report the annotation",
        preset=PresetJoin(
            "20261123",
            {"rename_fields_hiding_types_from_type_checkers": True},
            title="Fields named like their types told apart for type checkers",
            description=(
                "Rename a msgspec field, or a dataclass type, that a field the class body assigns hides from type "
                "checkers because it is named like it, the way Pydantic output renames the field: the msgspec field "
                "keeps the wire name in name=, and a dataclass, which has no alias, renames the type."
            ),
        ),
    ),
    # Options for templating output
    value(
        (Section.TEMPLATE, _C.FIELD),
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
    ),
    value(
        (Section.TEMPLATE, _C.FIELD),
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
    ),
    value(
        (Section.TEMPLATE, _C.FIELD),
        "--deserialize-default-values",
        help="Deserialize serialized schema defaults for selected generated types. Supported values: decimal, enum.",
        choices=_values(DefaultValueType),
        nargs="+",
    ),
    clear(
        (Section.TEMPLATE, _C.FIELD),
        "--no-deserialize-default-values",
        help="Disable schema default value deserialization.",
    ),
    value((Section.TEMPLATE, _G.GENERATED_OUTPUT), "--custom-file-header", help="Custom file header", type=str),
    value(
        (Section.TEMPLATE, _G.GENERATED_OUTPUT),
        "--custom-file-header-path",
        help="Custom file header file path",
        type=str,
    ),
    value(
        (Section.TEMPLATE, _G.GENERATED_OUTPUT),
        "--custom-file-header-mode",
        help="How to combine a custom file header with the generated header (default: replace)",
        choices=_values(CustomFileHeaderMode),
    ),
    value(
        (Section.TEMPLATE, _G.CUSTOM_TEMPLATES),
        "--custom-template-dir",
        help="Custom template directory. Jinja2 renders custom templates and will become optional: "
        "declare the jinja2 extra.",
        type=str,
    ),
    value(
        (Section.TEMPLATE, _C.BASE),
        "--encoding",
        help=f"The encoding of input and output (default: {DEFAULT_ENCODING})",
    ),
    value(
        (Section.TEMPLATE, _G.CUSTOM_TEMPLATES),
        "--extra-template-data",
        help="Extra template data for output models as inline JSON or a JSON file path. "
        "For OpenAPI and Jsonschema the keys are the spec path of the object, or the name of the object if you want to "
        "apply the template data to multiple objects with the same name. "
        "If you are using another input file type (e.g. GraphQL), the key is the name of the object. "
        "The value is a dictionary of the template data to add.",
        type=str,
    ),
    value(
        (Section.TEMPLATE, _G.CUSTOM_TEMPLATES),
        "--validators",
        help="Validators configuration as inline JSON or a JSON file path. "
        "Defines field validators for Pydantic v2 models. "
        "Keys are model names, values contain validator definitions with field, function, and mode.",
        type=str,
    ),
    flag(
        Section.TEMPLATE,
        "--generate-schema-validators",
        help="Generate Pydantic v2 model validators for JSON Schema rules that cannot be represented as type hints "
        "(experimental).",
    ),
    toggle(
        Section.TEMPLATE,
        "--check-pattern-maps-strictly",
        help="Under --generate-schema-validators, count every key that a pattern of patternProperties admits, check "
        "declared keys against the patterns that match them, and keep the propertyNames keys and the other types of a "
        "type array of an object without properties",
    ),
    value(
        Section.TEMPLATE,
        "--schema-validator-type",
        help="Select the schema-derived runtime validator backend. "
        "'pydantic-v2' generates Pydantic v2 model validators (experimental).",
        choices=_values(SchemaValidatorType),
    ),
    value(
        Section.TEMPLATE,
        "--schema-validator-base-class-name",
        help="Set the generated shared Pydantic v2 schema runtime validator base class name.",
        type=str,
    ),
    toggle(
        (Section.TEMPLATE, _G.IMPORTS),
        "--use-type-checking-imports",
        help="Allow Ruff to move typing-only imports into TYPE_CHECKING blocks. "
        "By default this stays enabled, except for multi-module Ruff formatting of modular Pydantic output "
        "where referenced models stay imported at runtime. "
        "Use --no-use-type-checking-imports to force runtime imports.",
    ),
    flag(
        (Section.TEMPLATE, _G.OUTPUT_FORMATTING),
        "--use-double-quotes",
        help="Model generated with double quotes. Single quotes or "
        "your black config skip_string_normalization value will be used without this option.",
    ),
    flag(
        (Section.TEMPLATE, _G.OUTPUT_FORMATTING),
        "--wrap-string-literal",
        help="Wrap string literal by using black `experimental-string-processing` option (require black 20.8b0 or "
        "later)",
    ),
    value(
        (Section.BASE, _C.TEMPLATE, _G.IMPORTS),
        "--additional-imports",
        help='Custom imports for output (delimited list input). For example "datetime.date,datetime.datetime"',
        type=str,
    ),
    value(
        (Section.BASE, _C.TEMPLATE, _G.GENERATED_OUTPUT),
        "--class-decorators",
        help="Custom decorators for generated model classes (delimited list input). "
        'For example "@dataclass_json(letter_case=LetterCase.CAMEL)". '
        'The "@" prefix is optional and will be added automatically if missing.',
        type=str,
    ),
    value(
        (Section.BASE, _C.TEMPLATE, _G.OUTPUT_FORMATTING),
        "--formatters",
        help="Formatters (current default: black, isort; future default: builtin). "
        "For Ruff projects, use --formatters ruff-check ruff-format "
        f"(install with {install_command('ruff', row='plain')}). "
        "Use --formatters builtin when no external formatter is used or generation speed takes priority. "
        "Keep --formatters black isort to preserve existing formatting. "
        "Presets dated 20260909 or later include builtin; explicit formatters override presets. "
        "The future builtin default reduces installation dependencies and version constraints. "
        "Black/isort remain required today; declare [black,isort] extras for their later optional installation. "
        "Review custom-template output with builtin. "
        "Details: https://datamodel-code-generator.koxudaxi.dev/formatter-behavior/",
        choices=_values(Formatter),
        nargs="+",
    ),
    clear(
        (Section.BASE, _C.TEMPLATE, _G.OUTPUT_FORMATTING),
        "--no-formatters",
        help="Select no formatter, not even the default or a preset's formatters; --custom-formatters still run.",
    ),
    value(
        (Section.BASE, _C.TEMPLATE, _G.OUTPUT_FORMATTING),
        "--custom-formatters",
        help="List of modules with custom formatter (delimited list input).",
        type=str,
    ),
    value(
        (Section.TEMPLATE, _G.OUTPUT_FORMATTING),
        "--custom-formatters-kwargs",
        help="Custom formatter kwargs as inline JSON or a JSON file path.",
        type=str,
    ),
    # Options specific to OpenAPI input schemas
    value(
        (Section.OPENAPI, _G.OPENAPI_SCOPES),
        "--openapi-scopes",
        help="Scopes of OpenAPI model generation (default: schemas)",
        choices=_values(OpenAPIScope),
        nargs="+",
    ),
    flag(
        (Section.OPENAPI, _G.OPENAPI_NAMING),
        "--use-operation-id-as-name",
        help="use operation id of OpenAPI as class names of models",
    ),
    flag(
        (Section.OPENAPI, _G.OPENAPI_PATHS),
        "--include-path-parameters",
        help="Include path parameters in generated parameter models in addition to query parameters (Only OpenAPI)",
    ),
    value(
        (Section.OPENAPI, _G.OPENAPI_PATHS),
        "--openapi-include-paths",
        nargs="+",
        metavar="PATTERN",
        help="Include only OpenAPI paths matching fnmatch patterns. "
        "Use wildcards: '*' matches any chars, '?' matches single char. "
        "Example: '/users/*' '/products'. "
        "Requires '--openapi-scopes' to include 'paths'.",
    ),
    flag(
        Section.OPENAPI,
        "--openapi-include-info-version",
        help="Emit OpenAPI info.version as OPENAPI_INFO_VERSION in generated models",
    ),
    value(
        (Section.OPENAPI, _G.OPENAPI_SCOPES),
        "--openapi-as-definitions",
        help="Give each given kind of OpenAPI schema the model a definitions entry of the same schema gets. "
        "paths-bodies: the request and response bodies of the paths and webhooks scopes, so an object body with a type "
        "array such as [object, null], or with map keywords and no type, becomes a model instead of an inline type, "
        "and an object body with patternProperties or propertyNames becomes the root model of a map. ref-components: a "
        "component written as a $ref to a recursive schema with sibling keywords, which gets the root model; without "
        "it such a component gets no model, and a reference to it is typed by the schema its $ref names",
        choices=_values(OpenAPIAsDefinitions),
        nargs="+",
    ),
    deprecated(Section.OPENAPI, "--validation"),
    value(
        (Section.OPENAPI, _G.READ_ONLY_WRITE_ONLY),
        "--read-only-write-only-model-type",
        help="Model generation for readOnly/writeOnly fields: "
        "'request-response' = Request/Response models only (no base model), "
        "'all' = Base + Request + Response models.",
        choices=_values(ReadOnlyWriteOnlyModelType),
    ),
    toggle(
        (Section.OPENAPI, _G.READ_ONLY_WRITE_ONLY),
        "--dedupe-request-response-variants",
        help="Decide Request/Response variants from the schema graph and build each inline model once, without the "
        "unreferenced duplicate classes and shifted class numbers of 0.86.0",
    ),
    toggle(
        (Section.OPENAPI, _G.READ_ONLY_WRITE_ONLY),
        "--dedupe-numbered-request-response-variants",
        help="Deduplicate a Request or Response variant of a numbered model (Page1Request of an inline copy Page1 of "
        "Page) by the variant name of the name its model was numbered from, so it merges into PageRequest where it "
        "renders the same; without it such a variant stays a class of its own, as in 0.90.0",
    ),
    flag(
        (Section.OPENAPI, _G.OPENAPI_NAMING),
        "--use-status-code-in-response-name",
        help="Include HTTP status code in response model names (e.g., ResourceGetResponse200, "
        "ResourceGetResponseDefault)",
    ),
    toggle(
        (Section.OPENAPI, _G.OPENAPI_PATHS),
        "--keep-first-character-of-path-keys",
        help="Strip only a leading slash from a paths key when naming its operation models, as webhook names are, so "
        "the key pets gives PetsGetResponse; without it the first character is dropped whatever it is "
        "(EtsGetResponse), as in 0.88.0",
    ),
    toggle(
        (Section.OPENAPI, _G.OPENAPI_PATHS),
        "--keep-content-parameter-refs",
        help="Type a parameter whose content media schema is a $ref by the model the $ref names, its Request variant "
        "under --read-only-write-only-model-type request-response, as a schema parameter is; without it the parameter "
        "takes a copy of the referenced schema (a model of its own name, or the inline type of a scalar, with its "
        "default and description), as in 0.89.0",
    ),
    toggle(
        (Section.OPENAPI, _G.OPENAPI_PATHS),
        "--skip-path-extension-keys",
        help="In the paths scope, skip a key of paths that begins with x-, a specification extension and no path item, "
        "as the api scope does; without it such a key that holds an object is read as a path and its operations get "
        "models, as in 0.89.0",
    ),
    toggle(
        (Section.OPENAPI, _G.OPENAPI_PATHS),
        "--use-enum-field-as-literal-in-operations",
        help="Write the enum of an operation's request body or response as a root model of a Literal where "
        "x-enum-field-as-literal, --enum-field-as-literal-map or --enum-field-as-literal would write a definition's "
        "enum so; without it such an enum stays an Enum class, as in 0.90.0",
    ),
    # Schema version options (for both JSON Schema and OpenAPI)
    value(
        Section.BASE,
        "--schema-version",
        help="Schema version. Valid values depend on input type: "
        "JsonSchema: auto, draft-04, draft-06, draft-07, 2019-09, 2020-12. "
        "OpenAPI: auto, 3.0, 3.1, 3.2. "
        "AsyncAPI: auto, 2.0, 3.0. "
        "XMLSchema: auto, 1.0, 1.1. "
        "Protobuf: auto, proto2, proto3, 2023. "
        "(default: auto - detected from $schema, openapi/asyncapi field, XML Schema versioning attributes, "
        "or Protobuf syntax/edition)",
    ),
    value(
        Section.BASE,
        "--schema-version-mode",
        help="Schema version validation mode. "
        "'lenient': accept all features regardless of version (default). "
        "'strict': warn on features outside declared/detected version.",
        choices=_values(VersionMode),
    ),
    toggle(
        Section.BASE,
        "--read-schema-version-per-document",
        help="Read each document by the schema version it declares ($schema, or the openapi or asyncapi field); a "
        "referenced document that declares none takes the version of the document whose reference loads it. The "
        "keywords beside a $ref and the features of the version (const, prefixItems, $id, $defs) then apply as each "
        "document's version says: a 2020-12 file of a directory read after a draft-07 one keeps the maximum beside an "
        "allOf $ref, an OpenAPI 3.1 document reads $ref siblings as JSON Schema 2020-12 does, and a draft-07 document "
        "sets no property-count rule for a minProperties beside an allOf $ref. Without it the version of the document "
        "parsed first applies to every document, as in 0.88.0. An explicit --schema-version gives every document its "
        "version; the schema validators then read an OpenAPI or AsyncAPI document by it only with the option, else as "
        "draft 7.",
    ),
    toggle(
        Section.BASE,
        "--read-escaped-ref-pointers",
        help="Read the ~0 and ~1 escapes and the percent-encoded octets of a $ref JSON pointer as the characters they "
        "encode, as JSON Pointer and URI fragments do, so #/$defs/a~1b, and #/$defs/Foo Bar written with its space "
        "percent-encoded, name the models of the definitions a/b and Foo Bar; without it such a reference gets a copy "
        "of its target named after the escaped text (A1b, Foo20Bar), as in 0.89.0.",
    ),
    toggle(
        Section.BASE,
        "--read-dynamic-anchors-in-document-order",
        help="Resolve a plain-name $dynamicRef that its dynamic scope does not bind from the $dynamicAnchors its "
        "document declares, the first in document order when several declare the name, and a $ref or $dynamicRef to a "
        "plain name several schemas of a document declare to the first of them; without it the $dynamicRef takes the "
        "first anchor the parse has met, so the answer depends on the order of the schemas, and the $ref the last "
        "declaration, as in 0.89.0.",
    ),
    toggle(
        Section.BASE,
        "--read-ids-with-empty-fragment",
        help="Read an $id that ends with an empty fragment, such as https://example.com/item.json#, as the URI without "
        "it, which names the same resource, so a $ref to https://example.com/item.json# or "
        "https://example.com/item.json#/definitions/Tag in the document that declares it resolves inside that "
        "document; without it such a $ref loads the document at that URL, from the network, as in 0.96.0.",
    ),
    toggle(
        Section.BASE,
        "--read-refs-in-their-resources",
        help="Read every $id, $anchor and $dynamicAnchor wherever a document holds schemas, and read each $ref in the "
        "schema resource that encloses it: a $defs member that declares a $id is the resource its own $defs pointers "
        "name, a schema an OpenAPI operation, webhook or component holds inline is a resource of its own, a plain-name "
        "anchor of another document names the schema that declares it, and a $ref to a resource the run holds is never "
        "fetched. Without it a $defs member without schema keywords is read for its entries alone, only "
        "components/schemas are OpenAPI resources, an anchor of another document is found by the released search, and "
        "a URL another document of the run declares is fetched where that succeeds, as in 0.100.0.",
        preset=PresetJoin(
            "20261123",
            {"read_refs_in_their_resources": True},
            title="References in their schema resources",
            description=(
                "Read every $id, $anchor and $dynamicAnchor wherever a document holds schemas ($defs members, inline "
                "OpenAPI operation and component schemas) and each $ref in the schema resource that encloses it, so a "
                "pointer inside a bundled $id resource names that resource's definitions, a plain-name anchor of "
                "another document names its schema, and a resource the run holds is never fetched."
            ),
        ),
    ),
    toggle(
        Section.BASE,
        "--read-single-allof-refs-in-their-documents",
        help="Read the target of the one $ref of an allOf as every other $ref is read: in the document that holds the "
        "schema, a plain-name anchor such as #code included. The target decides whether the schema is typed as the "
        "referenced enum, as a root model over it, or refused, so a schema of a referenced file takes the form the "
        "same schemas take in one file; without it the target is read at the same pointer of the input document, where "
        "a plain-name anchor names nothing, so such a schema can be written as a copy of the enum or refused, as in "
        "0.96.0.",
    ),
    toggle(
        Section.BASE,
        "--read-ref-siblings-in-closed-allof-members",
        help="Read every keyword beside a $ref of the allOf members an unevaluatedProperties: false allOf member "
        "evaluates where the schema version applies them, as the schema validators read them, so a dependentSchemas "
        "or $dynamicRef beside such a $ref, which may evaluate more properties, keeps the member from closing the "
        "model and a payload with those properties validates; without it they are read only where the $ref member "
        "holds an applicator, if, patternProperties, additionalProperties or unevaluatedProperties, and the model "
        "forbids those properties, as in 0.97.0.",
    ),
    toggle(
        Section.BASE,
        "--ignore-ref-siblings-by-schema-version",
        help="Read a $ref as its target alone where the schema version ignores the keywords beside it (JSON Schema "
        "draft 4, 6 and 7, OpenAPI 3.0, AsyncAPI 2), as those versions specify: a $ref with a sibling format: binary "
        "takes the referenced type instead of bytes, a sibling uniqueItems makes no set, and a sibling description or "
        "default is left out too. Versions that apply the keywords beside a $ref (2019-09 and later, OpenAPI 3.1 and "
        "later, AsyncAPI 3) read them as before. Without it the keywords beside such a $ref overlay its target, as in "
        "0.88.0. The version is the one --read-schema-version-per-document reads, else that of the document parsed. A "
        "schema converted from Avro, XML Schema or Protocol Buffers keeps the keywords its converter writes beside a "
        "$ref, such as the default of a field typed by a named type.",
    ),
    toggle(
        Section.BASE,
        "--read-yaml-1-1-numbers-as-text",
        help="Read an unquoted YAML value with _ or : that YAML 1.1 reads as a number, such as 16:9 (969 in base 60), "
        "18_24 or 1_000, as its text, as YAML 1.2, which OpenAPI names, and the ryaml reader read it, also in enum, "
        "default, const, examples, extensions and YAML data input. Without it such a value is the number YAML 1.1 "
        "reads, as in 0.96.0, except where a string is required in a document that the number makes invalid (a "
        "pattern, a format, a description, a component or property name), where it is its text.",
    ),
    # Options specific to GraphQL input schemas
    flag(
        Section.GRAPHQL,
        "--graphql-no-typename",
        help="Exclude __typename field from generated GraphQL models. "
        "Useful when using generated models for GraphQL mutations.",
    ),
    # General options
    flag(
        Section.GENERAL,
        "--check",
        help="Verify generated files are up-to-date without modifying them. "
        "Exits with code 1 if differences found, 0 if up-to-date. "
        "Useful for CI to ensure generated code is committed.",
    ),
    value(
        Section.GENERAL,
        "--diff-against",
        metavar="BASELINE_INPUT",
        help="Generate BASELINE_INPUT and the current --input into temporary outputs, then show the generated-code "
        "diff from baseline to current. Requires --input and --output; --output is a virtual output path that selects "
        "file or directory layout and is never modified. Exits with code 1 when generated outputs differ.",
        requires=(
            CLIOptionRelation(
                "--input", message="`--diff-against` requires --input with the current local schema path."
            ),
            CLIOptionRelation(
                "--output", message="`--diff-against` requires --output to select file or directory output layout."
            ),
        ),
        conflicts=(
            *_relations("--check --watch --url --input-model --emit-model-metadata --fail-on-multi-module-stdout"),
            *_relations(
                "--job --all-jobs", message="`--diff-against` compares one profile or input and cannot run named jobs."
            ),
        ),
    ),
    flag(Section.GENERAL, "--debug", help=f'show debug message (require "debug". {install_command("debug")})'),
    flag(Section.GENERAL, "--disable-warnings", help="disable warnings"),
    *_list_options(),
    value(
        Section.GENERAL,
        "-h",
        "--help",
        action="help",
        default="==SUPPRESS==",
        help="show this help message and exit",
        config=False,
    ),
    flag(Section.GENERAL, "--no-color", help="disable colorized output", default=False, config=False),
    value(
        Section.GENERAL,
        "--output-format",
        choices=["text", "json"],
        help="Format for command output (default: text). Use json for structured output when supported.",
        config=False,
    ),
    flag(
        Section.GENERAL,
        "--fail-on-multi-module-stdout",
        help="Return an error instead of concatenating multiple generated modules in text stdout. "
        "This does not affect single-module, JSON, or file output.",
        config=False,
    ),
    value(
        Section.GENERAL,
        "--output-format-json-schema",
        choices=list(JSON_SCHEMA_NAMES),
        metavar=f"{{{','.join(JSON_SCHEMA_NAMES)}}}",
        help="Output JSON Schema for the selected JSON output or JSON configuration format and exit.",
        config=False,
    ),
    flag(
        Section.GENERAL,
        "--generate-pyproject-config",
        help="Generate pyproject.toml configuration from the provided CLI arguments and exit",
        config=False,
    ),
    flag(
        Section.GENERAL,
        "--generate-cli-command",
        help="Generate CLI command from pyproject.toml configuration and exit",
        config=False,
    ),
    value(
        Section.GENERAL,
        "--generate-prompt",
        type=str,
        nargs="?",
        const="",
        metavar="QUESTION",
        help="Generate a prompt for consulting LLMs about CLI options. "
        "Optionally provide your question as an argument. "
        "Pipe to CLI tools (e.g., `| claude -p`, `| codex exec`) "
        "or copy to clipboard (e.g., `| pbcopy`, `| xclip`) for web LLM chats.",
        config=False,
    ),
    value(
        Section.GENERAL,
        "--recommend-options",
        nargs="?",
        const=True,
        type=_recommend_mode,
        metavar="",
        help="Analyse the input schema and print the options to add on top of the latest dated preset, with the "
        "reasons, as a command and a pyproject.toml block, then exit (experimental). Schemas of up to 3000 estimated "
        "models are also compared by generating the candidate configurations in memory. With --generate-prompt, the "
        "prompt includes the recommendation instead.",
        config=False,
    ),
    flag(
        Section.GENERAL,
        "--write-pyproject-config",
        help="Write the pyproject.toml table that --recommend-options or --generate-pyproject-config prints into "
        "the project's pyproject.toml, setting only the keys it adds or changes, and print what was written "
        "(experimental).",
        config=False,
        conflicts=_relations("--ignore-pyproject"),
    ),
    flag(
        Section.GENERAL, "--ignore-pyproject", help="Ignore pyproject.toml configuration", default=False, config=False
    ),
    toggle(
        Section.GENERAL,
        "--read-implied-options-from-merged-values",
        help="Read the options that another option turns on (--use-annotated turns on --field-constraints) from the "
        "arguments and pyproject.toml together, so --no-use-annotated over use-annotated = true in pyproject.toml also "
        "leaves --field-constraints off where pyproject.toml gives it false, as use-annotated = false there does; "
        "without it --field-constraints stays on, as in 0.96.0.",
    ),
    value(
        Section.GENERAL,
        "--install-skill",
        choices=["codex", "claude-code"],
        metavar="{codex,claude-code}",
        help="Install the bundled datamodel-code-generator Agent Skill and exit (experimental). "
        "Use --skill-scope to select a project or personal installation.",
        config=False,
    ),
    value(
        Section.GENERAL,
        "--skill-scope",
        choices=["project", "user"],
        metavar="{project,user}",
        help="Installation scope for --install-skill (default: project). "
        "Project installs use .agents/skills or .claude/skills; user installs use the matching home directory.",
        config=False,
    ),
    flag(
        Section.GENERAL,
        "--overwrite-skill",
        help="Replace an existing regular skill directory when used with --install-skill.",
        default=False,
        config=False,
    ),
    value(
        Section.GENERAL,
        "--profile",
        help="Use a named profile from pyproject.toml [tool.datamodel-codegen.profiles.<name>]",
        config=False,
    ),
    value(
        Exclusive.JOB_SELECTION,
        "--job",
        action="append",
        metavar="NAME",
        help="Run a named job from pyproject.toml [tool.datamodel-codegen.jobs.<name>] (experimental). Can be "
        "repeated.",
        config=False,
    ),
    flag(
        Exclusive.JOB_SELECTION,
        "--all-jobs",
        help="Run every named job from pyproject.toml in declaration order (experimental).",
        config=False,
    ),
    flag(Section.GENERAL, "--watch", help="Watch input file(s) for changes and regenerate output automatically"),
    value(
        Section.GENERAL,
        "--watch-delay",
        type=float,
        help=f"Debounce delay in seconds for watch mode (default: {DEFAULT_WATCH_DELAY})",
    ),
    flag(Section.GENERAL, "--version", help="show version", default=False, config=False),
    # Target generation options
    value(
        Exclusive.TARGET_SELECTION,
        "--generate-server",
        choices=_values(ServerType),
        help="Generate a FastAPI server package for the models; the --server-* options configure it (experimental).",
    ),
    *_package_options("server"),
    value(
        Section.TARGET,
        "--server-layout",
        choices=_values(ServerLayout),
        help="Write one router module per tag (default) or a single routes module (experimental).",
    ),
    value(
        Section.TARGET,
        "--server-handler-mode",
        choices=_values(ServerHandlerMode),
        help="Make service methods plain functions (default) or coroutine functions (experimental).",
    ),
    value(
        Section.TARGET,
        "--server-handler-modes",
        type=str,
        help="Per-operation handler modes as inline JSON or a JSON file path, keyed by operation reference such as "
        "/paths/~1pets/get; overrides --server-handler-mode for matched operations (experimental).",
    ),
    toggle(
        Section.TARGET,
        "--server-include-request",
        help="Pass the Starlette Request to every service method (experimental).",
    ),
    value(
        Section.TARGET,
        "--server-body-mode",
        choices=_values(ServerBodyMode),
        help="Pass request bodies as validated models (default) or as the raw Request (experimental).",
    ),
    value(
        Section.TARGET,
        "--server-body-modes",
        type=str,
        help="Per-operation body modes as inline JSON or a JSON file path, keyed by operation reference; "
        "overrides --server-body-mode for matched operations (experimental).",
    ),
    value(
        Section.TARGET,
        "--server-primary-responses",
        type=str,
        help="The declared response a bare return value takes, per operation, as inline JSON or a JSON file path: "
        'an object with "status_code" and an optional "media_type" for each operation reference (experimental).',
    ),
    value(
        Section.TARGET,
        "--server-operation-names",
        type=str,
        help="Service method names per operation reference as inline JSON or a JSON file path (experimental).",
    ),
    value(
        Section.TARGET,
        "--server-router-names",
        type=str,
        help="Router group names keyed by group key, such as tag:pets, as inline JSON or a JSON file path "
        "(experimental).",
    ),
    value(
        Section.TARGET,
        "--server-parameter-names",
        type=str,
        help="Argument names per operation reference, each keyed by location and name such as query:limit, "
        "as inline JSON or a JSON file path (experimental).",
    ),
    value(
        Exclusive.TARGET_SELECTION,
        "--generate-client",
        choices=_values(ClientType),
        help="Generate an HTTPX2 client package for the models; the --client-* options configure it (experimental).",
    ),
    *_package_options("client"),
    value(
        Section.TARGET,
        "--client-signature-style",
        choices=_values(ClientSignatureStyle),
        help="Declare operation arguments as keywords (default) or as one Unpack[TypedDict] (experimental).",
    ),
    value(
        Section.TARGET,
        "--client-body-arguments",
        choices=_values(ClientBodyArguments),
        help="Take request bodies as one body argument (default) or also as field arguments (experimental).",
    ),
    value(
        Section.TARGET,
        "--client-resource-names",
        type=str,
        help='Resource namespaces keyed by tag, such as {"pets": "store.pets"}, as inline JSON or a JSON file path '
        "(experimental).",
    ),
    value(
        Section.TARGET,
        "--client-operations",
        type=str,
        help="Settings per operation reference, such as /paths/~1pets/get, as inline JSON or a JSON file path; "
        "their body_arguments overrides --client-body-arguments (experimental).",
    ),
    value(
        Section.TARGET,
        "--client-default-base-url",
        help="Server URL of the operations that declare no servers (experimental).",
    ),
    value(
        Section.TARGET,
        "--client-server-base-url",
        help="Base URL that relative server URLs of the document resolve against (experimental).",
    ),
    toggle(
        Section.TARGET,
        "--client-deep-object-brackets",
        help="Write a deepObject query parameter or URL-encoded form member in the bracket form Stripe documents: an "
        "array as name[]=value for each item, a scalar as name=value, nested objects as name[key][key]=value, an array "
        "of objects as name[index][key]=value, and a value that may be a scalar or an object as the one a call gives, "
        "instead of leaving its operation out (experimental).",
    ),
    value(
        Section.TARGET,
        "--client-asyncapi-perspective",
        choices=_values(ClientAsyncAPIPerspective),
        help="Read an AsyncAPI document as the server's (default), whose sends the client receives, or as the "
        "client's, for its WebSocket channels; the client receives every message of a server-sent event channel "
        "(experimental).",
    ),
    value(
        Section.TARGET,
        "--client-protocols",
        type=str,
        help="Pagination, polling, stream, WebSocket, cache, upload, and webhook helpers by name, as inline JSON or a "
        "JSON file path (experimental).",
    ),
)
COMMAND_DESTS: Final = frozenset(option.dest for option in OPTIONS if not option.config)
"""The names argparse stores the options of a command under, which no config carries."""
