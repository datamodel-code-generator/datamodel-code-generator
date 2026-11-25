"""The tool table of a generated client: one tool per operation, searched, described, and called with JSON values.

A tool's JSON schemas come from the model types of its arguments and result, through the models' own library, without
the description, title, or examples of any schema in a table generated from untrusted input, and a call validates each
argument as JSON through the codec of its type, the mode the schemas describe, calls the client's method, and returns
the result as its codec writes it. A read-only table leaves out every tool whose HTTP method is not safe. A failure of
the client returns its class, reason, status, operation, request ID, and bounded body, never its headers, URL, or
cause; every credential the client placed on the call's requests is replaced in the result, and in the request ID and
the body of a failure, so an API that echoes one does not hand it to the caller, and a result its model refuses once
they are replaced returns an error value that holds it; arguments a tool refuses return each problem.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from operator import itemgetter
from typing import TYPE_CHECKING, ClassVar, Final, Generic, Protocol

from pydantic import TypeAdapter, ValidationError
from pydantic.json_schema import GenerateJsonSchema
from typing_extensions import TypeIs, TypeVar, override

from ..model_codecs.media import JSONValue, json_bytes, json_value
from ..model_codecs.pydantic_adapters import adapter
from .errors import APIStatusError, SDKError
from .positions import placed_credentials
from .tool_search import score, words

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic.json_schema import DefsRef, JsonSchemaKeyT, JsonSchemaMode, JsonSchemaValue
    from pydantic_core import CoreSchema
    from typing_extensions import Never, TypeForm

ClientT = TypeVar("ClientT")
_NAME: Final = 3
_SITE: Final = 2
_REDACTED: Final = "[redacted]"
_NAMING: Final = frozenset({"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"})
"""The JSON Schema keywords whose value maps names to schemas."""
_HOLDING: Final = frozenset({
    "additionalItems",
    "additionalProperties",
    "allOf",
    "anyOf",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "oneOf",
    "prefixItems",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})
"""The JSON Schema keywords whose value is a schema or a list of schemas."""
_PROSE: Final = frozenset({"description", "examples", "title"})
"""The JSON Schema keywords that hold the text or values of the document, which a table of untrusted documents omits."""


class UnknownToolError(LookupError):
    """A tool name the table holds no tool of, also for an unsafe tool that a read-only table leaves out."""

    def __init__(self, name: str) -> None:
        """Keep the name that was asked for."""
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        """Name the unknown tool."""
        return f"No tool is named {self.name!r}"


def problem(argument: str | None, message: str, kind: str, location: tuple[str | int, ...] = ()) -> JSONValue:
    """Return one problem of a call's arguments: the argument, where in its value, what is wrong, and its kind."""
    return {"argument": argument, "location": list(location), "message": message, "kind": kind}


def invalid(argument: str, error: ValidationError) -> list[JSONValue]:
    """Return the problems the validation of one argument's value found, without the value it was given."""
    return [problem(argument, item["msg"], item["type"], tuple(item["loc"])) for item in error.errors()]


def refused(problems: list[JSONValue]) -> JSONValue:
    """Return the error value of a call whose arguments the tool refuses."""
    return {"error": {"type": "invalid_arguments", "problems": problems}}


def redacted_result(value: JSONValue) -> JSONValue:
    """Return the error value of a result that its model refuses once the credentials placed in it are replaced."""
    return {"error": {"type": "redacted_result", "result": value}}


def failed(error: SDKError, placed: Collection[str]) -> JSONValue:
    """Return the error value of a call the client failed: the allow-listed metadata of its error.

    `placed` holds the texts of the credentials the client placed on the call's requests, which the request ID and the
    body never show.
    """
    return {
        "error": {
            "type": type(error).__name__,
            "reason": error.reason,
            "status_code": None if error.info is None else error.info.status_code,
            "operation_id": error.operation_id,
            "request_id": None if (request_id := error.request_id) is None else _redacted(request_id, placed),
            "body": _body(error, placed) if isinstance(error, APIStatusError) else None,
        }
    }


def _body(error: APIStatusError, placed: Collection[str]) -> JSONValue:
    """Return the bounded body of a refused call without the credentials placed: its JSON value, else its text, or None.

    A whole JSON body is parsed before anything is replaced, so the credentials are replaced in the values it holds,
    where an escape spells one too; the text of any other body has each spelling a JSON string gives one replaced.
    """
    content = error.body_bytes
    if not content:
        return None
    if not error.truncated:
        try:
            value = json_value(content)
        except ValueError:
            pass
        else:
            return _cleared(value, placed)
    return _redacted(content.decode("utf-8", errors="replace"), _spellings(placed), cut=error.truncated)


