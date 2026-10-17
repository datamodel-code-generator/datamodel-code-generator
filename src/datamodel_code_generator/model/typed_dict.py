"""TypedDict model generator.

Generates Python TypedDict classes for use with type checkers.
"""

from __future__ import annotations

import keyword
from typing import TYPE_CHECKING, Any, ClassVar

from datamodel_code_generator._target_unicode import is_identifier
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model.base import UNDEFINED
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
)
from datamodel_code_generator.types import NOT_REQUIRED_PREFIX, READ_ONLY_PREFIX, REQUIRED_PREFIX

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.reference import Reference


escape_characters = str.maketrans({
    "\\": r"\\",
    "'": r"\'",
    "\b": r"\b",
    "\f": r"\f",
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
})


def _field_source_name(field: DataModelFieldBase) -> str | None:
    return field.original_name if field.original_name is not None else field.name


def _is_valid_field_name(field: DataModelFieldBase) -> bool:
    if (name := _field_source_name(field)) is None:  # pragma: no cover
        return False
    return is_identifier(name, field.data_type.python_version) and not keyword.iskeyword(name)


class TypedDict(DataModel):
    """DataModel implementation for Python TypedDict."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypedDict.jinja2"
    BASE_CLASS: ClassVar[str] = "typing.TypedDict"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_TYPED_DICT,)
    REQUIRES_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES: ClassVar[bool] = True
    SUPPORTS_TYPED_DICT_TOTAL_FALSE: ClassVar[bool] = True
    SUPPORTS_DESERIALIZED_DEFAULT_VALUES: ClassVar[bool] = False

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
    def resolve_module_import_conflicts(
        cls,
        models: Iterable[DataModel],
        model_imports: Mapping[DataModel, tuple[Import, ...]],
        imports: Imports,
    ) -> None:
        """Prefer one TypedDict source when a module mixes target-version requirements."""
        if not any(IMPORT_TYPED_DICT_BACKPORT in model_imports[model] for model in models):
            return
        if IMPORT_TYPED_DICT_BACKPORT.import_ not in imports.get(
            IMPORT_TYPED_DICT_BACKPORT.from_, set()
        ) or IMPORT_TYPED_DICT.import_ not in imports.get(IMPORT_TYPED_DICT.from_, set()):
            return
        while imports.counter.get((IMPORT_TYPED_DICT.from_, IMPORT_TYPED_DICT.import_), 0) > 0:
            imports.remove(IMPORT_TYPED_DICT)

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
        parent has closed=True.
        """
        additional_props = self.extra_template_data.get("additionalProperties")
        additional_props_type = self.extra_template_data.get("additionalPropertiesType")
        is_base_class = self.extra_template_data.get("is_base_class", False)

        typed_dict_kwargs: dict[str, str] = {"total": "False"} if self.use_total_false_for_typed_dict else {}

        if additional_props is False and not is_base_class:
            typed_dict_kwargs["closed"] = "True"
        elif additional_props_type and not is_base_class:
            if isinstance(additional_props_type, _InternalTypeExpression):
                typed_dict_kwargs["extra_items"] = additional_props_type.code
            elif len(self.additional_properties_reference_classes):
                typed_dict_kwargs["extra_items"] = represent_untrusted_python_value(additional_props_type)
            else:
                typed_dict_kwargs["extra_items"] = represent_untrusted_public_type_name(additional_props_type)

        if typed_dict_kwargs:
            kwargs_str = ", ".join(f"{k}={v}" for k, v in typed_dict_kwargs.items())
            self._set_internal_template_data("typed_dict_kwargs", typed_dict_kwargs)
            self._set_internal_template_data("typed_dict_kwargs_suffix", f", {kwargs_str}")

    @property
    def _has_pep728_kwargs(self) -> bool:
        """Check if this TypedDict has closed or extra_items kwargs."""
        typed_dict_kwargs = self._internal_template_data.get("typed_dict_kwargs", {})
        return "closed" in typed_dict_kwargs or "extra_items" in typed_dict_kwargs

    @property
    def _use_typeddict_backport(self) -> bool:
        """Check if this TypedDict needs typing_extensions.TypedDict for closed/extra_items."""
        return bool(self.extra_template_data.get("use_typeddict_backport"))

    @property
    def _requires_typeddict_backport(self) -> bool:
        """Check if this TypedDict needs typing_extensions.TypedDict."""
        if self.extra_template_data.get("use_total_false_typeddict_backport"):
            return True
        return self._use_typeddict_backport and self._has_pep728_kwargs

    @property
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

    @property
    def is_functional_syntax(self) -> bool:
        """Check if TypedDict requires functional syntax."""
        return any(not _is_valid_field_name(f) for f in self.fields)

    @property
    def all_fields(self) -> Iterator[DataModelFieldBase]:
        """Iterate over all fields, without docstrings the functional dict literal cannot hold."""
        fields: Iterable[DataModelFieldBase] = self.fields
        if any(base.reference and isinstance(base.reference.source, DataModel) for base in self.base_classes):
            fields = {_field_source_name(field): field for field in self.iter_all_fields()}.values()
        for field in fields:
            yield field.model_copy(update={"use_field_description": False, "use_inline_field_description": False})

    def render(self, *, class_name: str | None = None) -> str:
        """Render TypedDict class with appropriate syntax."""
        use_custom_template = self._uses_custom_root_template
        description = self._template_description(use_custom_template=use_custom_template)
        if not use_custom_template and self.is_functional_syntax:
            description = None
        extra_template_data = self._custom_template_data() if use_custom_template else self._builtin_template_data()
        return self._render(
            class_name=class_name or self.class_name,
            fields=self._template_fields(use_custom_template=use_custom_template),
            decorators=self.decorators,
            base_class=self.base_class,
            methods=self.methods,
            description=description,
            is_functional_syntax=self.is_functional_syntax,
            all_fields=self.all_fields,
            **extra_template_data,
        )


