"""Exercise call-owned inputs when encoding omits them or their final release fails."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2

from tests.data.python.client_bodies import _photo
from tests.data.python.client_body_replay import _File
from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType


class _Codec:
    """Simulate a faulty serializer that omits the resource it received."""

    def __init__(self) -> None:
        self.calls = 0

    def serialize(self, value: object, context: object, *, validate: bool = False) -> str:
        del value, context, validate
        self.calls += 1
        return "encoded"


class _FailedClose(_File):
    def close(self) -> None:
        super().close()
        message = "owned input close failed"
        raise RuntimeError(message)


def retry_ownership(package: ModuleType, lines: list[str]) -> None:
    """Observe omitted input release before sending and final source release before return or handoff."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    codecs = importlib.import_module(f"{package.__name__}._runtime.model_codecs.pydantic_v2")
    exchange = Exchange([])
    with exchange.client() as native, package.Client(http_client=native) as api:
        file, codec = _File(b"unused"), _Codec()
        observed: list[tuple[object, ...]] = []

        def omitted(request: httpx2.Request) -> httpx2.Response:
            observed.append((file.closed, file.closes, codec.calls, request.content))
            return httpx2.Response(201, json={"id": 1, "name": "encoded"})

        exchange.respond(omitted)
        with patch.object(codecs.PydanticModelCodec, "serialize", side_effect=codec.serialize):
            record(
                lines,
                "codec omits owned input",
                lambda: api.pets.create_pet(body=bodies.FileBody(file, ownership="owned"), media_type="text/plain"),
            )
        record(lines, "omitted input released before send", lambda: observed)
        for mode in ("typed", "buffered", "stream"):
            file = _FailedClose(b"input")
            body = bodies.FileBody(file, ownership="owned")
            exchange.respond(raw_response(200, b"image", "image/png"))

            def call(mode: str = mode, body: object = body) -> object:
                if mode == "typed":
                    return api.pets.photos.upload(pet_id=_photo(package), body=body)
                if mode == "buffered":
                    return api.request_raw("PUT", "https://source.example.com/", body=body)
                with api.with_streaming_response.request_raw("PUT", "https://source.example.com/", body=body):
                    return "handed off"

            record(lines, f"source close failure {mode}", call)
            record(lines, "source released once", lambda file=file: (file.closed, file.closes))
        record(lines, "ownership sends", lambda: sum(line.startswith("  > ") for line in exchange.lines))
    run(lambda: _async(package, bodies, codecs, lines))


async def _async(package: ModuleType, bodies: ModuleType, codecs: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        file, codec = _File(b"unused"), _Codec()
        observed: list[tuple[object, ...]] = []

        def omitted(request: httpx2.Request) -> httpx2.Response:
            observed.append((file.closed, file.closes, codec.calls, request.content))
            return httpx2.Response(201, json={"id": 1, "name": "encoded"})

        exchange.respond(omitted)
        with patch.object(codecs.PydanticModelCodec, "serialize", side_effect=codec.serialize):
            await arecord(
                lines,
                "async codec omits owned input",
                lambda: api.pets.create_pet(
                    body=bodies.AsyncFileBody(file, ownership="owned"), media_type="text/plain"
                ),
            )
        record(lines, "async omitted input released before send", lambda: observed)
        for mode in ("typed", "buffered", "stream"):
            file = _FailedClose(b"input")
            body = bodies.AsyncFileBody(file, ownership="owned")
            exchange.respond(raw_response(200, b"image", "image/png"))

            async def call(mode: str = mode, body: object = body) -> object:
                if mode == "typed":
                    return await api.pets.photos.upload(pet_id=_photo(package), body=body)
                if mode == "buffered":
                    return await api.request_raw("PUT", "https://source.example.com/", body=body)
                async with api.with_streaming_response.request_raw("PUT", "https://source.example.com/", body=body):
                    return "handed off"

            await arecord(lines, f"async source close failure {mode}", call)
            record(lines, "async source released once", lambda file=file: (file.closed, file.closes))
        record(lines, "async ownership sends", lambda: sum(line.startswith("  > ") for line in exchange.lines))
