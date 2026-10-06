"""Report how generated clients finish file work before a cancellation or an expired deadline releases the file."""

from __future__ import annotations

import asyncio
import importlib
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Protocol
from unittest.mock import patch

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from concurrent.futures import Future
    from types import ModuleType


class _DiskGate:
    """Hold a disk operation in its thread until released, marking while it runs."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.proceed = threading.Event()
        self.running = False

    def wait(self) -> None:
        self.running = True
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            if not self.proceed.wait(5):
                msg = "file fixture was not released"
                raise TimeoutError(msg)
        finally:
            self.running = False


class _BlockedFile(io.BytesIO):
    """A file whose first size probe, or first read, waits on a gate, and which notes a close during that wait."""

    def __init__(
        self,
        gate: _DiskGate,
        *,
        failure: BaseException | None = None,
        close_failure: BaseException | None = None,
        reading: bool = False,
    ) -> None:
        super().__init__(b"upload")
        self.gate = gate
        self.failure = failure
        self.close_failure = close_failure
        self.reading = reading
        self.blocked = False
        self.closes = 0
        self.overlapped = False

    def _block(self) -> None:
        self.blocked = True
        self.gate.wait()
        if self.failure is not None:
            raise self.failure

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if whence == os.SEEK_END and not self.reading and not self.blocked:
            self._block()
        return super().seek(offset, whence)

    def read(self, size: int | None = -1, /) -> bytes:
        if self.reading and not self.blocked:
            self._block()
        return super().read(size)

    def close(self) -> None:
        self.closes += 1
        self.overlapped = self.overlapped or self.gate.running
        super().close()
        if self.close_failure is not None:
            raise self.close_failure


class _OpenedFile(io.BufferedReader):
    def __init__(self, path: Path, *, failure: BaseException | None, close_failure: BaseException | None) -> None:
        super().__init__(io.FileIO(path, "r"))
        self.failure = failure
        self.close_failure = close_failure
        self.closes = 0

    def fileno(self) -> int:
        if self.failure is not None:
            raise self.failure
        return super().fileno()

    def close(self) -> None:
        self.closes += 1
        super().close()
        if self.close_failure is not None:
            raise self.close_failure


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


class _Unsent(httpx2.AsyncBaseTransport):
    """Count sends, reading the request body first when asked, and fail any request that reaches it."""

    def __init__(self, *, reading: bool = False) -> None:
        self.sends = 0
        self.reading = reading

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.sends += 1
        if self.reading:
            await request.aread()
        msg = "cancelled file reached the transport"
        raise RuntimeError(msg)


def _client(package: ModuleType, transport: _Unsent, settings: object) -> Any:
    return package.AsyncClient(http_client=httpx2.AsyncClient(transport=transport), options=settings)


def _settings(options: ModuleType, *, deadline: bool, now: list[float]) -> object:
    """Return options whose deadline, when asked for, expires once the fake clock moves past it."""
    return options.ClientOptions(total_timeout=10 if deadline else None, clock=options.Clock(monotonic=lambda: now[0]))


def _secondary(error: BaseException, *failures: BaseException | None) -> tuple[object, ...]:
    """Report a failure's arguments, secondary errors, and notes, and which secondary errors are the given ones."""
    secondary = getattr(error, "secondary_errors", ())
    return (
        error.args if isinstance(error, asyncio.CancelledError) else (),
        tuple(type(item).__name__ for item in secondary),
        tuple(getattr(error, "__notes__", ())),
        getattr(error, "attempt_count", None),
        all(item is not error and getattr(item, "cause", None) is not error for item in secondary),
        tuple(
            tuple(item is failure for failure in failures)
            for item in secondary
            if any(item is failure for failure in failures)
        ),
    )


