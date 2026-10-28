"""Shared native backend names, model artifact import locations, and the model facts both API targets serialize by."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._python_type_annotation import (
    PythonTypeBoundName,
    PythonTypeName,
    PythonTypeSubscript,
    PythonTypeUnion,
)
from datamodel_code_generator._target_contract import (
    BoundType,
    BuiltinType,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    KnownBackendValue,
    LiteralScalar,
    NoneType,
    UnionType,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._target_contract import (
        BackendValue,
        FieldUseBinding,
        FinalModelSymbol,
        ModelArtifactAddress,
        SymbolId,
        TypeView,
    )

PydanticBackend: TypeAlias = Literal["pydantic_v2.BaseModel", "pydantic_v2.dataclass"]
_ALLOW: Final = KnownBackendValue(LiteralScalar(kind="str", value="allow"))
_TYPING: Final = ("typing", "typing_extensions")
_ANY: Final = frozenset((module, "Any") for module in _TYPING)
_LITERAL: Final = frozenset((module, "Literal") for module in _TYPING)
_NONE: Final = PythonTypeName("None")
_NULL_NAMES: Final = frozenset({_NONE, PythonTypeName("object")})
_WRAPPERS: Final = frozenset({"root", "alias"})


def artifact_module(artifact: ModelArtifactAddress) -> str:
    """Return the dotted module path of a model artifact below its model package."""
    *parents, name = artifact.relative_path
    modules = () if artifact.result_key == "single" else (*parents, name.removesuffix(".py"))
    return ".".join((artifact.model_package, *modules)).removesuffix(".__init__")


def extras_symbols(
    symbols: Mapping[SymbolId, FinalModelSymbol], members: Mapping[SymbolId, Sequence[FieldUseBinding]]
) -> frozenset[SymbolId]:
    """Return the models whose values reach a Pydantic dataclass that allows extra members, which Pydantic never dumps.

    A dataclass allows them by its own configuration or by a base's, which it inherits; a model, root model, or alias
    reaches what its bases and the types of its fields reach.
    """
    if not (reached := {symbol for symbol, declared in symbols.items() if _allows_extras(declared)}):
        return frozenset()
    named = {
        symbol: frozenset((
            *declared.bases,
            *(
                name
                for member in members.get(symbol, ())
                if (facts := member.model_facts) is not None
                for name in named_symbols(facts.type)
            ),
        ))
        for symbol, declared in symbols.items()
    }
    while grown := {symbol for symbol, names in named.items() if not names.isdisjoint(reached)} - reached:
        reached |= grown
    return frozenset(reached)


def named_symbols(value: TypeView) -> Iterator[SymbolId]:
    """Yield the generated symbols a type names, among its members and arguments too."""
    match value:
        case GeneratedSymbolType():
            yield value.symbol
        case UnionType():
            for item in value.members:
                yield from named_symbols(item)
        case GenericType():
            for item in value.arguments:
                yield from named_symbols(item)
        case _:
            pass


def takes_null(
    value: TypeView | None,
    symbols: Mapping[SymbolId, FinalModelSymbol],
    members: Mapping[SymbolId, Sequence[FieldUseBinding]],
    seen: frozenset[SymbolId] = frozenset(),
) -> bool:
    """Return whether a type validates JSON null, as the generated models annotate it, which both targets send by.

    Any value does without a type, as Any, or as object; so does None, an enum with a null value, a union with such a
    member, a root model or alias whose annotation accepts None or whose type does, and an annotation the document
    writes as None, a union with None, Any under any alias, object, or a `Literal` with None.
    """
    null = False
    match value:
        case None | NoneType():
            null = True
        case BuiltinType():
            null = value.name == "object"
        case ImportedType():
            null = (value.import_.from_, value.import_.import_) in _ANY
        case BoundType():
            null = _written_null(value.binding.expression)
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


def _written_null(expression: PythonTypeExpr) -> bool:
    """Return whether an annotation written in the document accepts None, by the imports its names come from.

    None, object, Any under any alias, a union with such a member, and a `Literal` with a None value do.
    """
    null = expression in _NULL_NAMES
    match expression:
        case PythonTypeUnion():
            null = any(map(_written_null, expression.items))
        case PythonTypeBoundName():
            null = (expression.import_from, expression.import_name) in _ANY
        case PythonTypeSubscript() if (
            isinstance(base := expression.base, PythonTypeBoundName)
            and (base.import_from, base.import_name) in _LITERAL
        ):
            null = _NONE in expression.arguments
        case _:
            pass
    return null


def keeps_extras(symbols: Mapping[SymbolId, FinalModelSymbol], symbol: SymbolId) -> bool:
    """Return whether a Pydantic model or dataclass keeps extra members by the `extra` its configuration takes."""
    return _extra(symbols, symbol) == _ALLOW


def _extra(symbols: Mapping[SymbolId, FinalModelSymbol], symbol: SymbolId) -> BackendValue | None:
    """Return the `extra` a model takes: its own setting, else a base's as Pydantic reads them, else None.

    A model takes the last base's that sets one, and a dataclass the first's, which its method resolution reaches.
    """
    declared = symbols[symbol]
    if (own := _own_extra(declared)) is not None:
        return own
    first = declared.facts is not None and declared.facts.backend == "pydantic_dataclass"
    bases = declared.bases if first else declared.bases[::-1]
    return next((found for base in bases if (found := _extra(symbols, base)) is not None), None)


def _own_extra(declared: FinalModelSymbol) -> BackendValue | None:
    """Return the `extra` a Pydantic model's or dataclass's own configuration sets, or None when it sets none."""
    configuration = () if declared.facts is None else declared.facts.configuration
    return next((setting.value for setting in configuration if setting.name == "extra" and setting.present), None)


def _allows_extras(declared: FinalModelSymbol) -> bool:
    """Return whether a Pydantic dataclass's own configuration allows extra members."""
    return (
        (facts := declared.facts) is not None
        and facts.backend == "pydantic_dataclass"
        and _own_extra(declared) == _ALLOW
    )
