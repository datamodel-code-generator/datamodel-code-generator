"""Built-in immutable option presets for the CLI."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from pydantic import ConfigDict, PrivateAttr
from typing_extensions import TypedDict

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    Formatter,
    PythonVersion,
)
from datamodel_code_generator._registry_render import _render_registry_json
from datamodel_code_generator._shared_types import LiteralType
from datamodel_code_generator.base_config import BaseGenerateConfig
from datamodel_code_generator.cli_options import CLI_OPTION_META
from datamodel_code_generator.enums import (
    AllOfClassHierarchy,
    AllOfMergeMode,
    CollapseRootModelsNameStrategy,
    DataModelType,
    DefaultValueType,
    ExtraFields,
    FieldTypeCollisionStrategy,
    InputFileType,
    NamingStrategy,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    TargetPydanticVersion,
    UnionMode,
    VersionMode,
)

if TYPE_CHECKING:
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
        if isinstance(self.value, Enum):
            return str(self.value.value)
        if isinstance(self.value, tuple):
            return [str(value.value) for value in self.value]
        return self.value


@dataclass(frozen=True, slots=True)
class ResolvedPresetConfig:
    """Typed config updates resolved for a concrete generation context."""

    target_python_version: PythonVersion | None
    items: tuple[PresetConfigItem, ...]
    force_field_constraints: bool


class PresetConfig(BaseGenerateConfig):
    """Typed immutable config updates supplied by a preset group."""

    model_config = ConfigDict(frozen=True)

    _items: tuple[PresetConfigItem, ...] = PrivateAttr(default=())

    def __init__(self, **values: Unpack[BaseGenerateConfigDict]) -> None:
        """Build immutable preset updates from statically checked GenerateConfig fields."""
        for field_name in values:
            if field_name not in type(self).model_fields:  # pragma: no cover
                msg = f"Preset field {field_name!r} is not a BaseGenerateConfig field"
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
        return not self.requires_python_strenum or context.target_python_version.has_strenum


def _merge_preset_configs(*configs: PresetConfig) -> tuple[PresetConfigItem, ...]:
    """Merge preset configs, rejecting conflicting explicit updates."""
    items: dict[str, PresetConfigItem] = {}
    for config in configs:
        for item in config.items():
            existing = items.get(item.field_name)
            if existing is None:
                items[item.field_name] = item
                continue
            if existing.value != item.value:  # pragma: no cover
                msg = f"Preset field {item.field_name!r} is configured with both {existing.value!r} and {item.value!r}"
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
                msg = f"Unsupported extra_fields preset value: {value!r}"
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
    match value:
        case (
            LiteralType()
            | NamingStrategy()
            | TargetPydanticVersion()
            | ReuseScope()
            | AllOfMergeMode()
            | AllOfClassHierarchy()
            | CollapseRootModelsNameStrategy()
            | UnionMode()
            | ReadOnlyWriteOnlyModelType()
            | FieldTypeCollisionStrategy()
            | VersionMode()
            | DateClassType()
            | DatetimeClassType()
            | DefaultValueType()
            | Formatter()
        ):
            return value
    msg = f"Preset field {field_name!r} cannot be rendered as a preset CLI option"  # pragma: no cover
    raise PresetError(msg)  # pragma: no cover


def _config_item_to_cli_option(item: PresetConfigItem) -> str:
    """Translate a typed config field name to its CLI option spelling."""
    option_name = item.field_name.replace("_", "-")
    option = f"--{option_name}"
    if option not in CLI_OPTION_META:  # pragma: no cover
        msg = f"Preset field {item.field_name!r} does not map to a documented CLI option"
        raise PresetError(msg)
    if item.value is True:
        return option
    if isinstance(item.value, Enum):
        return f"{option} {item.value.value}"
    if item.value:
        return f"{option} {' '.join(str(value.value) for value in item.value)}"

    negative_option = f"--no-{option_name}"
    if negative_option not in CLI_OPTION_META:  # pragma: no cover
        msg = f"Preset field {item.field_name!r} does not map to a documented negative CLI option"
        raise PresetError(msg)
    return negative_option


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
        output_model_types=frozenset({
            DataModelType.PydanticV2BaseModel,
            DataModelType.PydanticV2Dataclass,
        }),
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

_STANDARD_20260619_PRESET_NAMES_BY_TARGET: dict[PythonVersion, PresetName] = {
    PythonVersion.PY_310: PresetName.StandardPy31020260619,
    PythonVersion.PY_311: PresetName.StandardPy31120260619,
    PythonVersion.PY_312: PresetName.StandardPy31220260619,
    PythonVersion.PY_313: PresetName.StandardPy31320260619,
    PythonVersion.PY_314: PresetName.StandardPy31420260619,
}
_PRACTICAL_20260619_PRESET_NAMES_BY_TARGET: dict[PythonVersion, PresetName] = {
    PythonVersion.PY_310: PresetName.PracticalPy31020260619,
    PythonVersion.PY_311: PresetName.PracticalPy31120260619,
    PythonVersion.PY_312: PresetName.PracticalPy31220260619,
    PythonVersion.PY_313: PresetName.PracticalPy31320260619,
    PythonVersion.PY_314: PresetName.PracticalPy31420260619,
}

_STANDARD_20260826_PRESET_NAMES_BY_TARGET: dict[PythonVersion, PresetName] = {
    PythonVersion.PY_310: PresetName.StandardPy31020260826,
    PythonVersion.PY_311: PresetName.StandardPy31120260826,
    PythonVersion.PY_312: PresetName.StandardPy31220260826,
    PythonVersion.PY_313: PresetName.StandardPy31320260826,
    PythonVersion.PY_314: PresetName.StandardPy31420260826,
}
_PRACTICAL_20260826_PRESET_NAMES_BY_TARGET: dict[PythonVersion, PresetName] = {
    PythonVersion.PY_310: PresetName.PracticalPy31020260826,
    PythonVersion.PY_311: PresetName.PracticalPy31120260826,
    PythonVersion.PY_312: PresetName.PracticalPy31220260826,
    PythonVersion.PY_313: PresetName.PracticalPy31320260826,
    PythonVersion.PY_314: PresetName.PracticalPy31420260826,
}

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

_STANDARD_20260909_PRESET_NAMES_BY_TARGET: dict[PythonVersion, PresetName] = {
    PythonVersion.PY_310: PresetName.StandardPy31020260909,
    PythonVersion.PY_311: PresetName.StandardPy31120260909,
    PythonVersion.PY_312: PresetName.StandardPy31220260909,
    PythonVersion.PY_313: PresetName.StandardPy31320260909,
    PythonVersion.PY_314: PresetName.StandardPy31420260909,
}
_PRACTICAL_20260909_PRESET_NAMES_BY_TARGET: dict[PythonVersion, PresetName] = {
    PythonVersion.PY_310: PresetName.PracticalPy31020260909,
    PythonVersion.PY_311: PresetName.PracticalPy31120260909,
    PythonVersion.PY_312: PresetName.PracticalPy31220260909,
    PythonVersion.PY_313: PresetName.PracticalPy31320260909,
    PythonVersion.PY_314: PresetName.PracticalPy31420260909,
}

_STANDARD_PRESET_NAMES_BY_TARGET = _STANDARD_20260909_PRESET_NAMES_BY_TARGET
_PRACTICAL_PRESET_NAMES_BY_TARGET = _PRACTICAL_20260909_PRESET_NAMES_BY_TARGET


def _standard_option_groups_for_target(
    option_groups: tuple[PresetOptionGroup, ...],
    target_python_version: PythonVersion,
) -> tuple[PresetOptionGroup, ...]:
    return tuple(
        group for group in option_groups if not group.requires_python_strenum or target_python_version.has_strenum
    )


def _practical_option_groups_for_target(
    standard_option_groups: tuple[PresetOptionGroup, ...],
    practical_extra_option_groups: tuple[PresetOptionGroup, ...],
    target_python_version: PythonVersion,
) -> tuple[PresetOptionGroup, ...]:
    return (
        *_standard_option_groups_for_target(standard_option_groups, target_python_version),
        *practical_extra_option_groups,
    )


def _build_preset_info(
    *,
    name: PresetName,
    target_python_version: PythonVersion,
    summary: str,
    description: str,
    option_groups: tuple[PresetOptionGroup, ...],
) -> PresetInfo:
    return PresetInfo(
        name=name,
        summary=summary,
        description=description,
        requires_target_python_version=False,
        target_python_version=target_python_version,
        option_groups=option_groups,
    )


def _build_preset_family_infos(
    *,
    standard_names_by_target: dict[PythonVersion, PresetName],
    practical_names_by_target: dict[PythonVersion, PresetName],
    standard_option_groups: tuple[PresetOptionGroup, ...],
    practical_extra_option_groups: tuple[PresetOptionGroup, ...],
) -> tuple[PresetInfo, ...]:
    standard_infos = tuple(
        _build_preset_info(
            name=name,
            target_python_version=target_python_version,
            summary=f"Recommended modern Python {target_python_version.value} output for new projects.",
            description=(
                "This immutable preset enables the project-recommended Python output style for new code targeting "
                f"Python {target_python_version.value}. It is output-model aware and keeps stdlib dataclass and "
                "TypedDict keys compatible with their input names."
            ),
            option_groups=_standard_option_groups_for_target(standard_option_groups, target_python_version),
        )
        for target_python_version, name in standard_names_by_target.items()
    )
    practical_infos = tuple(
        _build_preset_info(
            name=name,
            target_python_version=target_python_version,
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
            option_groups=_practical_option_groups_for_target(
                standard_option_groups,
                practical_extra_option_groups,
                target_python_version,
            ),
        )
        for target_python_version, name in practical_names_by_target.items()
    )
    return (*standard_infos, *practical_infos)


_PRESET_INFOS: tuple[PresetInfo, ...] = (
    *_build_preset_family_infos(
        standard_names_by_target=_STANDARD_20260619_PRESET_NAMES_BY_TARGET,
        practical_names_by_target=_PRACTICAL_20260619_PRESET_NAMES_BY_TARGET,
        standard_option_groups=_STANDARD_20260619_OPTION_GROUPS,
        practical_extra_option_groups=_PRACTICAL_20260619_EXTRA_OPTION_GROUPS,
    ),
    *_build_preset_family_infos(
        standard_names_by_target=_STANDARD_20260826_PRESET_NAMES_BY_TARGET,
        practical_names_by_target=_PRACTICAL_20260826_PRESET_NAMES_BY_TARGET,
        standard_option_groups=_STANDARD_20260826_OPTION_GROUPS,
        practical_extra_option_groups=_PRACTICAL_20260619_EXTRA_OPTION_GROUPS,
    ),
    *_build_preset_family_infos(
        standard_names_by_target=_STANDARD_20260909_PRESET_NAMES_BY_TARGET,
        practical_names_by_target=_PRACTICAL_20260909_PRESET_NAMES_BY_TARGET,
        standard_option_groups=_STANDARD_20260909_OPTION_GROUPS,
        practical_extra_option_groups=_PRACTICAL_20260619_EXTRA_OPTION_GROUPS,
    ),
)

_COPYABLE_INPUT_FILE_TYPE = InputFileType.JsonSchema
_COPYABLE_OUTPUT_MODEL_TYPE = DataModelType.PydanticV2BaseModel


def _validate_preset_infos(infos: tuple[PresetInfo, ...]) -> None:
    """Validate preset metadata invariants at import time."""
    preset_names: set[PresetName] = set()
    for info in infos:
        if info.name in preset_names:  # pragma: no cover
            msg = f"Preset {info.name.value!r} is defined more than once"
            raise PresetError(msg)
        preset_names.add(info.name)
        if info.target_python_version.value.replace(".", "") not in info.name.value:  # pragma: no cover
            msg = f"Preset {info.name.value!r} does not include its Python target in the name"
            raise PresetError(msg)

        output_model_groups: dict[DataModelType, str] = {}
        for group in info.option_groups:
            if not group.config:  # pragma: no cover
                msg = f"Preset option group {group.title!r} does not define any config fields"
                raise PresetError(msg)
            for output_model_type in group.output_model_types:
                if output_model_type in output_model_groups:  # pragma: no cover
                    msg = (
                        f"Preset {info.name.value!r} maps {output_model_type.value!r} to both "
                        f"{output_model_groups[output_model_type]!r} and {group.title!r}"
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


def render_presets_markdown() -> str:
    """Render the preset reference page."""
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
        "  --preset standard-py312-20260909 \\",
        "  --output model.py",
        "```",
        "",
        (
            "Use `standard-py312-20260909` for the project-recommended modern Python 3.12 baseline. "
            "Use `practical-py312-20260909` when you also want schema-authored names, model reuse, and schema "
            "descriptions embedded in the generated code."
        ),
        "",
        (
            "The 20260909 presets select builtin to avoid running external formatters. "
            "For project-wide Ruff consistency, use the preset with `--formatters ruff-check ruff-format`. "
            "Keep `--formatters black isort` to preserve existing Black/isort output. "
            "Earlier dated presets are unchanged. Black and isort remain required installation dependencies."
        ),
        "",
        "## Override Preset Options",
        "",
        "Preset options are defaults, not locks. Explicit options override preset-supplied values.",
        "",
        "```bash",
        "datamodel-codegen \\",
        "  --input schema.json \\",
        "  --preset standard-py312-20260909 \\",
        "  --no-snake-case-field \\",
        "  --no-use-annotated \\",
        "  --enum-field-as-literal none",
        "```",
        "",
        (
            "Use documented `--no-*` options for boolean settings that support negation. "
            "For value options, pass the replacement value explicitly, such as `--enum-field-as-literal none`."
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
        "  --preset standard-py312-20260909 \\",
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
        'preset = "standard-py312-20260909"',
        "",
        "[tool.datamodel-codegen.profiles.api]",
        'input = "schemas/api.json"',
        'output = "src/models/api.py"',
        'preset = "practical-py312-20260909"',
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
        "  --preset practical-py312-20260909 \\",
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
            f"`{_STANDARD_PRESET_NAMES_BY_TARGET[target_python_version].value}` | "
            f"`{_PRACTICAL_PRESET_NAMES_BY_TARGET[target_python_version].value}` |"
        )
        for target_python_version in PythonVersion
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
            lines.append(f"| {group.title} | {options} | {group.description} |")
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
    for item in _resolve_preset_info(info, context):
        config_data[item.field_name] = cast("TomlValue", item.pyproject_value)
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
    rendered = ""
    match format_:
        case "json":
            rendered = render_presets_json() + "\n"
        case "markdown":
            rendered = render_presets_markdown().rstrip() + "\n"
        case _:  # pragma: no cover
            msg = f"Unsupported preset docs format: {format_!r}"
            raise PresetError(msg)
    return rendered


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
        msg = f"Unknown preset: {preset!r}. Available presets: {names}"
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

    return _merge_preset_configs(*(group.config for group in applicable_groups))
