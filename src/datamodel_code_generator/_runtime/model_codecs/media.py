"""Media-type identity plus reversible JSON, text, and URL-encoded form representations."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from json.encoder import encode_basestring, encode_basestring_ascii
from typing import TYPE_CHECKING, Final, Literal, NoReturn, cast, overload
from urllib.parse import quote, unquote_to_bytes

from .errors import CodecResourceLimitError, ParameterEncodingError, WireIssue, WireValidationError
from .wire import JSONScalar as WireScalar
from .wire import JSONValue as WireJSON
from .wire import WireValue, checked_key, checked_scalar, enter, freeze_wire

if TYPE_CHECKING:
    from .plain import JSONValue

LexicalKind = Literal["string", "integer", "number", "boolean"]
MediaKind = Literal["json", "text", "form", "multipart", "binary"]

_HTTP_WORD: Final = r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+"
_MEDIA: Final = re.compile(rf"[ \t]*(?P<type>{_HTTP_WORD})/(?P<subtype>{_HTTP_WORD})[ \t]*")
_PARAMETER: Final = re.compile(
    rf";[ \t]*(?P<name>{_HTTP_WORD})=(?P<value>{_HTTP_WORD}|"
    r'"(?:[\t !#-\[\]-~\x80-\xff]|\\[\t -~\x80-\xff])*")[ \t]*'
)
_INTEGER: Final = re.compile(r"-?(?:0|[1-9][0-9]*)")
_NUMBER: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_TRIPLET: Final = re.compile(rb"%[0-9A-Fa-f]{2}")
_FORM_SAFE: Final = "*-._"


@dataclass(frozen=True, slots=True)
class FieldPlan:
    """Declare one form or object member's lexical kind and whether it repeats."""

    name: str
    kind: LexicalKind = "string"
    repeated: bool = False


def json_bytes(value: object) -> bytes:
    """Serialize ordinary JSON builtins as compact UTF-8 bytes."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def json_value(content: str | bytes) -> JSONValue:
    """Parse ordinary JSON into its builtin values without model validation."""
    return cast("JSONValue", json.loads(content))


def plain(value: object) -> JSONValue:
    """Turn parsed form and header containers into builtins a native backend accepts."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {cast("str", key): plain(item) for key, item in cast("Mapping[object, object]", value).items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in cast("list[object] | tuple[object, ...]", value)]
    return cast("JSONValue", value)


def issue(*, code: str, message: str) -> WireValidationError:
    """Build a value-free lexical failure for a whole media or parameter value."""
    return WireValidationError((
        WireIssue(code=code, message=message, instance_pointer="", schema_id="", schema_pointer=""),
    ))


def normalize_media_type(value: str) -> str:
    """Return lowercase type/subtype and parameter names, keeping parameter order and values."""
    if (essence := _MEDIA.match(value)) is None:
        msg = "A media type must be a token/token essence"
        raise ValueError(msg)
    parts = [f"{essence['type'].lower()}/{essence['subtype'].lower()}"]
    position = essence.end()
    while position < len(value):
        if (parameter := _PARAMETER.match(value, position)) is None:
            msg = "A media type parameter must be a token=value pair"
            raise ValueError(msg)
        parts.append(f"{parameter['name'].lower()}={parameter['value']}")
        position = parameter.end()
    return "; ".join(parts)


def media_kind(media_type: str) -> MediaKind:
    """Classify a normalized media type by its builtin representation."""
    essence = media_type.partition(";")[0]
    kind, _, subtype = essence.partition("/")
    match kind, subtype:
        case "application", "json":
            return "json"
        case _, _ if subtype.endswith("+json"):
            return "json"
        case "application", "x-www-form-urlencoded":
            return "form"
        case "multipart", _:
            return "multipart"
        case "text", _:
            return "text"
        case _:
            return "binary"


class _DuplicateKeyError(ValueError):
    pass


class _NonFiniteNumberError(ValueError):
    pass


class _IntegerLimitError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, WireJSON]]) -> dict[str, WireJSON]:
    if len(result := dict(pairs)) != len(pairs):
        raise _DuplicateKeyError
    return result


def _reject_constant(_: str) -> NoReturn:
    raise _NonFiniteNumberError


