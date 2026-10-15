"""Generate a FastAPI server and an HTTPX2 client from one OpenAPI document and call the server through the client.

Both targets share one models module. The server's services record the typed arguments each handler receives, a
wire recorder in front of the application records each request line and response status, and every call reports
the value the client returned or the error it raised. The calls run synchronously and with asyncio, in process
through HTTPX2's ASGI transports and, for a case that asks for it, over a local Uvicorn socket; transports that
agree print one section. A case that names other `BACKENDS` also calls the server through a client of each, generated
with its own models module, and reports each client under its backend's name where it disagrees.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import re
import shutil
import socket
import threading
import warnings
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx2
import uvicorn
from fastapi.testclient import TestClient
from pydantic import BaseModel

from datamodel_code_generator import DataModelType, generate
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.api_generation.support.backends import backend_settings
from tests.api_generation.support.client_generation import generator_warnings
from tests.api_generation.support.client_runtime import agreeing_backends, describe
from tests.api_generation.support.generated_packages import generated_root, import_generated

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager
    from types import ModuleType

    from fastapi import FastAPI

SOURCE: Final = Path(__file__).parents[2] / "data" / "generation_platform"
_BOUNDARY: Final = re.compile(r"boundary=[^;\s]+")
_MESSAGE: Final = re.compile(r'"msg":"(?:[^"\\]|\\.)*"')
Calls = list[tuple[str, "Callable[[], object]"]]


@dataclass
class Kit:
    """What a scenario's calls use: a client, the shared models, the client package, and the execution mode.

    `connect` opens another client over the same HTTPX2 client with other settings, such as credentials; the run
    closes each one after the calls.
    """

    models: ModuleType
    client: ModuleType
    http: Any
    options: dict[str, Any]
    asynchronous: bool
    opened: list[Any] = field(default_factory=list)

    def connect(self, **settings: Any) -> Any:
        """Open a client of the execution mode with the run's settings and these."""
        kind = self.client.AsyncClient if self.asynchronous else self.client.Client
        self.opened.append(opened := kind(http_client=self.http, **self.options, **settings))
        return opened

    @cached_property
    def api(self) -> Any:
        """Return the client with the run's settings."""
        return self.connect()

    def then(self, value: object, function: Callable[[Any], object]) -> object:
        """Apply a function to a call's result, after awaiting it in the asyncio mode."""
        if not self.asynchronous:
            return function(value)

        async def later() -> object:
            return function(await value)

        return later()

    def module(self, name: str) -> ModuleType:
        """Return a module of the client package, such as `bodies`."""
        return importlib.import_module(f"{self.client.__name__}.{name}")


def show(value: object) -> str:
    """Show a value without representations that change between library releases, such as time zones."""
    shown = repr(value)
    match value:
        case BaseModel():
            shown = f"{type(value).__name__}({', '.join(f'{name}={show(item)}' for name, item in value)})"
        case date():
            shown = f"{type(value).__name__}({value.isoformat()})"
        case Enum():
            shown = f"{type(value).__name__}.{value.name}"
        case list():
            shown = f"[{', '.join(map(show, value))}]"
        case tuple():
            shown = f"({', '.join(map(show, value))}{',' if len(value) == 1 else ''})"
        case dict():
            shown = f"{{{', '.join(f'{show(key)}: {show(item)}' for key, item in value.items())}}}"
        case _ if hasattr(value, "continuation") and hasattr(value, "items"):
            shown = f"Page({show(value.items)}, next={show(value.continuation)})"
        case _ if hasattr(value, "event_type") and hasattr(value, "sequence"):
            shown = f"Event({value.sequence}, {value.event_type!r}, {show(value.data)}, id={value.event_id!r})"
        case _ if hasattr(value, "info") and hasattr(value, "data"):
            shown = f"Response({show(value.data)}, {value.info.status_code}, {value.info.content_type!r})"
        case _:
            pass
    return shown


