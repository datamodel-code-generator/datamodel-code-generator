"""Page through the pagination server with its client's helpers: body, header, path, and status cursors.

The server pages five users two at a time and reports each page request; the client's helpers send the cursor each
page returned until the page that ends the traversal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/pagination.yaml"
CLIENT = {"protocols": "client/protocols/pagination.json"}
NAMES = ("Ann", "Bob", "Cid", "Dee", "Eve")


def _window(cursor: str | None, size: int = 2) -> tuple[list[str], str | None]:
    """Return the names a cursor's page holds and the next page's cursor, or None after the last page."""
    start = int(cursor.removeprefix("c")) if cursor else 0
    return list(NAMES[start : start + size]), f"c{start + size}" if start + size < len(NAMES) else None


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return services that page the users by cursor, header, path, and status."""
    result = server.HTTPResult

    def users(cursor: str | None, names: Mapping[str, None] | None = None) -> tuple[list[object], str | None]:
        window, following = _window(cursor)
        return [models.User(id=name.lower(), name=name) for name in window if names is None or name in names], following

    def page(cursor: str | None, **_: object) -> object:
        data, following = users(cursor)
        return result(200, models.UserPage(data=data, next_cursor=following), headers={"X-Next": following or "done"})

    def labels(after: str | None) -> object:
        window, following = _window(after)
        return result(200, [models.Label(name=name) for name in window], headers={"X-Next": following or ""})

    def nested(cursor: str | None) -> object:
        data, following = users(cursor)
        found = models.NestedResult(items=data, next=following) if following else models.NestedResult(items=data)
        return models.NestedPage(result=found)

    def loose(cursor: int | None) -> object:
        data, following = users(f"c{cursor or 0}")
        return models.LoosePage(data=data, next=following and int(following.removeprefix("c")))

    def statuses(code: int | None) -> object:
        data, _ = users("c0" if code is None else "c2")
        return result(200 if code else 206, models.UserPage(data=data))

    return {
        "users": service(
            server.services.UsersService,
            record,
            list_users=page,
            search_users=lambda cursor, body: models.UserPage(
                data=users(cursor, dict.fromkeys(name for name in NAMES if body.name in name))[0],
                next_cursor=_window(cursor)[1],
            ),
        ),
        "loose": service(server.services.LooseService, record, list_loose=loose),
        "nested": service(server.services.NestedService, record, list_nested=nested),
        "labels": service(server.services.LabelsService, record, list_labels=labels, list_label_sets=labels),
        "archive": service(
            server.services.ArchiveService,
            record,
            list_archive=lambda cursor: page(None if cursor == "start" else cursor),
        ),
        "statuses": service(server.services.StatusesService, record, list_statuses=statuses),
        "secure": service(server.services.SecureService, record, list_secure_users=lambda cursor, **_: page(cursor)),
        "authorize": lambda requirement_sets, credentials: (
            record("authorize", {"requirement_sets": requirement_sets, "credentials": dict(credentials)}) or "reader"
        ),
    }


def calls(kit: Kit) -> Calls:
    """Return a traversal of each helper, the first two pages of one, and a plain call of a paged operation."""
    helpers, models = kit.api.protocols, kit.models
    users = helpers.users.all
    return [
        ("list_users", lambda: kit.api.users.list_users(limit=2)),
        ("users.all", lambda: users.iterate(limit=2)),
        ("users.all pages", lambda: kit.then(users.page(), users.next_page)),
        ("users.by_header", lambda: helpers.users.by_header.iterate(X_Snapshot="s1")),
        ("users.search", lambda: helpers.users.search.iterate(body=models.UserQuery(name="e"))),
        ("loose.all", helpers.loose.all.iterate),
        ("nested.all", helpers.nested.all.iterate),
        ("labels.all", helpers.labels.all.iterate),
        ("labels.sets", helpers.labels.sets.iterate),
        ("archive.all", lambda: helpers.archive.all.iterate(cursor="start")),
        ("statuses.all", helpers.statuses.all.iterate),
        ("secure.users", kit.connect(oauth="token-1").protocols.secure.users.iterate),
    ]
