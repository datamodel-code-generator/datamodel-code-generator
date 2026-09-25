"""Check the handlers a generated router connects before it registers any route."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from types import MappingProxyType


class HandlerConfigurationError(TypeError):
    """Reject a handler mapping that does not provide exactly one fitting callable per operation."""


def checked_handlers(
    handlers: object, operations: tuple[tuple[str, tuple[str, ...]], ...]
) -> Mapping[str, Callable[..., object]]:
    """Return the handlers once every operation has a synchronous callable accepting its keywords.

    Each operation is named with the keywords the endpoint passes. The check reads signatures only; it
    never evaluates annotations, so it proves shapes, not argument or return types.
    """
    if not isinstance(handlers, Mapping):
        msg = "handlers must map each operation name to its handler"
        raise HandlerConfigurationError(msg)
    names = {name for name, _ in operations}
    if unknown := sorted(str(key) for key in handlers if key not in names):
        msg = f"handlers names no operation called {', '.join(unknown)}"
        raise HandlerConfigurationError(msg)
    checked: dict[str, Callable[..., object]] = {}
    for name, keywords in operations:
        if not callable(handler := handlers.get(name)):
            msg = f"handlers needs a callable for {name}"
            raise HandlerConfigurationError(msg)
        if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(getattr(handler, "__call__", None)):  # noqa: B004
            msg = f"The {name} handler is asynchronous, but its operation calls it synchronously"
            raise HandlerConfigurationError(msg)
        try:
            inspect.signature(handler).bind(**dict.fromkeys(keywords))
        except (TypeError, ValueError) as error:
            msg = f"The {name} handler does not accept exactly the keywords {', '.join(keywords) or 'none'}"
            raise HandlerConfigurationError(msg) from error
        checked[name] = handler
    return MappingProxyType(checked)
