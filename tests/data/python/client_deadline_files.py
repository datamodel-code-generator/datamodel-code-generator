"""Report generated-client ownership when an asynchronous file operation outlives cancellation."""

from __future__ import annotations

import asyncio
import importlib
import io
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Protocol
from unittest.mock import patch

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from concurrent.futures import Executor
    from types import ModuleType


class _DiskGate:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.proceed = threading.Event()

    def wait(self) -> None:
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            if not self.proceed.wait(5):
                msg = "file fixture was not released"
                raise TimeoutError(msg)
        finally:
            self.loop.call_soon_threadsafe(self.finished.set)


class _BlockedFile(io.BytesIO):
    def __init__(
        self,
        gate: _DiskGate,
        *,
        failure: BaseException | None = None,
        close_failure: BaseException | None = None,
        close_gate: _DiskGate | None = None,
        reading: bool = False,
    ) -> None:
        super().__init__(b"upload")
        self.gate = gate
        self.failure = failure
        self.close_failure = close_failure
        self.close_gate = close_gate
        self.reading = reading
        self.blocked = False
        self.closes = 0
        self.threads: list[int] = []

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if whence == os.SEEK_END and not self.reading and not self.blocked:
            self.blocked = True
            self.threads.append(threading.get_ident())
            self.gate.wait()
            if self.failure is not None:
                raise self.failure
        return super().seek(offset, whence)

    def read(self, size: int | None = -1, /) -> bytes:
        if self.reading and not self.blocked:
            self.blocked = True
            self.threads.append(threading.get_ident())
            self.gate.wait()
            if self.failure is not None:
                raise self.failure
        return super().read(size)

    def close(self) -> None:
        self.closes += 1
        self.threads.append(threading.get_ident())
        if self.close_gate is not None:
            self.close_gate.wait()
        super().close()
        if self.close_failure is not None:
            raise self.close_failure


class _OpenedFile(io.BufferedReader):
    def __init__(self, path: Path, *, failure: BaseException | None, close_failure: BaseException | None) -> None:
        super().__init__(io.FileIO(path, "r"))
        self.failure = failure
        self.close_failure = close_failure
        self.closes = 0
        self.threads = [threading.get_ident()]

    def fileno(self) -> int:
        if self.failure is not None:
            raise self.failure
        return super().fileno()

    def close(self) -> None:
        self.closes += 1
        self.threads.append(threading.get_ident())
        super().close()
        if self.close_failure is not None:
            raise self.close_failure


class _Body(Protocol):
    def aiter_bytes(self) -> AsyncIterator[bytes]: ...


class _Request(Protocol):
    @property
    def body(self) -> _Body | None: ...


class _Event(Protocol):
    @property
    def name(self) -> str: ...


class _CloseBeforeRead:
    def __init__(self, close: Callable[[], None]) -> None:
        self.close = close

    async def on_event(self, event: _Event) -> None:
        if event.name == "attempt_start":
            self.close()


class _Stopped(BaseException):
    pass


class _UnsentAdapter:
    def __init__(self, transports: ModuleType, *, reading: bool = False) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.sends = 0
        self.reading = reading

    async def send(self, request: _Request, context: object) -> object:
        del context
        self.sends += 1
        if self.reading and request.body is not None:
            async for _chunk in request.body.aiter_bytes():
                pass
        msg = "cancelled file reached the transport"
        raise RuntimeError(msg)

    async def aclose(self) -> None:
        pass


