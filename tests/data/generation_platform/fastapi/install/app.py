"""Serve requests from an installed generated server through its ASGI interface, printing each response."""

from __future__ import annotations

import asyncio
import json

from models import Contact, FieldContactsSearchPostRequest, FieldContactsSearchPostResponse
from server import create_app
from server.services import UntaggedService


class Contacts(UntaggedService):
    def create_contact(self, *, body: Contact) -> Contact:
        return body

    def search_contacts(self, *, body: FieldContactsSearchPostRequest) -> FieldContactsSearchPostResponse:
        return FieldContactsSearchPostResponse(body.nickname)


async def call(app: object, path: str, media_type: str, body: bytes) -> str:
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    headers = [(b"host", b"test"), (b"content-type", media_type.encode()), (b"content-length", str(len(body)).encode())]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    payload = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return f"{status} {payload.decode()}" if status < 400 else str(status)


async def main() -> None:
    app = create_app(untagged=Contacts())
    contact = {"id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "mail": "ann@example.com", "born": "2020-02-29", "nickname": "ann"}
    for path, media_type, body in (
        ("/contacts", "application/json", json.dumps(contact).encode()),
        ("/contacts", "application/json", json.dumps({**contact, "mail": "ann"}).encode()),
        ("/contacts", "application/json", json.dumps({**contact, "nickname": "Ann"}).encode()),
        ("/contacts/search", "application/x-www-form-urlencoded", b"nickname=ann"),
        ("/contacts/search", "application/x-www-form-urlencoded", b"nickname=Ann"),
    ):
        print(path, await call(app, path, media_type, body))


asyncio.run(main())
