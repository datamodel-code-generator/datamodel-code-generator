"""TypedDict model generator.

Generates Python TypedDict classes for use with type checkers.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import starmap
from typing import TYPE_CHECKING, Any, ClassVar, cast

from typing_extensions import override

from datamodel_code_generator._container_kinds import container_shape
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._python_syntax import SINGLE_QUOTED_ESCAPES, keyword_argument, subscript_text
from datamodel_code_generator._target_unicode import is_python_name
from datamodel_code_generator.imports import Import, _holds, _remove_all
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model._render_plan import FieldRenderPlan, RuleId, Why
from datamodel_code_generator.model.base import (
    _USE_CLOSED_TYPED_DICT_TEMPLATE_DATA_KEY,
    UNDEFINED,
    _field_source_name,
    _find_base_classes,
    linearize_data_models,
)
from datamodel_code_generator.model.imports import (
    IMPORT_NOT_REQUIRED,
    IMPORT_NOT_REQUIRED_BACKPORT,
    IMPORT_READ_ONLY,
    IMPORT_READ_ONLY_BACKPORT,
    IMPORT_REQUIRED,
    IMPORT_REQUIRED_BACKPORT,
    IMPORT_TYPED_DICT,
    IMPORT_TYPED_DICT_BACKPORT,
)
from datamodel_code_generator.python_literal import (
    _InternalTypeExpression,
    represent_untrusted_public_type_name,
    represent_untrusted_python_value,
    stable_repr,
)
from datamodel_code_generator.types import NOT_REQUIRED, READ_ONLY, REQUIRED

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Collection, Iterable, Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator._mro import MemberRule
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._decisions.hashability import SetItemHash
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType


_KEY_REQUIREDNESS = Why(RuleId("D3.typed-dict.key-requiredness"), keyword="required")
"""A key whose requiredness differs from the class's totality is ``Required[`` or ``NotRequired[``."""
_READ_ONLY = Why(RuleId("D11.typed-dict.read-only"), keyword="readOnly", option="--use-frozen-field")


escape_characters = SINGLE_QUOTED_ESCAPES


def _is_valid_field_name(field: DataModelFieldBase) -> bool:
    if (name := _field_source_name(field)) is None:  # pragma: no cover
        return False
    return is_python_name(name, field.data_type.python_version)


def _written_union(data_types: Iterable[DataType]) -> tuple[frozenset[tuple[object, ...]], bool]:
    """Return the types a union of ``data_types`` writes, in no order, a model by its key, and whether it holds None.

    It reads the facts final at B1. A union writes the members of a member that writes nothing of its own, so a type and
    a union of it alone, or a None member and a nullable type, write the same; a TypedDict writes no constraint, call
    or discriminator.
    """
    members = [(data_type, data_type._is_bare_none) for data_type in data_types]  # noqa: SLF001
    return (
        frozenset(written for data_type, none in members if not none for written in _written(data_type)),
        any(none or data_type.admits_none for data_type, none in members),
    )


def _written(data_type: DataType) -> Iterator[tuple[object, ...]]:
    """Yield the types ``data_type`` writes as a member of a union (``_written_union``)."""
    writes_members = data_type._writes_nothing_of_its_own  # noqa: SLF001
    if writes_members and not (data_type.literals or data_type.enum_member_literals):
        for member in data_type.data_types:
            yield from _written_union((member,))[0]
        return
    members = data_type.data_types
    enum = data_type._enum_member_literal_reference  # noqa: SLF001
    yield (
        data_type.type if (reference := data_type.reference) is None else model_key_of(reference),
        data_type.import_,
        data_type.python_type,
        frozenset((type(value), value) for value in data_type.literals),
        None if enum is None else model_key_of(enum),
        frozenset(member for _, member in data_type.enum_member_literals),
        container_shape(data_type),
        None if (key := data_type.dict_key) is None else _written_union((key,)),
        tuple(_written_union((member,)) for member in members) if data_type.is_tuple else _written_union(members),
    )


def _key_declaration(field: DataModelFieldBase) -> tuple[object, ...]:
    """Return what the annotation of a key declares: whether it is required, ``ReadOnly`` and None, and its types."""
    return (
        field.required,
        field.use_frozen_field and field.read_only,
        field._admits_none(),  # noqa: SLF001
        _written_union((field.data_type,))[0],
    )


