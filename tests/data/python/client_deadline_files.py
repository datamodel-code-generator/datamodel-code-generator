"""Report generated-client ownership when an asynchronous file operation outlives cancellation."""

from __future__ import annotations

import asyncio
import importlib
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Protocol
from unittest.mock import patch

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from concurrent.futures import Future
    from types import ModuleType


class _DiskGate:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.proceed = threading.Event()
        self.thread: int | None = None

    def wait(self) -> None:
        self.thread = threading.get_ident()
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            if not self.proceed.wait(5):
                msg = "file fixture was not released"
                raise TimeoutError(msg)
        finally:
            self.loop.call_soon_threadsafe(self.finished.set)


async def _close_released(api: Any, errors: ModuleType) -> None:
    """Close a client whose retained file work was released, waiting while that work still returns to the loop."""
    for _ in range(500):
        try:
            await api.aclose()
        except errors.CleanupError:
            await asyncio.sleep(0.01)
        else:
            return
    await api.aclose()


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


class _SettlingAdapter(_UnsentAdapter):
    """Read the body, and once cancelled let the interrupted disk work finish before the cancellation goes on."""

    def __init__(self, transports: ModuleType, gate: _DiskGate) -> None:
        super().__init__(transports, reading=True)
        self.gate = gate

    async def send(self, request: _Request, context: object) -> object:
        try:
            return await super().send(request, context)
        except asyncio.CancelledError:
            self.gate.proceed.set()
            await asyncio.wait_for(self.gate.finished.wait(), 5)
            await asyncio.sleep(0.01)
            raise


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
    errors = importlib.import_module(f"{package.__name__}.errors")
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

    submitted: list[asyncio.Task[object]] = []
    original = ThreadPoolExecutor.submit

    def submit(executor: ThreadPoolExecutor, function: Callable[..., object], *arguments: object) -> Future[object]:
        if (task := asyncio.current_task()) is not None:
            submitted.append(task)
        return original(executor, function, *arguments)

    with patch.object(ThreadPoolExecutor, "submit", submit):
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
            await _close_released(api, errors)
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
    errors = importlib.import_module(f"{package.__name__}.errors")
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
                await _close_released(api, errors)
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
                tuple((type(item).__name__, item is close_failure) for item in getattr(error, "secondary_errors", ())),
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


async def _native_close(package: ModuleType, lines: list[str]) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    gate = _DiskGate()
    gate.proceed.set()
    failure = asyncio.CancelledError("file close interruption")
    file = _BlockedFile(gate, close_failure=failure)
    body = bodies.AsyncFileBody(file, ownership="owned")
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, b"ok", "text/plain"))
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        try:
            await api.request_raw("POST", "https://files.example.com/", body=body)
        except asyncio.CancelledError as error:
            record(
                lines,
                "native close failure",
                lambda error=error: (
                    error is failure,
                    error.args,
                    tuple(getattr(error, "__notes__", ())),
                    file.closed,
                    file.closes,
                    len(set(file.threads)) == 1,
                ),
            )
        await arecord(
            lines,
            "native close input consumed",
            lambda: api.request_raw("POST", "https://files.example.com/", body=body),
        )
        record(lines, "native close runs once", lambda: file.closes)
    body.close()


