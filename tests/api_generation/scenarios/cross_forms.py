"""Call the form encodings server through its client: styled and typed URL-encoded members, and typed parts.

A URL-encoded member whose encoding gives it a style, allowReserved, or content travels as a query parameter of that
encoding, an exploded object as its own members' pairs; the server reads each the same way. In this OpenAPI 3.1
document a style applies to multipart members too, whose parts carry the style's text without percent-encoding, so a
value holding its style's delimiter is refused before it is sent. A multipart member whose encoding names text in
UTF-8 or US-ASCII, or a media type of files, is read as one without such an encoding, and a style leaves its media
type unused. Requests the client never sends show where the server's records point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/form-encodings.yaml"
_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def _raw(kit: Kit, path: str, content: str) -> object:
    """Post URL-encoded text the client never writes, and return the status with the records' types and locations."""
    return kit.then(
        kit.http.post(f"https://encodings.example.com{path}", content=content, headers=_FORM),
        lambda response: (
            response.status_code,
            [(record["type"], record["loc"]) for record in response.json()["detail"]]
            if response.status_code == 422
            else None,
        ),
    )


def _raw_parts(kit: Kit, path: str, files: dict[str, tuple[str | None, bytes]]) -> object:
    """Post form-data parts the client never writes, and return the status with the records' types and locations."""
    return kit.then(
        kit.http.post(f"https://encodings.example.com{path}", files=files),
        lambda response: (
            response.status_code,
            [(record["type"], record["loc"]) for record in response.json()["detail"]]
            if response.status_code == 422
            else None,
        ),
    )


def calls(kit: Kit) -> Calls:
    """Return calls that send each encoding with values that need escaping, and requests the client never sends."""
    api, models, bodies = kit.api.default, kit.models, kit.module("bodies")
    multipart = bodies.AsyncMultipartBody if kit.asynchronous else bodies.MultipartBody
    styles = models.StylesPostRequest(
        name="a b",
        point=models.Point(x=1, y=-2),
        corner=models.Point(x=3, y=4),
        deep=models.Named(name="d&e=f"),
        spaced=["a", "b+c"],
        piped=[1, 2],
        listed=[3, 4],
        path="a/b?c&d=e#f",
        meta=models.Named(name="café & co"),
        note="x & y+z",
    )
    images = multipart((
        bodies.FilePart("image", b"\x89PNG\r\n", filename="a.png", content_type="image/png"),
        bodies.FilePart("thumbnails", b"\x00one", filename="1.png", content_type="image/png"),
        bodies.FilePart("thumbnails", b"\xfftwo", filename="2.gif", content_type="image/gif"),
        bodies.FieldPart("caption", "*café*"),
    ))
    parts = models.StyledPartsPostRequest(
        point=models.Point(x=1, y=-2),
        corner=models.Point(x=3, y=4),
        deep=models.Named(name="d&e=f [x]"),
        spaced=["a", "b+c,d"],
        piped=[1, 2],
        listed=["a b", "%41|"],
        path="a/b?c",
        meta=models.Named(name="caf\u00e9"),
    )
    return [
        ("post_styles", lambda: api.post_styles(body=styles)),
        ("post_styles name only", lambda: api.post_styles(body=models.StylesPostRequest(name="n"))),
        (
            "post_styles empty arrays",
            lambda: api.post_styles(body=models.StylesPostRequest(name="n", spaced=[], listed=[])),
        ),
        (
            "post_styles delimiter in a value",
            lambda: api.post_styles(body=models.StylesPostRequest(name="n", spaced=["b c"])),
        ),
        (
            "post_labels",
            lambda: api.post_labels(body=models.LabelsPostRequest(id=1, labels={"a": "x", "b c": "ä"})),
        ),
        ("post_labels labels only", lambda: api.post_labels(body=models.LabelsPostRequest(labels={"id2": "y"}))),
        (
            "post_choices form",
            lambda: api.post_choices(body=models.Choice(ids=["a", "b"]), media_type=_FORM["Content-Type"]),
        ),
        (
            "post_choices JSON",
            lambda: api.post_choices(body=models.Choice(ids=["a b"]), media_type="application/json"),
        ),
        (
            "post_texts",
            lambda: api.post_texts(body=models.TextsPostRequest(note="café", count=5, code="ab", ids=[1, 2])),
        ),
        ("post_texts non-ASCII code", lambda: api.post_texts(body=models.TextsPostRequest(code="ä"))),
        ("post_images", lambda: api.post_images(body=images)),
        ("post_styled_parts", lambda: api.post_styled_parts(body=parts)),
        (
            "post_styled_parts comma in a comma-delimited value",
            lambda: api.post_styled_parts(body=models.StyledPartsPostRequest(listed=["a,b"])),
        ),
        (
            "post_styled_parts space in a space-delimited value",
            lambda: api.post_styled_parts(body=models.StyledPartsPostRequest(spaced=["a b"])),
        ),
        (
            "post_part_labels",
            lambda: api.post_part_labels(body=models.PartLabelsPostRequest(id=1, labels={"a": "x,y", "b c": "\u00e4"})),
        ),
        ("/styles unknown member of a closed exploded object", lambda: _raw(kit, "/styles", "name=n&x=1&z=2")),
        ("/styles exploded member of another kind", lambda: _raw(kit, "/styles", "name=n&x=one")),
        ("/styles JSON content that is not JSON", lambda: _raw(kit, "/styles", "name=n&meta=%7B&note=%7B")),
        ("/styles unpaired object member", lambda: _raw(kit, "/styles", "name=n&corner=x,1,y")),
        ("/styles percent-encoded deepObject member", lambda: _raw(kit, "/styles", "name=n&deep%5Bname%5D=a")),
        ("/styles piped item of another kind", lambda: _raw(kit, "/styles", "name=n&piped=1%7Ctwo")),
        ("/labels other member", lambda: _raw(kit, "/labels", "id=1&z=w&id=2")),
        (
            "/styled-parts unpaired object member",
            lambda: _raw_parts(kit, "/styled-parts", {"corner": (None, b"x,1,y")}),
        ),
        (
            "/styled-parts items of another kind",
            lambda: _raw_parts(kit, "/styled-parts", {"piped": (None, b"1|two"), "x": (None, b"one")}),
        ),
        (
            "/styled-parts styled members sent as files",
            lambda: _raw_parts(kit, "/styled-parts", {"x": ("x.txt", b"5"), "deep[name]": ("d.txt", b"caf\xc3\xa9")}),
        ),
        (
            "/styled-parts styled member of a file not UTF-8",
            lambda: _raw_parts(kit, "/styled-parts", {"deep[name]": ("d.txt", b"\xff")}),
        ),
    ]
