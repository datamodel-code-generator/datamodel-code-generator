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
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property, lru_cache
from itertools import accumulate, chain, takewhile
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, NamedTuple, Optional, TypeAlias, TypeVar, cast
from warnings import warn

from pydantic import ConfigDict, Field
from typing_extensions import Self, deprecated, final, override

from datamodel_code_generator import Error, cached_path_exists
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._constraint_applicability import VALUES_AS_WRITTEN_KEY
from datamodel_code_generator._format_types import PythonVersion  # noqa: TC001 - used by Pydantic at runtime
from datamodel_code_generator._internal_utils import get_most_of_parent, to_hashable
from datamodel_code_generator._model_keys import FieldKey, ModelKey, model_key_of
from datamodel_code_generator._parse_memo import (
    MemoTable,
    bump_deep,
    bump_field,
    bump_model,
    forget,
    memoized,
    note_reads,
)
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, walk_graph
from datamodel_code_generator._requirements import backport_path, python_target_meets
from datamodel_code_generator._target_unicode import host_identifiers, host_parsable
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_ANY,
    IMPORT_OPTIONAL,
    IMPORT_UNION,
    Import,
)
from datamodel_code_generator.model._decisions.assignment import (
    AssignmentInputs,
    DataclassVisibility,
    ModelWritten,
    PydanticDataclassWritten,
    classify_assignment,
    participates_in_constructor,
)
from datamodel_code_generator.model._decisions.literal_default import const_type, decide_literal_default
from datamodel_code_generator.model._decisions.nullability import field_renders_none
from datamodel_code_generator.model._render_plan import (
    RENDER_READY,
    AssignmentFacts,
    FieldDefault,
    FieldRenderPlan,
    RuleId,
    Why,
    frozen_render_plan,
    visible_nested_factories,
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
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Generator, Hashable, Iterable, Iterator, Mapping, Sequence

    from jinja2 import Environment, Template

    from datamodel_code_generator import DataclassArguments
    from datamodel_code_generator import types as _types
    from datamodel_code_generator._checks import CheckMode
    from datamodel_code_generator._reference_graph import ReachedModels
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model import runtime_validation as _runtime_validation
    from datamodel_code_generator.model._decisions.assignment import AssignmentRow, WrittenAssignment
    from datamodel_code_generator.model._decisions.literal_default import LiteralDefault
    from datamodel_code_generator.model._decisions.nullability import FieldNullSource
    from datamodel_code_generator.model._render_plan import (
        NestedFactoryMetadata,
        PlannedField,
        RenderInputs,
        RenderReady,
    )
    from datamodel_code_generator.python_literal import PythonRuntimeExpression

TEMPLATE_DIR: Path = Path(__file__).parents[0] / "template"
_TYPING_IMPORT_NAMES: frozenset[str] = frozenset({
    IMPORT_ANNOTATED.import_,
    IMPORT_OPTIONAL.import_,
    IMPORT_UNION.import_,
})
_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES_TEMPLATE_DATA_KEY = "additionalPropertiesReferenceClasses"
_ADDITIONAL_PROPERTIES_TEMPLATE_DATA_KEY = "additionalProperties"
_ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY = "additionalPropertiesType"
# This key remains part of the legacy template contract, including custom outputs.
_USE_TYPED_DICT_BACKPORT_TEMPLATE_DATA_KEY = "use_typeddict_backport"
_MODULE_NAME_INVALID_CHAR_PATTERN = re.compile(r"[^0-9a-zA-Z_]")
_MODULE_NAME_INVALID_CHAR_WITH_DOTS_PATTERN = re.compile(r"[^0-9a-zA-Z_.]")
_MAX_MISSING_CUSTOM_TEMPLATE_SUBDIRS = 128
_MAX_CUSTOM_TEMPLATE_SIGNATURES = 128
_MAX_CUSTOM_TEMPLATE_DEPENDENCIES = 128
_ORIGINAL_TEMPLATE_LOADER_MARKER = "__datamodel_code_generator_original_template_loader__"
_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY = "_nested_model_default_factory_order"
_NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_KEYS_KEY = "_nested_model_default_factory_recursive_keys"
_PATH_PARTNER_KEY = "_path_partner_model_key"
_REQUIRED_INHERITED_DEFAULT_FACTORY_KEY = "_required_inherited_default_factory"
_UNTAKEN_CONSTRAINTS_KEY = "_untaken_constraints"
_MEMBER_UNTAKEN_CONSTRAINTS_KEY = "_member_untaken_constraints"
_OVERRIDDEN_CONSTRAINTS_KEY = "_overridden_constraints"
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
_AnswerT = TypeVar("_AnswerT")
_CustomTemplateSignature = bytes


@record
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


@dataclass(slots=True)
class _TypingImportNames:
    """The typing imports a field's type hint needs, collected over its type tree into one value."""

    union: bool = False
    optional: bool = False
    annotated: bool = False
    any_: bool = False

    def add(self, name: str) -> None:
        match name:
            case IMPORT_UNION.import_:
                self.union = True
            case IMPORT_OPTIONAL.import_:
                self.optional = True
            case IMPORT_ANNOTATED.import_:
                self.annotated = True

    def requirements(self) -> _TypingImportRequirements:
        return _TypingImportRequirements(self.union, self.optional, self.annotated, self.any_)


@record
class _TypingImportRequirements:
    union: bool = False
    optional: bool = False
    annotated: bool = False
    any_: bool = False

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
        tree = ast.parse(lent.source if (lent := host_identifiers(annotation)) else annotation, mode="eval")
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
        return _overlay_constraints(a, b, class_from="first-constraints")


_ConstraintsClassRow: TypeAlias = Literal["first-given", "first-constraints"]
"""Which argument of ``_overlay_constraints`` gives the class of the result."""


def _overlay_constraints(
    base: ConstraintsBaseT | None, overriding: ConstraintsBaseT | None, *, class_from: _ConstraintsClassRow
) -> ConstraintsBaseT | None:
    """Overlay the constraint values ``overriding`` sets on those ``base`` sets; a value left unset keeps the other's.

    The result takes the class of an argument by row: ``first-given`` that of the first argument given (an inherited
    field copy), ``first-constraints`` that of the first ``ConstraintsBase`` (collapsing a root model, and the public
    ``ConstraintsBase.merge_constraints``). A class that is no ``ConstraintsBase`` gives no constraints.
    """
    if class_from == "first-given":
        constraints_class = type(base or overriding)
    else:
        constraints_class = type(base) if isinstance(base, ConstraintsBase) else type(overriding)
    values = {
        key: value
        for constraints in (base, overriding)
        if isinstance(constraints, ConstraintsBase)
        for key, value in constraints.model_dump(by_alias=True).items()
        if value is not None
    }
    return constraints_class.model_validate(values) if issubclass(constraints_class, ConstraintsBase) else None


class DataModelFieldBase(_BaseModel):  # noqa: PLR0904
    """Base class for model field representation and rendering."""

    PARSER_CONSTRUCTOR: ClassVar[Callable[..., DataModelFieldBase] | None] = None
    PREPARE_PYTHON_PATTERNS: ClassVar[
        Callable[[DataModelFieldBase, str, dict[str, PythonRuntimeExpression]], None] | None
    ] = None
    PREPARE_TYPE_ALIAS_FIELD: ClassVar[
        Callable[
            [DataModelFieldBase, dict[str, PythonRuntimeExpression], Callable[[DataModelFieldBase, DataType], None]],
            bool,
        ]
        | None
    ] = None
    SUPPORTS_FIELD_CONSTRAINTS: ClassVar[bool] = False
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = False
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = False
    _FIELD_ASSIGNMENT_ROW: ClassVar[AssignmentRow] = "model"

    if TYPE_CHECKING:

        def _pydantic_constructs(self) -> tuple[PydanticConstruct, ...]:
            """Return the Pydantic constructs that this field's rendering writes; only Pydantic fields define it.

            A generation collects them only for a model type that Pydantic reads, whose fields are Pydantic fields.
            """

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
    order_constructor_fields_by_written_defaults: bool = False
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
    target_python_version: Optional[PythonVersion] = None  # noqa: UP045
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

    def target_version_key(self, data_type: DataType) -> tuple[int, int]:
        """Return the target Python of a decision on ``data_type``: the parser's target, else the one the type carries.

        The parser sets the target on every field it builds, so a field built without one keeps the type's version.
        """
        return (data_type.python_version if (target := self.target_python_version) is None else target).version_key

    @property
    def dropped_constraints_warning(self) -> str | None:
        """Return a warning naming the constraints the output model leaves out on this field's type, if any."""
        return None

    def _generated_without_constraints(self, reason: str, count: int) -> str:
        """Return the dropped constraints warning for ``reason``, naming the model and field that leave them out."""
        return (
            f"{reason}; {self._constraint_owner} is generated without "
            f"{'this constraint' if count == 1 else 'these constraints'}."
        )

    @property
    def _constraint_owner(self) -> str:
        """Return the model and field that a constraint warning names; the field of a root model is its model."""
        owner = getattr(self.parent, "class_name", "")
        return f"{owner}.{self.name}" if self.name and not getattr(self.parent, "IS_ROOT_MODEL", False) else owner

    @property
    def untaken_constraints(self) -> tuple[dict[str, Any], dict[str, str]]:
        """Return the declared constraints the field is generated without, and the target taking some of them.

        They are the field's own and those of the members, items and values that collapsing inlines into its type. The
        second mapping names, for a constraint an explicit later target model version takes, that version.
        """
        own = self.__dict__.get(_UNTAKEN_CONSTRAINTS_KEY) or ({}, {})
        if (member := self.__dict__.get(_MEMBER_UNTAKEN_CONSTRAINTS_KEY)) is None:
            return own
        return {**member[0], **own[0]}, {**member[1], **own[1]}

    def record_untaken_constraints(self, untaken: dict[str, Any], minimums: dict[str, str]) -> None:
        """Remember the declared constraints of this field that its type does not take, to warn about them."""
        self.__dict__[_UNTAKEN_CONSTRAINTS_KEY] = (untaken, minimums)

    def pop_untaken_constraints(self) -> dict[str, Any]:
        """Forget the remembered untaken constraints, and return those this field declares itself."""
        self.__dict__.pop(_MEMBER_UNTAKEN_CONSTRAINTS_KEY, None)
        return own[0] if (own := self.__dict__.pop(_UNTAKEN_CONSTRAINTS_KEY, None)) else {}

    def inherit_untaken_constraints(self, source: DataModelFieldBase, *, whole_type: bool) -> None:
        """Take over the untaken constraints of a root field that collapsing inlines into this field's type.

        A root inlined as the ``whole_type`` declares this field's own constraints; one inlined as a member, an item or
        a value of the type declares those of that part, which this field only warns about.
        """
        if whole_type:
            self._merge_untaken_constraints(_UNTAKEN_CONSTRAINTS_KEY, source.__dict__.get(_UNTAKEN_CONSTRAINTS_KEY))
            inherited = source.__dict__.get(_MEMBER_UNTAKEN_CONSTRAINTS_KEY)
        else:
            inherited = source.untaken_constraints
        self._merge_untaken_constraints(_MEMBER_UNTAKEN_CONSTRAINTS_KEY, inherited)

    def _merge_untaken_constraints(self, key: str, inherited: tuple[dict[str, Any], dict[str, str]] | None) -> None:
        """Add ``inherited`` untaken constraints under ``key``, keeping those this field already remembers there."""
        if inherited and inherited[0]:
            untaken, minimums = self.__dict__.get(key) or ({}, {})
            self.__dict__[key] = ({**inherited[0], **untaken}, {**inherited[1], **minimums})

    @property
    def overridden_constraints(self) -> tuple[str, int] | None:
        """Return the type ``--type-overrides`` replaces on the field and how many constraints leave with it, if any."""
        return self.__dict__.get(_OVERRIDDEN_CONSTRAINTS_KEY)

    def record_overridden_constraints(self, replaced: str, count: int) -> None:
        """Remember that ``--type-overrides`` replaces the type hint ``replaced`` and ``count`` constraints with it."""
        self.__dict__[_OVERRIDDEN_CONSTRAINTS_KEY] = (replaced, count)

    def unconstrain_union_members(self) -> bool | None:
        """Write union members without the constraints the output model cannot check on one member alone.

        Return False when the field stayed as it was; None, which an override that returns nothing gives, counts as a
        change.
        """

    def inherit_unconstrained_union_members(self, source: DataModelFieldBase) -> None:
        """Take over the union members written without constraints from a field whose type this field inlines."""

    @property
    def requires_immediate_forward_reference_resolution(self) -> bool:
        """Return whether this field's annotation is evaluated in the class body."""
        return bool(getattr(self, "is_pydantic_extra_field", False))

    def _process_const_as_literal(self) -> None:
        """Type a const as ``const_type`` decides and write its default and nullability (``decide_literal_default``).

        The ``process_const`` hook of the subclasses calls it.
        """
        if (const := self.extras.get("const", UNDEFINED)) is UNDEFINED:
            return
        self.const = True
        match const_type(const):
            case "none":
                self.replace_data_type(self.data_type.__class__(type=NONE), clear_old_parent=False)
            case "literal":
                self.replace_data_type(self.data_type.__class__(literals=[const]), clear_old_parent=False)
        _apply_literal_default(
            self,
            decide_literal_default(
                "const",
                const,
                required=self.required,
                has_default=self.has_default,
                default=self.default,
                nullable=self.nullable,
                type_has_null=self.type_has_null,
            ),
        )

    def should_strip_default_none(self, *, keep_optional: bool = False) -> bool:
        """Return whether an actual None default should be omitted."""
        match (self.strip_default_none, keep_optional and self.data_type._optional_before_render_preparation()):  # noqa: SLF001
            case (False, _) | (_, True):
                return False

        return self.default is None

    def self_reference(self) -> bool:
        """Check if field references its parent model.

        The parse's memo keeps the result until a GenerationStore-managed semantic mutation.
        """
        if self.parent is None or not self.parent.reference:  # pragma: no cover
            return False
        parent = self.parent
        return memoized(
            MemoTable.SELF_REFERENCE,
            FieldKey(id(self)),
            self,
            None,
            lambda: any(
                _reference_names_model(d.reference, parent, "self reference")
                for d in self.data_type.all_data_types
                if d.reference
            ),
        )

    @property
    def _can_apply_constraints(self) -> bool:
        """Apply constraints to containers without suppressing recursive item types."""
        return self._constraints_blocked_by() is None

    def _constraints_blocked_by(self) -> Literal["self-reference-container", "strict-kwargs"] | None:
        """Return why the field's call cannot write its constraints, or None when it can.

        A field naming its own model outside a container takes none; a strict type with arguments writes them itself.
        """
        if self.self_reference():
            data_type = self.data_type
            while True:
                if (
                    data_type.is_list  # noqa: PLR0916
                    or data_type.is_sequence
                    or data_type.is_dict
                    or data_type.is_mapping
                    or data_type.is_set
                    or data_type.is_frozen_set
                    or data_type.is_tuple
                ):
                    break
                if data_type.alias or data_type.type or data_type.reference or len(data_type.data_types) != 1:
                    return "self-reference-container"
                data_type = data_type.data_types[0]
        return "strict-kwargs" if self.data_type.strict and self.data_type.kwargs else None

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

    def _type_hint_from_data_type(self, data_type: DataType, *, base: bool = False) -> str:
        """Get the type hint string, or with ``base`` the base hint, for a field data type, including nullability."""
        type_hint = data_type.base_type_hint if base else data_type.type_hint

        if not type_hint:
            return NONE
        if self._field_null_source(data_type) is None:
            return type_hint
        return data_type._optional_hint(type_hint, self._use_union_operator)  # noqa: SLF001

    def _field_null_source(
        self,
        data_type: DataType,
        *,
        pending_reference_null: bool = False,
    ) -> FieldNullSource | None:
        """Return why the field writes None around the hint of ``data_type`` (``field_renders_none``).

        Without ``pending_reference_null`` the hint is the one ``type_hint`` renders; with it, imports collected
        without a hint read the type's own None and pass its model's.
        """
        required = self.required
        hint_has_none = (
            data_type.is_optional if pending_reference_null else data_type._hint_null_source() is not None  # noqa: SLF001
        )
        return field_renders_none(
            hint_has_none=hint_has_none and data_type.type != ANY,
            has_default_factory=self.has_default_factory,
            nullable=self.nullable,
            required=required,
            type_has_null=required and self.type_has_null,
            fall_back_to_nullable=not required and self.fall_back_to_nullable,
            pending_reference_null=pending_reference_null,
        )

    @property
    def base_type_hint(self) -> str:
        """Get the base type hint without constrained type kwargs.

        This returns the type without kwargs (e.g., 'str' instead of 'constr(pattern=...)').
        Used in RootModel generics when regex_engine config is needed for lookaround patterns.
        """
        return self._type_hint_from_data_type(self.data_type, base=True)

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
        """Collect type-hint imports, reading rendered nullability without writing it.

        ``needs_annotated`` is passed in so subclasses can precompute it.
        """
        data_type = data_type or self.data_type
        if self._can_collect_imports_without_type_hint(needs_annotated=needs_annotated, data_type=data_type):
            return self._collect_field_imports_without_type_hint(data_type=data_type)
        requirements = self._typing_import_requirements(needs_annotated=needs_annotated, data_type=data_type)
        imports = tuple(data_type.all_imports)
        if requirements.any_ and IMPORT_ANY not in imports:
            imports = chain_as_tuple(requirements.leading_imports, imports)

        filtered_imports = tuple(import_ for import_ in imports if requirements.allows(import_))
        missing_imports = tuple(import_ for import_ in requirements.trailing_imports if import_ not in filtered_imports)
        if not missing_imports:
            return filtered_imports
        return chain_as_tuple(filtered_imports, missing_imports)

    def _collect_field_imports_without_type_hint(self, *, data_type: DataType) -> tuple[Import, ...]:
        needs_optional = self._needs_optional_import_without_type_hint(data_type=data_type)
        if self._can_skip_data_type_imports(data_type=data_type):
            return (IMPORT_OPTIONAL,) if needs_optional else ()

        imports = tuple(data_type.all_imports)
        if needs_optional and IMPORT_OPTIONAL not in imports:
            return chain_as_tuple(imports, (IMPORT_OPTIONAL,))
        return imports

    def _import_state_data_type(self) -> DataType | None:
        """Return the data type ``imports`` collects from, or None when it collects without a type hint."""
        return self._import_state_target(
            needs_annotated=self.use_annotated and self.needs_annotated_import, data_type=self.data_type
        )

    def _hint_renders_own_type(self) -> bool:  # noqa: PLR6301
        """Return whether the type hint renders the field's own data type rather than a copy of it."""
        return True

    def _import_state_target(self, *, needs_annotated: bool, data_type: DataType) -> DataType | None:
        if self._can_collect_imports_without_type_hint(needs_annotated=needs_annotated, data_type=data_type):
            return None
        return data_type

    def _write_pre_render_nullability(self, data_type: DataType) -> None:
        """Write the None of the nullable models the type names and of bare None union members, as L3 imports did."""
        if data_type.reference:
            data_type._materialize_render_state("reference-null")  # noqa: SLF001

        for child in data_type.data_types:
            self._write_pre_render_nullability(child)
        if data_type.dict_key:
            self._write_pre_render_nullability(data_type.dict_key)

        if not data_type.is_union or data_type.preserve_union_member_order:
            return
        if any(self._data_type_renders_none(child) for child in data_type.data_types):
            data_type._materialize_render_state("member-null")  # noqa: SLF001

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
        for annotation in data_type._annotation_texts:  # noqa: SLF001
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
        names = _TypingImportNames()
        self._add_typing_import_names(data_type, names)
        if needs_annotated:
            names.add(IMPORT_ANNOTATED.import_)
        if not names.optional and self._needs_field_optional_import_from_structure(data_type=data_type):
            names.add(IMPORT_OPTIONAL.import_)
        return names.requirements()

    def _add_typing_import_names(self, data_type: DataType, names: _TypingImportNames) -> None:
        """Add the typing imports the hint of ``data_type`` needs to ``names``."""
        self._add_explicit_typing_import_names(data_type, names)

        if data_type.is_union:
            self._add_union_typing_import_names(data_type, names)
        elif len(data_type.data_types) == 1:
            self._add_typing_import_names(data_type.data_types[0], names)

        if data_type.dict_key:
            self._add_typing_import_names(data_type.dict_key, names)
            if not self._mapping_writes_value(data_type):
                names.any_ = True

        if data_type.discriminator:
            names.add(IMPORT_ANNOTATED.import_)
        if self._data_type_needs_optional_import(data_type):
            names.add(IMPORT_OPTIONAL.import_)

    def _add_union_typing_import_names(self, data_type: DataType, names: _TypingImportNames) -> None:
        """Add the typing imports a union needs; one whose members all write nothing or None renders ``Any``."""
        branch_keys: set[tuple[Any, ...]] = set()
        has_none = False
        writes_member = False
        for child in data_type.data_types:
            self._add_typing_import_names(child, names)
            if self._data_type_renders_none(child):
                has_none = True
                continue
            branch_keys.add(self._data_type_import_key(child))
            writes_member = writes_member or self._data_type_writes_hint(child)

        if has_none and not data_type.preserve_union_member_order and not data_type.use_union_operator:
            names.add(IMPORT_OPTIONAL.import_)
        ordered_none_branch_count = int(data_type.preserve_union_member_order and has_none)
        if not writes_member and data_type.data_types and not ordered_none_branch_count:
            names.any_ = True
        if len(branch_keys) + ordered_none_branch_count > 1 and not data_type.use_union_operator:
            names.add(IMPORT_UNION.import_)

    @staticmethod
    def _add_explicit_typing_import_names(data_type: DataType, names: _TypingImportNames) -> None:
        if data_type.python_type:
            from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
                iter_python_type_expr_names,
            )

            for name in iter_python_type_expr_names(data_type.python_type.expression):
                names.add(name)
        for annotation in data_type._annotation_texts:  # noqa: SLF001
            if not annotation:
                continue
            for name in _annotation_typing_import_names(annotation):
                names.add(name)

    def _data_type_needs_optional_import(self, data_type: DataType) -> bool:
        if data_type.use_union_operator or data_type.type == ANY:
            return False
        return data_type.admits_none and self._data_type_renders_importable_hint(data_type)

    def _needs_field_optional_import_from_structure(self, *, data_type: DataType | None = None) -> bool:
        data_type = data_type or self.data_type
        if self._use_union_operator or not self._data_type_renders_importable_hint(data_type):
            return False
        return self._field_null_source(data_type) is not None

    def _data_type_renders_importable_hint(self, data_type: DataType) -> bool:
        if self._data_type_renders_none(data_type):
            return False
        return self._data_type_has_renderable_structure(data_type)

    @classmethod
    def _data_type_writes_hint(cls, data_type: DataType) -> bool:
        """Return whether a data type writes a hint: a renderable structure or a hint of its own."""
        return cls._data_type_has_renderable_structure(data_type) or bool(data_type.type_hint)

    @classmethod
    def _mapping_writes_value(cls, data_type: DataType) -> bool:
        """Return whether a mapping writes a value type of its own; one with a key type writes ``Any`` otherwise."""
        return bool(data_type.alias or data_type.type) or any(
            cls._data_type_writes_hint(child) for child in data_type.data_types
        )

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

    def _admits_none(self) -> bool:
        """Return whether the field's annotation admits None, from the facts final at B1 and without rendering it."""
        data_type = self.data_type
        return (
            data_type.admits_none
            or self._data_type_renders_none(data_type)
            or field_renders_none(
                hint_has_none=False,
                has_default_factory=self.has_default_factory,
                nullable=self.nullable,
                required=(required := self.required),
                type_has_null=required and self.type_has_null,
                fall_back_to_nullable=not required and self.fall_back_to_nullable,
            )
            is not None
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
            data_type.admits_none,
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
        """Return whether the field-level nullable wrapper needs typing.Optional.

        No hint is rendered here, so the None of a nullable model that the type names is not written yet.
        """
        data_type = data_type or self.data_type
        if self._use_union_operator or not self._has_renderable_data_type_without_type_hint(data_type=data_type):
            return False
        return self._field_null_source(data_type, pending_reference_null=data_type.names_nullable_model) is not None

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
        """Clear the parent model's render caches."""
        if (parent := self.parent) is None:
            return
        parent.invalidate_render_caches()

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
        """Return whether the field's structured default needs output-specific validation it does not have yet.

        The parse then writes ``validate_default`` into the field's extras through the store. The neutral field
        policy needs none; output backends that require validation at construction time override this hook.
        """
        return False

    def _get_constructor_default_info(self) -> tuple[bool, bool]:
        """Return the constructor default of the field, as its own written assignment gives it."""
        return _classify_field_assignment(self, self._FIELD_ASSIGNMENT_ROW).default_info

    def _build_frozen_render_plan(self) -> FieldRenderPlan | None:  # noqa: PLR6301
        """Return the render plan the parse freezes for this field at B2, or None when it renders without one.

        A field that renders from a plan freezes its live plan (``_render_plan`` before B2), which ``RenderReady``
        rebuilds from the field's facts while it compares or refreshes a frozen plan.
        """
        return None

    def _render_inputs(self) -> RenderInputs:
        """Return what the field's render plan reads from the field, to compare when it renders (``RenderReady``)."""
        return _render_inputs_of(self)

    def _render_plan(self) -> FieldRenderPlan:
        """Return the plan the parse froze for this field, or the plan its facts give now (`live_render_plan`)."""
        if (plan := frozen_render_plan(self)) is not None:
            return plan
        return live_render_plan(self)

    def _build_render_plan(self) -> FieldRenderPlan:
        """Build the field's render plan from its facts as they are now: its type alone, nothing after it."""
        return FieldRenderPlan(
            annotation_form="plain",
            call=None,
            default=FieldDefault(self.default, missing_sentinel=False, why=_DEFAULT_VALUE),
            has_default_factory_in_field=False,
            omitted=(),
            symbol_imports=(),
            why=_PLAIN_ANNOTATION,
        )

    def _written_assignment(self) -> WrittenAssignment:
        """Return what the field's backend writes after its annotation, as assignment classification reads it."""
        return _model_written(self)

    def _construction_written_assignment(self) -> WrittenAssignment:
        """Return the written assignment the L1 construction key reads: ``_written_assignment``.

        A backend that decides what its field writes without building the field's render plan answers from that.
        """
        return self._written_assignment()

    def _dataclass_visibility(self) -> DataclassVisibility:
        """Return what the Pydantic dataclass template needs to see of the field as an assignment.

        Constructor ordering that counts defaults as the construction key gives them reads no type as admitting None.
        """
        return DataclassVisibility(
            metadata_assignment=self.use_annotated and getattr(self, "requires_dataclass_field_assignment", False),
            factory_in_field=getattr(self, "has_default_factory_in_field", False),
            admits_none=not _ORDERS_BY_CONSTRUCTION_DEFAULTS.get() and self.data_type.admits_none,
        )

    def _takes_nested_model_factory(self) -> bool:
        """Return whether the field may write its nested model's factory: optional, defaulting to None, option on."""
        return (
            self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
        )

    def _has_default_for_nested_model_factory(self) -> bool:
        """Return whether the emitted constructor supplies this field's default.

        Row ``legacy-nested-factory-order-before-nullability`` counts a Pydantic dataclass field's default as its
        construction key gives it.
        """
        if _COUNTS_CONSTRUCTION_DEFAULTS.get() and self._FIELD_ASSIGNMENT_ROW == "pydantic-dataclass":
            return (
                not self.is_class_var
                and not (self.required and not self.use_default_with_required)
                and _classify_field_assignment(self, "pydantic-dataclass").construction_assigned
            )
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
        """Clear field caches derived from mutable type and parent semantics, and move the field's revision."""
        forget(MemoTable.SELF_REFERENCE, FieldKey(id(self)), self)
        bump_field(self)
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


def _apply_literal_default(field: DataModelFieldBase, decision: LiteralDefault) -> None:
    """Write what ``decide_literal_default`` decides for a field fixed to one value; the field keeps the rest."""
    if decision.writes_default:
        field.default = decision.default
    if decision.required is not None:
        field.required = decision.required
    if decision.not_null:
        field.nullable = False


_LOOKING_UP_REQUIRED_FIELDS: Final[ContextVar[frozenset[ModelKey]]] = ContextVar(
    "looking_up_required_fields", default=frozenset()
)
"""The models whose required constructor fields are being looked up; a field holding one leads back to it."""


def _nested_model_required_fields(source: DataModel, key: ModelKey) -> bool:
    """Look up whether ``source`` has required constructor fields, with ``source`` marked as being looked up."""
    token = _LOOKING_UP_REQUIRED_FIELDS.set(_LOOKING_UP_REQUIRED_FIELDS.get() | {key})
    try:
        return _nested_model_has_required_constructor_fields(source)
    finally:
        _LOOKING_UP_REQUIRED_FIELDS.reset(token)


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


def _default_factory_type_name(data_type: DataType) -> str:
    """Return the name a default factory uses for the referenced type.

    The factory runs once the module defines the type, so a quoted forward reference is named directly.
    """
    if (alias := data_type.alias) and not data_type._is_quoted_reference:  # noqa: SLF001
        return alias
    return cast("DataModel", cast("Reference", data_type.reference).source).class_name


_ASSIGNMENT_HOOK_NAMES = frozenset({
    "FIELD_ASSIGNMENT_CHECKER",
    "FIELD_DEFAULT_CLASSIFIER",
    "FIELD_PARTICIPATES_IN_CONSTRUCTOR",
    "FIELD_CONSTRUCTION_ORDER_KEY",
})
"""The ``DataModel`` hooks that are views of an assignment row."""


def _is_hidden_by_earlier_field(field: DataModelFieldBase, name: str) -> bool:
    """Return whether an earlier field of the class has bound ``name`` when the field's value is evaluated.

    Only an output that keeps a field named like a type and writes that type as a string can meet this. The derived
    value being computed reads the class's other fields (`note_reads`).
    """
    if (parent := field.parent) is None or not parent.RESOLVES_STRING_ANNOTATIONS_FROM_MODULE:
        return False
    note_reads((parent,))
    return any(
        earlier.name == name and parent.field_binds_class_attribute(earlier)
        for earlier in takewhile(lambda earlier: earlier is not field, parent.fields)
    )


_IMMUTABLE: Final = frozenset({str, bool, int, float, bytes, type(None)})
_HOLDERS: Final = (_BaseModel, dict, list, tuple, set, frozenset)


def _render_inputs_of(value: _BaseModel | dict[Any, Any] | Collection[Any]) -> RenderInputs:
    """Return what a render plan reads from ``value``: its keys, its values and the inputs of the values that hold more.

    A field, a type or constraints (``_BaseModel``) give their attributes, a dict its items, a list, a tuple or a set
    its items. Those that hold more (``_holds_render_inputs``) give their own inputs, so a change inside them counts,
    but not the back-references a model leaves out of its dumps (``_exclude_fields``).
    """
    if isinstance(value, _BaseModel):
        attributes = vars(value)
        excluded = value._exclude_fields  # noqa: SLF001
        return (
            attributes,
            tuple(attributes),
            tuple(attributes.values()),
            tuple([
                _render_inputs_of(item)
                for name, item in attributes.items()
                if type(item) not in _IMMUTABLE and name not in excluded and _holds_render_inputs(item)
            ]),
        )
    if isinstance(value, dict):
        items = tuple(value.values())
        return value, tuple(value), items, _nested_render_inputs(items)
    items = tuple(value)
    return value, None, items, _nested_render_inputs(items)


def _nested_render_inputs(items: tuple[Any, ...]) -> tuple[RenderInputs, ...]:
    """Return the inputs of the items that hold more."""
    return tuple([
        _render_inputs_of(item) for item in items if type(item) not in _IMMUTABLE and _holds_render_inputs(item)
    ])


def _holds_render_inputs(value: object) -> bool:
    """Return whether a render plan reads inside ``value``: a container, or a type or constraints, not a reference."""
    return isinstance(value, _HOLDERS) and not isinstance(value, Reference)


def _check_render_plan_accounting(
    mode: CheckMode, model: DataModel, field: PlannedField, plan: FieldRenderPlan
) -> None:
    """Report a frozen or rebuilt plan that drops a declared item without a reason or writes an undeclared one."""
    missing, extra = plan.unaccounted(field._declared_render_items())  # noqa: SLF001
    if missing or extra:
        mode.violation(
            Error,
            f"the render plan of {model.class_name}.{field.name} leaves {sorted(missing)} unaccounted and "
            f"accounts for undeclared {sorted(extra)}",
        )


_PLAIN_ANNOTATION = Why(RuleId("D11.annotation.plain"))
"""The annotation is the field's type alone."""
_DEFAULT_VALUE = Why(RuleId("D4.default.value"), keyword="default")
"""The field's default, as its schema or the parser gives it."""


@final
@record(eq=False)
class _ValueFactory:
    """The default factory of a mutable default: ``lambda: <value>``, or the type of an empty container.

    ``always_lambda`` writes the lambda for an empty or false value too (a field the class body leaves unbound).
    """

    value: object
    always_lambda: bool = False

    @override
    def __str__(self) -> str:
        """Return the factory as the field's call writes it."""
        if self.value or self.always_lambda:
            return f"lambda: {represent_python_value(self.value)}"
        return type(self.value).__name__


@final
@record(eq=False)
class _NestedModelFactory:
    """The default factory of an optional field holding a nested model: the model's class, or a lambda building it.

    Its text names the model as the module finally names it, so a plan that holds it keeps it across renames.
    """

    data_type: DataType
    source: DataModel
    builds_by_name: bool

    @override
    def __str__(self) -> str:
        """Return the factory as the field's call writes it."""
        if self.builds_by_name:
            return f"lambda: {self.source.class_name}()"
        return _default_factory_type_name(self.data_type)


def _nested_factory_decision(model: DataModel) -> tuple[tuple[int, int] | None, Collection[ModelKey]]:
    """Return the module and position of ``model`` and the models of its reference cycle, for its nested factories.

    While the parse freezes the render plans they come from the decision of render preparation; otherwise from what
    ``Parser`` published on the models, which is nothing before it publishes (row ``legacy.L-NF`` of
    ``model/_render_plan.py``).
    """
    if (metadata := visible_nested_factories()) is not None:
        return metadata.order.get(id(model)), metadata.recursive.get(model_key_of(model.reference), ())
    return (
        model.__dict__.get(_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY),
        model.__dict__.get(_NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_KEYS_KEY, ()),
    )


_ORDERS_BEFORE_NULLABILITY: Final[ContextVar[bool]] = ContextVar("orders_before_nullability", default=False)
"""Whether the nested factories read row ``legacy-nested-factory-order-before-nullability``: in constructor ordering,
and in the row's own required check."""
_COUNTS_CONSTRUCTION_DEFAULTS: Final[ContextVar[bool]] = ContextVar("counts_construction_defaults", default=False)
"""Whether a field's default counts as its construction key gives it, as that row's required check counts it."""
_ORDERS_BY_CONSTRUCTION_DEFAULTS: Final[ContextVar[bool]] = ContextVar("orders_by_construction_defaults", default=False)
"""Whether constructor ordering counts a ``None`` default that ``--strip-default-none`` strips as no default, as the
construction key does, also where a Pydantic dataclass keeps it for a type rendered with None."""
_ORDERS_CONSTRUCTOR_FIELDS: Final[ContextVar[bool]] = ContextVar("orders_constructor_fields", default=False)
"""Whether constructor ordering runs, which leaves out under ``--order-constructor-fields-by written-defaults`` the
nested factories render preparation has not decided yet."""


@contextmanager
def ordering_constructor_fields(
    *, before_nullability: bool = False, construction_defaults: bool = False
) -> Generator[None, None, None]:
    """Let the nested factories answer constructor ordering while the block runs.

    Under ``--order-constructor-fields-by written-defaults`` a field holds no nested factory until render preparation
    decides them, and the ordering after that decision orders by the decided ones. ``before_nullability`` reads row
    ``legacy-nested-factory-order-before-nullability`` otherwise. ``construction_defaults`` counts the defaults as the
    construction key gives them, for an ordering that runs before the rendered nullability is final.
    """
    ordering = _ORDERS_CONSTRUCTOR_FIELDS.set(True)
    token = _ORDERS_BEFORE_NULLABILITY.set(before_nullability)
    defaults = _ORDERS_BY_CONSTRUCTION_DEFAULTS.set(construction_defaults)
    try:
        yield
    finally:
        _ORDERS_BY_CONSTRUCTION_DEFAULTS.reset(defaults)
        _ORDERS_BEFORE_NULLABILITY.reset(token)
        _ORDERS_CONSTRUCTOR_FIELDS.reset(ordering)


def _counting_construction_defaults(compute: Callable[[], bool], *, counts: bool) -> bool:
    """Return ``compute()``, a required check, with defaults counted by the construction key or the written assignment.

    A required check is no constructor ordering: the nested factories it reads are those it would answer anywhere.
    """
    ordering = _ORDERS_CONSTRUCTOR_FIELDS.set(False)
    orders = _ORDERS_BEFORE_NULLABILITY.set(counts)
    defaults = _ORDERS_BY_CONSTRUCTION_DEFAULTS.set(False)
    token = _COUNTS_CONSTRUCTION_DEFAULTS.set(counts)
    try:
        return compute()
    finally:
        _COUNTS_CONSTRUCTION_DEFAULTS.reset(token)
        _ORDERS_BY_CONSTRUCTION_DEFAULTS.reset(defaults)
        _ORDERS_BEFORE_NULLABILITY.reset(orders)
        _ORDERS_CONSTRUCTOR_FIELDS.reset(ordering)


def _plan_view(field: DataModelFieldBase, ready: RenderReady) -> Hashable:
    """Return what a field's plan reads besides its facts: whether the nested-factory decision answers.

    Under ``--use-default-factory-for-optional-nested-models`` also how constructor ordering asks for nested factories.
    """
    deciding = ready.deciding_nested_factories() is not None
    if not field.use_default_factory_for_optional_nested_models:
        return deciding
    return (
        deciding,
        _ORDERS_CONSTRUCTOR_FIELDS.get(),
        _ORDERS_BEFORE_NULLABILITY.get(),
        _COUNTS_CONSTRUCTION_DEFAULTS.get(),
        _LOOKING_UP_REQUIRED_FIELDS.get(),
    )


def live_render_plan(field: DataModelFieldBase) -> FieldRenderPlan:
    """Return the plan of ``field`` built from its facts as they are now, kept in the parse's memo while they hold.

    The memo keeps one plan for each view (`_plan_view`); a plan rebuilt to compare with a frozen one, and a plan
    outside a parse, is built at each read.
    """
    if (ready := RENDER_READY.get()) is None or (memo := ready.derived_from(field)) is None:
        return field._build_render_plan()  # noqa: SLF001
    return memo.derived(MemoTable.FIELD_RENDER_PLAN, field, _plan_view(field, ready), field._build_render_plan)  # noqa: SLF001


def field_imports(field: DataModelFieldBase) -> tuple[Import, ...]:
    """Return the imports of ``field``, kept in the parse's memo with the plan they read while its facts hold.

    The plan is the one frozen for the field in the current view, else its live plan (`live_render_plan`).
    """
    if (ready := RENDER_READY.get()) is None or (memo := ready.derived_from(field)) is None:
        return field.imports
    plan = ready.plan_of(field) or live_render_plan(field)
    return memo.derived(MemoTable.FIELD_IMPORTS, field, _plan_view(field, ready), lambda: field.imports, source=plan)


def _keeps_order_before_nullability(field: DataModelFieldBase) -> bool:
    """Return whether the field's nested factory comes under row ``legacy-nested-factory-order-before-nullability``."""
    return (
        field._FIELD_ASSIGNMENT_ROW == "pydantic-dataclass"  # noqa: SLF001
        and field.strip_default_none
        and not field.order_constructor_fields_by_written_defaults
    )


def _nested_model_has_required_fields(field: DataModelFieldBase, source: DataModel, source_key: ModelKey) -> bool:
    """Return whether ``source`` has required constructor fields, which keeps the field from building it by factory.

    Under row ``legacy-nested-factory-order-before-nullability`` the answer kept from the first read, with defaults
    counted by the construction key, is the one constructor ordering reads.
    """
    if _keeps_order_before_nullability(field):
        before_nullability = _required_before_nullability(source, source_key)
        if _ORDERS_BEFORE_NULLABILITY.get():
            return before_nullability
    return _counting_construction_defaults(lambda: _nested_model_required_fields(source, source_key), counts=False)


def _required_before_nullability(source: DataModel, source_key: ModelKey) -> bool:
    """Return whether ``source`` had required constructor fields when row ``legacy-nested-factory-...`` first read it.

    The row is ``legacy-nested-factory-order-before-nullability``; defaults count as the construction key gives them.
    """
    return memoized(
        MemoTable.NESTED_MODEL_REQUIRED_FIELDS_BEFORE_NULLABILITY,
        source_key,
        source,
        None,
        lambda: _counting_construction_defaults(lambda: _nested_model_required_fields(source, source_key), counts=True),
    )


def remember_nested_factory_order_before_nullability(model: DataModel) -> None:
    """Read row ``legacy-nested-factory-order-before-nullability`` for the nested models the model's fields build.

    0.86.0 read it where collecting a module's imports wrote each model's nullability, model by model, so a nested
    model later in the module answered with the nullability its fields were parsed with.
    """
    for field in model.fields:
        if not (_keeps_order_before_nullability(field) and field._takes_nested_model_factory()):  # noqa: SLF001
            continue
        for source in [
            data_type.reference.source
            for data_type in field.data_type.data_types or (field.data_type,)
            if data_type.reference and isinstance(data_type.reference.source, DataModel)
        ]:
            _required_before_nullability(source, model_key_of(source.reference))


def _defers_nested_factory(field: DataModelFieldBase) -> bool:
    """Return whether constructor ordering runs before render preparation decides the field's nested factory.

    Under ``--order-constructor-fields-by written-defaults`` the field then holds none.
    """
    return (
        field.order_constructor_fields_by_written_defaults
        and _ORDERS_CONSTRUCTOR_FIELDS.get()
        and visible_nested_factories() is None
    )


def _nested_model_factory(field: DataModelFieldBase, model_cls: type[DataModel]) -> _NestedModelFactory | None:
    """Return the factory of the nested model an optional field holds, or None when it cannot have one.

    A nested model that leads back to the field's model, by the decided reference cycles or by its required
    constructor fields being looked up already, has none: its factory would build the field's model again.
    """
    for data_type in () if _defers_nested_factory(field) else field.data_type.data_types or (field.data_type,):
        if data_type.is_dict:
            continue
        if data_type.reference and isinstance(source := data_type.reference.source, model_cls):
            parent_order, parent_cycle = (
                (None, ()) if (parent := field.parent) is None else _nested_factory_decision(parent)
            )
            if (source_key := model_key_of(source.reference)) in parent_cycle or (
                source_key in _LOOKING_UP_REQUIRED_FIELDS.get()
            ):
                return None
            if _nested_model_has_required_fields(field, source, source_key):
                return None
            if _is_hidden_by_earlier_field(field, source.class_name):
                return _NestedModelFactory(data_type, source, builds_by_name=True)
            match (parent_order, _nested_factory_decision(source)[0]):
                case ((parent_module, parent_index), (source_module, source_index)) if (
                    parent_module == source_module and source_index >= parent_index
                ):
                    return _NestedModelFactory(data_type, source, builds_by_name=True)
                case _:
                    return _NestedModelFactory(data_type, source, builds_by_name=False)
    return None


def _set_nested_model_default_factory_order(models: list[DataModel], metadata: NestedFactoryMetadata) -> None:
    """Publish the declaration order and the reference cycle of each model for the nested model factories."""
    for model in models:
        model.__dict__[_NESTED_MODEL_DEFAULT_FACTORY_ORDER_KEY] = metadata.order[id(model)]
        if recursive_keys := metadata.recursive.get(model_key_of(model.reference)):
            model.__dict__[_NESTED_MODEL_DEFAULT_FACTORY_RECURSIVE_KEYS_KEY] = recursive_keys


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

    @override
    def __str__(self) -> str:
        """Return the rendered template as a string."""
        return self.render()


class BaseClassDataType(DataType):
    """DataType subclass for base class references."""

    @override
    def _reference_adds_null(self) -> bool:
        """Keep nullable model references out of class inheritance clauses."""
        return False


UNDEFINED: Any = object()


class BaseLayoutView(NamedTuple):
    """How the layouts of generated bases look before the processing of each module changes them.

    ``is_slot`` accepts the fields that keep a slot whatever that processing does, ``layout_model`` gives the model
    whose layout a model may take from it, and ``is_opaque`` tells the models whose layout the generator does not know.
    """

    is_slot: Callable[[DataModelFieldBase], bool]
    layout_model: Callable[[DataModel], DataModel]
    is_opaque: Callable[[DataModel], bool]


def _model_written(field: DataModelFieldBase) -> ModelWritten:
    """Return the written assignment of a field as the neutral rule reads it: its call, declared factory and default."""
    default = field.default
    return ModelWritten(
        text=bool(field.field),
        declared_factory="default_factory" in field.extras,
        default="undefined" if default is UNDEFINED else "none" if default is None else "value",
    )


def _assignment_inputs(
    field: DataModelFieldBase, row: AssignmentRow, written: WrittenAssignment | None = None
) -> AssignmentInputs:
    """Read the facts ``classify_assignment`` decides on from a live field, with its own written assignment by default.

    The written assignment is read as the field renders it now, nested-model default factories included, before L6
    records the recursive paths that drop them (row ``legacy-nested-factory-at-read`` of
    ``model/_decisions/assignment.py``).
    """
    written = field._written_assignment() if written is None else written  # noqa: SLF001
    return AssignmentInputs(
        row=row,
        written=written,
        required=field.required and not field.use_default_with_required,
        strips_none=field.should_strip_default_none(),
        annotated=field.use_annotated,
        class_var=field.is_class_var,
        init_excluded=field.extras.get("init") is False,
        visibility=(
            field._dataclass_visibility()  # noqa: SLF001
            if row == "pydantic-dataclass" or isinstance(written, PydanticDataclassWritten)
            else None
        ),
        keeps_stripped_place=row in {"msgspec", "pydantic-dataclass"} and _keeps_stripped_place(field),
    )


def _keeps_stripped_place(field: DataModelFieldBase) -> bool:
    """Return whether the field takes its constructor place among those without a default, as in 0.86.0.

    ``--strip-default-none`` strips the field's ``None`` default, which a msgspec Struct writes as ``UNSET`` and a
    Pydantic dataclass as ``None`` for a type that admits None all the same, and neither
    ``--order-constructor-fields-by written-defaults`` nor ``--use-default-with-required`` is given.
    """
    return (
        not (field.order_constructor_fields_by_written_defaults or field.use_default_with_required)
        and field.should_strip_default_none()
    )


def _classify_field_assignment(
    field: DataModelFieldBase, row: AssignmentRow, written: WrittenAssignment | None = None
) -> AssignmentFacts:
    """Return the assignment facts of a field under an output model's row."""
    return classify_assignment(_assignment_inputs(field, row, written))


def _has_field_assignment(field: DataModelFieldBase) -> bool:
    """Return whether a standard model field renders with an assignment."""
    return _classify_field_assignment(field, "model").assigned


def _get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return neutral constructor-default semantics for output fields."""
    return _classify_field_assignment(field, "model", _model_written(field)).default_info


def _field_participates_in_constructor(field: DataModelFieldBase) -> bool:
    """Return whether a field participates in its model constructor."""
    return participates_in_constructor(
        "model", init_excluded=field.extras.get("init") is False, class_var=field.is_class_var
    )


def _bind_field_expressions(rendered: str, class_name: str, bindings: Mapping[str, str]) -> str:
    """Bind names in field annotations and defaults without changing template objects.

    Identifier characters the running interpreter does not know are lent letters of the same UTF-8 length and a type
    statement is parsed as an assignment of the same length (`host_parsable`), so the offsets it parses hold for the
    rendered text.
    """
    source = rendered.encode()
    offsets = [0, *accumulate(map(len, source.splitlines(keepends=True)))]
    replacements: list[tuple[int, int, str]] = []
    host_source, restore_name = host_parsable(rendered)
    for statement in ast.parse(host_source).body:
        if not isinstance(statement, ast.ClassDef) or restore_name(statement.name) != class_name:
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
                        ) if (alias := bindings.get(restore_name(name))) is not None:
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
    reused_reference: Reference | None = None
    """The reference of the model that reuse replaced with this empty subclass of an equal model."""

    TEMPLATE_FILE_PATH: ClassVar[str] = ""
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    IS_ALIAS: ClassVar[bool] = False
    IS_ROOT_MODEL: ClassVar[bool] = False
    DEFERS_RECURSIVE_TYPE_HINT: ClassVar[bool] = False
    SUPPORTS_GENERIC_BASE_CLASS: ClassVar[bool] = True
    FIELD_ASSIGNMENT_CHECKER: ClassVar[Callable[[DataModelFieldBase], bool]] = staticmethod(_has_field_assignment)
    FIELD_DEFAULT_CLASSIFIER: ClassVar[Callable[[DataModelFieldBase], tuple[bool, bool]]] = staticmethod(
        _get_field_default_info
    )
    FIELD_PARTICIPATES_IN_CONSTRUCTOR: ClassVar[Callable[[DataModelFieldBase], bool]] = staticmethod(
        _field_participates_in_constructor
    )
    FIELD_CONSTRUCTION_ORDER_KEY: ClassVar[Callable[[DataModelFieldBase], bool] | None] = None
    """Order key of the fields when the model is built: True for a field whose class-body default is known then.

    None for a model that keeps its fields in schema order when it is built.
    """
    _ASSIGNMENT_ROW: ClassVar[AssignmentRow | None] = "model"
    """The row of ``classify_assignment`` that the hooks above are views of.

    None for a class that brings its own assignment hooks without naming a row, whose hooks then answer.
    """
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
    SUPPORTS_FIELD_TYPE_RENAMING: ClassVar[bool] = False
    NORMALIZES_FIELD_NAMES: ClassVar[bool] = False
    RESOLVES_STRING_ANNOTATIONS_FROM_MODULE: ClassVar[bool] = False
    WRITES_QUOTED_NAMES_IN_TYPING_GENERICS: ClassVar[bool] = False
    SUPPORTS_KW_ONLY: ClassVar[bool] = False
    REQUIRES_MODEL_LEVEL_KW_ONLY: ClassVar[bool] = False
    SUPPORTS_BOOLEAN_LITERAL: ClassVar[bool] = True
    SUPPORTS_NULL_ENUM_MEMBER: ClassVar[bool] = True
    _DECODES_MIXED_ENUM_VALUES: ClassVar[bool] = True
    """Whether the output decodes an Enum whose values are not all str or all int (msgspec does not)."""
    _DECODES_VALUE_UNIONS: ClassVar[bool] = True
    """Whether the output decodes a union of any value types (msgspec decodes unions of some kinds only)."""
    _READ_BY_PYDANTIC: ClassVar[bool] = False
    """Whether Pydantic builds the output, so that a run states the Pydantic release its constructs need."""
    _READ_BY_MSGSPEC: ClassVar[bool] = False
    """Whether msgspec decodes data by the output's annotations, which it resolves at the first decode."""
    _PYDANTIC_FORM: ClassVar[PydanticConstruct | None] = None
    """The construct that the model's own form writes where Pydantic reads it, such as a ``type`` statement."""
    _CONFIG_KEYS_WITHOUT_REQUIREMENT: ClassVar[Callable[[], tuple[str, ...]]] = staticmethod(tuple)
    """The config keys that the model can write without a row in ``_requirements.py``, for the debug check."""
    _LITERAL_HOLDS_ENUM_MEMBERS: ClassVar[bool] = True
    """Whether the output validates a ``Literal`` of enum members (msgspec does not)."""
    _LAYERS_REPEATED_CONSTRAINTS: ClassVar[bool] = True
    """Whether the output checks a constraint written on an alias whose type sets the same one (msgspec raises)."""
    _CHECKS_CONSTRAINTS: ClassVar[bool] = False
    """Whether the output checks the constraints of a value (bounds, lengths, patterns) when it reads data."""
    REQUIRES_FIELD_DEPENDENCY_ORDERING: ClassVar[bool] = False
    REQUIRES_TAGGED_UNION_DISCRIMINATOR: ClassVar[bool] = False
    REQUIRES_UNIQUE_FIELD_ALIASES: ClassVar[bool] = False
    FLATTENS_CONFLICTING_BASE_LAYOUTS: ClassVar[bool] = False
    REQUIRES_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES: ClassVar[bool] = False
    SUPPORTS_TYPED_DICT_TOTAL_FALSE: ClassVar[bool] = False
    SUPPORTS_DESERIALIZED_DEFAULT_VALUES: ClassVar[bool] = True
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = False
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = None
    TYPED_EXTRA_FIELD_NAME: ClassVar[str | None] = None
    TYPED_EXTRA_PLAIN_ANNOTATION_TEMPLATE_DATA_KEY: ClassVar[str | None] = None
    REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK: ClassVar[bool] = False
    REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS: ClassVar[bool] = False
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = False
    """Whether the parse freezes a render plan for each field at B2 (``model/_render_plan.py``)."""
    SUPPORTS_SCHEMA_RUNTIME_VALIDATION: ClassVar[bool] = False
    ROOT_MODEL_CONSTRAINTS_FALLBACK: ClassVar[
        Callable[[Path | None], Callable[[list[DataModelFieldBase]], type[DataModel] | None] | None] | None
    ] = None
    # Retain the original extension hook; the backend interprets its result.
    PLAIN_PATTERN_ROOT_TYPES: ClassVar[
        Callable[[], tuple[type[DataModel], type[DataModel], type[DataModelFieldBase], type[_types.DataTypeManager]]]
        | None
    ] = None
    PLAIN_PATTERN_ROOT_CHECKER: ClassVar[
        Callable[
            [
                type[DataModel],
                type[DataModel],
                type[DataModelFieldBase],
                type[_types.DataTypeManager],
                Iterable[DataType],
            ],
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
    _INCLUDE_DICT_KEY_REFERENCE_CLASSES: ClassVar[Callable[[DataModel], bool] | None] = None
    _TYPED_EXTRA_DICT_KEY_CAPABILITY: ClassVar[Callable[[DataType], bool] | None] = None
    has_forward_reference: bool = False
    discriminator_tag: tuple[Any, Any] | None = None

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

    @classmethod
    def order_fields_for_construction(cls, fields: Iterable[DataModelFieldBase]) -> list[DataModelFieldBase]:
        """Return the fields sorted by ``FIELD_CONSTRUCTION_ORDER_KEY``, or as given when the model has no such key."""
        with ordering_constructor_fields():
            return list(fields) if (key := cls.FIELD_CONSTRUCTION_ORDER_KEY) is None else sorted(fields, key=key)

    def apply_discriminator_tag(  # noqa: PLR6301
        self,
        field: DataModelFieldBase,  # noqa: ARG002
        field_name: str,  # noqa: ARG002
        value: Any,  # noqa: ARG002
    ) -> bool:
        """Apply an output-specific tagged-union discriminator when supported.

        Return whether the discriminator ``field`` leaves the instance fields as a class variable, which the parse
        writes into its extras (``is_classvar``) through the store.
        """
        return False

    def opt_out_of_inherited_tag(self) -> None:
        """Clear an output-specific tagged-union discriminator of the model or inherited from a base, when supported."""

    def has_keyword_only_definition(self) -> bool:  # noqa: PLR6301
        """Return whether the model already makes inherited fields keyword-only."""
        return False

    def flatten_conflicting_base_layouts(self, view: BaseLayoutView, flatten: Callable[[DataModel], None]) -> None:
        """Flatten the model where the output cannot combine the layouts of its generated bases, as msgspec cannot."""

    def flatten_base_layouts(
        self, adopt_field: Callable[[DataModelFieldBase, bool], DataModelFieldBase]
    ) -> list[DataModelFieldBase]:
        """Return the fields that declare the model without its generated bases.

        Each base gives its fields after those of its own bases, in declaration order, and the model its fields last.
        A name keeps its first position and the field declared last, and is required where any declaration requires
        it, as allOf merges the properties of its parts. ``adopt_field`` makes a field one of the model, required or
        not.
        """
        fields: dict[object, DataModelFieldBase] = {}
        required: dict[object, bool] = {}
        visited: set[int] = set()

        def collect(model: DataModel) -> None:
            if id(model) in visited:
                return
            visited.add(id(model))
            for base in _find_base_classes(model):
                collect(base)
            for field in model.fields:
                key = id(field) if field.name is None else field.name
                fields[key] = field
                required[key] = required.get(key, False) or field.required

        collect(self)
        return [adopt_field(field, required[key]) for key, field in fields.items()]

    def orders_required_assignments_before_defaults(self) -> bool:  # noqa: PLR6301
        """Return whether required fields written as assignments must precede the fields with defaults."""
        return False

    def keeps_assignment_with_required_fields(self, field: DataModelFieldBase) -> bool:  # noqa: ARG002, PLR6301
        """Return whether a field written as an assignment stays among the required fields without one."""
        return False

    @classmethod
    def configure_annotations(
        cls,
        extra_template_data: defaultdict[str, dict[str, Any]],
        *,
        target_python_version: PythonVersion,
        use_deferred_annotations: bool,
    ) -> None:
        """Prepare the output's annotation representation for the target runtime."""
        if key := cls.TYPED_EXTRA_PLAIN_ANNOTATION_TEMPLATE_DATA_KEY:
            extra_template_data.setdefault(ALL_MODEL, {})[key] = (
                python_target_meets("python.deferred_annotations", target_python_version.version_key)
                or not use_deferred_annotations
            )

    @classmethod
    def configure_required_fields(
        cls,
        extra_template_data: defaultdict[str, dict[str, Any]],
        *,
        target_python_version: PythonVersion,
        use_total_false: bool,
    ) -> None:
        """Preserve the declared total=False capability for custom output classes."""
        if use_total_false and cls.SUPPORTS_TYPED_DICT_TOTAL_FALSE:
            data = extra_template_data[ALL_MODEL]
            data["use_total_false_for_typed_dict"] = True
            if not python_target_meets("python.typed_dict_required", target_python_version.version_key):
                data["use_total_false_typeddict_backport"] = True

    @staticmethod
    def requires_extra_items_backport(target_python_version: PythonVersion) -> bool:
        """Preserve the historic extra-item template flag for custom output metadata."""
        return not python_target_meets("python.typed_dict_closed", target_python_version.version_key)

    @staticmethod
    def resolve_dataclass_arguments(
        arguments: DataclassArguments | None, *, frozen: bool, keyword_only: bool
    ) -> DataclassArguments:
        """Preserve explicit decorator arguments ahead of the legacy flags."""
        if arguments is not None:
            return arguments
        arguments = {}
        if frozen:
            arguments["frozen"] = True
        if keyword_only:
            arguments["kw_only"] = True
        return arguments

    @classmethod
    def prepare_constructor_arguments(
        cls,
        arguments: dict[str, Any],
        *,
        dataclass_arguments: DataclassArguments | None,
        frozen: bool,
        keyword_only: bool,
    ) -> None:
        """Adapt shared model creation options to the selected output constructor."""
        if not cls.USES_DATACLASS_ARGUMENTS:
            arguments.pop("dataclass_arguments", None)
            return
        if (explicit_arguments := arguments.pop("dataclass_arguments", None)) is None:
            explicit_arguments = dataclass_arguments
        arguments["dataclass_arguments"] = cls.resolve_dataclass_arguments(
            explicit_arguments, frozen=frozen, keyword_only=keyword_only
        )
        arguments.pop("frozen", None)
        arguments.pop("keyword_only", None)

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

    @classmethod
    def resolve_type_alias_model_type(
        cls,
        configured_root_model_type: type[DataModel],
        target_python_version: PythonVersion,  # noqa: ARG003
    ) -> type[DataModel]:
        """Return the model type that --use-type-alias selects for a root value."""
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
        bump_model(self)

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

    def iter_all_fields(self, visited: set[ModelKey] | None = None) -> Iterator[DataModelFieldBase]:
        """Yield all fields including those from base classes (parent fields first), each model once by model key.

        The models of ``visited`` are left out, with the bases reached only through them; every model yielded from is
        added to it.
        """
        met = set() if visited is None else visited
        met_before = set(met)
        walk = _base_walk(GraphSnapshot.INHERITED_FIELDS, (self,))
        for key in graph_answer(
            GraphSnapshot.INHERITED_FIELDS,
            walk.graph.postorder(walk.starts[0], EdgeKind.BASE, met),
            lambda: [_model_key_of_model(model) for model in _walked_inherited_field_models(self, met_before)],
        ):
            yield from walk.node_of[key].fields

    def get_dedup_key(self, class_name: str | None = None, *, use_default: bool = True) -> tuple[Any, ...]:
        """Generate hashable key for model deduplication.

        The parse's memo keeps the key per (class_name, use_default) combination, since the key computation involves
        expensive render() and imports calls.
        """
        render_class_name = class_name if class_name is not None or not use_default else "M"

        def compute() -> tuple[Any, ...]:
            self._prepare_dedup_key()
            return self._render_dedup_key(render_class_name)

        return memoized(
            MemoTable.DEDUP_KEYS,
            model_key_of(self.reference),
            self,
            (class_name, use_default),
            compute,
            lambda: self._render_dedup_key(render_class_name),
        )

    def _prepare_dedup_key(self) -> None:
        """Write what rendering the dedup key decides, so rendering it writes nothing.

        A root model decides its sequence interface, and every field whose hint renders its own type writes the None
        that rendering the key wrote at L3 (``legacy-l3-import-nullability``): a type keeps the None of the nullable
        model it names when duplicate deletion then points it at a model without one.
        """
        if self.IS_ROOT_MODEL:
            self.finalize_sequence_interface()
        for field in self.fields:
            if field._hint_renders_own_type():  # noqa: SLF001
                field._write_pre_render_nullability(field.data_type)  # noqa: SLF001

    def _write_pre_render_nullability(self) -> None:
        """Write the nullability that collecting the model's imports wrote at L3 (``legacy-l3-import-nullability``).

        Type overrides copy it as the type's own and constructor ordering reads it before render preparation writes
        rendered nullability; the row goes when ordering reads the plans of render preparation (RP14).
        """
        for field in self.fields:
            if (data_type := field._import_state_data_type()) is not None:  # noqa: SLF001
                field._write_pre_render_nullability(data_type)  # noqa: SLF001

    def _render_dedup_key(self, class_name: str | None) -> tuple[Any, ...]:
        """Render the hashable deduplication key for a class name without caching it.

        The key ends with the values as written of the enum or the Literals the model holds only some values of
        (``GenerationStore.record_values_as_written``), so models stay apart as their schemas were written. A root
        model decides its sequence interface first (``_prepare_dedup_key``).
        """
        key = tuple(to_hashable(v) for v in (self.render(class_name=class_name), self.imports))
        if written := self._written_values_by_position():
            key = (*key, to_hashable(written))
        if (rules := self._unwritable_runtime_rules) is not None:
            key = (*key, ("schema-runtime-validation", rules._written_form()))  # noqa: SLF001
        return (*key, ("target-null", target_null)) if (target_null := self._target_null_positions()) else key

    def _target_null_positions(self) -> tuple[int, ...]:
        """Return the positions of the types whose None is the one the schema they reference admits.

        Only ``--omit-redundant-reference-null`` marks them (``GenerationStore.mark_target_null``); it drops such a
        None later, so models whose types render alike today stay apart when their None comes from different places.
        """
        return tuple(
            position
            for position, data_type in enumerate(
                data_type for field in self.fields for data_type in field.data_type.all_data_types
            )
            if data_type.has_target_null
        )

    def _written_values_by_position(self) -> tuple[tuple[int, tuple[Any, ...]], ...]:
        """Return the values as written that the model and the types of its fields keep, by their position."""
        holders = chain((self,), (data_type for field in self.fields for data_type in field.data_type.all_data_types))
        return tuple(
            (position, values)
            for position, holder in enumerate(holders)
            if (values := holder.__dict__.get(VALUES_AS_WRITTEN_KEY)) is not None
        )

    def _pydantic_constructs(self) -> Iterator[tuple[PydanticConstruct, str | None]]:
        """Yield the Pydantic constructs that the model's rendering writes, each with the field that writes it.

        The field is None for a construct of the model itself, and for the fields of a root model or an alias.
        """
        if self._PYDANTIC_FORM is not None:
            yield self._PYDANTIC_FORM, None
        names_fields = not self.IS_ROOT_MODEL and not self.IS_ALIAS
        for field in self.fields:
            for construct in field._pydantic_constructs():  # noqa: SLF001
                yield construct, field.name if names_fields else None

    def create_reuse_model(self, base_ref: Reference) -> Self:
        """Create inherited model with empty fields pointing to base reference."""
        reuse_model = self._derive(
            reference=Reference(name=self.name, path=self.reference.path + "/reuse"),
            fields=[],
            base_classes=[base_ref],
            description=self.description,
        )
        reuse_model.reused_reference = self.reference
        return reuse_model

    def _derive(
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        base_classes: list[Reference] | None = None,
        description: str | None = None,
    ) -> Self:
        """Build a model of this class from ``reference``, ``fields`` and the rest, carrying over this model's options.

        The options are those ``_derived_options`` returns; a model built from another one goes through here.
        """
        return self.__class__(
            reference=reference,
            fields=fields,
            base_classes=base_classes,
            description=description,
            **self._derived_options(),
        )

    def _derived_options(self) -> dict[str, Any]:
        """Return the constructor options a model built from this one carries over."""
        return {
            "custom_template_dir": self._custom_template_dir,
            "custom_base_class": self.custom_base_class,
            "keyword_only": self.keyword_only,
            "treat_dot_as_module": self._treat_dot_as_module,
        }

    def _set_deprecated_decorator(self) -> None:
        """Add a class-level deprecated decorator when schema metadata requires it.

        Every target imports the decorator from its backport.
        """
        if not self.extra_template_data.get("deprecated"):
            return
        from datamodel_code_generator._python_decorator import is_named_python_decorator  # noqa: PLC0415

        if not any(is_named_python_decorator(decorator, "deprecated") for decorator in self.decorators):
            message = f"{self.class_name} is deprecated."
            self.decorators = [*self.decorators, f"@deprecated({message!r})"]
        self._additional_imports.append(Import.from_full_path(backport_path("python.deprecated_decorator")))

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
    @override
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

    @override
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
    @override
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
        """Get all imports required by this model and its fields: the fields' kept imports, then the model's own."""
        return chain_as_tuple(
            (i for f in self.fields for i in field_imports(f)),
            self._additional_imports,
        )

    def _unrendered_imports(self) -> tuple[Import, ...]:  # ruff: ignore[no-self-use]
        """Return the part of ``imports`` that the class statement does not render.

        The module binds their names, so names and reuse decisions read them, but its import block leaves them out.
        """
        return ()

    def _field_assignment_facts(self, field: DataModelFieldBase) -> AssignmentFacts:
        """Return what the class body writes for the field and what the field is in the model's constructor.

        Constructor ordering, keyword-only fixes, the hidden-type checks and the signature warning read the field
        through this, classified once by the model's row, or by its own hooks (rule ``hook``).
        """
        cls = type(self)
        owner = next(base for base in cls.__mro__ if "_ASSIGNMENT_ROW" in vars(base))
        if (
            (row := getattr(owner, "_ASSIGNMENT_ROW")) is not None  # noqa: B009
            and type(field)._get_constructor_default_info is DataModelFieldBase._get_constructor_default_info  # noqa: SLF001
            and all(getattr(cls, name) is getattr(owner, name) for name in _ASSIGNMENT_HOOK_NAMES)
        ):
            return _classify_field_assignment(field, row)
        assigned = cls.FIELD_ASSIGNMENT_CHECKER(field)
        has_default, has_value = cls.FIELD_DEFAULT_CLASSIFIER(field)
        return AssignmentFacts(
            assigned=assigned,
            construction_assigned=(cls.FIELD_CONSTRUCTION_ORDER_KEY or cls.FIELD_ASSIGNMENT_CHECKER)(field),
            binds_class_attribute=bool(field.field) or assigned,
            constructor_default="value" if has_value else "factory" if has_default else "none",
            participates_in_constructor=cls.FIELD_PARTICIPATES_IN_CONSTRUCTOR(field),
            rule="hook",
            place_assigned=assigned,
        )

    def field_binds_class_attribute(self, field: DataModelFieldBase) -> bool:
        """Return whether the class body assigns a field call or a default to the field's name."""
        return self._field_assignment_facts(field).binds_class_attribute

    def field_name_hides_types(self, field: DataModelFieldBase) -> bool:  # noqa: PLR6301, ARG002
        """Return whether the field's name hides a type of that name from annotations that are not strings."""
        return True

    def unbind_fields(self, names: Collection[str | None]) -> bool:  # noqa: PLR6301, ARG002
        """Keep the finished class from binding the fields of these names, which hide types its annotations name.

        The result tells whether a field changed. The neutral model policy leaves the fields bound.
        """
        return False

    def resolves_hidden_types_from_strings(self) -> bool:  # noqa: PLR6301
        """Return whether a type hidden by a field of the class needs string annotations on native deferred targets."""
        return True

    def fields_evaluated_at_definition(self) -> Sequence[DataModelFieldBase]:  # noqa: PLR6301
        """Return the fields whose types the statement that defines the model evaluates as it runs.

        Deferred annotations do not defer these types, so a model the module defines at or after this one is quoted
        in them. The neutral model annotates its fields, which deferred annotations leave unevaluated.
        """
        return ()

    def class_attribute_hides_named_type(  # noqa: PLR6301
        self,
        field: DataModelFieldBase,  # noqa: ARG002
        *,
        assigned: bool,
        resolved_late: bool,  # noqa: ARG002
        as_string: bool,  # noqa: ARG002
    ) -> bool:
        """Return whether a class attribute named like the field hides the type that its annotation names.

        ``assigned`` tells that the finished class body assigns a value to the field's name, ``resolved_late``
        that the annotation is resolved once the class exists instead of right after that assignment, and
        ``as_string`` that the library resolves it from a string.
        """
        return assigned

    def assigns_forward_reference_field(  # noqa: PLR6301
        self,
        field: DataModelFieldBase,  # noqa: ARG002
        positions: Mapping[str, int],  # noqa: ARG002
        index: int,  # noqa: ARG002
    ) -> bool:
        """Return whether ``assign_forward_reference_fields`` writes the field by assignment."""
        return False

    def assign_forward_reference_fields(self, positions: Mapping[str, int], index: int) -> bool:
        """Render fields by assignment when ``Field()`` inside ``Annotated`` is unsafe for a forward reference.

        ``positions`` maps the path of each model in the module to its order and ``index`` is this model's order.
        The neutral model policy intentionally changes nothing. Output backends whose target mishandles such a
        field override ``assigns_forward_reference_field``; the result tells whether a field changed.
        """
        changed = False
        for field in self.fields:
            if self.assigns_forward_reference_field(field, positions, index):
                field.use_annotated = False
                changed = True
        return changed

    @deprecated("DataModel.imports is computed at each read; there is no imports cache to clear.", category=None)
    def clear_imports_cache(self) -> None:
        """Do nothing: ``imports`` keeps no cache. Kept so that subclasses that call or override it still work."""

    def invalidate_render_caches(self) -> None:
        """Clear the render-derived model identity after its fields changed, and move its ``deep`` revision."""
        forget_model_memos(self, MemoTable.DEDUP_KEYS, MemoTable.NESTED_MODEL_REQUIRED_FIELDS_BEFORE_NULLABILITY)
        bump_deep(self)

    def refresh_field_config(self) -> None:
        """Update config derived from field types once the parser has finished replacing them."""

    @classmethod
    def finalize_type_alias_users(cls, models: list[DataModel]) -> None:
        """Update config that depends on the type aliases models reach, once every alias body is final."""

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

    @property
    def holds_null(self) -> bool:
        """Whether the class holds null as a value of its own: a root model or type alias whose type admits None.

        A type that names it restates no None under ``--omit-redundant-reference-null``.
        """
        return (self.IS_ROOT_MODEL or self.IS_ALIAS) and len(fields := self.fields) == 1 and fields[0]._admits_none()  # noqa: SLF001

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

    def _name_final_classes(self) -> None:
        """Render the backend settings that name the classes of additional properties once class names are final."""

    def _setting_data_types(self) -> Iterator[DataType]:  # ruff: ignore[no-self-use]
        """Yield the data types that the backend settings of the class write; the neutral model has none."""
        yield from ()

    def _decide_config_items(self) -> None:
        """Decide the config items the class writes, once every fact they read is final; the neutral model has none."""

    @classmethod
    def get_native_hash_model_paths(cls, models: list[DataModel]) -> set[str]:  # noqa: ARG003
        """Return models whose backend hash can replace the legacy set-item hash."""
        return set()

    def enable_identity_hash(self) -> None:
        """Retain the legacy identity hash for a set item without a usable native hash."""
        self._append_internal_template_data("class_body_lines", "__hash__ = object.__hash__")

    @property
    def _keys_take_root_type(self) -> bool:
        """Whether a dict key that names the model takes its root type instead."""
        return False

    @property
    def schema_runtime_validation(self) -> _runtime_validation.SchemaRuntimeValidation | None:
        """Return executable schema rules already prepared by this output model."""
        return self._internal_template_data.get("schema_runtime_validation")

    @property
    def _unwritable_runtime_rules(self) -> _runtime_validation.SchemaRuntimeValidation | None:
        """Return the parser's schema validator rules in the template data of a model whose class cannot write them."""
        if self.SUPPORTS_SCHEMA_RUNTIME_VALIDATION or not (
            rules := self.extra_template_data.get("schema_runtime_validation")
        ):
            return None
        from datamodel_code_generator.model.runtime_validation import (  # noqa: PLC0415
            _is_internal_schema_runtime_validation,
        )

        return rules if _is_internal_schema_runtime_validation(rules) else None

    @property
    def schema_runtime_validation_references(self) -> tuple[Reference, ...]:
        """Return the other models that runtime rules name, which the class body evaluates when it runs."""
        if not (runtime_validation := self.schema_runtime_validation):
            return ()
        return tuple(
            reference
            for rule_data_type in runtime_validation.data_types
            for data_type in rule_data_type.all_data_types
            if (reference := data_type.reference) is not None
            and not _reference_names_model(reference, self, "schema runtime validation references")
        )

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
    @override
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
        """Return the restricted context used by project-owned templates.

        A model whose class cannot write the parser's schema validator rules leaves them out; the parser writes such a
        model as a class that can before it renders for the output.
        """
        if self._unwritable_runtime_rules is None:
            return _safe_extra_template_data(self.extra_template_data, self._internal_template_data)
        return _safe_extra_template_data(
            {key: value for key, value in self.extra_template_data.items() if key != "schema_runtime_validation"},
            self._internal_template_data,
        )

    def _custom_template_data(self) -> dict[str, Any]:
        """Return the legacy unrestricted custom-template context."""
        if not self._internal_template_data:
            return self.extra_template_data
        return {**self.extra_template_data, **self._internal_template_data}

    @override
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
        """Return fields in the representation expected by the selected template, their plans rebuilt if changed."""
        self._refresh_field_render_plans()
        if use_custom_template:
            return self._custom_template_fields
        return self.rendered_fields

    def _refresh_field_render_plans(self) -> None:
        """Rebuild the frozen plan of each field whose contents changed since, as ``render`` may change them.

        A rebuilt plan must account for every item its field declares, as a frozen one, under
        ``DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS``.
        """
        if (ready := RENDER_READY.get()) is None:
            return
        for field in cast("list[PlannedField]", self.fields):
            if (plan := ready.refreshed(field)) is not None and (
                mode := check_mode(CheckArea.RENDER_PLANS)
            ) is not None:
                _check_render_plan_accounting(mode, self, field, plan)

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


def direct_base_models(model: DataModel, stand_ins: Mapping[int, Reference | None] | None = None) -> list[DataModel]:
    """Return the generated models ``model`` inherits from; a base a type override replaced is its stand-in model."""
    if not stand_ins:
        return _find_base_classes(model)
    references = (base.reference or stand_ins.get(id(base)) for base in model.base_classes)
    return [reference.source for reference in references if reference and isinstance(reference.source, DataModel)]


def _base_walk(
    snapshot: GraphSnapshot, models: Iterable[DataModel], stand_ins: Mapping[int, Reference | None] | None = None
) -> ReachedModels[DataModel]:
    """Take the snapshot of ``models`` and the generated models they inherit from, however distant.

    A base a type override replaced is its stand-in model in ``stand_ins``, at every level of the chain.
    """
    return walk_graph(
        snapshot, EdgeKind.BASE, models, _model_key_of_model, lambda model: direct_base_models(model, stand_ins)
    )


def _walked_inherited_field_models(model: DataModel, met: set[ModelKey]) -> list[DataModel]:
    """Return the models whose fields ``model`` inherits, bases first, each once, by walking its bases."""
    if (key := model_key_of(model.reference)) in met:
        return []
    met.add(key)
    models = [
        inherited for base in _find_base_classes(model) for inherited in _walked_inherited_field_models(base, met)
    ]
    models.append(model)
    return models


def _model_ancestor_keys(model: DataModel, stand_ins: Mapping[int, Reference | None] | None) -> frozenset[ModelKey]:
    """Collect the model keys of generated ancestors without assuming direct bases are already sorted."""
    walk = _base_walk(GraphSnapshot.ANCESTORS, (model,), stand_ins)
    return graph_answer(
        GraphSnapshot.ANCESTORS,
        walk.graph.reached(walk.starts[0], (EdgeKind.BASE,)),
        lambda: _walked_ancestors(model, stand_ins),
    )


def _walked_ancestors(model: DataModel, stand_ins: Mapping[int, Reference | None] | None) -> frozenset[ModelKey]:
    """Collect the model keys of the generated ancestors of ``model`` by walking its bases."""
    ancestors: set[ModelKey] = set()
    to_visit = direct_base_models(model, stand_ins)
    while to_visit:
        parent = to_visit.pop()
        if (parent_key := model_key_of(parent.reference)) in ancestors:
            continue
        ancestors.add(parent_key)
        to_visit.extend(direct_base_models(parent, stand_ins))
    return frozenset(ancestors)


def _model_key_of_model(model: DataModel) -> ModelKey:
    """Return the model key of ``model``, the identity C3 merges compare."""
    return model_key_of(model.reference)


def _keyed_answer(site: str, by_key: _AnswerT, by_path: Callable[[], _AnswerT]) -> _AnswerT:
    """Return ``by_key``, what a site finds by model key or Reference identity.

    Under the ``MODEL_GRAPH`` check, a ``by_key`` that differs from ``by_path()``, what the site found by path, name or
    Reference equality before, is a violation.
    """
    if (mode := check_mode(CheckArea.MODEL_GRAPH)) is not None and by_key != by_path():
        mode.violation(Error, f"{site}: the lookups by model key and by path disagree", site=site)
    return by_key


@final
class _ModelKeys:
    """The model keys of the models that one walk, map or cache meets, which it tells models apart by.

    Under the ``MODEL_GRAPH`` check, a model met at the path of another model the same walk met is a violation
    (`_keyed_answer`): told apart by path, the two would be taken for one model.
    """

    __slots__ = ("_keys_by_path", "_met", "_site")

    def __init__(self, site: str) -> None:
        """Start the keys of one walk, map or cache, which ``site`` names in a violation."""
        self._site = site
        self._keys_by_path: dict[str, ModelKey] = {}
        self._met: set[ModelKey] = set()

    def of(self, model: DataModel) -> ModelKey:
        """Return the model key of ``model``."""
        key = model_key_of(model.reference)
        return _keyed_answer(self._site, key, lambda: self._keys_by_path.setdefault(model.path, key))

    def first(self, model: DataModel) -> bool:
        """Return whether the walk meets ``model`` for the first time, and remember it as met."""
        if (key := self.of(model)) in self._met:
            return False
        self._met.add(key)
        return True


def _share_path_identity(root_model: DataModel, inner_model: DataModel) -> None:
    """Make the root model that collapse replaces and the inner model that takes its path name each other.

    Row ``legacy-replaced-root-shares-path``: models were told apart by path, so a reference to either of the two named
    both (`_reference_names_model`).
    """
    root_model.__dict__[_PATH_PARTNER_KEY] = model_key_of(inner_model.reference)
    inner_model.__dict__[_PATH_PARTNER_KEY] = model_key_of(root_model.reference)


def _path_identity_keys(model: DataModel) -> tuple[ModelKey, ...]:
    """Return the model key of ``model``, and the key of the model that shares its path (`_share_path_identity`).

    Row ``legacy-replaced-root-shares-path``: a reference to either of the two names both, so what is recorded under
    the path of one is recorded for both.
    """
    key = model_key_of(model.reference)
    return (key,) if (partner := model.__dict__.get(_PATH_PARTNER_KEY)) is None else (key, partner)


def forget_model_memos(model: DataModel, *tables: MemoTable) -> None:
    """Drop what the current parse's memo keeps for the model in ``tables``; a model still being built keeps nothing."""
    if (reference := vars(model).get("reference")) is not None:
        for table in tables:
            forget(table, model_key_of(reference), model)


def _reference_names_model(reference: Reference, model: DataModel, site: str) -> bool:
    """Return whether ``reference`` names ``model``: whether the two carry one model key.

    Row ``legacy-keyless-reference-by-path``: a reference that no model was built with (one built outside the resolver,
    such as by a parser extension) names the model at its path. Row ``legacy-replaced-root-shares-path``: a reference
    to the model that shares the path of ``model`` since collapse replaced one by the other names it too
    (`_share_path_identity`). Under the ``MODEL_GRAPH`` check, a reference whose path and model key disagree on
    whether it names ``model`` is a violation (`_keyed_answer`).
    """
    by_path = reference.path == model.path
    key = model_key_of(reference)
    named = (
        by_path
        if reference.source is None
        else key in {model_key_of(model.reference), model.__dict__.get(_PATH_PARTNER_KEY)}
    )
    return _keyed_answer(site, named, lambda: by_path)


def sort_data_models_for_mro(
    models: list[DataModel], stand_ins: Mapping[int, Reference | None] | None = None
) -> list[DataModel]:
    """Match the stable descendant-before-ancestor order used for rendered bases."""
    return _sort_for_mro(models, lambda model: _model_ancestor_keys(model, stand_ins))


def _sort_for_mro(models: list[DataModel], ancestors_of: Callable[[DataModel], frozenset[ModelKey]]) -> list[DataModel]:
    """Order ``models`` descendants first, stably, by the model keys of the ancestors ``ancestors_of`` gives."""
    if len(models) <= 1:
        return models.copy()
    ancestor_keys = {model_key_of(model.reference): ancestors_of(model) for model in models}
    return sorted(
        models,
        key=lambda model: sum(model_key_of(model.reference) in ancestors for ancestors in ancestor_keys.values()),
    )


def c3_merge(sequences: list[list[MroT]], key: Callable[[MroT], Hashable]) -> list[MroT]:
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


def linearize_data_models(
    models: list[DataModel], stand_ins: Mapping[int, Reference | None] | None = None
) -> list[DataModel]:
    """Return the effective C3 order for direct generated models; ``stand_ins`` give the bases overrides replaced.

    Each model is linearized after its bases, from the snapshot of the models reached through base classes, a base a
    type override replaced counted at every level. An inheritance cycle, which the generated code cannot define, keeps
    the depth-first walk that cuts the cycle where it meets a model again (`_linearize_depth_first`).
    """
    if len(models) == 1 and not models[0].base_classes:
        return models.copy()
    walk = _base_walk(GraphSnapshot.BASE_CLASS_ORDER, models, stand_ins)
    graph = walk.graph
    if (order := graph.acyclic_order((EdgeKind.BASE,))) is None:
        return _linearize_depth_first(models, stand_ins)
    node_of = walk.node_of

    def ancestors_of(model: DataModel) -> frozenset[ModelKey]:
        return graph.reached(model_key_of(model.reference), (EdgeKind.BASE,))

    def merged(heads: list[DataModel]) -> list[DataModel]:
        heads = _sort_for_mro(heads, ancestors_of)
        return c3_merge(
            [*[lineages[model_key_of(head.reference)].copy() for head in heads], heads.copy()], key=_model_key_of_model
        )

    lineages: dict[ModelKey, list[DataModel]] = {}
    for key in order:
        lineages[key] = [node_of[key], *merged([node_of[base] for base in graph.ordered_targets(key, EdgeKind.BASE)])]
    return [
        node_of[key]
        for key in graph_answer(
            GraphSnapshot.BASE_CLASS_ORDER,
            list(map(_model_key_of_model, merged(list(models)))),
            lambda: list(map(_model_key_of_model, _linearize_depth_first(models, stand_ins))),
        )
    ]


def _linearize_depth_first(
    models: list[DataModel], stand_ins: Mapping[int, Reference | None] | None
) -> list[DataModel]:
    """Return the effective C3 order for direct generated models by a depth-first walk of their bases."""
    linearized_models: dict[ModelKey, list[DataModel]] = {}

    def linearize(model: DataModel, active: frozenset[ModelKey] = frozenset()) -> list[DataModel]:
        model_key = model_key_of(model.reference)
        if cached := linearized_models.get(model_key):
            return cached
        if model_key in active:
            return [model]
        parents = sort_data_models_for_mro(direct_base_models(model, stand_ins), stand_ins)
        result = [
            model,
            *c3_merge(
                [
                    *[linearize(parent, active | {model_key}).copy() for parent in parents],
                    parents.copy(),
                ],
                key=_model_key_of_model,
            ),
        ]
        linearized_models[model_key] = result
        return result

    direct_models = sort_data_models_for_mro(models, stand_ins)
    return c3_merge(
        [
            *[linearize(model).copy() for model in direct_models],
            direct_models.copy(),
        ],
        key=_model_key_of_model,
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
