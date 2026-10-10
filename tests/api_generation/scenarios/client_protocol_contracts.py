"""Exercise generated protocol records, option boundaries, client integration, and resume-state envelopes."""

from __future__ import annotations

import collections.abc
import importlib
import inspect
import json
import subprocess
import sys
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, get_args, get_origin, get_type_hints

from tests.api_generation.support.client_runtime import Exchange, arecord, argument, json_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

_IMPORT_PROBE: Final = """
import importlib
import sys
import threading
sys.path.insert(0, sys.argv[1])
before = threading.active_count()
for name in ('._runtime.client.errors', '._runtime.client.options', '.errors', '.options'):
    importlib.import_module(sys.argv[2] + name)
loaded = sorted(name.removeprefix(sys.argv[2] + '.') for name in sys.modules if name.startswith(sys.argv[2] + '._runtime.protocols.'))
print('client imports load protocols=' + repr(loaded))
errors = importlib.import_module(sys.argv[2] + '.errors')
options = importlib.import_module(sys.argv[2] + '.options')
print('dir lists lazy names=' + repr(('ProtocolDataError' in dir(errors), 'SessionLimitError' in dir(errors))))
print('dir loads nothing=' + repr(sys.argv[2] + '._runtime.protocols.options' not in sys.modules and sys.argv[2] + '._runtime.protocols.errors' not in sys.modules))
print('protocol errors loaded on use=' + repr(errors.SessionLimitError.__module__ == sys.argv[2] + '._runtime.protocols.errors'))
print('lazy names cached=' + repr(('SessionLimitError' in vars(errors), 'ProtocolDataError' in vars(errors))))
module = importlib.import_module(sys.argv[2] + '.protocols')
optional = ('httpx2', 'httpcore2', 'cryptography', 'asyncio', 'pydantic', 'msgspec', 'anyio')
print('optional imports=' + repr([name for name in optional if name in sys.modules]))
print('import threads unchanged=' + repr(threading.active_count() == before))
print('replay loaded with contracts=' + repr(sys.argv[2] + '._runtime.protocols.replay' in sys.modules))
print('pagination loaded with contracts=' + repr(sys.argv[2] + '._runtime.protocols.pagination' in sys.modules))
pagination = importlib.import_module(sys.argv[2] + '._runtime.protocols.pagination')
print('pagination types=' + repr(tuple(getattr(module, name) is getattr(pagination, name) for name in ('Page', 'Pager', 'AsyncPager'))))
print('pagination optional imports=' + repr([name for name in optional if name in sys.modules]))
print('client protocols=' + repr(hasattr(importlib.import_module(sys.argv[2]).Client, 'protocols')))
runtime = importlib.import_module(sys.argv[2] + '._runtime.protocols.options')
print('option identities=' + repr((module.PaginationOptions is runtime.PaginationOptions, module.StreamOptions is runtime.StreamOptions)))
print('construction threads unchanged=' + repr(threading.active_count() == before))
"""
_CLIENT_PROBE: Final = """
import importlib
import sys
sys.path.insert(0, sys.argv[1])
package = importlib.import_module(sys.argv[2])
with package.Client(max_retries=1):
    loaded = sorted(name.removeprefix(sys.argv[2] + '.') for name in sys.modules if name.startswith(sys.argv[2] + '._runtime.protocols.'))
print('configured client loads protocols=' + repr(loaded))
"""
_RECORDS: Final = (
    "BodySelector",
    "HeaderSelector",
    "StatusSelector",
    "ParameterTarget",
    "QuerystringTarget",
    "BodyTarget",
    "Origin",
    "PollSnapshot",
    "PaginationOptions",
    "PollOptions",
    "StreamOptions",
)
_COUNTS: Final = (
    ("None", None),
    ("0", 0),
    ("1", 1),
    ("2**63", 2**63),
    ("-1", -1),
    ("True", True),
    ("1.0", 1.0),
    ("'1'", "1"),
)
_DURATIONS: Final = (
    ("None", None),
    ("0", 0),
    ("0.0", 0.0),
    ("0.5", 0.5),
    ("1", 1),
    ("-1", -1),
    ("False", False),
    ("nan", float("nan")),
    ("inf", float("inf")),
    ("10**1000", 10**1000),
    ("'1'", "1"),
)
_OPTION_FIELDS: Final = (
    ("PaginationOptions", "max_pages", _COUNTS),
    ("PaginationOptions", "max_items", _COUNTS),
    ("PaginationOptions", "total_timeout", _DURATIONS),
    ("PollOptions", "max_polls", _COUNTS),
    ("PollOptions", "interval", _DURATIONS),
    ("PollOptions", "max_wait", _DURATIONS),
    ("PollOptions", "total_timeout", _DURATIONS),
    ("StreamOptions", "idle_timeout", _DURATIONS),
    ("StreamOptions", "reconnect", (("True", True), ("False", False), ("None", None), ("1", 1), ("'yes'", "yes"))),
    ("StreamOptions", "max_reconnects", _COUNTS),
    ("StreamOptions", "max_reconnect_wait", _DURATIONS),
    ("StreamOptions", "total_timeout", _DURATIONS),
)


