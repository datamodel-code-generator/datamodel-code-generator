"""What generated code requires: one frozen table of constructs, the library version that has each, and its backport.

A form decision asks whether the target has a construct; a target before the construct's minimum gets an older form,
or the backport the row names. A Python target is always set. An unset Pydantic target answers by the row: ``newest``
gives the newest supported forms, ``unsupported`` writes a construct only for an explicit target that has it.

This module is a leaf: it imports only the standard library, ``typing_extensions`` and ``enums``, so the command line,
presets and ``PythonVersion`` read it without loading the model package.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from typing_extensions import final

from datamodel_code_generator.enums import TargetPydanticVersion, _is_pydantic_version_at_least, _pydantic_version_key

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    "pydantic.string_constraints",
    "pydantic.dataclass_alias",
    "pydantic.field_deprecated",
    "pydantic.dict_key_forward_ref",
    "pydantic.alias_generators",
    "pydantic.compiled_pattern",
    "pydantic.dataclass_type_alias",
    "pydantic.encoded_bytes_length",
    "pydantic.dataclass_own_name",
    "pydantic.protected_namespaces",
    "pydantic.type_alias_module_namespace",
    "pydantic.annotated_forward_ref",
    "pydantic.validate_by_name",
    "pydantic.experimental_missing_sentinel",
    "pydantic.dataclass_signature_order",
    "pydantic.missing_sentinel",
    "pydantic.bytes_pattern",
    "pydantic.timedelta_multiple_of",
    "pydantic.api_target_runtime",
]
Construct: TypeAlias = PythonConstruct | PydanticConstruct


@final
@dataclass(frozen=True, slots=True)
class Requirement:
    """One construct of generated code, the library version that has it, and what an older target writes instead.

    ``form`` is the import the construct writes on a target that has it, and ``backport`` the import that stands in
    for it on an older target. ``unset_target`` is how an unset Pydantic target answers; a Python target is always set.
    """

    construct: Construct
    library: Library
    minimum: str
    unset_target: UnsetTarget = "newest"
    form: str | None = None
    backport: str | None = None


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
    Requirement("pydantic.string_constraints", "pydantic", "2.1"),
    Requirement("pydantic.dataclass_alias", "pydantic", "2.4"),
    Requirement("pydantic.field_deprecated", "pydantic", "2.7"),
    Requirement("pydantic.dict_key_forward_ref", "pydantic", "2.8"),
    Requirement("pydantic.alias_generators", "pydantic", "2.8"),
    Requirement("pydantic.compiled_pattern", "pydantic", "2.8"),
    Requirement("pydantic.dataclass_type_alias", "pydantic", "2.10"),
    Requirement("pydantic.encoded_bytes_length", "pydantic", "2.10"),
    Requirement("pydantic.dataclass_own_name", "pydantic", "2.10"),
    Requirement("pydantic.protected_namespaces", "pydantic", "2.10"),
    Requirement("pydantic.type_alias_module_namespace", "pydantic", "2.10"),
    Requirement("pydantic.annotated_forward_ref", "pydantic", "2.11"),
    Requirement("pydantic.validate_by_name", "pydantic", "2.11", "unsupported"),
    Requirement("pydantic.experimental_missing_sentinel", "pydantic", "2.12", "unsupported"),
    Requirement("pydantic.dataclass_signature_order", "pydantic", "2.13", "unsupported"),
    Requirement("pydantic.missing_sentinel", "pydantic", "2.14", "unsupported", form="pydantic.MISSING"),
    Requirement("pydantic.bytes_pattern", "pydantic", "2.14", "unsupported"),
    Requirement("pydantic.timedelta_multiple_of", "pydantic", "2.14", "unsupported"),
    Requirement("pydantic.api_target_runtime", "pydantic", "2.14"),
)
REQUIREMENTS: Final[Mapping[Construct, Requirement]] = MappingProxyType({row.construct: row for row in _ROWS})
NEWEST_TARGET_PYDANTIC_VERSION: Final = max(TargetPydanticVersion, key=_pydantic_version_key)
_MINIMUM_KEYS: Final[Mapping[Construct, tuple[int, ...]]] = MappingProxyType({
    row.construct: tuple(int(part) for part in row.minimum.split(".")) for row in _ROWS
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
    "NEWEST_TARGET_PYDANTIC_VERSION",
    "REQUIREMENTS",
    "Construct",
    "Library",
    "PydanticConstruct",
    "PythonConstruct",
    "Requirement",
    "UnsetTarget",
    "backport_path",
    "form_path",
    "pydantic_target_meets",
    "python_target_meets",
]
