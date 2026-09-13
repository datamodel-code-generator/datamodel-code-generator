"""Base classes for data model generation.

Provides ConstraintsBase for field constraints, DataModelFieldBase for field
representation, and DataModel as the abstract base for all model types.
"""

from __future__ import annotations

import ast
import re
import sys
from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from itertools import accumulate
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, ClassVar, Optional, TypeVar
from warnings import warn

from pydantic import ConfigDict, Field
from typing_extensions import Self

from datamodel_code_generator import Error, cached_path_exists
from datamodel_code_generator._internal_utils import get_most_of_parent, to_hashable
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_ANY,
    IMPORT_OPTIONAL,
    IMPORT_UNION,
    Import,
)
from datamodel_code_generator.model.runtime_validation import (
    SchemaRuntimeValidation,
    _is_internal_schema_runtime_validation,
)
from datamodel_code_generator.python_literal import (
    _make_internal_type_expression,
    _normalize_string,
    represent_python_value,
)
from datamodel_code_generator.reference import Reference, _BaseModel
from datamodel_code_generator.types import (
    ANY,
    NONE,
    DataType,
    Nullable,
    chain_as_tuple,
    get_optional_type,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from jinja2 import Environment, Template

    from datamodel_code_generator import DataclassArguments
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.python_literal import PythonRuntimeExpression
    from datamodel_code_generator.types import DataTypeManager

TEMPLATE_DIR: Path = Path(__file__).parents[0] / "template"
_TYPING_IMPORT_NAMES: frozenset[str] = frozenset({
    IMPORT_ANNOTATED.import_,
    IMPORT_OPTIONAL.import_,
    IMPORT_UNION.import_,
})
_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES_TEMPLATE_DATA_KEY = "additionalPropertiesReferenceClasses"
_ADDITIONAL_PROPERTIES_TEMPLATE_DATA_KEY = "additionalProperties"
_ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY = "additionalPropertiesType"
_USE_TYPED_DICT_BACKPORT_TEMPLATE_DATA_KEY = "use_typeddict_backport"
_MODULE_NAME_INVALID_CHAR_PATTERN = re.compile(r"[^0-9a-zA-Z_]")
_MODULE_NAME_INVALID_CHAR_WITH_DOTS_PATTERN = re.compile(r"[^0-9a-zA-Z_.]")
_MAX_MISSING_CUSTOM_TEMPLATE_SUBDIRS = 128
_MAX_CUSTOM_TEMPLATE_SIGNATURES = 128
_MAX_CUSTOM_TEMPLATE_DEPENDENCIES = 128
_ORIGINAL_TEMPLATE_LOADER_MARKER = "__datamodel_code_generator_original_template_loader__"
_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY = "_nested_model_default_factory_order"
_NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_PATHS_KEY = "_nested_model_default_factory_recursive_paths"
_NESTED_MODEL_REQUIRED_CONSTRUCTOR_FIELDS_KEY = "_nested_model_required_constructor_fields"
_REQUIRED_INHERITED_DEFAULT_FACTORY_KEY = "_required_inherited_default_factory"
_RUNTIME_EXPRESSION_IMPORTS_FIELD_KEY = "_runtime_expression_imports"
_EXTRA_TEMPLATE_DATA_MAPPING_ERROR = "extra template data must be a dictionary"
_EXTRA_TEMPLATE_DATA_KEY_ERROR = "extra template data keys must be strings"
_DATACLASS_ARGUMENTS_MAPPING_ERROR = "dataclass_arguments must be a dictionary"
_DATACLASS_ARGUMENTS_KEY_ERROR = "dataclass_arguments keys must be strings"
_DATACLASS_ARGUMENT_NAMES: frozenset[str] = frozenset({
    "eq",
    "frozen",
    "init",
    "kw_only",
    "match_args",
    "order",
    "repr",
    "slots",
    "unsafe_hash",
    "weakref_slot",
})
_BUILTIN_TEMPLATE_INTERNAL_DATA_KEYS: frozenset[str] = frozenset({
    "class_body_lines",
    "config_items",
    "schema_runtime_validation",
    "schema_runtime_validation_base_class_name",
    "schema_runtime_validation_use_base",
    "sequence_base_class",
    "sequence_item_type",
    "sequence_slice_type",
    "_safe_config_items",
    "typed_dict_kwargs",
    "typed_dict_kwargs_suffix",
})
_RESOLVE_REFERENCE_ACTION_CAPABILITIES_MARKER = "__datamodel_code_generator_resolve_reference_action_capabilities__"
MroT = TypeVar("MroT")
_CustomTemplateSignature = bytes


@dataclass(frozen=True, slots=True)
class ResolveReferenceActionCapabilities:
    """Output-owned behavior used by parser resolve-action fast paths."""

    filter_forward_references: bool = False
    generated_formatter_safe: bool = False


_DEFAULT_RESOLVE_REFERENCE_ACTION_CAPABILITIES = ResolveReferenceActionCapabilities()


def declare_resolve_reference_action_capabilities(
    action: Callable[[Iterable[str]], str],
    *,
    filter_forward_references: bool = False,
    generated_formatter_safe: bool = False,
) -> Callable[[Iterable[str]], str]:
    """Attach immutable output-owned capabilities to a resolve action."""
    action.__dict__[_RESOLVE_REFERENCE_ACTION_CAPABILITIES_MARKER] = ResolveReferenceActionCapabilities(
        filter_forward_references=filter_forward_references,
        generated_formatter_safe=generated_formatter_safe,
    )
    return action


def get_resolve_reference_action_capabilities(action: object) -> ResolveReferenceActionCapabilities:
    """Return immutable output capabilities, failing closed for custom callables."""
    capabilities = getattr(action, _RESOLVE_REFERENCE_ACTION_CAPABILITIES_MARKER, None)
    return (
        capabilities
        if isinstance(capabilities, ResolveReferenceActionCapabilities)
        else _DEFAULT_RESOLVE_REFERENCE_ACTION_CAPABILITIES
    )


class _CustomTemplateDependencies:
    """Bounded file metadata observed by one custom-template loader tree."""

    __slots__ = ("directories", "incomplete", "overflow", "paths")

    def __init__(self) -> None:
        self.paths: dict[Path, tuple[int, ...] | None] = {}
        self.directories: dict[Path, _CustomTemplateSignature | None] = {}
        self.overflow = False
        self.incomplete = False

    def require_full_scan(self) -> None:
        """Capture external baselines before the first unobserved dependency is read."""
        if self.overflow:
            return
        self.overflow = True
        for directory in self.directories:
            self.directories[directory] = _get_custom_template_signature(directory)

    @property
    def needs_full_scan(self) -> bool:
        """Check the complete root when no bounded file inventory is available."""
        return self.overflow or not self.paths


class _MissingCustomTemplateState:
    """Bounded bookkeeping for mutable custom-template directories."""

    __slots__ = ("count", "dependencies", "lock", "overflow", "paths", "signatures")

    def __init__(self) -> None:
        self.paths: dict[Path, tuple[Path, ...]] = {}
        self.signatures: OrderedDict[Path, _CustomTemplateSignature] = OrderedDict()
        self.dependencies: dict[Path, _CustomTemplateDependencies] = {}
        self.count = 0
        self.overflow = False
        self.lock = RLock()


_missing_custom_template_state = _MissingCustomTemplateState()


@dataclass(frozen=True, slots=True)
class _TypingImportRequirements:
    union: bool = False
    optional: bool = False
    annotated: bool = False
    any_: bool = False

    def merge(self, other: _TypingImportRequirements) -> _TypingImportRequirements:
        return _TypingImportRequirements(
            union=self.union or other.union,
            optional=self.optional or other.optional,
            annotated=self.annotated or other.annotated,
            any_=self.any_ or other.any_,
        )

    def with_import_name(self, name: str) -> _TypingImportRequirements:
        match name:
            case IMPORT_UNION.import_:
                return replace(self, union=True)
            case IMPORT_OPTIONAL.import_:
                return replace(self, optional=True)
            case IMPORT_ANNOTATED.import_:
                return replace(self, annotated=True)
        return self

    def allows(self, import_: Import) -> bool:
        match import_:
            case _ if import_ == IMPORT_UNION:
                return self.union
            case _ if import_ == IMPORT_OPTIONAL:
                return self.optional
        return True

    @property
    def leading_imports(self) -> tuple[Import, ...]:
        return (IMPORT_ANY,) if self.any_ else ()

    @property
    def trailing_imports(self) -> tuple[Import, ...]:
        imports = []
        if self.union:
            imports.append(IMPORT_UNION)
        if self.optional:
            imports.append(IMPORT_OPTIONAL)
        if self.annotated:
            imports.append(IMPORT_ANNOTATED)
        return tuple(imports)


@lru_cache(maxsize=1024)
def _annotation_typing_import_names(annotation: str) -> frozenset[str]:
    if annotation in _TYPING_IMPORT_NAMES:
        return frozenset((annotation,))
    if annotation.isidentifier():
        return frozenset()

    try:
        tree = ast.parse(annotation, mode="eval")
    except SyntaxError:
        return frozenset()

    return frozenset(
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in _TYPING_IMPORT_NAMES
    )


class _EscapedDocstring(str):  # noqa: FURB189
    """Marker for values already escaped for a generated Python docstring."""

    __slots__ = ()


def escape_docstring(value: str | None) -> str | None:
    r"""Escape special characters in a docstring to prevent syntax errors.

    Handles:
    - Backslashes: `\\` -> `\\\\` (must be escaped first)
    - Triple quotes: `\"\"\"` -> `\\\"\\\"\\\"` (would terminate docstring)

    Args:
        value: The string to escape, or None.

    Returns:
        The escaped string, or None if input was None.
    """
    if value is None:
        return None
    if type(value) is _EscapedDocstring:
        return value
    value = _normalize_string(value)
    # Escape backslashes first, then triple quotes. Retain the original string
    # when no escaping is needed so custom-template fast paths stay allocation-free.
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return _EscapedDocstring(escaped) if escaped != value else value


def _ends_with_unescaped_quote(value: str) -> bool:
    """Return whether *value* ends with a double quote that is not escaped."""
    if not value.endswith('"'):
        return False

    backslash_count = 0
    for char in reversed(value[:-1]):
        if char != "\\":
            break
        backslash_count += 1
    return backslash_count % 2 == 0


def format_docstring(value: str | None, indent_spaces: int = 0, *, use_single_line_docstring: bool = False) -> str:
    """Format *value* as a docstring as per PEP 257.

    PEP 257 recommends that docstrings that can fit on one line should be formatted on a
    single line, for consistency and readability. When use_single_line_docstring is
    false, docstrings retain the historical multi-line formatting. It is assumed that
    the opening triple-quotes are indented appropriately in the template. If it's a
    multi-line docstring, each line including the closing triple-quotes will be indented
    as per indent_spaces.

    Args:
        value: docstring text
        indent_spaces: Spaces to indent for all lines after the opening triple-quotes
        use_single_line_docstring: Use one-line docstrings when possible

    Returns:
        Empty string when `value` is falsy; otherwise the docstring block.
    """
    if value is None:
        return ""
    if type(value) is not _EscapedDocstring:
        value = _normalize_string(value)
    if not value.strip():
        return ""

    escaped = escape_docstring(value) or ""

    if use_single_line_docstring and "\n" not in value and "\r" not in value:
        if _ends_with_unescaped_quote(escaped):
            escaped = f'{escaped[:-1]}\\"'

        return f'"""{escaped}"""'

    indent = max(indent_spaces, 0) * " "
    if indent:
        escaped = "\n".join(f"{indent}{line}" if line else "" for line in escaped.split("\n"))
        return f'"""\n{escaped}\n{indent}"""'
    return f'"""\n{escaped}\n"""'


def comment_safe(value: str | None) -> str | None:
    """Normalize line endings before rendering text in Python comments.

    Built-in union templates already prefix LF continuation lines with ``# ``.
    This helper converts CRLF and bare CR into LF so that existing template
    behavior keeps the whole description inside the comment block.
    """
    if value is None:
        return None
    value = _normalize_string(value)
    # Collapse CRLF before converting lone CR.
    return value.replace("\r\n", "\n").replace("\r", "\n")


def inline_comment_safe(value: str | None) -> str | None:
    """Make a value safe for a generated inline Python comment."""
    if value is None:
        return None
    safe_value = comment_safe(value) or ""
    return safe_value.replace("\v", "\n").replace("\f", "\n").replace("\n", "\n# ")


def _safe_extra_template_data(
    extra_template_data: dict[str, Any],
    internal_template_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return built-in template context without user-controlled Python fragments.

    ``extra_template_data`` remains deliberately unrestricted for custom Jinja
    templates. Built-in templates, however, have a small set of values that
    are rendered as Python syntax rather than data. Project code places those
    values in ``internal_template_data`` so user JSON cannot turn an extension
    value into generated code. Rejecting a reserved user key is intentional:
    silently ignoring a requested code-generation change would be surprising.
    """
    normalized_template_data = _normalize_template_data_keys(extra_template_data)
    if not normalized_template_data and not internal_template_data:
        return normalized_template_data
    internal_template_data = internal_template_data or {}
    comment = normalized_template_data.get("comment")
    if unsafe_keys := _BUILTIN_TEMPLATE_INTERNAL_DATA_KEYS.intersection(normalized_template_data):
        keys = ", ".join(sorted(unsafe_keys))
        msg = (
            f"{keys} is reserved for generator-owned built-in template data. "
            "Use --custom-template-dir to render trusted custom template data instead."
        )
        raise Error(msg)
    if isinstance(comment, str):
        comment = _normalize_string(comment)
    elif comment is not None:
        comment = _normalize_string(str(comment))
    if comment is None and not internal_template_data:
        return normalized_template_data

    safe_template_data = normalized_template_data
    if comment is not None:
        safe_template_data["comment"] = inline_comment_safe(comment)
    safe_template_data.update(internal_template_data)
    return safe_template_data


def _normalize_template_data_keys(extra_template_data: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize public template keys before inspecting or forwarding them.

    A ``str`` subclass may give a mapping a stateful ``__hash__`` implementation.
    Never use such a key for the reserved-key check or for ``**kwargs`` rendering:
    either could observe a different hash value.  Always return an ordinary
    dictionary, including for an empty subclass whose truthiness or ``items``
    protocol might lie to the renderer.
    """
    if not isinstance(extra_template_data, dict):
        raise Error(_EXTRA_TEMPLATE_DATA_MAPPING_ERROR)

    normalized_template_data: dict[str, Any] = {}
    for key, value in dict.items(extra_template_data):
        if not isinstance(key, str):
            raise Error(_EXTRA_TEMPLATE_DATA_KEY_ERROR)
        normalized_key = _normalize_string(key)
        if normalized_key in normalized_template_data:
            msg = f"extra template data contains duplicate key {normalized_key!r}"
            raise Error(msg)
        normalized_template_data[normalized_key] = value
    return normalized_template_data


def _safe_dataclass_arguments(dataclass_arguments: Any) -> dict[str, bool]:
    """Snapshot and validate decorator arguments consumed as Python syntax."""
    if not isinstance(dataclass_arguments, dict):
        raise Error(_DATACLASS_ARGUMENTS_MAPPING_ERROR)

    safe_arguments: dict[str, bool] = {}
    for key, value in dict.items(dataclass_arguments):
        if not isinstance(key, str):
            raise Error(_DATACLASS_ARGUMENTS_KEY_ERROR)
        normalized_key = _normalize_string(key)
        if normalized_key not in _DATACLASS_ARGUMENT_NAMES:
            msg = f"invalid dataclass argument {normalized_key!r}"
            raise Error(msg)
        if type(value) is not bool:
            msg = f"dataclass argument {normalized_key!r} must be a bool"
            raise Error(msg)
        if normalized_key in safe_arguments:
            msg = f"dataclass_arguments contains duplicate key {normalized_key!r}"
            raise Error(msg)
        safe_arguments[normalized_key] = value
    return safe_arguments


class _RenderedDataModelField:
    """Proxy a field with a pre-rendered docstring for built-in templates."""

    def __init__(self, field: DataModelFieldBase, docstring: str) -> None:
        field_values = self.__dict__
        field_values["_field"] = field
        field_values["docstring"] = docstring

    def __getattr__(self, name: str) -> Any:
        field_values = self.__dict__
        field = field_values["_field"]
        if (
            name in {"annotated", "field"}
            and (rendered_field_values := getattr(field, "_rendered_field_values", None)) is not None
        ):
            rendered_field, annotated = rendered_field_values()
            field_values["field"] = rendered_field
            field_values["annotated"] = annotated
            return field_values[name]
        value = getattr(field, name)
        field_values[name] = value
        return value


ALL_MODEL: str = "#all#"
GENERIC_BASE_CLASS_PATH: str = "#/__datamodel_code_generator__/generic_base_class__"
GENERIC_BASE_CLASS_NAME: str = "__generic_base_class__"


def _copy_all_model_data(source: dict[str, Any], target: dict[str, Any]) -> None:
    """Copy ALL_MODEL data to target dict, deep copying mutable containers only."""
    for key, value in source.items():
        target[key] = deepcopy(value) if isinstance(value, (dict, list, set)) else value


ConstraintsBaseT = TypeVar("ConstraintsBaseT", bound="ConstraintsBase")
DataModelFieldBaseT = TypeVar("DataModelFieldBaseT", bound="DataModelFieldBase")


class ConstraintsBase(_BaseModel):
    """Base class for field constraints (min/max, patterns, etc.)."""

    unique_items: Optional[bool] = Field(None, alias="uniqueItems")  # noqa: UP045
    _exclude_fields: ClassVar[set[str]] = {"has_constraints", "_exclude_unset_dump"}
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
        defer_build=True,
    )

    @cached_property
    def has_constraints(self) -> bool:
        """Check if any constraint values are set."""
        return any(v is not None for v in self.model_dump().values())

    @cached_property
    def _exclude_unset_dump(self) -> dict[str, Any]:
        """Cached model_dump(exclude_unset=True); constraints are immutable after creation. Read-only."""
        return self.model_dump(exclude_unset=True)

    @staticmethod
    def merge_constraints(a: ConstraintsBaseT | None, b: ConstraintsBaseT | None) -> ConstraintsBaseT | None:
        """Merge two constraint objects, with b taking precedence over a."""
        constraints_class = None
        if isinstance(a, ConstraintsBase):  # pragma: no cover
            root_type_field_constraints = {k: v for k, v in a.model_dump(by_alias=True).items() if v is not None}
            constraints_class = a.__class__
        else:
            root_type_field_constraints = {}  # pragma: no cover

        if isinstance(b, ConstraintsBase):  # pragma: no cover
            model_field_constraints = {k: v for k, v in b.model_dump(by_alias=True).items() if v is not None}
            constraints_class = constraints_class or b.__class__
        else:
            model_field_constraints = {}

        if constraints_class is None or not issubclass(constraints_class, ConstraintsBase):  # pragma: no cover
            return None

        return constraints_class.model_validate(
            {
                **root_type_field_constraints,
                **model_field_constraints,
            },
        )


class DataModelFieldBase(_BaseModel):  # noqa: PLR0904
    """Base class for model field representation and rendering."""

    PARSER_CONSTRUCTOR: ClassVar[Callable[..., DataModelFieldBase] | None] = None
    PREPARE_PYTHON_PATTERNS: ClassVar[
        Callable[[DataModelFieldBase, str, dict[str, PythonRuntimeExpression]], None] | None
    ] = None
    _FIELD_IMPORTS_CACHE_MAX_SIZE: ClassVar[int] = 4096
    _field_imports_cache: ClassVar[dict[tuple[Any, ...], tuple[Import, ...]]] = {}
    _SEMANTIC_CACHE_KEYS: ClassVar[tuple[str, ...]] = (
        "_computed_default_factory",
        "_self_reference_cache",
    )
    SUPPORTS_FIELD_CONSTRAINTS: ClassVar[bool] = False
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = False
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = False

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        defer_build=True,
    )

    name: Optional[str] = None  # noqa: UP045
    default: Optional[Any] = None  # noqa: UP045
    required: bool = False
    alias: Optional[str] = None  # noqa: UP045
    validation_aliases: Optional[list[str]] = None  # noqa: UP045  # Multiple aliases for Pydantic v2 AliasChoices
    serialization_alias: Optional[str] = None  # noqa: UP045
    data_type: DataType
    constraints: Any = None
    strip_default_none: bool = False
    nullable: Optional[bool] = None  # noqa: UP045
    parent: Optional[DataModel] = None  # noqa: UP045
    extras: dict[str, Any] = Field(default_factory=dict)
    use_annotated: bool = False
    use_serialize_as_any: bool = False
    has_default: bool = False
    use_field_description: bool = False
    use_field_description_example: bool = False
    use_inline_field_description: bool = False
    const: bool = False
    original_name: Optional[str] = None  # noqa: UP045
    use_default_kwarg: bool = False
    use_missing_sentinel: bool = False
    use_one_literal_as_default: bool = False
    _exclude_fields: ClassVar[set[str]] = {"parent"}
    _pass_fields: ClassVar[set[str]] = {"parent", "data_type"}
    can_have_extra_keys: ClassVar[bool] = True
    type_has_null: Optional[bool] = None  # noqa: UP045
    read_only: bool = False
    write_only: bool = False
    use_frozen_field: bool = False
    use_serialization_alias: bool = False
    use_default_factory_for_optional_nested_models: bool = False
    use_default_with_required: bool = False

    if not TYPE_CHECKING:  # pragma: no branch

        def __init__(self, **data: Any) -> None:
            """Initialize the field and set up parent relationships."""
            super().__init__(**data)
            if self.data_type.reference or self.data_type.data_types:
                self.data_type.parent = self
            self.process_const()

    def process_const(self) -> None:
        """Process const fields in subclasses."""

    @property
    def requires_immediate_forward_reference_resolution(self) -> bool:
        """Return whether this field's annotation is evaluated in the class body."""
        return bool(getattr(self, "is_pydantic_extra_field", False))

    def _process_const_as_literal(self) -> None:
        """Process const values by converting to literal type. Used by subclasses."""
        if (const := self.extras.get("const", UNDEFINED)) is UNDEFINED:
            return
        self.const = True

        match const:
            case None:
                self.nullable = False
                self.replace_data_type(self.data_type.__class__(type=NONE), clear_old_parent=False)
                return
            case bool() | int() | str():
                self.replace_data_type(self.data_type.__class__(literals=[const]), clear_old_parent=False)
            case _:
                self.nullable = False
                return

        # A const is only a generated Python default when the field would carry one anyway:
        # a required field (whose default the renderer suppresses) or a schema that also
        # defines a default. An optional const without a schema default follows the normal
        # optional path (nullable + ``None`` default) so an omitted field stays unset rather
        # than being silently filled with the const value.
        if not (self.required or self.has_default or self.default is not None):
            return
        self.nullable = False
        if self.default is None and not self.has_default:
            self.default = const

    def should_strip_default_none(self, *, keep_optional: bool = False) -> bool:
        """Return whether an actual None default should be omitted."""
        match (self.strip_default_none, keep_optional and self.data_type.is_optional):
            case (False, _) | (_, True):
                return False

        return self.default is None

    def self_reference(self) -> bool:
        """Check if field references its parent model.

        Result is cached until a GenerationStore-managed semantic mutation.
        Uses __dict__ for caching to avoid Pydantic-managed field assignment.
        """
        if "_self_reference_cache" in self.__dict__:
            return self.__dict__["_self_reference_cache"]
        if self.parent is None or not self.parent.reference:  # pragma: no cover
            self.__dict__["_self_reference_cache"] = False
            return False
        result = self.parent.reference.path in {d.reference.path for d in self.data_type.all_data_types if d.reference}
        self.__dict__["_self_reference_cache"] = result
        return result

    @property
    def _use_union_operator(self) -> bool:
        """Get effective use_union_operator considering parent model's forward reference."""
        if self.parent and self.parent.has_forward_reference:
            return False
        return self.data_type.use_union_operator

    @property
    def type_hint(self) -> str:
        """Get the type hint string for this field, including nullability."""
        return self._type_hint_from_data_type(self.data_type)

    def _type_hint_from_data_type(self, data_type: DataType) -> str:  # noqa: PLR0911
        """Get the type hint string for a field data type, including nullability."""
        type_hint = data_type.type_hint

        if not type_hint:
            return NONE
        if self.has_default_factory or (data_type.is_optional and data_type.type != ANY):
            return type_hint
        if self.nullable is not None:
            if self.nullable:
                return get_optional_type(type_hint, self._use_union_operator)
            return type_hint
        if self.required:
            if self.type_has_null:
                return get_optional_type(type_hint, self._use_union_operator)
            return type_hint
        if self.fall_back_to_nullable:
            return get_optional_type(type_hint, self._use_union_operator)
        return type_hint

    @property
    def base_type_hint(self) -> str:
        """Get the base type hint without constrained type kwargs.

        This returns the type without kwargs (e.g., 'str' instead of 'constr(pattern=...)').
        Used in RootModel generics when regex_engine config is needed for lookaround patterns.
        """
        base_hint = self.data_type.base_type_hint

        if not base_hint:  # pragma: no cover
            return NONE

        needs_optional = (
            (self.nullable is True)
            or (self.required and self.type_has_null)
            or (self.nullable is None and not self.required and self.fall_back_to_nullable)
        )
        skip_optional = (
            self.has_default_factory
            or (self.data_type.is_optional and self.data_type.type != ANY)
            or (self.nullable is False)
        )

        if needs_optional and not skip_optional:  # pragma: no cover
            return get_optional_type(base_hint, self._use_union_operator)
        return base_hint

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get all imports required for this field's type hint."""
        return self._collect_field_imports(needs_annotated=self.use_annotated and self.needs_annotated_import)

    @property
    def runtime_expression_imports(self) -> tuple[Import, ...]:
        """Return parser-registered nested runtime expression imports without scanning defaults."""
        return self.__dict__.get(_RUNTIME_EXPRESSION_IMPORTS_FIELD_KEY, ())

    def _set_runtime_expression_imports(self, imports: tuple[Import, ...]) -> None:
        """Register parser-owned nested default imports once at their producer boundary."""
        if imports:
            self.__dict__[_RUNTIME_EXPRESSION_IMPORTS_FIELD_KEY] = imports
            return
        self.__dict__.pop(_RUNTIME_EXPRESSION_IMPORTS_FIELD_KEY, None)

    def _collect_field_imports(
        self,
        *,
        needs_annotated: bool,
        data_type: DataType | None = None,
    ) -> tuple[Import, ...]:
        """Collect type-hint imports; needs_annotated is passed in so subclasses can precompute it."""
        data_type = data_type or self.data_type
        if self._can_collect_imports_without_type_hint(needs_annotated=needs_annotated, data_type=data_type):
            return self._collect_field_imports_without_type_hint(data_type=data_type)

        self._normalize_data_type_import_state(data_type)
        cache_key = self._field_imports_cache_key(needs_annotated=needs_annotated, data_type=data_type)
        if (cached_imports := self._field_imports_cache.get(cache_key)) is not None:
            return cached_imports
        imports = self._collect_field_imports_uncached(needs_annotated=needs_annotated, data_type=data_type)
        self._set_cached_field_imports(cache_key, imports)
        return imports

    def _collect_field_imports_without_type_hint(self, *, data_type: DataType) -> tuple[Import, ...]:
        needs_optional = self._needs_optional_import_without_type_hint(data_type=data_type)
        if self._can_skip_data_type_imports(data_type=data_type):
            return (IMPORT_OPTIONAL,) if needs_optional else ()

        imports = tuple(data_type.all_imports)
        if needs_optional and IMPORT_OPTIONAL not in imports:
            return chain_as_tuple(imports, (IMPORT_OPTIONAL,))
        return imports

    def _collect_field_imports_uncached(
        self,
        *,
        needs_annotated: bool,
        data_type: DataType,
    ) -> tuple[Import, ...]:
        """Collect field imports after import-affecting DataType normalization."""
        requirements = self._typing_import_requirements(needs_annotated=needs_annotated, data_type=data_type)
        imports = tuple(data_type.all_imports)
        if requirements.any_ and IMPORT_ANY not in imports:
            imports = chain_as_tuple(requirements.leading_imports, imports)

        filtered_imports = tuple(import_ for import_ in imports if requirements.allows(import_))
        missing_imports = tuple(import_ for import_ in requirements.trailing_imports if import_ not in filtered_imports)
        if not missing_imports:
            return filtered_imports
        return chain_as_tuple(filtered_imports, missing_imports)

    def _normalize_data_type_import_state(self, data_type: DataType) -> None:
        """Apply import-relevant DataType mutations before cache lookup."""
        if data_type.reference:
            data_type._apply_nullable_from_reference()  # noqa: SLF001

        for child in data_type.data_types:
            self._normalize_data_type_import_state(child)
        if data_type.dict_key:
            self._normalize_data_type_import_state(data_type.dict_key)

        if not data_type.is_union or data_type.preserve_union_member_order:
            return
        if any(self._data_type_renders_none(child) for child in data_type.data_types):
            data_type.is_optional = True

    @staticmethod
    def _import_key(import_: Import | None) -> tuple[str, str | None, str | None, str | None] | None:
        if import_ is None:
            return None
        return (import_.import_, import_.from_, import_.alias, import_.reference_path)

    @staticmethod
    def _reference_source_import_key(reference: Reference | None) -> tuple[bool, bool] | None:
        if reference is None:
            return None
        source = reference.source
        return (bool(getattr(source, "nullable", False)), bool(getattr(source, "is_alias", False)))

    def _field_imports_cache_key(self, *, needs_annotated: bool, data_type: DataType) -> tuple[Any, ...]:
        return (
            self.__class__,
            needs_annotated,
            self.nullable,
            self.required,
            self.type_has_null,
            self.has_default_factory,
            self.fall_back_to_nullable,
            self._use_union_operator,
            bool(self.parent and self.parent.has_forward_reference),
            self._data_type_import_key(data_type),
        )

    def _set_cached_field_imports(self, cache_key: tuple[Any, ...], imports: tuple[Import, ...]) -> None:
        cache = self._field_imports_cache
        if len(cache) >= self._FIELD_IMPORTS_CACHE_MAX_SIZE:
            del cache[next(iter(cache))]
        cache[cache_key] = imports

    def _can_collect_imports_without_type_hint(
        self,
        *,
        needs_annotated: bool,
        data_type: DataType | None = None,
    ) -> bool:
        """Return whether imports can be collected without rendering the type hint."""
        if needs_annotated:
            return False
        if self.parent and self.parent.has_forward_reference:
            return False

        data_type = data_type or self.data_type
        if data_type.use_serialize_as_any:
            return False

        if self._has_explicit_typing_import_requirements(data_type):
            return False

        return not (
            data_type.data_types
            or data_type.dict_key
            or data_type.literals
            or data_type.enum_member_literals
            or (data_type.is_optional and data_type.type == ANY)
        )

    @staticmethod
    def _has_explicit_typing_import_requirements(data_type: DataType) -> bool:
        if data_type.python_type:
            from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
                iter_python_type_expr_names,
            )

            if any(
                name in _TYPING_IMPORT_NAMES for name in iter_python_type_expr_names(data_type.python_type.expression)
            ):
                return True
        annotations = (data_type.alias,) if data_type.python_type else (data_type.alias, data_type.type)
        for annotation in annotations:
            if annotation and (names := _annotation_typing_import_names(annotation)):
                return bool(names)
        return False

    def _typing_import_requirements(
        self,
        *,
        needs_annotated: bool,
        data_type: DataType | None = None,
    ) -> _TypingImportRequirements:
        data_type = data_type or self.data_type
        requirements = self._data_type_typing_import_requirements(data_type)
        if needs_annotated:
            requirements = requirements.with_import_name(IMPORT_ANNOTATED.import_)
        if not requirements.optional and self._needs_field_optional_import_from_structure(data_type=data_type):
            requirements = requirements.with_import_name(IMPORT_OPTIONAL.import_)
        return requirements

    def _data_type_typing_import_requirements(self, data_type: DataType) -> _TypingImportRequirements:
        requirements = self._explicit_typing_import_requirements(data_type)

        if data_type.is_union:
            requirements = requirements.merge(self._union_typing_import_requirements(data_type))
        elif len(data_type.data_types) == 1:
            requirements = requirements.merge(self._data_type_typing_import_requirements(data_type.data_types[0]))

        if data_type.dict_key:
            requirements = requirements.merge(self._data_type_typing_import_requirements(data_type.dict_key))

        if data_type.discriminator:
            requirements = requirements.with_import_name(IMPORT_ANNOTATED.import_)
        if self._data_type_needs_optional_import(data_type):
            requirements = requirements.with_import_name(IMPORT_OPTIONAL.import_)
        return requirements

    def _union_typing_import_requirements(self, data_type: DataType) -> _TypingImportRequirements:
        requirements = _TypingImportRequirements()
        branch_keys: set[tuple[Any, ...]] = set()
        has_none = False
        for child in data_type.data_types:
            requirements = requirements.merge(self._data_type_typing_import_requirements(child))
            if self._data_type_renders_none(child):
                has_none = True
                continue
            branch_keys.add(self._data_type_import_key(child))

        if has_none and not data_type.preserve_union_member_order and not data_type.use_union_operator:
            requirements = requirements.with_import_name(IMPORT_OPTIONAL.import_)
        ordered_none_branch_count = int(data_type.preserve_union_member_order and has_none)
        if not branch_keys and data_type.data_types and not ordered_none_branch_count:
            requirements = replace(requirements, any_=True)
        if len(branch_keys) + ordered_none_branch_count > 1 and not data_type.use_union_operator:
            requirements = requirements.with_import_name(IMPORT_UNION.import_)
        return requirements

    @staticmethod
    def _explicit_typing_import_requirements(data_type: DataType) -> _TypingImportRequirements:
        requirements = _TypingImportRequirements()
        if data_type.python_type:
            from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
                iter_python_type_expr_names,
            )

            for name in iter_python_type_expr_names(data_type.python_type.expression):
                requirements = requirements.with_import_name(name)
        annotations = (data_type.alias,) if data_type.python_type else (data_type.alias, data_type.type)
        for annotation in annotations:
            if not annotation:
                continue
            for name in _annotation_typing_import_names(annotation):
                requirements = requirements.with_import_name(name)
        return requirements

    def _data_type_needs_optional_import(self, data_type: DataType) -> bool:
        if data_type.use_union_operator or data_type.type == ANY:
            return False
        return data_type.is_optional and self._data_type_renders_importable_hint(data_type)

    def _needs_field_optional_import_from_structure(self, *, data_type: DataType | None = None) -> bool:
        data_type = data_type or self.data_type
        if (
            self._use_union_operator
            or self.has_default_factory
            or not self._data_type_renders_importable_hint(data_type)
        ):
            return False
        if data_type.is_optional and data_type.type != ANY:
            return False

        match self.nullable:
            case True:
                result = True
            case False:
                result = False
            case None if self.required:
                result = bool(self.type_has_null)
            case None:
                result = bool(self.fall_back_to_nullable)
            case _:
                result = False  # pragma: no cover
        return result

    def _data_type_renders_importable_hint(self, data_type: DataType) -> bool:
        if self._data_type_renders_none(data_type):
            return False
        return self._data_type_has_renderable_structure(data_type)

    @staticmethod
    def _data_type_has_renderable_structure(data_type: DataType) -> bool:
        if data_type.alias or data_type.type or data_type.reference:
            return True
        return bool(
            data_type.data_types
            or data_type.dict_key
            or data_type.literals
            or data_type.enum_member_literals
            or data_type.is_dict
            or data_type.is_list
            or data_type.is_set
            or data_type.is_frozen_set
            or data_type.is_mapping
            or data_type.is_sequence
            or data_type.is_tuple
        )

    def _data_type_renders_none(self, data_type: DataType) -> bool:
        if any((
            data_type.is_dict,
            data_type.is_list,
            data_type.is_set,
            data_type.is_frozen_set,
            data_type.is_mapping,
            data_type.is_sequence,
            data_type.is_tuple,
        )):
            return False
        if data_type.alias or data_type.reference or data_type.literals or data_type.enum_member_literals:
            return False
        if data_type.type == NONE:
            return not data_type.data_types
        return len(data_type.data_types) == 1 and self._data_type_renders_none(data_type.data_types[0])

    def _data_type_import_key(self, data_type: DataType) -> tuple[Any, ...]:
        return (
            data_type.__class__,
            data_type.alias,
            data_type.type,
            data_type.python_type,
            self._import_key(data_type.import_),
            data_type.reference.path if data_type.reference else None,
            self._reference_source_import_key(data_type.reference),
            tuple(self._data_type_import_key(child) for child in data_type.data_types),
            self._data_type_import_key(data_type.dict_key) if data_type.dict_key else None,
            tuple(data_type.literals),
            tuple(data_type.enum_member_literals),
            to_hashable(data_type.kwargs),
            data_type.strict,
            data_type.is_custom_type,
            data_type.is_optional,
            data_type.is_func,
            data_type.is_dict,
            data_type.is_list,
            data_type.is_set,
            data_type.is_frozen_set,
            data_type.is_mapping,
            data_type.is_sequence,
            data_type.is_tuple,
            data_type.tuple_item_count,
            data_type.use_standard_collections,
            data_type.use_generic_container,
            data_type.use_union_operator,
            data_type.preserve_union_member_order,
            data_type.use_serialize_as_any,
            data_type.discriminator,
        )

    def _can_skip_data_type_imports(self, *, data_type: DataType | None = None) -> bool:
        """Return whether the simple DataType cannot contribute imports."""
        data_type = data_type or self.data_type
        return not (
            data_type.import_
            or data_type.python_type
            or data_type.kwargs
            or data_type.is_optional
            or data_type.is_dict
            or data_type.is_list
            or data_type.is_set
            or data_type.is_frozen_set
            or data_type.is_mapping
            or data_type.is_sequence
            or data_type.is_tuple
            or data_type.use_generic_container
        )

    def _needs_optional_import_without_type_hint(self, *, data_type: DataType | None = None) -> bool:
        """Return whether the field-level nullable wrapper needs typing.Optional."""
        data_type = data_type or self.data_type
        if (
            self._use_union_operator
            or not self._has_renderable_data_type_without_type_hint(data_type=data_type)
            or self.has_default_factory
            or (data_type.is_optional and data_type.type != ANY)
        ):
            return False

        if self._reference_source_nullable_without_type_hint(data_type=data_type):
            return True

        match self.nullable:
            case True:
                return True
            case False:
                return False
            case None:
                return bool(self.type_has_null) if self.required else bool(self.fall_back_to_nullable)
        return False  # pragma: no cover

    def _reference_source_nullable_without_type_hint(self, *, data_type: DataType | None = None) -> bool:
        """Return whether a referenced non-alias model should make this type optional."""
        data_type = data_type or self.data_type
        reference = data_type.reference
        if reference is None:
            return False
        source = reference.source
        return not getattr(source, "is_alias", False) and bool(getattr(source, "nullable", False))

    def _has_renderable_data_type_without_type_hint(self, *, data_type: DataType | None = None) -> bool:
        """Return whether this simple DataType renders to something other than None."""
        data_type = data_type or self.data_type
        return bool(
            data_type.alias
            or data_type.type
            or data_type.reference
            or data_type.is_dict
            or data_type.is_list
            or data_type.is_set
            or data_type.is_frozen_set
            or data_type.is_mapping
            or data_type.is_sequence
            or data_type.is_tuple
        )

    @property
    def docstring(self) -> str | None:
        """Get the docstring for this field from its description and/or example."""
        parts = []

        if self.use_field_description:
            description = self.extras.get("description")
            if description is not None:
                parts.append(description)
        elif self.use_inline_field_description and self.use_field_description_example:
            description = self.extras.get("description")
            if description is not None and "\n" in description:
                parts.append(description)

        if self.use_field_description_example:
            example = self.extras.get("example")
            examples = self.extras.get("examples")

            if examples and isinstance(examples, list) and len(examples) > 1:
                examples_str = "\n".join(f"- {e!r}" for e in examples)
                parts.append(f"Examples:\n{examples_str}")
            elif example is not None:
                parts.append(f"Example: {example!r}")
            elif examples and isinstance(examples, list) and len(examples) == 1:  # pragma: no branch
                parts.append(f"Example: {examples[0]!r}")

        if parts:
            return "\n\n".join(parts)

        if self.use_inline_field_description:
            description = self.extras.get("description")
            if description is not None and "\n" in description:
                return description

        return None

    @property
    def inline_field_docstring(self) -> str | None:
        """Get the inline docstring for this field if single-line."""
        if self.use_inline_field_description:
            description = self.extras.get("description", None)
            if description is not None and "\n" not in description:
                escaped = escape_docstring(description)
                return f'"""{escaped}"""'

        return None

    @property
    def unresolved_types(self) -> frozenset[str]:
        """Get the set of unresolved type references."""
        return self.data_type.unresolved_types

    @property
    def field(self) -> str | None:
        """For backwards compatibility."""
        return None

    def force_field_assignment(self) -> None:
        """Render an explicit required-field assignment without changing its semantics."""
        if self.__dict__.get("_forced_field_assignment") is True:
            return
        self.__dict__["_forced_field_assignment"] = True
        self._invalidate_parent_render_caches()

    def _invalidate_parent_render_caches(self) -> None:
        """Clear parent render caches through current or legacy model hooks."""
        if (parent := self.parent) is None:
            return
        if invalidate_render_caches := getattr(parent, "invalidate_render_caches", None):
            invalidate_render_caches()
            return
        parent.clear_imports_cache()

    def mark_as_keyword_only(self) -> None:
        """Exclude this field from positional constructor ordering."""
        self.extras["kw_only"] = True

    @property
    def constructor_keyword_only(self) -> bool | None:
        """Return the field-level keyword-only policy, if explicitly configured."""
        return self.extras.get("kw_only")

    @property
    def is_class_var(self) -> bool:
        """Return whether this output field is a class variable."""
        return self.extras.get("is_classvar") is True

    def enable_structured_default_validation(self) -> bool:  # noqa: PLR6301
        """Enable output-specific validation for a structured default.

        The neutral field policy intentionally does nothing. Output backends that
        require validation at construction time can override this hook.
        """
        return False

    def _get_constructor_default_info(self) -> tuple[bool, bool]:
        """Return neutral constructor-default semantics for this field."""
        return _get_field_default_info(self)

    def _has_default_for_nested_model_factory(self) -> bool:
        """Return whether the emitted constructor supplies this field's default."""
        return self._get_constructor_default_info()[0]

    @property
    def _has_forced_field_assignment(self) -> bool:
        """Return whether an explicit required-field assignment must be rendered."""
        return self.__dict__.get("_forced_field_assignment", False)

    @property
    def method(self) -> str | None:
        """Get the method string for this field, if any."""
        return None

    @property
    def represented_default(self) -> str:
        """Get the repr() string of the default value."""
        return represent_python_value(self.default)

    @property
    def annotated(self) -> str | None:
        """Get the Annotated type hint content, if any."""
        return None

    @property
    def needs_annotated_import(self) -> bool:
        """Check if this field requires the Annotated import."""
        return bool(self.annotated)

    @property
    def needs_meta_import(self) -> bool:  # pragma: no cover
        """Check if this field requires the Meta import (msgspec only)."""
        return False

    @property
    def has_default_factory(self) -> bool:
        """Check if this field has a default_factory."""
        return "default_factory" in self.extras

    @property
    def fall_back_to_nullable(self) -> bool:
        """Check if optional fields should be nullable by default."""
        return True

    def copy_deep(self) -> Self:
        """Create a deep copy of this field to avoid mutating the original."""
        copied = self.model_copy()
        copied.parent = None
        copied.extras = deepcopy(self.extras)
        copied.data_type = self.data_type.model_copy()
        if self.data_type.data_types:
            copied.data_type.data_types = [dt.model_copy() for dt in self.data_type.data_types]
        if self.data_type.dict_key:
            copied.data_type.dict_key = self.data_type.dict_key.model_copy()
        copied.invalidate_semantic_caches()
        return copied

    def invalidate_semantic_caches(self, *, invalidate_parent: bool = True) -> None:
        """Clear field caches derived from mutable type and parent semantics."""
        field_values = self.__dict__
        for key in self._SEMANTIC_CACHE_KEYS:
            field_values.pop(key, None)
        if not invalidate_parent:
            return
        self._invalidate_parent_render_caches()

    def replace_data_type(self, new_data_type: DataType, *, clear_old_parent: bool = True) -> None:
        """Replace data_type and update parent relationships.

        Args:
            new_data_type: The new DataType to set.
            clear_old_parent: If True, clear the old data_type's parent reference.
                Set to False when the old data_type may be referenced elsewhere.
        """
        if self.data_type.parent is self and clear_old_parent:
            self.data_type.swap_with(new_data_type)
        else:
            self.data_type = new_data_type
            new_data_type.parent = self
        self.invalidate_semantic_caches()


