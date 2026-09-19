"""Helpers for rendering Python source literals."""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from math import isfinite, isnan
from typing import TYPE_CHECKING, Any, cast

from typing_extensions import Self

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator.imports import Import


class PythonCode(str):  # noqa: FURB189 - must behave as str for regex consumers.
    """Python expression rendered without extra quoting."""

    code: str
    __slots__ = ("code",)

    def __new__(cls, code: str, value: str | None = None) -> Self:
        """Initialize with a raw Python expression and optional string value."""
        obj = super().__new__(cls, code if value is None else value)
        obj.code = code
        return obj

    def __repr__(self) -> str:
        """Render the wrapped expression."""
        return self.code


@dataclass(frozen=True, slots=True, eq=False, init=False)
class PythonRuntimeExpression:
    """Parser-owned source expression with import identities kept until finalization."""

    _value: str | None
    import_: Import
    prefix: str
    suffix: str

    def __init__(self, import_: Import, prefix: str, suffix: str, value: str | None = None) -> None:
        """Retain one import identity and generate source only when rendered."""
        object.__setattr__(self, "import_", import_)
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "suffix", suffix)
        object.__setattr__(self, "_value", value)

    @property
    def code(self) -> str:
        """Render the expression using its current effective import binding."""
        return f"{self.prefix}{self.import_.binding_name}{self.suffix}"

    @property
    def imports(self) -> tuple[Import, ...]:
        """Expose the single import for compatibility with the legacy XML helper."""
        return (self.import_,)

    @classmethod
    def from_import_call(cls, import_: Import, *arguments: str, value: str | None = None) -> Self:
        """Create a call expression that retains its constructor import identity."""
        return cls(import_, "", f"({', '.join(arguments)})", value)

    def with_import_aliases(self, aliases: Mapping[tuple[str | None, str], Import]) -> Self:
        """Return self unless module-wide import resolution changed a referenced binding."""
        if (import_ := aliases.get((self.import_.from_, self.import_.import_))) is None or import_ is self.import_:
            return self
        return self.__class__(import_, self.prefix, self.suffix, self._value)

    def __str__(self) -> str:
        """Preserve the explicit semantic value when a caller requested one."""
        return self._value if self._value is not None else self.code

    def __repr__(self) -> str:
        """Render the expression without quoting it in generated source."""
        return self.code

    __hash__ = object.__hash__

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        """Share immutable parser-owned expression state across copied model graphs."""
        return self


def _runtime_expression_container_items(value: object) -> Iterable[object] | None:
    """Return only exact built-in literal containers eligible for recursive handling."""
    match value:
        case dict() if type(value) is dict:
            return value.items()
        case list() if type(value) is list:
            return value
        case tuple() if type(value) is tuple:
            return value
        case set() if type(value) is set:
            return value
        case frozenset() if type(value) is frozenset:
            return value
    return None


def runtime_expression_imports(value: object) -> tuple[Import, ...]:
    """Collect imports from parser-owned runtime expressions in built-in literal containers."""
    if isinstance(value, PythonRuntimeExpression):
        return (value.import_,)
    if _runtime_expression_container_items(value) is None:
        return ()

    imports: list[Import] = []
    stack: list[object] = [value]
    seen: set[int] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, PythonRuntimeExpression):
            imports.append(value.import_)
            continue
        if (items := _runtime_expression_container_items(value)) is None:
            continue
        value_id = id(value)
        if value_id in seen:
            continue
        seen.add(value_id)
        stack.extend(items)
    return tuple(imports)


def rewrite_runtime_imports(
    imports: tuple[Import, ...],
    aliases: Mapping[tuple[str | None, str], Import],
) -> tuple[Import, ...]:
    """Return original imports unless module-wide resolution changed a binding."""
    if not aliases:
        return imports
    for index, import_ in enumerate(imports):
        if (aliased_import := aliases.get((import_.from_, import_.import_))) is None or aliased_import is import_:
            continue
        rewritten = [*imports[:index], aliased_import]
        rewritten.extend(
            aliases.get((remaining.from_, remaining.import_), remaining) for remaining in imports[index + 1 :]
        )
        return tuple(rewritten)
    return imports


