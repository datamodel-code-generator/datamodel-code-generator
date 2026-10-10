"""The HTTP errors and validation records that generated adapters raise for requests they cannot read or accept."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi.exceptions import HTTPException

if TYPE_CHECKING:
    from pydantic import ValidationError

_VALUE: Final = ("value_error", "Invalid value")

Record = dict[str, object]


def unsupported_media() -> HTTPException:
    """Return the error for a request body whose media type no declared content accepts."""
    return HTTPException(status_code=415, detail="Unsupported media type")


def malformed_request() -> HTTPException:
    """Return the error for request data that no declared parameter or media form can represent."""
    return HTTPException(status_code=400, detail="Invalid request")


def missing(location: tuple[str, ...]) -> Record:
    """Return the record of a required value the request omits."""
    return {"type": "missing", "loc": location, "msg": "Field required"}


def invalid(location: tuple[str, ...]) -> Record:
    """Return the record of a value the request carries in a form its declaration rejects."""
    kind, message = _VALUE
    return {"type": kind, "loc": location, "msg": message}


def media_invalid(location: tuple[str, ...]) -> Record:
    """Return the record of a body that is not text in its declared charset."""
    return {"type": "media_invalid", "loc": location, "msg": "Invalid request body"}


def model_records(error: ValidationError, location: tuple[str, ...]) -> list[Record]:
    """Return the records of a value the model rejects, located as FastAPI locates its own, without the value."""
    return [
        {**item, "loc": (*location, *item["loc"])}
        for item in error.errors(include_url=False, include_context=False, include_input=False)
    ]
