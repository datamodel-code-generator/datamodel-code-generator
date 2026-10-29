"""The None of a model type as the client and server targets read it from the type's frozen view (H23).

The binder writes into each view the None the model renders (``model/_decisions/nullability.py``), as a `NoneType`
member; these pure functions are the one reading of it, so no target decides by its own walk whether a type takes
None.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._target_contract import (
    BoundType,
    BuiltinType,
    GeneratedSymbolType,
    ImportedType,
    NoneType,
    UnionType,
)
from datamodel_code_generator.model._decisions.nullability import imported_name_null, written_annotation_null

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from datamodel_code_generator._target_contract import FieldUseBinding, FinalModelSymbol, SymbolId, TypeView

__all__ = ["admits_none", "is_none", "present", "takes_null", "without_none"]

_WRAPPERS: Final = frozenset({"root", "alias"})


def is_none(value: TypeView) -> bool:
    """Return whether a type is None itself."""
    return isinstance(value, NoneType)


def _members(value: TypeView) -> tuple[TypeView, ...]:
    return value.members if isinstance(value, UnionType) else (value,)


def admits_none(value: TypeView | None) -> bool:
    """Return whether a type's annotation includes None: it is None or a union with a None member."""
    return value is not None and any(map(is_none, _members(value)))


def present(value: TypeView) -> tuple[TypeView, ...]:
    """Return the members of a union besides None, or the type itself when it is neither a union nor None."""
    return tuple(member for member in _members(value) if not is_none(member))


def without_none(value: TypeView) -> TypeView:
    """Return a union without its None: its one other member, or the union of the others; any other type itself.

    The union of the others keeps the discriminator its schema declares and the one its annotation validates by.
    """
    if not isinstance(value, UnionType) or not admits_none(value):
        return value
    members = present(value)
    return members[0] if len(members) == 1 else replace(value, members=members, hint=None)


def takes_null(
    value: TypeView | None,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
    seen: frozenset[SymbolId] = frozenset(),
) -> bool:
    """Return whether a type validates JSON null, as the generated models annotate it, which both targets send by.

    Any value does without a type, as Any, or as object; so does None, an enum with a null value, a union with such a
    member, a root model or alias whose annotation accepts None or whose type does, and an annotation the document
    writes that accepts None (``written_annotation_null``).
    """
    null = False
    match value:
        case None | NoneType():
            null = True
        case BuiltinType():
            null = value.name == "object"
        case ImportedType():
            null = imported_name_null(module=value.import_.from_, name=value.import_.import_)
        case BoundType():
            null = written_annotation_null(value.binding.expression)
        case UnionType():
            null = any(takes_null(member, symbols, members, seen) for member in value.members)
        case GeneratedSymbolType() if (declared := symbols[value.symbol]).kind == "enum":
            null = "null" in declared.value_types
        case GeneratedSymbolType() if (
            value.symbol not in seen
            and symbols[value.symbol].kind in _WRAPPERS
            and (
                facts := next(
                    (found for member in members.get(value.symbol, ()) if (found := member.model_facts) is not None),
                    None,
                )
            )
            is not None
        ):
            null = facts.annotation_null or takes_null(facts.type, symbols, members, seen | {value.symbol})
        case _:
            pass
    return null
