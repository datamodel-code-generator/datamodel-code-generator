"""Call the parameter styles server through its client: label, matrix, simple, delimited, form and header objects.

The document also holds exploded cookie arrays and objects, an allowReserved query, and names that each target
renames: Python keywords, a path and query parameter of one name, and the targets' own reserved arguments. A header
carries ASCII text only: the client refuses any other value in every style before it sends, and JSON content, which
it writes escaped, carries the rest; a call's media type with other text labels no body. Literal path
text the document percent-encodes is sent as written and routed by its decoded value.
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
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return calls that send every style with values that need encoding."""
    api, models = kit.api, kit.models
    point, corner = models.Point(x=1, y=-2), models.Point(x=3, y=4)
    text, kind = api.default.get_header_text, models.HeaderTextGetHeaderXKindParameter
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
        ("get_headers non-ASCII array item", lambda: api.default.get_headers(X_Tags=["caf\u00e9", "b"])),
        ("get_header_text non-ASCII scalar", lambda: text(X_Name="caf\u00e9")),
        ("get_header_text scalar beyond Latin-1", lambda: text(X_Name="\u65e5\u672c\u8a9e")),
        ("get_header_text non-ASCII exploded array item", lambda: text(X_Items=["caf\u00e9", "b"])),
        ("get_header_text non-ASCII object member", lambda: text(X_Label=models.Named(name="caf\u00e9"))),
        ("get_header_text non-ASCII enum member", lambda: text(X_Kind=kind("caf\u00e9"))),
        ("get_header_text non-ASCII text content", lambda: text(X_Note="caf\u00e9")),
        (
            "get_header_text JSON content beyond Latin-1",
            lambda: text(X_Named=models.Named(name="\u65e5\u672c\u8a9e")),
        ),
        ("get_header_text JSON content with DEL", lambda: text(X_Named=models.Named(name="a\x7fb"))),
        (
            "get_header_text ASCII",
            lambda: text(
                X_Name="a\tb ~",
                X_Items=["a b", "c"],
                X_Label=models.Named(name="a b"),
                X_Kind=kind.plain,
                X_Note="a, b",
                X_Named=models.Named(name="a b"),
            ),
        ),
        ("get_literal encoded path text", lambda: api.default.get_literal(id="x y")),
        (
            "post_labelled_text non-ASCII media type",
            lambda: api.default.post_labelled_text(
                body=models.LabelledTextPostRequest("a"), media_type='text/plain; label="caf\u00e9"'
            ),
        ),
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
