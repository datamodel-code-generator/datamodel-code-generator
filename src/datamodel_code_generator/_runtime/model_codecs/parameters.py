"""OpenAPI parameter styles between raw HTTP fragments and decoded scalar values."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from itertools import starmap
from typing import Final, Literal, TypeAlias, cast
from urllib.parse import quote

from .errors import MalformedError, ParameterEncodingError
from .media import (
    FieldPlan,
    JSONValue,
    LexicalKind,
    decode_form,
    encode_form,
    json_bytes,
    lexical,
    media_kind,
    percent_decode,
    split_form,
    typed,
)
from .unset import UNSET, Unset

ParameterLocation: TypeAlias = Literal["path", "query", "querystring", "header", "cookie"]
ValueShape: TypeAlias = Literal["scalar", "array", "object"]

_RESERVED: Final = ":/?#[]@!$&'()*+,;="
_PATH_KEPT: Final = "/?#[],;="
_QUERY_KEPT: Final = "#&+=[],"
_TRIPLET: Final = re.compile(r"(%[0-9A-Fa-f]{2})")
_DELIMITERS: Final = {
    "spaceDelimited": (re.compile(rb"%20|\+| "), "%20", " "),
    "pipeDelimited": (re.compile(rb"%7[Cc]|\|"), "%7C", "|"),
}
_COMMA: Final = re.compile(rb",")
_DOT: Final = re.compile(rb"\.")
_PREFIXES: Final = {"label": b".", "matrix": b";"}
_STYLES: Final = {
    "path": frozenset({"simple", "label", "matrix"}),
    "query": frozenset({"form", "spaceDelimited", "pipeDelimited", "deepObject"}),
    "header": frozenset({"simple"}),
    "cookie": frozenset({"form", "cookie"}),
}
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_COOKIE_OCTETS: Final = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*")
_HEADER_CONTROL: Final = re.compile(r"[\x00-\x08\x0A-\x1F\x7F\ud800-\udfff]")
_OWS: Final = " \t"


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterPlan:
    """Describe one effective parameter: location, style or content media, and lexical kinds."""

    location: ParameterLocation
    name: str
    style: str | None = None
    explode: bool = False
    required: bool = False
    allow_reserved: bool = False
    content_media_type: str | None = None
    shape: ValueShape = "scalar"
    kind: LexicalKind = "string"
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    reserved_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject style, content, and shape combinations without a reversible builtin form.

        A cookie sent as it is, a single value or one of the cookie style, needs a name that is a token.
        """
        if not _reversible(self):
            content = "" if self.content_media_type is None else f" with content {self.content_media_type!r}"
            msg = f"{self.location} parameter style {self.style!r}{content} has no builtin form for {self.shape} values"
            raise ValueError(msg)
        if (
            self.location == "cookie"
            and (self.style == "cookie" or self.shape == "scalar")
            and not _TOKEN.fullmatch(self.name)
        ):
            msg = f"cookie parameter name {self.name!r} is not a token, which a cookie sent as it is needs"
            raise ValueError(msg)


def builtin_content(location: ParameterLocation, media_type: str) -> bool:
    """Return whether a builtin codec carries parameter content of this media type in this location."""
    match media_kind(media_type):
        case "json" | "text":
            return location != "cookie"
        case "form":
            return location == "querystring"
        case _:
            return False


def _reversible(plan: ParameterPlan) -> bool:
    if plan.content_media_type is not None:
        return plan.style is None and builtin_content(plan.location, plan.content_media_type)
    if plan.style is None or plan.style not in _STYLES.get(plan.location, ()):
        return False
    match plan.style, plan.shape, plan.explode:
        case "spaceDelimited" | "pipeDelimited", shape, explode:
            return shape != "scalar" and not explode
        case "deepObject", shape, explode:
            return shape == "object" and explode
        case _, "array" | "object", False:
            return plan.location != "cookie"
        case _:
            return True


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


_Entry: TypeAlias = tuple[str | None, str]


def _entries(plan: ParameterPlan, value: JSONValue) -> list[_Entry]:
    match plan.shape, value:
        case _, None:
            msg = "JSON null cannot be written by a style-based parameter"
            raise ParameterEncodingError(msg)
        case "scalar", _:
            return [(None, lexical(value, plan.kind))]
        case "array", tuple() | list():
            return [(None, lexical(item, plan.kind)) for item in value]
        case "object", Mapping():
            entries: list[_Entry] = []
            for key, item in value.items():
                entries.append((key, lexical(item, "string")))
            return entries
        case _:
            msg = "The value does not have the parameter's declared shape"
            raise ParameterEncodingError(msg)


def _percent(plan: ParameterPlan, kept: str) -> Callable[[str], str]:
    if not plan.allow_reserved:
        return lambda text: quote(text, safe="")
    safe = "".join(char for char in _RESERVED if char not in kept)
    return lambda text: "".join(
        part if index % 2 else quote(part, safe=safe) for index, part in enumerate(_TRIPLET.split(text))
    )


def _encoded(entries: list[_Entry], encode: Callable[[str], str]) -> list[_Entry]:
    return [(None if key is None else encode(key), encode(text)) for key, text in entries]


