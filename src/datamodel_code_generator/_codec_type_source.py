"""Spell final Python types as runtime and static expressions over one generated module's module imports."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._target_contract import (
    AnnotatedType,
    BuiltinType,
    ConstructorType,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    LiteralScalar,
    LiteralType,
    NoneType,
    SourceExpression,
    UnionType,
    UnsupportedBindingValueError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from datamodel_code_generator._runtime.model_codecs.media import LexicalKind
    from datamodel_code_generator._target_contract import (
        FinalModelSymbol,
        FinalPythonType,
        GeneratedTypeContractBatch,
        SourceLocation,
        TypeArgument,
        TypeProjectionReason,
    )

_UNSUPPORTED: Final = "BND_TYPE_EXPRESSION_UNSUPPORTED"
_BUILTINS: Final = (
    "bool",
    "bytes",
    "complex",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "set",
    "str",
    "tuple",
)
_STATIC_CONSTRUCTORS: Final[dict[tuple[str | None, str], tuple[str | None, str]]] = {
    ("pydantic", "conbytes"): (None, "bytes"),
    ("pydantic", "condecimal"): ("decimal", "Decimal"),
    ("pydantic", "confloat"): (None, "float"),
    ("pydantic", "conint"): (None, "int"),
    ("pydantic", "constr"): (None, "str"),
}
_STATIC_SCALARS: Final = {name: BuiltinType(name) for name in ("bytes", "float", "int", "str")}
_LITERAL_KINDS: Final[dict[str, LexicalKind]] = {"bool": "boolean", "float": "number", "int": "integer"}
_LEXICAL_KINDS: Final[dict[tuple[str | None, str], LexicalKind]] = {
    (None, "bool"): "boolean",
    (None, "float"): "number",
    (None, "int"): "integer",
    ("pydantic", "StrictBool"): "boolean",
    ("pydantic", "NegativeFloat"): "number",
    ("pydantic", "NonNegativeFloat"): "number",
    ("pydantic", "NonPositiveFloat"): "number",
    ("pydantic", "PositiveFloat"): "number",
    ("pydantic", "StrictFloat"): "number",
    ("pydantic", "confloat"): "number",
    ("pydantic", "NegativeInt"): "integer",
    ("pydantic", "NonNegativeInt"): "integer",
    ("pydantic", "NonPositiveInt"): "integer",
    ("pydantic", "PositiveInt"): "integer",
    ("pydantic", "StrictInt"): "integer",
    ("pydantic", "conint"): "integer",
}
_INTEGER_NUMBER: Final = frozenset({"integer", "number"})
_WRAPPERS: Final = frozenset({"alias", "root"})


def static_scalar(value: FinalPythonType) -> FinalPythonType:
    """Return the builtin scalar a static checker sees for a constrained one, or any other type itself."""
    match value:
        case ConstructorType() if (
            target := _STATIC_CONSTRUCTORS.get((value.callable.import_.from_, value.callable.import_.import_))
        ) and (scalar := _STATIC_SCALARS.get(target[1])):
            return scalar
        case _:
            pass
    return value


class LexicalKinds:
    """Read the lexical kind of a text leaf from the final type the model generator bound at its schema.

    An int, float or bool leaf has its own kind: as the builtin, a constrained or strict form of it, or the member
    type of an enum or literal. The text of any other leaf, a union of several kinds included, is the model's to read.
    """

    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        """Keep the batch whose schema uses, symbols and root values the first lookups index."""
        self._batch = batch

    @cached_property
    def _types(self) -> dict[tuple[int, str], FinalPythonType | None]:
        return {
            (use.id.use_site.document, use.id.use_site.pointer): use.type
            for use in self._batch.type_uses
            if use.id.role == "schema" and use.id.projection == "value" and use.id.direction == "neutral"
        }

    @cached_property
    def _symbols(self) -> dict[int, FinalModelSymbol]:
        return {symbol.id: symbol for symbol in self._batch.symbols}

    @cached_property
    def _roots(self) -> dict[int, FinalPythonType]:
        return {
            member.consumer: facts.type
            for member in self._batch.fields
            if member.member_kind == "root_value" and (facts := member.model_facts) is not None
        }

    def at(self, location: SourceLocation) -> LexicalKind:
        """Return the kind of the leaf a schema declares, taking an int beside a float as a number.

        A schema no type is bound at has the string kind.
        """
        kinds = set(self._leaves(self._types.get((location.document, location.pointer)), frozenset()))
        if kinds == _INTEGER_NUMBER:
            return "number"
        return kinds.pop() if len(kinds) == 1 else "string"

    def _leaves(self, value: FinalPythonType | None, seen: frozenset[int]) -> Iterator[LexicalKind]:
        match value:
            case None | NoneType():
                pass
            case AnnotatedType() | ConstructorType():
                yield from self._leaves(value.base if isinstance(value, AnnotatedType) else value.callable, seen)
            case UnionType():
                for member in value.members:
                    yield from self._leaves(member, seen)
            case LiteralType():
                for item in value.values:
                    yield from (
                        self._leaves(GeneratedSymbolType(item.symbol), seen)
                        if isinstance(item, GeneratedEnumMember)
                        else _literal_kinds(item)
                    )
            case GeneratedSymbolType():
                yield from self._symbol(self._symbols[value.symbol], seen)
            case BuiltinType():
                yield _LEXICAL_KINDS.get((None, value.name), "string")
            case ImportedType():
                yield _LEXICAL_KINDS.get((value.import_.from_, value.import_.import_), "string")
            case _:
                yield "string"

    def _symbol(self, symbol: FinalModelSymbol, seen: frozenset[int]) -> Iterator[LexicalKind]:
        if symbol.kind == "enum":
            for item in symbol.values:
                yield from _literal_kinds(item)
        elif symbol.kind in _WRAPPERS and symbol.id not in seen:
            yield from self._leaves(self._roots.get(symbol.id), seen | {symbol.id})
        else:
            yield "string"


def _literal_kinds(value: LiteralScalar | None) -> Iterator[LexicalKind]:
    """Yield the kind of one literal or enum member value, none for null."""
    if value is None or value.kind != "none":
        yield "string" if value is None else _LITERAL_KINDS.get(value.kind, "string")


class Namespace:
    """Give each imported module one local alias that shadows no reserved name, spelled builtin, or other alias."""

    __slots__ = ("_modules", "_names", "_taken")

    def __init__(self, reserved: Iterable[str]) -> None:
        """Reserve the names the module defines itself, together with every builtin a spelling may use."""
        self._taken = {*reserved, *_BUILTINS}
        self._modules: dict[str, str] = {}
        self._names: dict[tuple[str, str], str] = {}

    def _alias(self, base: str) -> str:
        alias = base
        count = 0
        while alias in self._taken:
            count += 1
            alias = f"{base}_{count}"
        self._taken.add(alias)
        return alias

    def module(self, path: str) -> str:
        """Return the local alias of an absolute module path, importing it on first use."""
        if (alias := self._modules.get(path)) is None:
            alias = self._modules[path] = self._alias(path.replace(".", "_"))
        return alias

    def name(self, module: str, name: str) -> str:
        """Return the local alias of a name imported from a module, which may be relative, on first use."""
        if (alias := self._names.get((module, name))) is None:
            alias = self._names[module, name] = self._alias(name)
        return alias

    def imports(self) -> list[str]:
        """Return the import statements of every aliased module in path order, then every imported name."""
        modules = [
            f"import {path}" if alias == path else f"import {path} as {alias}"
            for path, alias in sorted(self._modules.items())
        ]
        grouped: dict[str, list[str]] = {}
        for (module, name), alias in sorted(self._names.items()):
            grouped.setdefault(module, []).append(name if alias == name else f"{name} as {alias}")
        return [*modules, *(f"from {module} import {', '.join(names)}" for module, names in grouped.items())]


class TypeSource:
    """Spell final types through a namespace, keeping generated symbols at their import locations.

    A leaf callback spells each imported type name, such as a model class, instead of its module attribute.
    """

    __slots__ = ("_leaf", "_namespace", "_symbols")

    def __init__(
        self, namespace: Namespace, symbols: Mapping[int, str], leaf: Callable[[str, str], str] | None = None
    ) -> None:
        """Keep the namespace, each generated symbol's `module:Name` import location, and the leaf callback."""
        self._namespace = namespace
        self._symbols = symbols
        self._leaf = self._attribute if leaf is None else leaf

    def _attribute(self, module: str, name: str) -> str:
        return f"{self._namespace.module(module)}.{name}"

    def runtime(self, value: FinalPythonType) -> str:
        """Return the expression that evaluates to the native type."""
        return self._spell(value, static=False)

    def static(self, value: FinalPythonType) -> str:
        """Return the type expression that type checkers read for the native type."""
        return self._spell(value, static=True)

    def _spell(  # noqa: PLR0911, PLR0912
        self, value: FinalPythonType | TypeArgument | GeneratedEnumMember, *, static: bool
    ) -> str:
        match value:
            case GeneratedSymbolType() if value.symbol in self._symbols:
                module, _, name = self._symbols[value.symbol].partition(":")
                return self._leaf(module, name)
            case BuiltinType():
                return value.name
            case NoneType():
                return "None"
            case ImportedType() if value.import_.from_ and not (
                value.import_.from_.startswith(".") or "." in value.import_.import_
            ):
                return ".".join((self._leaf(value.import_.from_, value.import_.import_), *value.qualified_suffix))
            case GenericType():
                items = ", ".join(self._spell(item, static=static) for item in value.arguments)
                base = self._spell(value.base, static=static)
                return f"{base}[{items or '()'}]" if items or value.tuple_form == "fixed" else base
            case UnionType():
                return " | ".join(self._spell(member, static=static) for member in value.members)
            case LiteralType():
                literals = ", ".join(self._spell(item, static=static) for item in value.values)
                return f"{self._namespace.module('typing')}.Literal[{literals}]"
            case GeneratedEnumMember() if value.symbol in self._symbols:
                module, _, name = self._symbols[value.symbol].partition(":")
                return f"{self._leaf(module, name)}.{value.name}"
            case LiteralScalar(kind="decimal"):
                return f"{self._namespace.module('decimal')}.Decimal({str(value.value)!r})"
            case LiteralScalar():
                return repr(value.value)
            case ConstructorType() if static and (
                target := _STATIC_CONSTRUCTORS.get((value.callable.import_.from_, value.callable.import_.import_))
            ):
                return target[1] if target[0] is None else self._leaf(target[0], target[1])
            case ConstructorType() if not static:
                return f"{self._spell(value.callable, static=False)}({self._keywords(value.keywords)})"
            case AnnotatedType() if static:
                return self._spell(value.base, static=True)
            case AnnotatedType():
                calls = ", ".join(
                    f"{self._spell(ImportedType(call.import_), static=False)}({self._keywords(call.keywords)})"
                    for call in value.metadata
                )
                return f"{self._namespace.module('typing')}.Annotated[{self._spell(value.base, static=False)}, {calls}]"
            case SourceExpression():
                return value.text
            case _:
                raise UnsupportedBindingValueError(_UNSUPPORTED)

    def _keywords(self, keywords: tuple[tuple[str, TypeArgument], ...]) -> str:
        return ", ".join(f"{name}={self._spell(argument, static=False)}" for name, argument in keywords)


def type_reason(value: FinalPythonType, symbols: Mapping[int, str]) -> TypeProjectionReason | None:
    """Return why a final type has no runtime or static expression in a generated module, if it has none."""
    source = TypeSource(Namespace(()), symbols)
    try:
        source.runtime(value)
        source.static(value)
    except UnsupportedBindingValueError as error:
        return error.reason
    return None