class Recorder:
    """Collect a transcript: the calls, the requests the application receives, and what its handlers get."""

    def __init__(self) -> None:
        """Start an empty transcript."""
        self.lines: list[str] = []

    def __call__(self, name: str, arguments: dict[str, object]) -> None:
        """Record a handler call with the typed arguments it received."""
        self.lines.append(f"  {name}({', '.join(f'{key}={show(value)}' for key, value in arguments.items())})")

    def wire(self, app: FastAPI) -> Callable[..., Any]:
        """Return the application behind a recorder of each request line and media type and each response status."""

        async def recorded(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            path = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1")
            query = f"?{scope['query_string'].decode('latin-1')}" if scope["query_string"] else ""
            media = dict(scope["headers"]).get(b"content-type", b"").decode("latin-1")
            kind = f" [{_BOUNDARY.sub('boundary=<boundary>', media)}]" if media else ""
            self.lines.append(f"  > {scope['method']} {path}{query}{kind}")

            async def sending(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    answered = dict(message.get("headers", ())).get(b"content-type", b"-").decode("latin-1")
                    self.lines.append(f"  < {message['status']} {answered}")
                await send(message)

            await app(scope, receive, sending)

        return recorded


def service(protocol: type, record: Recorder, **answers: Callable[..., object]) -> object:
    """Implement a service Protocol: each method records its typed arguments, then returns its answer or None."""

    def method(name: str) -> Callable[..., object]:
        answer = answers.get(name, _nothing)

        def handle(self: object, **arguments: object) -> object:
            del self
            record(name, arguments)
            return answer(**arguments)

        return handle

    methods = {name: method(name) for name in sorted(protocol.__abstractmethods__)}
    return type(protocol.__name__.removesuffix("Service"), (protocol,), methods)()


def _nothing(**_: object) -> None:
    return None


def _generate(scenario: ModuleType, root: Path, package: str) -> str:
    """Generate the server, then each client, into one root with the same model options.

    Report whether the clients share the server's models, and the generator's warnings for each client.
    """
    source = shutil.copy2(SOURCE / scenario.DOCUMENT, root / Path(scenario.DOCUMENT).name)
    models = root / f"{package}_models.py"
    model = {
        "input_file_type": "openapi",
        "target_python_version": "3.11",
        "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
        "output": models,
        **getattr(scenario, "MODEL", {}),
    }
    generate(
        source,
        **model,
        generate_server="fastapi",
        server_output=root / package,
        server_package=package,
        server_model_package=f"{package}_models",
    )
    served = models.read_bytes()
    client = {
        f"client_{key}": json.loads((SOURCE / value).read_text(encoding="utf-8")) if key == "protocols" else value
        for key, value in getattr(scenario, "CLIENT", {}).items()
    }
    warned: list[str] = []
    for suffix, backend in _clients(scenario):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", UserWarning)
            generate(
                source,
                **{
                    **model,
                    **(backend_settings(backend) if backend else {}),
                    "output": root / f"{package}{suffix}_models.py",
                },
                generate_client="httpx2",
                client_output=root / f"{package}_client{suffix}",
                client_package=f"{package}_client{suffix}",
                client_model_package=f"{package}{suffix}_models",
                **client,
            )
        warned.extend(f"client{suffix.replace('_', ' ', 1)} {line}\n" for line in generator_warnings(caught))
    return f"models shared {models.read_bytes() == served}\n" + "".join(warned)


def _clients(scenario: ModuleType) -> list[tuple[str, DataModelType | None]]:
    """Return the package suffix and model backend of each client of a case: the shared models' first, as None."""
    return [
        ("", None),
        *((f"_{backend.value.replace('.', '_')}", backend) for backend in getattr(scenario, "BACKENDS", ())),
    ]


def _drained(value: object) -> object:
    """Read a pager or an event stream to its end inside its block."""
    if hasattr(value, "__enter__") and hasattr(value, "__iter__"):
        with value:
            return list(value)
    return value


async def _adrained(value: object) -> object:
    """Await a call, then read an asyncio pager or event stream to its end inside its block."""
    while inspect.isawaitable(value):
        value = await value
    if hasattr(value, "__aenter__") and hasattr(value, "__aiter__"):
        async with value:
            return [item async for item in value]
    return value


def _sync(calls: Calls, lines: list[str]) -> None:
    for label, call in calls:
        lines.append(label)
        try:
            result = _drained(call())
        except Exception as error:  # noqa: BLE001
            lines.append(f"  ! {describe(error)}")
        else:
            lines.append(f"  = {show(result)}")


async def _async(calls: Calls, lines: list[str]) -> None:
    for label, call in calls:
        lines.append(label)
        try:
            result = await _adrained(call())
        except Exception as error:  # noqa: BLE001
            lines.append(f"  ! {describe(error)}")
        else:
            lines.append(f"  = {show(result)}")


class _Socket:
    """Serve an application with Uvicorn on a loopback socket that listens before the server starts."""

    def __init__(self, app: Callable[..., Any]) -> None:
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]})

    def __enter__(self) -> dict[str, str]:
        self.thread.start()
        return {"base_url": f"http://127.0.0.1:{self.socket.getsockname()[1]}"}

    def __exit__(self, *_: object) -> None:
        self.server.should_exit = True
        self.thread.join()
        self.socket.close()


