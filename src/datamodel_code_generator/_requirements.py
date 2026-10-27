"""What generated code requires: one frozen table of constructs, the library version that has each, and its backport.

A form decision asks whether the target has a construct; a target before the construct's minimum gets an older form,
or the backport the row names. A Python target is always set. An unset Pydantic target answers by the row: ``newest``
gives the newest supported forms, ``unsupported`` writes a construct only for an explicit target that has it.

Every Pydantic construct generated code can contain has a row, also those written for every target: each ``ConfigDict``
key the generator can write (``config_key``) and the forms older releases reject or ignore. ``pydantic_floor`` reads
the rows of the constructs a run writes and ``pydantic_floor_message`` states the release they need above the target.

This module is a leaf: it imports only the standard library, ``typing_extensions`` and ``enums``, so the command line,
presets and ``PythonVersion`` read it without loading the model package.
"""

from __future__ import annotations

from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

from typing_extensions import final

from datamodel_code_generator._records import record
from datamodel_code_generator.enums import TargetPydanticVersion, _is_pydantic_version_at_least, _pydantic_version_key

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

Library: TypeAlias = Literal["python", "pydantic"]
UnsetTarget: TypeAlias = Literal["newest", "unsupported"]
PythonConstruct: TypeAlias = Literal[
    "python.union_operator",
    "python.type_alias_annotation",
    "python.kw_only_dataclass",
    "python.typed_dict_required",
    "python.typed_dict_not_required",
    "python.builtin_generic_strings",
    "python.strenum",
    "python.type_statement",
    "python.typed_dict_read_only",
    "python.deprecated_decorator",
    "python.deferred_annotations",
    "python.typed_dict_closed",
]
PydanticConstruct: TypeAlias = Literal[
    "pydantic.config_extra",
    "pydantic.config_title",
    "pydantic.config_populate_by_name",
    "pydantic.config_alias_generator",
    "pydantic.config_from_attributes",
    "pydantic.config_frozen",
    "pydantic.config_arbitrary_types_allowed",
    "pydantic.config_protected_namespaces",
    "pydantic.config_use_enum_values",
    "pydantic.config_json_schema_extra",
    "pydantic.config_allow_inf_nan",
    "pydantic.config_hide_input_in_errors",
    "pydantic.config_ignored_types",
    "pydantic.config_loc_by_alias",
    "pydantic.config_revalidate_instances",
    "pydantic.config_ser_json_bytes",
    "pydantic.config_ser_json_timedelta",
    "pydantic.config_str_max_length",
    "pydantic.config_str_min_length",
    "pydantic.config_str_strip_whitespace",
    "pydantic.config_str_to_lower",
    "pydantic.config_str_to_upper",
    "pydantic.config_strict",
    "pydantic.config_validate_assignment",
    "pydantic.config_validate_default",
    "pydantic.config_validate_return",
    "pydantic.config_defer_build",
    "pydantic.config_json_encoders",
    "pydantic.config_schema_generator",
    "pydantic.config_json_schema_mode_override",
    "pydantic.config_json_schema_serialization_defaults_required",
    "pydantic.config_plugin_settings",
    "pydantic.config_ser_json_inf_nan",
    "pydantic.config_validation_error_cause",
    "pydantic.config_cache_strings",
    "pydantic.config_field_title_generator",
    "pydantic.config_model_title_generator",
    "pydantic.config_val_json_bytes",
    "pydantic.config_ser_json_temporal",
    "pydantic.config_url_preserve_empty_path",
    "pydantic.config_val_temporal_unit",
    "pydantic.config_polymorphic_serialization",
    "pydantic.string_constraints",
    "pydantic.union_mode",
    "pydantic.str_bytes_union_pattern",
    "pydantic.coerce_numbers_to_str",
    "pydantic.dataclass_alias",
    "pydantic.regex_engine",
    "pydantic.attribute_docstrings",
    "pydantic.field_deprecated",
    "pydantic.dict_key_forward_ref",
    "pydantic.alias_generators",
    "pydantic.compiled_pattern",
    "pydantic.model_type_alias",
    "pydantic.type_statement",
    "pydantic.encoded_bytes_length",
    "pydantic.dataclass_own_name",
    "pydantic.protected_namespaces",
    "pydantic.type_alias_module_namespace",
    "pydantic.annotated_forward_ref",
    "pydantic.validate_by_name",
    "pydantic.validate_by_alias",
    "pydantic.serialize_by_alias",
    "pydantic.experimental_missing_sentinel",
    "pydantic.dataclass_signature_order",
    "pydantic.missing_sentinel",
    "pydantic.bytes_pattern",
    "pydantic.timedelta_multiple_of",
    "pydantic.api_target_runtime",
]
Construct: TypeAlias = PythonConstruct | PydanticConstruct