def protocol_contracts(package: ModuleType, lines: list[str]) -> None:
    """Report the public protocol shapes, their validation, and their client integration."""
    protocols, options, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("protocols", "options", "responses")
    )
    records = importlib.import_module(f"{package.__name__}._runtime.protocols.records")
    _imports(package, lines)
    _shapes(protocols, lines)
    _selectors(protocols, lines)
    _origins(protocols, lines)
    _canonical_values(protocols, records, lines)
    _snapshots(protocols, responses, lines)
    _option_matrix(protocols, options, lines)
    _client_options(package, protocols, options, lines)


def _imports(package: ModuleType, lines: list[str]) -> None:
    """Import shared contracts in a fresh process before any test fixture imports HTTP libraries."""
    if (location := package.__file__) is None:
        msg = "Generated package has no source path"
        raise RuntimeError(msg)
    root = Path(location).parent.parent
    for probe in (_IMPORT_PROBE, _CLIENT_PROBE):
        completed = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(root), package.__name__],
            check=True,
            capture_output=True,
            text=True,
        )
        lines.extend(f"  {line}" for line in completed.stdout.splitlines())


def _shapes(protocols: ModuleType, lines: list[str]) -> None:
    """Expose declared fields, keyword-only constructors, resolvable hints, and immutable records."""
    for name in _RECORDS:
        record_type = getattr(protocols, name)
        parameters = inspect.signature(record_type).parameters.values()
        lines.extend((
            f"  {name} fields={tuple(item.name for item in fields(record_type))}",
            f"  {name} keyword-only={all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters)}"
            f" hints={tuple(get_type_hints(record_type))}",
        ))
    literals = (
        ("HeaderSelector.occurrence", get_type_hints(protocols.HeaderSelector)["occurrence"]),
        ("ParameterTarget.location", get_type_hints(protocols.ParameterTarget)["location"]),
        ("ProgressKey", protocols.ProgressKey),
    )
    lines.extend(f"  {label} values={get_args(hint)}" for label, hint in literals)
    for alias in ("Selector", "RequestTarget"):
        lines.append(f"  {alias} members={tuple(item.__name__ for item in get_args(getattr(protocols, alias)))}")
    progress = protocols.ProtocolProgress
    lines.append(
        f"  ProtocolProgress mapping={get_origin(progress) is collections.abc.Mapping} "
        f"args={get_args(progress) == (protocols.ProgressKey, int)}"
    )
    parameter = protocols.PollSnapshot.__parameters__[0]
    lines.append(
        f"  PollSnapshot covariance={parameter.__covariant__} default={getattr(parameter, '__default__', None)} "
        f"subscripted={get_origin(protocols.PollSnapshot[int]) is protocols.PollSnapshot}"
    )
    selector = protocols.BodySelector(pointer="/next")
    target = protocols.ParameterTarget(location="query", name="cursor")
    origin = protocols.Origin(scheme="https", host="api.example.com", port=443)
    for label, value, name, replacement in (
        ("selector", selector, "pointer", "/other"),
        ("target", target, "name", "page"),
        ("origin", origin, "port", 8443),
        ("options", protocols.PollOptions(), "interval", 1),
    ):
        record(
            lines,
            f"{label} frozen",
            lambda value=value, name=name, replacement=replacement: setattr(value, name, replacement),
        )
    for label, create in (
        ("selector positional", lambda: protocols.BodySelector("/next")),
        ("header positional", lambda: protocols.HeaderSelector("X-Cursor")),
        ("status positional", lambda: protocols.StatusSelector("status")),
        ("target positional", lambda: protocols.ParameterTarget("query", "cursor")),
        ("querystring positional", lambda: protocols.QuerystringTarget("filter", "")),
        ("body target positional", lambda: protocols.BodyTarget("/next")),
        ("origin positional", lambda: protocols.Origin("https", "api.example.com", 443)),
        ("options positional", lambda: protocols.PaginationOptions(10)),
    ):
        record(lines, label, create)
    record(lines, "equal selectors", lambda: selector == protocols.BodySelector(pointer="/next"))
    record(lines, "equal status selectors", lambda: protocols.StatusSelector() == protocols.StatusSelector())
    record(lines, "hashable targets", lambda: len({target, protocols.ParameterTarget(location="query", name="cursor")}))