async def _queued(  # noqa: PLR0914
    package: ModuleType,
    lines: list[str],
    label: str,
    *,
    path_body: bool = False,
    reading: bool = False,
    owned: bool = True,
    cancel_close: bool = False,
) -> None:
    """Cancel queued disk work; an existing file snapshots at entry before its per-hop rewind and read."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    errors = importlib.import_module(f"{package.__name__}.errors")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    blocker, ready = _DiskGate(), _DiskGate()
    ready.proceed.set()
    file = _BlockedFile(ready, reading=reading)
    adapter = _UnsentAdapter(transports, reading=reading)
    cleanup_timeout = 0.01 if owned and not path_body else 1.0
    api = package.AsyncClient(
        transport_adapter=adapter, options=options.ClientOptions(total_timeout=None, cleanup_timeout=cleanup_timeout)
    )
    blocked_index = (2 if path_body else 3) if reading else 1
    submitted: list[tuple[Future[object], asyncio.Task[object]]] = []
    signals = [asyncio.Event() for _ in range(4)]
    blockers: list[Future[None]] = []
    failures: list[BaseException] = []
    opened: list[_OpenedFile] = []
    original = ThreadPoolExecutor.submit

    def submit(executor: ThreadPoolExecutor, function: Callable[..., object], *arguments: object) -> Future[object]:
        task = asyncio.current_task()
        if task is None:
            msg = "queued file fixture must submit from an asyncio task"
            raise RuntimeError(msg)
        if len(submitted) + 1 == blocked_index:
            blockers.append(original(executor, blocker.wait))
        future = original(executor, function, *arguments)
        submitted.append((future, task))
        signals[len(submitted) - 1].set()
        return future

    with TemporaryDirectory() as directory:
        path = Path(directory) / "queued.bin"
        path.write_bytes(b"upload")
        body = (
            bodies.AsyncFileBody.from_path(path)
            if path_body
            else bodies.AsyncFileBody(file, ownership="owned" if owned else "borrowed")
        )

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            value = _OpenedFile(path, failure=None, close_failure=None)
            opened.append(value)
            return value

        async def request() -> None:
            try:
                await api.request_raw("POST", "https://files.example.com/", body=body)
            except BaseException as error:  # noqa: BLE001
                failures.append(error)

        with patch.object(ThreadPoolExecutor, "submit", submit), patch.object(Path, "open", open_file):
            caller = asyncio.create_task(request())
            try:
                await asyncio.wait_for(signals[blocked_index - 1].wait(), 5)
                await asyncio.wait_for(blocker.started.wait(), 5)
                caller.cancel("queued file cancellation")
                await asyncio.wait_for(caller, 5)
                if cancel_close:
                    await asyncio.wait_for(signals[1].wait(), 5)
                    submitted[1][1].cancel("queued close cancellation")
                    await asyncio.wait_for(signals[2].wait(), 5)
                    submitted[2][1].cancel("replacement close cancellation")
                    await asyncio.sleep(0)
                    record(lines, f"{label} replacement retained", lambda: not submitted[2][0].cancelled())
                body.close()
                record(
                    lines,
                    f"{label} before release",
                    lambda: (submitted[blocked_index - 1][0].cancelled(), file.blocked, file.closes, len(opened)),
                )
                await arecord(lines, f"{label} retained", api.aclose)
                blocker.proceed.set()
                await asyncio.wrap_future(blockers[0])
                await _close_released(api, errors)
                record(
                    lines,
                    f"{label} released",
                    lambda: (
                        tuple(future.cancelled() for future, _ in submitted),
                        file.closed,
                        file.closes,
                        len(opened),
                        adapter.sends,
                        all(thread == blocker.thread for thread in file.threads),
                    ),
                )
                record(
                    lines,
                    f"{label} diagnostics",
                    lambda: (
                        type(failures[0]).__name__,
                        failures[0].args,
                        tuple(getattr(failures[0], "__notes__", ())),
                    ),
                )
            finally:
                blocker.proceed.set()
                await asyncio.wait_for(caller, 5)
                await api.aclose()
                body.close()
                if not file.closed:
                    file.close()


async def _completed_job(
    package: ModuleType,
    lines: list[str],
    label: str,
    *,
    path_body: bool = False,
    failure: BaseException | None = None,
) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    ready = _DiskGate()
    ready.proceed.set()
    file = _BlockedFile(ready, failure=failure)
    adapter = _UnsentAdapter(transports)
    submitted: list[Future[object]] = []
    opened: list[_OpenedFile] = []
    original = ThreadPoolExecutor.submit
    original_wrap = asyncio.wrap_future
    delivery_delayed = False

    def submit(executor: ThreadPoolExecutor, function: Callable[..., object], *arguments: object) -> Future[object]:
        future = original(executor, function, *arguments)
        if not submitted:
            future.exception(timeout=5)
        submitted.append(future)
        return future

    def wrap_future(future: Future[object], *, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Future[object]:
        """Suspend initial delivery even when Python wraps the completed job without yielding."""
        nonlocal delivery_delayed
        if submitted and future is submitted[0] and not delivery_delayed:
            task = asyncio.current_task()
            if task is None:
                msg = "completed file fixture must wrap from an asyncio task"
                raise RuntimeError(msg)
            delivery_delayed = True
            loop = asyncio.get_running_loop() if loop is None else loop
            delivery = loop.create_future()
            loop.call_soon(task.cancel, "completed file cancellation")
            return delivery
        return original_wrap(future, loop=loop)

    with TemporaryDirectory() as directory:
        path = Path(directory) / "completed.bin"
        path.write_bytes(b"upload")
        body = bodies.AsyncFileBody.from_path(path) if path_body else bodies.AsyncFileBody(file, ownership="owned")

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            value = _OpenedFile(path, failure=failure, close_failure=None)
            opened.append(value)
            return value

        async with package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=None)
        ) as api:
            with (
                patch.object(ThreadPoolExecutor, "submit", submit),
                patch.object(Path, "open", open_file),
                patch.object(asyncio, "wrap_future", wrap_future),
            ):
                try:
                    await api.request_raw("POST", "https://files.example.com/", body=body)
                except BaseException as error:  # noqa: BLE001
                    record(
                        lines,
                        f"{label} diagnostics",
                        lambda error=error: (type(error).__name__, error.args, tuple(getattr(error, "__notes__", ()))),
                    )
                value = opened[0] if path_body else file
                record(
                    lines,
                    f"{label} resources",
                    lambda: (
                        tuple(future.cancelled() for future in submitted),
                        value.closed,
                        value.closes,
                        adapter.sends,
                    ),
                )
        body.close()
        if not file.closed:
            file.close()


async def _shared_path(
    package: ModuleType,
    lines: list[str],
    label: str,
    failures: tuple[BaseException | None, BaseException | None] = (None, None),
) -> None:
    """Cancel two calls on one path body after both opens ran; each settles its own late open and failure."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    adapter = _UnsentAdapter(transports)
    submitted: list[Future[object]] = []
    delayed: list[Future[object]] = []
    opened: list[_OpenedFile] = []
    errors: list[BaseException | None] = [None, None]
    original = ThreadPoolExecutor.submit
    original_wrap = asyncio.wrap_future

    def submit(executor: ThreadPoolExecutor, function: Callable[..., object], *arguments: object) -> Future[object]:
        future = original(executor, function, *arguments)
        if len(submitted) < len(failures):
            future.exception(timeout=5)
        submitted.append(future)
        return future

    def wrap_future(future: Future[object], *, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Future[object]:
        """Cancel each call before its completed open is delivered."""
        if future in submitted[: len(failures)] and future not in delayed:
            task = asyncio.current_task()
            if task is None:
                msg = "shared path fixture must wrap from an asyncio task"
                raise RuntimeError(msg)
            delayed.append(future)
            loop = asyncio.get_running_loop() if loop is None else loop
            loop.call_soon(task.cancel, f"shared path cancellation {len(delayed)}")
            return loop.create_future()
        return original_wrap(future, loop=loop)

    with TemporaryDirectory() as directory:
        path = Path(directory) / "shared.bin"
        path.write_bytes(b"upload")
        body = bodies.AsyncFileBody.from_path(path)

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            file = _OpenedFile(path, failure=failures[len(opened)], close_failure=None)
            opened.append(file)
            return file

        api = package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=None, cleanup_timeout=1.0)
        )

        async def request(index: int) -> None:
            try:
                await api.request_raw("POST", "https://files.example.com/", body=body)
            except BaseException as error:  # noqa: BLE001
                errors[index] = error

        with (
            patch.object(ThreadPoolExecutor, "submit", submit),
            patch.object(Path, "open", open_file),
            patch.object(asyncio, "wrap_future", wrap_future),
        ):
            await asyncio.wait_for(asyncio.gather(request(0), request(1)), 5)
            await arecord(lines, f"{label} close", api.aclose)
        record(lines, f"{label} files", lambda: (tuple((file.closed, file.closes) for file in opened), adapter.sends))
        record(
            lines,
            f"{label} diagnostics",
            lambda: tuple(
                (type(error).__name__, error.args, tuple(getattr(error, "__notes__", ()))) for error in errors
            ),
        )
        body.close()