def _nested_model_has_required_constructor_fields(source: DataModel) -> bool:
    """Identify required constructor fields without executing opaque custom model code."""
    inherited = any(base.reference for base in source.base_classes)
    models = linearize_data_models([source]) if inherited else (source,)
    for model in models:
        if (
            model.decorators or (model._custom_template_dir is not None and model._uses_custom_root_template)  # noqa: SLF001
        ):
            return False
        if (
            (custom_base_class := model.custom_base_class) is not None
            and custom_base_class not in (model.BASE_CLASS, [model.BASE_CLASS])
            and any(not base.reference for base in model.base_classes)
        ):
            return False
    fields = get_effective_fields(source) if inherited else source.fields
    return any(
        (field.required or field.should_strip_default_none())
        and (source.USES_DATACLASS_ARGUMENTS or not field.is_class_var)
        and source.FIELD_PARTICIPATES_IN_CONSTRUCTOR(field)
        and (
            (field.required and not field.use_default_with_required)
            or (
                (field.default is UNDEFINED or field.default is None)
                and not field._has_default_for_nested_model_factory()  # noqa: SLF001  # output-owned default policy
            )
        )
        for field in fields
    )


def _nested_model_default_factory(field: DataModelFieldBase, model_cls: type[DataModel]) -> str | None:
    """Return the nested model name usable as a default_factory for optional fields."""
    for data_type in field.data_type.data_types or (field.data_type,):
        if data_type.is_dict:
            continue
        if data_type.reference and isinstance(source := data_type.reference.source, model_cls):
            if field.parent is not None and source.path in field.parent.__dict__.get(
                _NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_PATHS_KEY, ()
            ):
                return None
            if (required_fields := source.__dict__.get(_NESTED_MODEL_REQUIRED_CONSTRUCTOR_FIELDS_KEY)) is None:
                required_fields = _nested_model_has_required_constructor_fields(source)
                source.__dict__[_NESTED_MODEL_REQUIRED_CONSTRUCTOR_FIELDS_KEY] = required_fields
            if required_fields:
                return None
            factory_name = data_type.alias or source.class_name
            parent_order = (
                field.parent.__dict__.get(_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY) if field.parent is not None else None
            )
            source_order = source.__dict__.get(_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY)
            match (parent_order, source_order):
                case ((parent_module, parent_index), (source_module, source_index)) if (
                    parent_module == source_module and source_index >= parent_index
                ):
                    return f"lambda: {source.class_name}()"
                case _:
                    return factory_name
    return None


