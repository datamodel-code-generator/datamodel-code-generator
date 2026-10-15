"""Services of the request body servers: they report the decoded bodies they receive, null included."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records its keywords, reading raw requests to their bytes.

    It holds the methods of the bodies document and of the nullable body documents, one of those asynchronous.
    """

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Default(server.services.DefaultService):
        def post_item(self, **arguments: object) -> None:
            record("post_item", arguments)

        def post_profile(self, **arguments: object) -> None:
            record("post_profile", arguments)

        def post_account(self, **arguments: object) -> None:
            record("post_account", arguments)

        def post_variant(self, **arguments: object) -> None:
            record("post_variant", arguments)

        def put_document(self, **arguments: object) -> None:
            record("put_document", arguments)

        def put_archive(self, **arguments: object) -> None:
            record("put_archive", arguments)

        def put_blob(self, **arguments: object) -> None:
            record("put_blob", arguments)

        def post_form(self, **arguments: object) -> None:
            record("post_form", arguments)

        def post_native_form(self, **arguments: object) -> None:
            record("post_native_form", arguments)

        def post_check(self, **arguments: object) -> None:
            record("post_check", arguments)

        def post_note(self, **arguments: object) -> None:
            record("post_note", arguments)

        def post_label(self, **arguments: object) -> None:
            record("post_label", arguments)

        def post_text(self, **arguments: object) -> None:
            record("post_text", arguments)

        async def post_list(self, **arguments: object) -> None:
            record("post_list", arguments)

        def post_choice(self, **arguments: object) -> None:
            record("post_choice", arguments)

        def post_color(self, **arguments: object) -> None:
            record("post_color", arguments)

        def post_name(self, **arguments: object) -> None:
            record("post_name", arguments)

        def post_pet(self, **arguments: object) -> None:
            record("post_pet", arguments)

        def post_code(self, **arguments: object) -> None:
            record("post_code", arguments)

        def post_value(self, **arguments: object) -> None:
            record("post_value", arguments)

        def post_multi(self, **arguments: object) -> None:
            record("post_multi", arguments)

        def post_multi_optional(self, **arguments: object) -> None:
            record("post_multi_optional", arguments)

        def post_ok(self, **arguments: object) -> None:
            record("post_ok", arguments)

        def post_any_alias(self, **arguments: object) -> None:
            record("post_any_alias", arguments)

        def post_anything(self, **arguments: object) -> None:
            record("post_anything", arguments)

        def post_id(self, **arguments: object) -> None:
            record("post_id", arguments)

        def post_draft(self, **arguments: object) -> None:
            record("post_draft", arguments)

        def post_extended(self, **arguments: object) -> None:
            record("post_extended", arguments)

        def upload(self, **arguments: object) -> None:
            record("upload", arguments)

        def search(self, **arguments: object) -> None:
            record("search", arguments)

        def post_choices(self, **arguments: object) -> None:
            record("post_choices", arguments)

        def post_choice_files(self, **arguments: object) -> None:
            record("post_choice_files", arguments)

        def post_extras(self, **arguments: object) -> None:
            record("post_extras", arguments)

        def post_extra_lists(self, **arguments: object) -> None:
            record("post_extra_lists", arguments)

        def post_extra_counts(self, **arguments: object) -> None:
            record("post_extra_counts", arguments)

        def post_extra_files(self, **arguments: object) -> None:
            record("post_extra_files", arguments)

        def post_extra_scores(self, **arguments: object) -> None:
            record("post_extra_scores", arguments)

        def post_extra_bases(self, **arguments: object) -> None:
            record("post_extra_bases", arguments)

        def post_closed_form(self, **arguments: object) -> None:
            record("post_closed_form", arguments)

        def post_nullable_search(self, **arguments: object) -> None:
            record("post_nullable_search", arguments)

        def post_closed_parts(self, **arguments: object) -> None:
            record("post_closed_parts", arguments)

        def post_anything_extras(self, **arguments: object) -> None:
            record("post_anything_extras", arguments)

        def post_anything_files(self, **arguments: object) -> None:
            record("post_anything_files", arguments)

        def post_untyped_others(self, **arguments: object) -> None:
            record("post_untyped_others", arguments)

        def post_open_others(self, **arguments: object) -> None:
            record("post_open_others", arguments)

        async def post_raw(self, *, request: object) -> None:
            body = await request.body()
            calls.append(f"post_raw(request={request.method} {request.url.path}, body={body!r})")

    return {"default": {"default": Default()}}
