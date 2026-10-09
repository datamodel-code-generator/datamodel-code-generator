"""Exercise generated protocol records, option boundaries, client integration, and resume-state envelopes."""

from __future__ import annotations

import collections.abc
import copy
import importlib
import inspect
import json
import pickle
import subprocess
import sys
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, get_args, get_origin, get_type_hints

from tests.data.python.client_runtime import Exchange, arecord, argument, json_response, record, run

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
names = importlib.import_module(sys.argv[2] + '._runtime.protocols.names')
print('dir lists lazy names=' + repr(('ProtocolDataError' in dir(errors), 'SessionLimitError' in dir(errors), 'ProtocolClientOptions' in dir(options), 'ProtocolClientOptions' in dir(names))))
print('dir loads nothing=' + repr(sys.argv[2] + '._runtime.protocols.options' not in sys.modules and sys.argv[2] + '._runtime.protocols.errors' not in sys.modules))
print('protocol errors loaded on use=' + repr(errors.SessionLimitError.__module__ == sys.argv[2] + '._runtime.protocols.errors'))
print('lazy names cached=' + repr(('SessionLimitError' in vars(errors), 'ProtocolDataError' in vars(errors), options.ProtocolClientOptions is names.ProtocolClientOptions, 'ProtocolClientOptions' in vars(options), 'ProtocolClientOptions' in vars(names))))
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
print('option identities=' + repr((options.ProtocolClientOptions is runtime.ProtocolClientOptions, module.ProtocolDefaults is runtime.ProtocolDefaults)))
state = module.ResumeState(helper='helper', state={'page': 1})
print('resume round trip=' + repr(module.import_state(state.export()).export() == state.export()))
print('construction threads unchanged=' + repr(threading.active_count() == before))
"""
_CLIENT_PROBE: Final = """
import importlib
import sys
sys.path.insert(0, sys.argv[1])
package = importlib.import_module(sys.argv[2])
options = importlib.import_module(sys.argv[2] + '.options')
with package.Client(options=options.ClientOptions(retry=options.RetryOptions(max_retries=1))):
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
    "ProtocolSecurityContext",
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
    ("PollOptions", "max_polls", _COUNTS),
    ("PollOptions", "interval", _DURATIONS),
    ("PollOptions", "max_wait", _DURATIONS),
    ("StreamOptions", "idle_timeout", _DURATIONS),
    ("StreamOptions", "max_line_bytes", _COUNTS),
    ("StreamOptions", "max_event_bytes", _COUNTS),
    ("StreamOptions", "reconnect", (("True", True), ("False", False), ("None", None), ("1", 1), ("'yes'", "yes"))),
    ("StreamOptions", "max_reconnects", _COUNTS),
    ("StreamOptions", "max_reconnect_wait", _DURATIONS),
)
_VALID: Final = {"helper": "helper-secret", "state": {"cursor": "state-secret", "page": 2}, "version": 1}


def protocol_contracts(package: ModuleType, lines: list[str]) -> None:
    """Report the public protocol shapes, their validation, and their client integration."""
    protocols, options, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("protocols", "options", "responses")
    )
    records = importlib.import_module(f"{package.__name__}._runtime.protocols.records")
    _imports(package, lines)
    _shapes(protocols, options, lines)
    _selectors(protocols, lines)
    _origins(protocols, lines)
    _canonical_values(protocols, records, lines)
    _snapshots(protocols, responses, lines)
    _resume_states(protocols, lines)
    _imported_states(protocols, lines)
    _option_matrix(protocols, options, lines)
    _security(protocols, lines)
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


