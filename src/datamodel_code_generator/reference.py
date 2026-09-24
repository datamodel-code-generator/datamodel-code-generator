"""Reference resolution and model tracking system.

Provides Reference for tracking model references across schemas, ModelResolver
for managing class names and field names, and FieldNameResolver for converting
schema field names to valid Python identifiers.
"""

from __future__ import annotations

import re
import stat
from collections import defaultdict
from contextlib import contextmanager
from enum import Enum, auto
from functools import cached_property, lru_cache
from itertools import zip_longest
from keyword import iskeyword
from pathlib import Path, PurePath
from re import Pattern
from threading import RLock
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    NamedTuple,
    Optional,
    Protocol,
    SupportsIndex,
    TypeVar,
    cast,
    runtime_checkable,
)
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypeIs

from datamodel_code_generator import Error, NamingStrategy
from datamodel_code_generator._format_types import PythonVersion
from datamodel_code_generator.enums import ClassNameAffixScope, HTTPBackend
from datamodel_code_generator.util import camel_to_snake

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Generator, Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    import inflect

    from datamodel_code_generator.model import base as model_base
    from datamodel_code_generator.types import DataType

    DEFAULT_FIELD_NAME_RESOLVERS: dict[ModelType, type[FieldNameResolver]]


_ALIAS_RESOLUTION_CLASS_NAME_KEY: Final = "_alias_resolution_class_name"
_EXPLICIT_FIELD_ALIAS_KEY: Final = "_explicit_field_alias"


def split_module_name(
    name: str,
    *,
    treat_dot_as_module: bool | None,
    strict_dotted_module_names: bool = False,
) -> list[str] | None:
    """Split a dotted model name according to the configured inference policy."""
    match treat_dot_as_module:
        case False:
            return None
        case _ if "." not in name:
            return None
        case True:
            return name.split(".")
        case _ if not strict_dotted_module_names:
            return name.split(".")

    is_normalized = None
    if not name.isascii():
        from unicodedata import is_normalized  # noqa: PLC0415

    parts = name.split(".")
    for part in parts:
        if (
            not part.isidentifier()
            or iskeyword(part)
            or (is_normalized is not None and not is_normalized("NFKC", part))
        ):
            return None
    return parts


def get_inferred_module_name(
    name: str,
    *,
    treat_dot_as_module: bool | None,
    strict_dotted_module_names: bool = False,
) -> str:
    """Return the inferred parent module for a dotted model name."""
    if not (
        parts := split_module_name(
            name,
            treat_dot_as_module=treat_dot_as_module,
            strict_dotted_module_names=strict_dotted_module_names,
        )
    ):
        return ""
    return ".".join(parts[:-1])


def _is_data_type(value: object) -> TypeIs[DataType]:
    """Check if value is a DataType instance."""
    from datamodel_code_generator.types import DataType as DataType_  # noqa: PLC0415

    return isinstance(value, DataType_)


def _is_data_model(value: object) -> TypeIs[model_base.DataModel]:
    """Check if value is a DataModel instance."""
    from datamodel_code_generator.model.base import DataModel as DataModel_  # noqa: PLC0415

    return isinstance(value, DataModel_)


@runtime_checkable
class ReferenceChild(Protocol):
    """Protocol for objects that can be stored in Reference.children.

    This is a minimal protocol - actual usage checks isinstance for DataType
    or DataModel to access specific methods like replace_reference or class_name.
    Using a property makes the type covariant, allowing both DataModel (Reference)
    and DataType (Reference | None) to satisfy this protocol.
    """

    @property
    def reference(self) -> Reference | None:
        """Return the reference associated with this object."""
        ...


if TYPE_CHECKING:

    class _ReferenceSource(ReferenceChild, Protocol):
        """Protocol for objects that can be assigned to Reference.source."""

        fields: Sequence[Any]

        @property
        def is_alias(self) -> bool:
            """Return whether this source renders as an alias."""
            raise NotImplementedError

        @property
        def module_name(self) -> str | None:
            """Return this source module name."""
            raise NotImplementedError

        @property
        def nullable(self) -> bool:
            """Return whether this source is nullable."""
            raise NotImplementedError

else:
    _ReferenceSource = ReferenceChild


class _BaseModel(BaseModel):
    """Base model with field exclusion and pass-through support."""

    _exclude_fields: ClassVar[set[str]] = set()
    _pass_fields: ClassVar[set[str]] = set()

    if not TYPE_CHECKING:  # pragma: no branch

        def __init__(self, **values: Any) -> None:
            super().__init__(**values)
            for pass_field_name in self._pass_fields:
                if pass_field_name in values:
                    setattr(self, pass_field_name, values[pass_field_name])

    if not TYPE_CHECKING:  # pragma: no branch

        def dict(  # noqa: PLR0913  # pragma: no cover
            self,
            *,
            include: AbstractSet[int | str] | Mapping[int | str, Any] | None = None,
            exclude: AbstractSet[int | str] | Mapping[int | str, Any] | None = None,
            by_alias: bool = False,
            exclude_unset: bool = False,
            exclude_defaults: bool = False,
            exclude_none: bool = False,
        ) -> dict[str, Any]:
            return self.model_dump(
                include=include,  # ty: ignore[invalid-argument-type]
                exclude=set(exclude or ()) | self._exclude_fields,
                by_alias=by_alias,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )


class Reference(_BaseModel):
    """Represents a reference to a model in the schema.

    Tracks path, name, and relationships between models for resolution.
    """

    path: str
    original_name: str = ""
    name: str
    duplicate_name: Optional[str] = None  # noqa: UP045
    loaded: bool = True
    source: Optional[_ReferenceSource] = None  # noqa: UP045
    children: list[ReferenceChild] = Field(default_factory=list)
    _exclude_fields: ClassVar[set[str]] = {"children"}

    @model_validator(mode="before")
    def validate_original_name(cls, values: Any) -> Any:  # noqa: N805
        """Assign name to original_name if original_name is empty."""
        if not isinstance(values, dict):  # pragma: no cover
            return values
        original_name = values.get("original_name")
        if original_name:
            return values

        values["original_name"] = values.get("name", original_name)
        return values

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
        revalidate_instances="never",
        defer_build=True,
    )

    @property
    def short_name(self) -> str:
        """Return the last component of the dotted name."""
        return self.name.rsplit(".", 1)[-1]

    def replace_children_references(self, new_reference: Reference) -> None:
        """Replace all DataType children's reference with new_reference."""
        for child in self.children[:]:
            if _is_data_type(child):
                child.replace_reference(new_reference)

    def iter_data_model_children(self) -> Iterator[Any]:
        """Yield all DataModel children."""
        for child in self.children:
            if _is_data_model(child):
                yield child


SINGULAR_NAME_SUFFIX: str = "Item"

ID_PATTERN: Pattern[str] = re.compile(r"^#[^/].*")
_NON_IDENTIFIER_PATTERN: Pattern[str] = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹]|\W")

SPECIAL_PATH_MARKER: str = "#-datamodel-code-generator-#-"

T = TypeVar("T")


@contextmanager
def context_variable(setter: Callable[[T], None], current_value: T, new_value: T) -> Generator[None, None, None]:
    """Context manager that temporarily sets a value and restores it on exit."""
    previous_value: T = current_value
    setter(new_value)
    try:
        yield
    finally:
        setter(previous_value)


_BUILTIN_TYPE_ATTRIBUTES: frozenset[str] = frozenset(
    name for cls in (str, int, float, bytes) for name in dir(cls) if not name.startswith("_")
)

_BUILTIN_TYPE_ATTRIBUTES_INTRODUCED_IN: dict[PythonVersion, frozenset[str]] = {
    PythonVersion.PY_314: frozenset({"from_number"}),
}


def _get_builtin_type_attributes_for_target(target: PythonVersion) -> frozenset[str]:
    """Get builtin type attributes adjusted for the target Python version."""
    attrs = set(_BUILTIN_TYPE_ATTRIBUTES)
    target_key = target.version_key
    for ver, names in _BUILTIN_TYPE_ATTRIBUTES_INTRODUCED_IN.items():
        if target_key >= ver.version_key:
            attrs.update(names)
        else:
            attrs.difference_update(names)
    return frozenset(attrs)