def _transports(
    scenario: ModuleType, app: Callable[..., Any]
) -> Iterator[tuple[str, AbstractContextManager[dict[str, str]], Callable[[], Any], Callable[[], Any]]]:
    """Yield each transport's name, the client settings that reach its server, and its HTTPX2 clients."""
    yield (
        "in-process",
        nullcontext({}),
        lambda: TestClient(app, raise_server_exceptions=False),
        lambda: httpx2.AsyncClient(transport=httpx2.ASGITransport(app, raise_app_exceptions=False)),
    )
    if getattr(scenario, "SOCKET", False):
        yield "socket", _Socket(app), httpx2.Client, httpx2.AsyncClient


def _sync_report(
    scenario: ModuleType, title: str, native: Callable[[], Any], shared: dict[str, Any], lines: list[str]
) -> str:
    """Make the scenario's calls with a synchronous client over a native client, then close what they opened."""
    lines[:] = [f"# {title} sync"]
    with native() as http:
        kit = Kit(http=http, asynchronous=False, **shared)
        try:
            _sync(scenario.calls(kit), lines)
        finally:
            for opened in kit.opened:
                opened.close()
    return "\n".join(lines) + "\n"


async def _async_report(
    scenario: ModuleType, title: str, native: Callable[[], Any], shared: dict[str, Any], lines: list[str]
) -> str:
    """Make the scenario's calls with an asyncio client over a native client, then close what they opened."""
    lines[:] = [f"# {title} async"]
    async with native() as http:
        kit = Kit(http=http, asynchronous=True, **shared)
        try:
            await _async(scenario.calls(kit), lines)
        finally:
            for opened in kit.opened:
                await opened.aclose()
    return "\n".join(lines) + "\n"


def cross_report(case: str, root: Path) -> str:
    """Generate the case's server and client, then call every operation of the server through the client."""
    scenario = importlib.import_module(f"tests.api_generation.scenarios.cross_{case}")
    header = _generate(scenario, root, case)
    reports: dict[str, str] = {}
    suffixes = [suffix for suffix, _ in _clients(scenario)]
    with generated_root(root, case, *(f"{case}_client{suffix}" for suffix in suffixes)):
        server = import_generated(case)
        clients = [
            (
                suffix.replace("_", " ", 1),
                import_generated(f"{case}_client{suffix}"),
                importlib.import_module(f"{case}{suffix}_models"),
            )
            for suffix in suffixes
        ]
        recorder = Recorder()
        app = server.create_app(**scenario.services(server, clients[0][2], recorder))
        if hasattr(scenario, "extend"):
            scenario.extend(app, recorder)
        for name, reach, native, anative in _transports(scenario, recorder.wire(app)):
            with reach as options:
                for label, client, models in clients:
                    shared = {"models": models, "client": client, "options": options}
                    title = f"{case} {name}{label}"
                    reports[f"{name}{label} sync"] = _sync_report(scenario, title, native, shared, recorder.lines)
                    reports[f"{name}{label} async"] = asyncio.run(
                        _async_report(scenario, title, anative, shared, recorder.lines)
                    )
    return _MESSAGE.sub('"msg":"<msg>"', header + agreeing_backends(reports))