class DataModelField(DataModelFieldBase):
    """Field implementation for TypedDict models.

    For Python 3.13+: uses typing.NotRequired and typing.ReadOnly.
    """

    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_NOT_REQUIRED,)
    DEFAULT_REQUIRED_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_REQUIRED,)
    DEFAULT_READ_ONLY_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_READ_ONLY,)

    def process_const(self) -> None:
        """Process const field constraint using literal type."""
        self._process_const_as_literal()

    @property
    def key(self) -> str:
        """Get escaped field key for TypedDict."""
        name = _field_source_name(self)
        return (name if name is not None else "").translate(escape_characters)

    @property
    def type_hint(self) -> str:
        """Get type hint with ReadOnly and/or key-requiredness wrapper if needed."""
        type_hint = super().type_hint
        # Apply ReadOnly first (inner), then Required/NotRequired (outer)
        if self._read_only:
            type_hint = f"{READ_ONLY_PREFIX}{type_hint}]"
        if requiredness := self._requiredness:
            type_hint = f"{requiredness}{type_hint}]"
        return type_hint

    @property
    def _requiredness(self) -> str:
        """Get the TypedDict key-requiredness qualifier prefix."""
        if not isinstance((parent := self.parent), TypedDict):
            return ""
        match (parent.use_total_false_for_typed_dict, self.required):
            case (True, True):
                requiredness = REQUIRED_PREFIX
            case (False, False):
                requiredness = NOT_REQUIRED_PREFIX
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
    def fall_back_to_nullable(self) -> bool:
        """Check if field should fall back to nullable."""
        return not self._is_optional_typed_dict_key

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get imports including key-requiredness and ReadOnly qualifiers."""
        base_imports = super().imports
        read_only_imports = self.DEFAULT_READ_ONLY_IMPORTS if self._read_only else ()
        if not (requiredness := self._requiredness) and not read_only_imports:
            return base_imports

        match requiredness:
            case requiredness if requiredness == REQUIRED_PREFIX:
                requiredness_imports = self.DEFAULT_REQUIRED_IMPORTS
            case requiredness if requiredness == NOT_REQUIRED_PREFIX:
                requiredness_imports = self.DEFAULT_IMPORTS
            case _:
                requiredness_imports = ()
        return (
            *base_imports,
            *requiredness_imports,
            *read_only_imports,
        )


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