class FieldNameResolver:
    """Converts schema field names to valid Python identifiers."""

    FIELD_ASSIGNMENT_HELPER: ClassVar[str | None] = None

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        aliases: Mapping[str, str | list[str]] | None = None,
        snake_case_field: bool = False,  # noqa: FBT001, FBT002
        empty_field_name: str | None = None,
        original_delimiter: str | None = None,
        special_field_name_prefix: str | None = None,
        remove_special_field_name_prefix: bool = False,  # noqa: FBT001, FBT002
        capitalise_enum_members: bool = False,  # noqa: FBT001, FBT002
        no_alias: bool = False,  # noqa: FBT001, FBT002
        use_subclass_enum: bool = False,  # noqa: FBT001, FBT002
        target_python_version: PythonVersion | None = None,
    ) -> None:
        """Initialize field name resolver with transformation options."""
        self.aliases: Mapping[str, str | list[str]] = {} if aliases is None else {**aliases}
        self.empty_field_name: str = empty_field_name or "_"
        self.snake_case_field = snake_case_field
        self.original_delimiter: str | None = original_delimiter
        self.special_field_name_prefix: str | None = (
            "field" if special_field_name_prefix is None else special_field_name_prefix
        )
        if self.special_field_name_prefix and not f"{self.special_field_name_prefix}_x".isidentifier():
            msg = (
                f"--special-field-name-prefix '{self.special_field_name_prefix}' "
                "is not a valid Python identifier prefix"
            )
            raise Error(msg)
        self.remove_special_field_name_prefix: bool = remove_special_field_name_prefix
        self.capitalise_enum_members: bool = capitalise_enum_members
        self.no_alias = no_alias
        self.use_subclass_enum: bool = use_subclass_enum
        self.target_python_version = target_python_version

    def prepare_explicit_field_aliases(self, fields: list[model_base.DataModelFieldBase], reference: Reference) -> None:
        """Retain selected explicit aliases until all emitted field names are finalized."""
        aliases = self.aliases
        for field in fields:
            if (original_name := field.alias if field.alias is not None else field.original_name) is None:
                continue
            class_name = field.__dict__.get(_ALIAS_RESOLUTION_CLASS_NAME_KEY, reference.original_name)
            alias = aliases.get(f"{class_name}.{original_name}")
            if not isinstance(alias, str) and not alias:
                alias = aliases.get(original_name)
            if isinstance(alias, str):
                field.__dict__[_EXPLICIT_FIELD_ALIAS_KEY] = (alias, original_name)
            else:
                field.__dict__.pop(_EXPLICIT_FIELD_ALIAS_KEY, None)
        self.validate_explicit_field_aliases(fields, final=False)

    @classmethod
    def validate_explicit_field_aliases(cls, fields: list[model_base.DataModelFieldBase], *, final: bool) -> None:
        """Diagnose collisions without applying automatic naming policy to user choices."""
        if not any(_EXPLICIT_FIELD_ALIAS_KEY in field.__dict__ for field in fields):
            return
        helper_name = cls.FIELD_ASSIGNMENT_HELPER if final else None
        names: dict[str, model_base.DataModelFieldBase] = {}
        field_helper: model_base.DataModelFieldBase | None = None
        for field in fields:
            name = cast("str", field.name)
            if final and not name.isascii():
                from unicodedata import normalize  # noqa: PLC0415

                name = normalize("NFKC", name)
            original_name = field.alias if field.alias is not None else field.original_name
            explicit = _EXPLICIT_FIELD_ALIAS_KEY in field.__dict__
            if (
                (previous := names.get(name)) is not None
                and (previous.alias if previous.alias is not None else previous.original_name) != original_name
                and (explicit or _EXPLICIT_FIELD_ALIAS_KEY in previous.__dict__)
            ):
                conflict = field if explicit else previous
                alias, original_name = conflict.__dict__[_EXPLICIT_FIELD_ALIAS_KEY]
                msg = f"Alias {alias!r} for field {original_name!r} conflicts with another field."
                raise Error(msg)
            names[name] = field
            if not final:
                continue
            invalid = explicit and (not cast("str", field.name).isidentifier() or iskeyword(cast("str", field.name)))
            if (
                explicit
                and (conflicts_with_alias := cast("model_base.DataModel", field.parent).EXPLICIT_ALIAS_CONFLICT_CHECKER)
                is not None
            ):
                invalid |= conflicts_with_alias(field, name)
            if field_helper is not None and str(field).startswith(f"{helper_name}("):
                field = field_helper  # noqa: PLW2901
                invalid = True
            if explicit and name == helper_name:
                field_helper = field
            if invalid:
                alias, original_name = field.__dict__[_EXPLICIT_FIELD_ALIAS_KEY]
                msg = f"Alias {alias!r} for field {original_name!r} is not a valid field name."
                raise Error(msg)

    def _validate_field_name(self, field_name: str) -> bool:  # noqa: ARG002, PLR6301
        """Check if a field name is valid. Subclasses may override."""
        return True

    def _ascii_identifier_fallback(self, name: str) -> str:
        fallback = "".join(
            character if character == "_" or (character.isascii() and character.isalnum()) else f"_u{ord(character):x}_"
            for character in name
        )
        if fallback.startswith("_"):
            fallback = f"{self.special_field_name_prefix}{fallback}"
        elif not fallback or fallback[0].isnumeric():
            fallback = f"{self.special_field_name_prefix}_{fallback}"
        return fallback

    def get_valid_name(  # noqa: PLR0912
        self,
        name: str,
        excludes: set[str] | None = None,
        ignore_snake_case_field: bool = False,  # noqa: FBT001, FBT002
        upper_camel: bool = False,  # noqa: FBT001, FBT002
    ) -> str:
        """Convert a name to a valid Python identifier."""
        if not name:
            name = self.empty_field_name
        if name[0] == "#":
            name = name[1:] or self.empty_field_name

        if self.snake_case_field and not ignore_snake_case_field and self.original_delimiter:
            name = snake_to_upper_camel(name, delimiter=self.original_delimiter)

        name = _NON_IDENTIFIER_PATTERN.sub("_", name)
        if name[0].isnumeric():
            name = f"{self.special_field_name_prefix}_{name}"

        # We should avoid having a field begin with an underscore, as it
        # causes pydantic to consider it as private
        while name.startswith("_"):
            if self.remove_special_field_name_prefix and (stripped_name := name.lstrip("_")):
                name = stripped_name
                continue
            name = f"{self.special_field_name_prefix}{name}"
            break
        if self.capitalise_enum_members or (self.snake_case_field and not ignore_snake_case_field):
            name = camel_to_snake(name)
        count = 1
        validated_name = name.upper() if self.capitalise_enum_members else name
        if iskeyword(validated_name) or not self._validate_field_name(validated_name):
            name += "_"
        if upper_camel:
            new_name = snake_to_upper_camel(name)
        elif self.capitalise_enum_members:
            new_name = name.upper()
        else:
            new_name = name
        while (
            not new_name.isidentifier()
            or iskeyword(new_name)
            or (excludes and new_name in excludes)
            or not self._validate_field_name(new_name)
        ):
            if not new_name.isidentifier() and not new_name.isascii():
                name = self._ascii_identifier_fallback(name)
                count = 1
                if upper_camel:
                    new_name = snake_to_upper_camel(name)
                elif self.capitalise_enum_members:
                    new_name = name.upper()
                else:
                    new_name = name
                continue
            new_name = f"{name}{count}" if upper_camel else f"{name}_{count}"
            count += 1
        return new_name

    def _resolve_alias_value(
        self,
        field_name: str,
        alias_value: object,
        excludes: set[str] | None,
    ) -> tuple[str, str | list[str]] | None:
        if isinstance(alias_value, list) and alias_value:
            if not all(isinstance(alias, str) for alias in alias_value):
                return None
            alias_values = cast("list[str]", alias_value)
            valid_name = self.get_valid_name(alias_values[0], excludes=excludes)
            return valid_name, [field_name, *alias_values]
        if isinstance(alias_value, str):
            return alias_value, field_name
        return None

    def get_valid_field_name_and_alias(
        self,
        field_name: str,
        excludes: set[str] | None = None,
        path: list[str] | None = None,
        class_name: str | None = None,
    ) -> tuple[str, str | list[str] | None]:
        """Get valid field name and original alias if different.

        Supports hierarchical alias resolution with the following priority:
        1. Scoped aliases (ClassName.field_name) - class-level specificity
        2. Flat aliases (field_name) - applies to all occurrences

        Args:
            field_name: The original field name from the schema.
            excludes: Set of names to avoid when generating valid names.
            path: Unused, kept for backward compatibility.
            class_name: Optional class name for scoped alias resolution.

        Returns:
            A tuple of (python_field_name, alias_or_aliases) where:
            - python_field_name: The valid Python identifier to use as the field name.
            - alias_or_aliases: None if no alias needed, str for single alias,
              or list[str] for multiple aliases (Pydantic v2 AliasChoices).
        """
        del path
        if class_name:
            scoped_key = f"{class_name}.{field_name}"
            resolved_alias = self._resolve_alias_value(field_name, self.aliases.get(scoped_key), excludes)
            if resolved_alias is not None:
                return resolved_alias

        resolved_alias = self._resolve_alias_value(field_name, self.aliases.get(field_name), excludes)
        if resolved_alias is not None:
            return resolved_alias

        valid_name = self.get_valid_name(field_name, excludes=excludes)
        return (
            valid_name,
            None if self.no_alias or field_name == valid_name else field_name,
        )


