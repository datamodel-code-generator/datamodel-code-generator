"""HTTP results, and the declared responses an operation sends results and bare values as."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Generic, Literal, cast

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import ResponseValidationError
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response
from typing_extensions import TypeIs, TypeVar

BodyT_co = TypeVar("BodyT_co", covariant=True)

_EMPTY_STATUSES: Final = frozenset({204, 205, 304})
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HEADER_TEXT: Final = re.compile(r"(?:[\x21-\x7E](?:[\t\x20-\x7E]*[\x21-\x7E])?)?")
_MIN_CONTENT_STATUS: Final = 200
_MIN_REDIRECT_STATUS: Final = 300
_JSON: Final = "application/json"


@dataclass(frozen=True, slots=True)
class HTTPResult(Generic[BodyT_co]):
    """Send one declared response: its status, an optional body of the declared type, and extra headers.

    Without a body, a success response that declares content sends None through its model, as a bare value is sent:
    `null` where the model accepts null, a validation error where it does not. A response whose JSON accepts null
    sends `null` on any status, and any other response sends no body.
    """

    status_code: int
    body: BodyT_co | None = None
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Declared:
    """One declared response: its default media type with that type's kind and charset, and the model its body takes.

    A response that declares no content has no media type; a body sent under it anyway is JSON.
    `null` says the body is JSON of a type that accepts null, so the response sends None as `null` instead of no body.
    `dump` writes a JSON body by the model's adapter, for a model whose adapter's own dump leaves out what it holds.
    The model's adapter is built when the response first sends a body; a concurrent first send builds an equal one.
    """

    media_type: str | None = None
    kind: Literal["json", "text", "form", "multipart", "binary"] = "json"
    charset: str = "utf-8"
    model: object = None
    null: bool = False
    dump: Callable[[TypeAdapter[object], object], bytes] | None = None
    _adapter: TypeAdapter[object] | None = field(default=None, init=False, repr=False, compare=False)

    def adapter(self) -> TypeAdapter[object] | None:
        """Return the adapter of the body model, building and keeping it on first use, or None without a model."""
        if (adapter := self._adapter) is None and (model := self.model) is not None:
            built: TypeAdapter[object] = TypeAdapter(cast("type[object]", model))
            object.__setattr__(self, "_adapter", built)  # noqa: PLC2801 - Keep the adapter on the frozen value.
            return built
        return adapter

    def sends_none(self, status: int) -> bool:
        """Return whether the response sends None as a body: JSON that accepts null, or content of a success status."""
        return self.null or (self.media_type is not None and status < _MIN_REDIRECT_STATUS)


_UNDECLARED: Final = Declared()


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResponses:
    """An operation's declared responses by status key, and the status of a bare value the route sends itself.

    FastAPI's response_model sends the bare value of an operation without that status. `primary_media` is the media
    a bare value takes when it is chosen over the default media of the response that declares that status. `bare` is
    False for an operation without a primary response, which refuses a bare value.
    """

    responses: Mapping[str, Declared]
    primary: int | None = None
    primary_media: Declared | None = None
    head: bool = False
    bare: bool = True

    def find(self, status: int) -> Declared | None:
        """Return the response that declares a status: exact, then the same status class, then default."""
        responses = self.responses
        return responses.get(str(status)) or responses.get(f"{status // 100}XX") or responses.get("default")


def dispatch(value: object, plan: OperationResponses) -> object:
    """Pass a Response through, send an HTTPResult as its declared response, and leave a bare value to FastAPI.

    An operation whose primary response FastAPI does not send sends a bare value as that response, and an operation
    without a primary response refuses one.
    """
    if isinstance(value, Response):
        return value
    if _is_result(value):
        return _respond(value.status_code, value.body, value.headers, plan)
    if plan.primary is not None:
        return _respond(plan.primary, value, None, plan, plan.primary_media)
    if plan.bare:
        return value
    msg = "The method returned a value for an operation without a primary response; return an HTTPResult"
    raise _failure(msg)


def _is_result(value: object) -> TypeIs[HTTPResult[object]]:
    return isinstance(value, HTTPResult)


def _respond(
    status: int,
    body: object,
    headers: Mapping[str, str] | None,
    plan: OperationResponses,
    chosen: Declared | None = None,
) -> Response:
    """Send a body as its declared response, raw bytes of a `text/*` one under its media type without a charset added.

    Starlette labels every `text/*` body without a charset UTF-8, which only text the response encodes itself is.
    """
    if headers:
        _check_headers(headers.items())
    declared = chosen or plan.find(status) or _UNDECLARED
    if (
        (body is None and not declared.sends_none(status))
        or plan.head
        or status < _MIN_CONTENT_STATUS
        or status in _EMPTY_STATUSES
    ):
        if inspect.isawaitable(body):
            raise _unawaited(body)
        return Response(status_code=status, headers=headers)
    media_type = declared.media_type or _JSON
    if isinstance(content := _content(declared, body), str):
        return Response(_encoded(declared, content), status_code=status, media_type=media_type, headers=headers)
    if declared.kind == "json" or not media_type.startswith("text/"):
        return Response(content, status_code=status, media_type=media_type, headers=headers)
    response = Response(content, status_code=status, headers=headers)
    response.headers.setdefault("content-type", media_type)
    return response


def _check_headers(headers: Iterable[tuple[object, object]]) -> None:
    """Refuse a result's header whose name is not a token or whose value is not header text, as RFC 9110 writes them.

    Header text, as the generated client sends it, is HTAB, SP, and visible ASCII, without whitespace at either end.
    A result's headers may hold other values than its annotation says, such as bytes.
    """
    for name, value in headers:
        if not (isinstance(name, str) and _TOKEN.fullmatch(name)):
            msg = f"The response header name {name!r} is not a token"
            raise _failure(msg)
        if not (isinstance(value, str) and _HEADER_TEXT.fullmatch(value)):
            msg = f"The {name} response header is not header text"
            raise _failure(msg)


def _unawaited(body: object) -> TypeError:
    """Return the error for an awaitable result that a response without a body would drop before it ran."""
    if isinstance(body, Coroutine):
        body.close()
    return TypeError("The method returned an awaitable for a response without a body; nothing awaited it")


def _content(declared: Declared, body: object) -> str | bytes:
    """Return the text of a body to encode in the declared charset, or the bytes to send as they are.

    A text body's model writes its value as its JSON text, an enum member as its value.
    """
    json_media = declared.kind == "json"
    if (adapter := declared.adapter()) is None:
        payload = _json(jsonable_encoder(body, by_alias=True, exclude_unset=True)) if json_media else body
    else:
        try:
            value = adapter.validate_python(body)
        except ValidationError as error:
            raise ResponseValidationError(error.errors(include_url=False)) from error
        if json_media:
            if (dump := declared.dump) is None:
                return adapter.dump_json(value, by_alias=True, exclude_unset=True)
            return dump(adapter, value)
        payload = adapter.dump_python(value, mode="json" if declared.kind == "text" else "python", by_alias=True)
    if isinstance(payload, (str, bytes)):
        return payload
    msg = f"A {declared.media_type} body must be a string or bytes"
    raise _failure(msg)


def _encoded(declared: Declared, text: str) -> bytes:
    try:
        return text.encode(declared.charset)
    except (LookupError, UnicodeError) as error:
        msg = f"The text body cannot be encoded as {declared.media_type}"
        raise _failure(msg) from error


def _failure(message: str) -> ResponseValidationError:
    """Return the error for a handler result that no declared response accepts."""
    return ResponseValidationError([{"type": "value_error", "loc": ("response",), "msg": message}])


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
