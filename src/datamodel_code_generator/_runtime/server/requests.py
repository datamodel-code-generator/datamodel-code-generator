"""Request adapters: dependencies that read what FastAPI cannot, then validate it with the model's TypeAdapter."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, TypeAlias, cast
from urllib.parse import quote, unquote_to_bytes

from fastapi.exceptions import RequestValidationError
from pydantic import Field, TypeAdapter, ValidationError
from starlette.datastructures import UploadFile
from starlette.requests import Request  # noqa: TC002 - FastAPI resolves the dependencies' annotations.
from typing_extensions import TypeVar

from ..model_codecs.errors import MalformedError
from ..model_codecs.media import (
    FieldPlan,
    charset,
    decode_form,
    decode_text,
    media_kind,
    normalize_media_type,
    plain,
    typed,
)
from ..model_codecs.parameter_reads import (
    ParameterFragment,
    RawParameter,
    RawParameters,
    decode_parameter,
    raw_parameter,
    split_query,
)
from ..model_codecs.unset import UNSET
from .application import ParameterReader, Rejected, malformed_request, reject
from .errors import invalid, media_invalid, missing, model_records, unsupported_media

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic.fields import FieldInfo

    from ..model_codecs.media import JSONValue
    from ..model_codecs.parameters import ParameterPlan
    from .errors import Record

RequestKind: TypeAlias = Literal["json", "text", "binary", "form", "multipart"]
T = TypeVar("T")

_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_HEX: Final = {digit: f"[{digit}{digit.lower()}]" for digit in "ABCDEF"}
_PATH_SAFE: Final = "/:@!$&'()*+,;="
_TEXT: Final = FieldPlan("")
_ANY: Final[TypeAdapter[object]] = TypeAdapter(object)
_JSON_WHITESPACE: Final = b" \t\r\n"


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterArgument:
    """One adapter parameter: the handler keyword, its HTTP plan, its model's adapter, and the model's default."""

    name: str
    plan: ParameterPlan
    adapter: TypeAdapter[object] | None = None
    default: object = field(default=None, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RawPath:
    """Find the named slots of a route template in the raw request path, which ends with the template.

    Literal template text matches in any percent-encoding, as the decoded path FastAPI routes by does.
    """

    template: str
    names: frozenset[str]
    patterns: tuple[re.Pattern[bytes], re.Pattern[bytes]] = field(init=False, repr=False, compare=False)
    slots: tuple[tuple[str, str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Compile the template's patterns once: its usual encoding first, then any percent-encoding."""
        pieces = _PLACEHOLDER.split(self.template)
        patterns = tuple(_pattern(pieces[::2], literal) for literal in (_encoded, _any_encoding))
        object.__setattr__(self, "patterns", patterns)
        slots = tuple((f"s{index}", name) for index, name in enumerate(pieces[1::2]) if name in self.names)
        object.__setattr__(self, "slots", slots)

    def search(self, raw: bytes) -> re.Match[bytes] | None:
        """Return the slots' match at the end of a raw path."""
        usual, any_encoding = self.patterns
        return usual.search(raw) or any_encoding.search(raw)


def _pattern(literals: list[str], literal: Callable[[str], bytes]) -> re.Pattern[bytes]:
    *heads, last = literals
    groups = b"".join(literal(head) + b"(?P<s%d>[^/]+)" % index for index, head in enumerate(heads))
    return re.compile(groups + literal(last) + rb"\Z")


def _encoded(text: str) -> bytes:
    return re.escape(quote(text, safe=_PATH_SAFE).encode())


