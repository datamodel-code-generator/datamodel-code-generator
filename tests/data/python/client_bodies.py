"""Send native files, paths and iterables through generated public operations."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from tests.data.python.client_runtime import Exchange, arecord, argument, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from types import ModuleType

_DATA = Path(__file__).parents[1] / "generation_platform/client/native-bodies.json"


def _photo(package: ModuleType) -> object:
    return argument(package, "uploadPhoto", "path", "petId", 1)


class Chunks:
    """A caller-owned binary iterable for multipart and request examples."""

    def __init__(self, lines: list[str], chunks: tuple[bytes, ...], closing: BaseException | None = None) -> None:
        self.lines, self.chunks, self.closing = lines, chunks, closing

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    def close(self) -> None:
        self.lines.append("  caller chunks closed")
        if self.closing is not None:
            raise self.closing


def bodies(package: ModuleType, lines: list[str]) -> None:
    """Keep caller files open, open paths lazily and accept native binary iterables."""
    data = json.loads(_DATA.read_text())
    exchange = Exchange(lines)
    with (
        tempfile.TemporaryDirectory() as directory,
        exchange.client() as native,
        package.Client(http_client=native) as api,
    ):
        file = io.BytesIO(data["file"].encode())
        file.seek(data["offset"])
        path = Path(directory) / "body.bin"
        path.write_bytes(data["payload"].encode())
        for label, body in (
            ("bytes", data["payload"].encode()),
            ("file entry offset", file),
            ("native path", path),
            ("native iterable", (data["payload"].encode(),)),
            ("empty native iterable", iter(())),
        ):
            exchange.respond(raw_response(200, data["payload"].encode(), "image/png"))
            record(lines, label, lambda body=body: api.pets.photos.upload(pet_id=_photo(package), body=body))
        lines.append(f"  caller file open={not file.closed} offset={file.tell()}")
        for label, body in (
            ("invalid binary object", object()),
            ("invalid binary mapping", {}),
            ("string is not a path", str(path)),
        ):
            record(lines, label, lambda body=body: api.pets.photos.upload(pet_id=_photo(package), body=body))
        file.close()
        path.unlink()
    run(lambda: _async_bodies(package, data, lines))


async def _async_bodies(package: ModuleType, data: dict[str, object], lines: list[str]) -> None:
    exchange = Exchange(lines)
    payload = str(data["payload"]).encode()
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        file = io.BytesIO(payload)
        for label, body in (
            ("async bytes", payload),
            ("async conventional file", file),
            ("async sync iterable", iter((payload,))),
            ("native async iterable", Chunks(lines, (payload,))),
        ):
            exchange.respond(raw_response(200, payload, "image/png"))
            await arecord(lines, label, lambda body=body: api.pets.photos.upload(pet_id=_photo(package), body=body))
        await arecord(
            lines, "async invalid input", lambda: api.pets.photos.upload(pet_id=_photo(package), body=object())
        )
        lines.append(f"  async caller file open={not file.closed}")
        file.close()
