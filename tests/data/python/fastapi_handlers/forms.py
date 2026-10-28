"""Services of the forms server: URL-encoded fields, uploaded files, lists of files, and JSON parts, as received.

The models of the forms that type their other properties hold them where the backend keeps them: a dataclass as
attributes beside its fields.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def _describe(body: object) -> str:
    if isinstance(body, dict):
        return repr(body)
    if is_dataclass(body) and not isinstance(body, type):
        fields = asdict(body)
        fields.update((key, value) for key, value in vars(body).items() if key not in fields)
    else:
        fields = body.model_dump()
    return f"{type(body).__name__}({', '.join(f'{key}={value!r}' for key, value in fields.items())})"


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records the form model each operation receives."""

    class Default(server.services.DefaultService):
        def post_form(self, *, body: object) -> None:
            calls.append(f"post_form({_describe(body)})")

        def post_notes(self, *, body: object) -> None:
            calls.append(f"post_notes({_describe(body)})")

        def upload(self, *, body: object) -> None:
            calls.append(f"upload({_describe(body)})")

        def upload_many(self, *, body: object) -> None:
            calls.append(f"upload_many({_describe(body)})")

        def search(self, *, body: object) -> None:
            calls.append(f"search({_describe(body)})")

        def post_pets(self, *, body: object) -> None:
            calls.append(f"post_pets({_describe(body)})")

        def post_extras(self, *, body: object) -> None:
            calls.append(f"post_extras({_describe(body)})")

        def post_extra_lists(self, *, body: object) -> None:
            calls.append(f"post_extra_lists({_describe(body)})")

        def post_extra_scores(self, *, body: object) -> None:
            calls.append(f"post_extra_scores({_describe(body)})")

        def post_extra_bases(self, *, body: object) -> None:
            calls.append(f"post_extra_bases({_describe(body)})")

        def post_extra_patterns(self, *, body: object) -> None:
            calls.append(f"post_extra_patterns({_describe(body)})")

        def post_extra_profiles(self, *, body: object, media_type: str) -> None:
            calls.append(f"post_extra_profiles({_describe(body)}, {media_type!r})")

        def post_spreads(self, *, body: object) -> None:
            calls.append(f"post_spreads({_describe(body)})")

    return {"default": {"default": Default()}}
