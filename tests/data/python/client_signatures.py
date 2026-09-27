"""Compare how the explicit and unpacked signature styles expose, bind, and refuse an operation's keyword arguments."""

from __future__ import annotations

import importlib
import inspect
from typing import TYPE_CHECKING, Any, Final

import httpx2
import typing_extensions

from tests.data.python.client_runtime import Exchange, json_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_VIEWS: Final = ("", "with_response", "with_raw_response", "with_streaming_response")


def _view(api: Any, view: str) -> Any:
    return getattr(api.pets, view) if view else api.pets


def _parameters(method: Callable[..., object]) -> str:
    signature = inspect.signature(method, follow_wrapped=False)
    return ", ".join(f"{name}:{parameter.kind.name}" for name, parameter in signature.parameters.items())


def _keywords(method: Callable[..., object]) -> str:
    """Return the logical keywords of a method, with the required ones marked, whichever style declares them.

    An unpacked method's keywords come from its TypedDict, resolved from this module through the hints of the method.
    """
    parameters = inspect.signature(method, follow_wrapped=False).parameters.values()
    if (collector := next((item for item in parameters if item.kind is inspect.Parameter.VAR_KEYWORD), None)) is None:
        return ", ".join(f"{item.name}{'!' if item.default is inspect.Parameter.empty else ''}" for item in parameters)
    (keys,) = typing_extensions.get_args(typing_extensions.get_type_hints(method, include_extras=True)[collector.name])
    return ", ".join(
        f"{name}{'' if typing_extensions.get_origin(hint) is typing_extensions.NotRequired else '!'}"
        for name, hint in typing_extensions.get_type_hints(keys, include_extras=True).items()
    )


def _introspection(api: Any, lines: list[str], mode: str) -> None:
    """Report each view's real signature, the logical keywords its hints declare, and whether it is a coroutine."""
    for view in _VIEWS:
        method = _view(api, view).get_pet
        unbound = getattr(type(_view(api, view)), "get_pet")
        lines.extend((
            f"  {mode}{view or 'plain'} get_pet ({_parameters(method)}) unbound ({_parameters(unbound)})",
            f"    keywords {_keywords(method)} coroutine {inspect.iscoroutinefunction(method)}",
        ))


def _refused(lines: list[str], label: str, call: Callable[[], object], exchange: Exchange) -> None:
    """Report a call that binding refuses, and whether anything was sent."""
    sent = len(exchange.lines)
    try:
        call()
    except TypeError:
        lines.append(f"  {label} ! TypeError, sent {len(exchange.lines) > sent}")


def signatures(package: ModuleType, lines: list[str]) -> None:
    """Expose, call, forward, and refuse the keyword arguments of the same operations in either style."""
    types, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("types.pets", "options"))
    trace = types.ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t1").value
    pet = types.GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(3)
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        _introspection(api, lines, "")
        exchange.respond(json_response(200, []), json_response(200, []), json_response(200, {"id": 3, "name": "fox"}))
        record(lines, "list", lambda: api.pets.list_pets(x_trace=trace))
        known = {"x_trace": trace, "options": options.RequestOptions(headers=(("X-Call", "1"),))}
        record(lines, "list forwarding a mapping of known keys", lambda: api.pets.list_pets(**known))
        record(lines, "get with its narrowed response media", lambda: api.pets.get_pet(pet_id=pet, response_media_type="application/json"))
        for label, call in (
            ("list with an unknown keyword", lambda: api.pets.list_pets(x_trace=trace, color="red")),
            ("list without its required keyword", lambda: api.pets.list_pets()),
            ("raw list with an unknown keyword", lambda: api.pets.with_raw_response.list_pets(x_trace=trace, color="red")),
            ("streaming list without its required keyword", lambda: api.pets.with_streaming_response.list_pets()),
        ):
            _refused(lines, label, call, exchange)
    http.close()
    run(lambda: _async_signatures(package, lines, trace))


async def _async_signatures(package: ModuleType, lines: list[str], trace: object) -> None:
    """Refuse the same calls with asyncio: explicit binding refuses them when called, unpacked binding when awaited."""
    exchange = Exchange(lines)
    http = exchange.async_client()
    async with package.AsyncClient(http_client=http) as api:
        _introspection(api, lines, "async ")
        exchange.respond(json_response(200, []))
        lines.append(f"  async list = {await api.pets.list_pets(x_trace=trace)!r}")
        for label, keywords in (("an unknown keyword", {"x_trace": trace, "color": "red"}), ("no keyword", {})):
            sent = len(exchange.lines)
            try:
                pending = api.pets.list_pets(**keywords)
            except TypeError:
                lines.append(f"  async list with {label} ! TypeError when called, sent {len(exchange.lines) > sent}")
                continue
            try:
                await pending
            except TypeError:
                lines.append(f"  async list with {label} ! TypeError when awaited, sent {len(exchange.lines) > sent}")
        _refused(lines, "async streaming list with an unknown keyword", lambda: api.pets.with_streaming_response.list_pets(color="red"), exchange)
    await http.aclose()


def keywords(package: ModuleType, lines: list[str]) -> None:
    """Take keywords named like the collector, and return a model named like a TypedDict, which the binding keeps apart."""
    codecs = importlib.import_module(f"{package.__name__}.types.search").SearchRequestCodecs
    first, second = (codecs.parameter(location="query", name="kwargs").from_wire(value).value for value in ("a", "b"))
    count = codecs.parameter(location="query", name="args").from_wire(2).value
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        exchange.respond(json_response(200, {"kwargs": "found"}), json_response(200, {}))
        record(lines, "search", lambda: api.search.search(kwargs=first, args=count))
        record(lines, "search without args", lambda: api.search.search(kwargs=second))
        lines.append(f"  search keywords {_keywords(api.search.search)}")
    http.close()
