"""Spell final Python types as runtime and static expressions over one generated module's module imports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from datamodel_code_generator._binding_literals import UnsupportedBindingValueError
from datamodel_code_generator._generation_contract import (
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
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._generation_contract import (
        FinalPythonType,
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
    """Spell final types through a namespace, keeping generated symbols at their import locations."""

    __slots__ = ("_namespace", "_symbols")

    def __init__(self, namespace: Namespace, symbols: Mapping[int, str]) -> None:
        """Keep the namespace and each generated symbol's `module:Name` import location."""
        self._namespace = namespace
        self._symbols = symbols

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
                return f"{self._namespace.module(module)}.{name}"
            case BuiltinType():
                return value.name
            case NoneType():
                return "None"
            case ImportedType() if value.import_.from_ and not (
                value.import_.from_.startswith(".") or "." in value.import_.import_
            ):
                return ".".join((
                    self._namespace.module(value.import_.from_),
                    value.import_.import_,
                    *value.qualified_suffix,
                ))
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
                return f"{self._namespace.module(module)}.{name}.{value.name}"
            case LiteralScalar(kind="decimal"):
                return f"{self._namespace.module('decimal')}.Decimal({str(value.value)!r})"
            case LiteralScalar():
                return repr(value.value)
            case ConstructorType() if static and (
                target := _STATIC_CONSTRUCTORS.get((value.callable.import_.from_, value.callable.import_.import_))
            ):
                return target[1] if target[0] is None else f"{self._namespace.module(target[0])}.{target[1]}"
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