def _inherits_written_keys(model: DataModel, kept: Collection[Import]) -> bool:
    """Return whether the model is a TypedDict whose bases are generated models or the classes of ``kept``."""
    return isinstance(model, TypedDict) and all(
        isinstance(reference.source, DataModel) if (reference := base.reference) else base.import_ in kept
        for base in model.base_classes
    )


class TypedDict(DataModel):
    """DataModel implementation for Python TypedDict."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypedDict.jinja2"
    BASE_CLASS: ClassVar[str] = "typing.TypedDict"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPED_DICT,)
    REQUIRES_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES: ClassVar[bool] = True
    SUPPORTS_TYPED_DICT_TOTAL_FALSE: ClassVar[bool] = True
    WRITES_CLOSED_ADDITIONAL_PROPERTIES: ClassVar[bool] = True
    SUPPORTS_DESERIALIZED_DEFAULT_VALUES: ClassVar[bool] = False
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    _PYTHON_CHECKS_BASES: ClassVar[bool] = False
    _BASE_FIELDS: ClassVar[MemberRule] = "nearest"
    _KEEPS_INHERITED_FIELD_TYPES: ClassVar[bool] = True

    @override
    def _declares_inherited_fields(self) -> bool:
        """Return whether the TypedDict declares the keys of its generated bases without them.

        The functional form writes no base, so it declares every key of its bases, naming their types from its own
        module (#6323). A type checker holds a key that a subclass declares again to the type, the requiredness and
        the ``ReadOnly`` of the base's, which would lose the narrower declaration of the schema; such a class takes the
        bases it is configured with instead, so the bases it leaves must declare their keys in the output: generated
        TypedDicts or ``TypedDict``. A custom template keeps the bases as they are.
        """
        if not _find_base_classes(self) or self._uses_custom_root_template:
            return False
        if self.is_functional_syntax:
            return True
        kept = {Import.from_full_path(base) for base in (self.BASE_CLASS, *self._configured_base_classes())}
        ancestors = linearize_data_models([self])
        if not all(_inherits_written_keys(model, kept) for model in ancestors):
            return False
        own = {key: field for field in self.fields if (key := _field_source_name(field)) is not None}
        return any(
            (declared := own.get(_field_source_name(field))) is not None
            and _key_declaration(declared) != _key_declaration(field)
            for ancestor in ancestors[1:]
            for field in ancestor.fields
        )

    @override
    def _class_hash(self, generic_base_config: Mapping[str, Any] | None) -> SetItemHash:
        """Return ``never``: the instances are dicts, which never hash."""
        return self._compared_class_hash(dicts=True)

    @classmethod
    @override
    def store_additional_properties_type(
        cls,
        extra_template_data: dict[str, Any],
        type_hint: str,
        reference_classes: set[str] | None = None,
        *,
        root_model_type: type[DataModel] | None = None,
        imports: tuple[Import, ...] = (),
        use_backport: bool = False,
    ) -> None:
        """Store TypedDict extra-item imports without exposing them to other outputs."""
        super().store_additional_properties_type(
            extra_template_data,
            type_hint,
            reference_classes,
            root_model_type=root_model_type,
            use_backport=use_backport,
        )
        if imports:
            extra_template_data["additionalPropertiesImports"] = imports

    @classmethod
    @override
    def resolve_module_import_conflicts(
        cls,
        models: Iterable[DataModel],
        model_imports: Mapping[DataModel, tuple[Import, ...]],
        imports: Imports,
    ) -> None:
        """Prefer one TypedDict source when a module mixes target-version requirements."""
        if not any(IMPORT_TYPED_DICT_BACKPORT in model_imports[model] for model in models):
            return
        if _holds(imports, IMPORT_TYPED_DICT_BACKPORT) and _holds(imports, IMPORT_TYPED_DICT):
            _remove_all(imports, IMPORT_TYPED_DICT)

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
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize TypedDict model."""
        super().__init__(
            reference=reference,
            fields=fields,
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )
        self.use_total_false_for_typed_dict = bool(self.extra_template_data.get("use_total_false_for_typed_dict"))
        self._setup_typed_dict_kwargs()

    def _setup_typed_dict_kwargs(self) -> None:
        """Set up total, closed, and extra_items keyword arguments.

        For PEP 728 TypedDict support:
        - additionalProperties: false -> closed=True
        - additionalProperties: { type: X } -> extra_items=X

        Note: closed=True is not applied to TypedDicts used as base classes,
        as PEP 728 doesn't allow child TypedDicts to add new fields when
        parent has closed=True. ``--no-use-closed-typed-dict`` writes neither.
        """
        additional_props = self.extra_template_data.get("additionalProperties")
        additional_props_type = self.extra_template_data.get("additionalPropertiesType")
        is_base_class = self.extra_template_data.get("is_base_class", False)

        typed_dict_kwargs: dict[str, str] = {"total": "False"} if self.use_total_false_for_typed_dict else {}

        if (
            additional_props is False
            and not is_base_class
            and self.extra_template_data.get(_USE_CLOSED_TYPED_DICT_TEMPLATE_DATA_KEY, True)
        ):
            typed_dict_kwargs["closed"] = "True"
        elif additional_props_type and not is_base_class:
            if isinstance(additional_props_type, _InternalTypeExpression):
                typed_dict_kwargs["extra_items"] = additional_props_type.code
            elif len(self.additional_properties_reference_classes):
                typed_dict_kwargs["extra_items"] = represent_untrusted_python_value(additional_props_type)
            else:
                typed_dict_kwargs["extra_items"] = represent_untrusted_public_type_name(
                    additional_props_type, target=self.target_python_version
                )

        if typed_dict_kwargs:
            self._set_typed_dict_kwargs(typed_dict_kwargs)

    def _set_typed_dict_kwargs(self, typed_dict_kwargs: dict[str, str]) -> None:
        kwargs_str = ", ".join(starmap(keyword_argument, typed_dict_kwargs.items()))
        self._set_internal_template_data("typed_dict_kwargs", typed_dict_kwargs)
        self._set_internal_template_data("typed_dict_kwargs_suffix", f", {kwargs_str}")

    @property
    @override
    def additional_properties_type(self) -> DataType | None:
        """The type that ``extra_items`` writes, or None where the class writes no ``extra_items``."""
        return self._extra_items_source

    @override
    def _write_additional_properties_type(self, data_type: DataType) -> None:
        """Write ``data_type`` as the type of ``extra_items``, whose imports replace those of the type it replaces."""
        self.extra_template_data.pop("additionalPropertiesImports", None)
        super()._write_additional_properties_type(data_type)

    @property
    def _extra_items_source(self) -> DataType | None:
        """The data type that ``extra_items`` renders once class names are final, or None."""
        typed_dict_kwargs: dict[str, str] = self._internal_template_data.get("typed_dict_kwargs", {})
        additional_props_type = self.extra_template_data.get("additionalPropertiesType")
        if "extra_items" not in typed_dict_kwargs or not isinstance(additional_props_type, _InternalTypeExpression):
            return None
        return cast("DataType | None", additional_props_type.source)

    @override
    def _name_final_classes(self, *, binds_named_classes: bool = False) -> None:
        """Render ``extra_items`` again from the data type it was built from, as field types render.

        The class statement evaluates it as it runs, so it is a string while it names a generated class that is not
        bound by then (``binds_named_classes``), such as the class itself or one its module declares later.
        """
        if (source := self._extra_items_source) is None:
            return
        typed_dict_kwargs: dict[str, str] = self._internal_template_data["typed_dict_kwargs"]
        type_hint = source.type_hint
        quotes = not binds_named_classes and len(self.additional_properties_reference_classes)
        extra_items = stable_repr(type_hint) if quotes else type_hint
        self._set_typed_dict_kwargs({**typed_dict_kwargs, "extra_items": extra_items})

    @override
    def _setting_data_types(self) -> Iterator[DataType]:
        """Yield the data types of ``extra_items``, which names models as field types do."""
        if (source := self._extra_items_source) is not None:
            yield from source.all_data_types

    @override
    def _setting_data_types_read_at_definition(self) -> Iterator[DataType]:
        """Yield the data types of ``extra_items`` where it is written as the type itself, not as a string."""
        if (source := self._extra_items_source) is not None and self._internal_template_data["typed_dict_kwargs"][
            "extra_items"
        ] == source.type_hint:
            yield from source.all_data_types

    @property
    def _has_pep728_kwargs(self) -> bool:
        """Check if this TypedDict has closed or extra_items kwargs."""
        typed_dict_kwargs = self._internal_template_data.get("typed_dict_kwargs", {})
        return "closed" in typed_dict_kwargs or "extra_items" in typed_dict_kwargs

    @property
    def _use_typeddict_backport(self) -> bool:
        """Check if this TypedDict needs typing_extensions.TypedDict for closed/extra_items.

        Its target's ``typing.TypedDict`` takes neither before Python 3.15, whether the parser or template data such
        as ``additionalPropertiesType`` gave them; the template flag still selects the backport for custom outputs.
        """
        return bool(self.extra_template_data.get("use_typeddict_backport")) or self.requires_extra_items_backport(
            self.target_python_version
        )

    @property
    def _requires_typeddict_backport(self) -> bool:
        """Check if this TypedDict needs typing_extensions.TypedDict."""
        if self.extra_template_data.get("use_total_false_typeddict_backport"):
            return True
        return self._use_typeddict_backport and self._has_pep728_kwargs

    @property
    @override
    def base_class(self) -> str:
        """Get base class string with kwargs if needed.

        For PEP 728 support, includes closed=True or extra_items=X in the base class.
        """
        base = super().base_class
        use_custom_template = self._uses_custom_root_template
        template_data = self._custom_template_data() if use_custom_template else self._internal_template_data
        if typed_dict_kwargs_suffix := template_data.get("typed_dict_kwargs_suffix"):
            return f"{base}{typed_dict_kwargs_suffix}"
        return base

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get imports, using the TypedDict backport when required by the target version."""
        extra_item_imports = tuple(
            import_
            for import_ in self.extra_template_data.get("additionalPropertiesImports", ())
            if isinstance(import_, Import)
        )
        base_imports = (*super().imports, *extra_item_imports)
        if not self._requires_typeddict_backport:
            return base_imports
        return (*(i for i in base_imports if i != IMPORT_TYPED_DICT), IMPORT_TYPED_DICT_BACKPORT)

    @override
    def create_reuse_model(self, base_ref: Reference) -> TypedDict:
        """Create a reuse model while preserving total=False behavior."""
        reuse_model = super().create_reuse_model(base_ref)
        if not self.use_total_false_for_typed_dict:
            return reuse_model

        reuse_model.use_total_false_for_typed_dict = True
        reuse_model.extra_template_data["use_total_false_for_typed_dict"] = True
        if self.extra_template_data.get("use_total_false_typeddict_backport"):
            reuse_model.extra_template_data["use_total_false_typeddict_backport"] = True
        TypedDict._setup_typed_dict_kwargs(reuse_model)
        return reuse_model

    @staticmethod
    @override
    def _base_layout_key(field: DataModelFieldBase) -> object:
        """Return the key the schema gives the field: a TypedDict holds it apart from a key of the same Python name."""
        return id(field) if (key := _field_source_name(field)) is None else key

    @property
    def is_functional_syntax(self) -> bool:
        """Check if TypedDict requires functional syntax."""
        return any(not _is_valid_field_name(f) for f in self.fields)

    def _flattened_fields(self) -> Iterable[DataModelFieldBase]:
        """Return the own fields, or with base models the fields they and their bases give, by key."""
        if any(base.reference and isinstance(base.reference.source, DataModel) for base in self.base_classes):
            return {_field_source_name(field): field for field in self.iter_all_fields()}.values()
        return self.fields

    @property
    def all_fields(self) -> Iterator[DataModelFieldBase]:
        """Iterate over all fields, without docstrings the functional dict literal cannot hold."""
        for field in self._flattened_fields():
            yield field.model_copy(update={"use_field_description": False, "use_inline_field_description": False})

    @override
    def fields_evaluated_at_definition(self) -> list[DataModelFieldBase]:
        """Return the fields of the functional form, whose dict literal evaluates their types as the module runs."""
        return list(self._flattened_fields()) if self.is_functional_syntax else []

    @override
    def render(self, *, class_name: str | None = None) -> str:
        """Render TypedDict class with appropriate syntax."""
        use_custom_template = self._uses_custom_root_template
        description = self._template_description(use_custom_template=use_custom_template)
        if not use_custom_template and self.is_functional_syntax:
            description = None
        return self._render_with_template_data(
            use_custom_template=use_custom_template,
            class_name=class_name or self.class_name,
            fields=self._template_fields(use_custom_template=use_custom_template),
            decorators=self.decorators,
            base_class=self.base_class,
            methods=self.methods,
            description=description,
            is_functional_syntax=self.is_functional_syntax,
            all_fields=self.all_fields,
        )


class DataModelField(DataModelFieldBase):
    """Field implementation for TypedDict models.

    For Python 3.13+: uses typing.NotRequired and typing.ReadOnly.
    """

    _FREEZES_RENDER_PLAN: ClassVar[bool] = True
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_NOT_REQUIRED,)
    DEFAULT_REQUIRED_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_REQUIRED,)
    DEFAULT_READ_ONLY_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_READ_ONLY,)

    @override
    def process_const(self) -> None:
        """Process const field constraint using literal type."""
        self._process_const_as_literal()

    @property
    def key(self) -> str:
        """Get escaped field key for TypedDict."""
        name = _field_source_name(self)
        return (name if name is not None else "").translate(escape_characters)

    @property
    @override
    def type_hint(self) -> str:
        """Get type hint with ReadOnly and/or key-requiredness wrapper if needed."""
        type_hint = super().type_hint
        for qualifier, _ in self._render_plan().qualifiers:
            type_hint = subscript_text(qualifier, (type_hint,))
        return type_hint

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Build the plan: the type alone, wrapped in ``ReadOnly`` (inner) and the key's requiredness (outer)."""
        qualifiers: list[tuple[str, Why]] = []
        symbol_imports: list[tuple[Import, Why]] = []
        if read_only := self._read_only:
            qualifiers.append((READ_ONLY, _READ_ONLY))
        if requiredness := self._requiredness:
            qualifiers.append((requiredness, _KEY_REQUIREDNESS))
            requiredness_imports = self.DEFAULT_REQUIRED_IMPORTS if requiredness == REQUIRED else self.DEFAULT_IMPORTS
            symbol_imports.extend((import_, _KEY_REQUIREDNESS) for import_ in requiredness_imports)
        if read_only:
            symbol_imports.extend((import_, _READ_ONLY) for import_ in self.DEFAULT_READ_ONLY_IMPORTS)
        return replace(super()._build_render_plan(), qualifiers=tuple(qualifiers), symbol_imports=tuple(symbol_imports))

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:  # noqa: PLR6301
        """Return what the field writes from ``plan``, to compare a frozen plan with a rebuilt one."""
        return (
            tuple(qualifier for qualifier, _ in plan.qualifiers),
            tuple(import_ for import_, _ in plan.symbol_imports),
        )

    def _declared_render_items(self) -> frozenset[str]:  # noqa: PLR6301
        """Return the items the field declares that its plan must write or omit: a key writes none."""
        return frozenset()

    @property
    def _requiredness(self) -> str:
        """Get the TypedDict key-requiredness qualifier."""
        if not isinstance((parent := self.parent), TypedDict):
            return ""
        match (parent.use_total_false_for_typed_dict, self.required):
            case (True, True):
                requiredness = REQUIRED
            case (False, False):
                requiredness = NOT_REQUIRED
            case _:
                requiredness = ""
        return requiredness

    @property
    def _is_optional_typed_dict_key(self) -> bool:
        """Check if this field is an optional TypedDict key."""
        return not self.required and isinstance(self.parent, TypedDict)

    @property
    def _read_only(self) -> bool:
        """Check if field should be marked as ReadOnly."""
        return self.use_frozen_field and self.read_only and isinstance(self.parent, TypedDict)

    @property
    @override
    def fall_back_to_nullable(self) -> bool:
        """Check if field should fall back to nullable."""
        return not self._is_optional_typed_dict_key

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get imports including key-requiredness and ReadOnly qualifiers."""
        base_imports = super().imports
        if not (symbol_imports := self._render_plan().symbol_imports):
            return base_imports
        return (*base_imports, *(import_ for import_, _ in symbol_imports))


class DataModelFieldReadOnlyBackport(DataModelField):
    """Field implementation for TypedDict models using typing_extensions.ReadOnly.

    For Python 3.11-3.12: uses typing.NotRequired and typing_extensions.ReadOnly.
    """

    DEFAULT_READ_ONLY_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_READ_ONLY_BACKPORT,)


class DataModelFieldBackport(DataModelField):
    """Field implementation for TypedDict models using typing_extensions.

    For Python 3.10: uses typing_extensions.NotRequired and typing_extensions.ReadOnly.
    """

    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_NOT_REQUIRED_BACKPORT,)
    DEFAULT_REQUIRED_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_REQUIRED_BACKPORT,)
    DEFAULT_READ_ONLY_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_READ_ONLY_BACKPORT,)


_rebuild_model_with_datamodel_namespace(DataModelField)
_rebuild_model_with_datamodel_namespace(DataModelFieldReadOnlyBackport)
_rebuild_model_with_datamodel_namespace(DataModelFieldBackport)
