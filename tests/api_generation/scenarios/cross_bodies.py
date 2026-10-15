"""Call the bodies server through its client: JSON, forms, multipart files and JSON parts, text, and bytes.

Each call names the media type it sends where the operation declares several, and the server reports the model or
value its handler received with the media type it read. Members whose types have no one scalar kind travel as JSON
parts, in a body sent whole and in one sent part by part, and so do the other properties a form's schema types,
or a schema it combines with allOf, under whatever names a call gives them. So do members without a type, and the
other properties a schema allows without one, a string or an array included, which the server reads back as the
client sent them, and keeps as text, or a file's bytes, when another sender's part is not JSON. A form that allows no
other properties refuses a member its schema does not declare, which its model's client never writes.
"""

from __future__ import annotations

from itertools import starmap
from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-bodies.yaml"
CLIENT = {"default_base_url": "https://bodies.example.com"}
_OTHER = {"name": "n", "other": "x"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return a call for each media type of each operation, with and without its optional body."""
    api, models, bodies = kit.api.default, kit.models, kit.module("bodies")
    multipart = bodies.AsyncMultipartBody if kit.asynchronous else bodies.MultipartBody
    upload = multipart((
        bodies.FilePart("file", b"\x00binary\xff", filename="a b.bin", content_type="application/octet-stream"),
        bodies.FieldPart("note", "café"),
    ))
    form = models.FormsPostRequest(name="n", tags=["a", "b c"], count=2, extra=5)
    search = models.SearchesPostRequest(
        filter=models.Filter(color="blue", minAge=3),
        filters=[models.Filter(color="a"), models.Filter(minAge=2)],
        tags=["a", "b c"],
        limit=5,
    )
    refused = models.SearchesPostRequest.model_construct(
        filter=models.Filter.model_construct(minAge=-1),
        filters=[models.Filter(), models.Filter.model_construct(minAge=-2)],
    )
    blue, old = models.Filter(color="blue"), models.Filter(minAge=3)
    negative = models.Filter.model_construct(minAge=-1)
    untyped = (
        ("values", {"anything": {"a": 1}, "many": [{"b": 2}, [3], "s"]}),
        ("arrays", {"anything": [1, "2"], "many": [None, 1.5]}),
        ("strings", {"anything": "1", "many": ["s", "true"]}),
    )
    choices = (
        ("text", {"either": "plain", "level": models.Level("a"), "words": "s", "lists": ["x"]}),
        (
            "values",
            {"either": models.Filter(color="c"), "level": models.Level(1), "words": ["a", "b"], "lists": [1, 2]},
        ),
    )
    return [
        ("post_item", lambda: api.post_item(body=models.Item(name="a"))),
        ("post_item without a body", api.post_item),
        ("post_account", lambda: api.post_account(body=models.Account(id=1, name="a"))),
        (
            "post_profile profile=a",
            lambda: api.post_profile(body=models.ProfilesPostRequest(a=1), media_type="application/json; profile=a"),
        ),
        (
            "post_profile profile=b",
            lambda: api.post_profile(body=models.ProfilesPostRequest1(b="x"), media_type="application/json; profile=b"),
        ),
        (
            "post_variant JSON",
            lambda: api.post_variant(body=models.VariantsPostRequest1(c=True), media_type="application/json"),
        ),
        (
            "post_variant profile=v1",
            lambda: api.post_variant(body=models.VariantsPostRequest(a=1), media_type="application/json; profile=v1"),
        ),
        (
            "post_variant form",
            lambda: api.post_variant(
                body=models.VariantsPostRequest2(tags=["a", "b"]),
                media_type="application/x-www-form-urlencoded",
            ),
        ),
        ("put_document JSON", lambda: api.put_document(body=models.Item(name="j"), media_type="application/json")),
        (
            "put_document vendor JSON",
            lambda: api.put_document(body=models.Item(name="v"), media_type="application/vnd.item+json"),
        ),
        (
            "put_document text",
            lambda: api.put_document(body=models.DocumentsPutRequest("hi"), media_type="text/markdown"),
        ),
        ("put_archive bytes", lambda: api.put_archive(body=b"PK\x03\x04", media_type="application/zip")),
        (
            "put_archive text",
            lambda: api.put_archive(body=models.ArchivesPutRequest("abc"), media_type="text/plain"),
        ),
        ("put_blob", lambda: api.put_blob(body=b"\x00\x01\x02")),
        ("post_form", lambda: api.post_form(body=form)),
        ("post_native_form", lambda: api.post_native_form(body=models.NativeFormsPostRequest(name="n"))),
        ("upload", lambda: api.upload(body=upload)),
        ("search", lambda: api.search(body=search)),
        ("search refused parts", lambda: api.search(body=refused)),
        *(
            (f"post_choices {label}", lambda choice=choice: api.post_choices(body=models.Choices(**choice)))
            for label, choice in choices
        ),
        *(
            (
                f"post_choice_files {label}",
                lambda choice=choice: api.post_choice_files(
                    body=multipart(tuple(starmap(bodies.FieldPart, choice.items())))
                ),
            )
            for label, choice in choices
        ),
        ("post_extras", lambda: api.post_extras(body=models.ExtrasPostRequest(name="n", first=blue, second=old))),
        (
            "post_extras refused",
            lambda: api.post_extras(body=models.ExtrasPostRequest.model_construct(first=blue, second=negative)),
        ),
        (
            "post_extra_lists",
            lambda: api.post_extra_lists(body=models.ExtraListsPostRequest(name="n", first=[blue, old], second=[old])),
        ),
        (
            "post_extra_lists refused",
            lambda: api.post_extra_lists(body=models.ExtraListsPostRequest.model_construct(first=[blue, negative])),
        ),
        (
            "post_extra_counts",
            lambda: api.post_extra_counts(body=models.ExtraCountsPostRequest(name="n", first=1, second=2)),
        ),
        (
            "post_extra_counts refused",
            lambda: api.post_extra_counts(body=models.ExtraCountsPostRequest.model_construct(first=1, second=-2)),
        ),
        (
            "post_extra_files",
            lambda: api.post_extra_files(
                body=multipart((
                    bodies.FilePart("file", b"\x00"),
                    bodies.FieldPart("name", "n"),
                    bodies.FieldPart("first", [blue, old]),
                    bodies.FieldPart("second", [old]),
                ))
            ),
        ),
        ("post_extra_files without parts", lambda: api.post_extra_files(body=multipart(()))),
        (
            "post_extra_files refused",
            lambda: api.post_extra_files(body=multipart((bodies.FieldPart("first", [blue, negative]),))),
        ),
        (
            "post_extra_scores",
            lambda: api.post_extra_scores(body=models.ExtraScoresPostRequest(name="n", first=[1, 2], second=[3])),
        ),
        (
            "post_extra_scores refused",
            lambda: api.post_extra_scores(body=models.ExtraScoresPostRequest.model_construct(first=[1, -2])),
        ),
        (
            "post_extra_bases",
            lambda: api.post_extra_bases(body=models.ExtraBasesPostRequest(base="b", name="n", first=blue, second=old)),
        ),
        (
            "post_nullable_search",
            lambda: api.post_nullable_search(
                body=models.NullableSearchesPostRequest(models.Search(filter=blue, limit=5, first=old))
            ),
        ),
        (
            "post_nullable_search refused",
            lambda: api.post_nullable_search(
                body=models.NullableSearchesPostRequest(models.Search.model_construct(filter=negative, first=negative))
            ),
        ),
        ("post_closed_form", lambda: api.post_closed_form(body=models.ClosedFormsPostRequest(name="n"))),
        ("post_closed_parts", lambda: api.post_closed_parts(body=models.ClosedPartsPostRequest(name="n"))),
        ("post_closed_parts without members", lambda: api.post_closed_parts(body=models.ClosedPartsPostRequest())),
        *(
            (
                f"{path} with another member",
                lambda path=path, sent=sent: kit.then(
                    kit.http.post(f"https://bodies.example.com{path}", **sent),
                    lambda response: (
                        response.status_code,
                        [(record["type"], record["loc"]) for record in response.json()["detail"]],
                    ),
                ),
            )
            for path, sent in (
                ("/closed-forms", {"data": _OTHER}),
                ("/closed-parts", {"files": {name: (None, value) for name, value in _OTHER.items()}}),
            )
        ),
        ("post_check", lambda: api.post_check(body=models.Check(code=7, scores=[1, 2]))),
        ("post_note null", lambda: api.post_note(body=models.NotesPostRequest(None))),
        ("post_note item", lambda: api.post_note(body=models.NotesPostRequest(models.Item(name="i")))),
        ("post_label null", lambda: api.post_label(body=models.LabelsPostRequest(None))),
        ("post_label text", lambda: api.post_label(body=models.LabelsPostRequest("a"))),
        ("post_label None", lambda: api.post_label(body=None)),
        ("post_label refused", lambda: api.post_label(body=models.LabelsPostRequest.model_construct("too long"))),
        ("post_account None", lambda: api.post_account(body=None)),
        ("post_raw", lambda: api.post_raw(body=models.RawPostRequest())),
        *(
            (f"post_anything {label}", lambda value=value: api.post_anything(body=models.Anythings(**value)))
            for label, value in untyped
        ),
        *(
            (
                f"post_anything_extras {label}",
                lambda value=value: api.post_anything_extras(
                    body=models.AnythingExtrasPostRequest(anything=value["anything"], first=blue)
                ),
            )
            for label, value in untyped
        ),
        *(
            (
                f"post_anything_files {label}",
                lambda value=value: api.post_anything_files(
                    body=multipart(tuple(starmap(bodies.FieldPart, value.items())))
                ),
            )
            for label, value in untyped
        ),
        *(
            (
                f"{operation} {label}",
                lambda operation=operation, model=model, value=value: getattr(api, operation)(
                    body=getattr(models, model)(name="n", **value)
                ),
            )
            for operation, model in (
                ("post_untyped_others", "UntypedOthersPostRequest"),
                ("post_open_others", "OpenOthersPostRequest"),
            )
            for label, value in untyped
        ),
        *(
            (
                f"{path} other text and file parts",
                lambda path=path: kit.then(
                    kit.http.post(
                        f"https://bodies.example.com{path}",
                        files=[("other", (None, b"plain")), ("more", ("a.txt", b"plain", "text/plain"))],
                    ),
                    lambda response: response.status_code,
                ),
            )
            for path in ("/untyped-others", "/open-others")
        ),
        *(
            (
                f"/anythings {label}",
                lambda sent=sent: kit.then(
                    kit.http.post("https://bodies.example.com/anythings", files=sent),
                    lambda response: response.status_code,
                ),
            )
            for label, sent in (
                ("text parts", [("anything", (None, b"plain")), ("many", (None, b"[1]")), ("many", (None, b"x y"))]),
                ("file part", [("anything", ("a.txt", b"plain", "text/plain"))]),
            )
        ),
    ]