def _selectors(protocols: ModuleType, lines: list[str]) -> None:
    """Accept RFC 6901 pointers and HTTP tokens as given, rejecting every other form or type."""
    for pointer in ("", "/", "/data", "/a~0b~1c", "/items/0", "/a b", "/é", "//"):
        record(lines, f"body pointer {pointer!r}", lambda pointer=pointer: protocols.BodySelector(pointer=pointer))
    for pointer in ("data", "/~", "/~2", "/a~", "$.data", "#/data"):
        record(lines, f"body pointer {pointer!r}", lambda pointer=pointer: protocols.BodySelector(pointer=pointer))
    for label, value in (("None", None), ("bytes", b"/data"), ("int", 1)):
        record(lines, f"body pointer {label}", lambda value=value: protocols.BodySelector(pointer=value))
    for name, occurrence in (("X-Cursor", "single"), ("link", "all"), ("x-next~page", "single")):
        record(
            lines,
            f"header {name} {occurrence}",
            lambda name=name, occurrence=occurrence: protocols.HeaderSelector(name=name, occurrence=occurrence),
        )
    record(lines, "header default occurrence", lambda: protocols.HeaderSelector(name="X-Cursor").occurrence)
    for label, arguments in (
        ("header space", {"name": "X Cursor"}),
        ("header empty", {"name": ""}),
        ("header colon", {"name": "X-Cursor:"}),
        ("header unicode", {"name": "X-é"}),
        ("header None", {"name": None}),
        ("header occurrence", {"name": "X-Cursor", "occurrence": "many"}),
        ("header occurrence None", {"name": "X-Cursor", "occurrence": None}),
        ("header missing name", {}),
    ):
        record(lines, label, lambda arguments=arguments: protocols.HeaderSelector(**arguments))
    record(lines, "status selector", lambda: protocols.StatusSelector())
    for location, name in (
        ("path", "petId"),
        ("query", "cursor"),
        ("query", "page size"),
        ("query", "é"),
        ("header", "X-Page"),
        ("cookie", "session"),
        ("query", ""),
        ("path", ""),
        ("header", "X Page"),
        ("cookie", "a;b"),
        ("body", "cursor"),
        ("querystring", "filter"),
        ("QUERY", "cursor"),
        (None, "cursor"),
        ("query", None),
    ):
        record(
            lines,
            f"parameter {location} {name!r}",
            lambda location=location, name=name: protocols.ParameterTarget(location=location, name=name),
        )
    for name, pointer in (
        ("filter", ""),
        ("filter", "/cursor"),
        ("filter", "/page~1size"),
        ("", "/cursor"),
        ("filter", "cursor"),
        (None, ""),
        ("filter", None),
    ):
        record(
            lines,
            f"querystring {name!r} {pointer!r}",
            lambda name=name, pointer=pointer: protocols.QuerystringTarget(name=name, pointer=pointer),
        )
    for pointer in ("", "/cursor", "/page/0", "cursor", None):
        record(lines, f"body target {pointer!r}", lambda pointer=pointer: protocols.BodyTarget(pointer=pointer))


def _origins(protocols: ModuleType, lines: list[str]) -> None:
    """Accept exactly the origins the client's URL rules produce, never rewriting another spelling."""
    for scheme, host, port in (
        ("https", "api.example.com", 443),
        ("http", "localhost", 8080),
        ("http", "127.0.0.1", 80),
        ("https", "::1", 8443),
        ("https", "2001:db8::1", 1),
        ("https", "xn--bcher-kva.example", 65535),
        ("https", "example.com.", 443),
        ("https", "my_host.local", 443),
        ("https", "a.example", 80),
        ("http", "a.example", 80),
        ("https", "[::1]", 443),
        ("HTTPS", "api.example.com", 443),
        ("ftp", "api.example.com", 21),
        ("ws", "api.example.com", 80),
        ("https", "API.example.com", 443),
        ("https", "b\u00fccher.example", 443),
        ("https", "256.0.0.1", 443),
        ("https", "01.2.3.4", 443),
        ("https", "", 443),
        ("https", "a b", 443),
        ("https", "user@api.example.com", 443),
        ("https", "a\x00b", 443),
        ("https", "\ud800", 443),
        ("https", "api.example.com", 0),
        ("https", "api.example.com", 65536),
        ("https", "api.example.com", True),
        ("https", "api.example.com", "443"),
        ("https", "api.example.com", 443.0),
        (None, "api.example.com", 443),
        ("https", None, 443),
        ("https", b"api.example.com", 443),
    ):
        record(
            lines,
            f"origin {scheme!r} {host!r} {port!r}",
            lambda scheme=scheme, host=host, port=port: protocols.Origin(scheme=scheme, host=host, port=port),
        )