def rewrite_runtime_expressions(
    value: object,
    aliases: Mapping[tuple[str | None, str], Import],
) -> object:
    """Alias nested parser-owned runtime expressions while preserving unchanged containers."""
    return _rewrite_runtime_value(value, aliases)


def _rewrite_runtime_value(
    value: object,
    aliases: Mapping[tuple[str | None, str], Import],
) -> object:
    """Rewrite one source value and make structural copies only after an actual change."""
    if isinstance(value, PythonRuntimeExpression):
        return value.with_import_aliases(aliases)
    if type(value) is dict:
        return _rewrite_runtime_mapping(cast("dict[object, object]", value), aliases)
    if (items := _runtime_expression_container_items(value)) is None:
        return value
    rewritten = tuple(_rewrite_runtime_value(item, aliases) for item in items)
    if all(item is original for item, original in zip(rewritten, cast("Iterable[object]", value), strict=True)):
        return value
    return _rebuild_runtime_container(value, rewritten)


def _rewrite_runtime_mapping(
    value: dict[object, object],
    aliases: Mapping[tuple[str | None, str], Import],
) -> object:
    """Copy a mapping only when a key or value received an alias."""
    items = tuple(
        (_rewrite_runtime_value(key, aliases), _rewrite_runtime_value(item, aliases)) for key, item in value.items()
    )
    if all(
        rewritten_key is original_key and rewritten_item is original_item
        for (rewritten_key, rewritten_item), (original_key, original_item) in zip(items, value.items(), strict=True)
    ):
        return value
    return dict(items)


def _rebuild_runtime_container(value: object, items: tuple[object, ...]) -> object:
    """Create the exact built-in container type after a nested expression changed."""
    if type(value) is list:
        return list(items)
    if type(value) is tuple:
        return items
    if type(value) is set:
        return set(items)
    return frozenset(items)


class _NonFiniteFloat(float):
    """A parser-owned non-finite float with a source-safe representation."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Render a valid Python expression without changing numeric semantics."""
        if isnan(self):
            return "float('nan')"
        return "float('inf')" if self > 0 else "float('-inf')"

    def __str__(self) -> str:
        """Render a valid Python expression for templates that stringify values."""
        return repr(self)


def _safe_non_finite_float(value: float) -> float:
    """Preserve regular floats while making non-finite parser values source-safe."""
    return _NonFiniteFloat(value) if not isfinite(value) else value


def _semantic_value_text(value: object) -> str:
    """Return parser-internal text without source-literal replacements."""
    return repr(float(value)) if isinstance(value, _NonFiniteFloat) else str(value)


_INTERNAL_TYPE_EXPRESSION_TOKEN = object()
_INTERNAL_TYPE_EXPRESSION_ERROR = "internal type expressions must be created by the parser"


class _InternalTypeExpression(str):  # noqa: FURB189 - must remain string-compatible for template contexts.
    """A parser-owned Python type expression.

    Public template data never creates this value.  The private token makes the
    raw-expression path unavailable to ordinary API and JSON template data.
    """

    code: str
    __slots__ = ("code",)

    def __new__(cls, value: str, token: object, code: str | None = None) -> Self:
        if token is not _INTERNAL_TYPE_EXPRESSION_TOKEN:
            raise TypeError(_INTERNAL_TYPE_EXPRESSION_ERROR)
        obj = super().__new__(cls, value)
        obj.code = value if code is None else code
        return obj


def _make_internal_type_expression(value: str, code: str | None = None) -> _InternalTypeExpression:
    """Mark a parser-generated type hint as safe to emit as Python source."""
    return _InternalTypeExpression(value, _INTERNAL_TYPE_EXPRESSION_TOKEN, code)


def _normalize_string(value: str) -> str:
    """Return the underlying built-in string without invoking subclass overrides."""
    return value if type(value) is str else str.__str__(value)  # noqa: PLC2801 - bypass untrusted subclass overrides