def _set_nested_model_default_factory_order(
    models: list[DataModel],
    module_index: int,
    recursive_paths_by_model: Mapping[str, frozenset[str]],
) -> None:
    """Record final declaration order and recursive paths for nested model factories."""
    for model_index, model in enumerate(models):
        model.__dict__[_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY] = (module_index, model_index)
        if recursive_paths := recursive_paths_by_model.get(model.path):
            model.__dict__[_NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_PATHS_KEY] = recursive_paths


def _build_environment(loader: Any, *, auto_reload: bool = True) -> Environment:
    """Build a Jinja environment with built-in filters."""
    from jinja2 import Environment, select_autoescape  # noqa: PLC0415

    env = Environment(
        loader=loader,
        autoescape=select_autoescape(["html", "xml"]),
        auto_reload=auto_reload,
    )
    env.filters["escape_docstring"] = escape_docstring  # For old custom templates
    env.filters["format_docstring"] = format_docstring
    env.filters["repr"] = repr
    return env


@lru_cache(maxsize=16)
def _get_environment(template_subdir: Path, custom_template_dir: Path | None) -> Environment:
    """Get or create a cached Jinja2 Environment for the given directories."""
    from jinja2 import ChoiceLoader, FileSystemLoader  # noqa: PLC0415

    loaders: list[FileSystemLoader] = []
    has_custom_loader = False

    if custom_template_dir is not None:
        custom_dir = custom_template_dir / template_subdir
        if cached_path_exists(custom_dir):
            loaders.append(_custom_template_loader(custom_template_dir, custom_dir))
            has_custom_loader = True
        else:
            _remember_missing_custom_template_subdir(custom_template_dir, custom_dir)

    loaders.append(FileSystemLoader(str(TEMPLATE_DIR / template_subdir)))

    loader: ChoiceLoader | FileSystemLoader = ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0]
    return _build_environment(loader, auto_reload=has_custom_loader)