def _joined(entries: list[_Entry], *, separator: str = ",", pair: str = ",") -> str:
    if not (joined := separator.join(text if key is None else f"{key}{pair}{text}" for key, text in entries)):
        msg = "An empty delimited value cannot be distinguished from an empty container"
        raise ParameterEncodingError(msg)
    return joined


def _matrix(name: str, value: str) -> str:
    return f";{name}={value}" if value else f";{name}"


def _encode_path(plan: ParameterPlan, entries: list[_Entry]) -> str:
    encoded = _encoded(entries, encode := _percent(plan, _PATH_KEPT))
    name = encode(plan.name)
    match plan.style, plan.shape, plan.explode:
        case "simple", "scalar", _:
            text = encoded[0][1]
        case "simple", _, explode:
            text = _joined(encoded, pair="=" if explode else ",")
        case "label", "scalar", _:
            text = f".{encoded[0][1]}"
        case "label", _, True:
            if any("." in part for entry in encoded for part in entry if part is not None):
                msg = "An exploded label value cannot contain its '.' delimiter"
                raise ParameterEncodingError(msg)
            text = f".{_joined(encoded, separator='.', pair='=')}"
        case "label", _, False:
            text = f".{_joined(encoded)}"
        case "matrix", "scalar", _:
            text = _matrix(name, encoded[0][1])
        case "matrix", _, True:
            text = "".join(_matrix(name if key is None else key, value) for key, value in encoded)
        case _:
            text = f";{name}={_joined(encoded)}"
    return text


def _verbatim(text: str) -> str:
    return text


def _encode_query(plan: ParameterPlan, entries: list[_Entry], *, raw: bool = False) -> list[_Entry]:
    encode = _verbatim if raw else _percent(plan, _QUERY_KEPT)
    encoded = entries if raw else _encoded(entries, encode)
    name = encode(plan.name)
    match plan.style, plan.shape, plan.explode:
        case "form", "scalar", _:
            return [(name, encoded[0][1])]
        case "form", _, True:
            return [(name if key is None else key, text) for key, text in encoded]
        case ("spaceDelimited" | "pipeDelimited") as style, _, _:
            _, delimiter, character = _DELIMITERS[style]
            if any(character in part for entry in entries for part in entry if part is not None):
                msg = "A delimited query value cannot contain its own delimiter"
                raise ParameterEncodingError(msg)
            separator = character if raw else delimiter
            return [(name, _joined(encoded, separator=separator, pair=separator))]
        case "deepObject", _, _:
            return [(encode(f"{plan.name}[{key}]"), text) for (key, _), (_, text) in zip(entries, encoded, strict=True)]
        case _:
            return [(name, _joined(encoded))]


def _header_text(text: str, *, item: bool) -> str:
    if _HEADER_CONTROL.search(text) or text != text.strip(_OWS) or (item and "," in text):
        msg = "A header value contains control characters, surrounding whitespace, or a list delimiter"
        raise ParameterEncodingError(msg)
    return text


def _encode_header(plan: ParameterPlan, entries: list[_Entry]) -> str:
    if plan.shape == "scalar":
        return _header_text(entries[0][1], item=False)
    checked = [
        (None if key is None else _header_text(key, item=True), _header_text(text, item=True)) for key, text in entries
    ]
    if plan.explode and any("=" in key for key, _ in checked if key is not None):
        msg = "An exploded header member name cannot contain '='"
        raise ParameterEncodingError(msg)
    return _joined(checked, pair="=" if plan.explode else ",")


def _encode_cookie(plan: ParameterPlan, entries: list[_Entry]) -> list[_Entry]:
    pairs = [(plan.name if key is None else key, text) for key, text in entries]
    if plan.style == "form" and plan.shape != "scalar":
        return [(quote(key, safe=""), quote(text, safe="")) for key, text in pairs]
    if not all(_TOKEN.fullmatch(key) and _COOKIE_OCTETS.fullmatch(text) for key, text in pairs):
        msg = "A cookie name must be a token and its value unquoted cookie octets"
        raise ParameterEncodingError(msg)
    return list(pairs)


def _content(plan: ParameterPlan, value: JSONValue) -> str:
    match media_kind(plan.content_media_type or ""), value:
        case "json", _:
            text = json_bytes(value).decode()
            return text if plan.location != "header" or text.isascii() else json_bytes(value, ascii_only=True).decode()
        case "text", str():
            return value
        case _:
            msg = "The parameter content media or value is not builtin"
            raise ParameterEncodingError(msg)


def _querystring_text(plan: ParameterPlan, value: JSONValue) -> str:
    match media_kind(plan.content_media_type or ""):
        case "form":
            return encode_form(value, plan.fields, plan.additional).decode("ascii")
        case _:
            return quote(_content(plan, value), safe="")


def querystring(plan: ParameterPlan, value: object) -> str:
    """Return a querystring parameter's whole encoded query, without its leading question mark."""
    if text := _querystring_text(plan, cast("JSONValue", value)):
        return text
    msg = "An explicit querystring value cannot encode to an empty query"
    raise ParameterEncodingError(msg)


