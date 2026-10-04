"""Turn wire syntax and model validation failures into the HTTP errors that generated adapters raise."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi.exceptions import HTTPException, ResponseValidationError

from ..model_codecs.errors import CodecResourceLimitError, WireValidationError
from ..model_codecs.wire import pointer_tokens

if TYPE_CHECKING:
    from pydantic import ValidationError

_MALFORMED: Final = frozenset({
    "parameter.duplicate",
    "parameter.empty",
    "parameter.encoding",
    "parameter.object",
    "parameter.percent",
    "parameter.syntax",
})
_REASONS: Final = {
    "form.duplicate": ("media_invalid", "Invalid request body"),
    "form.undeclared": ("extra_forbidden", "Unexpected field"),
    "parameter.lexical": ("type_error", "Invalid value type"),
    "parameter.undeclared": ("extra_forbidden", "Unexpected field"),
    "text.encoding": ("media_invalid", "Invalid request body"),
}
REQUEST_ERRORS: Final = (WireValidationError, CodecResourceLimitError)
_JSON: Final = ("json_invalid", "Invalid JSON body")
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


def _record(code: str, location: tuple[str | int, ...]) -> Record:
    kind, message = _JSON if code.startswith("json.") else _REASONS.get(code, _VALUE)
    return {"type": kind, "loc": location, "msg": message}


def _located(pointer: str) -> tuple[str | int, ...]:
    return tuple(int(token) if token.isascii() and token.isdecimal() else token for token in pointer_tokens(pointer))


def wire_records(error: WireValidationError | CodecResourceLimitError, location: tuple[str, ...]) -> list[Record]:
    """Return the 422 records of request data its wire syntax rejects, or raise the 400 of malformed data."""
    if isinstance(error, WireValidationError) and not any(issue.code in _MALFORMED for issue in error.issues):
        return [_record(issue.code, (*location, *_located(issue.instance_pointer))) for issue in error.issues]
    raise malformed_request() from error


def model_records(error: ValidationError, location: tuple[str, ...]) -> list[Record]:
    """Return the records of a value the model rejects, located as FastAPI locates its own."""
    return [{**item, "loc": (*location, *item["loc"])} for item in error.errors(include_url=False)]


def response_failure(message: str) -> ResponseValidationError:
    """Return the error for a handler result that no declared response accepts."""
    return ResponseValidationError([{"type": "value_error", "loc": ("response",), "msg": message}])