@lru_cache
def _get_template_with_custom_dir(
    template_file_path: Path,
    custom_template_dir: Path | None,
    template_adapter: Callable[[Template], Template] | None = None,
) -> Template:
    """Load and cache a Jinja2 template with optional custom directory support.

    When custom_template_dir is provided, templates are searched in this order:
    1. custom_template_dir/<template_subdir>/
    2. TEMPLATE_DIR/<template_subdir>/ (fallback)

    This allows users to override individual templates (including included ones)
    while keeping other templates from the default directory.
    """
    template_subdir = template_file_path.parent
    environment = _get_environment(template_subdir, custom_template_dir)
    template = environment.get_template(template_file_path.name)
    return (
        _apply_custom_template_adapter(template, template_adapter, custom_template_dir)
        if template_adapter is not None
        else template
    )


def _uses_original_template_loader(adapter: Callable[[Template], Template]) -> Callable[[Template], Template]:
    """Declare that an internal adapter preserves the observed Jinja loader."""
    adapter.__dict__[_ORIGINAL_TEMPLATE_LOADER_MARKER] = True
    return adapter


def _apply_custom_template_adapter(
    template: Template, adapter: Callable[[Template], Template], custom_template_dir: Path | None
) -> Template:
    """Use the full-root fallback for adapters with unobserved dependencies."""
    if custom_template_dir is not None and not getattr(adapter, _ORIGINAL_TEMPLATE_LOADER_MARKER, False):
        with _missing_custom_template_state.lock:
            if dependencies := _missing_custom_template_state.dependencies.get(custom_template_dir):
                dependencies.require_full_scan()
    return adapter(template)


