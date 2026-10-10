"""OpenAPI parameters read back: raw HTTP fragments decoded into builtins, and content returned as its text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import starmap
from typing import TYPE_CHECKING, Final

from .errors import MalformedError
from .media import decode_form, media_kind, percent_decode, split_form, typed
from .unset import UNSET

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .media import JSONValue
    from .parameters import ParameterLocation, ParameterPlan

_DELIMITERS: Final = {"spaceDelimited": re.compile(rb"%20|\+| "), "pipeDelimited": re.compile(rb"%7[Cc]|\|")}
_COMMA: Final = re.compile(rb",")
_DOT: Final = re.compile(rb"\.")
_PREFIXES: Final = {"label": b".", "matrix": b";"}
_OWS: Final = " \t"


@dataclass(frozen=True, slots=True)
class ParameterFragment:
    """Keep one raw name/value occurrence before any percent decoding."""

    name: bytes | None
    value: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class RawParameter:
    """Carry one location's ordered raw occurrences, or the whole raw query."""

    location: ParameterLocation
    fragments: tuple[ParameterFragment, ...] = ()
    raw_query: bytes | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RawParameters:
    """Carry an operation's raw path captures, raw query bytes, and ordered raw header lines."""

    path: Mapping[str, bytes] = field(default_factory=dict[str, bytes])
    query: bytes | None = None
    headers: tuple[tuple[bytes, bytes], ...] = ()


def _object(plan: ParameterPlan, members: list[tuple[str, str]]) -> JSONValue:
    declared = {field.name: field for field in plan.fields}
    result: dict[str, JSONValue] = {}
    for name, text in members:
        field = declared.get(name, plan.additional)
        result[name] = typed(text, "string" if field is None else field.kind)
    return result


def _collection(plan: ParameterPlan, parts: list[str], *, exploded_pairs: bool) -> JSONValue:
    if plan.shape == "array":
        return [typed(text, plan.kind) for text in parts]
    if exploded_pairs:
        return _paired(plan, [part.partition("=") for part in parts])
    if len(parts) % 2:
        msg = "An object parameter has an unpaired member"
        raise MalformedError(msg)
    return _object(plan, list(zip(parts[::2], parts[1::2], strict=True)))


def _paired(plan: ParameterPlan, members: list[tuple[str, str, str]]) -> JSONValue:
    if not all(equals for _, equals, _ in members):
        msg = "An exploded object member has no '=' separator"
        raise MalformedError(msg)
    return _object(plan, [(name, text) for name, _, text in members])


def _parts(raw: bytes, delimiter: re.Pattern[bytes]) -> list[bytes]:
    if not raw:
        msg = "An empty delimited value cannot represent a container"
        raise MalformedError(msg)
    return delimiter.split(raw)


def _split(raw: bytes, delimiter: re.Pattern[bytes], *, plus: bool) -> list[str]:
    return [percent_decode(part, plus=plus) for part in _parts(raw, delimiter)]


def _decode_path(plan: ParameterPlan, raw: bytes) -> JSONValue:
    prefix = _PREFIXES.get(plan.style or "", b"")
    if prefix == b"." and raw.startswith((b"%2E", b"%2e")):
        prefix = raw[:3]
    if not raw.startswith(prefix):
        msg = "A label or matrix path value is missing its prefix"
        raise MalformedError(msg)
    body = raw[len(prefix) :]
    if plan.style == "matrix":
        return _decode_matrix(plan, body.split(b";"))
    if plan.shape == "scalar":
        return typed(percent_decode(body, plus=False), plan.kind)
    delimiter = _DOT if plan.style == "label" and plan.explode else _COMMA
    if plan.shape == "object" and plan.explode:
        return _paired(
            plan,
            [
                (percent_decode(name, plus=False), equals.decode(), percent_decode(text, plus=False))
                for name, equals, text in (part.partition(b"=") for part in _parts(body, delimiter))
            ],
        )
    return _collection(plan, _split(body, delimiter, plus=False), exploded_pairs=False)


def _decode_matrix(plan: ParameterPlan, parts: list[bytes]) -> JSONValue:
    members = [(percent_decode(name, plus=False), value) for name, _, value in (part.partition(b"=") for part in parts)]
    if plan.shape == "object" and plan.explode:
        return _object(plan, [(name, percent_decode(value, plus=False)) for name, value in members])
    if any(name != plan.name for name, _ in members):
        msg = "A matrix path member has an unexpected name"
        raise MalformedError(msg)
    if plan.shape == "array" and plan.explode:
        return [typed(percent_decode(value, plus=False), plan.kind) for _, value in members]
    if plan.shape == "scalar":
        return typed(percent_decode(members[-1][1], plus=False), plan.kind)
    return _collection(plan, _split(members[-1][1], _COMMA, plus=False), exploded_pairs=False)


def _last(values: list[bytes]) -> bytes | UNSET:
    """Return the last of a repeated single value, as FastAPI reads query parameters, or UNSET when absent."""
    return values[-1] if values else UNSET


def _exploded_object(plan: ParameterPlan, pairs: list[tuple[str, str]]) -> JSONValue | UNSET:
    declared = {item.name for item in plan.fields}
    members = [
        (name, text)
        for name, text in pairs
        if name in declared or (plan.additional is not None and name not in plan.reserved_names)
    ]
    return _object(plan, members) if members else UNSET