async def _settled_read(package: ModuleType, lines: list[str]) -> None:
    """Report a late read failure that settles before the cancelled call releases its file."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    gate = _DiskGate()
    file = _BlockedFile(gate, failure=OSError("read failed after cancellation"), reading=True)
    body = bodies.AsyncFileBody(file, ownership="owned")
    adapter = _SettlingAdapter(transports, gate)
    failures: list[BaseException] = []
    api = package.AsyncClient(
        transport_adapter=adapter, options=options.ClientOptions(total_timeout=None, cleanup_timeout=1.0)
    )

    async def request() -> None:
        try:
            await api.request_raw("POST", "https://files.example.com/", body=body)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    caller = asyncio.create_task(request())
    try:
        await asyncio.wait_for(gate.started.wait(), 5)
        caller.cancel("settled read cancellation")
        await asyncio.wait_for(caller, 5)
        await api.aclose()
        record(
            lines,
            "settled read failure",
            lambda: (
                type(failures[0]).__name__,
                failures[0].args,
                tuple(getattr(failures[0], "__notes__", ())),
                file.closed,
                file.closes,
                adapter.sends,
            ),
        )
    finally:
        gate.proceed.set()
        body.close()


async def _multipart_close(package: ModuleType, lines: list[str]) -> None:
    """Close a multipart call's begun file part although its deadline ends the wait for another call's slow open."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    loop = asyncio.get_running_loop()
    gate, first_open, failing = _DiskGate(), asyncio.Event(), asyncio.Event()
    adapter = _UnsentAdapter(transports)
    opened: list[_OpenedFile] = []
    outcomes: dict[str, str] = {}

    async def unavailable(_context: object) -> object:
        await failing.wait()
        msg = "second part unavailable"
        raise RuntimeError(msg)

    async def outcome(name: str, call: Awaitable[object]) -> None:
        try:
            await call
        except BaseException as error:  # noqa: BLE001
            outcomes[name] = type(error).__name__

    with TemporaryDirectory() as directory:
        path = Path(directory) / "shared.bin"
        path.write_bytes(b"upload")
        shared = bodies.AsyncFileBody.from_path(path)

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            file = _OpenedFile(path, failure=None, close_failure=None)
            opened.append(file)
            if len(opened) == 1:
                loop.call_soon_threadsafe(first_open.set)
            else:
                gate.wait()
            return file

        multipart_api = package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=0.05, cleanup_timeout=2.0)
        )
        plain_api = package.AsyncClient(
            transport_adapter=adapter, options=options.ClientOptions(total_timeout=None, cleanup_timeout=2.0)
        )
        body = bodies.AsyncMultipartBody((
            bodies.FilePart("first", shared, filename="first.bin"),
            bodies.FilePart("second", bodies.AsyncBodyFactory(unavailable), filename="second.bin"),
        ))
        with patch.object(Path, "open", open_file):
            multipart = asyncio.create_task(
                outcome("multipart", multipart_api.request_raw("POST", "https://files.example.com/", body=body))
            )
            try:
                await asyncio.wait_for(first_open.wait(), 5)
                plain = asyncio.create_task(
                    outcome("plain", plain_api.request_raw("POST", "https://files.example.com/", body=shared))
                )
                await asyncio.wait_for(gate.started.wait(), 5)
                plain.cancel("slow open cancelled")
                failing.set()
                await asyncio.sleep(0.15)
                gate.proceed.set()
                await asyncio.wait_for(asyncio.gather(multipart, plain), 5)
                await multipart_api.aclose()
                await plain_api.aclose()
                record(
                    lines,
                    "multipart part closed after its deadline",
                    lambda: (
                        outcomes["multipart"],
                        outcomes["plain"],
                        tuple((file.closed, file.closes) for file in opened),
                        adapter.sends,
                    ),
                )
            finally:
                gate.proceed.set()
                shared.close()


