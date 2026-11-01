"""How a model field decides the imports its type hint needs.

``DataModelFieldBase.imports`` collects them through ``_collect_field_imports``: the imports of the field's data type,
filtered and completed by the typing names (``Union``, ``Optional``, ``Annotated``, ``Any``) the hint writes, which
``typing_import_requirements`` gathers over the type tree into a ``_TypingImportNames``. A simple type whose hint
needs no typing name is read without rendering the hint (``can_collect_imports_without_type_hint``). The None a field
writes comes from ``DataModelFieldBase._field_null_source``; ``write_pre_render_nullability`` writes the None of the
nullable models a type names before rendering. Every function takes the field (``FieldImportsHost``) first and
reaches the field's members through it, so a subclass that overrides a member keeps seeing the calls;
``DataModelFieldBase`` keeps each moved method as a delegator, and ``model/base.py`` keeps importing the names other
modules import from it. The module reads data types and references through the protocols below and imports neither
``types`` nor ``reference``: both reach ``model.base``, which imports this module.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, TypeVar

from datamodel_code_generator._internal_utils import to_hashable
from datamodel_code_generator._target_unicode import host_identifiers
from datamodel_code_generator.imports import IMPORT_ANNOTATED, IMPORT_ANY, IMPORT_OPTIONAL, IMPORT_UNION, Import
from datamodel_code_generator.model._decisions.nullability import field_renders_none
from datamodel_code_generator.model._rendered_names import _names_of

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from typing_extensions import Self

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator.model._decisions.nullability import FieldNullSource

_ANY: Final = "Any"
_NONE: Final = "None"
_TYPING_IMPORT_NAMES: frozenset[str] = frozenset({
    IMPORT_ANNOTATED.import_,
    IMPORT_OPTIONAL.import_,
    IMPORT_UNION.import_,
})


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

    return frozenset(node.id for node in _names_of(tree) if node.id in _TYPING_IMPORT_NAMES)


class _ReferenceView(Protocol):
    """A reference as import collection reads it: ``Reference``."""

    @property
    def path(self) -> str:
        """The path of the referenced schema."""

    @property
    def source(self) -> object:
        """The model the reference resolves to."""


class _PythonTypeView(Protocol):
    """A type the document writes as Python (``x-python-type``), as import collection reads it."""

    @property
    def expression(self) -> PythonTypeExpr:
        """The parsed type expression."""


class _DataTypeView(Protocol):  # noqa: PLR0904
    """A data type as import collection reads it: ``DataType``. ``_ANY`` and ``_NONE`` are its ``ANY`` and ``NONE``."""

    @property
    def type(self) -> str | None:
        """The name the hint writes."""

    @property
    def alias(self) -> str | None:
        """The alias the hint writes instead of ``type``."""

    @property
    def reference(self) -> _ReferenceView | None:
        """The model the type names."""

    @property
    def data_types(self) -> Sequence[Self]:
        """The member or item types."""

    @property
    def dict_key(self) -> Self | None:
        """The key type of a mapping."""

    @property
    def literals(self) -> Sequence[object]:
        """The literal values."""

    @property
    def enum_member_literals(self) -> Sequence[tuple[str, str]]:
        """The enum members the type writes as literals."""

    @property
    def import_(self) -> Import | None:
        """The import of the type."""

    @property
    def python_type(self) -> _PythonTypeView | None:
        """The Python type the document writes."""

    @property
    def kwargs(self) -> dict[str, Any] | None:
        """The arguments of a constrained type."""

    @property
    def all_imports(self) -> Iterator[Import]:
        """The imports of the type and its children."""

    @property
    def _annotation_texts(self) -> tuple[str | None, ...]:
        """The annotations the type writes itself."""

    @property
    def _is_container(self) -> bool:
        """Whether the type is a container."""

    @property
    def _own_text(self) -> str | None:
        """The text the type writes of its own: its alias, else its type."""

    @property
    def _writes_nothing_of_its_own(self) -> bool:
        """Whether the type writes no text, names no model and is no container."""

    @property
    def is_union(self) -> bool:
        """Whether the type is a union."""

    @property
    def type_hint(self) -> str:
        """The hint the type writes."""

    @property
    def admits_none(self) -> bool:
        """Whether the type admits None."""

    @property
    def names_nullable_model(self) -> bool:
        """Whether the type names a nullable model."""

    @property
    def is_optional(self) -> bool:
        """Whether the type is optional."""

    @property
    def is_custom_type(self) -> bool:
        """Whether the type is a custom type."""

    @property
    def is_func(self) -> bool:
        """Whether the type is a function call."""

    @property
    def is_dict(self) -> bool:
        """Whether the type is a dict."""

    @property
    def is_list(self) -> bool:
        """Whether the type is a list."""

    @property
    def is_set(self) -> bool:
        """Whether the type is a set."""

    @property
    def is_frozen_set(self) -> bool:
        """Whether the type is a frozen set."""

    @property
    def is_mapping(self) -> bool:
        """Whether the type is a mapping."""

    @property
    def is_sequence(self) -> bool:
        """Whether the type is a sequence."""

    @property
    def is_tuple(self) -> bool:
        """Whether the type is a tuple."""

    @property
    def tuple_item_count(self) -> int | None:
        """The number of items of a fixed-length tuple."""

    @property
    def strict(self) -> bool:
        """Whether the type is strict."""

    @property
    def use_standard_collections(self) -> bool:
        """Whether the hint writes standard collections."""

    @property
    def use_generic_container(self) -> bool:
        """Whether the hint writes generic containers."""

    @property
    def use_union_operator(self) -> bool:
        """Whether the hint writes unions with ``|``."""

    @property
    def preserve_union_member_order(self) -> bool:
        """Whether the union keeps the order of its members."""

    @property
    def use_serialize_as_any(self) -> bool:
        """Whether the hint is wrapped in ``SerializeAsAny``."""

    @property
    def discriminator(self) -> str | None:
        """The discriminator of a union."""

    def _materialize_render_state(self, write: Literal["reference-null", "member-null"], /) -> None:
        """Write the render state ``write`` names."""


_D = TypeVar("_D", bound=_DataTypeView)


class _FieldParent(Protocol):
    """The model that holds a field, as import collection reads it."""

    @property
    def has_forward_reference(self) -> bool:
        """Whether the model's annotations name a model defined after it."""