async def _preparation(
    package: ModuleType,
    lines: list[str],
    label: str,
    *,
    owned: bool = True,
    failure: BaseException | None = None,
    close_failure: BaseException | None = None,
    repeat: bool = False,
    close_adapter: bool = False,
    deadline: bool = False,
    token_cancel: bool = False,
    pause_close: bool = False,
    reading: bool = False,
) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    gate = _DiskGate()
    closing = _DiskGate() if pause_close else None
    file = _BlockedFile(gate, failure=failure, close_failure=close_failure, close_gate=closing, reading=reading)
    body = bodies.AsyncFileBody(file, ownership="owned" if owned else "borrowed")
    adapter = _UnsentAdapter(transports, reading=reading)
    token = options.CancelToken() if token_cancel else None
    api = package.AsyncClient(
        transport_adapter=adapter,
        options=options.ClientOptions(
            total_timeout=0.1 if deadline else None, cancel_token=token, cleanup_timeout=0.01
        ),
    )
    failures: list[BaseException] = []

    async def request() -> None:
        try:
            await api.request_raw("POST", "https://files.example.com/", body=body)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    loop = asyncio.get_running_loop()
    submitted: list[asyncio.Task[object]] = []
    original = loop.run_in_executor

    def submit(
        executor: Executor | None, function: Callable[..., object], *arguments: object
    ) -> asyncio.Future[object]:
        if (task := asyncio.current_task()) is not None:
            submitted.append(task)
        return original(executor, function, *arguments)

    with patch.object(loop, "run_in_executor", submit):
        caller = asyncio.create_task(request())
        try:
            await asyncio.wait_for(gate.started.wait(), 5)
            if token is not None:
                token.cancel()
            elif not deadline:
                caller.cancel("file caller cancelled")
            await asyncio.wait_for(caller, 5)
            record(lines, f"{label} interruption", lambda: type(failures[0]).__name__)
            record(lines, f"{label} before release", lambda: (file.closes, adapter.sends))
            async with package.AsyncClient(transport_adapter=adapter) as competing:
                await arecord(
                    lines,
                    f"{label} claim held",
                    lambda: competing.request_raw("POST", "https://files.example.com/", body=body),
                )
            if repeat:
                submitted[0].cancel("repeated owned cancellation")
                await asyncio.sleep(0)
                submitted[0].cancel("third owned cancellation")
                await asyncio.sleep(0)
            if close_adapter:
                body.close()
            await arecord(lines, f"{label} retained", api.aclose)
            gate.proceed.set()
            await asyncio.wait_for(gate.finished.wait(), 5)
            if closing is not None:
                await asyncio.wait_for(closing.started.wait(), 5)
                await arecord(lines, f"{label} closing retained", api.aclose)
                closing.proceed.set()
            await api.aclose()
            record(
                lines,
                f"{label} released",
                lambda: (file.closed, file.closes, adapter.sends, len(set(file.threads)) == 1),
            )
            record(
                lines,
                f"{label} diagnostics",
                lambda: (
                    failures[0].args if isinstance(failures[0], asyncio.CancelledError) else (),
                    tuple(type(item).__name__ for item in getattr(failures[0], "secondary_errors", ())),
                    tuple(getattr(failures[0], "__notes__", ())),
                    getattr(failures[0], "network_send_count", None),
                    all(
                        item is not failures[0] and getattr(item, "cause", None) is not failures[0]
                        for item in getattr(failures[0], "secondary_errors", ())
                    ),
                    tuple(
                        (item is failure, item is close_failure)
                        for item in getattr(failures[0], "secondary_errors", ())
                        if item is failure or item is close_failure
                    ),
                ),
            )
            if owned:
                async with package.AsyncClient(transport_adapter=adapter) as another:
                    await arecord(
                        lines,
                        f"{label} owned input consumed",
                        lambda: another.request_raw("POST", "https://files.example.com/", body=body),
                    )
                record(lines, f"{label} owned close once", lambda: file.closes)
            if close_adapter and not owned:
                async with package.AsyncClient(transport_adapter=adapter) as another:
                    await arecord(
                        lines,
                        f"{label} later attempt refused",
                        lambda: another.request_raw("POST", "https://files.example.com/", body=body),
                    )
        finally:
            gate.proceed.set()
            if closing is not None:
                closing.proceed.set()
            await asyncio.wait_for(caller, 5)
            body.close()
            if not file.closed:
                file.close()