def _decode_query(plan: ParameterPlan, pairs: list[tuple[str, bytes]]) -> JSONValue | UNSET:
    match plan.style, plan.shape, plan.explode:
        case "form", "array", True:
            return [
                typed(percent_decode(value, plus=True), plan.kind) for name, value in pairs if name == plan.name
            ] or UNSET
        case "form", "object", True:
            return _exploded_object(plan, [(name, percent_decode(value, plus=True)) for name, value in pairs])
        case "deepObject", _, _:
            prefix = f"{plan.name}["
            members = [
                (name[len(prefix) : -1], percent_decode(value, plus=True))
                for name, value in pairs
                if name.startswith(prefix) and name.endswith("]")
            ]
            return _object(plan, members) if members else UNSET
        case _:
            pass
    if (value := _last([value for name, value in pairs if name == plan.name])) is UNSET:
        return UNSET
    if plan.shape == "scalar":
        return typed(percent_decode(value, plus=True), plan.kind)
    delimiter = _DELIMITERS.get(plan.style or "", _COMMA)
    return _collection(plan, _split(value, delimiter, plus=True), exploded_pairs=False)


def _decode_header(plan: ParameterPlan, values: list[bytes]) -> JSONValue | UNSET:
    """Decode the first value of a scalar header, as Starlette reads one, or every value of a container."""
    try:
        texts = [value.decode().strip(_OWS) for value in (values[:1] if plan.shape == "scalar" else values)]
    except UnicodeDecodeError:
        msg = "A header value must be UTF-8"
        raise MalformedError(msg) from None
    if not texts:
        return UNSET
    if plan.shape == "scalar":
        return typed(texts[0], plan.kind)
    if not (combined := ",".join(texts)):
        msg = "An empty header value cannot represent a container"
        raise MalformedError(msg)
    return _collection(plan, [part.strip(_OWS) for part in combined.split(",")], exploded_pairs=plan.explode)


def _decode_cookie(plan: ParameterPlan, pairs: list[tuple[str, str]]) -> JSONValue | UNSET:
    if plan.shape == "object":
        return _exploded_object(plan, pairs)
    matching = [text for name, text in pairs if name == plan.name]
    if plan.shape == "array":
        return [typed(text, plan.kind) for text in matching] or UNSET
    return typed(matching[-1], plan.kind) if matching else UNSET


def _utf8(raw: bytes) -> str:
    try:
        return raw.decode()
    except UnicodeDecodeError:
        msg = "A parameter value must be UTF-8"
        raise MalformedError(msg) from None


def _decode_querystring(plan: ParameterPlan, raw: bytes | None) -> JSONValue | UNSET:
    if not raw:
        return UNSET
    if media_kind(plan.content_media_type or "") == "form":
        return decode_form(raw, plan.fields, plan.additional)
    return percent_decode(raw, plus=False)


def _decode_path_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | UNSET:
    if not fragments:
        return UNSET
    if plan.content_media_type is not None:
        return percent_decode(fragments[0].value, plus=False)
    return _decode_path(plan, fragments[0].value)


def _decode_query_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | UNSET:
    pairs = [(percent_decode(item.name or b"", plus=True), item.value) for item in fragments]
    if plan.content_media_type is None:
        return _decode_query(plan, pairs)
    if (value := _last([value for name, value in pairs if name == plan.name])) is UNSET:
        return UNSET
    return percent_decode(value, plus=True)


def _decode_header_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | UNSET:
    key = plan.name.lower().encode("latin-1")
    values = [item.value for item in fragments if (item.name or b"").lower() == key]
    if plan.content_media_type is None:
        return _decode_header(plan, values)
    if not values:
        return UNSET
    return _utf8(values[0]).strip(_OWS)


def _decode_cookie_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | UNSET:
    if plan.style == "form" and plan.shape != "scalar":
        pairs = [
            (percent_decode(item.name or b"", plus=False), percent_decode(item.value, plus=False)) for item in fragments
        ]
    else:
        pairs = [(_utf8(item.name or b""), _utf8(item.value)) for item in fragments]
    return _decode_cookie(plan, pairs)


def decode_parameter(plan: ParameterPlan, raw: RawParameter) -> JSONValue | UNSET:
    """Decode one parameter from its location's ordered raw occurrences, or UNSET when absent.

    A style value becomes builtins; content is returned as its text, which the model's backend reads as its media.
    """
    match plan.location:
        case "querystring":
            return _decode_querystring(plan, raw.raw_query)
        case "path":
            return _decode_path_value(plan, raw.fragments)
        case "query":
            return _decode_query_value(plan, raw.fragments)
        case "header":
            return _decode_header_value(plan, raw.fragments)
        case _:
            pass
    return _decode_cookie_value(plan, raw.fragments)


def split_query(raw: bytes | None) -> tuple[ParameterFragment, ...]:
    """Split raw query bytes into ordered name/value fragments, preserving duplicates."""
    return tuple(starmap(ParameterFragment, split_form(raw or b"")))


def split_cookies(headers: tuple[tuple[bytes, bytes], ...]) -> tuple[ParameterFragment, ...]:
    """Split every Cookie header into ordered name/value fragments, removing optional whitespace."""
    return tuple(
        ParameterFragment(name, value)
        for header, line in headers
        if header.lower() == b"cookie"
        for name, _, value in (pair.strip(b" \t").partition(b"=") for pair in line.split(b";") if pair.strip(b" \t"))
    )


def raw_parameter(plan: ParameterPlan, raw: RawParameters) -> RawParameter:
    """Select one plan's location view from an operation's raw parameters."""
    match plan.location:
        case "path":
            fragments = () if (captured := raw.path.get(plan.name)) is None else (ParameterFragment(None, captured),)
            return RawParameter(location="path", fragments=fragments)
        case "query":
            return RawParameter(location="query", fragments=split_query(raw.query), raw_query=raw.query)
        case "querystring":
            return RawParameter(location="querystring", raw_query=raw.query)
        case "header":
            return RawParameter(location="header", fragments=tuple(starmap(ParameterFragment, raw.headers)))
        case _:
            pass
    return RawParameter(location="cookie", fragments=split_cookies(raw.headers))