class FieldImportsHost(Protocol[_D]):
    """A model field as the functions of this module read it: ``DataModelFieldBase`` and its subclasses."""

    @property
    def data_type(self) -> _D:
        """The type of the field."""

    @property
    def parent(self) -> _FieldParent | None:
        """The model that holds the field."""

    @property
    def use_annotated(self) -> bool:
        """Whether the field writes its constraints through ``Annotated``."""

    @property
    def needs_annotated_import(self) -> bool:
        """Whether the field's hint needs the ``Annotated`` import."""

    @property
    def _use_union_operator(self) -> bool:
        """Whether the field's hint writes unions with ``|``."""

    @property
    def has_default_factory(self) -> bool:
        """Whether the field has a default factory."""

    @property
    def nullable(self) -> bool | None:
        """Whether the schema makes the field nullable."""

    @property
    def required(self) -> bool:
        """Whether the field is required."""

    @property
    def type_has_null(self) -> bool | None:
        """Whether the schema type admits null."""

    @property
    def fall_back_to_nullable(self) -> bool:
        """Whether an optional field is nullable by default."""

    def _field_null_source(
        self,
        data_type: _D,
        *,
        pending_reference_null: bool = False,
    ) -> FieldNullSource | None:
        """Return why the field writes None around the hint of ``data_type``."""

    def _collect_field_imports_without_type_hint(self, *, data_type: _D) -> tuple[Import, ...]:
        """Return the imports of a simple type, collected without rendering its hint."""

    def _import_state_target(self, *, needs_annotated: bool, data_type: _D) -> _D | None:
        """Return the data type ``imports`` collects from, or None when it collects without a type hint."""

    def _write_pre_render_nullability(self, data_type: _D) -> None:
        """Write the None of the nullable models ``data_type`` names and of its bare None union members."""

    @staticmethod
    def _import_key(import_: Import | None) -> tuple[str, str | None, str | None, str | None] | None:
        """Return what tells an import apart."""

    @staticmethod
    def _reference_source_import_key(reference: Any) -> tuple[bool, bool] | None:
        """Return what of a reference's source changes the imports."""

    def _can_collect_imports_without_type_hint(
        self,
        *,
        needs_annotated: bool,
        data_type: _D | None = None,
    ) -> bool:
        """Return whether imports can be collected without rendering the type hint."""

    @staticmethod
    def _has_explicit_typing_import_requirements(data_type: _D) -> bool:
        """Return whether the annotations ``data_type`` writes itself name a typing import."""

    def _typing_import_requirements(
        self,
        *,
        needs_annotated: bool,
        data_type: _D | None = None,
    ) -> _TypingImportNames:
        """Return the typing imports the field's hint needs."""

    def _add_typing_import_names(self, data_type: _D, names: _TypingImportNames) -> None:
        """Add the typing imports the hint of ``data_type`` needs to ``names``."""

    def _add_union_typing_import_names(self, data_type: _D, names: _TypingImportNames) -> None:
        """Add the typing imports a union needs."""

    @staticmethod
    def _add_explicit_typing_import_names(data_type: _D, names: _TypingImportNames) -> None:
        """Add the typing imports the annotations ``data_type`` writes itself name."""

    def _data_type_needs_optional_import(self, data_type: _D) -> bool:
        """Return whether ``data_type`` writes ``Optional`` around its hint."""

    def _needs_field_optional_import_from_structure(self, *, data_type: _D | None = None) -> bool:
        """Return whether the field writes ``Optional`` around the hint of ``data_type``."""

    def _data_type_renders_importable_hint(self, data_type: _D) -> bool:
        """Return whether ``data_type`` writes a hint other than None."""

    @classmethod
    def _data_type_writes_hint(cls, data_type: _D) -> bool:
        """Return whether a data type writes a hint: a renderable structure or a hint of its own."""

    @classmethod
    def _mapping_writes_value(cls, data_type: _D) -> bool:
        """Return whether a mapping writes a value type of its own."""

    @staticmethod
    def _data_type_has_renderable_structure(data_type: _D) -> bool:
        """Return whether ``data_type`` has a structure its hint writes."""

    def _data_type_renders_none(self, data_type: _D) -> bool:
        """Return whether ``data_type`` writes None."""

    def _data_type_import_key(self, data_type: _D) -> tuple[Any, ...]:
        """Return what tells the imports of ``data_type`` apart from those of another union member."""

    def _can_skip_data_type_imports(self, *, data_type: _D | None = None) -> bool:
        """Return whether the simple DataType cannot contribute imports."""

    def _needs_optional_import_without_type_hint(self, *, data_type: _D | None = None) -> bool:
        """Return whether the field-level nullable wrapper needs typing.Optional."""

    def _has_renderable_data_type_without_type_hint(self, *, data_type: _D | None = None) -> bool:
        """Return whether this simple DataType renders to something other than None."""


