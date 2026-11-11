"""Built-in immutable option presets for the CLI."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise as _pairwise
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from pydantic import ConfigDict, PrivateAttr
from typing_extensions import TypedDict

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    Formatter,
    PythonVersion,
)
from datamodel_code_generator._option_names import option_arguments as _option_arguments
from datamodel_code_generator._option_names import option_flags, toml_value
from datamodel_code_generator._option_rules import implied_options
from datamodel_code_generator._registry_render import _render_registry_json
from datamodel_code_generator._requirements import python_target_meets
from datamodel_code_generator._shared_types import LiteralType
from datamodel_code_generator.base_config import BaseGenerateConfig
from datamodel_code_generator.cli_options import CLI_OPTION_META
from datamodel_code_generator.enums import (
    _PYDANTIC_MODEL_TYPES,
    AllOfClassHierarchy,
    AllOfMergeMode,
    CollapseRootModelsNameStrategy,
    ConstructorFieldOrder,
    DataModelType,
    DefaultValueType,
    ExtraFields,
    FieldTypeCollisionStrategy,
    InputFileType,
    NamingStrategy,
    OpenAPIAsDefinitions,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    TargetPydanticVersion,
    UnionMode,
    VersionMode,
)
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Set as AbstractSet

    from typing_extensions import Unpack

    from datamodel_code_generator.__main__ import TomlValue
    from datamodel_code_generator._types.generate_config_dict import BaseGenerateConfig as BaseGenerateConfigDict

PresetFormat = Literal["json", "markdown"]
PresetEnumConfigValue: TypeAlias = (
    LiteralType
    | NamingStrategy
    | TargetPydanticVersion
    | ReuseScope
    | AllOfMergeMode
    | AllOfClassHierarchy
    | CollapseRootModelsNameStrategy
    | UnionMode
    | ReadOnlyWriteOnlyModelType
    | FieldTypeCollisionStrategy
    | VersionMode
    | DateClassType
    | DatetimeClassType
    | ExtraFields
    | DefaultValueType
    | ConstructorFieldOrder
    | OpenAPIAsDefinitions
    | Formatter
)
PresetEnumSequenceConfigValue: TypeAlias = tuple[PresetEnumConfigValue, ...]
PresetConfigValue: TypeAlias = bool | PresetEnumConfigValue | PresetEnumSequenceConfigValue
PresetRawConfigValue: TypeAlias = PresetConfigValue | Sequence[PresetEnumConfigValue] | str | None


class _PresetOptionGroupDict(TypedDict):
    title: str
    options: list[str]
    description: str


class _PresetCopyableConfigContextDict(TypedDict):
    input_file_type: str
    output_model_type: str
    target_python_version: str


class _PresetCopyableConfigDict(TypedDict):
    context: _PresetCopyableConfigContextDict
    pyproject_toml: str
    cli: str


class _PresetInfoDict(TypedDict):
    name: str
    summary: str
    description: str
    requires_target_python_version: bool
    target_python_version: str
    option_groups: list[_PresetOptionGroupDict]
    copyable_config: _PresetCopyableConfigDict


class PresetError(Exception):
    """Raised when a preset cannot be resolved."""


class PresetName(str, Enum):
    """Available immutable preset names."""

    StandardPy31020260619 = "standard-py310-20260619"
    StandardPy31120260619 = "standard-py311-20260619"
    StandardPy31220260619 = "standard-py312-20260619"
    StandardPy31320260619 = "standard-py313-20260619"
    StandardPy31420260619 = "standard-py314-20260619"
    PracticalPy31020260619 = "practical-py310-20260619"
    PracticalPy31120260619 = "practical-py311-20260619"
    PracticalPy31220260619 = "practical-py312-20260619"
    PracticalPy31320260619 = "practical-py313-20260619"
    PracticalPy31420260619 = "practical-py314-20260619"
    StandardPy31020260826 = "standard-py310-20260826"
    StandardPy31120260826 = "standard-py311-20260826"
    StandardPy31220260826 = "standard-py312-20260826"
    StandardPy31320260826 = "standard-py313-20260826"
    StandardPy31420260826 = "standard-py314-20260826"
    PracticalPy31020260826 = "practical-py310-20260826"
    PracticalPy31120260826 = "practical-py311-20260826"
    PracticalPy31220260826 = "practical-py312-20260826"
    PracticalPy31320260826 = "practical-py313-20260826"
    PracticalPy31420260826 = "practical-py314-20260826"

    StandardPy31020260909 = "standard-py310-20260909"
    StandardPy31120260909 = "standard-py311-20260909"
    StandardPy31220260909 = "standard-py312-20260909"
    StandardPy31320260909 = "standard-py313-20260909"
    StandardPy31420260909 = "standard-py314-20260909"
    PracticalPy31020260909 = "practical-py310-20260909"
    PracticalPy31120260909 = "practical-py311-20260909"
    PracticalPy31220260909 = "practical-py312-20260909"
    PracticalPy31320260909 = "practical-py313-20260909"
    PracticalPy31420260909 = "practical-py314-20260909"

    StandardPy31020261015 = "standard-py310-20261015"
    StandardPy31120261015 = "standard-py311-20261015"
    StandardPy31220261015 = "standard-py312-20261015"
    StandardPy31320261015 = "standard-py313-20261015"
    StandardPy31420261015 = "standard-py314-20261015"
    StandardPy31520261015 = "standard-py315-20261015"
    PracticalPy31020261015 = "practical-py310-20261015"
    PracticalPy31120261015 = "practical-py311-20261015"
    PracticalPy31220261015 = "practical-py312-20261015"
    PracticalPy31320261015 = "practical-py313-20261015"
    PracticalPy31420261015 = "practical-py314-20261015"
    PracticalPy31520261015 = "practical-py315-20261015"

    StandardPy31020261017 = "standard-py310-20261017"
    StandardPy31120261017 = "standard-py311-20261017"
    StandardPy31220261017 = "standard-py312-20261017"
    StandardPy31320261017 = "standard-py313-20261017"
    StandardPy31420261017 = "standard-py314-20261017"
    StandardPy31520261017 = "standard-py315-20261017"
    PracticalPy31020261017 = "practical-py310-20261017"
    PracticalPy31120261017 = "practical-py311-20261017"
    PracticalPy31220261017 = "practical-py312-20261017"
    PracticalPy31320261017 = "practical-py313-20261017"
    PracticalPy31420261017 = "practical-py314-20261017"
    PracticalPy31520261017 = "practical-py315-20261017"

    StandardPy31020261027 = "standard-py310-20261027"
    StandardPy31120261027 = "standard-py311-20261027"
    StandardPy31220261027 = "standard-py312-20261027"
    StandardPy31320261027 = "standard-py313-20261027"
    StandardPy31420261027 = "standard-py314-20261027"
    StandardPy31520261027 = "standard-py315-20261027"
    PracticalPy31020261027 = "practical-py310-20261027"
    PracticalPy31120261027 = "practical-py311-20261027"
    PracticalPy31220261027 = "practical-py312-20261027"
    PracticalPy31320261027 = "practical-py313-20261027"
    PracticalPy31420261027 = "practical-py314-20261027"
    PracticalPy31520261027 = "practical-py315-20261027"

    StandardPy31020261104 = "standard-py310-20261104"
    StandardPy31120261104 = "standard-py311-20261104"
    StandardPy31220261104 = "standard-py312-20261104"
    StandardPy31320261104 = "standard-py313-20261104"
    StandardPy31420261104 = "standard-py314-20261104"
    StandardPy31520261104 = "standard-py315-20261104"
    PracticalPy31020261104 = "practical-py310-20261104"
    PracticalPy31120261104 = "practical-py311-20261104"
    PracticalPy31220261104 = "practical-py312-20261104"
    PracticalPy31320261104 = "practical-py313-20261104"
    PracticalPy31420261104 = "practical-py314-20261104"
    PracticalPy31520261104 = "practical-py315-20261104"

    StandardPy31020261106 = "standard-py310-20261106"
    StandardPy31120261106 = "standard-py311-20261106"
    StandardPy31220261106 = "standard-py312-20261106"
    StandardPy31320261106 = "standard-py313-20261106"
    StandardPy31420261106 = "standard-py314-20261106"
    StandardPy31520261106 = "standard-py315-20261106"
    PracticalPy31020261106 = "practical-py310-20261106"
    PracticalPy31120261106 = "practical-py311-20261106"
    PracticalPy31220261106 = "practical-py312-20261106"
    PracticalPy31320261106 = "practical-py313-20261106"
    PracticalPy31420261106 = "practical-py314-20261106"
    PracticalPy31520261106 = "practical-py315-20261106"

    StandardPy31020261111 = "standard-py310-20261111"
    StandardPy31120261111 = "standard-py311-20261111"
    StandardPy31220261111 = "standard-py312-20261111"
    StandardPy31320261111 = "standard-py313-20261111"
    StandardPy31420261111 = "standard-py314-20261111"
    StandardPy31520261111 = "standard-py315-20261111"
    PracticalPy31020261111 = "practical-py310-20261111"
    PracticalPy31120261111 = "practical-py311-20261111"
    PracticalPy31220261111 = "practical-py312-20261111"
    PracticalPy31320261111 = "practical-py313-20261111"
    PracticalPy31420261111 = "practical-py314-20261111"
    PracticalPy31520261111 = "practical-py315-20261111"


@dataclass(frozen=True, slots=True)
class PresetContext:
    """Context required to resolve a preset."""

    input_file_type: InputFileType
    output_model_type: DataModelType
    target_python_version: PythonVersion


@dataclass(frozen=True, slots=True)
class PresetInfo:
    """Public preset metadata used by CLI docs."""

    name: PresetName
    summary: str
    description: str
    requires_target_python_version: bool
    target_python_version: PythonVersion
    option_groups: tuple[PresetOptionGroup, ...]


@dataclass(frozen=True, slots=True)
class PresetConfigItem:
    """A typed config update that can be rendered as a CLI option."""

    field_name: str
    value: PresetConfigValue

    @property
    def applied_value(self) -> PresetConfigValue:
        """Return the value to assign to the existing runtime Config object."""
        return self.value

    @property
    def pyproject_value(self) -> bool | str | list[str]:
        """Return the value to render through existing config export helpers."""
        return toml_value(self.value)


@dataclass(frozen=True, slots=True)
class ResolvedPresetConfig:
    """Typed config updates resolved for a concrete generation context."""

    target_python_version: PythonVersion | None
    items: tuple[PresetConfigItem, ...]
    force_field_constraints: bool

    def updates(self) -> dict[str, PresetConfigValue | PythonVersion]:
        """Return the config updates a preset makes, in the order they are applied."""
        updates: dict[str, PresetConfigValue | PythonVersion] = {}
        if self.target_python_version is not None:
            updates["target_python_version"] = self.target_python_version
        updates.update((item.field_name, item.applied_value) for item in self.items)
        updates.update(implied_options({"use_annotated": self.force_field_constraints}))
        return updates


def _pyproject_values(items: Iterable[PresetConfigItem]) -> dict[str, bool | str | list[str]]:
    """Return the values of preset config items keyed by field name, as pyproject.toml spells them, in item order."""
    return {item.field_name: item.pyproject_value for item in items}


class PresetConfig(BaseGenerateConfig):
    """Typed immutable config updates supplied by a preset group."""

    model_config = ConfigDict(frozen=True)

    _items: tuple[PresetConfigItem, ...] = PrivateAttr(default=())

    def __init__(self, **values: Unpack[BaseGenerateConfigDict]) -> None:
        """Build immutable preset updates from statically checked GenerateConfig fields."""
        for field_name in values:
            if field_name not in type(self).model_fields:  # pragma: no cover
                msg = f"Preset field {stable_repr(field_name)} is not a BaseGenerateConfig field"
                raise PresetError(msg)

        super().__init__(**values)

        items: list[PresetConfigItem] = []
        for field_name, raw_value in values.items():
            value = _normalize_preset_config_value(
                field_name, cast("PresetRawConfigValue", raw_value), getattr(self, field_name)
            )
            items.append(PresetConfigItem(field_name=field_name, value=value))

        self._items = tuple(items)

    def __bool__(self) -> bool:
        """Return whether the preset config carries any updates."""
        return bool(self._items)

    def items(self) -> tuple[PresetConfigItem, ...]:
        """Return typed config field updates for internal preset processing."""
        return self._items


@dataclass(frozen=True, slots=True)
class PresetOptionGroup:
    """Documented option group for a preset."""

    title: str
    config: PresetConfig
    description: str
    input_file_types: frozenset[InputFileType] = frozenset()
    output_model_types: frozenset[DataModelType] = frozenset()
    requires_python_strenum: bool = False

    def __post_init__(self) -> None:
        """Check that the command line can turn off every option the group turns on, unless it is deprecated."""
        for item in self.config.items():
            if (
                item.value is True
                and _documented_option(item.field_name, negative=True) is None
                and not CLI_OPTION_META[_config_item_to_cli_option(item)].deprecated
            ):  # pragma: no cover
                msg = f"Preset option {_config_item_to_cli_option(item)} has no --no- form to turn it off"
                raise PresetError(msg)

    @property
    def options(self) -> tuple[str, ...]:
        """Return documented CLI options derived from typed config fields."""
        return tuple(_config_item_to_cli_option(item) for item in self.config.items())

    def applies_to(self, context: PresetContext) -> bool:
        """Return whether this option group applies to a preset context."""
        if self.input_file_types and context.input_file_type not in self.input_file_types:  # pragma: no cover
            return False
        if self.output_model_types and context.output_model_type not in self.output_model_types:
            return False
        return self.applies_to_python(context.target_python_version)

    def applies_to_python(self, target_python_version: PythonVersion) -> bool:
        """Return whether this group is available for a target Python version."""
        return not self.requires_python_strenum or python_target_meets(
            "python.strenum", target_python_version.version_key
        )


def _merge_preset_groups(groups: tuple[PresetOptionGroup, ...]) -> tuple[PresetConfigItem, ...]:
    """Merge preset groups; output-model groups override shared groups, other conflicting updates are rejected."""
    output_model_fields = {
        item.field_name for group in groups if group.output_model_types for item in group.config.items()
    }
    items: dict[str, PresetConfigItem] = {}
    for group in groups:
        for item in group.config.items():
            if not group.output_model_types and item.field_name in output_model_fields:
                continue
            existing = items.get(item.field_name)
            if existing is None:
                items[item.field_name] = item
                continue
            if existing.value != item.value:  # pragma: no cover
                msg = (
                    f"Preset field {stable_repr(item.field_name)} is configured with both "
                    f"{stable_repr(existing.value)} and {stable_repr(item.value)}"
                )
                raise PresetError(msg)
    return tuple(items.values())


def _normalize_preset_config_value(
    field_name: str,
    raw_value: PresetRawConfigValue,
    validated_value: PresetRawConfigValue,
) -> PresetConfigValue:
    """Normalize BaseGenerateConfig values that keep legacy string API types."""
    if field_name == "extra_fields":
        value = raw_value if isinstance(raw_value, ExtraFields) else validated_value
        if isinstance(value, ExtraFields):
            return value
        if isinstance(value, str):  # pragma: no branch
            try:
                return ExtraFields(value)
            except ValueError as exc:  # pragma: no cover
                msg = f"Unsupported extra_fields preset value: {stable_repr(value)}"
                raise PresetError(msg) from exc
    return _ensure_preset_config_value(field_name, validated_value)


def _ensure_preset_config_value(field_name: str, value: PresetRawConfigValue) -> PresetConfigValue:
    match value:
        case bool():
            return value
        case tuple() | list():
            return tuple(_ensure_preset_enum_config_value(field_name, item) for item in value)
        case _:
            return _ensure_preset_enum_config_value(field_name, value)


def _ensure_preset_enum_config_value(
    field_name: str,
    value: PresetRawConfigValue,
) -> PresetEnumConfigValue:
    if isinstance(value, PresetEnumConfigValue) and not isinstance(value, ExtraFields):
        return value
    msg = f"Preset field {stable_repr(field_name)} cannot be rendered as a preset CLI option"  # pragma: no cover
    raise PresetError(msg)  # pragma: no cover


def _documented_option(field_name: str, *, negative: bool = False) -> str | None:
    """Return the spelling the CLI reference documents of the option of a config field, or of its --no- form."""
    return next((flag for flag in option_flags(field_name, negative=negative) if flag in CLI_OPTION_META), None)


def _config_item_to_cli_option(item: PresetConfigItem) -> str:
    """Translate a typed config field name to the spelling of its CLI options that the CLI reference documents."""
    option = _documented_option(item.field_name)
    if option is None:  # pragma: no cover
        msg = f"Preset field {stable_repr(item.field_name)} does not map to a documented CLI option"
        raise PresetError(msg)
    arguments = _option_arguments(
        item.field_name, item.value, negative=lambda field: _documented_option(field, negative=True), flag=option
    )
    if arguments is None:  # pragma: no cover
        msg = f"Preset field {stable_repr(item.field_name)} does not map to a documented negative CLI option"
        raise PresetError(msg)
    return " ".join([arguments[0], *(str(value.value) for value in arguments[1:])])


_STANDARD_20260619_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    PresetOptionGroup(
        title="All output model types",
        config=PresetConfig(
            use_standard_collections=True,
            use_union_operator=True,
            use_annotated=True,
            enum_field_as_literal=LiteralType.One,
            use_subclass_enum=True,
            collapse_root_models=True,
            strict_nullable=True,
            set_default_enum_member=True,
            disable_timestamp=True,
        ),
        description=(
            "Use built-in collection syntax, PEP 604 unions, Annotated constraints, "
            "single-value enum Literals, typed enum subclasses, enum-member defaults, "
            "inline root wrappers, schema-accurate nullability, and reproducible file headers."
        ),
    ),
    PresetOptionGroup(
        title="Python 3.11+ targets",
        config=PresetConfig(use_specialized_enum=True),
        description="Use StrEnum or IntEnum only when the selected target Python version supports it.",
        requires_python_strenum=True,
    ),
    PresetOptionGroup(
        title="Pydantic v2 BaseModel and dataclass output",
        config=PresetConfig(
            snake_case_field=True,
            allow_population_by_field_name=True,
            use_frozen_field=True,
        ),
        description="Generate Pythonic field names while preserving input aliases and readOnly immutability metadata.",
        output_model_types=_PYDANTIC_MODEL_TYPES,
    ),
    PresetOptionGroup(
        title="msgspec Struct output",
        config=PresetConfig(
            snake_case_field=True,
            use_standard_primitive_types=True,
        ),
        description="Generate Pythonic field names with aliases and stdlib primitive types for schema formats.",
        output_model_types=frozenset({DataModelType.MsgspecStruct}),
    ),
    PresetOptionGroup(
        title="stdlib dataclass output",
        config=PresetConfig(use_standard_primitive_types=True),
        description="Use stdlib primitive types without renaming input keys because dataclasses do not carry aliases.",
        output_model_types=frozenset({DataModelType.DataclassesDataclass}),
    ),
    PresetOptionGroup(
        title="TypedDict output",
        config=PresetConfig(
            use_standard_primitive_types=True,
            use_frozen_field=True,
        ),
        description="Use stdlib primitive types and ReadOnly metadata without renaming dictionary keys.",
        output_model_types=frozenset({DataModelType.TypingTypedDict}),
    ),
)

_STANDARD_20260826_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    PresetOptionGroup(
        title="All output model types",
        config=PresetConfig(
            use_standard_collections=True,
            use_union_operator=True,
            use_annotated=True,
            enum_field_as_literal=LiteralType.One,
            use_subclass_enum=True,
            collapse_root_models=True,
            strict_nullable=True,
            disable_timestamp=True,
        ),
        description=(
            "Use built-in collection syntax, PEP 604 unions, Annotated constraints, "
            "single-value enum Literals, typed enum subclasses, inline root wrappers, "
            "schema-accurate nullability, and reproducible file headers."
        ),
    ),
    *_STANDARD_20260619_OPTION_GROUPS[1:],
    PresetOptionGroup(
        title="Serialized defaults",
        config=PresetConfig(deserialize_default_values=(DefaultValueType.Decimal, DefaultValueType.Enum)),
        description=(
            "Deserialize compatible serialized Decimal and enum defaults into generated Python values while "
            "preserving defaults that cannot be converted safely."
        ),
    ),
)

_PRACTICAL_20260619_EXTRA_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    PresetOptionGroup(
        title="Practical model structure and names",
        config=PresetConfig(
            reuse_model=True,
            collapse_reuse_models=True,
            use_title_as_name=True,
            naming_strategy=NamingStrategy.PrimaryFirst,
        ),
        description=(
            "Deduplicate identical models without empty inheritance wrappers, prefer schema titles for class names, "
            "and keep primary definitions ahead of inline duplicate names."
        ),
    ),
    PresetOptionGroup(
        title="Practical typing and defaults",
        config=PresetConfig(
            use_default_kwarg=True,
            use_object_type=True,
            use_tuple_for_fixed_items=True,
            use_unique_items_as_set=True,
            use_single_line_docstring=True,
        ),
        description=(
            "Render defaults explicitly, prefer object for unconstrained values, preserve fixed and unique arrays, "
            "and keep short docstrings concise."
        ),
    ),
    PresetOptionGroup(
        title="Schema documentation",
        config=PresetConfig(
            use_schema_description=True,
            use_field_description=True,
            use_field_description_example=True,
        ),
        description="Preserve schema and field descriptions, including examples, in generated model documentation.",
    ),
)

_PRACTICAL_20261104_EXTRA_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    PresetOptionGroup(
        title="Practical model structure and names",
        config=PresetConfig(reuse_model=True, collapse_reuse_models=True, use_title_as_name=True),
        description=(
            "Deduplicate identical models without empty inheritance wrappers and prefer schema titles for class "
            "names; the standard groups keep definitions ahead of inline duplicate names."
        ),
    ),
    *_PRACTICAL_20260619_EXTRA_OPTION_GROUPS[1:],
)
"""The practical extras of the 20261104 presets: the naming strategy comes from the standard groups."""
_PRACTICAL_EXTRA_OPTION_GROUPS_BY_DATE: dict[str, tuple[PresetOptionGroup, ...]] = {
    "20261104": _PRACTICAL_20261104_EXTRA_OPTION_GROUPS,
    "20261106": _PRACTICAL_20261104_EXTRA_OPTION_GROUPS,
    "20261111": _PRACTICAL_20261104_EXTRA_OPTION_GROUPS,
}
"""The practical extras of the dates whose extras are not those of 20260619."""

_STANDARD_20260909_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    *_STANDARD_20260826_OPTION_GROUPS,
    PresetOptionGroup(
        title="Built-in formatting",
        config=PresetConfig(formatters=[Formatter.BUILTIN]),
        description=(
            "Format generated models without running an external formatter. "
            "For projects using Ruff, override with --formatters ruff-check ruff-format. "
            "To preserve Black/isort formatting, override with --formatters black isort."
        ),
    ),
)

_RAW_VALUE_20261017_OPTION_GROUPS: dict[str, PresetOptionGroup] = {
    group.title: group
    for group in (
        PresetOptionGroup(
            title="stdlib dataclass output",
            config=PresetConfig(use_standard_primitive_types=True, enum_field_as_literal=LiteralType.All),
            description=(
                "Use stdlib primitive types without renaming input keys because dataclasses do not carry aliases. "
                "Stdlib dataclasses keep raw JSON values without conversion, so enums render as Literal values "
                "instead of enum classes; this overrides `--enum-field-as-literal one` from All output model types."
            ),
            output_model_types=frozenset({DataModelType.DataclassesDataclass}),
        ),
        PresetOptionGroup(
            title="TypedDict output",
            config=PresetConfig(
                use_standard_primitive_types=True,
                use_frozen_field=True,
                enum_field_as_literal=LiteralType.All,
            ),
            description=(
                "Use stdlib primitive types and ReadOnly metadata without renaming dictionary keys. "
                "TypedDict holds raw JSON values, so enums render as Literal values instead of enum classes; "
                "this overrides `--enum-field-as-literal one` from All output model types."
            ),
            output_model_types=frozenset({DataModelType.TypingTypedDict}),
        ),
    )
}
_STANDARD_20261017_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = tuple(
    _RAW_VALUE_20261017_OPTION_GROUPS.get(group.title, group) for group in _STANDARD_20260909_OPTION_GROUPS
)

_STANDARD_20261027_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    *_STANDARD_20261017_OPTION_GROUPS,
    PresetOptionGroup(
        title="Enums read as their const",
        config=PresetConfig(use_single_value_enum_as_const=True),
        description=(
            "Read an enum of one value, and an enum that admits the const beside it, as that const, so it renders "
            "as a Literal value instead of an enum class; one-value enum branches of a oneOf or anyOf merge into "
            "one enum."
        ),
    ),
    PresetOptionGroup(
        title="OpenAPI schemas modeled as definitions",
        config=PresetConfig(
            openapi_as_definitions=(OpenAPIAsDefinitions.PathsBodies, OpenAPIAsDefinitions.RefComponents)
        ),
        description=(
            "Give the request and response bodies of the OpenAPI paths and webhooks scopes the model a schema "
            "definition of the same body gets, so an object body with a type array such as [object, null] is a "
            "model instead of an inline type, and give an OpenAPI component written as a $ref to a recursive schema "
            "with sibling keywords the root model a definitions entry of the same schema gets, instead of no model."
        ),
    ),
    PresetOptionGroup(
        title="Site models for constrained references",
        config=PresetConfig(use_site_models_for_constrained_refs=True),
        description=(
            "Give an allOf of $ref members whose other parts add constraints, an enum or a type its own model of the "
            "values all parts admit, instead of the referenced type, which leaves the added assertions out; an "
            "allOf of enum references gets the enum of the members they share."
        ),
    ),
    PresetOptionGroup(
        title="Redundant constraints omitted",
        config=PresetConfig(omit_redundant_constraints=True),
        description=(
            "Do not write a constraint again beside a type that already enforces it: the bound of a zero-bound "
            "class such as NonNegativeInt, and the length and number constraints a const or enum Literal meets by "
            "its values, which also keeps such a const branch inline instead of a root model."
        ),
    ),
    PresetOptionGroup(
        title="Const read beside an enum",
        config=PresetConfig(read_const_beside_enum=True),
        description=(
            "Read a const beside an enum as JSON Schema does: a value is typed by the const that narrows the "
            "enum, and an enum that admits its const is read as that const."
        ),
    ),
    PresetOptionGroup(
        title="Literal values filtered",
        config=PresetConfig(filter_literal_values=True),
        description=(
            "Keep only the values of a const or enum that meet the length, pattern, bound and multipleOf "
            "constraints beside them."
        ),
    ),
    PresetOptionGroup(
        title="allOf keywords intersected",
        config=PresetConfig(intersect_allof_keywords=True),
        description=(
            "Combine the keywords of every part of an allOf, a $ref with sibling keywords and an inherited "
            "property override as JSON Schema validates them: the tightest bounds and the values every part "
            "admits."
        ),
    ),
    PresetOptionGroup(
        title="Nullable beside a type list",
        config=PresetConfig(read_nullable_beside_type_list=True),
        description=(
            "Write the null that nullable: true declares beside a type list with an object into the root model of "
            "that schema under strict nullable output."
        ),
    ),
    PresetOptionGroup(
        title="Field defaults kept off branch roots",
        config=PresetConfig(keep_field_defaults_off_branch_roots=True),
        description=(
            "Leave the default of a property off the root models extracted from the branches of its anyOf or oneOf."
        ),
    ),
    PresetOptionGroup(
        title="Tuples for closed arrays",
        config=PresetConfig(use_tuple_for_closed_arrays=True),
        description=(
            "Generate tuple types for positional arrays that no other item can follow, so that each position "
            "keeps its own type."
        ),
    ),
    PresetOptionGroup(
        title="One dict for object maps",
        config=PresetConfig(use_single_dict_for_object_maps=True),
        description=(
            "Type an object map whose keys patternProperties or propertyNames constrain as one dict of the values "
            "of every key class."
        ),
    ),
    PresetOptionGroup(
        title="Stricter pattern-map checks",
        config=PresetConfig(check_pattern_maps_strictly=True),
        description=(
            "Under --generate-schema-validators, count every key that a pattern admits, check declared keys "
            "against the patterns that match them, and keep the propertyNames keys and the other types of a type "
            "array of an object without properties."
        ),
    ),
    PresetOptionGroup(
        title="Typed map values beside other types",
        config=PresetConfig(use_typed_map_values_beside_other_types=True),
        description=(
            "Under --use-typed-nullable-map-values, also type the map values of a type array that lists object "
            "beside other types, such as [object, string]."
        ),
    ),
    PresetOptionGroup(
        title="Enum member values kept",
        config=PresetConfig(keep_enum_member_values=True),
        description=(
            "Write an enum as a plain Enum where its IntEnum, StrEnum or (T, Enum) base would change a member value, "
            "such as 2.5 beside integer members."
        ),
    ),
    PresetOptionGroup(
        title="Unevaluated properties read",
        config=PresetConfig(read_unevaluated_properties=True),
        description=(
            "Read a schema-valued unevaluatedProperties as additionalProperties where no applicator evaluates the "
            "object in place."
        ),
    ),
    PresetOptionGroup(
        title="Aliases pinned beside an alias generator",
        config=PresetConfig(pin_aliases_with_alias_generator=True),
        description=(
            "Write the generated alias on every field of a pydantic dataclass whose config names an alias "
            "generator, for pydantic versions older than 2.8."
        ),
    ),
    PresetOptionGroup(
        title="msgspec tags by payload name",
        config=PresetConfig(tag_msgspec_by_payload_name=True),
        description=(
            "Tag the structs of a discriminated union with the property name the document carries instead of the "
            "Python name it resolves to."
        ),
    ),
    PresetOptionGroup(
        title="Constructor fields ordered by final names and written defaults",
        config=PresetConfig(
            order_constructor_fields_by=(ConstructorFieldOrder.FinalNames, ConstructorFieldOrder.WrittenDefaults)
        ),
        description=(
            "Order the constructor fields of dataclass and msgspec models after every field has its final name, "
            "and the fields of msgspec and Pydantic dataclass models by the defaults they write."
        ),
    ),
    PresetOptionGroup(
        title="Request and response variants deduplicated",
        config=PresetConfig(dedupe_request_response_variants=True),
        description=("Decide Request and Response variants from the schema graph and build each inline model once."),
    ),
    PresetOptionGroup(
        title="Redundant reference null omitted",
        config=PresetConfig(omit_redundant_reference_null=True),
        description=("Do not write None again where a field references a model that holds null itself."),
    ),
    PresetOptionGroup(
        title="Decimal in field forms",
        config=PresetConfig(use_decimal_in_field_forms=True),
        description=(
            "Under --use-decimal-for-multiple-of, write Decimal for a number with multipleOf in the field forms too, "
            "and make it strict under --strict-types float."
        ),
    ),
    PresetOptionGroup(
        title="Shared parent wrappers collapsed",
        config=PresetConfig(collapse_shared_parent_wrappers=True),
        description=(
            "Under the parent name strategy, collapse a wrapper in every model that references it instead of "
            "keeping it for the models after the first."
        ),
    ),
)

_STANDARD_20261104_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    *_STANDARD_20261027_OPTION_GROUPS,
    PresetOptionGroup(
        title="Shared variant tags merged",
        config=PresetConfig(merge_shared_variant_tags=True),
        description=(
            "Give a variant that discriminated unions tag differently, such as one by a mapping key and one by the "
            "schema name, every tag they give it, so each union decodes the documents it selects the variant by. "
            "msgspec Structs take one tag, so they are unchanged."
        ),
    ),
    PresetOptionGroup(
        title="EncodedStr for constrained base64 strings",
        config=PresetConfig(use_encoded_str_for_constrained_base64=True),
        description=(
            "Write a format: byte string with a pattern or lengths in the field forms as an EncodedStr with the "
            "constraints in front of the decoding, so Pydantic 2.10 or later checks them on the base64 text."
        ),
    ),
    PresetOptionGroup(
        title="Length keywords read by type",
        config=PresetConfig(read_length_keywords_by_type=True),
        description=(
            "Read minLength, maxLength and pattern for strings only and minItems, maxItems and uniqueItems for "
            "arrays only, so a maxLength beside an array no longer limits how many items the list holds."
        ),
    ),
    PresetOptionGroup(
        title="msgspec union bounds read exactly",
        config=PresetConfig(read_msgspec_union_bounds_exactly=True),
        description=(
            "Decide whether the int member of a msgspec union with a number member drops its bounds on the bounds "
            "the schema writes, so a bound past 2**53 no longer rejects an integer the number member accepts."
        ),
    ),
    PresetOptionGroup(
        title="Model names taken from the schema",
        config=PresetConfig(
            naming_strategy=NamingStrategy.DefinitionsFirst,
            name_allof_union_members_after_branches=True,
            use_duplicate_name_suffix_for_every_collision=True,
            capitalise_numbered_enum_members=True,
            keep_first_character_of_path_keys=True,
            name_converted_payloads_by_message=True,
            singularize_last_word_of_item_names=True,
        ),
        description=(
            "Give every definition its own name before an inline model that asks for it first, name the union "
            "members of an allOf after their branches, give the "
            "--duplicate-name-suffix suffix to every model renamed because its name is taken, capitalize "
            "the number of a capitalized enum member, strip only a leading slash from a paths key, name a "
            "converted AsyncAPI payload that names no type after its message, and singularize only the last word "
            "of an item model's name."
        ),
    ),
    PresetOptionGroup(
        title="Schema version read per document",
        config=PresetConfig(read_schema_version_per_document=True),
        description=(
            "Read each document by the schema version it declares, or the version of the document that references "
            "it, so the keywords beside a $ref and the version's features apply as that version says in every "
            "document of a run, and an OpenAPI 3.1 or later document reads them as JSON Schema 2020-12 does."
        ),
    ),
    PresetOptionGroup(
        title="Ignored $ref siblings left out",
        config=PresetConfig(ignore_ref_siblings_by_schema_version=True),
        description=(
            "Read a $ref as its target alone where the schema version ignores the keywords beside it (JSON Schema "
            "draft 4, 6 and 7, OpenAPI 3.0, AsyncAPI 2), so a sibling format: binary or uniqueItems no longer changes "
            "the type of a field."
        ),
    ),
    PresetOptionGroup(
        title="Combined schema keywords intersected",
        config=PresetConfig(intersect_combined_schema_keywords=True),
        description=(
            "Combine the keywords beside an anyOf or oneOf with each branch, merge an inherited override's nested "
            "properties name by name and by its narrowed type, read the $ref siblings, $ref aliases and null branches "
            "of allOf members and the required names an allOf adds to the one model it names, and keep no enum value "
            "that the merged type rejects, as JSON Schema validates them."
        ),
    ),
    PresetOptionGroup(
        title="allOf distributed over unions",
        config=PresetConfig(distribute_allof_over_unions=True),
        description=(
            "Give an allOf one union variant per combination of the branches of the anyOf and oneOf it holds, also "
            "through a $ref member and for their value branches, read a $ref branch with the allOf members and the "
            "value keywords beside it, and an OpenAPI body with an allOf beside an anyOf or oneOf as that union, as "
            "JSON Schema validates them."
        ),
    ),
    PresetOptionGroup(
        title="Reference null kept on merged models",
        config=PresetConfig(keep_reference_null_on_merged_models=True),
        description=(
            "Keep the null that the schema of a reference admits when its model is merged into an equal model that "
            "rejects null, so a `type: [object, null]` property keeps `| None` after duplicate removal or reuse."
        ),
    ),
    PresetOptionGroup(
        title="Meta for msgspec map keys",
        config=PresetConfig(use_msgspec_meta_for_map_keys=True),
        description=(
            "Write the keys of a msgspec map whose keys propertyNames or patternProperties constrain as "
            "Annotated[str, Meta(...)], so msgspec checks them."
        ),
    ),
    PresetOptionGroup(
        title="Submodules hidden by classes renamed",
        config=PresetConfig(rename_submodules_hidden_by_classes=True),
        description=(
            "Rename a submodule named like a class of its package, such as pkg/Warn.py beside the class Warn of "
            "pkg/__init__.py, to the first free name with trailing underscores, so importing it leaves the class in "
            "place."
        ),
    ),
    PresetOptionGroup(
        title="Unique items as sets by value",
        config=PresetConfig(use_unique_items_as_set_by_value=True),
        description=(
            "Under --use-unique-items-as-set, decide each set on the final item types and write one only where every "
            "value of its items hashes by value, and keep apart models whose arrays differ in uniqueItems."
        ),
    ),
    PresetOptionGroup(
        title="Constrained root models kept in every container",
        config=PresetConfig(keep_constrained_root_models_in_every_container=True),
        description=(
            "Under --collapse-root-models with --field-constraints, keep a constrained root model named inside any "
            "container of its holder, so a set, Sequence or Mapping keeps the constraints of its items."
        ),
    ),
    PresetOptionGroup(
        title="Enum literal aliases kept",
        config=PresetConfig(keep_enum_literal_aliases=True),
        description=(
            "Under --collapse-root-models, keep a named enum that the output writes as a Literal type alias named at "
            "its uses."
        ),
    ),
    PresetOptionGroup(
        title="Plain aliases of recursive models",
        config=PresetConfig(use_plain_alias_for_recursive_models=True),
        description=(
            "In Pydantic output, write a type alias whose whole value is a model that names itself as a plain "
            "TypeAlias, which every model that uses it builds."
        ),
    ),
    PresetOptionGroup(
        title="Whole types replaced by type overrides",
        config=PresetConfig(type_overrides_replace_whole_type=True),
        description=(
            "Make a class path of --type-overrides replace an object map itself (dict, not dict[str, dict]), and "
            "keep the item or property counts of the array or map an override replaces only where the new type "
            "counts the same."
        ),
    ),
    PresetOptionGroup(
        title="SerializeAsAny for every subclass",
        config=PresetConfig(use_serialize_as_any_for_every_subclass=True),
        description=(
            "Under --use-serialize-as-any, wrap a field in SerializeAsAny when any subclass of its model in the "
            "output adds fields, at any depth, after reuse merges the model and through its module."
        ),
    ),
    PresetOptionGroup(
        title="Dynamic anchors read in document order",
        config=PresetConfig(read_dynamic_anchors_in_document_order=True),
        description=(
            "Resolve a plain-name $dynamicRef that its dynamic scope does not bind, and a reference to a plain name "
            "several schemas declare, to the first declaration in document order, so the answer no longer depends on "
            "the order the schemas are parsed in."
        ),
    ),
    PresetOptionGroup(
        title="One model per reference site",
        config=PresetConfig(use_one_model_per_ref_site=True),
        description=(
            "Give an object $ref with sibling keywords one model per place it is written, which every unchanged copy "
            "of that place shares, so a chain of N definitions gives N-1 site models instead of N(N-1)/2, parsed "
            "without nesting."
        ),
    ),
    PresetOptionGroup(
        title="Escaped reference pointers read",
        config=PresetConfig(read_escaped_ref_pointers=True),
        description=(
            "Read the ~0 and ~1 escapes and the percent-encoded octets of a $ref JSON pointer as the characters they "
            "encode, so every spelling of a pointer names the one model of its target instead of a copy named after "
            "the escaped text."
        ),
    ),
    PresetOptionGroup(
        title="Pydantic dataclass fields read by alias",
        config=PresetConfig(read_input_model_dataclass_fields_by_alias=True),
        description=(
            "With --input-model, read the fields of a Pydantic dataclass by their aliases, as those of a model, so "
            "a field with an alias keeps its Python type, such as deque[int], like one without."
        ),
    ),
)

_STANDARD_20261106_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (
    *_STANDARD_20261104_OPTION_GROUPS,
    PresetOptionGroup(
        title="Dataclass arguments set to false written",
        config=PresetConfig(keep_false_dataclass_arguments=True),
        description=(
            "Write every dataclass decorator argument set to false, such as eq=False from --dataclass-arguments, "
            "instead of leaving it out, which gives it its default."
        ),
    ),
    PresetOptionGroup(
        title="Content parameters typed by their reference",
        config=PresetConfig(keep_content_parameter_refs=True),
        description=(
            "Type an OpenAPI parameter whose content media schema is a $ref by the model the $ref names, as a schema "
            "parameter is, instead of a copy of the referenced schema."
        ),
    ),
    PresetOptionGroup(
        title="Extension keys of paths skipped",
        config=PresetConfig(skip_path_extension_keys=True),
        description=(
            "In the paths scope, skip a key of paths that begins with x-, a specification extension and no path "
            "item, as the api scope does, so its operations get no models."
        ),
    ),
    PresetOptionGroup(
        title="Every reached input model type reused",
        config=PresetConfig(reuse_every_reached_input_model_type=True),
        description=(
            "With --input-model-ref-strategy reuse-all or reuse-foreign, reach the types to reuse through the fields "
            "of every model kind, dataclasses too, and import a type the Python type of a field already imports "
            "instead of writing an unused copy of it."
        ),
    ),
    PresetOptionGroup(
        title="x-python-type as written",
        config=PresetConfig(use_x_python_type_as_written=True),
        description=(
            "Write a field's x-python-type as given: a union spelled Optional[...] or Union[...] types the field as "
            "one spelled X | Y already does, instead of the schema's type, and a container class compatible with the "
            "schema, such as tuple or MutableSequence, is written around the schema's item types instead of list, "
            "Sequence, set, frozenset or Mapping."
        ),
    ),
    PresetOptionGroup(
        title="Typed nullable map values",
        config=PresetConfig(use_typed_nullable_map_values=True),
        description=(
            "Type the values of a map whose type array lists object alone or with null, such as [object, null], as "
            "its additionalProperties say instead of Any, and with --use-typed-map-values-beside-other-types of the "
            "20261027 presets also those of a type array that lists object beside other types."
        ),
    ),
    PresetOptionGroup(
        title="Unknown fields forbidden by closed msgspec Structs",
        config=PresetConfig(use_msgspec_forbid_unknown_fields=True),
        description=(
            "Write forbid_unknown_fields=True on a msgspec Struct whose schema admits no property but its own "
            "(additionalProperties: false without patternProperties), so msgspec rejects the unknown fields the "
            "schema rejects."
        ),
    ),
    PresetOptionGroup(
        title="Reference null kept on collapsed root models",
        config=PresetConfig(keep_reference_null_on_collapsed_root_models=True),
        description=(
            "Under --collapse-root-models, keep the null of a root model that admits null where a field, item or "
            "map value that names it takes its type."
        ),
    ),
    PresetOptionGroup(
        title="allOf parents read in MRO order",
        config=PresetConfig(read_allof_parents_in_mro_order=True),
        description=(
            "Read the allOf parents of every inherited schema in the order of the class bases, so a property that "
            "several bases declare takes the nearer base's constraints and description, as the class does."
        ),
    ),
    PresetOptionGroup(
        title="Field options on every field",
        config=PresetConfig(apply_field_options_to_every_field=True),
        description=(
            "Give a GraphQL field, a property whose schema is true or false, a required name without a property and "
            "the tag field a discriminator appends the field options the other fields take, such as "
            "--use-default-kwarg, --use-annotated and --use-default-factory-for-optional-nested-models."
        ),
    ),
    PresetOptionGroup(
        title="Strict base64 strings",
        config=PresetConfig(use_strict_str_for_base64=True),
        description=(
            "Under --strict-types str, write a format: byte string as the StrictStr that EncodedStr decodes, so "
            "Pydantic 2.10 or later refuses bytes for it as for any other strict string, instead of Base64Str."
        ),
    ),
    PresetOptionGroup(
        title="Content media type strings as bytes",
        config=PresetConfig(use_bytes_for_content_media_type=True),
        description=(
            "Type a string of an OpenAPI document that has a contentMediaType and no contentEncoding, raw binary "
            "content, as bytes, as a format: binary string is, instead of str."
        ),
    ),
    PresetOptionGroup(
        title="Constrained string root models collapsed as Annotated",
        config=PresetConfig(collapse_constrained_root_models_as_annotated=True),
        description=(
            "Under --collapse-root-models with --use-annotated, inline a root model of a string with constraints "
            "into a union, list, dict or other container of its holder as Annotated[str, ...], which keeps the "
            "constraints on that member."
        ),
    ),
    PresetOptionGroup(
        title="Unused models removed beside type overrides",
        config=PresetConfig(remove_unused_models_beside_type_overrides=True),
        description=(
            "Remove the models nothing uses, such as an inlined root model, also from a module that a model-level "
            "--type-overrides key takes a model out of."
        ),
    ),
    PresetOptionGroup(
        title="Items of self-referencing arrays kept",
        config=PresetConfig(keep_self_referencing_array_items=True),
        description=(
            "Keep the items of an array whose items name the array itself (list[Node | Leaf] instead of list), "
            "except in msgspec output, which cannot decode such a type alias."
        ),
    ),
    PresetOptionGroup(
        title="Shared members kept on collapsed root models",
        config=PresetConfig(keep_shared_members_on_collapsed_root_models=True),
        description=(
            "Under --collapse-root-models, keep each member of a root type that collapse inlined at several sites at "
            "every site when a root model that member names is collapsed later, as a recursive union reaches its "
            "plain member."
        ),
    ),
    PresetOptionGroup(
        title="Operation enums written as Literal like definitions",
        config=PresetConfig(use_enum_field_as_literal_in_operations=True),
        description=(
            "Write the enum of an OpenAPI request body or response as a root model of a Literal where "
            "x-enum-field-as-literal, --enum-field-as-literal-map or --enum-field-as-literal writes a definition's "
            "enum so, instead of an Enum class."
        ),
    ),
    PresetOptionGroup(
        title="Request and response variants of numbered models deduplicated",
        config=PresetConfig(dedupe_numbered_request_response_variants=True),
        description=(
            "Under --read-only-write-only-model-type, merge a Request or Response variant of a numbered model, such "
            "as Page1Request of an inline copy Page1 of Page, into PageRequest where it renders the same, as the "
            "numbered model merges into Page."
        ),
    ),
    PresetOptionGroup(
        title="Defaults of inherited required fields kept",
        config=PresetConfig(keep_defaults_of_inherited_required_fields=True),
        description=(
            "With --use-default, keep the default a schema gives a property that only a schema it inherits from lists "
            "as required, as --use-default keeps that of a property the schema lists as required itself."
        ),
    ),
    PresetOptionGroup(
        title="Incomplete model defaults omitted",
        config=PresetConfig(omit_incomplete_model_defaults=True),
        description=(
            "Write a property whose object default lacks required fields of its model, such as default: {}, without "
            "that default, so input that leaves the property out is valid instead of failing on the default."
        ),
    ),
    PresetOptionGroup(
        title="AsyncAPI message trait headers merged",
        config=PresetConfig(merge_message_trait_headers=True),
        description=(
            "Give an AsyncAPI message the headers of all its traits, merged in order as JSON Merge Patches, inline "
            "and referenced traits alike, and then its own headers, which win."
        ),
    ),
)
"""The 20261104 groups and the opt-in options of the release after 0.90.0."""

_STANDARD_20261111_OPTION_GROUPS: tuple[PresetOptionGroup, ...] = (*_STANDARD_20261106_OPTION_GROUPS,)
"""The 20261106 groups and the opt-in options of the release after 0.93.0."""

_STANDARD_OPTION_GROUPS_BY_DATE: dict[str, tuple[PresetOptionGroup, ...]] = {
    "20260619": _STANDARD_20260619_OPTION_GROUPS,
    "20260826": _STANDARD_20260826_OPTION_GROUPS,
    "20260909": _STANDARD_20260909_OPTION_GROUPS,
    "20261015": _STANDARD_20260909_OPTION_GROUPS,
    "20261017": _STANDARD_20261017_OPTION_GROUPS,
    "20261027": _STANDARD_20261027_OPTION_GROUPS,
    "20261104": _STANDARD_20261104_OPTION_GROUPS,
    "20261106": _STANDARD_20261106_OPTION_GROUPS,
    "20261111": _STANDARD_20261111_OPTION_GROUPS,
}


def _group_preset_names() -> dict[tuple[str, str], dict[PythonVersion, PresetName]]:
    """Group the preset names by family and date, each in the order of its Python targets."""
    names_by_target: dict[tuple[str, str], dict[PythonVersion, PresetName]] = {}
    for name in PresetName:
        family, python, date = name.value.split("-")
        names_by_target.setdefault((family, date), {})[PythonVersion(f"{python[2]}.{python[3:]}")] = name
    return names_by_target


_PRESET_NAMES_BY_TARGET = _group_preset_names()
_LATEST_PRESET_DATE = max(_STANDARD_OPTION_GROUPS_BY_DATE)
_STANDARD_PRESET_NAMES_BY_TARGET = _PRESET_NAMES_BY_TARGET["standard", _LATEST_PRESET_DATE]
_PRACTICAL_PRESET_NAMES_BY_TARGET = _PRESET_NAMES_BY_TARGET["practical", _LATEST_PRESET_DATE]


def _standard_option_groups_for_target(
    option_groups: tuple[PresetOptionGroup, ...],
    target_python_version: PythonVersion,
) -> tuple[PresetOptionGroup, ...]:
    return tuple(group for group in option_groups if group.applies_to_python(target_python_version))


def _build_preset_family_infos(date: str) -> tuple[PresetInfo, ...]:
    standard_option_groups = _STANDARD_OPTION_GROUPS_BY_DATE[date]
    standard_names_by_target = _PRESET_NAMES_BY_TARGET["standard", date]
    standard_infos = tuple(
        PresetInfo(
            name=name,
            summary=f"Recommended modern Python {target_python_version.value} output for new projects.",
            description=(
                "This immutable preset enables the project-recommended Python output style for new code targeting "
                f"Python {target_python_version.value}. It is output-model aware and keeps stdlib dataclass and "
                "TypedDict keys compatible with their input names."
            ),
            requires_target_python_version=False,
            target_python_version=target_python_version,
            option_groups=_standard_option_groups_for_target(standard_option_groups, target_python_version),
        )
        for target_python_version, name in standard_names_by_target.items()
    )
    practical_infos = tuple(
        PresetInfo(
            name=name,
            summary=(
                f"Standard Python {target_python_version.value} output plus practical naming, deduplication, "
                "and schema documentation."
            ),
            description=(
                f"This immutable preset extends `{standard_names_by_target[target_python_version].value}` "
                "with options that make generated models easier to read and use in real projects. It favors "
                "schema-authored names, model reuse, and embedded schema documentation over the most conservative "
                "output-shape stability."
            ),
            requires_target_python_version=False,
            target_python_version=target_python_version,
            option_groups=(
                *_standard_option_groups_for_target(standard_option_groups, target_python_version),
                *_PRACTICAL_EXTRA_OPTION_GROUPS_BY_DATE.get(date, _PRACTICAL_20260619_EXTRA_OPTION_GROUPS),
            ),
        )
        for target_python_version, name in _PRESET_NAMES_BY_TARGET["practical", date].items()
    )
    return (*standard_infos, *practical_infos)


_PRESET_INFOS: tuple[PresetInfo, ...] = tuple(
    info
    for family, date in _PRESET_NAMES_BY_TARGET
    if family == "standard"
    for info in _build_preset_family_infos(date)
)

_COPYABLE_INPUT_FILE_TYPE = InputFileType.JsonSchema
_COPYABLE_OUTPUT_MODEL_TYPE = DataModelType.PydanticV2BaseModel


def _validate_preset_infos(infos: tuple[PresetInfo, ...]) -> None:
    """Validate preset metadata invariants at import time."""
    preset_names: set[PresetName] = set()
    for info in infos:
        if info.name in preset_names:  # pragma: no cover
            msg = f"Preset {stable_repr(info.name.value)} is defined more than once"
            raise PresetError(msg)
        preset_names.add(info.name)
        if info.target_python_version.value.replace(".", "") not in info.name.value:  # pragma: no cover
            msg = f"Preset {stable_repr(info.name.value)} does not include its Python target in the name"
            raise PresetError(msg)

        output_model_groups: dict[DataModelType, str] = {}
        for group in info.option_groups:
            if not group.config:  # pragma: no cover
                msg = f"Preset option group {stable_repr(group.title)} does not define any config fields"
                raise PresetError(msg)
            for output_model_type in group.output_model_types:
                if output_model_type in output_model_groups:  # pragma: no cover
                    msg = (
                        f"Preset {stable_repr(info.name.value)} maps {stable_repr(output_model_type.value)} to both "
                        f"{stable_repr(output_model_groups[output_model_type])} and {stable_repr(group.title)}"
                    )
                    raise PresetError(msg)
                output_model_groups[output_model_type] = group.title


_validate_preset_infos(_PRESET_INFOS)


def get_preset_names() -> tuple[str, ...]:
    """Return all public preset names."""
    return tuple(preset.value for preset in PresetName)


def get_latest_preset_name() -> str:
    """Return the latest public preset name."""
    return max(get_preset_names(), key=_preset_name_sort_key)


def latest_preset_name(family: str, target_python_version: PythonVersion) -> str:
    """Return the newest dated preset of a family, standard or practical, for a target Python version."""
    names = {"standard": _STANDARD_PRESET_NAMES_BY_TARGET, "practical": _PRACTICAL_PRESET_NAMES_BY_TARGET}[family]
    return names[target_python_version].value


def get_preset_infos() -> tuple[PresetInfo, ...]:
    """Return public preset metadata."""
    return _PRESET_INFOS


def _preset_name_sort_key(name: str) -> tuple[str, str]:
    prefix, separator, version = name.rpartition("-")
    if separator and version.isdecimal():
        return prefix, version
    return name, ""  # pragma: no cover


def preset_info_as_dict(info: PresetInfo) -> _PresetInfoDict:
    """Serialize a preset info entry to primitive values."""
    option_groups = [
        _PresetOptionGroupDict(
            title=group.title,
            options=list(group.options),
            description=group.description,
        )
        for group in info.option_groups
    ]
    return _PresetInfoDict(
        name=info.name.value,
        summary=info.summary,
        description=info.description,
        requires_target_python_version=info.requires_target_python_version,
        target_python_version=info.target_python_version.value,
        option_groups=option_groups,
        copyable_config=_render_copyable_config(info),
    )


def render_presets_json() -> str:
    """Render preset metadata as JSON."""
    return _render_registry_json(preset_info_as_dict(info) for info in _PRESET_INFOS)


def _date_additions_markdown(since: str) -> list[str]:
    """Return the lines that say, date by date after ``since``, what the presets of a date add to the date before.

    A date adds its new Python targets and the option groups its standard or practical presets list that the date before
    does not, each written as the reference tables write it: its title, its options and its description.
    """
    dates = list(_STANDARD_OPTION_GROUPS_BY_DATE)
    dates = dates[dates.index(since) :]
    lines: list[str] = []
    for previous, date in _pairwise(dates):
        targets = [
            target.value
            for target in _PRESET_NAMES_BY_TARGET["standard", date]
            if target not in _PRESET_NAMES_BY_TARGET["standard", previous]
        ]
        added = [
            *(f"- Python {target} targets" for target in targets),
            *(
                _group_item(group, family)
                for family, groups_by_date in (
                    ("", _STANDARD_OPTION_GROUPS_BY_DATE),
                    (" (practical presets)", _PRACTICAL_EXTRA_OPTION_GROUPS_BY_DATE),
                )
                for group in groups_by_date.get(date, _PRACTICAL_20260619_EXTRA_OPTION_GROUPS)
                if group not in groups_by_date.get(previous, _PRACTICAL_20260619_EXTRA_OPTION_GROUPS)
            ),
        ]
        heading = f"The {date} presets keep the {previous} options{' and add:' if added else '.'}"
        lines.extend((heading, "", *added, *([""] if added else [])))
    return lines


def _group_item(group: PresetOptionGroup, family: str) -> str:
    """Return the list item of an option group a preset date adds: its title, options and description."""
    options = ", ".join(f"`{option}`" for option in group.options)
    return f"- {group.title}{family} ({options}): {group.description}"


def render_presets_markdown() -> str:
    """Render the preset reference page."""
    standard, practical = (latest_preset_name(family, PythonVersion.PY_312) for family in ("standard", "practical"))
    builtin_date = "20260909"
    lines = [
        "# Presets",
        "",
        "<!-- Generated by scripts/build_preset_docs.py. Do not edit manually. -->",
        "",
        (
            "Presets are immutable named groups of CLI options. A preset name never changes behavior; "
            "new recommendations are published as new dated preset names."
        ),
        "",
        (
            "Every preset name includes its Python target, so generated Python syntax is pinned without an extra "
            "`--target-python-version` argument. Choose the preset name instead of adding a separate target option "
            "to normal preset commands; if an explicit target is already present in CLI or pyproject configuration, "
            "it must match the preset name."
        ),
        "",
        (
            "See also: "
            "[CLI Reference](cli-reference/index.md), "
            "[`--preset`](cli-reference/base-options.md#preset), "
            "[`--target-python-version`](cli-reference/model-customization.md#target-python-version), "
            "[`--output-model-type`](cli-reference/model-customization.md#output-model-type), "
            "[`--profile`](cli-reference/utility-options.md#profile), "
            "[`--generate-pyproject-config`](cli-reference/general-options.md#generate-pyproject-config), "
            "[`--generate-cli-command`](cli-reference/general-options.md#generate-cli-command), "
            "[pyproject.toml configuration](pyproject_toml.md), and "
            "[CI/CD integration](ci-cd.md)."
        ),
        "",
        "## Usage",
        "",
        "Use a preset by passing its immutable name:",
        "",
        "```bash",
        "datamodel-codegen \\",
        "  --input schema.json \\",
        "  --input-file-type jsonschema \\",
        "  --output-model-type pydantic_v2.BaseModel \\",
        f"  --preset {standard} \\",
        "  --output model.py",
        "```",
        "",
        (
            f"Use `{standard}` for the project-recommended modern Python 3.12 baseline. "
            f"Use `{practical}` when you also want schema-authored names, model reuse, and schema "
            "descriptions embedded in the generated code."
        ),
        "",
        (
            "To choose options on top of a preset for your schema, add `--recommend-options` to the command "
            "(experimental); see [Option recommendations](recommend-options.md)."
        ),
        "",
        (
            f"Presets dated {builtin_date} or later select builtin to avoid running external formatters. "
            "For project-wide Ruff consistency, use the preset with `--formatters ruff-check ruff-format`. "
            "Keep `--formatters black isort` to preserve existing Black/isort output. "
            "Earlier dated presets are unchanged. Black and isort remain required installation dependencies."
        ),
        "",
        *_date_additions_markdown(builtin_date),
        "## Override Preset Options",
        "",
        "Preset options are defaults, not locks. Explicit options override preset-supplied values.",
        "",
        "```bash",
        "datamodel-codegen \\",
        "  --input schema.json \\",
        f"  --preset {standard} \\",
        "  --no-snake-case-field \\",
        "  --no-use-annotated \\",
        "  --enum-field-as-literal none",
        "```",
        "",
        (
            "Use the `--no-*` form of a boolean option to turn it off; every boolean option a preset turns on has one, "
            "such as `--no-read-length-keywords-by-type`, and `false` beside the preset in pyproject.toml, such as "
            "`read-length-keywords-by-type = false`, turns it off the same way. "
            "For value options, pass the replacement value explicitly, such as `--enum-field-as-literal none`. "
            "A list option replaces the whole list a preset sets, such as `--openapi-as-definitions paths-bodies`; "
            "in pyproject.toml an empty list, such as `openapi-as-definitions = []`, turns off every value the preset "
            "sets."
        ),
        "",
        "## Add Options On Top",
        "",
        (
            "You can add any normal CLI option to a preset command. "
            "This is the recommended way to start from a preset and make a project-specific choice:"
        ),
        "",
        "```bash",
        "datamodel-codegen \\",
        "  --input schema.json \\",
        f"  --preset {standard} \\",
        "  --extra-fields forbid \\",
        "  --use-title-as-name \\",
        "  --output model.py",
        "```",
        "",
        "## Use Presets With Profiles",
        "",
        (
            "Presets can live in `[tool.datamodel-codegen]` or in a named profile. "
            "Profiles are useful when one repository generates several model sets:"
        ),
        "",
        '```toml title="pyproject.toml"',
        "[tool.datamodel-codegen]",
        'output-model-type = "pydantic_v2.BaseModel"',
        f'preset = "{standard}"',
        "",
        "[tool.datamodel-codegen.profiles.api]",
        'input = "schemas/api.json"',
        'output = "src/models/api.py"',
        f'preset = "{practical}"',
        'extra-fields = "forbid"',
        "",
        "[tool.datamodel-codegen.profiles.events]",
        'input = "schemas/events.json"',
        'output = "src/models/events.py"',
        "use-title-as-name = true",
        "```",
        "",
        "```bash",
        "datamodel-codegen --profile api",
        "datamodel-codegen --profile events",
        "```",
        "",
        (
            "If `--preset` is passed on the CLI, it overrides pyproject preset settings and pyproject options unless "
            "the same option is also explicit on the CLI. If `preset` is configured in `pyproject.toml`, "
            "pyproject/profile options and CLI options override preset-supplied values."
        ),
        "",
        "## Export Configuration",
        "",
        "To convert a working preset command into `pyproject.toml`, use `--generate-pyproject-config`:",
        "",
        "```bash",
        "datamodel-codegen \\",
        "  --input schema.json \\",
        "  --output model.py \\",
        "  --output-model-type pydantic_v2.BaseModel \\",
        f"  --preset {practical} \\",
        "  --extra-fields forbid \\",
        "  --generate-pyproject-config",
        "```",
        "",
        "To inspect the effective CLI command for an existing pyproject profile, use `--generate-cli-command`:",
        "",
        "```bash",
        "datamodel-codegen --profile api --generate-cli-command",
        "```",
        "",
        "Use `--ignore-pyproject` when you want to test a preset command without loading project configuration.",
        "",
        "## Built-in Presets",
        "",
        "### Target Matrix",
        "",
        "| Python target | Standard preset | Practical preset |",
        "|---------------|-----------------|------------------|",
    ]
    lines.extend(
        (
            "| "
            f"{target_python_version.value} | "
            f"`{standard_name.value}` | "
            f"`{_PRACTICAL_PRESET_NAMES_BY_TARGET[target_python_version].value}` |"
        )
        for target_python_version, standard_name in _STANDARD_PRESET_NAMES_BY_TARGET.items()
    )
    lines.extend((
        "",
        (
            "Each preset name includes its Python target. Do not add `--target-python-version` just to use a preset; "
            "only expanded configuration without `--preset` needs a separate target option."
        ),
        "",
        "### Preset Reference",
        "",
    ))
    for info in _PRESET_INFOS:
        target_required = "yes" if info.requires_target_python_version else "no"
        lines.extend((
            f"### `{info.name.value}`",
            "",
            info.summary,
            "",
            info.description,
            "",
            f"- **Requires separate target Python version:** {target_required}",
            f"- **Target Python version:** {info.target_python_version.value}",
            "",
            "#### Included Options",
            "",
            (
                "These snippets show the equivalent expanded configuration without `--preset` for JSON Schema input, "
                f"Pydantic v2 BaseModel output, and Python {info.target_python_version.value}. Replace the input and "
                "output paths for your project."
            ),
            "",
        ))
        lines.extend(_render_copyable_config_markdown(_render_copyable_config(info)))
        lines.extend((
            "| Scope | Options | Notes |",
            "|-------|---------|-------|",
        ))
        for group in info.option_groups:
            options = ", ".join(f"`{option}`" for option in group.options)
            notes = group.description.replace("|", r"\|")
            lines.append(f"| {group.title} | {options} | {notes} |")
        lines.append("")
    return "\n".join(lines)


def _render_copyable_config(info: PresetInfo) -> _PresetCopyableConfigDict:
    """Render a copyable expanded config through the existing CLI/config exporters."""
    from datamodel_code_generator.__main__ import generate_cli_command, generate_pyproject_config  # noqa: PLC0415

    config_data = _copyable_config_data(info)
    return _PresetCopyableConfigDict(
        context=_PresetCopyableConfigContextDict(
            input_file_type=_COPYABLE_INPUT_FILE_TYPE.value,
            output_model_type=_COPYABLE_OUTPUT_MODEL_TYPE.value,
            target_python_version=info.target_python_version.value,
        ),
        pyproject_toml=generate_pyproject_config(Namespace(**config_data)).rstrip(),
        cli=generate_cli_command(config_data).rstrip(),
    )


def _copyable_config_data(info: PresetInfo) -> dict[str, TomlValue]:
    context = PresetContext(
        input_file_type=_COPYABLE_INPUT_FILE_TYPE,
        output_model_type=_COPYABLE_OUTPUT_MODEL_TYPE,
        target_python_version=info.target_python_version,
    )
    config_data: dict[str, TomlValue] = {
        "input": "schema.json",
        "input_file_type": _COPYABLE_INPUT_FILE_TYPE.value,
        "output": "model.py",
        "output_model_type": _COPYABLE_OUTPUT_MODEL_TYPE.value,
        "target_python_version": info.target_python_version.value,
    }
    config_data.update(cast("dict[str, TomlValue]", _pyproject_values(_resolve_preset_info(info, context))))
    return dict(sorted(config_data.items()))


def _render_copyable_config_markdown(copyable_config: _PresetCopyableConfigDict) -> list[str]:
    return [
        '=== "pyproject.toml"',
        "",
        "    ```toml",
        *_indent_markdown_code(copyable_config["pyproject_toml"]),
        "    ```",
        "",
        '=== "CLI"',
        "",
        "    ```bash",
        *_indent_markdown_code(copyable_config["cli"]),
        "    ```",
        "",
    ]


def _indent_markdown_code(text: str) -> list[str]:
    return [f"    {line}" for line in text.splitlines()]


def render_presets(format_: PresetFormat) -> str:
    """Render presets in the requested format."""
    match format_:
        case "json":
            return render_presets_json() + "\n"
        case "markdown":
            return render_presets_markdown().rstrip() + "\n"
        case _:  # pragma: no cover
            msg = f"Unsupported preset docs format: {stable_repr(format_)}"
            raise PresetError(msg)


def resolve_preset_config_updates(
    preset: PresetName | str,
    *,
    context: PresetContext,
    use_annotated: bool,
    explicit_fields: set[str],
) -> ResolvedPresetConfig:
    """Resolve preset updates while preserving caller-explicit config fields."""
    preset_name = _coerce_preset_name(preset)
    info = _get_preset_info(preset_name)
    explicit_field_names = set(explicit_fields)
    explicit_field_names.discard("preset")

    target_python_version_update = None
    effective_target_python_version = context.target_python_version
    if "target_python_version" not in explicit_field_names:
        target_python_version_update = info.target_python_version
        effective_target_python_version = info.target_python_version

    effective_context = PresetContext(
        input_file_type=context.input_file_type,
        output_model_type=context.output_model_type,
        target_python_version=effective_target_python_version,
    )
    items = tuple(
        item
        for item in _resolve_preset_info_for_context(info, effective_context)
        if item.field_name not in explicit_field_names
    )
    return ResolvedPresetConfig(
        target_python_version=target_python_version_update,
        items=items,
        force_field_constraints=_resolve_force_field_constraints(use_annotated=use_annotated, items=items),
    )


def _resolve_config_preset(
    preset: PresetName | str, config: BaseGenerateConfig, explicit_fields: AbstractSet[str]
) -> ResolvedPresetConfig:
    """Resolve a preset for the input type, model type and target Python version of a config."""
    return resolve_preset_config_updates(
        preset,
        context=PresetContext(
            input_file_type=config.input_file_type,
            output_model_type=config.output_model_type,
            target_python_version=config.target_python_version,
        ),
        use_annotated=config.use_annotated,
        explicit_fields=set(explicit_fields),
    )


def _resolve_force_field_constraints(*, use_annotated: bool, items: tuple[PresetConfigItem, ...]) -> bool:
    for item in items:
        if item.field_name != "use_annotated":
            continue
        value = item.applied_value
        if isinstance(value, bool):  # pragma: no branch
            return value
    return use_annotated


def _resolve_preset_info_for_context(info: PresetInfo, context: PresetContext) -> tuple[PresetConfigItem, ...]:
    if info.target_python_version is not context.target_python_version:
        msg = (
            f"--preset {info.name.value} targets Python {info.target_python_version.value}; "
            f"current --target-python-version is {context.target_python_version.value}."
        )
        raise PresetError(msg)
    return _resolve_preset_info(info, context)


def _coerce_preset_name(preset: PresetName | str) -> PresetName:
    """Return a typed preset name or raise a user-facing preset error."""
    try:
        return PresetName(preset)
    except ValueError as exc:  # pragma: no cover
        names = ", ".join(get_preset_names())
        msg = f"Unknown preset: {stable_repr(preset)}. Available presets: {names}"
        raise PresetError(msg) from exc


def _get_preset_info(preset_name: PresetName) -> PresetInfo:
    """Return preset metadata for a known preset name."""
    for info in _PRESET_INFOS:
        if info.name is preset_name:  # pragma: no branch
            return info

    msg = f"Unsupported preset: {preset_name.value}"  # pragma: no cover
    raise PresetError(msg)  # pragma: no cover


def _resolve_preset_info(info: PresetInfo, context: PresetContext) -> tuple[PresetConfigItem, ...]:
    """Resolve preset metadata into config updates for the given context."""
    applicable_groups = tuple(group for group in info.option_groups if group.applies_to(context))
    if not any(group.output_model_types for group in applicable_groups):
        msg = (  # pragma: no cover
            f"Unsupported output model type for preset {info.name.value}: {context.output_model_type.value}"
        )
        raise PresetError(msg)  # pragma: no cover

    return _merge_preset_groups(applicable_groups)
