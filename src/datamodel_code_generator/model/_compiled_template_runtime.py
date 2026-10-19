"""Small, standard-library-only runtime used by generated built-in templates.

This module intentionally implements only the Jinja behaviour exercised by the
project's built-in templates.  Custom templates continue to use Jinja itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from pprint import pformat
from typing import Any

from typing_extensions import override


class _Missing:
    """The subset of Jinja's undefined value needed by built-in templates."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __iter__(self) -> Iterator[None]:
        return iter(())

    def __len__(self) -> int:
        return 0

    @override
    def __str__(self) -> str:
        return ""

    @override
    def __repr__(self) -> str:
        return "Undefined"


MISSING: Any = _Missing()


class Scope:
    """A compact lexical scope chain for a generated template renderer."""

    __slots__ = ("parent", "values")

    def __init__(self, values: dict[str, Any] | None = None, parent: Scope | None = None) -> None:
        self.parent = parent
        self.values = values if values is not None else {}

    def child(self) -> Scope:
        return Scope(parent=self)

    def get(self, name: str) -> Any:
        scope: Scope | None = self
        while scope is not None:
            if (value := scope.values.get(name, MISSING)) is not MISSING or name in scope.values:
                return value
            scope = scope.parent
        return MISSING

    def set(self, name: str, value: Any) -> None:
        self.values[name] = value


class Namespace:
    """Mutable counterpart of Jinja's namespace() helper."""

    __slots__ = ("_values",)

    def __init__(self, **values: Any) -> None:
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name: str) -> Any:
        return self._values.get(name, MISSING)

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        self._values[name] = value


def loop_last_iter(value: Iterable[Any] | _Missing) -> Iterator[tuple[Any, bool]]:
    """Yield an item and ``loop.last`` without allocating a Jinja loop object."""
    if value is MISSING:
        return
    iterator = iter(value)
    try:
        current = next(iterator)
    except StopIteration:
        return
    for following in iterator:
        yield current, False
        current = following
    yield current, True


def getattr_(value: Any, name: str) -> Any:
    """Match Jinja's attribute-first, mapping-item fallback lookup."""
    if value is MISSING:
        return MISSING
    try:
        return getattr(value, name)
    except AttributeError:
        if isinstance(value, Mapping):
            return value.get(name, MISSING)
        return MISSING


def getitem(value: Any, item: Any) -> Any:
    if value is MISSING:
        return MISSING
    try:
        return value[item]
    except (IndexError, KeyError, TypeError):
        return MISSING


def setattr_(value: Any, name: str, new_value: Any) -> None:
    if not isinstance(value, Namespace):
        error_message = "cannot assign attribute on a non-namespace object"
        raise TypeError(error_message)
    setattr(value, name, new_value)


def is_defined(value: Any) -> bool:
    return value is not MISSING


def stringify(value: Any) -> str:
    return "" if value is MISSING else str(value)


def concat(*values: Any) -> str:
    return "".join(stringify(value) for value in values)


def namespace(**values: Any) -> Namespace:
    return Namespace(**values)


def filter_default(value: Any, default_value: Any = "", boolean: bool = False) -> Any:  # noqa: FBT001, FBT002
    if value is MISSING or (boolean and not value):
        return default_value
    return value


def filter_indent(
    value: Any,
    width: int | str = 4,
    first: bool = False,  # noqa: FBT001, FBT002
    blank: bool = False,  # noqa: FBT001, FBT002
) -> str:
    """Indent the Jinja subset used by the built-in templates."""
    text = stringify(value)
    prefix = width if isinstance(width, str) else " " * width
    newline = "\n"
    # Jinja appends a newline before splitlines. This preserves the trailing
    # newline quirk and blank-line behaviour of its indent filter.
    text += newline
    if blank:
        result = (newline + prefix).join(text.splitlines())
    else:
        lines = text.splitlines()
        result = lines.pop(0)
        if lines:
            result += newline + newline.join(prefix + line if line else line for line in lines)
    return prefix + result if first else result


def filter_join(value: Any, delimiter: str = "") -> str:
    if value is MISSING:
        return ""
    return delimiter.join(stringify(item) for item in value)


def filter_length(value: Any) -> int:
    return 0 if value is MISSING else len(value)


def filter_list(value: Any) -> list[Any]:
    return [] if value is MISSING else list(value)


def filter_pprint(value: Any) -> str:
    return pformat(value)


def filter_replace(value: Any, old: str, new: str, count: int | None = None) -> str:
    text = stringify(value)
    return text.replace(old, new) if count is None else text.replace(old, new, count)


def filter_repr(value: Any) -> str:
    return repr(value)


def filter_selectattr(value: Any, attribute: str, test: str = "bool", expected: Any = MISSING) -> list[Any]:
    """Implement the selectattr form used by schema runtime validation."""
    if test != "equalto" or expected is MISSING:
        error_message = "the standalone template runtime supports selectattr(..., 'equalto', value) only"
        raise ValueError(error_message)
    if value is MISSING:
        return []
    return [item for item in value if getattr_(item, attribute) == expected]
