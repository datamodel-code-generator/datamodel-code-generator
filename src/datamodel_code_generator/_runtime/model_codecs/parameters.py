"""OpenAPI parameter styles between raw HTTP fragments and decoded scalar values."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import starmap
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast
from urllib.parse import quote

from .errors import ParameterEncodingError, WireIssue, WireValidationError
from .media import (
    FieldPlan,
    JSONValue,
    LexicalKind,
    decode_form,
    decode_json,
    encode_form,
    encode_json,
    issue,
    lexical,
    media_kind,
    percent_decode,
    split_form,
    typed,
)
from .unset import UNSET

if TYPE_CHECKING:
    from .wire import WireValue

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
_HEADER_CONTROL: Final = re.compile(r"[\x00-\x08\x0A-\x1F\x7F]")
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


@dataclass(frozen=True, slots=True, kw_only=True)
class FragmentContribution:
    """Contribute ordered encoded fragments to one non-querystring location."""

    location: Literal["path", "query", "header", "cookie"]
    ordered_fragments: tuple[ParameterFragment, ...]
    kind: Literal["fragments"] = "fragments"


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryStringContribution:
    """Contribute a complete encoded query without its leading question mark."""

    raw_query: bytes
    kind: Literal["querystring"] = "querystring"


EncodedParameterContribution: TypeAlias = FragmentContribution | QueryStringContribution
_Entry: TypeAlias = tuple[str | None, str]


def _entries(plan: ParameterPlan, value: JSONValue | WireValue) -> list[_Entry]:
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


def _content(plan: ParameterPlan, value: JSONValue | WireValue) -> str:
    match media_kind(plan.content_media_type or ""), value:
        case "json", _:
            return encode_json(value, ascii_only=plan.location == "header").decode()
        case "text", str():
            return value
        case _:
            msg = "The parameter content media or value is not builtin"
            raise ParameterEncodingError(msg)


def _querystring_bytes(plan: ParameterPlan, value: JSONValue | WireValue) -> bytes:
    match media_kind(plan.content_media_type or ""):
        case "form":
            return encode_form(value, plan.fields, plan.additional)
        case _:
            return quote(_content(plan, value), safe="").encode("ascii")


def _encode_querystring(plan: ParameterPlan, value: JSONValue | WireValue) -> bytes:
    if raw := _querystring_bytes(plan, value):
        return raw
    msg = "An explicit querystring value cannot encode to an empty query"
    raise ParameterEncodingError(msg)


def _style_pairs(plan: ParameterPlan, value: JSONValue | WireValue) -> list[_Entry]:
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


def _content_pairs(plan: ParameterPlan, value: JSONValue | WireValue) -> list[_Entry]:
    text = _content(plan, value)
    match plan.location:
        case "path":
            return [(None, quote(text, safe=""))]
        case "query":
            return [(quote(plan.name, safe=""), quote(text, safe=""))]
        case _:
            return [(plan.name, _header_text(text, item=False))]


def encode_parameter(plan: ParameterPlan, value: object) -> EncodedParameterContribution:
    """Format one native JSON value into its location's ordered raw contribution."""
    value = cast("JSONValue | WireValue", value)
    if plan.location == "querystring":
        return QueryStringContribution(raw_query=_encode_querystring(plan, value))
    pairs = _pairs(plan, value)
    return FragmentContribution(
        location=plan.location,
        ordered_fragments=tuple(
            ParameterFragment(None if key is None else key.encode("ascii"), text.encode()) for key, text in pairs
        ),
    )


def path_text(plan: ParameterPlan, value: object) -> str:
    """Return the text a path parameter's value substitutes for its placeholder in the path template."""
    return "".join(text for _, text in _pairs(plan, cast("JSONValue | WireValue", value)))


def query_pairs(plan: ParameterPlan, value: object) -> tuple[str, ...]:
    """Return a query parameter's ordered name=value pairs as a query or URL-encoded form carries them."""
    return tuple(f"{key}={text}" for key, text in _pairs(plan, cast("JSONValue | WireValue", value)))


def part_pairs(plan: ParameterPlan, value: object) -> tuple[tuple[str, str], ...]:
    """Return a query parameter's ordered names and values as form-data parts carry them, without percent-encoding."""
    if not (entries := _entries(plan, cast("JSONValue | WireValue", value))):
        return ()
    return tuple((plan.name if key is None else key, text) for key, text in _encode_query(plan, entries, raw=True))


def _pairs(plan: ParameterPlan, value: JSONValue | WireValue) -> list[_Entry]:
    return (_style_pairs if plan.content_media_type is None else _content_pairs)(plan, value)