async def _preparation(  # noqa: PLR0913
    package: ModuleType,
    lines: list[str],
    label: str,
    *,
    owned: bool = True,
    failure: BaseException | None = None,
    close_failure: BaseException | None = None,
    repeat: bool = False,
    close_body: bool = False,
    deadline: bool = False,
    reading: bool = False,
) -> None:
    """Stop a call while its file is probed or read; the call waits for that work before releasing the file."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    gate = _DiskGate()
    file = _BlockedFile(gate, failure=failure, close_failure=close_failure, reading=reading)
    body = bodies.AsyncFileBody(file, ownership="owned" if owned else "borrowed")
    transport = _Unsent(reading=reading)
    now = [0.0]
    api = _client(package, transport, _settings(options, deadline=deadline, now=now))
    failures: list[BaseException] = []

    async def request() -> None:
        try:
            await api.request_raw("POST", "https://files.example.com/", body=body)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)

    caller = asyncio.create_task(request())
    try:
        await asyncio.wait_for(gate.started.wait(), 5)
        if deadline:
            now[0] = 20.0
        else:
            caller.cancel("file caller cancelled")
        competing = _client(package, transport, options.ClientOptions())
        await arecord(
            lines,
            f"{label} claim held",
            lambda: competing.request_raw("POST", "https://files.example.com/", body=body),
        )
        if repeat:
            caller.cancel("repeated cancellation")
            await asyncio.sleep(0)
            caller.cancel("third cancellation")
            await asyncio.sleep(0)
        if close_body:
            body.close()
        record(lines, f"{label} before release", lambda: (file.closes, transport.sends, caller.done()))
        gate.proceed.set()
        await asyncio.wait_for(caller, 5)
        await api.aclose()
        record(lines, f"{label} interruption", lambda: type(failures[0]).__name__)
        record(lines, f"{label} released", lambda: (file.closed, file.closes, transport.sends, file.overlapped))
        record(lines, f"{label} diagnostics", lambda: _secondary(failures[0], failure, close_failure))
        if owned:
            await arecord(
                lines,
                f"{label} owned input consumed",
                lambda: competing.request_raw("POST", "https://files.example.com/", body=body),
            )
            record(lines, f"{label} owned close once", lambda: file.closes)
        elif close_body:
            await arecord(
                lines,
                f"{label} later attempt refused",
                lambda: competing.request_raw("POST", "https://files.example.com/", body=body),
            )
    finally:
        gate.proceed.set()
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
    close_body: bool = False,
    deadline: bool = False,
) -> None:
    """Stop a call while its path opens; the call waits for the open, then closes the file it no longer sends."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    gate = _DiskGate()
    transport = _Unsent()
    opened: list[_OpenedFile] = []
    failures: list[BaseException] = []
    now = [0.0]
    api = _client(package, transport, _settings(options, deadline=deadline, now=now))
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
                if deadline:
                    now[0] = 20.0
                else:
                    caller.cancel("path caller cancelled")
                if close_body:
                    body.close()
                record(lines, f"{label} before release", lambda: (opened[0].closes, transport.sends, caller.done()))
                gate.proceed.set()
                await asyncio.wait_for(caller, 5)
                await api.aclose()
                record(lines, f"{label} released", lambda: (opened[0].closed, opened[0].closes, transport.sends))
                record(
                    lines,
                    f"{label} diagnostics",
                    lambda: (type(failures[0]).__name__, *_secondary(failures[0], failure, close_failure)),
                )
                if close_body:
                    another = _client(package, transport, options.ClientOptions())
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
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    gate = _DiskGate()
    gate.proceed.set()
    file = _BlockedFile(gate, failure=failure, close_failure=close_failure)
    body = bodies.AsyncFileBody(file, ownership="owned")
    transport = _Unsent(reading=closed_before_read)
    hooks = (_CloseBeforeRead(body.close),) if closed_before_read else ()
    api = _client(package, transport, options.ClientOptions(hooks=hooks))
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
                transport.sends,
            ),
        )
    finally:
        await api.aclose()
        another = _client(package, transport, options.ClientOptions())
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
                ),
            )
        await arecord(
            lines,
            "native close input consumed",
            lambda: api.request_raw("POST", "https://files.example.com/", body=body),
        )
        record(lines, "native close runs once", lambda: file.closes)
    body.close()


@contextmanager
def _cancelled_on_completion(count: int, message: str) -> Iterator[list[Future[object]]]:
    """Finish each of the first disk jobs before handing it back, cancelling its task before its result arrives."""
    submitted: list[Future[object]] = []
    original = ThreadPoolExecutor.submit

    def submit(executor: ThreadPoolExecutor, function: Callable[..., object], *arguments: object) -> Future[object]:
        future = original(executor, function, *arguments)
        if len(submitted) < count and (task := asyncio.current_task()) is not None:
            future.exception(timeout=5)
            asyncio.get_running_loop().call_soon(task.cancel, f"{message} {len(submitted) + 1}")
        submitted.append(future)
        return future

    with patch.object(ThreadPoolExecutor, "submit", submit):
        yield submitted


