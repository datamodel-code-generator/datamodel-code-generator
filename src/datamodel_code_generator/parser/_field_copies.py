"""How the parser copies a field: the plain copy, and the copy that resolves a deferred inherited field.

``_copy_data_model_field`` copies a field with its own data type and mutable state, leaving the models its references
name in place, and makes the copy with the names, aliases and other values its caller gives (``FieldIdentity``,
``FieldCopyFacts``), so no copy is finished by hand. ``_copy_resolved_inherited_field`` replaces the placeholder that
a partial override of an inherited property leaves (the deferred keys of ``parser/_passes/_keys.py``) with a copy of
the inherited field that keeps the override's type modifiers, nullability and metadata. ``Parser`` reaches both
through its overridable ``_copy_model_field`` and ``_copy_inherited_field``, and ``parser/base.py`` keeps importing
the names other modules import from it.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from datamodel_code_generator.enums import AllOfMergeMode
from datamodel_code_generator.model.base import _overlay_constraints
from datamodel_code_generator.parser._passes._keys import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
)
from datamodel_code_generator.parser._passes._shared import _InheritedTypeModifiers, placeholder_identity
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.types import NONE, DataType, _copy_data_type, build_union, union_members

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._field_rows import FieldCopyFacts, FieldIdentity
    from datamodel_code_generator.parser._null_facts import NullRule


def _copy_data_model_field(
    field: DataModelFieldBase,
    *,
    data_type: DataType | None = None,
    register_references: bool = True,
    identity: FieldIdentity | None = None,
    **facts: Unpack[FieldCopyFacts],
) -> DataModelFieldBase:
    """Copy a field and its mutable state without copying model references, made with ``identity`` and ``facts``.

    The copy owns its extras, validation aliases and mutable default: copies of the source's, or the ones ``facts``
    gives. It takes every value ``identity`` and ``facts`` give when it is made, so no caller writes them on it later.
    """
    copied_data_type = data_type or _copy_data_type(
        field.data_type,
        register_references=register_references,
    )
    update: dict[str, Any] = {"data_type": copied_data_type, "parent": None}
    if "extras" not in facts:
        update["extras"] = deepcopy(field.extras)
    if field.validation_aliases is not None:
        update["validation_aliases"] = list(field.validation_aliases)
    if "default" not in facts:
        match field.default:
            case dict() | list() | set():
                update["default"] = deepcopy(field.default)
    update.update(facts)
    if identity is not None:
        update.update(identity)
    copied_field = field.model_copy(update=update)
    copied_data_type.parent = copied_field
    return copied_field


def _wraps_one_list(data_type: DataType) -> bool:
    """Return whether ``data_type`` is not a list and holds one member, which is a list."""
    return not data_type.is_list and len(data_type.data_types) == 1 and data_type.data_types[0].is_list


def _get_inherited_type_modifiers(
    data_type: DataType,
    *,
    null_rule: NullRule | None = None,
    drops_null_members: bool = False,
) -> _InheritedTypeModifiers:
    """Compress a partial type before replacing its forward placeholder."""
    list_wrapper = _copy_data_type(data_type, register_references=False) if _wraps_one_list(data_type) else None
    return _InheritedTypeModifiers(
        null_rule=null_rule,
        is_optional=data_type.is_optional,
        is_dict=data_type.is_dict,
        is_list=data_type.is_list,
        is_set=data_type.is_set,
        is_frozen_set=data_type.is_frozen_set,
        is_mapping=data_type.is_mapping,
        is_sequence=data_type.is_sequence,
        is_tuple=data_type.is_tuple,
        tuple_item_count=data_type.tuple_item_count,
        kwargs=deepcopy(data_type.kwargs),
        list_wrapper=list_wrapper,
        drops_null_members=drops_null_members,
    )


def _without_null_members(data_type: DataType, current_type: DataType | _InheritedTypeModifiers) -> DataType:
    """Return a fresh copy of the inherited ``data_type`` without the None members its override excludes."""
    if not (
        isinstance(current_type, _InheritedTypeModifiers)
        and current_type.drops_null_members
        and (members := union_members(data_type))
    ):
        return data_type
    kept = [member for member in members if member.type != NONE or member.data_types]
    rebuilt = data_type.model_copy(update={"data_types": build_union(DataType, kept).data_types})
    for member in rebuilt.data_types:
        member.parent = rebuilt
    return rebuilt


def _merge_data_type_modifiers(
    new_type: DataType,
    current_type: DataType | _InheritedTypeModifiers,
    *,
    preserve_container_shape: bool = False,
    preserve_optional: bool = False,
    preserve_inherited_kwargs: bool = False,
) -> None:
    """Merge an overriding type's container modifiers into an inherited type."""
    if preserve_optional:
        new_type.is_optional = new_type.is_optional or current_type.is_optional
    if isinstance(current_type, _InheritedTypeModifiers) and current_type.excludes_null:
        new_type.is_optional = False
    if preserve_container_shape or new_type._is_container or not (new_type.reference or new_type.type):  # noqa: SLF001
        new_type.is_dict = new_type.is_dict or current_type.is_dict
        new_type.is_list = new_type.is_list or current_type.is_list
        new_type.is_set = new_type.is_set or current_type.is_set
        new_type.is_frozen_set = new_type.is_frozen_set or current_type.is_frozen_set
        new_type.is_mapping = new_type.is_mapping or current_type.is_mapping
        new_type.is_sequence = new_type.is_sequence or current_type.is_sequence
        new_type.is_tuple = new_type.is_tuple or current_type.is_tuple
        if new_type.tuple_item_count is None:
            new_type.tuple_item_count = current_type.tuple_item_count
    if preserve_inherited_kwargs or current_type.kwargs is None:
        return
    if new_type.kwargs is None:
        new_type.kwargs = deepcopy(current_type.kwargs)
        return
    for name, value in current_type.kwargs.items():
        new_type.kwargs[name] = deepcopy(value)


