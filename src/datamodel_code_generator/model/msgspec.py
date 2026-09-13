"""msgspec.Struct model generator.

Generates Python models using msgspec.Struct for high-performance serialization.
"""

from __future__ import annotations

import keyword
from functools import wraps
from math import isfinite
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Optional, TypeVar

from datamodel_code_generator import Error
from datamodel_code_generator.imports import IMPORT_OPTIONAL, IMPORT_UNION, Import
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model._constraints import PatternConstraints as _Constraints
from datamodel_code_generator.model.base import UNDEFINED, BaseClassDataType, _nested_model_default_factory
from datamodel_code_generator.model.field_name import MsgspecFieldNameResolver
from datamodel_code_generator.model.imports import (
    IMPORT_MSGSPEC_CONVERT,
    IMPORT_MSGSPEC_FIELD,
    IMPORT_MSGSPEC_META,
    IMPORT_MSGSPEC_STRUCT,
    IMPORT_MSGSPEC_UNSET,
    IMPORT_MSGSPEC_UNSETTYPE,
)
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.model.types import DataTypeManager as _DataTypeManager
from datamodel_code_generator.python_literal import (
    _normalize_string,
    represent_python_value,
    represent_untrusted_python_value,
)
from datamodel_code_generator.reference import FieldNameResolver, ModelType
from datamodel_code_generator.types import (
    NONE,
    DataType,
    _create_context_data_type,
    chain_as_tuple,
    merge_normalized_constraint,
    normalize_integer_constraint,
)


class _UNSET:
    def __str__(self) -> str:
        return "UNSET"

    __repr__ = __str__


UNSET = _UNSET()


_ANNOTATED_CONSTRAINTS_CONTEXT: object = object()


class _MsgspecFieldRenderPlan(NamedTuple):
    """Structured msgspec field output and its syntax-owned imports."""

    rendered: str
    needs_field_import: bool
    needs_convert_import: bool
    needs_unset_import: bool


if TYPE_CHECKING:
    from collections import defaultdict
    from pathlib import Path

    from datamodel_code_generator.reference import Reference


def has_field_assignment(field: DataModelFieldBase) -> bool:
    """Return whether a msgspec field renders with a default assignment."""
    return field.use_default_with_required or not (field.required or field.should_strip_default_none())