def _clear_custom_template_render_caches() -> None:
    """Clear cached loaders, environments, and templates after a source change."""
    cached_path_exists.cache_clear()
    _get_environment.cache_clear()
    _get_template_with_custom_dir.cache_clear()
    _get_environment_with_absolute_path.cache_clear()
    _get_template_with_absolute_path.cache_clear()


def _clear_custom_template_caches() -> None:
    """Clear mutable custom-template path, environment, and template caches."""
    with _missing_custom_template_state.lock:
        _clear_custom_template_render_caches()
        _missing_custom_template_state.paths.clear()
        _missing_custom_template_state.signatures.clear()
        _missing_custom_template_state.dependencies.clear()
        _missing_custom_template_state.count = 0
        _missing_custom_template_state.overflow = False


def _remember_missing_custom_template_subdir(custom_template_dir: Path, custom_subdir: Path) -> None:
    """Track a missing custom subdirectory while keeping retained state bounded."""
    _remember_custom_template_dependency(custom_template_dir, custom_subdir)
    with _missing_custom_template_state.lock:
        if _missing_custom_template_state.overflow:
            return
        missing_subdirs = _missing_custom_template_state.paths.get(custom_template_dir, ())
        if custom_subdir in missing_subdirs:
            return
        if _missing_custom_template_state.count >= _MAX_MISSING_CUSTOM_TEMPLATE_SUBDIRS:
            _missing_custom_template_state.overflow = True
            return
        _missing_custom_template_state.paths[custom_template_dir] = (*missing_subdirs, custom_subdir)
        _missing_custom_template_state.count += 1


def _visit_custom_template_directory(
    directory_path: Path,
    relative_directory: str,
    entries: list[tuple[str, ...]],
    visited_directories: set[tuple[int, int]],
) -> bool:
    try:
        if directory_path.is_symlink():
            link_target = directory_path.readlink()
            entries.append(("directory-link", relative_directory or ".", link_target.as_posix()))
        directory_stat = directory_path.stat()
    except OSError:
        return False
    directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
    if directory_identity in visited_directories:
        return False
    visited_directories.add(directory_identity)
    return True


def _scan_custom_template_directory(
    directory_path: Path,
    relative_directory: str,
    entries: list[tuple[str, ...]],
    pending_directories: list[tuple[Path, str]],
) -> None:
    from os import scandir  # noqa: PLC0415
    from stat import S_ISDIR, S_ISREG  # noqa: PLC0415

    mode_checks = (S_ISDIR, S_ISREG)
    try:
        with scandir(directory_path) as directory_entries:
            for entry in directory_entries:
                relative_path = f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                _scan_custom_template_entry(entry, relative_path, entries, pending_directories, mode_checks)
    except OSError:
        return


def _scan_custom_template_entry(
    entry: Any,
    relative_path: str,
    entries: list[tuple[str, ...]],
    pending_directories: list[tuple[Path, str]],
    mode_checks: tuple[Callable[[int], bool], Callable[[int], bool]],
) -> None:
    is_directory, is_regular = mode_checks
    if entry.is_symlink():
        try:
            link_target = Path(entry.path).readlink()
            if entry.is_dir():
                pending_directories.append((Path(entry.path), relative_path))
                return
        except OSError:
            return
        entries.append(("file-link", relative_path, link_target.as_posix()))
    try:
        stat_result = entry.stat()
    except OSError:
        return
    if is_directory(stat_result.st_mode):
        pending_directories.append((Path(entry.path), relative_path))
    elif is_regular(stat_result.st_mode):
        entries.append((
            relative_path,
            str(stat_result.st_mtime_ns),
            str(stat_result.st_ctime_ns),
            str(stat_result.st_size),
        ))


def _get_custom_template_signature(custom_template_dir: Path) -> _CustomTemplateSignature:
    """Return a stable metadata signature for all files a custom template can include."""
    from hashlib import sha256  # noqa: PLC0415

    entries: list[tuple[str, ...]] = []
    visited_directories: set[tuple[int, int]] = set()
    pending_directories = [(custom_template_dir, "")]
    while pending_directories:
        directory_path, relative_directory = pending_directories.pop()
        if _visit_custom_template_directory(directory_path, relative_directory, entries, visited_directories):
            _scan_custom_template_directory(directory_path, relative_directory, entries, pending_directories)
    digest = sha256()
    for entry in sorted(entries):
        for value in entry:
            digest.update(value.encode("utf-8", "surrogateescape"))
            digest.update(b"\0")
    return digest.digest()


def _custom_template_path_signature(path: Path) -> tuple[int, ...] | None:
    """Check file identity as well as timestamps, including resolved symlink targets."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _remember_custom_template_dependency(custom_template_dir: Path, path: Path) -> None:
    """Record successful and failed lookups without growing with unused files."""
    with _missing_custom_template_state.lock:
        dependencies = _missing_custom_template_state.dependencies.get(custom_template_dir)
        if dependencies is None or dependencies.overflow or path in dependencies.paths:
            return
        if len(dependencies.paths) >= _MAX_CUSTOM_TEMPLATE_DEPENDENCIES:
            dependencies.require_full_scan()
            return
        dependencies.paths[path] = _custom_template_path_signature(path)


def _remember_custom_template_directory(custom_template_dir: Path, directory: Path) -> None:
    """Include resolved legacy loader roots in the bounded full-scan fallback."""
    with _missing_custom_template_state.lock:
        dependencies = _missing_custom_template_state.dependencies.get(custom_template_dir)
        if dependencies is None or dependencies.incomplete or directory in dependencies.directories:
            return
        if directory.resolve().is_relative_to(custom_template_dir.resolve()):
            return
        if len(dependencies.directories) >= _MAX_CUSTOM_TEMPLATE_DEPENDENCIES:
            dependencies.incomplete = True
            return
        dependencies.directories[directory] = (
            _get_custom_template_signature(directory) if dependencies.overflow else None
        )


def _custom_template_loader(custom_template_dir: Path, directory: Path) -> Any:
    from datamodel_code_generator.model._template_dependencies import DependencyTrackingLoader  # noqa: PLC0415

    _remember_custom_template_directory(custom_template_dir, directory)
    return DependencyTrackingLoader(
        directory, lambda path: _remember_custom_template_dependency(custom_template_dir, path)
    )


def _refresh_custom_template_paths(custom_template_dir: Path) -> None:
    """Check observed dependencies, retaining the full scan when tracking is incomplete."""
    with _missing_custom_template_state.lock:
        if _missing_custom_template_state.overflow:
            _clear_custom_template_caches()
        dependencies = _missing_custom_template_state.dependencies.get(custom_template_dir)
        if dependencies is not None and dependencies.incomplete:
            # Re-read untracked loaders without repeatedly rebuilding an unusable inventory.
            _clear_custom_template_render_caches()
            _missing_custom_template_state.signatures.move_to_end(custom_template_dir)
            return
        if (
            dependencies is not None
            and not dependencies.needs_full_scan
            and all(_custom_template_path_signature(path) == value for path, value in dependencies.paths.items())
        ):
            _missing_custom_template_state.signatures.move_to_end(custom_template_dir)
            return
        signature = _get_custom_template_signature(custom_template_dir)
        if (
            dependencies is not None
            and dependencies.needs_full_scan
            and _missing_custom_template_state.signatures.get(custom_template_dir) == signature
            and all(_get_custom_template_signature(path) == value for path, value in dependencies.directories.items())
        ):
            _missing_custom_template_state.signatures.move_to_end(custom_template_dir)
            return
        if (
            custom_template_dir not in _missing_custom_template_state.signatures
            and len(_missing_custom_template_state.signatures) >= _MAX_CUSTOM_TEMPLATE_SIGNATURES
        ):
            evicted, _ = _missing_custom_template_state.signatures.popitem(last=False)
            _missing_custom_template_state.dependencies.pop(evicted, None)
        _clear_custom_template_render_caches()
        _missing_custom_template_state.signatures[custom_template_dir] = signature
        _missing_custom_template_state.signatures.move_to_end(custom_template_dir)
        _missing_custom_template_state.dependencies[custom_template_dir] = _CustomTemplateDependencies()


@lru_cache(maxsize=16)
def _get_environment_with_absolute_path(
    absolute_template_dir: Path, builtin_subdir: Path, custom_template_dir: Path | None = None
) -> Environment:
    """Get or create a cached Jinja2 Environment for absolute path templates."""
    from jinja2 import ChoiceLoader, FileSystemLoader  # noqa: PLC0415

    loaders: list[FileSystemLoader] = [
        _custom_template_loader(custom_template_dir, absolute_template_dir)
        if custom_template_dir is not None
        else FileSystemLoader(str(absolute_template_dir)),
        FileSystemLoader(str(TEMPLATE_DIR / builtin_subdir)),
    ]
    return _build_environment(ChoiceLoader(loaders))


@lru_cache
def _get_template_with_absolute_path(
    absolute_template_path: Path,
    builtin_subdir: Path,
    template_adapter: Callable[[Template], Template] | None = None,
    custom_template_dir: Path | None = None,
) -> Template:
    """Load a Jinja2 template from an absolute path with fallback to built-in directory.

    This handles backward compatibility for custom templates found at absolute paths.
    Includes are searched in this order:
    1. The directory containing the absolute template path
    2. TEMPLATE_DIR/<builtin_subdir>/ (fallback for includes not in custom dir)
    """
    environment = _get_environment_with_absolute_path(
        absolute_template_path.parent, builtin_subdir, custom_template_dir
    )
    template = environment.get_template(absolute_template_path.name)
    return (
        _apply_custom_template_adapter(template, template_adapter, custom_template_dir)
        if template_adapter is not None
        else template
    )


@lru_cache
def get_template(template_file_path: Path) -> Template:
    """Load and cache a Jinja2 template from the template directory."""
    return _get_template_with_custom_dir(template_file_path, None)


def sanitize_module_name(name: str, *, treat_dot_as_module: bool | None) -> str:
    """Sanitize a module name by replacing invalid characters.

    If treat_dot_as_module is True, dots are preserved in the name.
    If treat_dot_as_module is False or None (default), dots are replaced with underscores.
    """
    pattern = _MODULE_NAME_INVALID_CHAR_WITH_DOTS_PATTERN if treat_dot_as_module else _MODULE_NAME_INVALID_CHAR_PATTERN
    sanitized = pattern.sub("_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def get_module_path(name: str, file_path: Path | None, *, treat_dot_as_module: bool | None) -> list[str]:
    """Get the module path components from a name and file path.

    The treat_dot_as_module flag controls behavior:
    - None (default): Split names on dots (backward compat), but sanitize file names (replace dots)
    - True: Split names on dots AND keep dots in file names (for modular output)
    - False: Don't split names on dots AND sanitize file names (new feature for flat output)
    """
    should_split_names = treat_dot_as_module is not False
    should_keep_dots_in_files = treat_dot_as_module is True
    if file_path:
        sanitized_stem = sanitize_module_name(file_path.stem, treat_dot_as_module=should_keep_dots_in_files)
        module_parts = name.split(".")[:-1] if should_split_names else []
        return [
            *file_path.parts[:-1],
            sanitized_stem,
            *module_parts,
        ]
    return name.split(".")[:-1] if should_split_names else []


def get_module_name(name: str, file_path: Path | None, *, treat_dot_as_module: bool | None) -> str:
    """Get the full module name from a name and file path."""
    return ".".join(get_module_path(name, file_path, treat_dot_as_module=treat_dot_as_module))


class TemplateBase(ABC):
    """Abstract base class for template-based code generation."""

    @cached_property
    @abstractmethod
    def template_file_path(self) -> Path:
        """Get the path to the template file."""
        raise NotImplementedError

    @cached_property
    def template(self) -> Template:
        """Get the cached Jinja2 template instance."""
        return get_template(self.template_file_path)

    @abstractmethod
    def render(self) -> str:
        """Render the template to a string."""
        raise NotImplementedError

    def _render(self, *args: Any, **kwargs: Any) -> str:
        """Render the template with the given arguments."""
        return self.template.render(*args, **kwargs)

    def __str__(self) -> str:
        """Return the rendered template as a string."""
        return self.render()


class BaseClassDataType(DataType):
    """DataType subclass for base class references."""

    def _apply_nullable_from_reference(self) -> None:
        """Keep nullable model references out of class inheritance clauses."""


UNDEFINED: Any = object()


def _has_field_assignment(field: DataModelFieldBase) -> bool:
    """Return whether a standard model field renders with an assignment."""
    return (bool(field.field) and not field.use_annotated) or not (
        (field.required and not field.use_default_with_required) or field.should_strip_default_none()
    )


def _get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return neutral constructor-default semantics for output fields."""
    if not _has_field_assignment(field) or (field.required and not field.use_default_with_required):
        return False, False
    if "default_factory" in field.extras:
        return True, False
    if field.default is UNDEFINED or (field.default is None and field.should_strip_default_none()):
        return False, False
    return True, True


