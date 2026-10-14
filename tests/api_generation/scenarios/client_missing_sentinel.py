"""Send and read unset optional members: the request leaves them out, and a response that leaves them out reads so."""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Final

from tests.api_generation.support.client_runtime import (
    Exchange,
    argument,
    json_response,
    raw_response,
    record,
    request_body,
)

if TYPE_CHECKING:
    from types import ModuleType

_BOUNDARY: Final = re.compile(r"\b[0-9a-f]{32}\b")


def missing_members(package: ModuleType, lines: list[str]) -> None:
    """Leave unset members out of JSON, forms, deepObject queries, and parts, and send null only when it is given."""
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        unset = request_body(package, "createPet", "application/json", {"name": "a", "owner": None})
        lines.append(f"  unset members {unset!r}")
        exchange.respond(json_response(200, {"name": "a", "owner": None}))
        record(lines, "json unset members", lambda: api.default.create_pet(body=unset))
        nick = request_body(package, "createPet", "application/json", {"name": "a", "owner": None, "nick": None})
        exchange.respond(json_response(200, {"name": "a", "owner": None, "nick": None}))
        record(lines, "json nullable null", lambda: api.default.create_pet(body=nick))
        for label, wire in (
            ("form unset members", {"user": "ann"}),
            ("form null member", {"user": "ann", "remember": True, "note": None}),
        ):
            exchange.respond(raw_response(204))
            body = request_body(package, "login", "application/x-www-form-urlencoded", wire)
            record(lines, label, lambda body=body: api.default.login(body=body))
        for label, wire in (
            ("profile unset members", {"name": "ann"}),
            ("profile null member", {"name": "ann", "note": None}),
        ):
            exchange.respond(raw_response(204))
            body = request_body(package, "updateProfile", "multipart/form-data", wire)
            record(lines, label, lambda body=body: api.default.update_profile(body=body))
        parts = bodies.MultipartBody((
            bodies.FilePart("file", b"x"),
            bodies.FieldPart("tag", "t"),
            bodies.FieldPart("note", None),
        ))
        exchange.respond(raw_response(204))
        record(lines, "multipart null field part", lambda: api.default.upload(body=parts))
        query = argument(package, "listPets", "query", "filter", {"status": "x", "owner": None})
        for label, payload in (
            ("absent response members", [{"name": "a", "owner": None}]),
            ("null non-nullable member", [{"name": "a", "owner": None, "tag": None}]),
            ("enum null member", [{"name": "a", "owner": None, "color": None}]),
            ("enum unknown member", [{"name": "a", "owner": None, "color": "blue"}]),
        ):
            exchange.respond(json_response(200, payload))
            record(lines, label, lambda: api.default.list_pets(filter=query))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]
