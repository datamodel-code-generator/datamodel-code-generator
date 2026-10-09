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
        GeneratedTypeContractBatch,
        LeafStep,
        SourceLocation,
        TypeArgument,
        TypeProjectionReason,
        TypeView,
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
_LEXICAL_KINDS: Final[dict[tuple[str | None, str], LexicalKind | None]] = {
    (None, "bool"): "boolean",
    (None, "float"): "number",
    (None, "int"): "integer",
    (None, "dict"): None,
    (None, "frozenset"): None,
    (None, "list"): None,
    (None, "set"): None,
    (None, "tuple"): None,
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
_ARGUMENTS: Final[dict[LeafStep, tuple[int, frozenset[tuple[str | None, str]]]]] = {
    "items": (
        1,
        frozenset({
            (None, "frozenset"),
            (None, "list"),
            (None, "set"),
            ("collections.abc", "Sequence"),
            ("typing", "Sequence"),
        }),
    ),
    "values": (2, frozenset({(None, "dict"), ("collections.abc", "Mapping"), ("typing", "Mapping")})),
}
_INTEGER_NUMBER: Final = frozenset({"integer", "number"})
_WRAPPERS: Final = frozenset({"alias", "root"})


def static_scalar(value: TypeView) -> TypeView:
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
    type of an enum or literal. The text of any other scalar leaf is the model's to read, and so is that of a union
    with such a leaf. A model, a container, null alone, an enum or literal of several kinds, and a union of several
    kinds none of which reads text as it is, have no kind.
    """

    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        """Keep the batch whose schema uses, symbols and root values the first lookups index."""
        self._batch = batch

    @cached_property
    def _types(self) -> dict[tuple[int, str], TypeView | None]:
        return {
            (use.id.use_site.document, use.id.use_site.pointer): use.type
            for use in self._batch.type_uses
            if use.id.role == "schema" and use.id.projection == "value" and use.id.direction == "neutral"
        }

    @cached_property
    def _symbols(self) -> dict[int, FinalModelSymbol]:
        return {symbol.id: symbol for symbol in self._batch.symbols}

    @cached_property
    def _roots(self) -> dict[int, TypeView]:
        return {
            member.consumer: facts.type
            for member in self._batch.fields
            if member.member_kind == "root_value" and (facts := member.model_facts) is not None
        }

    def at(self, *leaves: tuple[SourceLocation, tuple[LeafStep, ...]]) -> LexicalKind | None:
        """Return the kind of a leaf by the first of its places a type is bound for, if that type has one.

        A place is a schema's location and the steps from the type bound there to the leaf: a list's items, then a
        mapping's values. A leaf no place binds a type for has no kind.
        """
        value = next((found for leaf in leaves if (found := self._reached(*leaf)) is not None), None)
        return _kind(set(self._leaves(value, frozenset())), mixed=True)

    def of(self, value: TypeView, steps: tuple[LeafStep, ...] = ()) -> LexicalKind | None:
        """Return the kind of the leaf the steps reach from a type, such as a model field's type."""
        return _kind(set(self._leaves(self._stepped(value, steps), frozenset())), mixed=True)

    def _reached(self, location: SourceLocation, steps: tuple[LeafStep, ...]) -> TypeView | None:
        return self._stepped(self._types.get((location.document, location.pointer)), steps)

    def _stepped(self, value: TypeView | None, steps: tuple[LeafStep, ...]) -> TypeView | None:
        for step in steps:
            value = self._argument(value, *_ARGUMENTS[step])
        return value

    def _argument(
        self, value: TypeView | None, count: int, containers: frozenset[tuple[str | None, str]]
    ) -> TypeView | None:
        """Return the last argument of a container type, through aliases, root models and a union with None alone."""
        seen: set[int] = set()
        while True:
            if isinstance(value, UnionType) and len(present := _present(value)) == 1:
                value = present[0]
            elif (
                isinstance(value, GeneratedSymbolType)
                and value.symbol not in seen
                and self._symbols[value.symbol].kind in _WRAPPERS
            ):
                seen.add(value.symbol)
                value = self._roots.get(value.symbol)
            else:
                return (
                    value.arguments[-1]
                    if isinstance(value, GenericType)
                    and len(value.arguments) == count
                    and _name(value.base) in containers
                    else None
                )

    def _leaves(self, value: TypeView | None, seen: frozenset[int]) -> Iterator[LexicalKind | None]:
        match value:
            case NoneType():
                pass
            case AnnotatedType() | ConstructorType():
                yield from self._leaves(value.base if isinstance(value, AnnotatedType) else value.callable, seen)
            case UnionType():
                for member in value.members:
                    yield from self._leaves(member, seen)
            case LiteralType():
                yield _kind(
                    {
                        kind
                        for item in value.values
                        for kind in (
                            self._leaves(GeneratedSymbolType(item.symbol), seen)
                            if isinstance(item, GeneratedEnumMember)
                            else _literal_kinds(item)
                        )
                    },
                    mixed=False,
                )
            case GeneratedSymbolType():
                yield from self._symbol(self._symbols[value.symbol], seen)
            case BuiltinType() | ImportedType():
                yield _LEXICAL_KINDS.get(_name(value), "string")
            case None | GenericType():
                yield None
            case _:
                yield "string"

    def _symbol(self, symbol: FinalModelSymbol, seen: frozenset[int]) -> Iterator[LexicalKind | None]:
        if symbol.kind == "enum":
            yield _kind({kind for item in symbol.values for kind in _literal_kinds(item)}, mixed=False)
        elif symbol.kind in _WRAPPERS and symbol.id not in seen:
            yield from self._leaves(self._roots.get(symbol.id), seen | {symbol.id})
        else:
            yield None


def _present(value: UnionType) -> list[TypeView]:
    return [member for member in value.members if not isinstance(member, NoneType)]


def _name(value: TypeView) -> tuple[str | None, str]:
    """Return the module and name of a builtin or imported type, or no name for any other type."""
    return (
        (None, value.name)
        if isinstance(value, BuiltinType)
        else (value.import_.from_, value.import_.import_)
        if isinstance(value, ImportedType)
        else (None, "")
    )


def _literal_kinds(value: LiteralScalar | None) -> Iterator[LexicalKind | None]:
    """Yield the kind of one literal or enum member value: none for null, and None for one that is no scalar."""
    if value is None or value.kind != "none":
        yield None if value is None else _LITERAL_KINDS.get(value.kind, "string")


def _kind(kinds: set[LexicalKind | None], *, mixed: bool) -> LexicalKind | None:
    """Return the one kind of a leaf's kinds, taking an int beside a float as a number.

    Several other kinds are the string kind when `mixed` admits them and one of them is the string kind, whose leaf
    reads the text as it is; the members of one enum or literal admit none. A leaf of null alone has no kind.
    """
    if kinds == _INTEGER_NUMBER:
        return "number"
    if len(kinds) == 1:
        return kinds.pop()
    return "string" if mixed and "string" in kinds and None not in kinds else None


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

    def runtime(self, value: TypeView) -> str:
        """Return the expression that evaluates to the native type."""
        return self._spell(value, static=False)

    def static(self, value: TypeView) -> str:
        """Return the type expression that type checkers read for the native type."""
        return self._spell(value, static=True)

    def _spell(  # noqa: PLR0911, PLR0912
        self, value: TypeView | TypeArgument | GeneratedEnumMember, *, static: bool
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
            case GeneratedEnumMember() if value.symbol in self._symbols:  # pragma: no cover - only server form fields
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


def type_reason(value: TypeView, symbols: Mapping[int, str]) -> TypeProjectionReason | None:
    """Return why a final type has no runtime or static expression in a generated module, if it has none."""
    source = TypeSource(Namespace(()), symbols)
    try:
        source.runtime(value)
        source.static(value)
    except UnsupportedBindingValueError as error:
        return error.reason
    return None
