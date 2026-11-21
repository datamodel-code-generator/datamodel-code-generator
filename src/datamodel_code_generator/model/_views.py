"""Read-only views of the generation graph: what a decision of the decision phase reads.

``DataTypeView``, ``FieldView`` and ``ModelView`` list the facts that are final when the decision phase starts (B1):
the ``fact`` rows of ``parser/_facts.py:ATTRIBUTE_FACTS`` that hold a ``Fact`` final there, and rendered nullability
through ``admits_none`` (``model/_decisions/nullability.py:rendered_nullability``), never the ``is_optional`` that
rendering still writes. Presentation attributes (names, keyword-only and default-validation markers, quoting) are not
part of them. ``ReferenceView`` names a model and exposes nothing. ``DataType``, ``DataModelFieldBase`` and
``DataModel`` satisfy the views structurally, so a view is the object itself; type checking rejects writes through a
view, and with ``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` on a decision reads guarded views that report the
reads of render facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator.model._decisions.hashability import SetItemHash

__all__ = ["DataTypeView", "FieldView", "ModelForm", "ModelView", "ReferenceView"]

ModelForm: TypeAlias = Literal["alias", "root", "enum", "object"]
"""How a model holds its value: a type alias stands for the type of its field, a root model wraps the value of its
field, an enum is one of its members and any other model holds its fields."""


class ReferenceView(Protocol):
    """The reference of a model: which model a type, a base or a reuse names."""


class DataTypeView(Protocol):  # noqa: PLR0904
    """A data type as the decision phase reads it."""

    @property
    def type(self) -> str | None:
        """The scalar or container type name."""

    @property
    def reference(self) -> ReferenceView | None:
        """The model the type names."""

    @property
    def data_types(self) -> Sequence[DataTypeView]:
        """The member or item types."""

    @property
    def dict_key(self) -> DataTypeView | None:
        """The key type of a dict."""

    @property
    def is_func(self) -> bool:
        """Whether the type is a call."""

    @property
    def kwargs(self) -> Mapping[str, object] | None:
        """The arguments of a constrained type."""

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
    def given_container(self) -> str | None:
        """The container class an ``x-python-type`` gives the type, which it is written as."""

    @property
    def tuple_item_count(self) -> int | None:
        """The item count of a fixed-length tuple."""

    @property
    def is_custom_type(self) -> bool:
        """Whether the type is a custom type the schema names."""

    @property
    def python_type(self) -> BoundPythonType | None:
        """The Python type annotation that types it (``x-python-type``), with the imports that bind its names."""

    @property
    def literals(self) -> Sequence[bool | int | str]:
        """The literal values of a literal type."""

    @property
    def enum_member_literals(self) -> Sequence[tuple[str, str]]:
        """The enum members of a literal type, as enum class and member names."""

    @property
    def strict(self) -> bool:
        """Whether the type is strict."""

    @property
    def discriminator(self) -> str | None:
        """The discriminator property of a tagged union."""

    @property
    def admits_none(self) -> bool:
        """Whether the rendered type hint includes None."""

    @property
    def _writes_nothing_of_its_own(self) -> bool:
        """Whether the type writes no text, names no model and is no container, so only its members write."""

    @property
    def _renders_none(self) -> bool:
        """Whether the type writes ``None``: the type None, or a type whose one member writes it."""


class FieldView(Protocol):
    """A field as the decision phase reads it."""

    @property
    def name(self) -> str | None:
        """The field name."""

    @property
    def original_name(self) -> str | None:
        """The property name in the schema."""

    @property
    def alias(self) -> str | None:
        """The alias of the field."""

    @property
    def validation_aliases(self) -> Sequence[str] | None:
        """The aliases that validation accepts."""

    @property
    def serialization_alias(self) -> str | None:
        """The alias that serialization writes."""

    @property
    def data_type(self) -> DataTypeView:
        """The type of the field."""

    @property
    def required(self) -> bool:
        """Whether the field is required."""

    @property
    def nullable(self) -> bool | None:
        """Whether the schema makes the field nullable."""

    @property
    def nullable_without_default(self) -> bool | None:
        """The ``nullable`` of the field as one without its default (``--omit-incomplete-model-defaults``)."""

    @property
    def default(self) -> object:
        """The default value."""

    @property
    def has_default(self) -> bool:
        """Whether the schema gives a default."""

    @property
    def constraints(self) -> object:
        """The constraints of the field."""

    @property
    def const(self) -> bool:
        """Whether the schema fixes the value."""

    @property
    def type_has_null(self) -> bool | None:
        """Whether the schema type admits null."""

    @property
    def read_only(self) -> bool:
        """Whether the field is read-only."""

    @property
    def write_only(self) -> bool:
        """Whether the field is write-only."""

    @property
    def use_default_with_required(self) -> bool:
        """Whether a required field keeps its default."""

    @property
    def extras(self) -> Mapping[str, object]:
        """The field metadata: discriminator, default factory, init and class-variable markers among them."""

    @property
    def written_constraints(self) -> Mapping[str, object]:
        """The constraint values the field writes as ``Field()`` arguments when it is written, by schema keyword."""

    @property
    def union_sibling_constraints(self) -> Mapping[str, object] | None:
        """The constraints the field writes from the keywords a schema declares beside a union, by keyword."""


class ModelView(Protocol):
    """A model as the decision phase reads it."""

    @property
    def reference(self) -> ReferenceView:
        """The reference of the model."""

    @property
    def fields(self) -> Sequence[FieldView]:
        """The fields of the model."""

    @property
    def base_classes(self) -> Sequence[DataTypeView]:
        """The base classes of the model."""

    @property
    def custom_base_class(self) -> str | list[str] | None:
        """The custom base class the options name."""

    @property
    def description(self) -> str | None:
        """The description of the model."""

    @property
    def default(self) -> object:
        """The default value of a root model."""

    @property
    def discriminator_tag(self) -> tuple[object, object] | None:
        """The tag of a variant of a tagged union."""

    @property
    def reused_reference(self) -> ReferenceView | None:
        """The model whose duplicate the model is."""

    @property
    def effective_fields(self) -> Sequence[FieldView]:
        """The named fields of an instance after C3 inheritance and overrides (``get_effective_fields``)."""

    @property
    def holds_null(self) -> bool:
        """Whether the class holds null as its own value: a root model or alias admitting None, or an enum with None."""

    @property
    def form(self) -> ModelForm:
        """How the model holds its value."""

    @property
    def value_base(self) -> str | None:
        """The builtin type an enum subclasses for the values of its members (``str`` of a ``StrEnum``), or None."""

    @property
    def has_length(self) -> bool:
        """Whether the instance of the model has a length: a root model written with the sequence interface."""

    @property
    def additional_properties_type(self) -> DataTypeView | None:
        """The type of the additional properties the class settings write (TypedDict ``extra_items``), or None."""

    def set_item_hash(self, *, user_imports: bool) -> SetItemHash:
        """How the instances of the class hash as set items, by the class alone (``DataModel.set_item_hash``)."""
