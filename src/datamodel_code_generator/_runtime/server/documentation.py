"""Describe what the adapters read and send with their models' schemas, when FastAPI builds the OpenAPI document.

FastAPI documents the inputs and bodies it validates itself. Every other place an operation declares a model type at,
an adapter parameter, a header, a non-JSON body, or a callback, carries an empty schema in the route's
`openapi_extra` until the application's document is built. Then one FastAPI pass names every model of the routes and
of these places together, so their schemas reference the document's components as FastAPI's own do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.utils import create_model_field
from starlette.responses import Response

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastapi import FastAPI

Step: TypeAlias = "str | tuple[str, str]"

_PROBE: Final = "/{datamodel-code-generator}"
_FIRST: Final = 1000


@dataclass(frozen=True, slots=True)
class Schema:
    """One place of an operation object that a model type's schema describes, and the name FastAPI titles it by.

    A string token is a key of the object at hand, and a pair the parameter of that location and name in its
    parameters. A place under a response is described as FastAPI describes what a route sends, any other as what it
    validates.
    """

    at: tuple[Step, ...]
    type: Any
    name: str
    mode: Literal["validation", "serialization"] = "validation"


class OperationDocument(dict[str, Any]):  # noqa: FURB189 - FastAPI takes `openapi_extra` as a dict.
    """An operation's `openapi_extra`, which also carries the places that its model types describe."""

    __slots__ = ("schemas",)

    def __init__(self, extra: dict[str, Any], *, schemas: tuple[Schema, ...] = ()) -> None:
        """Keep the documentation FastAPI merges into the operation, and the places its schemas fill."""
        super().__init__(extra)
        self.schemas = schemas


def documented(app: FastAPI) -> None:
    """Make the application build its document with the schema of every place an operation declares a model type at.

    The document is built on the first request for it, as FastAPI builds its own, and kept for the later ones.
    """

    def openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            app.openapi_schema = _document(app)
        return app.openapi_schema

    setattr(app, "openapi", openapi)  # noqa: B010 - FastAPI's documented way to extend the document.


def _document(app: FastAPI) -> dict[str, Any]:
    """Return FastAPI's document of the application, with each place's schema from the same pass as FastAPI's own."""
    places = [
        (context, schema)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and context.include_in_schema
        and isinstance(extra := context.openapi_extra, OperationDocument)
        for schema in extra.schemas
    ]
    document = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        terms_of_service=app.terms_of_service,
        contact=app.contact,
        license_info=app.license_info,
        routes=app.routes,
        webhooks=[*app.webhooks.routes, _probe(schema for _, schema in places)],
        tags=app.openapi_tags,
        servers=app.servers,
        separate_input_output_schemas=app.separate_input_output_schemas,
        external_docs=app.openapi_external_docs,
    )
    webhooks = document["webhooks"]
    described = webhooks.pop(_PROBE)["post"]["responses"]
    if not webhooks:
        del document["webhooks"]
    for index, (context, schema) in enumerate(places, _FIRST):
        found = described[str(index)]["content"]["application/json"]["schema"]
        for method in context.methods or ():
            _place(document["paths"][context.path_format][method.lower()], schema.at)["schema"] = found
    return document


def _probe(schemas: Iterable[Schema]) -> APIRoute:
    """Return a route whose responses carry the places' types, for FastAPI to name with every other model."""
    probe = APIRoute(_PROBE, _endpoint, methods=["POST"], response_model=None, response_class=Response)
    fields = {
        index: create_model_field(name=schema.name, type_=schema.type, mode=schema.mode)
        for index, schema in enumerate(schemas, _FIRST)
    }
    probe.responses = {index: {"description": ""} for index in fields}
    probe.response_fields = dict(fields)
    return probe


def _endpoint() -> None:
    """Answer nothing: the probe route is documented, never served."""


def _place(operation: dict[str, Any], at: tuple[Step, ...]) -> dict[str, Any]:
    """Return the object a place names in an operation object."""
    target = operation
    for step in at:
        target = (
            next(item for item in target["parameters"] if (item["in"], item["name"]) == step)
            if isinstance(step, tuple)
            else target[step]
        )
    return target


__all__ = ["OperationDocument", "Schema", "documented"]