def _field_participates_in_constructor(_: DataModelFieldBase) -> bool:
    """Return whether a field participates in its model constructor."""
    return True


def _bind_field_expressions(rendered: str, class_name: str, bindings: Mapping[str, str]) -> str:
    """Bind names in field annotations and defaults without changing template objects."""
    source = rendered.encode()
    offsets = [0, *accumulate(map(len, source.splitlines(keepends=True)))]
    replacements: list[tuple[int, int, str]] = []
    for statement in ast.parse(rendered).body:
        if not isinstance(statement, ast.ClassDef) or statement.name != class_name:
            continue
        for field in statement.body:
            if not isinstance(field, ast.AnnAssign):
                continue
            expressions = (field.annotation,) if field.value is None else (field.annotation, field.value)
            for expression in expressions:
                for node in ast.walk(expression):
                    match node:
                        case ast.Name(
                            id=name,
                            ctx=ast.Load(),
                            end_lineno=int() as end_line,
                            end_col_offset=int() as end_column,
                        ) if (alias := bindings.get(name)) is not None:
                            replacements.append((
                                offsets[node.lineno - 1] + node.col_offset,
                                offsets[end_line - 1] + end_column,
                                alias,
                            ))
    for start, end, alias in sorted(replacements, reverse=True):
        source = source[:start] + alias.encode() + source[end:]
    return source.decode()


