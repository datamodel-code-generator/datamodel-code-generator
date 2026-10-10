"""Drive uploads to a generated server through ASGI directly: completion, parse errors, and anyio cancellation.

The generated server leaves multipart parsing, file lifetimes, disconnects, and cancellation to FastAPI and Starlette.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import anyio
from fastapi.responses import Response
from starlette.datastructures import UploadFile as ParsedFile

from tests.api_generation.support.fastapi_generation import SOURCE
from tests.api_generation.support.fastapi_server import _generate, _import
from tests.api_generation.support.generated_packages import forget_generated

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastapi import FastAPI


class _Uploads:
    """Serve the forms operations without subclassing their Protocol, recording the upload each request receives."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def upload(self, *, body: Any) -> Response:  # noqa: ANN401
        self.calls.append(f"upload({body.file!r}, {type(body.note).__name__})")
        return Response(status_code=204)

    def post_form(self, **_: object) -> None:
        return None

    def post_notes(self, **_: object) -> None:
        return None

    def upload_many(self, **_: object) -> None:
        return None


def _scope(length: int) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/uploads",
        "raw_path": b"/uploads",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"multipart/form-data; boundary=x"), (b"content-length", str(length).encode())],
        "client": ("client", 1),
        "server": ("server", 80),
    }


async def _drive(app: FastAPI, length: int, chunks: list[bytes], ending: str) -> str:
    messages = [{"type": "http.request", "body": chunk, "more_body": ending != "end"} for chunk in chunks]
    sent: list[str] = []

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        await anyio.sleep_forever()
        return {}

    async def send(message: dict[str, Any]) -> None:  # noqa: RUF029
        if message["type"] == "http.response.start":
            sent.append(str(message["status"]))

    call = app(_scope(length), receive, send)
    try:
        if ending == "cancel scope":
            with anyio.move_on_after(0.05) as scope:
                await call
            return f"cancelled {scope.cancelled_caught}, sent {sent}"
        await call
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__}, sent {sent}"
    return f"sent {sent}"


def _outcome(app: FastAPI, uploads: _Uploads, files: list[Any], length: int, scenario: dict[str, Any]) -> str:
    uploads.calls.clear()
    files.clear()
    answer = asyncio.run(_drive(app, length, [chunk.encode() for chunk in scenario["chunks"]], scenario["ending"]))
    return f"{answer}; calls {uploads.calls}; files closed {[file.file.closed for file in files]}"


def _observe(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every file the multipart parser allocates, before any handler receives its contents."""
    parsed: list[Any] = []
    initialize = ParsedFile.__init__

    def recorded(self: Any, *args: Any, **kwargs: Any) -> None:
        initialize(self, *args, **kwargs)
        parsed.append(self)

    monkeypatch.setattr(ParsedFile, "__init__", recorded)
    return parsed


def fastapi_upstream_report(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Drive each upload scenario through the generated server and report how the request ended.

    Each scenario reports the files the parser allocated and whether the request closed them.
    """
    fixture = json.loads((SOURCE / "uploads.json").read_text(encoding="utf-8"))
    length = fixture["content_length"]
    monkeypatch.syspath_prepend(str(root))
    package = "upstream_uploads"
    _generate({"input": "server-forms.yaml"}, "pydantic_v2.BaseModel", root, package)
    try:
        server, _ = _import(package)
        uploads = _Uploads()
        app = server.create_app(untagged=uploads)
        parsed = _observe(monkeypatch)
        lines = [
            f"{name}: {_outcome(app, uploads, parsed, length, scenario)}"
            for name, scenario in fixture["scenarios"].items()
        ]
    finally:
        forget_generated(package)
    return "\n".join(lines) + "\n"
