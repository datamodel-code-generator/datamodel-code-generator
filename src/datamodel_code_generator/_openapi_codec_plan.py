"""Shared native backend names, model artifact import locations, and the model facts both API targets serialize by."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._target_contract import (
    GeneratedSymbolType,
    GenericType,
    KnownBackendValue,
    LiteralScalar,
    UnionType,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        ModelArtifactAddress,
        SymbolId,
        TypeView,
    )

PydanticBackend: TypeAlias = Literal["pydantic_v2.BaseModel", "pydantic_v2.dataclass"]
_ALLOW: Final = KnownBackendValue(LiteralScalar(kind="str", value="allow"))


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


def _allows_extras(declared: FinalModelSymbol) -> bool:
    """Return whether a Pydantic dataclass's own configuration allows extra members."""
    return (
        (facts := declared.facts) is not None
        and facts.backend == "pydantic_dataclass"
        and any(setting.name == "extra" and setting.value == _ALLOW for setting in facts.configuration)
    )