async def _completed_job(
    package: ModuleType,
    lines: list[str],
    label: str,
    *,
    path_body: bool = False,
    failure: BaseException | None = None,
) -> None:
    """Cancel a call as its file work completes; a file the work still opened is closed before the call ends."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    ready = _DiskGate()
    ready.proceed.set()
    file = _BlockedFile(ready, failure=failure)
    transport = _Unsent()
    opened: list[_OpenedFile] = []
    with TemporaryDirectory() as directory:
        path = Path(directory) / "completed.bin"
        path.write_bytes(b"upload")
        body = bodies.AsyncFileBody.from_path(path) if path_body else bodies.AsyncFileBody(file, ownership="owned")

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            value = _OpenedFile(path, failure=failure, close_failure=None)
            opened.append(value)
            return value

        api = _client(package, transport, options.ClientOptions(total_timeout=None))
        with _cancelled_on_completion(1, label) as submitted, patch.object(Path, "open", open_file):
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
                lambda: (len(submitted), value.closed, value.closes, transport.sends),
            )
        await api.aclose()
        body.close()
        if not file.closed:
            file.close()


async def _shared_path(
    package: ModuleType,
    lines: list[str],
    label: str,
    failures: tuple[BaseException | None, BaseException | None] = (None, None),
) -> None:
    """Cancel two calls on one path body as their opens complete; each settles its own late open and failure."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    transport = _Unsent()
    opened: list[_OpenedFile] = []
    errors: list[BaseException | None] = [None, None]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "shared.bin"
        path.write_bytes(b"upload")
        body = bodies.AsyncFileBody.from_path(path)

        def open_file(_path: Path, _mode: str) -> _OpenedFile:
            file = _OpenedFile(path, failure=failures[len(opened)], close_failure=None)
            opened.append(file)
            return file

        api = _client(package, transport, options.ClientOptions(total_timeout=None))

        async def request(index: int) -> None:
            try:
                await api.request_raw("POST", "https://files.example.com/", body=body)
            except BaseException as error:  # noqa: BLE001
                errors[index] = error

        with _cancelled_on_completion(len(failures), "shared path cancellation"), patch.object(Path, "open", open_file):
            await asyncio.wait_for(asyncio.gather(request(0), request(1)), 5)
            await arecord(lines, f"{label} close", api.aclose)
        record(lines, f"{label} files", lambda: (tuple((file.closed, file.closes) for file in opened), transport.sends))
        record(
            lines,
            f"{label} diagnostics",
            lambda: tuple(
                (type(error).__name__, error.args, tuple(getattr(error, "__notes__", ()))) for error in errors
            ),
        )
        body.close()


async def _direct_cancellation(package: ModuleType, lines: list[str]) -> None:
    """Cancel a direct attempt while its file is measured; it waits for that work, then closes its owned file."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    context = bodies.BodyAttemptContext(call_id="direct", attempt_index=0, hop_index=0, remaining_timeout=None)
    gate = _DiskGate()
    file = _BlockedFile(gate)
    body = bodies.AsyncFileBody(file, ownership="owned")
    attempt = asyncio.create_task(body(context))
    try:
        await asyncio.wait_for(gate.started.wait(), 5)
        attempt.cancel("direct attempt cancelled")
        await arecord(lines, "direct attempt held", lambda: body(context))
        gate.proceed.set()
        try:
            await attempt
        except asyncio.CancelledError as error:
            record(lines, "direct attempt cancellation", lambda error=error: (type(error).__name__, error.args))
        record(lines, "direct attempt released", lambda: (file.closed, file.closes, file.overlapped))
        await arecord(lines, "direct attempt after release", lambda: body(context))
    finally:
        gate.proceed.set()
        body.close()


async def _direct_close_cancellation(package: ModuleType, lines: list[str]) -> None:
    """Cancel a direct attempt's close while another attempt's slow open runs; both files still close."""
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
                gate.proceed.set()
                await asyncio.wait_for(asyncio.gather(slow, closer), 5)
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
    await _preparation(package, lines, "closed borrowed body", owned=False, close_body=True)
    await _preparation(package, lines, "repeated cancellation", repeat=True, close_body=True)
    await _preparation(package, lines, "deadline preparation", deadline=True)
    await _preparation(package, lines, "late disk failure", failure=OSError("disk failed after cancellation"))
    await _preparation(package, lines, "late close failure", close_failure=OSError("close failed after cancellation"))
    await _preparation(
        package, lines, "deadline close failure", deadline=True, close_failure=OSError("deadline close failed")
    )
    await _preparation(package, lines, "deadline disk failure", deadline=True, failure=OSError("deadline disk failed"))
    await _preparation(package, lines, "owned read", reading=True, repeat=True, close_body=True)
    await _preparation(package, lines, "borrowed read", reading=True, owned=False)
    await _preparation(package, lines, "late read failure", reading=True, failure=OSError("read failed"))
    await _preparation(package, lines, "deadline read", reading=True, deadline=True)
    await _path(package, lines, "late path open")
    await _path(package, lines, "closed body late open", close_body=True)
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
    await _path(package, lines, "deadline path open", deadline=True)
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
    await _completed_job(package, lines, "completed file preparation")
    await _completed_job(package, lines, "completed path open", path_body=True)
    await _completed_job(package, lines, "completed file failure", failure=OSError("completed disk failure"))
    await _completed_job(
        package, lines, "completed path failure", path_body=True, failure=OSError("completed stat failure")
    )
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
    """Exercise cancelled and expired file preparation, reads, and path opens through generated public calls."""
    run(lambda: _files(package, lines))
