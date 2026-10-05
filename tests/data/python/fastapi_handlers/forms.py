"""Services of the forms server: URL-encoded fields, uploaded files, and lists of files, recorded as received."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def _describe(body: object) -> str:
    if isinstance(body, dict):
        return repr(body)
    fields = asdict(body) if is_dataclass(body) and not isinstance(body, type) else body.model_dump()
    return f"{type(body).__name__}({', '.join(f'{key}={value!r}' for key, value in fields.items())})"


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records the form model each operation receives."""

    class Untagged(server.services.UntaggedService):
        def post_form(self, *, body: object) -> None:
            calls.append(f"post_form({_describe(body)})")

        def post_notes(self, *, body: object) -> None:
            calls.append(f"post_notes({_describe(body)})")

        def upload(self, *, body: object) -> None:
            calls.append(f"upload({_describe(body)})")

        def upload_many(self, *, body: object) -> None:
            calls.append(f"upload_many({_describe(body)})")

    return {"default": {"untagged": Untagged()}}