@final
@record
class Requirement:
    """One construct of generated code, the library version that has it, and what an older target writes instead.

    ``form`` is the import the construct writes on a target that has it, and ``backport`` the import that stands in
    for it on an older target. ``unset_target`` is how an unset Pydantic target answers; a Python target is always set.
    ``label`` names the construct when a run states the release it needs, and ``config_key`` is the ``ConfigDict``
    key whose rendering writes it.
    """

    construct: Construct
    library: Library
    minimum: str
    unset_target: UnsetTarget = "newest"
    form: str | None = None
    backport: str | None = None
    label: str | None = None
    config_key: str | None = None


_ROWS: Final[tuple[Requirement, ...]] = (
    Requirement("python.union_operator", "python", "3.10"),
    Requirement("python.type_alias_annotation", "python", "3.10"),
    Requirement("python.kw_only_dataclass", "python", "3.10"),
    Requirement(
        "python.typed_dict_required", "python", "3.11", form="typing.Required", backport="typing_extensions.Required"
    ),
    Requirement(
        "python.typed_dict_not_required",
        "python",
        "3.11",
        form="typing.NotRequired",
        backport="typing_extensions.NotRequired",
    ),
    Requirement("python.builtin_generic_strings", "python", "3.11"),
    Requirement("python.strenum", "python", "3.11"),
    Requirement("python.type_statement", "python", "3.12", backport="typing_extensions.TypeAliasType"),
    Requirement(
        "python.typed_dict_read_only", "python", "3.13", form="typing.ReadOnly", backport="typing_extensions.ReadOnly"
    ),
    Requirement("python.deprecated_decorator", "python", "3.13", backport="typing_extensions.deprecated"),
    Requirement("python.deferred_annotations", "python", "3.14"),
    Requirement(
        "python.typed_dict_closed", "python", "3.15", form="typing.TypedDict", backport="typing_extensions.TypedDict"
    ),
    *(
        Requirement(cast("PydanticConstruct", f"pydantic.config_{key}"), "pydantic", minimum, label=key, config_key=key)
        for minimum, key in (
            ("2", "extra"),
            ("2", "title"),
            ("2", "populate_by_name"),
            ("2", "alias_generator"),
            ("2", "from_attributes"),
            ("2", "frozen"),
            ("2", "arbitrary_types_allowed"),
            ("2", "protected_namespaces"),
            ("2", "use_enum_values"),
            ("2", "json_schema_extra"),
            ("2", "allow_inf_nan"),
            ("2", "hide_input_in_errors"),
            ("2", "ignored_types"),
            ("2", "loc_by_alias"),
            ("2", "revalidate_instances"),
            ("2", "ser_json_bytes"),
            ("2", "ser_json_timedelta"),
            ("2", "str_max_length"),
            ("2", "str_min_length"),
            ("2", "str_strip_whitespace"),
            ("2", "str_to_lower"),
            ("2", "str_to_upper"),
            ("2", "strict"),
            ("2", "validate_assignment"),
            ("2", "validate_default"),
            ("2", "validate_return"),
            ("2.1", "defer_build"),
            ("2.1", "json_encoders"),
            ("2.1", "schema_generator"),
            ("2.4", "json_schema_mode_override"),
            ("2.4", "json_schema_serialization_defaults_required"),
            ("2.4", "plugin_settings"),
            ("2.5", "ser_json_inf_nan"),
            ("2.5", "validation_error_cause"),
            ("2.7", "cache_strings"),
            ("2.8", "experimental_defer_build_mode"),
            ("2.8", "field_title_generator"),
            ("2.8", "model_title_generator"),
            ("2.9", "val_json_bytes"),
            ("2.12", "ser_json_temporal"),
            ("2.12", "url_preserve_empty_path"),
            ("2.12", "val_temporal_unit"),
            ("2.13", "polymorphic_serialization"),
        )
    ),
    Requirement("pydantic.string_constraints", "pydantic", "2.1"),
    Requirement("pydantic.union_mode", "pydantic", "2.2", label="Field(union_mode=...)"),
    Requirement("pydantic.str_bytes_union_pattern", "pydantic", "2.2", label="a pattern on a union of str and bytes"),
    Requirement(
        "pydantic.coerce_numbers_to_str",
        "pydantic",
        "2.4",
        label="coerce_numbers_to_str",
        config_key="coerce_numbers_to_str",
    ),
    Requirement("pydantic.dataclass_alias", "pydantic", "2.4"),
    Requirement("pydantic.regex_engine", "pydantic", "2.5", label="regex_engine", config_key="regex_engine"),
    Requirement(
        "pydantic.attribute_docstrings",
        "pydantic",
        "2.7",
        label="use_attribute_docstrings",
        config_key="use_attribute_docstrings",
    ),
    Requirement("pydantic.field_deprecated", "pydantic", "2.7"),
    Requirement("pydantic.dict_key_forward_ref", "pydantic", "2.8"),
    Requirement("pydantic.alias_generators", "pydantic", "2.8"),
    Requirement("pydantic.compiled_pattern", "pydantic", "2.8", label="a pattern compiled with re.compile"),
    Requirement("pydantic.model_type_alias", "pydantic", "2.10", label="a TypeAliasType alias of a model or alias"),
    Requirement("pydantic.type_statement", "pydantic", "2.10", label="a type statement"),
    Requirement("pydantic.encoded_bytes_length", "pydantic", "2.10"),
    Requirement("pydantic.dataclass_own_name", "pydantic", "2.10"),
    Requirement("pydantic.protected_namespaces", "pydantic", "2.10"),
    Requirement("pydantic.type_alias_module_namespace", "pydantic", "2.10"),
    Requirement("pydantic.annotated_forward_ref", "pydantic", "2.11"),
    Requirement(
        "pydantic.validate_by_name",
        "pydantic",
        "2.11",
        "unsupported",
        label="validate_by_name",
        config_key="validate_by_name",
    ),
    Requirement(
        "pydantic.validate_by_alias", "pydantic", "2.11", label="validate_by_alias", config_key="validate_by_alias"
    ),
    Requirement(
        "pydantic.serialize_by_alias", "pydantic", "2.11", label="serialize_by_alias", config_key="serialize_by_alias"
    ),
    Requirement("pydantic.experimental_missing_sentinel", "pydantic", "2.12", "unsupported"),
    Requirement("pydantic.dataclass_signature_order", "pydantic", "2.13", "unsupported"),
    Requirement("pydantic.missing_sentinel", "pydantic", "2.14", "unsupported", form="pydantic.MISSING"),
    Requirement("pydantic.bytes_pattern", "pydantic", "2.14", "unsupported", label="a pattern on bytes"),
    Requirement("pydantic.timedelta_multiple_of", "pydantic", "2.14", "unsupported"),
    Requirement("pydantic.api_target_runtime", "pydantic", "2.14"),
)
REQUIREMENTS: Final[Mapping[Construct, Requirement]] = MappingProxyType({row.construct: row for row in _ROWS})
CONFIG_KEY_REQUIREMENTS: Final[Mapping[str, Requirement]] = MappingProxyType({
    key: row for row in _ROWS if (key := row.config_key) is not None
})
"""The rows of the ``ConfigDict`` keys generated models can write, by key: a view of the table, not a second one."""
_ROW_ORDER: Final[Mapping[Construct, int]] = MappingProxyType({row.construct: index for index, row in enumerate(_ROWS)})
_LISTED_OWNERS: Final = 3
NEWEST_TARGET_PYDANTIC_VERSION: Final = max(TargetPydanticVersion, key=_pydantic_version_key)
_MINIMUM_KEYS: Final[Mapping[Construct, tuple[int, ...]]] = MappingProxyType({
    row.construct: tuple(int(part) for part in row.minimum.split(".")) for row in _ROWS
})
_VERSION_PARTS: Final = 3