def _integer(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise _IntegerLimitError from None


def decode_json(data: bytes) -> WireValue:
    """Parse UTF-8 JSON text into a wire snapshot, keeping exact number lexemes."""
    try:
        parsed: WireJSON = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=Decimal,
            parse_int=_integer,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as failure:
        error = _json_error(failure)
    else:
        try:
            return freeze_wire(parsed)
        except RecursionError:
            error = _nesting_limit()
        except ValueError:
            error = issue(code="json.unicode", message="JSON strings must not contain lone surrogates")
    raise error from None


def _json_error(error: ValueError | RecursionError) -> WireValidationError | CodecResourceLimitError:
    """Classify a JSON parsing failure without keeping its document or exception chain."""
    match error:
        case _DuplicateKeyError():
            return issue(code="json.duplicate_key", message="A JSON object repeats a member name")
        case _NonFiniteNumberError():
            return issue(code="json.non_finite_number", message="JSON numbers must be finite")
        case _IntegerLimitError():
            return CodecResourceLimitError("A JSON integer exceeds the interpreter's decimal conversion limit")
        case UnicodeDecodeError():
            return issue(code="json.encoding", message="JSON text must be UTF-8")
        case RecursionError():
            return _nesting_limit()
        case _:
            pass
    return issue(code="json.syntax", message="The body is not valid JSON")


def _nesting_limit() -> CodecResourceLimitError:
    """Return the resource limit shared by JSON parsing and wire freezing."""
    return CodecResourceLimitError("JSON text is nested beyond the interpreter recursion limit")


@overload
def encode_json(value: WireJSON, *, ascii_only: bool = False) -> bytes: ...
@overload
def encode_json(value: WireValue, *, ascii_only: bool = False) -> bytes: ...
def encode_json(value: WireJSON | WireValue, *, ascii_only: bool = False) -> bytes:
    """Serialize a JSON-domain value as compact UTF-8 JSON with exact numbers, checking it while writing."""
    return _json_text(value, encode_basestring_ascii if ascii_only else encode_basestring, set()).encode()


def _json_text(value: WireJSON | WireValue, strings: Callable[[str], str], active: set[int]) -> str:
    if isinstance(value, (list, tuple)):
        identity = enter(value, active)
        text = f"[{','.join([_json_text(item, strings, active) for item in value])}]"
        active.discard(identity)
        return text
    if isinstance(value, Mapping):
        identity = enter(value, active)
        members = [f"{strings(checked_key(key))}:{_json_text(item, strings, active)}" for key, item in value.items()]
        active.discard(identity)
        return f"{{{','.join(members)}}}"
    return _json_scalar(checked_scalar(value), strings)


def _json_scalar(value: WireScalar, strings: Callable[[str], str]) -> str:
    match value:
        case None:
            return "null"
        case bool() as flag:
            return "true" if flag else "false"
        case str() as string:
            return strings(string)
        case int() as number:
            return _integer_text(number)
        case float() as number:
            return repr(number)
        case number:
            return str(number)


def charset(media_type: str) -> str:
    """Return a normalized media type's charset, defaulting to UTF-8."""
    if ";" not in media_type:
        return "utf-8"
    for parameter in _PARAMETER.finditer(media_type):
        if parameter["name"] == "charset":
            return parameter["value"].strip('"')
    return "utf-8"


def decode_text(data: bytes, encoding: str = "utf-8") -> str:
    """Decode a text body in its selected charset without normalizing its contents."""
    try:
        return data.decode(encoding)
    except (LookupError, UnicodeError):
        message = "Text must be UTF-8" if encoding == "utf-8" else f"Text must be {encoding}"
        raise issue(code="text.encoding", message=message) from None


def lexical(value: object, kind: LexicalKind) -> str:
    """Format one native JSON scalar for a parameter or form field."""
    if (text := _lexical_text(value, kind)) is None:
        msg = f"The {type(value).__name__} value does not have the {kind} lexical kind"
        raise ParameterEncodingError(msg)
    return text


def _lexical_text(value: object, kind: LexicalKind) -> str | None:
    match value:
        case bool():
            return "true" if value else "false"
        case str():
            return value
        case int() | float() | Decimal():
            return _numeral(value, kind) or _numeral(value, "number")
        case _:
            return None


def _numeral(value: float | Decimal, kind: LexicalKind) -> str | None:
    match value:
        case int() if kind in {"integer", "number"}:
            return _integer_text(value)
        case float() if kind == "integer" and value.is_integer():
            return str(int(value))
        case Decimal() if kind == "integer" and value == (integral := value.to_integral_value()):
            return format(integral, "f")
        case Decimal() if kind == "number":
            return str(value)
        case float() if kind == "number":
            return repr(value)
        case _:
            return None


def _integer_text(value: int) -> str:
    try:
        return str(value)
    except ValueError:
        msg = "An integer exceeds the interpreter's decimal conversion limit"
        raise CodecResourceLimitError(msg) from None


def typed(text: str, kind: LexicalKind) -> WireScalar:
    """Read one canonical lexical form back into its declared JSON scalar kind."""
    if kind == "string":
        return text
    if kind == "boolean" and text in {"true", "false"}:
        return text == "true"
    if kind != "boolean" and _INTEGER.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            msg = "An integer exceeds the interpreter's decimal conversion limit"
            raise CodecResourceLimitError(msg) from None
    if kind == "number" and _NUMBER.fullmatch(text):
        return Decimal(text)
    raise issue(code="parameter.lexical", message=f"The value is not a canonical {kind}")


def percent_decode(raw: bytes, *, plus: bool) -> str:
    """Decode percent triplets exactly once and require the result to be UTF-8."""
    if plus:
        raw = raw.replace(b"+", b" ")
    if raw.count(b"%") != len(_TRIPLET.findall(raw)):
        raise issue(code="parameter.percent", message="A percent sign must start a percent-encoded octet")
    try:
        return unquote_to_bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise issue(code="parameter.encoding", message="Percent-decoded octets must be UTF-8") from None


def form_encode(text: str) -> str:
    """Apply the application/x-www-form-urlencoded byte serializer to one name or value."""
    return quote(text, safe=_FORM_SAFE + " ").replace(" ", "+").replace("~", "%7E")


def encode_form(
    value: object,
    fields: tuple[FieldPlan, ...],
    additional: FieldPlan | None,
    styled: Mapping[str, Callable[[WireValue], Iterable[str]]] | None = None,
) -> bytes:
    """Serialize a flat object as ordered URL-encoded pairs, repeating array members.

    A member in `styled` writes its own pairs, as its encoding's query parameter style or content does, and no two
    members may then write the same name.
    """
    if not isinstance(value, Mapping):
        msg = "A URL-encoded form value must be an object"
        raise ParameterEncodingError(msg)
    declared = {field.name: field for field in fields}
    pairs: list[str] = []
    owners: dict[str, str] = {}
    for name, item in cast("Mapping[str, WireJSON | WireValue]", value).items():
        if styled and (style := styled.get(name)) is not None:
            written = list(style(cast("WireValue", item)))
            keys = [percent_decode(pair.partition("=")[0].encode("ascii"), plus=True) for pair in written]
        else:
            field = declared.get(name, additional)
            kind = "string" if field is None else field.kind
            written = [f"{form_encode(name)}={form_encode(lexical(member, kind))}" for member in _members(item)]
            keys = [name]
        if styled and any(owners.setdefault(key, name) != name for key in keys):
            msg = "Two URL-encoded form members write the same name"
            raise ParameterEncodingError(msg)
        pairs.extend(written)
    return "&".join(pairs).encode("ascii")


def _members(value: WireJSON | WireValue) -> tuple[WireJSON | WireValue, ...]:
    return tuple(value) if isinstance(value, (list, tuple)) else (value,)


def split_form(raw: bytes) -> tuple[tuple[bytes, bytes], ...]:
    """Split URL-encoded bytes into ordered raw name/value pairs, skipping empty sequences."""
    return tuple((name, value) for name, _, value in (part.partition(b"=") for part in raw.split(b"&") if part))


def decode_form(raw: bytes, fields: tuple[FieldPlan, ...], additional: FieldPlan | None) -> WireValue:
    """Read ordered URL-encoded pairs into a flat object, rejecting duplicate scalars."""
    declared = {field.name: field for field in fields}
    result: dict[str, WireJSON] = {}
    for raw_name, raw_value in split_form(raw):
        name = percent_decode(raw_name, plus=True)
        if (field := declared.get(name, additional)) is None:
            raise issue(code="form.undeclared", message="A URL-encoded form member is not declared")
        value = typed(percent_decode(raw_value, plus=True), field.kind)
        match result.get(name):
            case list() as values:
                values.append(value)
            case None if field.repeated:
                result[name] = [value]
            case None:
                result[name] = value
            case _:
                raise issue(code="form.duplicate", message="A URL-encoded form repeats a single-valued member")
    return freeze_wire(result)