def collect_field_imports(
    field: FieldImportsHost[_D],
    *,
    needs_annotated: bool,
    data_type: _D | None = None,
) -> tuple[Import, ...]:
    """Collect type-hint imports, reading rendered nullability without writing it.

    ``needs_annotated`` is passed in so subclasses can precompute it.
    """
    data_type = data_type or field.data_type
    if field._can_collect_imports_without_type_hint(needs_annotated=needs_annotated, data_type=data_type):  # noqa: SLF001
        return field._collect_field_imports_without_type_hint(data_type=data_type)  # noqa: SLF001
    requirements = field._typing_import_requirements(needs_annotated=needs_annotated, data_type=data_type)  # noqa: SLF001
    imports = tuple(data_type.all_imports)
    if requirements.any_ and IMPORT_ANY not in imports:
        imports = (*requirements.leading_imports, *imports)

    filtered_imports = tuple(import_ for import_ in imports if requirements.allows(import_))
    missing_imports = tuple(import_ for import_ in requirements.trailing_imports if import_ not in filtered_imports)
    if not missing_imports:
        return filtered_imports
    return (*filtered_imports, *missing_imports)


def collect_field_imports_without_type_hint(field: FieldImportsHost[_D], *, data_type: _D) -> tuple[Import, ...]:
    needs_optional = field._needs_optional_import_without_type_hint(data_type=data_type)  # noqa: SLF001
    if field._can_skip_data_type_imports(data_type=data_type):  # noqa: SLF001
        return (IMPORT_OPTIONAL,) if needs_optional else ()

    imports = tuple(data_type.all_imports)
    if needs_optional and IMPORT_OPTIONAL not in imports:
        return (*imports, IMPORT_OPTIONAL)
    return imports