def _spellings(placed: Collection[str]) -> set[str]:
    """Return each placed credential and the forms a JSON string may write it in: escaped, ASCII-only, slash-escaped."""
    forms: set[str] = set()
    for secret in placed:
        for escaped in (json_bytes(secret).decode()[1:-1], json_bytes(secret, ascii_only=True).decode()[1:-1]):
            forms.update((secret, escaped, escaped.replace("/", "\\/")))
    return forms


def _redacted(text: str, placed: Collection[str], *, cut: bool = False) -> str:
    """Return a text with each occurrence of a placed credential replaced, the longest first.

    A text that was `cut` short also has an end that begins a credential replaced.
    """
    known = sorted(set(placed), key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, _REDACTED)
    if not cut:
        return text
    tail = max((size for secret in known for size in range(1, len(secret)) if text.endswith(secret[:size])), default=0)
    return f"{text[: len(text) - tail]}{_REDACTED}" if tail else text


def _cleared(value: JSONValue, placed: Collection[str]) -> JSONValue:
    """Return a JSON value, a result or an error's body, with no placed credential in its strings, keys or numbers.

    A number or Boolean whose JSON text holds one becomes that text with it replaced.
    """
    match value:
        case str():
            return _redacted(value, placed)
        case int() | float():
            text = json_bytes(value).decode()
            shown = _redacted(text, placed)
            return value if shown == text else shown
        case list():
            return [_cleared(item, placed) for item in value]
        case dict():
            return {_redacted(key, placed): _cleared(item, placed) for key, item in value.items()}
        case _:
            return value


class _Proseless(GenerateJsonSchema):
    """Generate JSON schemas without the description, title, or examples of any schema in them, for untrusted documents.

    Only those keywords of a schema are left out: a property named `description`, `title`, or `examples` stays, and so
    do defaults, enum values, patterns, and every other keyword.
    """

    @override
    def generate(self, schema: CoreSchema, mode: JsonSchemaMode = "validation") -> JsonSchemaValue:
        """Generate the JSON schema of a core schema without its descriptions, titles, and examples."""
        return _proseless(super().generate(schema, mode))

    @override
    def generate_definitions(
        self, inputs: Sequence[tuple[JsonSchemaKeyT, JsonSchemaMode, CoreSchema]]
    ) -> tuple[dict[tuple[JsonSchemaKeyT, JsonSchemaMode], JsonSchemaValue], dict[DefsRef, JsonSchemaValue]]:
        """Generate the JSON schemas of several core schemas and their shared definitions without their prose."""
        schemas, definitions = super().generate_definitions(inputs)
        return (
            {key: _proseless(value) for key, value in schemas.items()},
            {name: _proseless(value) for name, value in definitions.items()},
        )


