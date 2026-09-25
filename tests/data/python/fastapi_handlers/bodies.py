"""Handlers of the request body server: they report the decoded bodies they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def handlers(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, Callable[..., object]]]:
    """Return handlers that record their keywords, reading uploads and raw requests to their bytes."""

    def recorder(name: str) -> Callable[..., object]:
        def handle(**arguments: object) -> None:
            calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

        return handle

    def upload(*, file: object, note: object) -> None:
        calls.append(f"upload(file={file.filename!r}:{file.file.read()!r}, note={note!r})")

    def post_raw(*, request: object) -> None:
        calls.append(f"post_raw(request={request.method} {request.url.path})")

    names = (
        "post_item",
        "post_profile",
        "post_account",
        "put_document",
        "put_blob",
        "post_form",
        "post_native_form",
        "post_check",
    )
    return {"default": {**{name: recorder(name) for name in names}, "upload": upload, "post_raw": post_raw}}