def import_state_data_type(field: FieldImportsHost[_D]) -> _D | None:
    """Return the data type ``imports`` collects from, or None when it collects without a type hint."""
    return field._import_state_target(  # noqa: SLF001
        needs_annotated=field.use_annotated and field.needs_annotated_import, data_type=field.data_type
    )


def import_state_target(field: FieldImportsHost[_D], *, needs_annotated: bool, data_type: _D) -> _D | None:
    if field._can_collect_imports_without_type_hint(needs_annotated=needs_annotated, data_type=data_type):  # noqa: SLF001
        return None
    return data_type


def write_pre_render_nullability(field: FieldImportsHost[_D], data_type: _D) -> None:
    """Write the None of the nullable models the type names and of bare None union members, as L3 imports did."""
    if data_type.reference:
        data_type._materialize_render_state("reference-null")  # noqa: SLF001

    for child in data_type.data_types:
        field._write_pre_render_nullability(child)  # noqa: SLF001
    if data_type.dict_key:
        field._write_pre_render_nullability(data_type.dict_key)  # noqa: SLF001

    if not data_type.is_union or data_type.preserve_union_member_order:
        return
    if any(field._data_type_renders_none(child) for child in data_type.data_types):  # noqa: SLF001
        data_type._materialize_render_state("member-null")  # noqa: SLF001


def import_key(import_: Import | None) -> tuple[str, str | None, str | None, str | None] | None:
    if import_ is None:
        return None
    return (import_.import_, import_.from_, import_.alias, import_.reference_path)


def reference_source_import_key(reference: _ReferenceView | None) -> tuple[bool, bool] | None:
    if reference is None:
        return None
    source = reference.source
    return (bool(getattr(source, "nullable", False)), bool(getattr(source, "is_alias", False)))


def can_collect_imports_without_type_hint(
    field: FieldImportsHost[_D],
    *,
    needs_annotated: bool,
    data_type: _D | None = None,
) -> bool:
    """Return whether imports can be collected without rendering the type hint."""
    if needs_annotated:
        return False
    if field.parent and field.parent.has_forward_reference:
        return False

    data_type = data_type or field.data_type
    if data_type.use_serialize_as_any:
        return False

    if field._has_explicit_typing_import_requirements(data_type):  # noqa: SLF001
        return False

    return not (
        data_type.data_types
        or data_type.dict_key
        or data_type.literals
        or data_type.enum_member_literals
        or (data_type.is_optional and data_type.type == _ANY)
    )


