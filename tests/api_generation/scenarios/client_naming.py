"""Call the methods of a client whose names follow the model's naming options, and record the requests they send."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import Exchange, raw_response, record

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

_GET: Final = {
    "itemId": 1,
    "X_Request_Id": "rid",
    "page_size_": 10,
    "field_2fa": "t",
    "class_": "c",
    "sessionId": "sid",
}
_CALL_OPTIONS: Final = {"options_1": "o", "body_1": "b", "self_1": "s"}
_NUMBERED: Final = {**_GET, "itemId_1": 2, "itemId_2": 3, **_CALL_OPTIONS, "itemId_1_1": 5}
_REPEATED: Final = (("itemId", 2), ("itemId", 3), ("options", "o"), ("body", "b"), ("self", "s"))


def _calls(
    package: ModuleType,
    lines: list[str],
    methods: tuple[str, str],
    item: Mapping[str, object],
    fields: Mapping[str, object],
    path: str = "itemId",
) -> None:
    """Send each operation once with every argument, so each request shows the wire name each argument names."""
    exchange = Exchange(lines)
    api: Any
    get, create = methods
    with exchange.client() as http, package.Client(http_client=http, timeout_1="key") as api:
        for label, call in (
            ("get an item", lambda: getattr(api.item_store, get)(**item)),
            ("create an item from fields", lambda: getattr(api.item_store, create)(name="q", **fields)),
            ("delete an item's tags", lambda: api.item_store_1.delete_items_by_item_id_tags(**{path: 4})),
            ("check the health", api.close_1.get_health),
        ):
            exchange.respond(raw_response(204))
            record(lines, label, call)


def _prefixed(prefix: str) -> dict[str, object]:
    """Return the arguments of the item operation whose duplicates take a prefix, then a number, as models do."""
    item: dict[str, object] = {**_GET, "itemId_1": 5}
    for wire, value in _REPEATED:
        name = f"{prefix}_{wire}"
        item[f"{name}_1" if name in item else name] = value
    return item


def naming(package: ModuleType, lines: list[str]) -> None:
    """Name each argument after its wire name as a model field is named, numbering those other names take."""
    _calls(package, lines, ("get_item", "get_item_1"), _NUMBERED, {"name_1": "n", "count": 2})


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
        "item_id_1_1": 5,
    }
    _calls(package, lines, ("get_item", "get_item_1"), item, {"name_1": "n", "item_count": 2}, "item_id")


def naming_parent_prefixed(package: ModuleType, lines: list[str]) -> None:
    """Prefix a duplicate with its enclosing scope: a method with its resource, an argument with its method."""
    fields = {"get_item_name": "n", "itemCount": 2}
    _calls(package, lines, ("get_item", "item_store_get_item"), _prefixed("get_item"), fields)


def naming_full_path(package: ModuleType, lines: list[str]) -> None:
    """Prefix a duplicate with every enclosing scope: an argument with its resource and its method."""
    fields = {"item_store_get_item_name": "n", "itemCount": 2}
    _calls(package, lines, ("get_item", "item_store_get_item"), _prefixed("item_store_get_item"), fields)


def naming_primary_first(package: ModuleType, lines: list[str]) -> None:
    """Give the names the document spells as written their own names first: the operationId `get_item` and `itemId_1`.

    The other duplicates are then numbered past them.
    """
    item = {**_GET, "itemId_2": 2, "itemId_3": 3, **_CALL_OPTIONS, "itemId_1": 5}
    _calls(package, lines, ("get_item_1", "get_item"), item, {"name_1": "n", "itemCount": 2})
