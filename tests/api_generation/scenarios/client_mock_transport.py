"""Answer generated clients through `httpx2.MockTransport`, the public pattern for testing code that uses HTTPX2.

The responses it returns are read on creation when made with `json=` or `content=` bytes, and streamed when made
from an iterator, so the clients must accept a body HTTPX2 already holds as well as one they read themselves.
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.api_generation.support.client_runtime import arecord, argument, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from types import ModuleType

_PET: Final = b'{"id":3,"name":"fox"}'
_JSON: Final = {"content-type": "application/json"}
_GZIP: Final = {**_JSON, "content-encoding": "gzip"}
_IDENTITY: Final = {**_JSON, "content-encoding": " Identity ,IDENTITY "}


class _Answers:
    """Answer each request with the next queued response, recording the request line."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.queued: list[Any] = []

    def respond(self, answer: Callable[[], Any]) -> None:
        self.queued.append(answer)

    def __call__(self, request: httpx2.Request) -> Any:
        self.lines.append(f"  > {request.method} {request.url}")
        return self.queued.pop(0)()


def _consumed() -> httpx2.Response:
    """Return a streamed response whose stream was consumed without being read."""
    response = httpx2.Response(200, headers=_JSON, content=iter((_PET,)))
    for _ in response.iter_raw():
        pass
    return response


async def _chunks() -> AsyncIterator[bytes]:  # ruff: ignore[unused-async] - an async iterator body is what HTTPX2 streams
    yield _PET[:9]
    yield _PET[9:]


async def _aconsumed() -> httpx2.Response:
    """Return an asynchronous streamed response whose stream was consumed without being read."""
    response = httpx2.Response(200, headers=_JSON, content=_chunks())
    async for _ in response.aiter_raw():
        pass
    return response


def _bodies(*, asynchronous: bool) -> tuple[tuple[str, Callable[[], Any]], ...]:
    """Return the responses a pet is decoded from: read on creation, streamed, and consumed without being read."""
    return (
        ("content", lambda: httpx2.Response(200, headers=_JSON, content=_PET)),
        ("gzip content", lambda: httpx2.Response(200, headers=_GZIP, content=gzip.compress(_PET, compresslevel=9))),
        (
            "streamed",
            (lambda: httpx2.Response(200, headers=_JSON, content=_chunks()))
            if asynchronous
            else (lambda: httpx2.Response(200, headers=_JSON, content=iter((_PET[:9], _PET[9:])))),
        ),
        ("consumed stream", _aconsumed if asynchronous else _consumed),
    )


def _raw_bodies() -> tuple[tuple[str, Callable[[], httpx2.Response]], ...]:
    """Return read responses whose raw bytes are their content, coded only as identity, or are gone with a coding."""
    return (
        ("raw content", lambda: httpx2.Response(200, headers=_JSON, content=_PET)),
        ("raw gzip content", lambda: httpx2.Response(200, headers=_GZIP, content=gzip.compress(_PET, compresslevel=9))),
        ("raw identity content", lambda: httpx2.Response(200, headers=_IDENTITY, content=_PET)),
    )


def mock_transport(package: ModuleType, lines: list[str]) -> None:
    """Call a generated client injected with a mock transport, in both execution modes."""
    trace = argument(package, "listPets", "header", "X-Trace", "t")
    pet = argument(package, "getPet", "path", "petId", 3)
    answers = _Answers(lines)
    with httpx2.Client(transport=httpx2.MockTransport(answers)) as http, package.Client(http_client=http) as api:
        answers.respond(lambda: httpx2.Response(200, json=[{"id": 1, "name": "dog"}]))
        record(lines, "json", lambda: api.pets.list_pets(X_Trace=trace))
        answers.respond(lambda: httpx2.Response(404, json={"code": 4, "message": "missing"}))
        record(lines, "json error", lambda: api.pets.list_pets(X_Trace=trace))
        for label, answer in _bodies(asynchronous=False):
            answers.respond(answer)
            record(lines, label, lambda: api.pets.get_pet(petId=pet))
        for label, answer in _raw_bodies():
            answers.respond(answer)
            with api.pets.with_streaming_response.get_pet(petId=pet) as response:
                record(lines, label, lambda response=response: list(response.iter_raw_bytes()))
    run(lambda: _amock_transport(package, answers, trace, pet))


async def _amock_transport(package: ModuleType, answers: _Answers, trace: object, pet: object) -> None:
    """Call an asyncio generated client injected with a mock transport."""
    lines = answers.lines
    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(answers)) as http,
        package.AsyncClient(http_client=http) as api,
    ):
        answers.respond(lambda: httpx2.Response(200, json=[{"id": 1, "name": "dog"}]))
        await arecord(lines, "async json", lambda: api.pets.list_pets(X_Trace=trace))
        answers.respond(lambda: httpx2.Response(404, json={"code": 4, "message": "missing"}))
        await arecord(lines, "async json error", lambda: api.pets.list_pets(X_Trace=trace))
        for label, answer in _bodies(asynchronous=True):
            answers.respond(answer)
            await arecord(lines, f"async {label}", lambda: api.pets.get_pet(petId=pet))
        for label, answer in _raw_bodies():
            answers.respond(answer)
            async with api.pets.with_streaming_response.get_pet(petId=pet) as response:
                await arecord(lines, f"async {label}", lambda response=response: _alist(response.iter_raw_bytes()))


async def _alist(chunks: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in chunks]
