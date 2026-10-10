"""Call the methods of a client whose arguments take the model's field names, and record the requests they send."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.data.python.client_runtime import Exchange, raw_response, record

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType


def _calls(
    package: ModuleType, lines: list[str], path: str, item: Mapping[str, object], fields: Mapping[str, object]
) -> None:
    """Send each operation once with every argument, so each request shows the wire name each argument names."""
    exchange = Exchange(lines)
    api: Any
    with exchange.client() as http, package.Client(http_client=http, timeout_1="key") as api:
        for label, call in (
            ("get an item", lambda: api.item_store.get_item(**item)),
            ("create an item from fields", lambda: api.item_store.get_item_1(name="q", **fields)),
            ("delete an item's tags", lambda: api.item_store_1.delete_items_by_item_id_tags(**{path: 4})),
            ("check the health", api.close_1.get_health),
        ):
            exchange.respond(raw_response(204))
            record(lines, label, call)


def naming(package: ModuleType, lines: list[str]) -> None:
    """Name each argument after its wire name as a model field is named, suffixing those other names take."""
    item = {
        "itemId": 1,
        "itemId_1": 2,
        "itemId_2": 3,
        "X_Request_Id": "rid",
        "page_size_": 10,
        "field_2fa": "t",
        "class_": "c",
        "options_1": "o",
        "body_1": "b",
        "self_1": "s",
        "sessionId": "sid",
    }
    _calls(package, lines, "itemId", item, {"name_1": "n", "itemCount": 2})


def naming_snake(package: ModuleType, lines: list[str]) -> None:
    """Name each argument in snake case, as `--snake-case-field` names model fields, with the same suffixes."""
    item = {
        "item_id": 1,
        "item_id_1": 2,
        "item_id_2": 3,
        "x_request_id": "rid",
        "page_size_": 10,
        "field_2fa": "t",
        "class_": "c",
        "options_1": "o",
        "body_1": "b",
        "self_1": "s",
        "session_id": "sid",
    }
    _calls(package, lines, "item_id", item, {"name_1": "n", "item_count": 2})