def _inherited_field_nullability(
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    current_type: DataType | _InheritedTypeModifiers,
    *,
    required: bool,
) -> FieldCopyFacts:
    """Return the null facts of the copy that resolves ``field``: the inherited ones, unless its override drops null."""
    nullable, type_has_null = inherited_field.nullable, inherited_field.type_has_null
    match current_type:
        case _InheritedTypeModifiers(excludes_null=True):
            nullable, type_has_null = (False if required or field.nullable is False else None), False
    return {"nullable": nullable, "type_has_null": type_has_null}


def _apply_inherited_field_nullability(
    copied_field: DataModelFieldBase,
    current_type: DataType | _InheritedTypeModifiers,
) -> None:
    """Keep omission optionality separate from the schema's null intersection: an optional copy's type admits None."""
    if not copied_field.required and current_type.is_optional:
        copied_field.data_type.is_optional = True
        GenerationStore.clear_target_null(copied_field.data_type)  # ty: ignore[deprecated]


def _copy_resolved_inherited_field(  # noqa: PLR0913
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    *,
    force_optional: bool = False,
    partial_merge_mode: AllOfMergeMode = AllOfMergeMode.All,
    register_references: bool = True,
    reserved_names: set[str] | None = None,
) -> DataModelFieldBase | None:
    """Resolve a deferred inherited field without copying its reference graph."""
    deferred_type = field.__dict__.get(_DEFERRED_INHERITED_TYPE_KEY)
    deferred_field = field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY)
    match deferred_type, deferred_field:
        case (DataType() | _InheritedTypeModifiers()) as current_type, _:
            metadata_source = field if partial_merge_mode == AllOfMergeMode.NoMerge else inherited_field
            copied_data_type = _copy_data_type(
                inherited_field.data_type,
                register_references=register_references,
            )
            copied_data_type = _without_null_members(copied_data_type, current_type)
            wrapper_template = (
                current_type.list_wrapper
                if isinstance(current_type, _InheritedTypeModifiers)
                else (current_type if _wraps_one_list(current_type) else None)
            )
            if wrapper_template is not None and copied_data_type.is_list:
                wrapper = _copy_data_type(wrapper_template, register_references=register_references)
                wrapper.data_types[0] = copied_data_type
                copied_data_type.parent = wrapper
                copied_data_type = wrapper
            else:
                _merge_data_type_modifiers(
                    copied_data_type,
                    current_type,
                    preserve_inherited_kwargs=partial_merge_mode == AllOfMergeMode.NoMerge,
                )
            required = False if force_optional else inherited_field.required or field.required
            facts: FieldCopyFacts = {
                "required": required,
                **_inherited_field_nullability(field, inherited_field, current_type, required=required),
            }
            if partial_merge_mode != AllOfMergeMode.NoMerge:
                facts["constraints"] = _overlay_constraints(
                    inherited_field.constraints, field.constraints, class_from="first-given"
                )
                merged_extras = deepcopy(metadata_source.extras)
                merged_extras.update(deepcopy(field.extras))
                facts["extras"] = merged_extras
                facts["read_only"] = inherited_field.read_only or field.read_only
                facts["write_only"] = inherited_field.write_only or field.write_only
            if takes_default := (has_default := bool(field.has_default)) or partial_merge_mode != AllOfMergeMode.All:
                facts["default"] = deepcopy(field.default) if has_default else field.default
                facts["has_default"] = has_default
                facts["use_default_with_required"] = has_default and field.use_default_with_required
            copied_field = _copy_data_model_field(
                metadata_source,
                data_type=copied_data_type,
                register_references=False,
                identity=placeholder_identity(field),
                **facts,
            )
            _apply_inherited_field_nullability(copied_field, current_type)
            if takes_default and _RAW_SCHEMA_DEFAULT_KEY in field.__dict__:
                copied_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = field.__dict__[_RAW_SCHEMA_DEFAULT_KEY]
        case _, str():
            identity = placeholder_identity(field)
            if inherited_field.name and (
                inherited_field.name == field.name
                or reserved_names is None
                or inherited_field.name not in reserved_names
            ):
                identity["name"] = inherited_field.name
                identity["alias"] = inherited_field.alias if field.alias is None else field.alias
            copied_field = _copy_data_model_field(
                inherited_field, register_references=register_references, identity=identity, required=field.required
            )
        case _:
            return None

    copied_field.__dict__.pop(_DEFERRED_INHERITED_FIELD_KEY, None)
    copied_field.__dict__.pop(_DEFERRED_INHERITED_CLASS_KEY, None)
    copied_field.__dict__.pop(_DEFERRED_INHERITED_TYPE_KEY, None)
    return copied_field