def encode_parameters(
    plans: Sequence[ParameterPlan], values: Mapping[tuple[ParameterLocation, str], WireValue | UNSET]
) -> tuple[EncodedParameterContribution, ...]:
    """Encode every present parameter in plan order, requiring each required value."""
    contributions: list[EncodedParameterContribution] = []
    for plan in plans:
        if (value := values.get((plan.location, plan.name), UNSET)) is not UNSET:
            contributions.append(encode_parameter(plan, value))
        elif plan.required:
            msg = f"The required {plan.location} parameter {plan.name!r} has no value"
            raise ParameterEncodingError(msg)
    return tuple(contributions)


def _object(plan: ParameterPlan, members: list[tuple[str, str]]) -> WireValue:
    declared = {field.name: field for field in plan.fields}
    result: dict[str, JSONValue] = {}
    for name, text in members:
        field = declared.get(name, plan.additional)
        result[name] = typed(text, "string" if field is None else field.kind)
    return result


def _collection(plan: ParameterPlan, parts: list[str], *, exploded_pairs: bool) -> WireValue:
    if plan.shape == "array":
        return tuple(typed(text, plan.kind) for text in parts)
    if exploded_pairs:
        return _paired(plan, [part.partition("=") for part in parts])
    if len(parts) % 2:
        raise issue(code="parameter.object", message="An object parameter has an unpaired member")
    return _object(plan, list(zip(parts[::2], parts[1::2], strict=True)))


def _paired(plan: ParameterPlan, members: list[tuple[str, str, str]]) -> WireValue:
    if not all(equals for _, equals, _ in members):
        raise issue(code="parameter.object", message="An exploded object member has no '=' separator")
    return _object(plan, [(name, text) for name, _, text in members])


def _parts(raw: bytes, delimiter: re.Pattern[bytes]) -> list[bytes]:
    if not raw:
        raise issue(code="parameter.empty", message="An empty delimited value cannot represent a container")
    return delimiter.split(raw)


def _split(raw: bytes, delimiter: re.Pattern[bytes], *, plus: bool) -> list[str]:
    return [percent_decode(part, plus=plus) for part in _parts(raw, delimiter)]


def _decode_path(plan: ParameterPlan, raw: bytes) -> WireValue:
    prefix = _PREFIXES.get(plan.style or "", b"")
    if prefix == b"." and raw.startswith((b"%2E", b"%2e")):
        prefix = raw[:3]
    if not raw.startswith(prefix):
        raise issue(code="parameter.syntax", message="A label or matrix path value is missing its prefix")
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


def _decode_matrix(plan: ParameterPlan, parts: list[bytes]) -> WireValue:
    members = [(percent_decode(name, plus=False), value) for name, _, value in (part.partition(b"=") for part in parts)]
    if plan.shape == "object" and plan.explode:
        return _object(plan, [(name, percent_decode(value, plus=False)) for name, value in members])
    if any(name != plan.name for name, _ in members):
        raise issue(code="parameter.syntax", message="A matrix path member has an unexpected name")
    if plan.shape == "array" and plan.explode:
        return tuple(typed(percent_decode(value, plus=False), plan.kind) for _, value in members)
    if plan.shape == "scalar":
        return typed(percent_decode(members[-1][1], plus=False), plan.kind)
    return _collection(plan, _split(members[-1][1], _COMMA, plus=False), exploded_pairs=False)


def _last(values: list[bytes]) -> bytes | UNSET:
    """Return the last of a repeated single value, as FastAPI reads query parameters, or UNSET when absent."""
    return values[-1] if values else UNSET


def _exploded_object(plan: ParameterPlan, pairs: list[tuple[str, str]]) -> WireValue | UNSET:
    declared = {item.name for item in plan.fields}
    members = [
        (name, text)
        for name, text in pairs
        if name in declared or (plan.additional is not None and name not in plan.reserved_names)
    ]
    return _object(plan, members) if members else UNSET


def _decode_query(plan: ParameterPlan, pairs: list[tuple[str, bytes]]) -> WireValue | UNSET:
    match plan.style, plan.shape, plan.explode:
        case "form", "array", True:
            return (
                tuple(typed(percent_decode(value, plus=True), plan.kind) for name, value in pairs if name == plan.name)
                or UNSET
            )
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