def _shapes(protocols: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Expose declared fields, keyword-only constructors, resolvable hints, and immutable records."""
    for name in (*_RECORDS, "ProtocolDefaults", "ProtocolClientOptions"):
        record_type = getattr(protocols, name, None) or getattr(options, name)
        parameters = inspect.signature(record_type).parameters.values()
        lines.extend((
            f"  {name} fields={tuple(item.name for item in fields(record_type))}",
            f"  {name} keyword-only={all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters)}"
            f" hints={tuple(get_type_hints(record_type))}",
        ))
    opaque = protocols.ResumeState
    parameters = inspect.signature(opaque).parameters.values()
    lines.append(
        f"  ResumeState parameters={tuple(item.name for item in parameters)} "
        f"keyword-only={all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters)} "
        f"hints={tuple(get_type_hints(opaque.__init__))} final={getattr(opaque, '__final__', False)}"
    )
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
        ("security positional", lambda: protocols.ProtocolSecurityContext("tenant")),
        ("defaults positional", lambda: protocols.ProtocolDefaults(options.SessionOptions())),
        ("client options positional", lambda: options.ProtocolClientOptions(None)),
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
        record(lines, f"canonical {label}", lambda value=value: _canonical(protocols, records, value))


def _deep() -> list[object]:
    """Return a list nested far beyond the interpreter recursion limit."""
    root: list[object] = []
    current = root
    for _ in range(100_000):
        current.append(inner := [])
        current = inner
    return root


def _canonical(protocols: ModuleType, records: ModuleType, value: object) -> tuple[bytes, bool, bool]:
    """Return a value's canonical JSON, whether it survives a decode, and whether resume state exports it."""
    encoded = records.canonical_json(value)
    state = protocols.ResumeState(helper="", state=value).export()
    return encoded, records.canonical_json(json.loads(encoded)) == encoded, b'"state":' + encoded + b"," in state


def _snapshots(protocols: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Copy the state, keep the data identity, and hide both from the representation."""
    info = responses.ResponseInfo(
        status_code=200,
        headers=responses.HeadersView((("x-state", "secret-header"),)),
        call_id="call-1",
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


def _resume_states(protocols: ModuleType, lines: list[str]) -> None:
    """Keep a resume token opaque, validate its fields, and export the documented JSON."""
    state = protocols.ResumeState(helper="helper-secret", state={"z": [1, 2.5, None, True], "a": "é", "n": 1e100})
    exported = state.export()
    lines.extend((
        f"  resume repr={state!r} str={state} secret={'secret' in repr(state) + str(state)}",
        f"  resume export={exported.decode()}",
        f"  resume export stable={state.export() == exported}",
        f"  resume identity equality={state == state}/{state == protocols.import_state(exported)}",
        f"  resume dict={hasattr(state, '__dict__')} public={[name for name in dir(state) if not name.startswith('_')]}",
    ))
    imported = protocols.import_state(exported)
    lines.append(
        f"  resume round trip={imported!r} distinct={imported is not state} same={imported.export() == exported}"
    )
    minimal = protocols.ResumeState(helper="", state=None)
    record(lines, "resume minimal export", lambda: minimal.export())
    record(lines, "resume set state", lambda: setattr(state, "_state_json", b"{}"))
    record(lines, "resume set new", lambda: setattr(state, "version", 2))
    record(lines, "resume delete state", lambda: delattr(state, "_state_json"))
    lines.append(f"  resume copies={copy.copy(state) is state}/{copy.deepcopy(state) is state}")
    record(lines, "resume pickle", lambda: pickle.dumps(state))
    valid = {"helper": "h", "state": {"page": 1}}
    for label, changes in (
        ("helper None", {"helper": None}),
        ("helper surrogate", {"helper": "\ud800"}),
        ("state integer over conversion limit", {"state": {"page": 10**5000}}),
        ("state object", {"state": object()}),
        ("state set", {"state": {1, 2}}),
        ("state deeply nested", {"state": _deep()}),
    ):
        record(lines, f"resume {label}", lambda changes=changes: protocols.ResumeState(**{**valid, **changes}))
    record(lines, "resume missing state", lambda: protocols.ResumeState(helper="h"))
    record(lines, "resume positional", lambda: protocols.ResumeState("h", {}))


def _token(changes: dict[str, Any], *, drop: str | None = None) -> bytes:
    """Build a token by the documented form, independently of the generated runtime."""
    body = {name: value for name, value in {**_VALID, **changes}.items() if name != drop}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _imported_states(protocols: ModuleType, lines: list[str]) -> None:
    """Reject form, version, and members in that order, never revealing the state."""
    valid = _token({})
    outcomes: list[str] = []
    for label, data in (
        ("valid", valid),
        ("not JSON", b"{"),
        ("array", b"[]"),
        ("invalid UTF-8", b"\xff"),
        ("duplicate member", valid[:-1] + b',"version":1}'),
        ("NaN", valid.replace(b'"page":2', b'"page":NaN')),
        ("string input", valid.decode()),
        ("bytearray input", bytearray(valid)),
        ("extra field", _token({"extra": 1})),
        ("missing field", _token({}, drop="state")),
        ("version string", _token({"version": "1"})),
        ("version bool", _token({"version": True})),
        ("version float", _token({"version": 1.0})),
        ("helper type", _token({"helper": 1})),
        ("deeply nested state", valid.replace(b'{"cursor":"state-secret","page":2}', b"[" * 100_000 + b"]" * 100_000)),
        ("version 2", _token({"version": 2})),
        ("version 0", _token({"version": 0})),
        ("version 2 extra field", _token({"version": 2, "extra": 1})),
        ("version 2 missing field", _token({"version": 2}, drop="state")),
        ("version missing", _token({}, drop="version")),
    ):
        record(outcomes, f"import {label}", lambda data=data: protocols.import_state(data))
    lines.extend(outcomes)
    lines.append(f"  import secret={any('secret' in line for line in outcomes)}")
    lines.append(f"  import exported independent token={protocols.import_state(valid).export() == valid}")


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
                max_line_bytes=1,
                max_event_bytes=1,
                reconnect=True,
                max_reconnects=None,
                max_reconnect_wait=0.25,
            ),
        ),
    ):
        record(lines, label, create)


def _security(protocols: ModuleType, lines: list[str]) -> None:
    """Hide the partition from the representation and copy origin sequences into tuples."""
    origin = protocols.Origin(scheme="https", host="files.example.com", port=443)
    origins = [origin]
    context = protocols.ProtocolSecurityContext(credential_partition="tenant-secret", allowed_origins=origins)
    origins.clear()
    lines.extend((
        f"  security repr={context!r} secret={'secret' in repr(context)}",
        f"  security origins={type(context.allowed_origins).__name__} identity={context.allowed_origins[0] is origin}",
    ))
    record(
        lines,
        "security default origins",
        lambda: protocols.ProtocolSecurityContext(credential_partition="anonymous").allowed_origins,
    )
    record(lines, "security frozen", lambda: setattr(context, "credential_partition", "other"))
    for label, arguments in (
        ("missing partition", {}),
        ("empty partition", {"credential_partition": ""}),
        ("newline partition", {"credential_partition": "tenant\n"}),
        ("delete partition", {"credential_partition": "tenant\x7f"}),
        ("C1 partition", {"credential_partition": "tenant\x85"}),
        ("partition None", {"credential_partition": None}),
        ("partition bytes", {"credential_partition": b"tenant"}),
        ("unicode partition", {"credential_partition": "ténant space"}),
        ("origin string", {"credential_partition": "tenant", "allowed_origins": ("https://files.example.com",)}),
        ("origins string", {"credential_partition": "tenant", "allowed_origins": "https://files.example.com"}),
        ("origins generator", {"credential_partition": "tenant", "allowed_origins": iter((origin,))}),
        ("origins None", {"credential_partition": "tenant", "allowed_origins": None}),
        ("duplicate origins", {"credential_partition": "tenant", "allowed_origins": (origin, origin)}),
    ):
        record(lines, f"security {label}", lambda arguments=arguments: protocols.ProtocolSecurityContext(**arguments))


def _client_options(package: ModuleType, protocols: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Accept protocol settings only on client options, freezing helper defaults without copying their values."""
    session = options.SessionOptions(total_timeout=3)
    pagination = protocols.PaginationOptions(max_items=0)
    defaults = protocols.ProtocolDefaults(session=session, options=pagination)
    lines.append(
        f"  defaults identity={defaults.session is session}/{defaults.options is pagination} "
        f"omitted={protocols.ProtocolDefaults()!r}"
    )
    for label, arguments in (
        ("poll", {"options": protocols.PollOptions(interval=2)}),
        ("stream", {"options": protocols.StreamOptions(reconnect=True)}),
        ("session None", {"session": None}),
        ("session request options", {"session": options.RequestOptions()}),
        ("webhook options", {"options": protocols.WebhookOptions()}),
        ("options None", {"options": None}),
        ("options mapping", {"options": {"max_items": 0}}),
    ):
        record(lines, f"defaults {label}", lambda arguments=arguments: protocols.ProtocolDefaults(**arguments))
    source = {"users.all": defaults, "jobs": protocols.ProtocolDefaults()}
    security = protocols.ProtocolSecurityContext(credential_partition="tenant-secret")
    configured = options.ProtocolClientOptions(security=security, defaults=source)
    source.clear()
    lines.extend((
        f"  protocol options repr={configured!r}",
        f"  protocol options copy={type(configured.defaults).__name__} keys={tuple(configured.defaults)} "
        f"identity={configured.defaults['users.all'] is defaults}/{configured.security is security}",
    ))
    record(lines, "protocol options frozen defaults", lambda: configured.defaults.__setitem__("x", defaults))
    record(lines, "protocol options omitted", lambda: options.ProtocolClientOptions())
    record(lines, "protocol options anonymous", lambda: options.ProtocolClientOptions(security=None, defaults={}))
    for name in ("users", "users.all", "_private.x1", "v2.users.list_all", "match.case", "élèves"):
        record(
            lines,
            f"helper name {name!r}",
            lambda name=name: tuple(options.ProtocolClientOptions(defaults={name: defaults}).defaults),
        )
    for name in (
        "",
        "users.",
        ".users",
        "users..all",
        "class",
        "users.class",
        "1users",
        "users-all",
        "users all",
        3,
        None,
    ):
        record(
            lines, f"helper name {name!r}", lambda name=name: options.ProtocolClientOptions(defaults={name: defaults})
        )
    for label, arguments in (
        ("security string", {"security": "anonymous"}),
        ("security options", {"security": pagination}),
        ("defaults list", {"defaults": [("users", defaults)]}),
        ("defaults None", {"defaults": None}),
        ("defaults value", {"defaults": {"users": pagination}}),
        ("defaults value None", {"defaults": {"users": None}}),
    ):
        record(
            lines, f"protocol options {label}", lambda arguments=arguments: options.ProtocolClientOptions(**arguments)
        )
    client_options = options.ClientOptions(protocols=configured)
    hints = get_type_hints(options.ClientOptions)
    lines.append(
        f"  client protocols identity={client_options.protocols is configured} "
        f"omitted={options.ClientOptions().protocols!r} "
        f"hint={hints['protocols'] == options.ProtocolClientOptions | options.Unset | None} "
        f"defaults in options={hasattr(options, 'ProtocolDefaults')} "
        f"request field={'protocols' in {item.name for item in fields(options.RequestOptions)}}"
    )
    for label, create in (
        ("client protocols None", lambda: options.ClientOptions(protocols=None).protocols),
        ("client protocols mapping", lambda: options.ClientOptions(protocols={})),
        ("client protocols security", lambda: options.ClientOptions(protocols=security)),
        ("request protocols", lambda: options.RequestOptions(protocols=configured)),
        ("request protocols None", lambda: options.RequestOptions(protocols=None)),
    ):
        record(lines, label, create)
    for label, module in (
        ("options", options),
        ("errors", importlib.import_module(f"{package.__name__}.errors")),
        ("names", importlib.import_module(f"{package.__name__}._runtime.protocols.names")),
    ):
        record(lines, f"unknown {label} attribute", lambda module=module: getattr(module, "MissingProtocolType"))
        record(
            lines, f"{label} dir", lambda module=module: [name for name in dir(module) if name.startswith("Protocol")]
        )
    record(lines, "client with defaults of helpers it lacks", lambda: package.Client(options=client_options))
    secured = options.ClientOptions(protocols=options.ProtocolClientOptions(security=security))
    _calls(package, options, secured, lines)
    run(lambda: _async_calls(package, options, secured, lines))


def _trace(package: ModuleType) -> object:
    return argument(package, "listPets", "header", "X-Trace", "t")


def _calls(package: ModuleType, options: ModuleType, client_options: Any, lines: list[str]) -> None:
    """Send ordinary calls unchanged through a client configured with protocol settings."""
    exchange = Exchange(lines)
    trace = _trace(package)
    with exchange.client() as native, package.Client(http_client=native, options=client_options) as api:
        exchange.respond(json_response(200, [{"id": 1, "name": "cat"}], **{"X-Rate": "1"}))
        record(lines, "configured client list", lambda: api.pets.list_pets(x_trace=trace))
        view = api.with_options(options.RequestOptions(total_timeout=2))
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}))
        record(lines, "configured view list", lambda: view.pets.list_pets(x_trace=trace))
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=options.ClientOptions(protocols=None)) as api,
    ):
        exchange.respond(json_response(200, [], **{"X-Rate": "1"}))
        record(lines, "anonymous protocols list", lambda: api.pets.list_pets(x_trace=trace))
    record(lines, "client wrong protocols", lambda: package.Client(options=options.ClientOptions(protocols="x")))


async def _async_calls(package: ModuleType, options: ModuleType, client_options: Any, lines: list[str]) -> None:
    """Send an asynchronous call through a client configured with protocol settings."""
    exchange = Exchange(lines)
    trace = _trace(package)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, options=client_options) as api,
    ):
        exchange.respond(json_response(200, [{"id": 2, "name": "dog"}], **{"X-Rate": "1"}))
        await arecord(lines, "async configured client list", lambda: api.pets.list_pets(x_trace=trace))
