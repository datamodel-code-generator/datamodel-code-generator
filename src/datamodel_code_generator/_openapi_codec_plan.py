"""Shared native backend names, model artifact import locations, and the model facts both API targets serialize by."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._graph import reaching
from datamodel_code_generator._module_paths import dotted_module
from datamodel_code_generator._target_contract import (
    GeneratedSymbolType,
    GenericType,
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


@dataclass(frozen=True, slots=True)
class ExtrasRuntime:
    """The runtime module that writes the extra members of Pydantic dataclasses, which Pydantic never dumps.

    A client's codecs take its `serializer` and a server's responses dump by its `writer`; a target that uses either
    copies the module's `paths`, its own and those of the codec modules it `imports`, and imports from its `module`.
    """

    name: str = "pydantic_extras"
    serializer: str = "extras_serializer"
    writer: str = "extras_json"
    imports: tuple[str, ...] = ("shared",)

    @property
    def paths(self) -> tuple[str, ...]:
        """Return the files below the runtime package a target copies for the module, as it declares them."""
        return tuple(map(codec_file, (*self.imports, self.name)))

    @property
    def module(self) -> str:
        """Return the module's dotted path below the generated package."""
        return codec_module(self.name)


EXTRAS: Final = ExtrasRuntime()


def codec_file(name: str) -> str:
    """Return the file of a model codec runtime module below the runtime package, as a target declares its copy."""
    return f"model_codecs/{name}.py"


def codec_module(name: str) -> str:
    """Return the dotted path of a model codec runtime module below the generated package."""
    return f"_runtime.model_codecs.{name}"


def artifact_module(artifact: ModelArtifactAddress) -> str:
    """Return the dotted module path of a model artifact below its model package."""
    if artifact.result_key == "single":
        return artifact.model_package.removesuffix(".__init__")
    return dotted_module(artifact.relative_path, artifact.model_package)


def extras_symbols(
    symbols: Mapping[SymbolId, FinalModelSymbol], members: Mapping[SymbolId, Sequence[FieldUseBinding]]
) -> frozenset[SymbolId]:
    """Return the models whose values reach a Pydantic dataclass that allows extra members, which Pydantic never dumps.

    A dataclass allows them by the configuration it applies (``FinalModelSymbol.extra``); a model, root model, or alias
    reaches what the types of its fields, inherited ones included, reach.
    """
    if not (
        allowing := [
            symbol
            for symbol, declared in symbols.items()
            if declared.backend == "pydantic_dataclass" and declared.extra == "allow"
        ]
    ):
        return frozenset()
    return frozenset(
        reaching(
            allowing,
            (
                (
                    symbol,
                    tuple(
                        name
                        for member in members.get(symbol, ())
                        if (facts := member.model_facts) is not None
                        for name in named_symbols(facts.type)
                    ),
                )
                for symbol in symbols
            ),
        )
    )


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