def has_explicit_typing_import_requirements(data_type: _DataTypeView) -> bool:
    add_explicit_typing_import_names(data_type, names := _TypingImportNames())
    return names.union or names.optional or names.annotated


def typing_import_requirements(
    field: FieldImportsHost[_D],
    *,
    needs_annotated: bool,
    data_type: _D | None = None,
) -> _TypingImportNames:
    data_type = data_type or field.data_type
    names = _TypingImportNames()
    field._add_typing_import_names(data_type, names)  # noqa: SLF001
    if needs_annotated:
        names.add(IMPORT_ANNOTATED.import_)
    if not names.optional and field._needs_field_optional_import_from_structure(data_type=data_type):  # noqa: SLF001
        names.add(IMPORT_OPTIONAL.import_)
    return names


def add_typing_import_names(field: FieldImportsHost[_D], data_type: _D, names: _TypingImportNames) -> None:
    """Add the typing imports the hint of ``data_type`` needs to ``names``."""
    field._add_explicit_typing_import_names(data_type, names)  # noqa: SLF001

    if data_type.is_union:
        field._add_union_typing_import_names(data_type, names)  # noqa: SLF001
    elif len(data_type.data_types) == 1:
        field._add_typing_import_names(data_type.data_types[0], names)  # noqa: SLF001

    if data_type.dict_key:
        field._add_typing_import_names(data_type.dict_key, names)  # noqa: SLF001
        if not field._mapping_writes_value(data_type):  # noqa: SLF001
            names.any_ = True

    if data_type.discriminator:
        names.add(IMPORT_ANNOTATED.import_)
    if field._data_type_needs_optional_import(data_type):  # noqa: SLF001
        names.add(IMPORT_OPTIONAL.import_)


def add_union_typing_import_names(field: FieldImportsHost[_D], data_type: _D, names: _TypingImportNames) -> None:
    """Add the typing imports a union needs; one whose members all write nothing or None renders ``Any``."""
    branch_keys: set[tuple[Any, ...]] = set()
    has_none = False
    writes_member = False
    for child in data_type.data_types:
        field._add_typing_import_names(child, names)  # noqa: SLF001
        if field._data_type_renders_none(child):  # noqa: SLF001
            has_none = True
            continue
        branch_keys.add(field._data_type_import_key(child))  # noqa: SLF001
        writes_member = writes_member or field._data_type_writes_hint(child)  # noqa: SLF001

    if has_none and not data_type.preserve_union_member_order and not data_type.use_union_operator:
        names.add(IMPORT_OPTIONAL.import_)
    ordered_none_branch_count = int(data_type.preserve_union_member_order and has_none)
    if not writes_member and data_type.data_types and not ordered_none_branch_count:
        names.any_ = True
    if len(branch_keys) + ordered_none_branch_count > 1 and not data_type.use_union_operator:
        names.add(IMPORT_UNION.import_)


def add_explicit_typing_import_names(data_type: _DataTypeView, names: _TypingImportNames) -> None:
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


def data_type_needs_optional_import(field: FieldImportsHost[_D], data_type: _D) -> bool:
    if data_type.use_union_operator or data_type.type == _ANY:
        return False
    return data_type.admits_none and field._data_type_renders_importable_hint(data_type)  # noqa: SLF001


def needs_field_optional_import_from_structure(field: FieldImportsHost[_D], *, data_type: _D | None = None) -> bool:
    data_type = data_type or field.data_type
    if field._use_union_operator or not field._data_type_renders_importable_hint(data_type):  # noqa: SLF001
        return False
    return field._field_null_source(data_type) is not None  # noqa: SLF001


def data_type_renders_importable_hint(field: FieldImportsHost[_D], data_type: _D) -> bool:
    if field._data_type_renders_none(data_type):  # noqa: SLF001
        return False
    return field._data_type_has_renderable_structure(data_type)  # noqa: SLF001