def _any_encoding(text: str) -> bytes:
    return b"".join(
        b"(?:%s|%%%s)"
        % (re.escape(bytes((byte,))), "".join(_HEX.get(digit, digit) for digit in f"{byte:02X}").encode())
        for byte in text.encode()
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterAdapter(ParameterReader):
    """Read an operation's adapter parameters once per request from the raw ASGI path, query, and headers."""

    arguments: tuple[ParameterArgument, ...]
    record: Callable[..., object]
    path: RawPath | None = None

    async def __call__(self, request: Request) -> object:
        """Return the operation's record of adapter parameter values, with their defaults or None for omitted ones.

        Values the declarations reject leave their records on the request instead, and the Rejected value that holds
        them stands for the record: the route reports them with FastAPI's own, and `accepted` raises them alone.
        """
        scope = request.scope
        records: list[Record] = []
        raw = RawParameters(
            path=self._path(scope, self.path, records) if self.path is not None else {},
            query=scope.get("query_string"),
            headers=tuple(scope.get("headers", ())),
        )
        if not records:
            values = self._values(raw, records)
            if not records:
                return self.record(**values)
        return reject(scope, records)

    def _values(self, raw: RawParameters, records: list[Record]) -> dict[str, object]:
        """Return the validated value of each adapter parameter, adding the records of the rejected ones."""
        values: dict[str, object] = {}
        views: dict[str, RawParameter] = {}
        for argument in self.arguments:
            plan = argument.plan
            if (view := views.get(plan.location)) is None:
                view = raw_parameter(plan, raw)
                if plan.location != "path":
                    views[plan.location] = view
            location = (plan.location, plan.name)
            try:
                wire = decode_parameter(plan, view)
            except MalformedError as error:
                raise malformed_request() from error
            if wire is UNSET:
                if plan.required:
                    records.append(missing(location))
                else:
                    values[argument.name] = deepcopy(argument.default)
                continue
            try:
                values[argument.name] = (
                    (argument.adapter or _ANY).validate_json(cast("str", wire))
                    if plan.content_media_type is not None and media_kind(plan.content_media_type) == "json"
                    else _validated(argument.adapter, plain(wire))
                )
            except ValidationError as error:
                records.extend(model_records(error, location))
        return values

    @staticmethod
    def _path(scope: Mapping[str, object], path: RawPath, records: list[Record]) -> dict[str, bytes]:
        """Return the raw value of each named slot, adding the record of a repeated slot whose values differ."""
        if not isinstance(raw := scope.get("raw_path"), bytes):
            decoded = scope.get("path")
            raw = quote(decoded, safe=_PATH_SAFE).encode() if isinstance(decoded, str) else b""
        if (found := path.search(raw.partition(b"?")[0])) is None:
            raise malformed_request()
        captured: dict[str, bytes] = {}
        for group, name in path.slots:
            value = found.group(group)
            if (first := captured.setdefault(name, value)) is not value and unquote_to_bytes(first) != unquote_to_bytes(
                value
            ):
                records.append(invalid(("path", name)))
        return captured


def accepted(parameters: T) -> T:
    """Return an endpoint's adapter parameter record, or raise the records of the Rejected value standing for it.

    The endpoint runs only when FastAPI found no records of its own, so these are the request's only ones.
    """
    if isinstance(parameters, Rejected):
        raise RequestValidationError(parameters.take())
    return parameters


def _validated(adapter: TypeAdapter[object] | None, value: object) -> object:
    return value if adapter is None else adapter.validate_python(value)


def field_adapter(model: type[object], name: str) -> TypeAdapter[object]:
    """Return the adapter of a field of a Pydantic model or dataclass, which validates a value as the field does.

    The field's constraints and its discriminator apply, so a tagged union rejects a value by its tag.
    """
    owner: Any = model
    found: FieldInfo = owner.__pydantic_fields__[name]
    annotated: Any = Annotated
    return TypeAdapter[object](annotated[(found.annotation, *found.metadata, Field(discriminator=found.discriminator))])


@dataclass(frozen=True, slots=True)
class JsonPart:
    """One multipart member whose parts carry JSON: its name, the adapter of its value or items, and its repeats.

    The one that reads a form's other properties has no name. An untyped member takes any value, so it keeps a part
    that is not JSON as it comes, as a text member does.
    """

    name: str
    adapter: TypeAdapter[object]
    repeated: bool = False
    untyped: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class BodyMedia:
    """One declared request media type, how the adapter reads it, and the model's adapter.

    A form's members are read as text in the kinds of `fields`, and those of a multipart form in `json_parts` as JSON.
    A URL-encoded member whose encoding gives it a style or content is read by its query parameter plan in `encoded`
    from the pairs its style names, and JSON content then by its JSON part. `additional` reads every member neither
    names: as text as it comes unless the form's schema types its other properties, and never as JSON in a
    URL-encoded form, whose other members `other` reads. `checked` validates such text, as a mapping of their type,
    when the model keeps them without one. A binary body has a model's adapter only when its models constrain its
    bytes.
    """

    media_type: str
    kind: RequestKind
    adapter: TypeAdapter[object] | None = None
    fields: tuple[FieldPlan, ...] = ()
    json_parts: tuple[JsonPart, ...] = ()
    additional: FieldPlan | JsonPart = _TEXT
    checked: TypeAdapter[object] | None = None
    encoded: tuple[ParameterPlan, ...] = ()
    members: Mapping[str, FieldPlan | JsonPart] = field(init=False, repr=False, compare=False)
    other: FieldPlan = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Index the members of a multipart form by name once, and keep the text plan of any other member."""
        members: tuple[FieldPlan | JsonPart, ...] = (*self.fields, *self.json_parts)
        object.__setattr__(self, "members", {item.name: item for item in members})
        object.__setattr__(self, "other", self.additional if isinstance(self.additional, FieldPlan) else _TEXT)


@dataclass(frozen=True, slots=True, kw_only=True)
class BodyAdapter:
    """Read a request body FastAPI cannot read through its declared media type, then validate it with its model."""

    media: tuple[BodyMedia, ...]
    required: bool = True
    _essences: tuple[tuple[str, BodyMedia], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Index the declared media by essence, most specific first: exact types, then type/*, then */*.

        Within each, a declaration without parameters comes before one with parameters the request did not name.
        """
        essences = [(item.media_type.partition(";")[0], item) for item in self.media]
        object.__setattr__(
            self, "_essences", tuple(sorted(essences, key=lambda pair: (pair[0].count("*"), ";" in pair[1].media_type)))
        )

    async def __call__(self, request: Request) -> object:
        """Return the validated body, or None for an omitted optional body."""
        return (await self.receive(request))[1]

    async def receive(self, request: Request) -> tuple[str | None, object]:
        """Return the received media type with the validated body, or None for both of an omitted optional body.

        An empty body is omitted when it names no media type or names JSON, which is never empty. A JSON `null` that
        the model refuses is read as FastAPI reads it natively: a missing required body, or an omitted optional one.
        """
        header = request.headers.get("content-type")
        body = await request.body()
        if not body and (header is None or not self.required):
            if self.required:
                raise RequestValidationError([missing(("body",))])
            return None, None
        if (selected := self._select(header)) is None:
            raise unsupported_media()
        media, media_type, received_type = selected
        if not body and media.kind == "json":
            raise RequestValidationError([missing(("body",))])
        try:
            return media_type, await _read(media, received_type, body, request)
        except MalformedError as error:
            raise malformed_request() from error
        except ValidationError as error:
            if media.kind != "json" or body.strip(_JSON_WHITESPACE) != b"null":
                raise RequestValidationError(model_records(error, ("body",))) from error
            if self.required:
                raise RequestValidationError([missing(("body",))]) from error
            return None, None

    def _select(self, header: str | None) -> tuple[BodyMedia, str, str] | None:
        try:
            normalized = normalize_media_type(header or "")
        except ValueError:
            return None
        if (exact := next((media for media in self.media if media.media_type == normalized), None)) is not None:
            return exact, exact.media_type, normalized
        received = normalized.partition(";")[0]
        kind = received.partition("/")[0]
        for essence, media in self._essences:
            if essence in {received, f"{kind}/*", "*/*"}:
                return media, media.media_type if "*" not in essence else received, normalized
        return None


async def _read(media: BodyMedia, media_type: str, body: bytes, request: Request) -> object:
    """Return a body read as its media's kind says and validated by its model; raw bytes stay as they came."""
    adapter = media.adapter
    match media.kind:
        case "json":
            return (adapter or _ANY).validate_json(body)
        case "text":
            try:
                text = decode_text(body, charset(media_type))
            except MalformedError as error:
                raise RequestValidationError([media_invalid(("body",))]) from error
            return _validated(adapter, text)
        case "form" if media.encoded:
            return _validated(adapter, _checked(media, plain(_styled_form(media, body))))
        case "form":
            return _validated(adapter, _checked(media, plain(decode_form(body, media.fields, media.other))))
        case "multipart":
            return _validated(
                adapter, _checked(media, plain(await _parts(request, media.members, media.additional, media.encoded)))
            )
        case "binary" if adapter is not None:
            adapter.validate_python(body)
        case _:
            pass
    return body


def _checked(media: BodyMedia, values: JSONValue) -> JSONValue:
    """Return a form's members with those no plan names validated by `checked`, refusing the values it rejects."""
    if (checked := media.checked) is None:
        return values
    members = cast("dict[str, object]", values)
    encoded = {plan.name for plan in media.encoded}
    others = {name: value for name, value in members.items() if name not in media.members and name not in encoded}
    members.update(cast("dict[str, object]", checked.validate_python(others)))
    return values


def _styled_form(media: BodyMedia, body: bytes) -> dict[str, object]:
    """Return a URL-encoded form whose encoded members are read as a query reads parameters of their plans.

    Such a member takes the pairs its style names, as the client writes them, and its JSON content is parsed by its
    member's adapter; the other pairs are read as a form without such members is. The request is refused with the
    records of every JSON content the adapter rejects.
    """
    form = cast("dict[str, object]", decode_form(body, media.fields, media.other))
    for name in [name for name in form if any(_claims(plan, name) for plan in media.encoded)]:
        del form[name]
    view = RawParameter(location="query", fragments=split_query(body))
    records: list[Record] = []
    for plan in media.encoded:
        if (value := decode_parameter(plan, view)) is UNSET:
            continue
        form[plan.name] = value
        if isinstance(member := media.members.get(plan.name), JsonPart):
            try:
                form[plan.name] = member.adapter.validate_json(cast("str", value))
            except ValidationError as error:
                records.extend(model_records(error, ("body", plan.name)))
    if records:
        raise RequestValidationError(records)
    return form


def _claims(plan: ParameterPlan, name: str) -> bool:
    """Return whether a pair of this name belongs to an encoded member, as a query reads the member's style."""
    match plan.style, plan.shape, plan.explode:
        case "form", "object", True:
            return any(item.name == name for item in plan.fields) or (
                plan.additional is not None and name not in plan.reserved_names
            )
        case "deepObject", _, _:
            return name.startswith(f"{plan.name}[") and name.endswith("]")
        case _:
            pass
    return name == plan.name


async def _parts(
    request: Request,
    members: Mapping[str, FieldPlan | JsonPart],
    additional: FieldPlan | JsonPart,
    encoded: tuple[ParameterPlan, ...] = (),
) -> dict[str, object]:
    """Return the parts of a multipart body by name: uploads read to bytes, text in its field's kind, JSON validated.

    A part no member names is read as `additional` says. A repeated member holds a list, and a single-valued one its
    last part. The request is refused with the records of every JSON part its member's adapter rejects. A styled
    member takes the parts its style names, which carry its text without percent-encoding: each is percent-encoded
    but for its style's delimiters, as a query writes it, and read by its plan as a query parameter is.
    """
    parts: dict[str, object] = {}
    records: list[Record] = []
    styled: list[ParameterFragment] = []
    async with request.form() as form:
        for name in dict.fromkeys(form.keys()):
            member = members.get(name, additional)
            received = form.getlist(name)
            if encoded and any(_claims(plan, name) for plan in encoded):
                styled.extend([
                    ParameterFragment(quote(name, safe="").encode(), quote(await _text(value), safe=",|").encode())
                    for value in received
                ])
                continue
            if isinstance(member, JsonPart):
                values = await _json(name, member, received if member.repeated else received[-1:], records)
            else:
                values = [
                    await value.read() if isinstance(value, UploadFile) else typed(value, member.kind)
                    for value in received
                ]
            parts[name] = values if member.repeated else values[-1]
    view = RawParameter(location="query", fragments=tuple(styled))
    for plan in encoded:
        if (value := decode_parameter(plan, view)) is not UNSET:
            parts[plan.name] = value
    if records:
        raise RequestValidationError(records)
    return parts


async def _text(value: UploadFile | str) -> str:
    return decode_text(await value.read()) if isinstance(value, UploadFile) else value


async def _json(name: str, member: JsonPart, received: list[UploadFile | str], records: list[Record]) -> list[object]:
    """Return the parts of a JSON member, sent as fields or as files, parsed by its adapter as a JSON body is.

    A part the adapter rejects leaves its records, located at the parts' name and, when it repeats, the part's index,
    except that an untyped member keeps it as it comes.
    """
    values: list[object] = []
    for index, value in enumerate(received):
        location = ("body", name, index) if member.repeated else ("body", name)
        content = await value.read() if isinstance(value, UploadFile) else value
        try:
            values.append(member.adapter.validate_json(content))
        except ValidationError as error:
            if not member.untyped:
                records.extend(model_records(error, location))
            values.append(content if member.untyped else None)
    return values