async def _path(
    package: ModuleType,
    lines: list[str],
    label: str,
    *,
    failure: BaseException | None = None,
    close_failure: BaseException | None = None,
    close_adapter: bool = False,
    deadline: bool = False,
) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    gate = _DiskGate()
    adapter = _UnsentAdapter(transports)
    opened: list[_OpenedFile] = []
    failures: list[BaseException] = []
    api = package.AsyncClient(
        transport_adapter=adapter,
        options=options.ClientOptions(total_timeout=0.1 if deadline else None, cleanup_timeout=0.01),
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "upload.bin"
        path.write_bytes(b"upload")
        body = bodies.AsyncFileBody.from_path(path)

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            file = _OpenedFile(path, failure=failure, close_failure=close_failure)
            opened.append(file)
            gate.wait()
            return file

        async def request() -> None:
            try:
                await api.request_raw("POST", "https://files.example.com/", body=body)
            except BaseException as error:  # noqa: BLE001
                failures.append(error)

        with patch.object(Path, "open", open_file):
            caller = asyncio.create_task(request())
            try:
                await asyncio.wait_for(gate.started.wait(), 5)
                if not deadline:
                    caller.cancel("path caller cancelled")
                await asyncio.wait_for(caller, 5)
                if close_adapter:
                    body.close()
                record(lines, f"{label} before release", lambda: (opened[0].closes, adapter.sends))
                await arecord(lines, f"{label} retained", api.aclose)
                gate.proceed.set()
                await asyncio.wait_for(gate.finished.wait(), 5)
                await api.aclose()
                record(
                    lines,
                    f"{label} released",
                    lambda: (opened[0].closed, opened[0].closes, adapter.sends, len(set(opened[0].threads)) == 1),
                )
                record(
                    lines,
                    f"{label} diagnostics",
                    lambda: (
                        type(failures[0]).__name__,
                        failures[0].args if isinstance(failures[0], asyncio.CancelledError) else (),
                        tuple(
                            (type(item).__name__, tuple(getattr(item, "__notes__", ())))
                            for item in getattr(failures[0], "secondary_errors", ())
                        ),
                        tuple(getattr(failures[0], "__notes__", ())),
                        all(
                            item is not failures[0] and getattr(item, "cause", None) is not failures[0]
                            for item in getattr(failures[0], "secondary_errors", ())
                        ),
                        tuple(
                            (item is failure, item is close_failure)
                            for item in getattr(failures[0], "secondary_errors", ())
                            if item is failure or item is close_failure
                        ),
                    ),
                )
                if close_adapter:
                    async with package.AsyncClient(transport_adapter=adapter) as another:
                        await arecord(
                            lines,
                            f"{label} later attempt refused",
                            lambda: another.request_raw("POST", "https://files.example.com/", body=body),
                        )
            finally:
                gate.proceed.set()
                await asyncio.wait_for(caller, 5)
                body.close()
                for file in opened:
                    if not file.closed:
                        file.close()


async def _preparation_failure(
    package: ModuleType,
    lines: list[str],
    failure: BaseException | None,
    *,
    close_failure: BaseException | None = None,
    closed_before_read: bool = False,
) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    gate = _DiskGate()
    gate.proceed.set()
    file = _BlockedFile(gate, failure=failure, close_failure=close_failure)
    body = bodies.AsyncFileBody(file, ownership="owned")
    adapter = _UnsentAdapter(transports, reading=closed_before_read)
    hooks = (_CloseBeforeRead(body.close),) if closed_before_read else ()
    api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(hooks=hooks))
    label = "closed before read" if closed_before_read else f"preparation {type(failure).__name__}"
    try:
        await api.request_raw("POST", "https://files.example.com/", body=body)
    except BaseException as error:  # noqa: BLE001
        record(
            lines,
            label,
            lambda error=error: (
                type(error).__name__,
                error is failure,
                getattr(error, "cause", None) is failure,
                type(getattr(error, "cause", None)).__name__,
                tuple(getattr(failure, "__notes__", ())),
                file.closed,
                file.closes,
                adapter.sends,
                len(set(file.threads)) == 1,
            ),
        )
    finally:
        await api.aclose()
        async with package.AsyncClient(transport_adapter=adapter) as another:
            await arecord(
                lines,
                f"{label} owned input consumed",
                lambda: another.request_raw("POST", "https://files.example.com/", body=body),
            )
        record(lines, f"{label} owned close once", lambda: file.closes)
        body.close()