class EnumFieldNameResolver(FieldNameResolver):
    """Field name resolver for enum members with special handling for reserved names."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize with version-aware builtin type attributes."""
        super().__init__(**kwargs)
        target = self.target_python_version
        self._builtin_type_attributes = (
            _get_builtin_type_attributes_for_target(target) if target is not None else _BUILTIN_TYPE_ATTRIBUTES
        )

    def _validate_field_name(self, field_name: str) -> bool:
        """Check field name doesn't conflict with subclass enum base type attributes."""
        if not self.use_subclass_enum:
            return True
        return field_name not in self._builtin_type_attributes

    def get_valid_name(
        self,
        name: str,
        excludes: set[str] | None = None,
        ignore_snake_case_field: bool = False,  # noqa: FBT001, FBT002
        upper_camel: bool = False,  # noqa: FBT001, FBT002
    ) -> str:
        """Convert name to valid enum member, handling reserved names."""
        return super().get_valid_name(
            name="mro_" if name == "mro" else name,
            excludes={"mro"} | (excludes or set()),
            ignore_snake_case_field=ignore_snake_case_field,
            upper_camel=upper_camel,
        )


class ModelType(Enum):
    """Type of model for field name resolution strategy."""

    PYDANTIC = auto()
    ENUM = auto()
    CLASS = auto()
    MSGSPEC = auto()


_FIELD_NAME_RESOLVER_MODEL_TYPES = (
    ModelType.ENUM,
    ModelType.PYDANTIC,
    ModelType.CLASS,
    ModelType.MSGSPEC,
)


def _default_field_name_resolver_class(model_type: ModelType) -> type[FieldNameResolver]:
    """Resolve legacy defaults lazily while output models own their policies."""
    resolver_class: type[FieldNameResolver] = FieldNameResolver
    match model_type:
        case ModelType.PYDANTIC:
            from datamodel_code_generator.model.field_name import (  # noqa: PLC0415
                PydanticFieldNameResolver,
            )

            resolver_class = PydanticFieldNameResolver
        case ModelType.MSGSPEC:
            from datamodel_code_generator.model.field_name import MsgspecFieldNameResolver  # noqa: PLC0415

            resolver_class = MsgspecFieldNameResolver
        case ModelType.ENUM:
            resolver_class = EnumFieldNameResolver
        case _:
            resolver_class = FieldNameResolver
    return resolver_class


