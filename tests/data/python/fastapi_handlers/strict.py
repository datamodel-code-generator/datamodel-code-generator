"""Services of the strict parameter and form server: they report the converted values they receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records its keywords and form bodies and sends no body."""

    class Untagged(server.services.UntaggedService):
        def get_values(self, **arguments: object) -> None:
            calls.append(f"get_values({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

        def post_form(self, *, body: object) -> None:
            calls.append(f"post_form({body!r})")

        def upload(self, *, body: object) -> None:
            calls.append(f"upload({body!r})")

    return {"default": {"untagged": Untagged()}}