async def _direct_cancellation(package: ModuleType, lines: list[str]) -> None:
    """Cancel a direct attempt while its file is measured; its claim and owned close settle once the disk work ends."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    errors = importlib.import_module(f"{package.__name__}.errors")
    context = bodies.BodyAttemptContext(call_id="direct", attempt_index=0, hop_index=0, remaining_timeout=None)
    gate = _DiskGate()
    file = _BlockedFile(gate)
    body = bodies.AsyncFileBody(file, ownership="owned")
    attempt = asyncio.create_task(body(context))

    async def settled() -> object:
        while True:
            try:
                return await body(context)
            except errors.BodyNotReplayableError as error:
                if error.condition != "concurrent":
                    raise
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(gate.started.wait(), 5)
        attempt.cancel("direct attempt cancelled")
        try:
            await attempt
        except asyncio.CancelledError as error:
            record(lines, "direct attempt cancellation", lambda: (type(error).__name__, error.args, file.closes))
        gate.proceed.set()
        await arecord(lines, "direct attempt after release", lambda: asyncio.wait_for(settled(), 5))
        record(lines, "direct attempt released", lambda: (file.closed, file.closes, len(set(file.threads)) == 1))
    finally:
        gate.proceed.set()
        body.close()


async def _direct_close_cancellation(package: ModuleType, lines: list[str]) -> None:
    """Cancel a direct attempt's close while it waits for another attempt's slow open; the close still happens."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    context = bodies.BodyAttemptContext(call_id="direct", attempt_index=0, hop_index=0, remaining_timeout=None)
    gate = _DiskGate()
    opened: list[_OpenedFile] = []
    outcomes: dict[str, str] = {}

    async def outcome(name: str, call: Awaitable[object]) -> None:
        try:
            await call
        except BaseException as error:  # noqa: BLE001
            outcomes[name] = type(error).__name__

    with TemporaryDirectory() as directory:
        path = Path(directory) / "shared.bin"
        path.write_bytes(b"upload")
        body = bodies.AsyncFileBody.from_path(path)

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            file = _OpenedFile(path, failure=None, close_failure=None)
            opened.append(file)
            if len(opened) == 2:
                gate.wait()
            return file

        with patch.object(Path, "open", open_file):
            try:
                attempt = await body(context)
                slow = asyncio.create_task(outcome("slow open", body(context)))
                await asyncio.wait_for(gate.started.wait(), 5)
                slow.cancel("slow open cancelled")
                closer = asyncio.create_task(outcome("close", attempt.aclose()))
                await asyncio.sleep(0)
                closer.cancel("close cancelled")
                await asyncio.wait_for(asyncio.gather(slow, closer), 5)
                gate.proceed.set()
                while not all(file.closed for file in opened):  # noqa: ASYNC110
                    await asyncio.sleep(0)
                record(
                    lines,
                    "direct close cancelled while another open settles",
                    lambda: (
                        outcomes["close"],
                        outcomes["slow open"],
                        tuple((file.closed, file.closes) for file in opened),
                    ),
                )
            finally:
                gate.proceed.set()
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
    await _queued(package, lines, "queued path open", path_body=True)
    await _queued(package, lines, "queued owned preparation")
    await _queued(package, lines, "queued borrowed preparation", owned=False)
    await _queued(package, lines, "queued owned read", reading=True)
    await _queued(package, lines, "queued borrowed read", reading=True, owned=False)
    await _queued(package, lines, "queued owned close", cancel_close=True)
    await _completed_job(package, lines, "completed file preparation")
    await _completed_job(package, lines, "completed path open", path_body=True)
    await _completed_job(package, lines, "completed file failure", failure=OSError("completed disk failure"))
    await _completed_job(
        package, lines, "completed path failure", path_body=True, failure=OSError("completed stat failure")
    )
    await _settled_read(package, lines)
    await _multipart_close(package, lines)
    await _shared_path(package, lines, "shared path late opens")
    await _shared_path(
        package,
        lines,
        "shared path late failures",
        (FileNotFoundError("first open failed late"), PermissionError("second open failed late")),
    )
    await _direct_cancellation(package, lines)
    await _direct_close_cancellation(package, lines)
    await _native_close(package, lines)


def deadline_files(package: ModuleType, lines: list[str]) -> None:
    """Exercise cancelled file preparation and late path-open ownership through generated public calls."""
    run(lambda: _files(package, lines))