def _padded_version_key(key: tuple[int, ...]) -> tuple[int, ...]:
    """Pad a version key with zeros to three parts, so ``2`` compares equal to ``2.0.0``."""
    return key + (0,) * (_VERSION_PARTS - len(key))


_PADDED_MINIMUM_KEYS: Final[Mapping[Construct, tuple[int, ...]]] = MappingProxyType({
    construct: _padded_version_key(key) for construct, key in _MINIMUM_KEYS.items()
})


@cache
def _pydantic_version_meets(target: TargetPydanticVersion | str | None, minimum: str) -> bool:
    """Return whether a target Pydantic version reaches ``minimum``; an unset target is the newest one."""
    return _is_pydantic_version_at_least(NEWEST_TARGET_PYDANTIC_VERSION if target is None else target, minimum)


def pydantic_target_meets(construct: PydanticConstruct, target: TargetPydanticVersion | str | None) -> bool:
    """Return whether a target Pydantic version has ``construct``; an unset target answers by ``unset_target``."""
    requirement = REQUIREMENTS[construct]
    return (target is not None or requirement.unset_target == "newest") and _pydantic_version_meets(
        target, requirement.minimum
    )


def python_target_meets(construct: PythonConstruct, version_key: tuple[int, int]) -> bool:
    """Return whether a target Python version, given as its ``(major, minor)`` key, has ``construct``."""
    return version_key >= _MINIMUM_KEYS[construct]