def _style_pairs(plan: ParameterPlan, value: JSONValue) -> list[_Entry]:
    """Return a style's pairs of a value; an empty array or object writes none, as an omitted value does.

    A path segment cannot be omitted, so a path parameter refuses an empty array or object.
    """
    if not (entries := _entries(plan, value)):
        if plan.location == "path":
            msg = "An empty array or object cannot fill a path segment"
            raise ParameterEncodingError(msg)
        return []
    match plan.location:
        case "path":
            return [(None, _encode_path(plan, entries))]
        case "query":
            return _encode_query(plan, entries)
        case "header":
            return [(plan.name, _encode_header(plan, entries))]
        case _:
            return _encode_cookie(plan, entries)


def _content_pairs(plan: ParameterPlan, value: JSONValue) -> list[_Entry]:
    text = _content(plan, value)
    match plan.location:
        case "path":
            return [(None, quote(text, safe=""))]
        case "query":
            return [(quote(plan.name, safe=""), quote(text, safe=""))]
        case _:
            return [(plan.name, _header_text(text, item=False))]


def pairs(plan: ParameterPlan, value: object) -> list[tuple[str | None, str]]:
    """Return a parameter's ordered encoded names and texts in its location: the style's pairs, or its content."""
    value = cast("JSONValue", value)
    return (_style_pairs if plan.content_media_type is None else _content_pairs)(plan, value)


def path_text(plan: ParameterPlan, value: object) -> str:
    """Return the text a path parameter's value substitutes for its placeholder in the path template."""
    return "".join(text for _, text in pairs(plan, value))


def query_pairs(plan: ParameterPlan, value: object) -> tuple[str, ...]:
    """Return a query or cookie parameter's ordered name=value pairs as a query, form, or Cookie header carries them."""
    return tuple(f"{key}={text}" for key, text in pairs(plan, value))


def part_pairs(plan: ParameterPlan, value: object) -> tuple[tuple[str, str], ...]:
    """Return a query parameter's ordered names and values as form-data parts carry them, without percent-encoding."""
    if not (entries := _entries(plan, cast("JSONValue", value))):
        return ()
    return tuple((plan.name if key is None else key, text) for key, text in _encode_query(plan, entries, raw=True))


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


def _last(values: list[bytes]) -> bytes | Unset:
    """Return the last of a repeated single value, as FastAPI reads query parameters, or UNSET when absent."""
    return values[-1] if values else UNSET


def _exploded_object(plan: ParameterPlan, pairs: list[tuple[str, str]]) -> JSONValue | Unset:
    declared = {item.name for item in plan.fields}
    members = [
        (name, text)
        for name, text in pairs
        if name in declared or (plan.additional is not None and name not in plan.reserved_names)
    ]
    return _object(plan, members) if members else UNSET


def _decode_query(plan: ParameterPlan, pairs: list[tuple[str, bytes]]) -> JSONValue | Unset:
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
    delimiter = _DELIMITERS[plan.style][0] if plan.style in _DELIMITERS else _COMMA
    return _collection(plan, _split(value, delimiter, plus=True), exploded_pairs=False)


def _decode_header(plan: ParameterPlan, values: list[bytes]) -> JSONValue | Unset:
    try:
        texts = [value.decode().strip(_OWS) for value in values]
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


def _decode_cookie(plan: ParameterPlan, pairs: list[tuple[str, str]]) -> JSONValue | Unset:
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


def _decode_querystring(plan: ParameterPlan, raw: bytes | None) -> JSONValue | Unset:
    if not raw:
        return UNSET
    if media_kind(plan.content_media_type or "") == "form":
        return decode_form(raw, plan.fields, plan.additional)
    return percent_decode(raw, plus=False)


def _decode_path_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | Unset:
    if not fragments:
        return UNSET
    if plan.content_media_type is not None:
        return percent_decode(fragments[0].value, plus=False)
    return _decode_path(plan, fragments[0].value)


def _decode_query_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | Unset:
    pairs = [(percent_decode(item.name or b"", plus=True), item.value) for item in fragments]
    if plan.content_media_type is None:
        return _decode_query(plan, pairs)
    if (value := _last([value for name, value in pairs if name == plan.name])) is UNSET:
        return UNSET
    return percent_decode(value, plus=True)


def _decode_header_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | Unset:
    key = plan.name.lower().encode("latin-1")
    values = [item.value for item in fragments if (item.name or b"").lower() == key]
    if plan.content_media_type is None:
        return _decode_header(plan, values)
    if not values:
        return UNSET
    return _utf8(values[0]).strip(_OWS)


def _decode_cookie_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> JSONValue | Unset:
    if plan.style == "form" and plan.shape != "scalar":
        pairs = [
            (percent_decode(item.name or b"", plus=False), percent_decode(item.value, plus=False)) for item in fragments
        ]
    else:
        pairs = [(_utf8(item.name or b""), _utf8(item.value)) for item in fragments]
    return _decode_cookie(plan, pairs)


def decode_parameter(plan: ParameterPlan, raw: RawParameter) -> JSONValue | Unset:
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
            return RawParameter(location="cookie", fragments=split_cookies(raw.headers))