def _decode_header(plan: ParameterPlan, values: list[bytes]) -> WireValue | UNSET:
    """Decode the first value of a scalar header, as Starlette reads one, or every value of a container."""
    try:
        texts = [value.decode().strip(_OWS) for value in (values[:1] if plan.shape == "scalar" else values)]
    except UnicodeDecodeError:
        raise issue(code="parameter.encoding", message="A header value must be UTF-8") from None
    if not texts:
        return UNSET
    if plan.shape == "scalar":
        return typed(texts[0], plan.kind)
    if not (combined := ",".join(texts)):
        raise issue(code="parameter.empty", message="An empty header value cannot represent a container")
    return _collection(plan, [part.strip(_OWS) for part in combined.split(",")], exploded_pairs=plan.explode)


def _decode_cookie(plan: ParameterPlan, pairs: list[tuple[str, str]]) -> WireValue | UNSET:
    if plan.shape == "object":
        return _exploded_object(plan, pairs)
    matching = [text for name, text in pairs if name == plan.name]
    if plan.shape == "array":
        return tuple(typed(text, plan.kind) for text in matching) or UNSET
    return typed(matching[-1], plan.kind) if matching else UNSET


def _utf8(raw: bytes) -> str:
    try:
        return raw.decode()
    except UnicodeDecodeError:
        raise issue(code="parameter.encoding", message="A parameter value must be UTF-8") from None


def _decode_content(plan: ParameterPlan, text: str) -> WireValue:
    return decode_json(text.encode()) if media_kind(plan.content_media_type or "") == "json" else text


def _decode_querystring(plan: ParameterPlan, raw: bytes | None) -> WireValue | UNSET:
    if not raw:
        return UNSET
    if media_kind(plan.content_media_type or "") == "form":
        return decode_form(raw, plan.fields, plan.additional)
    return _decode_content(plan, percent_decode(raw, plus=False))


def _decode_path_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> WireValue | UNSET:
    if not fragments:
        return UNSET
    if plan.content_media_type is not None:
        return _decode_content(plan, percent_decode(fragments[0].value, plus=False))
    return _decode_path(plan, fragments[0].value)


def _decode_query_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> WireValue | UNSET:
    pairs = [(percent_decode(item.name or b"", plus=True), item.value) for item in fragments]
    if plan.content_media_type is None:
        return _decode_query(plan, pairs)
    if (value := _last([value for name, value in pairs if name == plan.name])) is UNSET:
        return UNSET
    return _decode_content(plan, percent_decode(value, plus=True))


def _decode_header_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> WireValue | UNSET:
    key = plan.name.lower().encode("latin-1")
    values = [item.value for item in fragments if (item.name or b"").lower() == key]
    if plan.content_media_type is None:
        return _decode_header(plan, values)
    if not values:
        return UNSET
    return _decode_content(plan, _utf8(values[0]).strip(_OWS))


def _decode_cookie_value(plan: ParameterPlan, fragments: tuple[ParameterFragment, ...]) -> WireValue | UNSET:
    if plan.style == "form" and plan.shape != "scalar":
        pairs = [
            (percent_decode(item.name or b"", plus=False), percent_decode(item.value, plus=False)) for item in fragments
        ]
    else:
        pairs = [(_utf8(item.name or b""), _utf8(item.value)) for item in fragments]
    return _decode_cookie(plan, pairs)


def decode_parameter(plan: ParameterPlan, raw: RawParameter) -> WireValue | UNSET:
    """Decode one parameter from its location's ordered raw occurrences, or UNSET when absent."""
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
            return RawParameter(location="cookie", fragments=split_cookies(raw.headers))


def decode_parameters(
    plans: Sequence[ParameterPlan], raw: RawParameters
) -> dict[tuple[ParameterLocation, str], WireValue | UNSET]:
    """Decode every declared parameter, reporting all missing and malformed values together."""
    values: dict[tuple[ParameterLocation, str], WireValue | UNSET] = {}
    issues: list[WireIssue] = []
    views: dict[ParameterLocation, RawParameter] = {}
    for plan in plans:
        view = raw_parameter(plan, raw) if plan.location == "path" else views.get(plan.location)
        if view is None:
            view = views[plan.location] = raw_parameter(plan, raw)
        subject = f"The {plan.location} parameter {plan.name!r}"[:512]
        try:
            value = decode_parameter(plan, view)
        except WireValidationError as error:
            issues.extend(replace(item, message=f"{subject}: {item.message}"[:1024]) for item in error.issues)
            continue
        if value is UNSET and plan.required:
            issues.append(
                WireIssue(
                    code="parameter.required",
                    message=f"{subject} is required",
                    instance_pointer="",
                    schema_id="",
                    schema_pointer="",
                )
            )
        values[plan.location, plan.name] = value
    if issues:
        raise WireValidationError(tuple(issues))
    return values