def get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return msgspec constructor-default semantics."""
    return field._get_constructor_default_info()  # noqa: SLF001  # output-owned field policy hook


DataModelFieldBaseT = TypeVar("DataModelFieldBaseT", bound=DataModelFieldBase)


def import_extender(cls: type[DataModelFieldBaseT]) -> type[DataModelFieldBaseT]:
    """Extend imports property with msgspec-specific imports."""
    original_imports: property = cls.imports

    @wraps(original_imports.fget)
    def new_imports(self: DataModelFieldBaseT) -> tuple[Import, ...]:
        if self.is_class_var:
            return ()
        extra_imports = []
        if isinstance(self, DataModelField):
            plan = self._get_field_render_plan()
            if plan.needs_field_import:
                extra_imports.append(IMPORT_MSGSPEC_FIELD)
            if plan.needs_convert_import:
                extra_imports.append(IMPORT_MSGSPEC_CONVERT)
            if self.needs_meta_import:
                extra_imports.append(IMPORT_MSGSPEC_META)
            if plan.needs_unset_import:
                extra_imports.append(IMPORT_MSGSPEC_UNSET)
        imports = original_imports.fget(self)
        if isinstance(self, DataModelField) and self._not_required and not self.nullable and self.data_type.is_optional:
            imports = tuple(import_ for import_ in imports if import_ != IMPORT_OPTIONAL)
        return chain_as_tuple(imports, extra_imports)

    cls.imports = property(new_imports)
    return cls


class Struct(DataModel):
    """DataModel implementation for msgspec.Struct."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "msgspec.jinja2"
    BASE_CLASS: ClassVar[str] = "msgspec.Struct"
    BASE_CLASS_NAME: ClassVar[str] = "Struct"
    BASE_CLASS_ALIAS: ClassVar[str] = "_Struct"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    FIELD_ASSIGNMENT_CHECKER = staticmethod(has_field_assignment)
    FIELD_DEFAULT_CLASSIFIER = staticmethod(get_field_default_info)
    FIELD_NAME_MODEL_TYPE: ClassVar[ModelType] = ModelType.MSGSPEC
    FIELD_NAME_RESOLVER_CLASS: ClassVar[type[FieldNameResolver]] = MsgspecFieldNameResolver
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = True
    SUPPORTS_KW_ONLY: ClassVar[bool] = True
    REQUIRES_MODEL_LEVEL_KW_ONLY: ClassVar[bool] = True
    SUPPORTS_BOOLEAN_LITERAL: ClassVar[bool] = False
    REQUIRES_TAGGED_UNION_DISCRIMINATOR: ClassVar[bool] = True
    REQUIRES_UNIQUE_FIELD_ALIASES: ClassVar[bool] = True
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS: ClassVar[bool] = True
    CONFIG_MAPPING: ClassVar[dict[tuple[str, Any], tuple[str, Any] | None]] = {
        ("allow_mutation", False): ("frozen", True),
        ("extra_fields", "forbid"): ("forbid_unknown_fields", True),
        ("extra_fields", "allow"): None,
        ("extra_fields", "ignore"): None,
        ("allow_extra_fields", True): None,
        ("allow_population_by_field_name", True): None,
        ("use_attribute_docstrings", True): None,
    }

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
        """Initialize msgspec Struct with fields sorted by field assignment requirement."""
        super().__init__(
            reference=reference,
            fields=sorted(fields, key=has_field_assignment),
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
        self.extra_template_data.setdefault("base_class_kwargs", {})
        self._set_internal_template_data("base_class_kwargs", {})
        if self.keyword_only:
            self.add_base_class_kwarg("kw_only", "True")

    @classmethod
    def render_module_code(cls, models: list[DataModel]) -> str:
        """Reject incompatible generated Struct layouts before rendering a module."""
        for model in models:
            if isinstance(model, cls) and len(model.base_classes) > 1:
                cls._validate_base_layouts(model)
        return ""

    @staticmethod
    def _has_opaque_layout_base(model: DataModel) -> bool:
        """Identify effective configured bases whose inherited slots are unknown."""
        return (
            (custom_base_class := model.custom_base_class) is not None
            and custom_base_class not in (Struct.BASE_CLASS, [Struct.BASE_CLASS])
            and any(not base.reference for base in model.base_classes)
        )

    @staticmethod
    def _find_layout_conflict(bases: list[DataModel]) -> tuple[DataModel, DataModel] | None:
        """Find independent slot owners with linear storage for compatible graphs.

        Field introduction depths distinguish inherited slots from extensions.
        Encounter ranks retain the original diagnostic order independently of
        the parent-first traversal needed to establish each model's layout.
        """
        pending = [(base, False) for base in bases]
        visited: dict[str, int] = {}
        owner_depths: dict[str, int] = {}
        field_depths: dict[str | None, int] = {}
        owners: list[DataModel] = []
        while pending:
            base, complete = pending.pop()
            if not complete and base.path in visited:
                continue
            parents = [
                parent.reference.source
                for parent in base.base_classes
                if parent.reference and isinstance(parent.reference.source, Struct)
            ]
            if not complete:
                visited[base.path] = len(visited)
                if Struct._has_opaque_layout_base(base):
                    return None
                pending.append((base, True))
                pending.extend((parent, False) for parent in reversed(parents))
                continue
            parent_depth = max((owner_depths.get(parent.path, 0) for parent in parents), default=0)
            if new_names := [
                field.name
                for field in base.fields
                if not field.is_class_var and field_depths.get(field.name, len(owners) + 1) > parent_depth
            ]:
                if parent_depth != len(owners):
                    other = min(owners[parent_depth:], key=lambda owner: visited[owner.path])
                    return Struct._select_layout_conflict((other, base), pending, owners, owner_depths, visited)
                owners.append(base)
                for name in new_names:
                    field_depths[name] = len(owners)
                parent_depth = len(owners)
            owner_depths[base.path] = parent_depth
        return None

    @staticmethod
    def _select_layout_conflict(
        conflict: tuple[DataModel, DataModel],
        pending: list[tuple[DataModel, bool]],
        owners: list[DataModel],
        owner_depths: dict[str, int],
        encounter_ranks: dict[str, int],
    ) -> tuple[DataModel, DataModel] | None:
        """Prefer an earlier active slot owner without repeating the layout proof.

        The compact witness establishes incompatibility. An active descendant
        may occur earlier in the emitted bases' preorder, or inherit an opaque
        layout that prevents diagnosing the generated class at all.
        """
        for base, complete in reversed(pending):
            if not complete:
                continue
            parents = [
                parent.reference.source
                for parent in base.base_classes
                if parent.reference and isinstance(parent.reference.source, Struct)
            ]
            ancestor_paths: set[str] = set()
            inherited_names: set[str | None] = set()
            parent_depth = 0
            while parents:
                parent = parents.pop()
                if parent.path in ancestor_paths:
                    continue
                if Struct._has_opaque_layout_base(parent):
                    return None
                ancestor_paths.add(parent.path)
                parent_depth = max(parent_depth, owner_depths.get(parent.path, 0))
                inherited_names.update(field.name for field in parent.fields if not field.is_class_var)
                parents.extend(
                    ancestor.reference.source
                    for ancestor in parent.base_classes
                    if ancestor.reference and isinstance(ancestor.reference.source, Struct)
                )
            if parent_depth < len(owners) and any(
                field.name not in inherited_names and not field.is_class_var for field in base.fields
            ):
                other = min(owners[parent_depth:], key=lambda owner: encounter_ranks[owner.path])
                conflict = other, base
        return conflict

    def _validate_base_layouts(self) -> None:
        """Allow empty mixins and shared layouts, but reject independent slot extensions."""
        if self._uses_custom_root_template:
            return
        bases: list[DataModel] = [
            base.reference.source
            for base in reversed(self.base_classes)
            if base.reference and isinstance(base.reference.source, Struct)
        ]
        if conflict := self._find_layout_conflict(bases):
            other, base = conflict
            msg = (
                f"msgspec.Struct model {self.class_name!r} has incompatible layouts from generated bases "
                f"{other.class_name!r} and {base.class_name!r}."
            )
            raise Error(msg)

    def add_base_class_kwarg(self, name: str, value: str) -> None:
        """Add keyword argument to base class constructor."""
        if isinstance((base_class_kwargs := self.extra_template_data.get("base_class_kwargs")), dict):
            base_class_kwargs[name] = value
        self._internal_template_data["base_class_kwargs"][name] = value

    def _builtin_template_data(self) -> dict[str, Any]:
        """Serialize user msgspec options while retaining generator-owned syntax."""
        template_data = super()._builtin_template_data()
        raw_base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        base_class_kwargs: dict[str, str] = {}
        if isinstance(raw_base_class_kwargs, dict):
            for key, value in raw_base_class_kwargs.items():
                if not isinstance(key, str):
                    continue
                normalized_key = _normalize_string(key)
                if not normalized_key.isidentifier() or keyword.iskeyword(normalized_key):
                    continue
                base_class_kwargs[normalized_key] = represent_untrusted_python_value(value)
        base_class_kwargs.update(self._internal_template_data["base_class_kwargs"])
        return {**template_data, "base_class_kwargs": base_class_kwargs}

    def _custom_template_data(self) -> dict[str, Any]:
        """Preserve legacy raw msgspec options for trusted custom templates."""
        template_data = super()._custom_template_data()
        raw_base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        internal_base_class_kwargs = self._internal_template_data["base_class_kwargs"]
        if not isinstance(raw_base_class_kwargs, dict):
            return {**template_data, "base_class_kwargs": raw_base_class_kwargs}
        return {
            **template_data,
            "base_class_kwargs": {**raw_base_class_kwargs, **internal_base_class_kwargs},
        }

    def apply_discriminator_tag(self, field: DataModelFieldBase, field_name: str, value: Any) -> None:
        """Configure msgspec's tag and exclude the discriminator from instance fields."""
        self.add_base_class_kwarg("tag_field", f"'{field_name}'")
        self.add_base_class_kwarg("tag", repr(value))
        field.extras["is_classvar"] = True

    def has_keyword_only_definition(self) -> bool:
        """Return whether msgspec's class declaration already enables keyword-only fields."""
        if self._internal_template_data["base_class_kwargs"].get("kw_only") == "True":
            return True
        base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        return isinstance(base_class_kwargs, dict) and base_class_kwargs.get("kw_only") in {True, "True"}

    def enable_model_keyword_only(self) -> None:
        """Enable msgspec's class-level keyword-only option."""
        self.add_base_class_kwarg("kw_only", "True")

    @classmethod
    def create_base_class_model(
        cls,
        config: dict[str, Any],
        reference: Reference,
        custom_template_dir: Path | None = None,
        keyword_only: bool = False,  # noqa: FBT001, FBT002
        treat_dot_as_module: bool | None = None,  # noqa: FBT001
    ) -> Struct | None:
        """Create a shared base class model for DRY configuration.

        Creates a Struct that inherits from msgspec.Struct (aliased as _Struct)
        with the specified configuration. Updates the reference path and name in place.
        """
        reference.path = f"#/{cls.BASE_CLASS_NAME}"
        reference.name = cls.BASE_CLASS_NAME

        base_model = cls(
            reference=reference,
            fields=[],
            custom_template_dir=custom_template_dir,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )

        base_model.base_classes = [BaseClassDataType(type=cls.BASE_CLASS_ALIAS)]

        for key, value in config.items():
            mapping_result = cls.CONFIG_MAPPING.get((key, value))
            if mapping_result is None:
                continue
            mapped_key, mapped_value = mapping_result
            base_model.add_base_class_kwarg(mapped_key, str(mapped_value))

        base_model._additional_imports.append(
            Import(from_=IMPORT_MSGSPEC_STRUCT.from_, import_=IMPORT_MSGSPEC_STRUCT.import_, alias=cls.BASE_CLASS_ALIAS)
        )

        return base_model


class Constraints(_Constraints):
    """Constraint model for msgspec fields."""


@import_extender
class DataModelField(DataModelFieldBase):
    """Field implementation for msgspec Struct models."""

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    _FIELD_KEYS: ClassVar[set[str]] = {
        "default",
        "default_factory",
    }
    _META_FIELD_KEYS: ClassVar[set[str]] = {
        "title",
        "description",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_items",
        "max_items",
        "min_length",
        "max_length",
        "pattern",
        "examples",
        # 'unique_items', # not supported by msgspec
    }
    _PARSE_METHOD = "convert"
    _COMPARE_EXPRESSIONS: ClassVar[set[str]] = {"gt", "ge", "lt", "le", "multiple_of"}
    constraints: Optional[Constraints] = None  # noqa: UP045

    def process_const(self) -> None:
        """Process const field constraint."""
        if "const" not in self.extras:
            return
        self.const = True
        self.nullable = False
        const = self.extras["const"]
        if self.data_type.type == "str" and isinstance(const, str):  # pragma: no cover
            self.replace_data_type(self.data_type.__class__(literals=[const]), clear_old_parent=False)

    def _unset_type_data_type(self) -> DataType:
        return self.data_type.__class__.from_import(IMPORT_MSGSPEC_UNSETTYPE)

    def _ordered_union_data_type(self, data_types: list[DataType]) -> DataType:
        if len(data_types) == 1:
            return data_types[0]
        return self.data_type.__class__(
            data_types=data_types,
            use_union_operator=self._use_union_operator,
            preserve_union_member_order=True,
        )

    def _data_type_has_top_level_none(self, data_type: DataType) -> bool:
        if data_type.is_optional or self._data_type_renders_none(data_type):
            return True
        if not data_type.is_union:
            return False
        return any(self._data_type_renders_none(child) for child in data_type.data_types)

    def _field_has_top_level_none(self) -> bool:
        return any((
            self.nullable is True,
            self.type_has_null is True,
            self._data_type_has_top_level_none(self.data_type),
        ))

    @staticmethod
    def _data_type_has_container(data_type: DataType) -> bool:
        return any((
            data_type.is_dict,
            data_type.is_list,
            data_type.is_set,
            data_type.is_frozen_set,
            data_type.is_mapping,
            data_type.is_sequence,
            data_type.is_tuple,
        ))

    def _data_type_is_plain_union(self, data_type: DataType) -> bool:
        return data_type.is_union and not self._data_type_has_container(data_type)

    def _copy_data_type(self, data_type: DataType) -> DataType:
        copied = data_type.model_copy()
        copied.parent = None
        copied.children = []
        copied.data_types = [self._copy_data_type(child) for child in data_type.data_types]
        copied.literals = list(data_type.literals)
        copied.enum_member_literals = list(data_type.enum_member_literals)
        if data_type.dict_key:
            copied.dict_key = self._copy_data_type(data_type.dict_key)
        if data_type.kwargs:
            copied.kwargs = dict(data_type.kwargs)
        return copied

    def _copy_data_type_without_top_level_none(self, data_type: DataType) -> DataType:
        data_type = self._copy_data_type(data_type)
        data_type.is_optional = False
        if data_type.is_union:
            data_type.data_types = [child for child in data_type.data_types if not self._data_type_renders_none(child)]
        return data_type

    def _unset_union_data_type(self) -> DataType:
        unset_type = self._unset_type_data_type()
        if self._data_type_renders_none(self.data_type):
            return self._ordered_union_data_type([self.data_type.__class__(type=NONE), unset_type])

        data_types = []
        has_none = self._field_has_top_level_none()
        match (has_none, self._data_type_is_plain_union(self.data_type)):
            case (False, True):
                data_types.extend(self._copy_data_type(child) for child in self.data_type.data_types)
            case (True, _):
                if self._data_type_has_renderable_structure(
                    base_data_type := self._copy_data_type_without_top_level_none(self.data_type)
                ) and not self._data_type_renders_none(base_data_type):
                    data_types.append(base_data_type)
            case _:
                if self._data_type_has_renderable_structure(
                    base_data_type := self._copy_data_type(self.data_type)
                ) and not self._data_type_renders_none(base_data_type):
                    data_types.append(base_data_type)
        if has_none:
            data_types.append(self.data_type.__class__(type=NONE))
        data_types.append(unset_type)
        return self._ordered_union_data_type(data_types)

    def _annotated_base_data_type(self) -> DataType:
        if not self._field_has_top_level_none():
            return self._copy_data_type(self.data_type)
        return self._copy_data_type_without_top_level_none(self.data_type)

    def _annotated_union_data_type(self, meta: str, *, include_unset: bool) -> DataType:
        data_types = [
            self.data_type.__class__(
                type=f"Annotated[{(base_data_type := self._annotated_base_data_type()).type_hint}, {meta}]",
                data_types=[base_data_type],
                use_union_operator=self.data_type.use_union_operator,
            )
        ]
        if self._field_has_top_level_none():
            data_types.append(self.data_type.__class__(type=NONE))
        if include_unset:
            data_types.append(self._unset_type_data_type())
        return self._ordered_union_data_type(data_types)

    def _annotated_type_hint(self, meta: str) -> str:
        return self._annotated_union_data_type(meta, include_unset=False).type_hint

    def _annotated_unset_union_data_type(self, meta: str) -> DataType:
        return self._annotated_union_data_type(meta, include_unset=True)

    def _type_hint_data_type(self) -> DataType:
        if self._not_required and not self.nullable:
            return self._unset_union_data_type()
        return self.data_type

    def _get_simple_unset_base_type(self) -> str | None:  # noqa: PLR0911
        """Return the existing leaf hint when direct unset rendering is equivalent."""
        parent = self.parent
        data_type = self.data_type
        if type(self) is not DataModelField or type(parent) is not Struct:
            return None
        if self.required or self.nullable or self.use_annotated:
            return None
        if data_type.data_types or data_type.dict_key or data_type.reference or data_type.python_type:
            return None
        if (type_hint := data_type.type) is None or not type_hint.isidentifier():
            return None
        if data_type.literals or data_type.enum_member_literals or data_type.kwargs or data_type.alias:
            return None
        if data_type.is_dict or data_type.is_list or data_type.is_set or data_type.is_frozen_set:
            return None
        if data_type.is_mapping or data_type.is_sequence or data_type.is_tuple:
            return None
        data_type_class = type(data_type)
        if data_type_class is not DataType and data_type_class is not _create_context_data_type(
            "ContextDataType",
            DataType,
            data_type.python_version,
            data_type.use_standard_collections,
            data_type.use_generic_container,
            data_type.use_union_operator,
            data_type.treat_dot_as_module,
            data_type.use_serialize_as_any,
        ):
            return None
        if (
            data_type.is_custom_type
            or data_type.discriminator is not None
            or data_type.is_optional
            or data_type.is_func
        ):
            return None
        if self._has_explicit_typing_import_requirements(data_type) or self.type_has_null is True:
            return None
        if type_hint == NONE:
            return None
        if type_hint == "UnsetType":
            return None
        return type_hint

    def _get_simple_unset_type_hint(self) -> str | None:
        """Render a standard unset union without copying its data type graph."""
        if (type_hint := self._get_simple_unset_base_type()) is None:
            return None

        match self._use_union_operator:
            case True:
                return f"{type_hint} | UnsetType"
            case _:
                pass
        return f"Union[{type_hint}, UnsetType]"

    def _imports_data_type(self, meta: str | None = None) -> DataType:
        if meta is None:
            return self._type_hint_data_type()
        if self.required or self.nullable:
            return self._annotated_union_data_type(meta, include_unset=False)
        if self._not_required:
            return self._annotated_unset_union_data_type(meta)
        return self._type_hint_data_type()

    def _has_numeric_data_type(self, type_name: str) -> bool:
        """Return whether any field data type is the given numeric type."""
        return any(data_type.type == type_name for data_type in self.data_type.all_data_types)

    def _get_strict_field_constraint(
        self, constraint: str, value: Any, *, is_float_type: bool, is_int_type: bool
    ) -> tuple[str, Any] | None:
        """Return a constraint normalized for the field's numeric type.

        Non-finite bounds are dropped because msgspec Meta only accepts finite values.
        """
        if value is None or constraint not in self._COMPARE_EXPRESSIONS:
            return constraint, value
        if isinstance(value, float) and not isfinite(value):
            return None
        if is_float_type:
            return constraint, float(value)
        if is_int_type:
            return normalize_integer_constraint(constraint, value)
        if isinstance(value, float) and value.is_integer():
            return constraint, int(value)
        return constraint, value

    @property
    def field(self) -> str | None:
        """For backwards compatibility."""
        return self._get_field_render_plan().rendered or None

    def _get_field_data_and_import_requirements(self) -> tuple[dict[str, Any], bool]:
        """Return structured field() arguments and whether msgspec.convert produced a factory."""
        data: dict[str, Any] = {k: v for k, v in self.extras.items() if k in self._FIELD_KEYS}
        needs_convert_import = False
        if self.alias is not None:
            data["name"] = self.alias

        if self.default is not UNDEFINED and self.default is not None:
            data["default"] = self.default
        elif self._not_required and "default_factory" not in data:
            data["default"] = None if self.nullable else UNSET

        if self.required and not self.use_default_with_required:
            data = {
                k: v
                for k, v in data.items()
                if k
                not in {
                    "default",
                    "default_factory",
                }
            }
        elif (
            self.default is not UNDEFINED
            and self.default is not None
            and "default_factory" not in data
            and (default_factory := self._get_default_as_struct_model()) is not None
        ):
            data.pop("default")
            data["default_factory"] = default_factory
            needs_convert_import = True

        if "default" in data and isinstance(data["default"], (list, dict, set)) and "default_factory" not in data:
            default_value = data.pop("default")
            if default_value:
                data["default_factory"] = f"lambda: {represent_python_value(default_value)}"
            else:
                data["default_factory"] = type(default_value).__name__

        if (
            self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
            and "default_factory" not in data
        ):
            nested_model_name = self._get_default_factory_for_optional_nested_model()
            if nested_model_name:
                data["default_factory"] = nested_model_name
                data.pop("default", None)

        return data, needs_convert_import

    def _get_field_data(self) -> dict[str, Any]:
        """Return structured field() arguments before rendering."""
        return self._get_field_data_and_import_requirements()[0]

    def _has_default_for_nested_model_factory(self) -> bool:
        """Preserve the UNSET default emitted for optional Struct fields."""
        return not self.required or super()._has_default_for_nested_model_factory()

    def _get_constructor_default_info(self) -> tuple[bool, bool]:
        """Return constructor-default semantics from structured field data."""
        if not has_field_assignment(self) or (self.required and not self.use_default_with_required):
            return False, False
        data, _ = self._get_field_data_and_import_requirements()
        if "default_factory" in data:
            return True, False
        if data and "default" not in data:
            return False, False
        return True, True

    def _get_field_render_plan(self) -> _MsgspecFieldRenderPlan:
        """Build the msgspec field output and imports without parsing rendered Python."""
        data, needs_convert_import = self._get_field_data_and_import_requirements()
        needs_unset_import = data.get("default") is UNSET
        if not data:
            return _MsgspecFieldRenderPlan(
                rendered="",
                needs_field_import=False,
                needs_convert_import=needs_convert_import,
                needs_unset_import=needs_unset_import,
            )

        if len(data) == 1 and "default" in data:
            return _MsgspecFieldRenderPlan(
                rendered=represent_python_value(data["default"]),
                needs_field_import=False,
                needs_convert_import=needs_convert_import,
                needs_unset_import=needs_unset_import,
            )

        kwargs = [f"{k}={v if k == 'default_factory' else represent_python_value(v)}" for k, v in data.items()]
        return _MsgspecFieldRenderPlan(
            rendered=f"field({', '.join(kwargs)})",
            needs_field_import=True,
            needs_convert_import=needs_convert_import,
            needs_unset_import=needs_unset_import,
        )

    def __str__(self) -> str:
        """Generate field() call or default value representation."""
        return self._get_field_render_plan().rendered

    @property
    def type_hint(self) -> str:
        """Return the type hint, using UnsetType for non-required non-nullable fields."""
        if (simple_unset_type_hint := self._get_simple_unset_type_hint()) is not None:
            return simple_unset_type_hint
        if self._not_required and not self.nullable:
            return self._unset_union_data_type().type_hint
        return super().type_hint

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get imports from the structurally rendered msgspec annotation."""
        if self._get_simple_unset_base_type() is not None:
            imports = self._collect_field_imports(needs_annotated=False, data_type=self.data_type)
            if IMPORT_MSGSPEC_UNSETTYPE not in imports:
                imports = (*imports, IMPORT_MSGSPEC_UNSETTYPE)
            if not self._use_union_operator and IMPORT_UNION not in imports:
                imports = (*imports, IMPORT_UNION)
            return imports
        meta = self._get_meta_string() if self.use_annotated else None
        return self._collect_field_imports(
            needs_annotated=meta is not None,
            data_type=self._imports_data_type(meta),
        )

    @property
    def _not_required(self) -> bool:
        return not self.required and isinstance(self.parent, Struct)

    @property
    def fall_back_to_nullable(self) -> bool:
        """Return whether to fall back to nullable type instead of UnsetType."""
        return not self._not_required

    def _get_meta_string(self) -> str | None:
        """Compute Meta(...) string if there are any meta constraints."""
        data: dict[str, Any] = {k: v for k, v in self.extras.items() if k in self._META_FIELD_KEYS}
        has_type_constraints = self.data_type.kwargs is not None and len(self.data_type.kwargs) > 0
        if (
            self.constraints is not None
            and not self.self_reference()
            and not (self.data_type.strict and has_type_constraints)
        ):
            dumped = self.constraints.model_dump()
            has_integer_constraints = any(dumped.get(key) is not None for key in self._COMPARE_EXPRESSIONS)
            is_float_type = has_integer_constraints and self._has_numeric_data_type("float")
            is_int_type = has_integer_constraints and not is_float_type and self._has_numeric_data_type("int")
            constraint_data: dict[str, Any] = {}
            for k, v in dumped.items():
                if k not in self._META_FIELD_KEYS or v is None:
                    continue
                if (
                    normalized := self._get_strict_field_constraint(
                        k, v, is_float_type=is_float_type, is_int_type=is_int_type
                    )
                ) is not None:
                    merge_normalized_constraint(constraint_data, normalized[0], normalized[1])
            data = {**data, **constraint_data}

        if (gt := data.get("gt")) is not None and (ge := data.get("ge")) is not None:
            data.pop("ge" if gt >= ge else "gt")
        if (lt := data.get("lt")) is not None and (le := data.get("le")) is not None:
            data.pop("le" if lt <= le else "lt")

        if (min_items := data.pop("min_items", None)) is not None:
            data["min_length"] = min_items
        if (max_items := data.pop("max_items", None)) is not None:
            data["max_length"] = max_items

        meta_arguments = sorted(f"{k}={represent_python_value(v)}" for k, v in data.items() if v is not None)
        return f"Meta({', '.join(meta_arguments)})" if meta_arguments else None

    @property
    def annotated(self) -> str | None:  # noqa: PLR0911
        """Get Annotated type hint with Meta constraints.

        For ClassVar fields (discriminator tag_field), ClassVar is required
        regardless of use_annotated setting.
        """
        if self.extras.get("is_classvar"):  # pragma: no cover
            meta = self._get_meta_string()
            if self.use_annotated and meta:
                return f"ClassVar[Annotated[{self.type_hint}, {meta}]]"
            return f"ClassVar[{self.type_hint}]"

        if not self.use_annotated:
            return None

        meta = self._get_meta_string()
        if not meta:
            return None

        if self.required:
            return self._annotated_type_hint(meta)

        if self.nullable:  # pragma: no cover
            return self._annotated_type_hint(meta)
        return self._annotated_unset_union_data_type(meta).type_hint

    @property
    def needs_annotated_import(self) -> bool:
        """Check if this field requires the Annotated import.

        ClassVar fields with Meta need Annotated only when use_annotated is True.
        ClassVar fields without Meta don't need Annotated.
        """
        return self.use_annotated and self._get_meta_string() is not None

    @property
    def needs_meta_import(self) -> bool:
        """Check if this field requires the Meta import."""
        return self.use_annotated and self._get_meta_string() is not None

    def _get_default_as_struct_model(self) -> str | None:
        """Convert default value to Struct model using msgspec convert."""
        if self._uses_empty_builtin_container_factory():
            return None

        data_types = self.data_type.data_types or (self.data_type,)
        if self.data_type.is_list and (item_type := data_types[0]).is_list:
            while item_type.is_list and len(item_type.data_types) == 1:
                item_type = item_type.data_types[0]
            if item_type.reference and isinstance(item_type.reference.source, Struct):
                data_types = (item_type,)

        for data_type in data_types:
            # TODO: Check nested data_types
            if data_type.is_dict:
                # TODO: Parse dict model for default
                continue
            if data_type.is_list and len(data_type.data_types) == 1:
                data_type_child = data_type.data_types[0]
                if (
                    data_type_child.reference
                    and (isinstance(data_type_child.reference.source, (Struct, TypeAliasBase)))
                    and isinstance(self.default, list)
                ):
                    return (
                        f"lambda: {self._PARSE_METHOD}({represent_python_value(self.default)},  "
                        f"type=list[{data_type_child.alias or data_type_child.reference.source.class_name}])"
                    )
            elif data_type.reference and (
                isinstance(data_type.reference.source, Struct)
                or (
                    isinstance(data_type.reference.source, TypeAliasBase)
                    and self._type_alias_needs_struct_conversion(data_type.reference.source)
                )
            ):
                if self.data_type.is_union:
                    if not isinstance(self.default, (dict, list)):
                        continue
                    if isinstance(self.default, dict) and any(dt.is_dict for dt in self.data_type.data_types):
                        continue
                model_type = (
                    self.data_type.type_hint
                    if self.data_type.is_list
                    else data_type.alias or data_type.reference.source.class_name
                )
                return f"lambda: {self._PARSE_METHOD}({represent_python_value(self.default)},  type={model_type})"
        return None

    def _type_alias_needs_struct_conversion(self, source: TypeAliasBase) -> bool:
        """Follow alias targets without changing direct defaults or empty factories."""
        if (
            not source.fields
            or not isinstance(self.default, (dict, list))
            or (isinstance(self.default, list) and not self.default)
        ):
            return False

        pending = [source.fields[0].data_type]
        visited = {id(source)}
        has_struct = False
        while pending:
            data_type = pending.pop()
            if data_type.is_dict:
                # Mapping defaults remain outside the existing Struct conversion support.
                return False
            if data_type.reference:
                referenced_model = data_type.reference.source
                if isinstance(referenced_model, Struct):
                    has_struct = True
                elif (
                    isinstance(referenced_model, TypeAliasBase)
                    and referenced_model.fields
                    and id(referenced_model) not in visited
                ):
                    visited.add(id(referenced_model))
                    pending.append(referenced_model.fields[0].data_type)
            else:
                pending.extend(data_type.data_types)
        return has_struct

    def _uses_empty_builtin_container_factory(self) -> bool:
        """Return whether an empty collection can use its zero-cost builtin factory."""
        if self.default:
            return False

        match self.default:
            case list():
                container_attribute = "is_list"
            case dict():
                container_attribute = "is_dict"
            case set():
                container_attribute = "is_set"
            case _:
                return False

        if getattr(self.data_type, container_attribute):
            return True
        return any(getattr(data_type, container_attribute) for data_type in self.data_type.data_types)

    def _get_default_factory_for_optional_nested_model(self) -> str | None:
        """Get default_factory for optional nested Struct model fields.

        Returns the class name if the field type references a Struct,
        otherwise returns None.
        """
        return _nested_model_default_factory(self, Struct)


class DataTypeManager(_DataTypeManager):
    """Type manager for msgspec Struct models."""

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT


_rebuild_model_with_datamodel_namespace(Constraints)
_rebuild_model_with_datamodel_namespace(DataModelField)