def __getattr__(name: str) -> Any:
    """Lazily preserve legacy resolver imports without owning output policy."""
    match name:
        case "PydanticFieldNameResolver":
            from datamodel_code_generator.model.field_name import (  # noqa: PLC0415
                PydanticFieldNameResolver,
            )

            value = PydanticFieldNameResolver
        case "MsgspecFieldNameResolver":
            from datamodel_code_generator.model.field_name import MsgspecFieldNameResolver  # noqa: PLC0415

            value = MsgspecFieldNameResolver
        case "DEFAULT_FIELD_NAME_RESOLVERS":
            value = {
                model_type: _default_field_name_resolver_class(model_type)
                for model_type in _FIELD_NAME_RESOLVER_MODEL_TYPES
            }
        case "__all__":
            value = (
                *(public_name for public_name in globals() if not public_name.startswith("_")),
                "PydanticFieldNameResolver",
                "MsgspecFieldNameResolver",
                "DEFAULT_FIELD_NAME_RESOLVERS",
            )
        case _:
            msg = f"module {__name__!r} has no attribute {name!r}"
            raise AttributeError(msg)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include lazily preserved exports in module discovery."""
    return sorted((*globals(), "PydanticFieldNameResolver", "MsgspecFieldNameResolver", "DEFAULT_FIELD_NAME_RESOLVERS"))


class _FieldNameResolverOptions(NamedTuple):
    """Construction options retained for lazy field-name resolver creation."""

    aliases: Mapping[str, str | list[str]] | None
    snake_case_field: bool
    empty_field_name: str | None
    original_delimiter: str | None
    special_field_name_prefix: str | None
    remove_special_field_name_prefix: bool
    capitalise_enum_members: bool
    no_alias: bool
    use_subclass_enum: bool
    target_python_version: PythonVersion | None


class _FieldNameResolvers:
    """Create output-owned resolvers only when the neutral resolver needs them."""

    __slots__ = ("_model_types", "_options", "_resolved", "_resolver_classes", "_strict_resolver_classes")

    def __init__(
        self,
        resolver_classes: dict[ModelType, type[FieldNameResolver]] | None,
        options: _FieldNameResolverOptions,
    ) -> None:
        if (legacy_defaults := globals().get("DEFAULT_FIELD_NAME_RESOLVERS")) is None:
            self._resolver_classes = () if resolver_classes is None else tuple(resolver_classes.items())
            self._model_types = _FIELD_NAME_RESOLVER_MODEL_TYPES
            self._strict_resolver_classes = False
        else:
            merged_resolver_classes = legacy_defaults.copy()
            if resolver_classes:
                merged_resolver_classes.update(resolver_classes)
            self._resolver_classes = tuple(merged_resolver_classes.items())
            self._model_types = tuple(merged_resolver_classes)
            self._strict_resolver_classes = True
        self._options = options
        self._resolved: dict[ModelType, FieldNameResolver] = {}

    def __getitem__(self, model_type: ModelType) -> FieldNameResolver:
        if (resolved := self._resolved.get(model_type)) is not None:
            return resolved
        resolver_class = None
        for configured_model_type, configured_resolver_class in self._resolver_classes:
            if configured_model_type is model_type:
                resolver_class = configured_resolver_class
                break
        if resolver_class is None and self._strict_resolver_classes:
            raise KeyError(model_type)
        if resolver_class is None:
            resolver_class = _default_field_name_resolver_class(model_type)
        is_enum = model_type is ModelType.ENUM
        options = self._options
        resolver = resolver_class(
            aliases=options.aliases,
            snake_case_field=options.snake_case_field,
            empty_field_name=options.empty_field_name,
            original_delimiter=options.original_delimiter,
            special_field_name_prefix=options.special_field_name_prefix,
            remove_special_field_name_prefix=options.remove_special_field_name_prefix,
            capitalise_enum_members=options.capitalise_enum_members if is_enum else False,
            no_alias=options.no_alias,
            use_subclass_enum=options.use_subclass_enum if is_enum else False,
            target_python_version=options.target_python_version if is_enum else None,
        )
        self._resolved[model_type] = resolver
        return resolver

    def materialize(self) -> dict[ModelType, FieldNameResolver]:
        """Return the exact eager mapping exposed by earlier releases."""
        self._resolved = {model_type: self[model_type] for model_type in self._model_types}
        return self._resolved


class ClassName(NamedTuple):
    """A class name with optional duplicate name for disambiguation."""

    name: str
    duplicate_name: str | None


def get_relative_path(base_path: PurePath, target_path: PurePath) -> PurePath:
    """Calculate relative path from base to target."""
    if base_path == target_path:
        return Path()
    if not target_path.is_absolute():
        return target_path
    parent_count: int = 0
    children: list[str] = []
    for base_part, target_part in zip_longest(base_path.parts, target_path.parts):
        if base_part == target_part and not parent_count:
            continue
        if base_part or not target_part:
            parent_count += 1
        if target_part:
            children.append(target_part)
    return Path(*[".." for _ in range(parent_count)], *children)


def _contains_symlink(path: Path) -> bool:
    """Return whether resolving ``path`` can traverse an existing symlink."""
    while path != path.parent:
        try:
            if stat.S_ISLNK(path.lstat().st_mode):
                return True
        except OSError:
            return True
        path = path.parent
    return False


class ModelResolver:  # noqa: PLR0904
    """Manages model references, class names, and field name resolution.

    Central registry for all model references during parsing, handling
    name uniqueness, path resolution, and field name transformations.
    """

    # Default suffixes for duplicate name resolution by model type
    DEFAULT_DUPLICATE_NAME_SUFFIX: ClassVar[dict[str, str]] = {
        "model": "Model",
        "enum": "Enum",
    }
    _MAX_RESOLVED_LOCAL_FILE_PARTS: ClassVar[int] = 256
    _MIN_STATEFUL_REDUCE_ITEMS: ClassVar[int] = 3
    _SLOTTED_STATE_ITEMS: ClassVar[int] = 2

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        exclude_names: set[str] | None = None,
        duplicate_name_suffix: str | None = None,
        base_url: str | None = None,
        singular_name_suffix: str | None = None,
        aliases: Mapping[str, str | list[str]] | None = None,
        snake_case_field: bool = False,  # noqa: FBT001, FBT002
        empty_field_name: str | None = None,
        custom_class_name_generator: Callable[[str], str] | None = None,
        base_path: Path | None = None,
        field_name_resolver_classes: dict[ModelType, type[FieldNameResolver]] | None = None,
        original_field_name_delimiter: str | None = None,
        special_field_name_prefix: str | None = None,
        remove_special_field_name_prefix: bool = False,  # noqa: FBT001, FBT002
        capitalise_enum_members: bool = False,  # noqa: FBT001, FBT002
        no_alias: bool = False,  # noqa: FBT001, FBT002
        use_subclass_enum: bool = False,  # noqa: FBT001, FBT002
        target_python_version: PythonVersion | None = None,
        remove_suffix_number: bool = False,  # noqa: FBT001, FBT002
        parent_scoped_naming: bool = False,  # noqa: FBT001, FBT002
        treat_dot_as_module: bool | None = None,  # noqa: FBT001
        strict_dotted_module_names: bool = False,  # noqa: FBT001, FBT002
        naming_strategy: NamingStrategy | None = None,
        duplicate_name_suffix_map: dict[str, str] | None = None,
        class_name_prefix: str | None = None,
        class_name_suffix: str | None = None,
        class_name_affix_scope: ClassNameAffixScope | None = None,
        skip_affix_for_root: bool = False,  # noqa: FBT001, FBT002
        model_name_map: Mapping[str, str] | None = None,
        default_value_overrides: Mapping[str, Any] | None = None,
        http_backend: HTTPBackend = HTTPBackend.AUTO,
    ) -> None:
        """Initialize model resolver with naming and resolution options."""
        self.references: dict[str, Reference] = {}
        self._current_root: Sequence[str] = []
        self._root_id: str | None = None
        self._root_id_base_path: str | None = None
        self.ids: defaultdict[str, dict[str, str]] = defaultdict(dict)
        self.after_load_files: set[str] = set()
        self.exclude_names: set[str] = exclude_names or set()
        self.duplicate_name_suffix: str | None = duplicate_name_suffix
        self._base_url: str | None = base_url
        self.http_backend = http_backend
        self.singular_name_suffix: str = (
            singular_name_suffix if isinstance(singular_name_suffix, str) else SINGULAR_NAME_SUFFIX
        )
        self._field_name_resolvers: dict[ModelType, FieldNameResolver] | _FieldNameResolvers = _FieldNameResolvers(
            field_name_resolver_classes,
            _FieldNameResolverOptions(
                aliases=None if aliases is None else {**aliases},
                snake_case_field=snake_case_field,
                empty_field_name=empty_field_name,
                original_delimiter=original_field_name_delimiter,
                special_field_name_prefix=special_field_name_prefix,
                remove_special_field_name_prefix=remove_special_field_name_prefix,
                capitalise_enum_members=capitalise_enum_members,
                no_alias=no_alias,
                use_subclass_enum=use_subclass_enum,
                target_python_version=target_python_version,
            ),
        )
        self.class_name_generator = custom_class_name_generator or self.default_class_name_generator
        self._base_path: Path = base_path or Path.cwd()
        self._current_base_path: Path | None = self._base_path
        self._resolved_local_file_parts: dict[tuple[Path, str], str] = {}
        self._resolved_base_path_cache: Path | None = None
        self.remove_suffix_number: bool = remove_suffix_number

        # Handle naming strategy with backward compatibility for parent_scoped_naming
        if naming_strategy is None and parent_scoped_naming:
            naming_strategy = NamingStrategy.ParentPrefixed
        self.naming_strategy: NamingStrategy = naming_strategy or NamingStrategy.Numbered
        self.parent_scoped_naming = parent_scoped_naming or (self.naming_strategy == NamingStrategy.ParentPrefixed)
        self.treat_dot_as_module = treat_dot_as_module
        self.strict_dotted_module_names = strict_dotted_module_names

        # Duplicate name suffix map for type-specific suffixes
        # Only use suffixes when explicitly provided via --duplicate-name-suffix
        self.duplicate_name_suffix_map: dict[str, str] = duplicate_name_suffix_map or {}

        self.class_name_prefix: str = class_name_prefix or ""
        self.class_name_suffix: str = class_name_suffix or ""
        if self.class_name_prefix and not self.class_name_prefix.isidentifier():
            msg = f"--class-name-prefix '{self.class_name_prefix}' is not a valid Python identifier"
            raise ValueError(msg)
        if self.class_name_suffix and not (f"A{self.class_name_suffix}").isidentifier():
            msg = f"--class-name-suffix '{self.class_name_suffix}' is not a valid Python identifier component"
            raise ValueError(msg)
        self.class_name_affix_scope: ClassNameAffixScope = class_name_affix_scope or ClassNameAffixScope.All
        self.skip_affix_for_root: bool = skip_affix_for_root
        self.model_name_map: Mapping[str, str] = {} if model_name_map is None else {**model_name_map}

        # Incrementally maintained counts of reference names for O(1) uniqueness checking.
        self._reference_names_cache: dict[str, int] = {}
        self._unique_name_start_hints: dict[tuple[str, str, str], int] = {}

        # Default value overrides from external JSON file
        self.default_value_overrides: Mapping[str, Any] = (
            {} if default_value_overrides is None else {**default_value_overrides}
        )

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> str | tuple[Any, ...]:
        """Serialize resolver state in the public layout used by earlier releases."""
        reduced = super().__reduce_ex__(protocol)
        if isinstance(reduced, str) or len(reduced) < self._MIN_STATEFUL_REDUCE_ITEMS:
            return reduced
        state = reduced[2]
        if isinstance(state, dict):
            if "_field_name_resolvers" not in state:
                return reduced
            instance_state = state
            slot_state = None
        elif (
            isinstance(state, tuple)
            and len(state) == self._SLOTTED_STATE_ITEMS
            and isinstance(state[0], dict)
            and isinstance(state[1], dict)
            and "_field_name_resolvers" in state[0]
        ):
            instance_state, slot_state = state
        else:
            return reduced
        resolvers = instance_state["_field_name_resolvers"]
        if isinstance(resolvers, _FieldNameResolvers):
            resolvers = resolvers.materialize()
            self._field_name_resolvers = resolvers
        instance_state = instance_state.copy()
        instance_state["field_name_resolvers"] = resolvers
        del instance_state["_field_name_resolvers"]
        instance_state["_reference_names_cache"] = set(self._reference_names_cache)
        state = instance_state if slot_state is None else (instance_state, slot_state.copy())
        return (*reduced[:2], state, *reduced[3:])

    def __setstate__(
        self,
        state: dict[str, Any] | tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        """Migrate resolver mappings serialized by earlier releases."""
        if isinstance(state, tuple):
            instance_state, slot_state = state
        else:
            instance_state, slot_state = state, {}
        if "field_name_resolvers" in instance_state:
            instance_state["_field_name_resolvers"] = instance_state.pop("field_name_resolvers")
        if type(instance_state.get("_reference_names_cache")) is not dict:
            reference_names: dict[str, int] = {}
            for reference in instance_state.get("references", {}).values():
                reference_names[reference.name] = reference_names.get(reference.name, 0) + 1
            instance_state["_reference_names_cache"] = reference_names
        self.__dict__.update(instance_state)
        self.__dict__.setdefault("_resolved_base_path_cache", None)
        for name, value in slot_state.items():
            setattr(self, name, value)

    @property
    def field_name_resolvers(self) -> dict[ModelType, FieldNameResolver]:
        """Expose the same eager plain dictionary as earlier releases."""
        if isinstance(resolvers := self._field_name_resolvers, _FieldNameResolvers):
            resolvers = resolvers.materialize()
            self._field_name_resolvers = resolvers
        return resolvers

    @field_name_resolvers.setter
    def field_name_resolvers(self, resolvers: dict[ModelType, FieldNameResolver]) -> None:
        """Preserve replacement of the public resolver mapping."""
        self._field_name_resolvers = resolvers

    def _get_model_name_map_value(self, path: str, generated_name: str, original_name: str) -> str | None:
        """Return an explicit model rename for a source path or generated name."""
        if not self.model_name_map:
            return None

        _, separator, fragment = path.partition("#")
        candidates = (
            (path, f"#{fragment}", generated_name, original_name)
            if separator
            else (
                path,
                generated_name,
                original_name,
            )
        )
        for candidate in candidates:
            if (mapped_name := self.model_name_map.get(candidate)) is not None:
                return mapped_name
        return None

    def _ensure_model_name_map_value(
        self,
        path: str,
        generated_name: str,
        mapped_name: str,
        reserved_name: str | None,
    ) -> None:
        if not self.validate_name(mapped_name):
            msg = f"--model-name-map maps {path!r} to invalid class name {mapped_name!r}"
            raise Error(msg)

        reference_names = self._get_reference_names()
        conflict_reason = (
            "already used"
            if mapped_name != reserved_name and mapped_name in reference_names
            else "reserved"
            if mapped_name != generated_name and mapped_name in self.exclude_names
            else None
        )
        if conflict_reason:
            msg = f"--model-name-map maps {path!r} to {mapped_name!r}, but that model name is {conflict_reason}"
            raise Error(msg)

    def _apply_model_name_map(
        self,
        path: str,
        original_name: str,
        class_name: ClassName,
        *,
        reserved_name: str | None = None,
    ) -> ClassName:
        if (mapped_name := self._get_model_name_map_value(path, class_name.name, original_name)) is None:
            return class_name

        self._ensure_model_name_map_value(path, class_name.name, mapped_name, reserved_name)
        return ClassName(name=mapped_name, duplicate_name=None)

    def _reset_for_reuse(self, exclude_names: set[str]) -> None:
        """Reset naming state so this resolver behaves like a freshly constructed one.

        Internal helper for hot paths that would otherwise construct a new
        ModelResolver per call; only the state touched by add() is reset.
        """
        self.exclude_names = exclude_names
        self.references.clear()
        self._reference_names_cache.clear()
        self._unique_name_start_hints.clear()

    def _get_reference_names(self) -> dict[str, int]:
        """Get the counts of all reference names for uniqueness checking."""
        return self._reference_names_cache

    def _update_reference_name(self, old_name: str | None, new_name: str, *, unique: bool = True) -> None:
        """Update the reference names cache when a reference name changes."""
        if old_name == new_name:
            return
        if old_name:
            self._remove_reference_name(old_name)
        if unique:
            self._reference_names_cache[new_name] = 1
            return
        self._reference_names_cache[new_name] = self._reference_names_cache.get(new_name, 0) + 1

    def _remove_reference_name(self, name: str) -> None:
        """Remove a name from the reference names cache."""
        if (count := self._reference_names_cache.get(name, 0)) > 1:
            self._reference_names_cache[name] = count - 1
            return
        self._reference_names_cache.pop(name, None)
        self._invalidate_unique_name_hints(name)

    def refresh_reference_names(self) -> None:
        """Refresh cached names after a batch reference rename."""
        reference_names: dict[str, int] = {}
        for reference in self.references.values():
            reference_names[reference.name] = reference_names.get(reference.name, 0) + 1
        self._reference_names_cache = reference_names
        self._unique_name_start_hints.clear()

    @property
    def current_base_path(self) -> Path | None:
        """Return the current base path for file resolution."""
        return self._current_base_path

    def set_current_base_path(self, base_path: Path | None) -> None:
        """Set the current base path for file resolution."""
        self._current_base_path = base_path

    @property
    def base_url(self) -> str | None:
        """Return the base URL for reference resolution."""
        return self._base_url

    def set_base_url(self, base_url: str | None) -> None:
        """Set the base URL for reference resolution."""
        self._base_url = base_url

    @contextmanager
    def current_base_path_context(self, base_path: Path | None) -> Generator[None, None, None]:
        """Temporarily set the current base path within a context."""
        if base_path:
            base_path = (self._base_path / base_path).resolve()
        with context_variable(self.set_current_base_path, self.current_base_path, base_path):  # ty: ignore[invalid-argument-type]
            yield

    @contextmanager
    def base_url_context(self, base_url: str | None) -> Generator[None, None, None]:
        """Temporarily set the base URL within a context.

        Only sets the base_url if:
        - The new value is actually a URL (http://, https://, or file://)
        - OR _base_url was already set (switching between URLs)
        This preserves backward compatibility for local file parsing where
        this method was previously a no-op.
        """
        if self._base_url or (base_url and is_url(base_url)):
            with context_variable(self.set_base_url, self.base_url, base_url):  # ty: ignore[invalid-argument-type]
                yield
        else:
            yield

    @property
    def current_root(self) -> Sequence[str]:
        """Return the current root path components."""
        return self._current_root

    def set_current_root(self, current_root: Sequence[str]) -> None:
        """Set the current root path components."""
        self._current_root = current_root

    @contextmanager
    def current_root_context(self, current_root: Sequence[str]) -> Generator[None, None, None]:
        """Temporarily set the current root path within a context."""
        with context_variable(self.set_current_root, self.current_root, current_root):  # ty: ignore[invalid-argument-type]
            yield

    @property
    def root_id(self) -> str | None:
        """Return the root identifier for the current schema."""
        return self._root_id

    @property
    def root_id_base_path(self) -> str | None:
        """Return the base path component of the root identifier."""
        return self._root_id_base_path

    def set_root_id(self, root_id: str | None) -> None:
        """Set the root identifier and extract its base path."""
        if root_id and "/" in root_id:
            self._root_id_base_path = root_id.rsplit("/", 1)[0]
        else:
            self._root_id_base_path = None

        self._root_id = root_id

    def add_id(self, id_: str, path: Sequence[str]) -> None:
        """Register an identifier mapping to a resolved reference path."""
        self.ids["/".join(self.current_root)][id_] = self.resolve_ref(path)

    def _get_path_absolute_local_file(self, ref: str) -> tuple[Path, str] | None:
        """Return the local file path for a path-absolute URI ref."""
        if not (self.root_id and self.current_base_path and self.current_root and ref.startswith("/")):
            return None

        file_path, fragment = ref.split("#", 1) if "#" in ref else (ref, "")
        root_path = urlparse(self.root_id).path
        current_root = "/".join(self.current_root)
        if not (root_path and current_root and root_path.endswith(f"/{current_root}")):
            return None

        uri_root = root_path[: -len(current_root)]
        if not file_path.startswith(uri_root):
            return None

        relative_file_path = file_path.removeprefix(uri_root).lstrip("/")
        if not relative_file_path:
            return None

        base_path = self._base_path.resolve()
        local_file_path = Path(base_path, relative_file_path).resolve()
        if not local_file_path.is_relative_to(base_path) or not local_file_path.is_file():
            return None

        return local_file_path, fragment

    def _resolve_path_absolute_local_ref(self, ref: str) -> str | None:
        """Resolve path-absolute URI refs against the local schema root."""
        local_ref = self._get_path_absolute_local_file(ref)
        current_base_path = self.current_base_path
        if local_ref is None or current_base_path is None:
            return None

        local_file_path, fragment = local_ref
        resolved_ref = get_relative_path(current_base_path, local_file_path).as_posix()
        if fragment:
            resolved_ref += f"#{fragment}"
        return resolved_ref

    def _resolved_base_path(self) -> Path:
        """Return a cached canonical base path for local references."""
        if self._resolved_base_path_cache is None:
            self._resolved_base_path_cache = self._base_path.resolve()
        return self._resolved_base_path_cache

    def resolve_ref(self, path: Sequence[str] | str) -> str:  # noqa: PLR0911, PLR0912, PLR0914, PLR0915
        """Resolve a reference path to its canonical form."""
        joined_path = path if isinstance(path, str) else self.join_path(tuple(path))
        if not joined_path or joined_path == "#":
            return f"{'/'.join(self.current_root)}#"
        if path_absolute_ref := self._resolve_path_absolute_local_ref(joined_path):
            joined_path = path_absolute_ref
        if (
            (current_base_path := self.current_base_path)
            and not self.base_url
            and joined_path[0] != "#"
            and not is_url(joined_path)
        ):
            # resolve local file path
            file_path, fragment = joined_path.split("#", 1) if "#" in joined_path else (joined_path, "")
            cache_key = current_base_path, file_path
            if (resolved_file_part := self._resolved_local_file_parts.get(cache_key)) is None:
                local_file_path = Path(current_base_path, file_path)
                resolved_file_path = local_file_path.resolve()
                resolved_file_part = get_relative_path(self._resolved_base_path(), resolved_file_path).as_posix()
                if len(self._resolved_local_file_parts) < self._MAX_RESOLVED_LOCAL_FILE_PARTS and not _contains_symlink(
                    local_file_path
                ):
                    self._resolved_local_file_parts[cache_key] = resolved_file_part
            joined_path = resolved_file_part
            if fragment:
                joined_path += f"#{fragment}"
        if "#" in joined_path:
            file_part, fragment = joined_path.split("#", 1)
            if (
                file_part
                and fragment
                and not fragment.startswith("/")
                and (anchor_ref := self.ids.get(file_part, {}).get(f"#{fragment}"))
            ):
                return anchor_ref
        if ID_PATTERN.match(joined_path) and SPECIAL_PATH_MARKER not in joined_path:
            id_scope = "/".join(self.current_root)
            scoped_ids = self.ids[id_scope]
            ref: str | None = scoped_ids.get(joined_path)
            if ref is None:
                msg = (
                    f"Unresolved $id reference '{joined_path}' in scope '{id_scope or '<root>'}'. "
                    f"Known $id values: {', '.join(sorted(scoped_ids)) or '<none>'}"
                )
                raise Error(msg)
        else:
            if "#" not in joined_path:
                joined_path += "#"
            elif joined_path[0] == "#" and self.current_root:
                joined_path = f"{'/'.join(self.current_root)}{joined_path}"

            file_path, fragment = joined_path.split("#", 1)
            ref = f"{file_path}#{fragment}"
            if (
                file_path
                and self.root_id_base_path
                and not self.base_url
                and not (is_url(joined_path) or Path(self._base_path, file_path).is_file())
            ):
                ref = f"{self.root_id_base_path}/{ref}"

        if is_url(ref):
            file_part, path_part = ref.split("#", 1)
            id_scope = "/".join(self.current_root)
            scoped_ids = self.ids[id_scope]
            if file_part in scoped_ids:
                mapped_ref = scoped_ids[file_part]
                if path_part:
                    mapped_base, mapped_fragment = mapped_ref.split("#", 1) if "#" in mapped_ref else (mapped_ref, "")
                    combined_fragment = f"{mapped_fragment.rstrip('/')}/{path_part.lstrip('/')}"
                    return f"{mapped_base}#{combined_fragment}"
                return mapped_ref

        if self.base_url:
            from .http import join_url  # noqa: PLC0415

            effective_base = self.base_url
            if self.root_id:
                effective_base = (
                    self.root_id
                    if is_url(self.root_id)
                    else join_url(self.base_url, self.root_id, http_backend=self.http_backend)
                )
            joined_url = join_url(effective_base, ref, http_backend=self.http_backend)
            joined_url_without_fragment, _, fragment = joined_url.partition("#")
            return f"{joined_url_without_fragment}#{fragment}"

        if is_url(ref):
            file_part, path_part = ref.split("#", 1)
            if file_part == self.root_id:  # pragma: no cover
                return f"{'/'.join(self.current_root)}#{path_part}"
            target_url: ParseResult = urlparse(file_part)
            if not (self.root_id and self.current_base_path):
                return ref
            root_id_url: ParseResult = urlparse(self.root_id)
            if (target_url.scheme, target_url.netloc) == (
                root_id_url.scheme,
                root_id_url.netloc,
            ):  # pragma: no cover
                target_url_path = Path(target_url.path)
                target_path = (
                    self.current_base_path
                    / get_relative_path(Path(root_id_url.path).parent, target_url_path.parent)
                    / target_url_path.name
                )
                if target_path.exists():
                    return f"{target_path.resolve().relative_to(self._resolved_base_path())}#{path_part}"

        return ref

    def is_after_load(self, ref: str) -> bool:
        """Check if a reference points to a file loaded after the current one."""
        if is_url(ref) or not self.current_base_path:
            return False
        file_part, *_ = ref.split("#", 1)
        absolute_path = Path(self._base_path, file_part).resolve().as_posix()
        if self.is_external_root_ref(ref) or self.is_external_ref(ref):
            return absolute_path in self.after_load_files
        return False  # pragma: no cover

    @staticmethod
    def is_external_ref(ref: str) -> bool:
        """Check if a reference points to an external file."""
        return "#" in ref and ref[0] != "#"

    @staticmethod
    def is_external_root_ref(ref: str) -> bool:
        """Check if a reference points to an external file root."""
        return bool(ref) and ref[-1] == "#"

    @staticmethod
    @lru_cache(maxsize=4096)
    def join_path(path: tuple[str, ...]) -> str:
        """Join path components with slashes and normalize anchors."""
        joined_path = "/".join(p for p in path if p).replace("/#", "#")
        if "#" not in joined_path:
            joined_path += "#"
        return joined_path

    def _is_external_path(self, resolved_path: str) -> bool:
        """Check if a resolved path belongs to an external file."""
        current_root_path = self.join_path(tuple(self._current_root))
        current_file = current_root_path.split("#")[0]
        resolved_file = resolved_path.split("#", maxsplit=1)[0]
        return current_file != resolved_file

    def ref_original_name(self, ref: str, path: str) -> str:
        """Return the model name a reference derives before its schema is parsed."""
        split_ref = ref.rsplit("/", 1)
        if len(split_ref) == 1:
            return Path(split_ref[0].rstrip("#") if self.is_external_root_ref(path) else split_ref[0]).stem
        return Path(split_ref[1].rstrip("#")).stem if self.is_external_root_ref(path) else split_ref[1]

    def add_ref(self, ref: str, resolved: bool = False) -> Reference:  # noqa: FBT001, FBT002
        """Add a reference and return the Reference object."""
        path = self.resolve_ref(ref) if not resolved else ref
        if reference := self.references.get(path):
            return reference
        original_name = self.ref_original_name(ref, path)
        # For PrimaryFirst strategy, use unique=True for external references
        # so that definitions in the main input file get priority for clean names
        use_unique = self.naming_strategy == NamingStrategy.PrimaryFirst and self._is_external_path(path)
        has_affix_config = bool(self.class_name_prefix or self.class_name_suffix)
        needs_scope = self.class_name_affix_scope != ClassNameAffixScope.All
        skip_affix = has_affix_config and needs_scope
        class_name = self.get_class_name(original_name, unique=use_unique, skip_affix=skip_affix)
        name = self._apply_model_name_map(path, original_name, class_name).name
        reference = Reference(
            path=path,
            original_name=original_name,
            name=name,
            loaded=False,
        )

        self.references[path] = reference
        self._update_reference_name(None, reference.name, unique=use_unique)
        return reference

    def _find_parent_reference(self, path: Sequence[str]) -> Reference | None:
        """Find the closest parent reference for a given path.

        Traverses up the path hierarchy to find the first existing parent reference.
        Returns None if no parent reference is found.
        """
        parent_path = list(path[:-1])
        while parent_path:
            if parent_reference := self.references.get(self.join_path(tuple(parent_path))):
                return parent_reference
            parent_path = parent_path[:-1]
        return None

    def _check_parent_scope_option(self, name: str, path: Sequence[str]) -> str:
        # Check for parent-prefixed naming via either the legacy flag or the new naming strategy
        use_parent_prefix = self.parent_scoped_naming or self.naming_strategy == NamingStrategy.ParentPrefixed
        if use_parent_prefix and (parent_ref := self._find_parent_reference(path)):
            return f"{parent_ref.name}_{name}"
        return name

    def _apply_full_path_naming(self, name: str, path: Sequence[str]) -> str:
        """Build name from full schema path for FullPath strategy.

        Uses the immediate parent reference to build a unique name.
        For example: Order > properties > item becomes OrderItem
        """
        if self.naming_strategy != NamingStrategy.FullPath:
            return name

        # Find the immediate parent reference to prefix the name
        if parent_ref := self._find_parent_reference(path):
            # Use immediate parent's name (CamelCase join without underscore)
            return f"{parent_ref.name}{snake_to_upper_camel(name)}"

        return name

    @staticmethod
    def _is_primary_definition(path: Sequence[str]) -> bool:
        """Check if path represents a primary schema definition."""
        # Primary definitions are directly under /definitions/ or /components/schemas/
        path_str = "/".join(path)
        primary_patterns = [
            "#/definitions/",
            "#/components/schemas/",
            "#/$defs/",
        ]
        for pattern in primary_patterns:
            if pattern in path_str:
                # Check if it's a direct child (not nested)
                after_pattern = path_str.split(pattern, 1)[-1]
                # If there's no more "/" after the pattern part, it's a primary definition
                if "/" not in after_pattern:
                    return True
        return False

    def _rename_external_ref_with_same_name(self, name: str, current_path: str) -> None:
        """Rename an external reference that has the same name as a primary definition.

        For PrimaryFirst strategy, when a primary definition in the main file
        has the same name as an external reference, rename the external reference
        so the primary definition can use the clean name.
        """
        for ref_path, ref in self.references.items():
            if ref.name == name and ref_path != current_path:
                # Check if this is an external reference (different file)
                ref_file = ref_path.split("#")[0]
                current_file = current_path.split("#", maxsplit=1)[0]
                if ref_file != current_file:  # pragma: no branch
                    # Rename this external reference
                    new_name = self._get_unique_name(name, camel=True)
                    old_name = ref.name
                    ref.duplicate_name = ref.name
                    ref.name = new_name
                    self._update_reference_name(old_name, new_name)
                    break

    def add(  # noqa: PLR0913
        self,
        path: Sequence[str],
        original_name: str,
        *,
        class_name: bool = False,
        singular_name: bool = False,
        unique: bool = True,
        singular_name_suffix: str | None = None,
        loaded: bool = False,
        model_type: str = "model",
        preserve_class_name: bool = False,
    ) -> Reference:
        """Add or update a model reference with the given path and name."""
        joined_path = self.join_path(tuple(path))
        is_root = joined_path == "#" or (joined_path.endswith("#") and "/" not in joined_path.split("#")[-1])
        reference: Reference | None = self.references.get(joined_path)
        old_ref_name: str | None = reference.name if reference else None
        if reference:
            was_already_loaded = reference.loaded
            if loaded and not reference.loaded:
                reference.loaded = True
            has_affix_config = bool(self.class_name_prefix or self.class_name_suffix)
            needs_scope = self.class_name_affix_scope != ClassNameAffixScope.All
            needs_reprocess = has_affix_config and needs_scope and not was_already_loaded
            if not needs_reprocess and (
                not original_name or original_name in {reference.original_name, reference.name}
            ):
                return reference
        name = original_name
        duplicate_name: str | None = None
        if class_name:
            name = self._check_parent_scope_option(name, path)
            name = self._apply_full_path_naming(name, path)

            # For PrimaryFirst strategy, check if this is a primary definition
            # Primary definitions get priority (don't need suffix), others get suffix when there's conflict
            is_primary = self._is_primary_definition(path)
            if self.naming_strategy == NamingStrategy.PrimaryFirst and is_primary:
                # For primary definitions, try to use the clean name first
                # If an external reference has the same name, rename it
                self._rename_external_ref_with_same_name(name, joined_path)
            class_name_result = self.get_class_name(
                name=name,
                unique=unique,
                reserved_name=reference.name if reference else None,
                singular_name=singular_name,
                singular_name_suffix=singular_name_suffix,
                model_type=model_type,
                is_root=is_root,
                preserve_name=preserve_class_name,
            )
            class_name_result = self._apply_model_name_map(
                joined_path,
                original_name,
                class_name_result,
                reserved_name=reference.name if reference else None,
            )
            name, duplicate_name = class_name_result
        else:
            # TODO: create a validate for module name
            name = self.get_valid_field_name(name, model_type=ModelType.CLASS)
            if singular_name:  # pragma: no cover
                name = get_singular_name(name, singular_name_suffix or self.singular_name_suffix)
            elif unique:  # pragma: no cover
                unique_name = self._get_unique_name(name)
                if unique_name != name:
                    duplicate_name = name
                name = unique_name
        if reference:
            reference.original_name = original_name
            reference.name = name
            reference.loaded = loaded
            reference.duplicate_name = duplicate_name
            self._update_reference_name(old_ref_name, name, unique=unique)
        else:
            reference = Reference(
                path=joined_path,
                original_name=original_name,
                name=name,
                loaded=loaded,
                duplicate_name=duplicate_name,
            )
            self.references[joined_path] = reference
            self._update_reference_name(None, name, unique=unique)
        return reference

    def get(self, path: Sequence[str] | str) -> Reference | None:
        """Get a reference by path, returning None if not found."""
        return self.references.get(self.resolve_ref(path))

    def delete(self, path: Sequence[str] | str) -> None:
        """Delete a reference by path if it exists."""
        resolved = self.resolve_ref(path)
        if resolved in self.references:
            old_name = self.references[resolved].name
            del self.references[resolved]
            self._remove_reference_name(old_name)

    def default_class_name_generator(self, name: str) -> str:
        """Generate a valid class name from a string."""
        # TODO: create a validate for class name
        return self._field_name_resolvers[ModelType.CLASS].get_valid_name(
            name, ignore_snake_case_field=True, upper_camel=True
        )

    def _apply_class_name_affix(
        self,
        name: str,
        model_type: str = "model",
        is_root: bool = False,  # noqa: FBT001, FBT002
    ) -> str:
        """Apply prefix/suffix based on scope.

        Args:
            name: The base class name (after singularization)
            model_type: "model" or "enum"
            is_root: True if this is the root schema

        Returns:
            The name with prefix/suffix applied according to scope rules.
        """
        if is_root and self.skip_affix_for_root:
            return name

        scope = self.class_name_affix_scope
        is_enum = model_type == "enum"
        should_apply = (
            scope == ClassNameAffixScope.All
            or (scope == ClassNameAffixScope.Models and not is_enum)
            or (scope == ClassNameAffixScope.Enums and is_enum)
        )

        if not should_apply:
            return name

        return f"{self.class_name_prefix}{name}{self.class_name_suffix}"

    def get_affixed_name(self, name: str, model_type: str = "model") -> str:
        """Apply class name generator and affix without uniqueness checks.

        Used by GraphQL parser which maintains its own naming.
        Note: GraphQL does NOT use singularization - this is intentional.
        Note: GraphQL dotted type names (e.g., 'Foo.Bar') are treated as single names,
              not module paths - treat_dot_as_module splitting is NOT applied.

        Args:
            name: The original name
            model_type: "model" or "enum"

        Returns:
            The name after applying generator and affix (no uniqueness, no singularization).
        """
        class_name = self.class_name_generator(name)
        return self._apply_class_name_affix(class_name, model_type=model_type, is_root=False)

    def _generate_class_name(self, name: str, *, preserve_name: bool = False) -> str:
        if preserve_name and self.validate_name(name):
            return name
        return self.class_name_generator(name)

    def get_class_name(  # noqa: PLR0913, PLR0917
        self,
        name: str,
        unique: bool = True,  # noqa: FBT001, FBT002
        reserved_name: str | None = None,
        singular_name: bool = False,  # noqa: FBT001, FBT002
        singular_name_suffix: str | None = None,
        model_type: str = "model",
        is_root: bool = False,  # noqa: FBT001, FBT002
        skip_affix: bool = False,  # noqa: FBT001, FBT002
        preserve_name: bool = False,  # noqa: FBT001, FBT002
    ) -> ClassName:
        """Generate a unique class name with optional singularization."""
        split_name = None
        if "." in name and self.treat_dot_as_module is not False:
            split_name = (
                split_module_name(
                    name,
                    treat_dot_as_module=self.treat_dot_as_module,
                    strict_dotted_module_names=self.strict_dotted_module_names,
                )
                if self.strict_dotted_module_names and self.treat_dot_as_module is None
                else name.split(".")
            )
        if split_name:
            prefix = ".".join(
                # TODO: create a validate for class name
                self._field_name_resolvers[ModelType.CLASS].get_valid_name(n, ignore_snake_case_field=True)
                for n in split_name[:-1]
            )
            prefix += "."
            class_name = split_name[-1]
        else:
            prefix = ""
            class_name = name.replace(".", "_") if "." in name else name

        class_name = self._generate_class_name(class_name, preserve_name=preserve_name)

        if singular_name:
            class_name = get_singular_name(class_name, singular_name_suffix or self.singular_name_suffix)

        if not skip_affix:
            class_name = self._apply_class_name_affix(class_name, model_type=model_type, is_root=is_root)

        if unique and reserved_name == class_name:
            return ClassName(name=f"{prefix}{class_name}", duplicate_name=None)

        duplicate_name: str | None = None
        if unique:
            unique_name = self._get_unique_name(class_name, camel=True, model_type=model_type)
            if unique_name != class_name:
                duplicate_name = class_name
            class_name = unique_name
        return ClassName(name=f"{prefix}{class_name}", duplicate_name=duplicate_name)

    def _get_unique_name(self, name: str, camel: bool = False, model_type: str = "model") -> str:  # noqa: FBT001, FBT002
        reference_names = self._get_reference_names()
        exclude_names = self.exclude_names

        # Determine the suffix to use
        suffix = self._get_suffix_for_model_type(model_type)
        if not suffix and self.duplicate_name_suffix:
            suffix = self.duplicate_name_suffix

        delimiter = "" if camel else "_"
        hint_key = (name, suffix or "", delimiter)
        count = self._unique_name_start_hints.get(hint_key, 0)
        if count and self._is_unique_name_available(
            self._build_unique_name_candidate(name, suffix, delimiter, count - 1),
            reference_names,
            exclude_names,
        ):
            count = 0
        unique_name = self._build_unique_name_candidate(name, suffix, delimiter, count)
        while unique_name in reference_names or unique_name in exclude_names:
            count += 1
            unique_name = self._build_unique_name_candidate(name, suffix, delimiter, count)
        if count:
            self._unique_name_start_hints[hint_key] = count + 1
        return unique_name

    @staticmethod
    def _build_unique_name_candidate(name: str, suffix: str | None, delimiter: str, count: int) -> str:
        """Build the same duplicate candidate sequence used by _get_unique_name."""
        if count == 0:
            return name
        if suffix:
            suffix_count = count - 1
            return delimiter.join((name, suffix, str(suffix_count))) if suffix_count else delimiter.join((name, suffix))
        return delimiter.join((name, str(count)))

    @staticmethod
    def _is_unique_name_available(candidate: str, reference_names: Collection[str], exclude_names: set[str]) -> bool:
        """Return whether a duplicate-name candidate is currently free."""
        return candidate not in reference_names and candidate not in exclude_names

    @staticmethod
    def _matches_unique_name_candidate(candidate: str, name: str, suffix: str, delimiter: str) -> bool:
        """Return whether candidate belongs to the duplicate sequence for name."""
        if candidate == name:
            return True
        if suffix:
            first_duplicate = delimiter.join((name, suffix))
            if candidate == first_duplicate:
                return True
            prefix = f"{first_duplicate}{delimiter}" if delimiter else first_duplicate
        else:
            prefix = f"{name}{delimiter}" if delimiter else name
        if not candidate.startswith(prefix):
            return False
        count = candidate.removeprefix(prefix)
        return bool(count) and count.isdecimal()

    def _invalidate_unique_name_hints(self, released_name: str) -> None:
        """Drop duplicate-name hints whose sequence may reuse a released name."""
        if not self._unique_name_start_hints:
            return
        for hint_key in tuple(self._unique_name_start_hints):
            if self._matches_unique_name_candidate(released_name, *hint_key):
                del self._unique_name_start_hints[hint_key]

    def _get_suffix_for_model_type(self, model_type: str) -> str:
        """Get the suffix for a given model type from the suffix map."""
        return self.duplicate_name_suffix_map.get(model_type, self.duplicate_name_suffix_map.get("default", ""))

    @classmethod
    def validate_name(cls, name: str) -> bool:
        """Check if a name is a valid Python identifier."""
        return name.isidentifier() and not iskeyword(name)

    def get_valid_field_name(
        self,
        name: str,
        excludes: set[str] | None = None,
        model_type: ModelType = ModelType.PYDANTIC,
    ) -> str:
        """Get a valid field name for the specified model type."""
        return self._field_name_resolvers[model_type].get_valid_name(name, excludes)

    def prepare_explicit_field_aliases(
        self, fields: list[model_base.DataModelFieldBase], reference: Reference, model_type: ModelType
    ) -> None:
        """Record explicit choices before model transforms can combine fields."""
        self._field_name_resolvers[model_type].prepare_explicit_field_aliases(fields, reference)

    @staticmethod
    def validate_explicit_field_aliases(fields: list[model_base.DataModelFieldBase], model_type: ModelType) -> None:
        """Check finalized names against output model rules without renaming them."""
        _default_field_name_resolver_class(model_type).validate_explicit_field_aliases(fields, final=True)

    def _get_unique_field_name(self, name: str) -> str:
        """Return a unique class field name without creating a Reference."""
        return self._get_unique_name(self.get_valid_field_name(name, model_type=ModelType.CLASS))

    def get_valid_field_name_and_alias(
        self,
        field_name: str,
        excludes: set[str] | None = None,
        model_type: ModelType = ModelType.PYDANTIC,
        path: list[str] | None = None,
        class_name: str | None = None,
    ) -> tuple[str, str | list[str] | None]:
        """Get a valid field name and alias for the specified model type.

        Args:
            field_name: The original field name from the schema.
            excludes: Set of names to avoid when generating valid names.
            model_type: The type of model (PYDANTIC, ENUM, or CLASS).
            path: Unused, kept for backward compatibility.
            class_name: Optional class name for scoped alias resolution.

        Returns:
            A tuple of (python_field_name, alias_or_aliases) where:
            - python_field_name: The valid Python identifier to use as the field name.
            - alias_or_aliases: None if no alias needed, str for single alias,
              or list[str] for multiple aliases (Pydantic v2 AliasChoices).
        """
        del path
        return self._field_name_resolvers[model_type].get_valid_field_name_and_alias(
            field_name, excludes, class_name=class_name
        )

    def resolve_default_value(
        self,
        field_name: str,
        original_default: Any,
        has_default: bool,  # noqa: FBT001
        class_name: str | None,
    ) -> tuple[Any, bool]:
        """Resolve default value for a field, applying overrides if configured."""
        if not self.default_value_overrides:
            return original_default, has_default

        scoped_key = f"{class_name}.{field_name}" if class_name else None
        if scoped_key and scoped_key in self.default_value_overrides:
            return self.default_value_overrides[scoped_key], True

        if field_name in self.default_value_overrides:
            return self.default_value_overrides[field_name], True

        return original_default, has_default


_inflect_engine: inflect.engine | None = None
_PRIVATE_INFLECT_MODULE = "datamodel_code_generator._inflect"
_INFLECT_IMPORT_LOCK = RLock()


def _noop_typechecked(target: Any = None, **_: Any) -> Any:
    """Return a no-op replacement for typeguard.typechecked."""
    if target is None:
        return lambda wrapped: wrapped
    return target


def _load_private_inflect() -> Any:
    """Execute inflect bytecode with a private package and module-local import policy."""
    import builtins  # ruff: ignore[import-outside-top-level]
    import copy  # ruff: ignore[import-outside-top-level]
    import importlib.util  # ruff: ignore[import-outside-top-level]
    import sys  # ruff: ignore[import-outside-top-level]
    import types  # ruff: ignore[import-outside-top-level]

    source_spec = importlib.util.find_spec("inflect")
    if source_spec is None or (get_code := getattr(source_spec.loader, "get_code", None)) is None:
        msg = "inflect loader does not expose Python code"
        raise ImportError(msg)
    code = get_code("inflect")
    private_spec = copy.copy(source_spec)
    private_spec.name = _PRIVATE_INFLECT_MODULE
    inflect_module = importlib.util.module_from_spec(private_spec)
    typeguard_stub = types.ModuleType("typeguard")
    typeguard_stub.__dict__["typechecked"] = _noop_typechecked
    original_import = builtins.__import__

    def import_without_typeguard(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "typeguard":
            return typeguard_stub
        return original_import(name, *args, **kwargs)

    inflect_module.__dict__["__builtins__"] = {**vars(builtins), "__import__": import_without_typeguard}
    sys.modules[_PRIVATE_INFLECT_MODULE] = inflect_module
    exec(code, inflect_module.__dict__)  # ruff: ignore[exec-builtin]
    return inflect_module


def _import_inflect_without_typeguard_instrumentation() -> Any:
    """Load a private inflect package without changing public dependency modules."""
    import importlib  # ruff: ignore[import-outside-top-level]
    import sys  # ruff: ignore[import-outside-top-level]

    with _INFLECT_IMPORT_LOCK:
        if sys.modules.get("inflect") is None:
            if (inflect_module := sys.modules.get(_PRIVATE_INFLECT_MODULE)) is not None:
                return inflect_module

            try:
                inflect_module = _load_private_inflect()
            except BaseException as error:
                # Failed imports must not leave a reusable partial private package.
                for module_name in tuple(sys.modules):
                    if module_name == _PRIVATE_INFLECT_MODULE or module_name.startswith(f"{_PRIVATE_INFLECT_MODULE}."):
                        sys.modules.pop(module_name, None)
                if not isinstance(error, (AttributeError, ImportError, TypeError)):
                    raise
            else:
                return inflect_module
    # Public import hooks may reenter generation; do not hold our lock while waiting.
    return importlib.import_module("inflect")


def _get_inflect_engine() -> inflect.engine:
    """Get or create the inflect engine lazily."""
    global _inflect_engine  # noqa: PLW0603
    if _inflect_engine is None:
        inflect = _import_inflect_without_typeguard_instrumentation()

        _inflect_engine = inflect.engine()
    return _inflect_engine


@lru_cache(maxsize=4096)
def get_singular_name(name: str, suffix: str = SINGULAR_NAME_SUFFIX) -> str:
    """Convert a plural name to singular form."""
    singular_name = _get_inflect_engine().singular_noun(cast("inflect.Word", name))  # ty: ignore[redundant-cast]
    if singular_name is False:
        singular_name = f"{name}{suffix}"
    return singular_name


@lru_cache(maxsize=4096)
def snake_to_upper_camel(word: str, delimiter: str = "_") -> str:
    """Convert snake_case or delimited string to UpperCamelCase."""
    prefix = ""
    if word.startswith(delimiter):
        prefix = "_"
        word = word[1:]

    return prefix + "".join(x[0].upper() + x[1:] for x in word.split(delimiter) if x)


def is_url(ref: str) -> bool:
    """Check if a reference string is a URL (HTTP, HTTPS, or file scheme)."""
    return ref.startswith(("https://", "http://", "file://"))