def _proseless(schema: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """Return a JSON schema without its description, title, and examples keywords and those of the schemas it holds."""
    return {key: _held(key, value) for key, value in schema.items() if key not in _PROSE}


def _held(keyword: str, value: JSONValue) -> JSONValue:
    """Return the value of a schema's keyword with the schemas it holds without prose, and any other value as it is."""
    if keyword in _NAMING and isinstance(value, dict):
        return {name: _subschema(item) for name, item in value.items()}
    if keyword not in _HOLDING:
        return value
    return [_subschema(item) for item in value] if isinstance(value, list) else _subschema(value)


def _subschema(value: JSONValue) -> JSONValue:
    """Return a schema without prose; a boolean schema has none."""
    return _proseless(value) if isinstance(value, dict) else value


def schema_patterns(schema: Mapping[str, JSONValue]) -> Iterator[str]:
    """Yield the regular expressions of a JSON schema and of the schemas it holds, `pattern` and `patternProperties`."""
    for keyword, value in schema.items():
        if keyword == "pattern" and isinstance(value, str):
            yield value
        if keyword == "patternProperties" and isinstance(value, dict):
            yield from value
        for item in _held_schemas(keyword, value):
            yield from schema_patterns(item)


def _held_schemas(keyword: str, value: JSONValue) -> list[dict[str, JSONValue]]:
    """Return the schema objects the value of a schema's keyword holds; a boolean schema holds no pattern."""
    if keyword in _NAMING and isinstance(value, dict):
        held = list(value.values())
    else:
        held = (value if isinstance(value, list) else [value]) if keyword in _HOLDING else []
    return [item for item in held if isinstance(item, dict)]


class ToolCodec(Protocol):
    """The codec of a model type, as a Pydantic backend's: its type, its validation, and its JSON encoding.

    `encode` takes a value of the codec's own type, which a table of several tools does not name: what the client's
    method returned.
    """

    @property
    def type(self) -> TypeForm[object]:
        """Return the model type the codec validates and encodes."""
        raise NotImplementedError

    def decode(self, content: bytes) -> object:
        """Construct a value of the type from JSON, validating it in JSON mode."""
        raise NotImplementedError

    def encode(self, value: Never) -> bytes:
        """Return the JSON bytes of a value of the type."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolArgument:
    """One argument of a tool: its name, the keyword of its method, the codec of its model type, and requiredness."""

    name: str
    codec: ToolCodec
    required: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolPlan:
    """One tool: its name, the client method it calls, the route, tags, text it is found by, and its codecs.

    `resource` is the dotted attribute path of the method's resource on the client. `safe` and `idempotent` say what
    the HTTP method promises, and `media_type` is the request media a call names beside its body, if any.
    """

    name: str
    resource: str
    method: str
    route: str
    tags: tuple[str, ...] = ()
    description: str
    signature: str
    safe: bool = False
    idempotent: bool = False
    arguments: tuple[ToolArgument, ...] = ()
    media_type: str | None = None
    result: ToolCodec


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What a call of a tool returned: its JSON value, and whether that is an error value.

    An error value is that of a refusal, of a failure, or of a result its model refuses with the placed credentials
    replaced.
    """

    value: JSONValue
    error: bool = False


class _RefusalError(Exception):
    """The error value of a call whose arguments a tool refuses, raised to the call that returns it."""

    def __init__(self, value: JSONValue) -> None:
        super().__init__()
        self.value = value


class _Table(Generic[ClientT]):
    """What the sync and asyncio tables share: their tools by name, the search, the schemas, and a call's values."""

    plans: ClassVar[tuple[ToolPlan, ...]] = ()
    prose: ClassVar[bool] = True
    """Whether the JSON schemas keep the descriptions, titles, and examples of the models; False for untrusted input."""
    rejected_patterns: ClassVar[frozenset[str]] = frozenset()
    """The patterns of the models that Python's `re` rejects, as generation decided, which MCP clients cannot check."""

    def __init__(self, client: ClientT, *, read_only: bool = False) -> None:
        """Keep the client the tools call, and the tools, without those of unsafe methods when read-only."""
        self.client = client
        self.read_only = read_only
        self._tools = {plan.name: plan for plan in self.plans if plan.safe or not read_only}

    @property
    def available(self) -> tuple[ToolPlan, ...]:
        """Return the tools the table holds, in table order, without those a read-only table leaves out."""
        return tuple(self._tools.values())

    def search(self, query: str = "", *, tag: str | None = None, limit: int = 10) -> list[dict[str, JSONValue]]:
        """Return the tools that match a query best, at most `limit`, each by its name, route, tags, and text.

        An empty query matches every tool in table order, and `tag` keeps only the tools that carry it.
        """
        if type(limit) is not int or limit < 1:
            msg = "limit must be a positive integer"
            raise ValueError(msg)
        asked = words(query)
        ranked: list[tuple[int, int, ToolPlan]] = []
        for index, plan in enumerate(self._tools.values()):
            if tag is not None and tag not in plan.tags:
                continue
            texts = (
                (_NAME, plan.name),
                *((_SITE, item) for item in plan.tags),
                (_SITE, plan.route),
                (1, plan.description),
                (1, plan.signature),
            )
            if found := (score(asked, texts) if asked else 1):
                ranked.append((-found, index, plan))
        ranked.sort(key=itemgetter(0, 1))
        return [_entry(plan) for _, _, plan in ranked[:limit]]

    def schema(self, name: str) -> dict[str, JSONValue]:
        """Return a tool's name, description, and the JSON schemas of its arguments object and of its result."""
        plan = self._plan(name)
        arguments, result = self.schemas(name)
        return {"name": plan.name, "description": plan.description, "input_schema": arguments, "output_schema": result}

    def schemas(self, name: str) -> tuple[dict[str, JSONValue], dict[str, JSONValue]]:
        """Return the JSON schemas of a tool's arguments object and of its result, which the models' library derives.

        A table without `prose` leaves the `description`, `title`, and `examples` of every schema out of both.
        """
        plan = self._plan(name)
        generator = GenerateJsonSchema if self.prose else _Proseless
        inputs: list[tuple[str, JsonSchemaMode, TypeAdapter[object]]] = [
            (argument.name, "validation", _adapter(argument.codec)) for argument in plan.arguments
        ]
        schemas, definitions = TypeAdapter.json_schemas(inputs, schema_generator=generator)
        properties: dict[str, JSONValue] = {
            argument.name: schemas[argument.name, "validation"] for argument in plan.arguments
        }
        arguments: dict[str, JSONValue] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required := [argument.name for argument in plan.arguments if argument.required]:
            arguments["required"] = [*required]
        return {**arguments, **definitions}, _adapter(plan.result).json_schema(
            mode="serialization", schema_generator=generator
        )

    def _plan(self, name: str) -> ToolPlan:
        if (plan := self._tools.get(name)) is None:
            raise UnknownToolError(name)
        return plan

    def _prepared(self, name: str, arguments: Mapping[str, object]) -> tuple[ToolPlan, object, dict[str, object]]:
        """Return a tool, the client resource that has its method, and its keywords, or raise the refusal of them.

        Each argument is written as JSON and validated in JSON mode through the codec of its type, as the schemas
        describe it; an argument the tool has not, one it requires that is missing, a value that is no JSON value, and
        each value its type refuses is a problem, all reported together.
        """
        plan = self._plan(name)
        given: object = arguments
        if not _is_mapping(given):
            raise _RefusalError(refused([problem(None, "Arguments must be a JSON object", "dict_type")]))
        known = {argument.name: argument for argument in plan.arguments}
        problems = [problem(str(key), "Unknown argument", "extra_forbidden") for key in given if key not in known]
        problems.extend(
            problem(argument.name, "Field required", "missing")
            for argument in plan.arguments
            if argument.required and argument.name not in given
        )
        values: dict[str, object] = {}
        for key, argument in known.items():
            if key not in given:
                continue
            try:
                content = json_bytes(given[key])
            except (TypeError, ValueError) as error:
                problems.append(problem(key, str(error), "json_invalid"))
                continue
            try:
                values[key] = argument.codec.decode(content)
            except ValidationError as error:
                problems.extend(invalid(key, error))
        if problems:
            raise _RefusalError(refused(problems))
        if plan.media_type is not None and "body" in values:
            values["media_type"] = plan.media_type
        target: object = self.client
        for part in plan.resource.split("."):
            target = getattr(target, part)
        return plan, target, values


class ToolTable(_Table[ClientT]):
    """The tools of a synchronous client."""

    def call(self, name: str, arguments: Mapping[str, object]) -> JSONValue:
        """Call a tool with JSON arguments and return its JSON result, or the error value of a refusal or failure.

        A name the table holds no tool of raises UnknownToolError.
        """
        return self.outcome(name, arguments).value

    def outcome(self, name: str, arguments: Mapping[str, object]) -> ToolOutcome:
        """Call a tool as `call` does, and return its JSON value with whether it is an error value."""
        try:
            plan, resource, values = self._prepared(name, arguments)
        except _RefusalError as refusal:
            return ToolOutcome(refusal.value, error=True)
        with placed_credentials() as placed:
            try:
                result = getattr(resource, plan.method)(**values)
            except SDKError as error:
                return ToolOutcome(failed(error, placed), error=True)
            return _answered(plan, result, placed)


class AsyncToolTable(_Table[ClientT]):
    """The tools of an asyncio client."""

    async def call(self, name: str, arguments: Mapping[str, object]) -> JSONValue:
        """Call a tool with JSON arguments and return its JSON result, or the error value of a refusal or failure.

        A name the table holds no tool of raises UnknownToolError.
        """
        return (await self.outcome(name, arguments)).value

    async def outcome(self, name: str, arguments: Mapping[str, object]) -> ToolOutcome:
        """Call a tool as `call` does, and return its JSON value with whether it is an error value."""
        try:
            plan, resource, values = self._prepared(name, arguments)
        except _RefusalError as refusal:
            return ToolOutcome(refusal.value, error=True)
        with placed_credentials() as placed:
            try:
                result = await getattr(resource, plan.method)(**values)
            except SDKError as error:
                return ToolOutcome(failed(error, placed), error=True)
            return _answered(plan, result, placed)


def _adapter(codec: ToolCodec) -> TypeAdapter[object]:
    """Return the shared type adapter of a codec's type, which describes the type as the codec validates it."""
    return adapter(codec.type)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _dumped(plan: ToolPlan, result: Never) -> JSONValue:
    """Return a tool's result as the JSON its codec writes, parsed into builtin values."""
    return json_value(plan.result.encode(result))


def _answered(plan: ToolPlan, result: Never, placed: Collection[str]) -> ToolOutcome:
    """Return the outcome of a call the client answered: its result without the credentials placed on its requests.

    A result they were replaced in is validated again as JSON through its model, since the text that replaces one may
    no longer match the result's schema, such as an integer that became text: one the model refuses is the error value
    of `redacted_result`. A result that held none is returned as the codec wrote it, without a second validation.
    """
    value = _dumped(plan, result)
    if (cleared := _cleared(value, placed)) == value:
        return ToolOutcome(value)
    try:
        plan.result.decode(json_bytes(cleared))
    except ValidationError:
        return ToolOutcome(redacted_result(cleared), error=True)
    return ToolOutcome(cleared)


def _entry(plan: ToolPlan) -> dict[str, JSONValue]:
    """Return how a search lists a tool: its name, route, tags, description, and signature."""
    return {
        "name": plan.name,
        "route": plan.route,
        "tags": list(plan.tags),
        "description": plan.description,
        "signature": plan.signature,
    }
