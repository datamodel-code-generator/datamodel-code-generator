"""Services of the forms server: urlencoded fields, uploaded files, and lists of files, recorded as received."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import UploadFile

if TYPE_CHECKING:
    from types import ModuleType


def _describe(value: object) -> str:
    match value:
        case UploadFile():
            return f"{value.filename!r}:{value.content_type}:{value.file.read()!r}"
        case list():
            return f"[{', '.join(_describe(item) for item in value)}]"
        case None:
            return "absent"
        case _ if type(value).__name__ == "Unset":
            return "absent"
    return repr(value)


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records the fields and files each operation receives."""

    def record(operation: str, /, **values: object) -> None:
        calls.append(f"{operation}({', '.join(f'{key}={_describe(value)}' for key, value in values.items())})")

    class Untagged(server.services.UntaggedService):
        def post_form(self, *, name: object, count: object, tags: object) -> None:
            record("post_form", name=name, count=count, tags=tags)

        def upload(self, *, file: object, note: object) -> None:
            record("upload", file=file, note=note)

        def upload_many(self, *, files: object, label: object) -> None:
            record("upload_many", files=files, label=label)

    return {"default": {"untagged": Untagged()}}