def _canonical_values(protocols: ModuleType, records: ModuleType, lines: list[str]) -> None:
    """Encode resume state values as canonical JSON and refuse what JSON cannot hold."""
    cycle: list[object] = []
    cycle.append(cycle)
    for label, value in (
        ("string", "secret-cursor"),
        ("integer", 20),
        ("decimal", Decimal("1.50")),
        ("float", 2.5),
        ("large float", 1e16),
        ("small float", 1e-7),
        ("negative zero float", -0.0),
        ("whole float", 100.0),
        ("negative zero decimal", Decimal("-0")),
        ("exponent decimal", Decimal("1E+2")),
        ("whole decimal", Decimal("7")),
        ("large integer", 2**70),
        ("null", None),
        ("boolean", False),
        ("nested", {"b": 1, "a": [{"d": 1, "c": "é"}]}),
        ("reordered", {"a": [{"c": "é", "d": 1}], "b": 1}),
        ("list", [1, "two"]),
        ("tuple", (1, "two")),
        ("mapping proxy", MappingProxyType({"z": None, "y": True})),
        ("bytes", b"secret-cursor"),
        ("integer key", {1: "secret-cursor"}),
        ("nan", float("nan")),
        ("surrogate", "\ud800"),
        ("cyclic", cycle),
        ("integral decimal over conversion limit", Decimal("1" + "0" * 5000)),
    ):
        record(lines, f"canonical {label}", lambda value=value: _canonical(records, value))


def _deep() -> list[object]:
    """Return a list nested far beyond the interpreter recursion limit."""
    root: list[object] = []
    current = root
    for _ in range(100_000):
        current.append(inner := [])
        current = inner
    return root


def _canonical(records: ModuleType, value: object) -> tuple[bytes, bool]:
    """Return a value's canonical JSON and whether it survives a decode."""
    encoded = records.canonical_json(value)
    return encoded, records.canonical_json(json.loads(encoded)) == encoded


