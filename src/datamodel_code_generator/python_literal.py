"""Helpers for rendering Python source literals."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, isnan
from typing import TYPE_CHECKING, Any, Protocol, cast

from typing_extensions import Self, Sentinel

from datamodel_code_generator._python_syntax import call_text, dict_display, list_display, set_display, tuple_display
from datamodel_code_generator._target_unicode import is_dotted_python_name, is_python_name
from datamodel_code_generator.imports import IMPORT_DATETIME_MODULE, _import_key

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

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
        return cls(import_, "", call_text("", arguments), value)

    def with_import_aliases(self, aliases: Mapping[tuple[str | None, str], Import]) -> Self:
        """Return self unless module-wide import resolution changed a referenced binding."""
        if (import_ := aliases.get(_import_key(self.import_))) is None or import_ is self.import_:
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


def _datetime_expression(suffix: str, *, prefix: str = "") -> PythonRuntimeExpression:
    """Return an expression on the ``datetime`` module, which generated code imports under one alias."""
    return PythonRuntimeExpression(IMPORT_DATETIME_MODULE, prefix, suffix)


def _isoformat_expression(kind: str, text: str) -> PythonRuntimeExpression:
    """Return the expression that builds the ``datetime`` module's ``kind`` (date, time, datetime) from ISO ``text``."""
    return _datetime_expression(call_text(f".{kind}.fromisoformat", (repr(text),)))


def _text_constructor_expression(import_: Import, text: str) -> PythonRuntimeExpression:
    """Return the call of the constructor ``import_`` on the string literal of ``text``, whose value is ``text``."""
    return PythonRuntimeExpression.from_import_call(import_, repr(text), value=text)


_RUNTIME_EXPRESSION_CONTAINERS = frozenset({list, tuple, set, frozenset})


def _runtime_expression_container_items(value: object) -> Iterable[object] | None:
    """Return only exact built-in literal containers eligible for recursive handling."""
    if type(value) is dict:
        return value.items()
    return cast("Iterable[object]", value) if type(value) in _RUNTIME_EXPRESSION_CONTAINERS else None


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
        if (aliased_import := aliases.get(_import_key(import_))) is None or aliased_import is import_:
            continue
        rewritten = [*imports[:index], aliased_import]
        rewritten.extend(aliases.get(_import_key(remaining), remaining) for remaining in imports[index + 1 :])
        return tuple(rewritten)
    return imports


def rewrite_runtime_expressions(
    value: object,
    aliases: Mapping[tuple[str | None, str], Import],
) -> object:
    """Alias nested parser-owned runtime expressions while preserving unchanged containers.

    Structural copies are made only after an actual change.
    """
    if isinstance(value, PythonRuntimeExpression):
        return value.with_import_aliases(aliases)
    if type(value) is dict:
        return _rewrite_runtime_mapping(cast("dict[object, object]", value), aliases)
    if (items := _runtime_expression_container_items(value)) is None:
        return value
    rewritten = tuple(rewrite_runtime_expressions(item, aliases) for item in items)
    if all(item is original for item, original in zip(rewritten, items, strict=True)):
        return value
    return rewritten if type(value) is tuple else type(value)(rewritten)


def _rewrite_runtime_mapping(
    value: dict[object, object],
    aliases: Mapping[tuple[str | None, str], Import],
) -> object:
    """Copy a mapping only when a key or value received an alias."""
    items = tuple(
        (rewrite_runtime_expressions(key, aliases), rewrite_runtime_expressions(item, aliases))
        for key, item in value.items()
    )
    if all(
        rewritten_key is original_key and rewritten_item is original_item
        for (rewritten_key, rewritten_item), (original_key, original_item) in zip(items, value.items(), strict=True)
    ):
        return value
    return dict(items)


class _NonFiniteFloat(float):
    """A parser-owned non-finite float with a source-safe representation."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Render a valid Python expression without changing numeric semantics."""
        return represent_python_value(float(self))

    def __str__(self) -> str:
        """Render a valid Python expression for templates that stringify values."""
        return repr(self)


def _safe_non_finite_float(value: float) -> float:
    """Preserve regular floats while making non-finite parser values source-safe."""
    return _NonFiniteFloat(value) if not isfinite(value) else value


def _converted_float(value: float, *, source_safe_non_finite: bool) -> float:
    """Return a float of a converted schema, source-safe when the converter's caller writes Python source."""
    return _safe_non_finite_float(value) if source_safe_non_finite else value


def _safe_non_finite_value(value: Any) -> Any:
    """Make every non-finite float of a parsed JSON value source-safe, copying only containers that hold one."""
    match value:
        case float():
            return _safe_non_finite_float(value)
        case dict():
            safe_items = {key: _safe_non_finite_value(item) for key, item in value.items()}
            return value if all(safe_items[key] is item for key, item in value.items()) else safe_items
        case list():
            safe_list = [_safe_non_finite_value(item) for item in value]
            return value if all(safe is item for safe, item in zip(safe_list, value, strict=True)) else safe_list
    return value


def _semantic_value_text(value: object) -> str:
    """Return parser-internal text without source-literal replacements."""
    return repr(float(value)) if isinstance(value, _NonFiniteFloat) else str(value)


_INTERNAL_TYPE_EXPRESSION_TOKEN = Sentinel("_INTERNAL_TYPE_EXPRESSION_TOKEN")
_INTERNAL_TYPE_EXPRESSION_ERROR = "internal type expressions must be created by the parser"