async def _successful_owned(package: ModuleType, lines: list[str]) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    gate = _DiskGate()
    gate.proceed.set()
    file = _BlockedFile(gate)
    body = bodies.AsyncFileBody(file, ownership="owned")
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        await api.request_raw("POST", "https://files.example.com/", body=body)
        record(lines, "successful owned close", lambda: (file.closed, file.closes))
        await arecord(
            lines,
            "successful owned input consumed",
            lambda: api.request_raw("POST", "https://files.example.com/", body=body),
        )
        record(lines, "successful owned close once", lambda: file.closes)
    body.close()


async def _files(package: ModuleType, lines: list[str]) -> None:
    await _successful_owned(package, lines)
    await _preparation(package, lines, "owned preparation")
    await _preparation(package, lines, "borrowed preparation", owned=False)
    await _preparation(package, lines, "closed borrowed adapter", owned=False, close_adapter=True)
    await _preparation(package, lines, "repeated cancellation", repeat=True, close_adapter=True)
    await _preparation(package, lines, "deadline preparation", deadline=True)
    await _preparation(package, lines, "token preparation", token_cancel=True)
    await _preparation(package, lines, "late disk failure", failure=OSError("disk failed after cancellation"))
    await _preparation(package, lines, "late close failure", close_failure=OSError("close failed after cancellation"))
    await _preparation(package, lines, "retained close", pause_close=True, close_adapter=True)
    await _preparation(
        package,
        lines,
        "deadline close failure",
        deadline=True,
        close_failure=OSError("deadline close failed"),
    )
    await _preparation(package, lines, "token disk failure", token_cancel=True, failure=OSError("token disk failed"))
    await _preparation(package, lines, "deadline disk failure", deadline=True, failure=OSError("deadline disk failed"))
    await _preparation(package, lines, "owned read", reading=True, repeat=True, close_adapter=True)
    await _preparation(package, lines, "borrowed read", reading=True, owned=False)
    await _preparation(package, lines, "late read failure", reading=True, failure=OSError("read failed"))
    await _path(package, lines, "late path open")
    await _path(package, lines, "closed adapter late open", close_adapter=True)
    await _path(package, lines, "late path stat failure", failure=OSError("stat failed after cancellation"))
    await _path(
        package, lines, "late path close failure", close_failure=OSError("path close failed after cancellation")
    )
    await _path(
        package,
        lines,
        "late path stat and close failures",
        failure=OSError("stat failed after cancellation"),
        close_failure=OSError("close failed after cancellation"),
    )
    repeated = OSError("stat and close share one failure")
    await _path(package, lines, "same path stat and close failure", failure=repeated, close_failure=repeated)
    await _path(
        package,
        lines,
        "deadline path stat and close failures",
        failure=OSError("deadline stat failed"),
        close_failure=OSError("deadline close failed"),
        deadline=True,
    )
    await _preparation_failure(package, lines, OSError("preparation failed"))
    await _preparation_failure(
        package, lines, OSError("preparation and close failed"), close_failure=OSError("owned close failed")
    )
    await _preparation_failure(package, lines, asyncio.CancelledError("file native interruption"))
    await _preparation_failure(package, lines, _Stopped("file stopped"))
    await _preparation_failure(package, lines, None, closed_before_read=True)


def deadline_files(package: ModuleType, lines: list[str]) -> None:
    """Exercise cancelled file preparation and late path-open ownership through generated public calls."""
    run(lambda: _files(package, lines))
