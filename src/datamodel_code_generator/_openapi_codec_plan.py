"""Shared native backend names, model artifact import locations, and the model facts both API targets serialize by."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._module_paths import dotted_module
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
        BackendValue,
        FieldUseBinding,
        FinalModelSymbol,
        ModelArtifactAddress,
        SymbolId,
        TypeView,
    )

PydanticBackend: TypeAlias = Literal["pydantic_v2.BaseModel", "pydantic_v2.dataclass"]
_ALLOW: Final = KnownBackendValue(LiteralScalar(kind="str", value="allow"))


@dataclass(frozen=True, slots=True)
class ExtrasRuntime:
    """The runtime module that writes the extra members of Pydantic dataclasses, which Pydantic never dumps.

    A client's codecs take its `serializer` and a server's responses dump by its `writer`; a target that uses either
    copies the module's `path` and imports from its `module`.
    """

    name: str = "pydantic_extras"
    serializer: str = "extras_serializer"
    writer: str = "extras_json"

    @property
    def path(self) -> str:
        """Return the module's file below the runtime package, as a target declares its copy."""
        return f"model_codecs/{self.name}.py"

    @property
    def module(self) -> str:
        """Return the module's dotted path below the generated package."""
        return f"_runtime.model_codecs.{self.name}"


EXTRAS: Final = ExtrasRuntime()


def artifact_module(artifact: ModelArtifactAddress) -> str:
    """Return the dotted module path of a model artifact below its model package."""
    if artifact.result_key == "single":
        return artifact.model_package.removesuffix(".__init__")
    return dotted_module(artifact.relative_path, artifact.model_package)


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
