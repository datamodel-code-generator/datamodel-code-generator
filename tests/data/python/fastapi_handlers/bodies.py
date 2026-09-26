"""Services of the request body server: they report the decoded bodies they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records its keywords, reading uploads and raw requests to their bytes."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Untagged(server.services.UntaggedService):
        def post_item(self, **arguments: object) -> None:
            record("post_item", arguments)

        def post_profile(self, **arguments: object) -> None:
            record("post_profile", arguments)

        def post_account(self, **arguments: object) -> None:
            record("post_account", arguments)

        def put_document(self, **arguments: object) -> None:
            record("put_document", arguments)

        def put_blob(self, **arguments: object) -> None:
            record("put_blob", arguments)

        def post_form(self, **arguments: object) -> None:
            record("post_form", arguments)

        def post_native_form(self, **arguments: object) -> None:
            record("post_native_form", arguments)

        def post_check(self, **arguments: object) -> None:
            record("post_check", arguments)

        def upload(self, *, file: object, note: object) -> None:
            calls.append(f"upload(file={file.filename!r}:{file.file.read()!r}, note={note!r})")

        async def post_raw(self, *, request: object) -> None:
            body = await request.body()
            calls.append(f"post_raw(request={request.method} {request.url.path}, body={body!r})")

    return {"default": {"untagged": Untagged()}}