def data_type_writes_hint(field_type: type[FieldImportsHost[_D]], data_type: _D) -> bool:
    """Return whether a data type writes a hint: a renderable structure or a hint of its own."""
    return field_type._data_type_has_renderable_structure(data_type) or bool(data_type.type_hint)  # noqa: SLF001


def mapping_writes_value(field_type: type[FieldImportsHost[_D]], data_type: _D) -> bool:
    """Return whether a mapping writes a value type of its own; one with a key type writes ``Any`` otherwise."""
    return bool(data_type._own_text) or any(  # noqa: SLF001
        field_type._data_type_writes_hint(child)  # noqa: SLF001
        for child in data_type.data_types
    )


def data_type_has_renderable_structure(data_type: _DataTypeView) -> bool:
    if not data_type._writes_nothing_of_its_own:  # noqa: SLF001
        return True
    return bool(data_type.data_types or data_type.dict_key or data_type.literals or data_type.enum_member_literals)


def field_admits_none(field: FieldImportsHost[_D]) -> bool:
    """Return whether the field's annotation admits None, from the facts final at B1 and without rendering it."""
    data_type = field.data_type
    return (
        data_type.admits_none
        or field._data_type_renders_none(data_type)  # noqa: SLF001
        or field_renders_none(
            hint_has_none=False,
            has_default_factory=field.has_default_factory,
            nullable=field.nullable,
            required=(required := field.required),
            type_has_null=required and field.type_has_null,
            fall_back_to_nullable=not required and field.fall_back_to_nullable,
        )
        is not None
    )


def data_type_renders_none(field: FieldImportsHost[_D], data_type: _D) -> bool:
    if data_type._is_container:  # noqa: SLF001
        return False
    if data_type.alias or data_type.reference or data_type.literals or data_type.enum_member_literals:
        return False
    if data_type.type == _NONE:
        return not data_type.data_types
    return len(data_type.data_types) == 1 and field._data_type_renders_none(data_type.data_types[0])  # noqa: SLF001


def data_type_import_key(field: FieldImportsHost[_D], data_type: _D) -> tuple[Any, ...]:
    return (
        data_type.__class__,
        data_type.alias,
        data_type.type,
        data_type.python_type,
        field._import_key(data_type.import_),  # noqa: SLF001
        data_type.reference.path if data_type.reference else None,
        field._reference_source_import_key(data_type.reference),  # noqa: SLF001
        tuple(field._data_type_import_key(child) for child in data_type.data_types),  # noqa: SLF001
        field._data_type_import_key(data_type.dict_key) if data_type.dict_key else None,  # noqa: SLF001
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


def can_skip_data_type_imports(field: FieldImportsHost[_D], *, data_type: _D | None = None) -> bool:
    """Return whether the simple DataType cannot contribute imports."""
    data_type = data_type or field.data_type
    return not (
        data_type.import_
        or data_type.python_type
        or data_type.kwargs
        or data_type.is_optional
        or data_type._is_container  # noqa: SLF001
        or data_type.use_generic_container
    )


def needs_optional_import_without_type_hint(field: FieldImportsHost[_D], *, data_type: _D | None = None) -> bool:
    """Return whether the field-level nullable wrapper needs typing.Optional.

    No hint is rendered here, so the None of a nullable model that the type names is not written yet.
    """
    data_type = data_type or field.data_type
    if field._use_union_operator or not field._has_renderable_data_type_without_type_hint(data_type=data_type):  # noqa: SLF001
        return False
    return field._field_null_source(data_type, pending_reference_null=data_type.names_nullable_model) is not None  # noqa: SLF001


def has_renderable_data_type_without_type_hint(field: FieldImportsHost[_D], *, data_type: _D | None = None) -> bool:
    """Return whether this simple DataType renders to something other than None."""
    data_type = data_type or field.data_type
    return not data_type._writes_nothing_of_its_own  # noqa: SLF001