class DataModel(TemplateBase, Nullable, ABC):  # noqa: PLR0904
    """Abstract base class for all data model types.

    Handles template rendering, import collection, and model relationships.
    """

    _field_name_bindings: Mapping[str, str] | None = None

    TEMPLATE_FILE_PATH: ClassVar[str] = ""
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    IS_ALIAS: ClassVar[bool] = False
    IS_ROOT_MODEL: ClassVar[bool] = False
    SUPPORTS_GENERIC_BASE_CLASS: ClassVar[bool] = True
    FIELD_ASSIGNMENT_CHECKER: ClassVar[Callable[[DataModelFieldBase], bool]] = staticmethod(_has_field_assignment)
    FIELDS_CREATE_CLASS_DESCRIPTORS: ClassVar[bool] = False
    FIELD_DEFAULT_CLASSIFIER: ClassVar[Callable[[DataModelFieldBase], tuple[bool, bool]]] = staticmethod(
        _get_field_default_info
    )
    FIELD_PARTICIPATES_IN_CONSTRUCTOR: ClassVar[Callable[[DataModelFieldBase], bool]] = staticmethod(
        _field_participates_in_constructor
    )
    SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE: ClassVar[bool] = False
    # Kept opaque so this generic layer does not import reference-layer policy.
    FIELD_NAME_MODEL_TYPE: ClassVar[Any] = None
    FIELD_NAME_RESOLVER_CLASS: ClassVar[Any] = None
    EXPLICIT_ALIAS_CONFLICT_CHECKER: ClassVar[Callable[[DataModelFieldBase, str], bool] | None] = None
    USES_DATACLASS_ARGUMENTS: ClassVar[bool] = False
    SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT: ClassVar[bool] = False
    REQUIRES_EXPLICIT_INHERITED_FACTORY_OVERRIDE: ClassVar[bool] = False
    REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT: ClassVar[bool] = False
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = False
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = False
    SUPPORTS_FIELD_RENAMING: ClassVar[bool] = False
    SUPPORTS_KW_ONLY: ClassVar[bool] = False
    REQUIRES_MODEL_LEVEL_KW_ONLY: ClassVar[bool] = False
    SUPPORTS_BOOLEAN_LITERAL: ClassVar[bool] = True
    REQUIRES_FIELD_DEPENDENCY_ORDERING: ClassVar[bool] = False
    REQUIRES_TAGGED_UNION_DISCRIMINATOR: ClassVar[bool] = False
    REQUIRES_UNIQUE_FIELD_ALIASES: ClassVar[bool] = False
    REQUIRES_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES: ClassVar[bool] = False
    SUPPORTS_TYPED_DICT_TOTAL_FALSE: ClassVar[bool] = False
    SUPPORTS_DESERIALIZED_DEFAULT_VALUES: ClassVar[bool] = True
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = False
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    TYPED_EXTRA_FIELD_NAME: ClassVar[str | None] = None
    TYPED_EXTRA_PLAIN_ANNOTATION_TEMPLATE_DATA_KEY: ClassVar[str | None] = None
    REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK: ClassVar[bool] = False
    REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS: ClassVar[bool] = False
    SUPPORTS_SCHEMA_RUNTIME_VALIDATION: ClassVar[bool] = False
    ROOT_MODEL_CONSTRAINTS_FALLBACK: ClassVar[
        Callable[[Path | None], Callable[[list[DataModelFieldBase]], type[DataModel] | None] | None] | None
    ] = None
    # Retain the original extension hook; the backend interprets its result.
    PLAIN_PATTERN_ROOT_TYPES: ClassVar[
        Callable[[], tuple[type[DataModel], type[DataModel], type[DataModelFieldBase], type[DataTypeManager]]] | None
    ] = None
    PLAIN_PATTERN_ROOT_CHECKER: ClassVar[
        Callable[
            [type[DataModel], type[DataModel], type[DataModelFieldBase], type[DataTypeManager], Iterable[DataType]],
            bool,
        ]
        | None
    ] = None
    SCHEMA_RUNTIME_VALIDATION_ROOT_MODEL: ClassVar[Callable[[], type[DataModel]] | None] = None
    DOCSTRING_INDENT: ClassVar[int] = 4
    FIELD_DOCSTRING_INDENT: ClassVar[int] = 4
    FORMAT_DESCRIPTION_AS_DOCSTRING: ClassVar[bool] = True
    CUSTOM_TEMPLATE_ADAPTER: ClassVar[Callable[[Template], Template] | None] = None
    # A static callable avoids allocating bound methods on dependency-index cache misses.
    _INCLUDE_DICT_KEY_REFERENCE_CLASSES: ClassVar[Callable[[type[DataModel]], bool] | None] = None
    _TYPED_EXTRA_DICT_KEY_CAPABILITY: ClassVar[Callable[[DataType], bool] | None] = None
    _IMPORTS_CACHE_KEY: ClassVar[str] = "_cached_imports"
    has_forward_reference: bool = False

    @classmethod
    def resolve_module_import_conflicts(
        cls,
        models: Iterable[DataModel],
        model_imports: Mapping[DataModel, tuple[Import, ...]],
        imports: Imports,
    ) -> None:
        """Resolve output-owned conflicts after module imports are finalized."""

    @classmethod
    def create_typed_extra_field(
        cls,
        *,
        field_model: type[DataModelFieldBase],  # noqa: ARG003
        data_type: DataType,  # noqa: ARG003
    ) -> DataModelFieldBase | None:
        """Create a model-specific typed extra field when supported."""
        return None

    def apply_discriminator_tag(
        self,
        field: DataModelFieldBase,
        field_name: str,
        value: Any,
    ) -> None:
        """Apply an output-specific tagged-union discriminator when supported."""

    def has_keyword_only_definition(self) -> bool:  # noqa: PLR6301
        """Return whether the model already makes inherited fields keyword-only."""
        return False

    def enable_model_keyword_only(self) -> None:
        """Enable output-specific model-level keyword-only behavior when supported."""

    @classmethod
    def prepare_required_inherited_field(
        cls,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        explicit_extras: Collection[str] = (),
    ) -> None:
        """Preserve output-specific inherited state until requiredness is final."""
        if "default_factory" in inherited_field.extras and "default_factory" not in explicit_extras:
            field.__dict__[_REQUIRED_INHERITED_DEFAULT_FACTORY_KEY] = True
        cls.finalize_required_inherited_field(field)

    @staticmethod
    def finalize_required_inherited_field(field: DataModelFieldBase) -> None:
        """Remove an inherited factory once the child field is known to be required."""
        if field.required and field.__dict__.pop(_REQUIRED_INHERITED_DEFAULT_FACTORY_KEY, False):
            field.extras.pop("default_factory", None)

    @classmethod
    def restore_required_inherited_field_state(cls, field: DataModelFieldBase) -> bool:  # noqa: ARG003
        """Restore output-specific inherited state after requiredness is final."""
        return False

    @classmethod
    def resolve_nested_constrained_model_type(
        cls,
        configured_root_model_type: type[DataModel],
    ) -> type[DataModel]:
        """Return the model type used for nested constrained values."""
        return configured_root_model_type

    @staticmethod
    def store_additional_properties_value(
        extra_template_data: dict[str, Any],
        *,
        value: bool,
        use_backport: bool = False,
    ) -> None:
        """Store an additional-properties constraint in model-owned metadata."""
        extra_template_data[_ADDITIONAL_PROPERTIES_TEMPLATE_DATA_KEY] = value
        if use_backport:
            extra_template_data[_USE_TYPED_DICT_BACKPORT_TEMPLATE_DATA_KEY] = True

    @staticmethod
    def has_additional_properties_type(extra_template_data: dict[str, Any]) -> bool:
        """Return whether model metadata contains a typed additional-properties entry."""
        return _ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY in extra_template_data

    @classmethod
    def store_additional_properties_type(  # noqa: PLR0913
        cls,
        extra_template_data: dict[str, Any],
        type_hint: str,
        reference_classes: set[str] | None = None,
        *,
        root_model_type: type[DataModel] | None = None,
        imports: tuple[Import, ...] = (),
        use_backport: bool = False,
    ) -> None:
        """Store typed additional-properties metadata and its dependencies."""
        del imports
        expression = repr(str(type_hint)) if reference_classes else type_hint
        extra_template_data[_ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY] = _make_internal_type_expression(
            type_hint,
            expression,
        )
        if use_backport:
            extra_template_data[_USE_TYPED_DICT_BACKPORT_TEMPLATE_DATA_KEY] = True
        if reference_classes is not None:
            cls.store_additional_properties_reference_classes(
                extra_template_data,
                reference_classes,
                root_model_type=root_model_type,
            )

    @classmethod
    def store_additional_properties_reference_classes(
        cls,
        extra_template_data: dict[str, Any],
        reference_classes: set[str],
        *,
        root_model_type: type[DataModel] | None = None,
    ) -> None:
        """Store dependency metadata for every configured output model shape."""
        store_data_model_metadata = cls._store_additional_properties_reference_classes
        store_data_model_metadata(extra_template_data, reference_classes)
        if root_model_type is None or root_model_type is cls:
            return
        # Keep legacy custom output hooks working without exposing them to parsers.
        store_root_model_metadata = root_model_type._store_additional_properties_reference_classes  # noqa: SLF001
        if store_root_model_metadata is store_data_model_metadata:
            return
        store_root_model_metadata(extra_template_data, reference_classes)

    @staticmethod
    def _store_additional_properties_reference_classes(
        extra_template_data: dict[str, Any],
        reference_classes: set[str],
    ) -> None:
        """Store parse-time additional-properties dependencies in model-owned metadata."""
        extra_template_data[_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES_TEMPLATE_DATA_KEY] = reference_classes

    @property
    def _additional_properties_reference_classes(self) -> Collection[str]:
        """Return model-owned dependencies contributed by additional properties."""
        return self.extra_template_data.get(_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES_TEMPLATE_DATA_KEY, ())

    @property
    def additional_properties_reference_classes(self) -> Collection[str]:
        """Return dependencies contributed by model-owned additional properties."""
        return self._additional_properties_reference_classes

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        frozen: bool = False,
        treat_dot_as_module: bool | None = None,
        dataclass_arguments: DataclassArguments | None = None,
    ) -> None:
        """Initialize a data model with fields, base classes, and configuration."""
        self.keyword_only = keyword_only
        self.frozen = frozen
        self.dataclass_arguments: DataclassArguments = dataclass_arguments if dataclass_arguments is not None else {}
        if not self.TEMPLATE_FILE_PATH:
            msg = "TEMPLATE_FILE_PATH is undefined"
            raise Exception(msg)  # noqa: TRY002

        self._custom_template_dir: Path | None = custom_template_dir
        self.decorators: list[str] = decorators or []
        self._additional_imports: list[Import] = []
        self.custom_base_class = custom_base_class
        if base_classes:
            self.base_classes: list[BaseClassDataType] = [BaseClassDataType(reference=b) for b in base_classes]
        else:
            self.set_base_class()

        self.file_path: Path | None = path
        self.reference: Reference = reference

        self.reference.source = self

        # Keep raw Python fragments owned by the generator separate from
        # user-supplied template data.  Custom templates continue to receive
        # ``extra_template_data`` unchanged; built-in templates receive this
        # private mapping in ``_builtin_template_data`` below.
        self._internal_template_data: dict[str, Any] = {}
        self.extra_template_data: dict[str, Any]
        if extra_template_data is not None:
            # The supplied defaultdict will either create a new entry,
            # or already contain a predefined entry for this type
            self.extra_template_data = extra_template_data[self.reference.path]

            # We use the full object reference path as dictionary key, but
            # we still support `name` as key because it was used for
            # `--extra-template-data` input file and we don't want to break the
            # existing behavior.
            self.extra_template_data.update(extra_template_data[self.name])
        else:
            self.extra_template_data = defaultdict(dict)

        self.fields = self._validate_fields(fields) if fields else []

        for base_class in self.base_classes:
            if base_class.reference:
                base_class.reference.children.append(self)

        if extra_template_data is not None:
            all_model_extra_template_data = extra_template_data.get(ALL_MODEL)
            if all_model_extra_template_data:
                _copy_all_model_data(all_model_extra_template_data, self.extra_template_data)

        self.methods: list[str] = methods or []

        self.description = description
        for field in self.fields:
            field.parent = self
            field.invalidate_semantic_caches(invalidate_parent=False)

        self._additional_imports.extend(self.DEFAULT_IMPORTS)
        self.default: Any = default
        self._nullable: bool = nullable
        self._treat_dot_as_module: bool | None = treat_dot_as_module
        self._dedup_key_cache: dict[tuple[str | None, bool], tuple[Any, ...]] = {}

    def _validate_fields(self, fields: list[DataModelFieldBase]) -> list[DataModelFieldBase]:
        names: set[str] = set()
        unique_fields: list[DataModelFieldBase] = []
        for field in fields:
            if field.name:
                if field.name in names:
                    warn(f"Field name `{field.name}` is duplicated on {self.name}", stacklevel=2)
                    continue
                names.add(field.name)
            unique_fields.append(field)
        return unique_fields

    def iter_all_fields(self, visited: set[str] | None = None) -> Iterator[DataModelFieldBase]:
        """Yield all fields including those from base classes (parent fields first)."""
        if visited is None:
            visited = set()
        if self.reference.path in visited:  # pragma: no cover
            return
        visited.add(self.reference.path)
        for base_class in self.base_classes:
            if base_class.reference and isinstance(base_class.reference.source, DataModel):
                yield from base_class.reference.source.iter_all_fields(visited)
        yield from self.fields

    def get_dedup_key(self, class_name: str | None = None, *, use_default: bool = True) -> tuple[Any, ...]:
        """Generate hashable key for model deduplication.

        Results are cached per (class_name, use_default) combination since
        the key computation involves expensive render() and imports calls.
        """
        cache_key = (class_name, use_default)
        cached = self._dedup_key_cache.get(cache_key)
        if cached is not None:
            return cached

        if self.IS_ROOT_MODEL:
            self.finalize_sequence_interface()
        render_class_name = class_name if class_name is not None or not use_default else "M"
        result = tuple(to_hashable(v) for v in (self.render(class_name=render_class_name), self.imports))
        self._dedup_key_cache[cache_key] = result
        return result

    def create_reuse_model(self, base_ref: Reference) -> Self:
        """Create inherited model with empty fields pointing to base reference."""
        return self.__class__(
            fields=[],
            base_classes=[base_ref],
            description=self.description,
            reference=Reference(
                name=self.name,
                path=self.reference.path + "/reuse",
            ),
            custom_template_dir=self._custom_template_dir,
            custom_base_class=self.custom_base_class,
            keyword_only=self.keyword_only,
            treat_dot_as_module=self._treat_dot_as_module,
        )

    def _set_deprecated_decorator(self) -> None:
        """Add a class-level deprecated decorator when schema metadata requires it."""
        if not self.extra_template_data.get("deprecated"):
            return
        from datamodel_code_generator._python_decorator import is_named_python_decorator  # noqa: PLC0415

        if not any(is_named_python_decorator(decorator, "deprecated") for decorator in self.decorators):
            message = f"{self.class_name} is deprecated."
            self.decorators = [*self.decorators, f"@deprecated({message!r})"]
        self._additional_imports.append(Import.from_full_path("typing_extensions.deprecated"))
        self.clear_imports_cache()

    def replace_children_in_models(self, models: list[DataModel], new_ref: Reference) -> None:
        """Replace reference children if their parent model is in models list."""
        for child in self.reference.children[:]:
            if not isinstance(child, DataType):
                continue
            owner_model = get_most_of_parent(child, DataModel)
            if isinstance(owner_model, DataModel):
                if owner_model not in models:
                    continue
                owner_models = (owner_model,)
            else:
                owner_models = tuple(
                    model for model in models if any(base_class is child for base_class in model.base_classes)
                )
            if not owner_models:
                continue
            child.replace_reference(new_ref)
            current: DataType | DataModelFieldBase = child
            while isinstance(parent := current.parent, DataType):
                current = parent
            if isinstance(parent, DataModelFieldBase):
                parent.invalidate_semantic_caches(invalidate_parent=False)
            for owner_model in owner_models:
                owner_model.invalidate_render_caches()

    def set_base_class(self) -> None:
        """Set up the base class(es) for this model."""
        if self.custom_base_class is None:
            base_class_list = [self.BASE_CLASS] if self.BASE_CLASS else []
        elif isinstance(self.custom_base_class, list):
            base_class_list = self.custom_base_class
        else:
            base_class_list = [self.custom_base_class]

        if not base_class_list:
            self.base_classes = []
            self.invalidate_render_caches()
            return

        result = []
        for base_class in base_class_list:
            base_class_import = Import.from_full_path(base_class)
            self._additional_imports.append(base_class_import)
            result.append(BaseClassDataType.from_import(base_class_import))
        self.base_classes = result
        self.invalidate_render_caches()

    @cached_property
    def template_file_path(self) -> Path:
        """Get the path to the template file, checking custom directory first."""
        template_file_path = Path(self.TEMPLATE_FILE_PATH)
        if self._custom_template_dir is not None:
            custom_template_file_path = self._custom_template_dir / template_file_path
            _remember_custom_template_dependency(self._custom_template_dir, custom_template_file_path)
            if cached_path_exists(custom_template_file_path):
                return custom_template_file_path
        return template_file_path

    @cached_property
    def _uses_custom_root_template(self) -> bool:
        """Return whether this model's root template, rather than an include, is custom."""
        template_file_path = self.template_file_path
        canonical_template_path = Path(self.TEMPLATE_FILE_PATH)
        if template_file_path.is_absolute():
            return True
        return self._custom_template_dir is not None and cached_path_exists(
            self._custom_template_dir / canonical_template_path
        )

    def _render(self, *args: Any, **kwargs: Any) -> str:
        """Render project-owned built-ins without loading Jinja."""
        if (
            args
            or self._custom_template_dir is not None
            or not type(self).__module__.startswith("datamodel_code_generator.model.")
        ):
            return super()._render(*args, **kwargs)

        from datamodel_code_generator.model._compiled_templates import get_builtin_renderer  # noqa: PLC0415

        if renderer := get_builtin_renderer(self.TEMPLATE_FILE_PATH):
            return renderer(**kwargs)
        return super()._render(**kwargs)

    @cached_property
    def template(self) -> Template:
        """Get the Jinja2 template with custom directory support for includes."""
        resolved_path = self.template_file_path
        template_adapter = self.CUSTOM_TEMPLATE_ADAPTER if self._custom_template_dir is not None else None
        if self._uses_custom_root_template:
            absolute_template_path = resolved_path.absolute()
            if template_adapter is None:
                return _get_template_with_absolute_path(
                    absolute_template_path,
                    Path(self.TEMPLATE_FILE_PATH).parent,
                    custom_template_dir=self._custom_template_dir,
                )
            return _get_template_with_absolute_path(
                absolute_template_path,
                Path(self.TEMPLATE_FILE_PATH).parent,
                template_adapter,
                self._custom_template_dir,
            )
        if template_adapter is None:
            return _get_template_with_custom_dir(Path(self.TEMPLATE_FILE_PATH), self._custom_template_dir)
        return _get_template_with_custom_dir(
            Path(self.TEMPLATE_FILE_PATH),
            self._custom_template_dir,
            template_adapter,
        )

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get all imports required by this model and its fields."""
        # DataModel is intentionally mutable during parser post-processing. Keep
        # this cache coarse-grained: import-affecting mutations must clear it.
        if self._IMPORTS_CACHE_KEY in self.__dict__:
            return self.__dict__[self._IMPORTS_CACHE_KEY]

        imports = chain_as_tuple(
            (i for f in self.fields for i in f.imports),
            self._additional_imports,
        )
        self.__dict__[self._IMPORTS_CACHE_KEY] = imports
        return imports

    def clear_imports_cache(self) -> None:
        """Clear cached imports after import-affecting model, field, or data type mutations."""
        self.__dict__.pop(self._IMPORTS_CACHE_KEY, None)
        self.__dict__.pop(_NESTED_MODEL_REQUIRED_CONSTRUCTOR_FIELDS_KEY, None)

    def invalidate_render_caches(self) -> None:
        """Clear cached imports and render-derived model identity."""
        self.clear_imports_cache()
        if dedup_key_cache := getattr(self, "_dedup_key_cache", None):
            dedup_key_cache.clear()

    @property
    def reference_classes(self) -> frozenset[str]:
        """Get all referenced class paths used by this model."""
        return frozenset(
            {r.reference.path for r in self.base_classes if r.reference}
            | {t for f in self.fields for t in f.unresolved_types}
        )

    @property
    def name(self) -> str:
        """Get the full name of this model."""
        return self.reference.name

    @property
    def duplicate_name(self) -> str:
        """Get the duplicate name for this model if it exists."""
        return self.reference.duplicate_name or ""

    @property
    def base_class(self) -> str:
        """Get the comma-separated string of base class names."""
        return ", ".join(b.type_hint for b in self.base_classes)

    @staticmethod
    def _get_class_name(name: str) -> str:
        if "." in name:
            return name.rsplit(".", 1)[-1]
        return name

    @property
    def class_name(self) -> str:
        """Get the class name without module path."""
        return self._get_class_name(self.name)

    @class_name.setter
    def class_name(self, class_name: str) -> None:
        if "." in self.reference.name:
            self.reference.name = f"{self.reference.name.rsplit('.', 1)[0]}.{class_name}"
        else:
            self.reference.name = class_name
        self.invalidate_render_caches()

    @property
    def duplicate_class_name(self) -> str:
        """Get the duplicate class name without module path."""
        return self._get_class_name(self.duplicate_name)

    @property
    def module_path(self) -> list[str]:
        """Get the module path components for this model."""
        return get_module_path(self.name, self.file_path, treat_dot_as_module=self._treat_dot_as_module)

    @property
    def module_name(self) -> str:
        """Get the full module name for this model."""
        return get_module_name(self.name, self.file_path, treat_dot_as_module=self._treat_dot_as_module)

    @property
    def all_data_types(self) -> Iterator[DataType]:
        """Iterate over all data types used in this model."""
        for field in self.fields:
            yield from field.data_type.all_data_types
        yield from self.base_classes

    @property
    def is_alias(self) -> bool:
        """Whether is a type alias (i.e. not an instance of BaseModel/RootModel)."""
        return self.IS_ALIAS

    @classmethod
    def create_base_class_model(
        cls,
        config: dict[str, Any],  # noqa: ARG003
        reference: Reference,  # noqa: ARG003
        custom_template_dir: Path | None = None,  # noqa: ARG003
        keyword_only: bool = False,  # noqa: ARG003, FBT001, FBT002
        treat_dot_as_module: bool | None = None,  # noqa: ARG003, FBT001
    ) -> DataModel | None:
        """Create a shared base class model for DRY configuration.

        Returns the base model or None if not supported. Updates reference in place.
        Each model type should override this to provide appropriate implementation.
        """
        return None

    @classmethod
    def render_module_code(cls, models: list[DataModel]) -> str:  # noqa: ARG003
        """Render shared code that should be emitted once per generated module."""
        return ""

    @classmethod
    def get_module_code_insertion_index(cls, models: list[DataModel]) -> int:  # noqa: ARG003
        """Return the number of models emitted before shared module code."""
        return 0

    def finalize_sequence_interface(self) -> None:
        """Finalize sequence helper metadata after parser type transformations."""

    @classmethod
    def get_native_hash_model_paths(cls, models: list[DataModel]) -> set[str]:  # noqa: ARG003
        """Return models whose backend hash can replace the legacy set-item hash."""
        return set()

    def enable_identity_hash(self) -> None:
        """Retain the legacy identity hash for a set item without a usable native hash."""
        self._append_internal_template_data("class_body_lines", "__hash__ = object.__hash__")

    @property
    def schema_runtime_validation(self) -> SchemaRuntimeValidation | None:
        """Return executable schema rules already prepared by this output model."""
        value = self._internal_template_data.get("schema_runtime_validation")
        return value if _is_internal_schema_runtime_validation(value) else None

    @property
    def has_runtime_object_validation(self) -> bool:
        """Keep object validators when replacing a root model with its underlying type."""
        rules = self._internal_template_data.get("schema_runtime_validation") or self.extra_template_data.get(
            "schema_runtime_validation"
        )
        return bool(
            rules
            and any(
                getattr(rules, name, None) for name in ("pattern_properties", "required_groups", "conditional_required")
            )
        )

    @property
    def has_model_config(self) -> bool:
        """Return whether collapsing this model would discard its emitted configuration."""
        return bool(self.extra_template_data.get("config"))

    @classmethod
    def prepare_module_code(cls, models: list[DataModel]) -> None:
        """Prepare shared module metadata before imports are collected."""

    @classmethod
    def invalidate_module_code_cache(cls, models: list[DataModel]) -> None:
        """Discard parser-owned module planning state after a model rename."""

    @property
    def custom_template_dir(self) -> Path | None:
        """Return the custom template directory used by this model."""
        return self._custom_template_dir

    @property
    def nullable(self) -> bool:
        """Check if this model is nullable."""
        return self._nullable

    @cached_property
    def path(self) -> str:
        """Get the full reference path for this model."""
        return self.reference.path

    def set_reference_path(self, new_path: str) -> None:
        """Set reference path and clear cached path property."""
        self.reference.path = new_path
        if "path" in self.__dict__:  # pragma: no branch
            del self.__dict__["path"]
        for field in self.fields:
            field.invalidate_semantic_caches(invalidate_parent=False)
        self.invalidate_render_caches()

    def _set_internal_template_data(self, key: str, value: Any) -> None:
        """Store project-produced template syntax for built-in renderers only."""
        self._internal_template_data[key] = value

    def _append_internal_template_data(self, key: str, value: str) -> None:
        """Append a project-produced line that a built-in template renders as code."""
        self._internal_template_data.setdefault(key, []).append(value)

    def _pop_internal_template_data(self, key: str) -> None:
        """Forget a project-produced built-in template value."""
        self._internal_template_data.pop(key, None)

    def _builtin_template_data(self) -> dict[str, Any]:
        """Return the restricted context used by project-owned templates."""
        return _safe_extra_template_data(self.extra_template_data, self._internal_template_data)

    def _custom_template_data(self) -> dict[str, Any]:
        """Return the legacy unrestricted custom-template context."""
        if not self._internal_template_data:
            return self.extra_template_data
        return {**self.extra_template_data, **self._internal_template_data}

    def render(self, *, class_name: str | None = None) -> str:
        """Render the model to a string using the template."""
        use_custom_template = self._uses_custom_root_template
        extra_template_data = self._custom_template_data() if use_custom_template else self._builtin_template_data()
        rendered_class_name = class_name or self.class_name
        rendered = self._render(
            class_name=rendered_class_name,
            fields=self._template_fields(use_custom_template=use_custom_template),
            decorators=self.decorators,
            base_class=self.base_class,
            methods=self.methods,
            description=self._template_description(use_custom_template=use_custom_template),
            dataclass_arguments=(
                self.dataclass_arguments
                if use_custom_template or not self.USES_DATACLASS_ARGUMENTS
                else _safe_dataclass_arguments(self.dataclass_arguments)
            ),
            path=self.path,
            **extra_template_data,
        )
        if self._field_name_bindings is None:
            return rendered
        return _bind_field_expressions(rendered, rendered_class_name, self._field_name_bindings)

    def set_field_name_bindings(self, bindings: Mapping[str, str]) -> None:
        """Set module import names used by rendered field expressions."""
        self._field_name_bindings = bindings
        self.invalidate_render_caches()

    @property
    def _custom_template_fields(self) -> Sequence[DataModelFieldBase | _RenderedDataModelField]:
        """Return custom-template fields, allocating proxies only when escaping changes a docstring."""
        if not any(
            field.use_field_description or field.use_field_description_example or field.use_inline_field_description
            for field in self.fields
        ):
            return self.fields

        rendered_fields: list[DataModelFieldBase | _RenderedDataModelField] | None = None
        for index, field in enumerate(self.fields):
            if (docstring := field.docstring) is None or (
                escaped_docstring := escape_docstring(docstring)
            ) == docstring:
                if rendered_fields is not None:
                    rendered_fields.append(field)
                continue

            if rendered_fields is None:
                rendered_fields = []
                rendered_fields.extend(self.fields[:index])
            rendered_fields.append(_RenderedDataModelField(field, escaped_docstring or ""))
        return self.fields if rendered_fields is None else rendered_fields

    def _template_fields(self, *, use_custom_template: bool) -> Sequence[DataModelFieldBase | _RenderedDataModelField]:
        """Return fields in the representation expected by the selected template."""
        if use_custom_template:
            return self._custom_template_fields
        return self.rendered_fields

    def _template_description(self, *, use_custom_template: bool) -> str | None:
        """Return a description safe for the selected template convention."""
        if use_custom_template:
            return escape_docstring(self.description)
        if not self.FORMAT_DESCRIPTION_AS_DOCSTRING:
            return self.description
        return self.rendered_description

    @property
    def use_single_line_docstring(self) -> bool:
        """Whether single-line docstring formatting is enabled for this model."""
        return bool(self.extra_template_data.get("use_single_line_docstring"))

    def _format_docstring(self, value: str | None, indent_spaces: int) -> str:
        return format_docstring(
            value,
            indent_spaces,
            use_single_line_docstring=self.use_single_line_docstring,
        )

    @property
    def rendered_description(self) -> str:
        """Return the model description as a generated docstring literal."""
        return self._format_docstring(self.description, self.DOCSTRING_INDENT)

    @property
    def rendered_fields(self) -> list[DataModelFieldBase | _RenderedDataModelField]:
        """Return fields with docstrings prepared for built-in templates."""
        return [
            _RenderedDataModelField(field, self._format_docstring(field.docstring, self.FIELD_DOCSTRING_INDENT))
            for field in self.fields
        ]


def _model_ancestor_paths(model: DataModel) -> set[str]:
    """Collect generated ancestors without assuming direct bases are already sorted."""
    ancestors: set[str] = set()
    to_visit = [
        base_class.reference.source
        for base_class in model.base_classes
        if base_class.reference and isinstance(base_class.reference.source, DataModel)
    ]
    while to_visit:
        parent = to_visit.pop()
        if parent.path in ancestors:
            continue
        ancestors.add(parent.path)
        to_visit.extend(
            base_class.reference.source
            for base_class in parent.base_classes
            if base_class.reference and isinstance(base_class.reference.source, DataModel)
        )
    return ancestors


def sort_data_models_for_mro(models: list[DataModel]) -> list[DataModel]:
    """Match the stable descendant-before-ancestor order used for rendered bases."""
    if len(models) <= 1:
        return models.copy()
    ancestor_paths = {model.path: _model_ancestor_paths(model) for model in models}
    return sorted(
        models,
        key=lambda model: sum(model.path in ancestors for ancestors in ancestor_paths.values()),
    )


def c3_merge(sequences: list[list[MroT]], key: Callable[[MroT], str]) -> list[MroT]:
    """Merge inheritance sequences using C3 with a deterministic cycle fallback."""
    result: list[MroT] = []
    while sequences := [sequence for sequence in sequences if sequence]:
        tail_keys = {key(item) for sequence in sequences for item in sequence[1:]}
        candidate = next(
            (sequence[0] for sequence in sequences if key(sequence[0]) not in tail_keys),
            sequences[0][0],
        )
        candidate_key = key(candidate)
        result.append(candidate)
        for sequence in sequences:
            sequence[:] = [item for item in sequence if key(item) != candidate_key]
    return result


def linearize_data_models(models: list[DataModel]) -> list[DataModel]:
    """Return the effective C3 order for direct generated models."""
    if len(models) == 1 and not models[0].base_classes:
        return models.copy()

    linearized_models: dict[str, list[DataModel]] = {}

    def linearize(model: DataModel, active: frozenset[str] = frozenset()) -> list[DataModel]:
        if cached := linearized_models.get(model.path):
            return cached
        if model.path in active:
            return [model]
        parents = sort_data_models_for_mro([
            base_class.reference.source
            for base_class in model.base_classes
            if base_class.reference and isinstance(base_class.reference.source, DataModel)
        ])
        result = [
            model,
            *c3_merge(
                [
                    *[linearize(parent, active | {model.path}).copy() for parent in parents],
                    parents.copy(),
                ],
                key=lambda item: item.path,
            ),
        ]
        linearized_models[model.path] = result
        return result

    direct_models = sort_data_models_for_mro(models)
    return c3_merge(
        [
            *[linearize(model).copy() for model in direct_models],
            direct_models.copy(),
        ],
        key=lambda item: item.path,
    )


def get_effective_fields(model: DataModel) -> tuple[DataModelFieldBase, ...]:
    """Return constructor-visible fields after C3 inheritance and overrides."""
    if not model.base_classes:
        return tuple(field for field in model.fields if field.name is not None)

    effective_fields: dict[str, DataModelFieldBase] = {}
    for inherited_model in reversed(linearize_data_models([model])):
        for field in inherited_model.fields:
            if field.name is not None:
                effective_fields[field.name] = field
    return tuple(effective_fields.values())


def get_inherited_fields(models: list[DataModel]) -> dict[str, DataModelFieldBase]:
    """Build one effective field lookup with exact original names taking priority."""
    original_names: dict[str, DataModelFieldBase] = {}
    generated_names: dict[str, DataModelFieldBase] = {}
    for model in linearize_data_models(models):
        for field in model.fields:
            if field.original_name is not None:
                original_names.setdefault(field.original_name, field)
            if field.name is not None:
                generated_names.setdefault(field.name, field)
    generated_names.update(original_names)
    return generated_names


def _model_rebuild_namespace(*classes: type[Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    for cls in classes:
        namespace.update(vars(sys.modules[cls.__module__]))
    return namespace


_rebuild_namespace = _model_rebuild_namespace(DataType, BaseClassDataType, DataModelFieldBase, DataModel)


def _rebuild_model_with_datamodel_namespace(model: type[Any]) -> None:
    model.model_rebuild(_types_namespace={**_rebuild_namespace, **vars(sys.modules[model.__module__])})


_rebuild_model_with_datamodel_namespace(DataType)
_rebuild_model_with_datamodel_namespace(BaseClassDataType)
_rebuild_model_with_datamodel_namespace(DataModelFieldBase)


def _find_base_classes(model: DataModel) -> list[DataModel]:
    """Get direct base class DataModels."""
    return [b.reference.source for b in model.base_classes if b.reference and isinstance(b.reference.source, DataModel)]