def _snapshots(protocols: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Copy the state, keep the data identity, and hide both from the representation."""
    info = responses.ResponseInfo(
        status_code=200,
        headers=responses.HeadersView((("x-state", "secret-header"),)),
        elapsed=0.25,
        content_type="application/json",
    )
    data = {"secret": "poll-data"}
    state = {"status": "secret-state", "steps": [1, 2]}
    snapshot = protocols.PollSnapshot(state=state, terminal=False, data=data, response=info)
    state["status"] = "changed"
    lines.extend((
        f"  snapshot repr={snapshot!r}",
        f"  snapshot secret={'secret' in repr(snapshot)} data identity={snapshot.data is data} "
        f"response identity={snapshot.response is info}",
        f"  snapshot state={type(snapshot.state).__name__} {dict(snapshot.state)!r}",
    ))
    record(lines, "snapshot frozen", lambda: setattr(snapshot, "terminal", True))
    record(
        lines,
        "snapshot terminal",
        lambda: protocols.PollSnapshot(state="done", terminal=True, data=None, response=info).terminal,
    )
    for label, arguments in (
        ("terminal integer", {"terminal": 1}),
        ("terminal string", {"terminal": "yes"}),
        ("response None", {"response": None}),
        ("response mapping", {"response": {"status_code": 200}}),
        ("state object", {"state": object()}),
        ("state nan", {"state": {"progress": float("nan")}}),
        ("state deeply nested", {"state": _deep()}),
    ):
        values = {"state": "running", "terminal": False, "data": data, "response": info, **arguments}
        record(lines, f"snapshot {label}", lambda values=values: protocols.PollSnapshot(**values))
    record(
        lines, "snapshot missing data", lambda: protocols.PollSnapshot(state="running", terminal=False, response=info)
    )


def _option_matrix(protocols: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Accept only the declared None, zero, and positive values of each kind's options."""
    for type_name, name, values in _OPTION_FIELDS:
        option_type = getattr(protocols, type_name)
        for label, value in values:
            record(
                lines,
                f"{type_name}.{name} {label}",
                lambda option_type=option_type, name=name, value=value: getattr(option_type(**{name: value}), name),
            )
    for type_name in ("PaginationOptions", "PollOptions", "StreamOptions"):
        option_type = getattr(protocols, type_name)
        omitted = option_type()
        lines.append(
            f"  {type_name} omitted={omitted!r} "
            f"UNSET={all(getattr(omitted, item.name) is options.UNSET for item in fields(option_type))}"
        )
    for label, create in (
        ("pagination unknown", lambda: protocols.PaginationOptions(interval=1)),
        ("poll unknown", lambda: protocols.PollOptions(idle_timeout=1)),
        ("stream total timeout", lambda: protocols.StreamOptions(total_timeout=1)),
        ("stream reconnect without limit", lambda: protocols.StreamOptions(reconnect=True, max_reconnects=0)),
        ("explicit UNSET", lambda: protocols.PollOptions(interval=options.UNSET, max_wait=None)),
        (
            "all limits",
            lambda: protocols.StreamOptions(
                idle_timeout=None,
                reconnect=True,
                max_reconnects=None,
                max_reconnect_wait=0.25,
            ),
        ),
    ):
        record(lines, label, create)


_HELPER_KEYWORDS: Final = ("helper_defaults", "cache_stores", "allowed_origins")


def _client_options(package: ModuleType, protocols: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Take helper defaults only as a client keyword, checking them against the package's helpers at construction."""
    pagination = protocols.PaginationOptions(max_items=0, total_timeout=3)
    source = {"pages.all": pagination, "jobs.run": protocols.PollOptions(interval=2)}
    parameters = inspect.signature(package.Client).parameters
    parameter = parameters["helper_defaults"]
    lines.append(
        f"  client helper_defaults keyword={parameter.kind.name} default={parameter.default!r} "
        f"helper keywords={[name for name in _HELPER_KEYWORDS if name in parameters]} "
        f"view keyword={'helper_defaults' in inspect.signature(package.ClientView.with_options).parameters} "
        f"request field={'helper_defaults' in {item.name for item in fields(options.RequestOptions)}}"
    )
    for label, defaults in (
        ("helper defaults", source),
        ("empty helper defaults", {}),
        ("stream defaults", {"events.watch": protocols.StreamOptions(idle_timeout=None, total_timeout=0)}),
        ("defaults of a helper it lacks", {"users.all": pagination}),
        ("defaults of another kind", {"pages.all": protocols.PollOptions()}),
        ("webhook options", {"jobs.run": protocols.WebhookOptions()}),
        ("defaults value None", {"pages.all": None}),
        ("defaults value mapping", {"pages.all": {"max_items": 0}}),
    ):
        record(lines, f"client {label}", lambda defaults=defaults: package.Client(helper_defaults=defaults).close())
    for label, create in (
        ("client protocols", lambda: package.Client(protocols=None)),
        ("view helper defaults", lambda: package.Client().with_options(helper_defaults=source)),
        ("request helper defaults", lambda: options.RequestOptions(helper_defaults=source)),
    ):
        record(lines, label, create)
    for label, module in (
        ("options", options),
        ("errors", importlib.import_module(f"{package.__name__}.errors")),
    ):
        record(lines, f"unknown {label} attribute", lambda module=module: getattr(module, "MissingProtocolType"))
        record(
            lines, f"{label} dir", lambda module=module: [name for name in dir(module) if name.startswith("Protocol")]
        )
    _calls(package, source, lines)
    run(lambda: _async_calls(package, source, lines))


def _trace(package: ModuleType) -> object:
    return argument(package, "listPets", "header", "X-Trace", "t")


def _calls(package: ModuleType, defaults: Any, lines: list[str]) -> None:
    """Send ordinary calls unchanged through a client configured with helper defaults."""
    exchange = Exchange(lines)
    trace = _trace(package)
    with exchange.client() as native, package.Client(http_client=native, helper_defaults=defaults) as api:
        exchange.respond(json_response(200, [{"id": 1, "name": "cat"}], **{"X-Rate": "1"}))
        record(lines, "configured client list", lambda: api.pets.list_pets(X_Trace=trace))
        view = api.with_options(total_timeout=2)
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}))
        record(lines, "configured view list", lambda: view.pets.list_pets(X_Trace=trace))
    with (
        exchange.client() as native,
        package.Client(http_client=native, helper_defaults=None) as api,
    ):
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}))
        record(lines, "default helper settings list", lambda: api.pets.list_pets(X_Trace=trace))


async def _async_calls(package: ModuleType, defaults: Any, lines: list[str]) -> None:
    """Send an asynchronous call through a client configured with helper defaults."""
    exchange = Exchange(lines)
    trace = _trace(package)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, helper_defaults=defaults) as api,
    ):
        exchange.respond(json_response(200, [{"id": 2, "name": "dog"}], **{"X-Rate": "1"}))
        await arecord(lines, "async configured client list", lambda: api.pets.list_pets(X_Trace=trace))