@final
@record
class PydanticFloor:
    """The Pydantic release a run's models need above its explicit target, and the constructs that need more.

    ``constructs`` holds each construct above the target with the models and fields that write it, highest minimum
    first.
    """

    minimum: str
    target: str
    constructs: tuple[tuple[PydanticConstruct, tuple[str, ...]], ...]


def pydantic_floor(
    target: TargetPydanticVersion | str, used: Iterable[tuple[PydanticConstruct, str]]
) -> PydanticFloor | None:
    """Return the floor of the constructs a run writes, each with its owner, when it is above the explicit target.

    Versions compare padded, so ``2`` equals ``2.0``. Owners keep the order they were first written in.
    """
    target_key = _padded_version_key(_pydantic_version_key(target))
    owners: dict[PydanticConstruct, dict[str, None]] = {}
    for construct, owner in used:
        if target_key < _PADDED_MINIMUM_KEYS[construct]:
            owners.setdefault(construct, {})[owner] = None
    if not owners:
        return None
    ordered = sorted(
        owners, key=lambda construct: (tuple(-part for part in _MINIMUM_KEYS[construct]), _ROW_ORDER[construct])
    )
    return PydanticFloor(
        REQUIREMENTS[ordered[0]].minimum,
        target.value if isinstance(target, TargetPydanticVersion) else target,
        tuple((construct, tuple(owners[construct])) for construct in ordered),
    )


def pydantic_floor_message(floor: PydanticFloor, *, target_from: str | None = None) -> str:
    """Say which Pydantic release the generated models need and which constructs need more than the target.

    ``target_from`` names the option whose minimum the target is; each construct lists three owners, then a count.
    """
    parts: list[str] = []
    for construct, owners in floor.constructs:
        listed = ", ".join(owners[:_LISTED_OWNERS])
        more = f" and {len(owners) - _LISTED_OWNERS} more" if len(owners) > _LISTED_OWNERS else ""
        row = REQUIREMENTS[construct]
        parts.append(f"{row.label} needs {row.minimum} ({listed}{more})")
    source = f", the minimum {target_from} needs" if target_from else ""
    return (
        f"The generated models need pydantic>={floor.minimum}, newer than the target Pydantic version "
        f"{floor.target}{source}: {'; '.join(parts)}."
    )


def form_path(construct: Construct) -> str:
    """Return the import that ``construct`` writes on a target that has it."""
    return _import_path(REQUIREMENTS[construct].form, construct)


def backport_path(construct: Construct) -> str:
    """Return the import that stands in for ``construct`` on a target before its minimum."""
    return _import_path(REQUIREMENTS[construct].backport, construct)


def _import_path(path: str | None, construct: Construct) -> str:
    if path is None:  # pragma: no cover
        msg = f"the requirement row {construct!r} names no such import"
        raise LookupError(msg)
    return path


__all__ = [
    "CONFIG_KEY_REQUIREMENTS",
    "NEWEST_TARGET_PYDANTIC_VERSION",
    "REQUIREMENTS",
    "Construct",
    "Library",
    "PydanticConstruct",
    "PydanticFloor",
    "PythonConstruct",
    "Requirement",
    "UnsetTarget",
    "backport_path",
    "form_path",
    "pydantic_floor",
    "pydantic_floor_message",
    "pydantic_target_meets",
    "python_target_meets",
]
