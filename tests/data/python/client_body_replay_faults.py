"""Exercise native file positioning and read failures through generated public clients."""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from tests.data.python.client_runtime import Exchange, arecord, describe, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_DATA = Path(__file__).parents[1] / "generation_platform/client/native-bodies.json"


class _Positionless(io.BytesIO):
    """A readable file with no available tell operation."""

    def tell(self) -> int:
        raise io.UnsupportedOperation("position unavailable")


class _RewindFailure(io.BytesIO):
    """A file that becomes unseekable after the public call captures its position."""

    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.armed = False

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self.armed:
            raise OSError("rewind failed")
        return super().seek(offset, whence)

    def on_event(self, event: Any) -> None:
        if event.name == "call_start":
            self.armed = True


class _ReadFailure(io.BytesIO):
    """A conventional binary file whose read fails."""

    def read(self, size: int | None = -1, /) -> bytes:
        raise OSError("read failed")


class _Unclosable(io.BytesIO):
    """A file the call opened from a path whose close fails after releasing it."""

    def close(self) -> None:
        super().close()
        raise OSError("close failed")


class _Held(io.BytesIO):
    """A file the call opened from a path whose first read waits in its thread until the scenario lets it go."""

    def __init__(self, content: bytes, events: list[str], failure: OSError | None) -> None:
        super().__init__(content)
        self.events, self.failure = events, failure
        self.loop = asyncio.get_running_loop()
        self.entered, self.released = asyncio.Event(), threading.Event()

    def read(self, size: int | None = -1, /) -> bytes:
        if not self.released.is_set():
            self.events.append("read entered")
            self.loop.call_soon_threadsafe(self.entered.set)
            self.released.wait(5)
            self.events.append("read left")
            if self.failure is not None:
                raise self.failure
        return super().read(size)

    def close(self) -> None:
        self.events.append("closed")
        super().close()


def _opening(paths: tuple[Path, ...], files: list[Any], make: Callable[[], object]) -> Callable[..., object]:
    """Open the scenario's paths as the files it makes, and any other path as it is."""
    original = Path.open

    def opened(self: Path, *arguments: Any, **keywords: Any) -> object:
        if self not in paths:
            return original(self, *arguments, **keywords)
        files.append(file := make())
        return file

    return opened


def _closed(lines: list[str], label: str, error: Exception | None, files: list[_Unclosable]) -> None:
    secondary = [type(item).__name__ for item in getattr(error, "secondary_errors", ())]
    lines.append(f"  {label} returned" if error is None else f"  {label} ! {describe(error)}")
    lines.append(f"    secondary={secondary} closed={[file.closed for file in files]}")
    files.clear()


_PATHS: Final = (Path("first.bin"), Path("second.bin"))
_STORED: Final = raw_response(200, b"ok", "text/plain")