def _stringify_untrusted_value(value: object) -> str:
    """Stringify public data once, returning an exact built-in ``str``."""
    return _normalize_string(str(value))


def _normalized_safe_public_type_name(value: object) -> str | None:
    """Return a normalized bare or dotted public type name when safe.

    Built-in scalar and TypedDict template settings accept simple names such as
    ``str`` and ``datetime.date``.  More expressive type syntax belongs to the
    parser-owned boundary above; accepting it here would require parsing source
    at runtime and would make this public extension point an execution sink.
    """
    if not isinstance(value, str):
        return None
    normalized_value = _normalize_string(value)
    if normalized_value and all(
        part.isidentifier() and not keyword.iskeyword(part) for part in normalized_value.split(".")
    ):
        return normalized_value
    return None


def is_safe_public_type_name(value: object) -> bool:
    """Return whether public data is a bare or dotted Python identifier."""
    return _normalized_safe_public_type_name(value) is not None


def represent_untrusted_public_type_name(value: Any) -> str:
    """Render a public type name without admitting executable source syntax."""
    if isinstance(value, str):
        if normalized_value := _normalized_safe_public_type_name(value):
            return normalized_value
        return represent_untrusted_python_value(_normalize_string(value))
    return represent_untrusted_python_value(value)


def represent_python_value(value: Any) -> str:  # noqa: PLR0911
    """Render a value as a Python expression safe for generated source."""
    match value:
        case PythonRuntimeExpression():
            return value.code
        case PythonCode():
            return value.code
        case float() if isnan(value):
            return "float('nan')"
        case float() if not isfinite(value):
            return "float('inf')" if value > 0 else "float('-inf')"
        case dict():
            rendered_items = ", ".join(
                f"{represent_python_value(key)}: {represent_python_value(item)}" for key, item in value.items()
            )
            return f"{{{rendered_items}}}"
        case list():
            return "[" + ", ".join(represent_python_value(item) for item in value) + "]"
        case tuple():
            rendered_items = ", ".join(represent_python_value(item) for item in value)
            trailing_comma = "," if len(value) == 1 else ""
            return f"({rendered_items}{trailing_comma})"
        case set() if not value:
            return "set()"
        case set():
            sorted_items = sorted(value, key=lambda item: (type(item).__name__, repr(item)))
            return "{" + ", ".join(represent_python_value(item) for item in sorted_items) + "}"
    return repr(value)


def represent_untrusted_python_value(value: Any) -> str:  # noqa: PLR0911
    """Render data from a public template context as a non-executing Python literal.

    Unlike :func:`represent_python_value`, this deliberately does not preserve
    ``PythonCode``.  Built-in templates call this at extension-data boundaries;
    parser-owned source fragments remain separate from those boundaries.
    """
    if value is None or type(value) is bool:
        return represent_python_value(value)
    if type(value) is int or type(value) is float:
        return represent_python_value(value)
    if isinstance(value, int):
        return represent_python_value(int.__int__(value))  # noqa: PLC2801 - bypass untrusted subclass overrides
    if isinstance(value, float):
        return represent_python_value(float.__float__(value))  # noqa: PLC2801 - bypass untrusted subclass overrides
    if isinstance(value, str):
        return repr(_normalize_string(value))
    if isinstance(value, dict):
        rendered_items = ", ".join(
            f"{represent_untrusted_python_value(key)}: {represent_untrusted_python_value(item)}"
            for key, item in value.items()
        )
        return f"{{{rendered_items}}}"
    if isinstance(value, list):
        return "[" + ", ".join(represent_untrusted_python_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        rendered_items = ", ".join(represent_untrusted_python_value(item) for item in value)
        trailing_comma = "," if len(value) == 1 else ""
        return f"({rendered_items}{trailing_comma})"
    if isinstance(value, set):
        if not value:
            return "set()"
        rendered_items = [(type(item).__name__, represent_untrusted_python_value(item)) for item in value]
        rendered_items.sort()
        return "{" + ", ".join(item for _, item in rendered_items) + "}"
    return repr(_stringify_untrusted_value(value))