class _TypeHintSource(Protocol):
    """An object that renders a type hint with the current names of the classes it references."""

    @property
    def type_hint(self) -> str:
        """The type hint with the current class names."""


class _InternalTypeExpression(str):  # noqa: FURB189 - must remain string-compatible for template contexts.
    """A parser-owned Python type expression.

    Public template data never creates this value.  The private token makes the
    raw-expression path unavailable to ordinary API and JSON template data.
    ``source`` is the parser data type the expression was rendered from, when the
    classes it references may still be renamed.
    """

    code: str
    source: _TypeHintSource | None
    __slots__ = ("code", "source")

    def __new__(cls, value: str, token: object, code: str | None = None, source: _TypeHintSource | None = None) -> Self:
        if token is not _INTERNAL_TYPE_EXPRESSION_TOKEN:
            raise TypeError(_INTERNAL_TYPE_EXPRESSION_ERROR)
        obj = super().__new__(cls, value)
        obj.code = value if code is None else code
        obj.source = source
        return obj


def _make_internal_type_expression(value: str, code: str | None = None) -> _InternalTypeExpression:
    """Mark a parser-generated type hint as safe to emit as Python source; an internal ``value`` keeps its source."""
    source = value.source if isinstance(value, _InternalTypeExpression) else None
    return _InternalTypeExpression(value, _INTERNAL_TYPE_EXPRESSION_TOKEN, code, source)


def _make_referencing_type_expression(source: _TypeHintSource) -> _InternalTypeExpression:
    """Mark the type hint of ``source`` so that it can be rendered again once class names are final."""
    return _InternalTypeExpression(source.type_hint, _INTERNAL_TYPE_EXPRESSION_TOKEN, source=source)


def _normalize_string(value: str) -> str:
    """Return the underlying built-in string without invoking subclass overrides."""
    return value if type(value) is str else str.__str__(value)  # noqa: PLC2801 - bypass untrusted subclass overrides


def _normalized_safe_python_name(value: object, *, dotted: bool = False) -> str | None:
    """Return untrusted data as a plain string when it is a Python name, or Python names joined by dots with ``dotted``.

    Only a ``str`` counts, read without its subclass's overrides, and its names follow the running interpreter's
    Unicode (row ``legacy-host-identifiers``). Template keys, option names and the simple type names of built-in
    templates (``str``, ``datetime.date``) take this form; more expressive type syntax belongs to the parser-owned
    boundary above, since accepting it here would parse source at runtime and make this public extension point an
    execution sink.
    """
    if not isinstance(value, str):
        return None
    normalized_value = _normalize_string(value)
    is_name = is_dotted_python_name if dotted else is_python_name
    return normalized_value if is_name(normalized_value, rule="legacy-host-identifiers") else None


def is_safe_public_type_name(value: object) -> bool:
    """Return whether public data is a bare or dotted Python identifier."""
    return _normalized_safe_python_name(value, dotted=True) is not None


def represent_untrusted_public_type_name(value: Any) -> str:
    """Render a public type name without admitting executable source syntax."""
    if isinstance(value, str):
        if normalized_value := _normalized_safe_python_name(value, dotted=True):
            return normalized_value
        return represent_untrusted_python_value(_normalize_string(value))
    return represent_untrusted_python_value(value)


def _represent_container(value: object, render: Callable[[Any], str], *, sort_sets_by_repr: bool) -> str | None:
    """Render a dict, list, tuple or set with ``render`` for its items; None for any other value.

    Set items are ordered by type name, then by their ``repr`` or their rendered text.
    """
    match value:
        case dict():
            return dict_display((render(key), render(item)) for key, item in value.items())
        case list():
            return list_display(map(render, value))
        case tuple():
            return tuple_display(map(render, value))
        case set():
            from operator import itemgetter  # noqa: PLC0415

            rows = [
                (type(item).__name__, repr(item) if sort_sets_by_repr else text, text)
                for item in value
                for text in (render(item),)
            ]
            rows.sort(key=itemgetter(0, 1))
            return set_display(text for _, _, text in rows)
    return None


def represent_python_value(value: Any) -> str:
    """Render a value as a Python expression safe for generated source."""
    match value:
        case PythonRuntimeExpression() | PythonCode():
            return value.code
        case float() if isnan(value):
            return "float('nan')"
        case float() if not isfinite(value):
            return "float('inf')" if value > 0 else "float('-inf')"
    rendered = _represent_container(value, represent_python_value, sort_sets_by_repr=True)
    return repr(value) if rendered is None else rendered


def represent_untrusted_python_value(value: Any) -> str:
    """Render data from a public template context as a non-executing Python literal.

    Unlike :func:`represent_python_value`, this deliberately does not preserve
    ``PythonCode``.  Built-in templates call this at extension-data boundaries;
    parser-owned source fragments remain separate from those boundaries.
    """
    if value is None or type(value) is bool or type(value) is int or type(value) is float:
        return represent_python_value(value)
    if isinstance(value, int):
        return represent_python_value(int.__int__(value))  # noqa: PLC2801 - bypass untrusted subclass overrides
    if isinstance(value, float):
        return represent_python_value(float.__float__(value))  # noqa: PLC2801 - bypass untrusted subclass overrides
    if isinstance(value, str):
        return repr(_normalize_string(value))
    if (rendered := _represent_container(value, represent_untrusted_python_value, sort_sets_by_repr=False)) is not None:
        return rendered
    return repr(_normalize_string(str(value)))
