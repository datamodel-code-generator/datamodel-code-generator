"""Call the parameter styles server through its client: label, matrix, simple, delimited, form and header objects.

The document also holds exploded cookie arrays and objects, an allowReserved query, and names that each target
renames: Python keywords, a path and query parameter of one name, and the targets' own reserved arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/parameter-styles.yaml"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records its typed arguments."""
    del models
    return {"untagged": service(server.services.UntaggedService, record)}


def calls(kit: Kit) -> Calls:
    """Return calls that send every style with values that need encoding."""
    api, models = kit.api, kit.models
    point, corner = models.Point(x=1, y=-2), models.Point(x=3, y=4)
    return [
        ("get_labels", lambda: api.default.get_labels(name="a b", names=["x", "y"], point=point)),
        ("get_matrix", lambda: api.default.get_matrix(name="a;b", names=["x", "y"], point=point)),
        ("get_simple", lambda: api.default.get_simple(name="a,b", names=[1, 2, 3], point=point)),
        (
            "get_query every style",
            lambda: api.default.get_query(
                spaced=["a", "b"], piped=[1, 2], point=point, corner=corner, flags=[True, False], reserved="a/b?c"
            ),
        ),
        ("get_query delimiter in a value", lambda: api.default.get_query(spaced=["b c"])),
        (
            "get_query characters to escape",
            lambda: api.default.get_query(spaced=["\u00e4&b=c", "#"], reserved="\u00e4 &=#"),
        ),
        ("get_labels escapes", lambda: api.default.get_labels(name="a.b", names=["%", "?", "#"], point=point)),
        (
            "get_labels slash",
            lambda: api.default.get_labels(name="a/b", names=["x"], point=point),
        ),
        (
            "get_matrix slash",
            lambda: api.default.get_matrix(name="a/b", names=["x"], point=point),
        ),
        (
            "get_simple slash",
            lambda: api.default.get_simple(name="a/b", names=[1], point=point),
        ),
        ("get_headers non-ASCII", lambda: api.default.get_headers(X_Tags=["caf\u00e9"])),
        ("get_query nothing", api.default.get_query),
        ("get_query empty arrays", lambda: api.default.get_query(spaced=[], piped=[])),
        ("get_headers", lambda: api.default.get_headers(X_Point=point, X_Corner=corner, X_Tags=["a", "b c"])),
        ("get_cookies", lambda: api.default.get_cookies(ids=[1, 2], point=point, count=3)),
        (
            "get_names",
            lambda: api.default.get_names(
                id=1,
                id_1=2,
                class_="c",
                from_="f",
                self_1="s",
                options_1="o",
                request="r",
                page_size_=10,
                user_id="u",
            ),
        ),
    ]