@pytest.mark.abnormal_path("A close failure of a file the SDK opened cannot be produced with a real file portably.")
def _close_faults(package: ModuleType, options: ModuleType, data: dict[str, Any], lines: list[str]) -> None:
    """Keep a failed call's error primary, close every opened path, and report a close failure after a success."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    path, files = _PATHS[0], []
    parts = bodies.MultipartBody(tuple(bodies.FilePart(item.stem, item) for item in _PATHS))
    config = options.ClientOptions(retry=options.RetryOptions(max_retries=0))
    exchange = Exchange([])
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=config) as api,
        pytest.MonkeyPatch.context() as fault,
    ):
        fault.setattr(Path, "open", _opening(_PATHS, files, lambda: _Unclosable(data["payload"].encode())))
        for label, response, call in (
            (
                "close failure after a typed result",
                raw_response(204),
                lambda: api.files.replace_file(body=path, media_type="image/png"),
            ),
            ("close failure after a raw result", _STORED, lambda: api.request_raw("PUT", data["url"], body=path)),
            (
                "close failure after a failed call",
                raw_response(500, b"down", "text/plain"),
                lambda: api.files.replace_file(body=path, media_type="image/png"),
            ),
            ("close failures of two parts", _STORED, lambda: api.request_raw("POST", data["url"], body=parts)),
            (
                "close failure entering a stream",
                _STORED,
                lambda: api.with_streaming_response.request_raw("PUT", data["url"], body=path).__enter__(),
            ),
        ):
            exchange.respond(response)
            try:
                call()
            except Exception as error:  # noqa: BLE001
                _closed(lines, label, error, files)
            else:
                _closed(lines, label, None, files)


@pytest.mark.abnormal_path("A close failure of a file the SDK opened cannot be produced with a real file portably.")
async def _async_close_faults(package: ModuleType, options: ModuleType, data: dict[str, Any], lines: list[str]) -> None:
    path, files = _PATHS[0], []
    config = options.ClientOptions(retry=options.RetryOptions(max_retries=0))
    exchange = Exchange([])
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
        with pytest.MonkeyPatch.context() as fault:
            fault.setattr(Path, "open", _opening(_PATHS, files, lambda: _Unclosable(data["payload"].encode())))
            for label, response, call in (
                (
                    "async close failure after a typed result",
                    raw_response(204),
                    lambda: api.files.replace_file(body=path, media_type="image/png"),
                ),
                (
                    "async close failure after a raw result",
                    _STORED,
                    lambda: api.request_raw("PUT", data["url"], body=path),
                ),
                (
                    "async close failure after a failed call",
                    raw_response(500, b"down", "text/plain"),
                    lambda: api.files.replace_file(body=path, media_type="image/png"),
                ),
                (
                    "async close failure entering a stream",
                    _STORED,
                    lambda: api.with_streaming_response.request_raw("PUT", data["url"], body=path).__aenter__(),
                ),
            ):
                exchange.respond(response)
                try:
                    await call()
                except Exception as error:  # noqa: BLE001
                    _closed(lines, label, error, files)
                else:
                    _closed(lines, label, None, files)


@pytest.mark.abnormal_path("A read of a real file cannot be held in its thread while the caller is cancelled.")
async def _async_thread_faults(package: ModuleType, data: dict[str, Any], lines: list[str]) -> None:
    """Finish the read running in a thread before a cancelled call closes the file it opened from a path."""
    exchange = Exchange([])
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        for label, cancellations, failure in (
            ("cancelled while a path is read", 1, None),
            ("cancelled twice while a path is read", 2, None),
            ("cancelled while a path read fails", 1, OSError("read failed")),
        ):
            events: list[str] = []
            held = _Held(data["payload"].encode(), events, failure)
            with pytest.MonkeyPatch.context() as fault:
                fault.setattr(Path, "open", _opening(_PATHS, [], lambda held=held: held))
                exchange.respond(_STORED)
                upload = asyncio.ensure_future(api.request_raw("PUT", data["url"], body=_PATHS[0]))
                await held.entered.wait()
                for _ in range(cancellations):
                    upload.cancel()
                    await asyncio.sleep(0)
                events.append(f"cancelled done={upload.done()} closed={held.closed}")
                held.released.set()
                try:
                    await upload
                except asyncio.CancelledError as error:
                    result = f"CancelledError notes {error.__dict__.get('__notes__', [])}"
                else:
                    result = "returned"
            exchange.responders.clear()
            lines.append(f"  {label}: {result} events={events}")


@pytest.mark.abnormal_path(
    "External file read and seek failures cannot be made portable with ordinary filesystem permissions."
)
def body_replay_faults(package: ModuleType, lines: list[str]) -> None:
    """Preserve caller ownership and stop before sending when rewinding fails."""
    options = importlib.import_module(f"{package.__name__}.options")
    data = json.loads(_DATA.read_text())
    exchange = Exchange([])
    with exchange.client() as native, package.Client(http_client=native) as api:
        file = _Positionless(data["payload"].encode())
        exchange.respond(raw_response(200, b"ok", "text/plain"))
        record(lines, "positionless file", lambda: api.request_raw("POST", data["url"], body=file).read())
        lines.append(f"    caller positionless file open={not file.closed}")
        file.close()
        file = _RewindFailure(data["payload"].encode())
        record(
            lines,
            "rewind failure before send",
            lambda: api.request_raw("POST", data["url"], body=file, options=options.RequestOptions(hooks=(file,))),
        )
        lines.append(f"    rewind failure caller file open={not file.closed}")
        file.close()
        file = _ReadFailure(data["payload"].encode())
        record(lines, "native file read failure", lambda: api.request_raw("POST", data["url"], body=file))
        lines.append(f"    read failure caller file open={not file.closed}")
        file.close()
        record(lines, "invalid raw binary input", lambda: api.request_raw("POST", data["url"], body=object()))
    _close_faults(package, options, data, lines)
    run(lambda: _async_faults(package, options, data, lines))
    run(lambda: _async_close_faults(package, options, data, lines))
    run(lambda: _async_thread_faults(package, data, lines))


async def _async_faults(package: ModuleType, options: ModuleType, data: dict[str, Any], lines: list[str]) -> None:
    exchange = Exchange([])
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        file = _Positionless(data["payload"].encode())
        exchange.respond(raw_response(200, b"ok", "text/plain"))

        async def call() -> bytes:
            response = await api.request_raw("POST", data["url"], body=file)
            return await response.read()

        await arecord(lines, "async positionless file", call)
        file.close()
        file = _RewindFailure(data["payload"].encode())
        await arecord(
            lines,
            "async rewind failure",
            lambda: api.request_raw("POST", data["url"], body=file, options=options.RequestOptions(hooks=(file,))),
        )
        lines.append(f"    async failed caller file open={not file.closed}")
        file.close()
