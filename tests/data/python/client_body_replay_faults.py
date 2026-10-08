"""Exercise native file positioning and read failures through generated public clients."""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
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
    run(lambda: _async_faults(package, options, data, lines))


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
