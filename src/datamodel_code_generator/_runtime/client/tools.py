"""The tool table of a generated client: one tool per operation, searched, described, and called with JSON values.

A tool's JSON schemas come from the model types of its arguments and result, through the models' own library, and a
call validates each argument as JSON through the codec of its type, the mode the schemas describe, calls the client's
method, and returns the result as its codec writes it. A read-only table leaves out every tool whose HTTP method is not
safe. A failure of the client returns its class, reason, status, operation, request ID, and bounded body, never its
headers, URL, or cause, with every credential the client placed on the call's requests replaced in the request ID and
the body, so an API that echoes one does not hand it to the caller; arguments a tool refuses return each problem.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from operator import itemgetter
from typing import TYPE_CHECKING, ClassVar, Final, Generic, Protocol

from pydantic import TypeAdapter, ValidationError
from typing_extensions import TypeIs, TypeVar

from ..model_codecs.media import JSONValue, json_bytes, json_value
from ..model_codecs.pydantic_adapters import adapter
from .errors import APIStatusError, SDKError
from .positions import placed_credentials
from .tool_search import score, words

if TYPE_CHECKING:
    from pydantic.json_schema import JsonSchemaMode
    from typing_extensions import Never, TypeForm

ClientT = TypeVar("ClientT")
_NAME: Final = 3
_SITE: Final = 2
_REDACTED: Final = "[redacted]"


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

    The credentials are replaced in the text, and in each string of the JSON value it holds, where an escape spells one.
    """
    content = error.body_bytes
    if not content:
        return None
    text = content.decode("utf-8", errors="replace")
    shown = _redacted(text, placed, cut=error.truncated)
    if not error.truncated:
        try:
            value = json_value(content if shown == text else shown)
        except ValueError:
            pass
        else:
            return _cleared(value, placed)
    return shown


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
    """Return a JSON value whose strings, keys included, hold no placed credential."""
    match value:
        case str():
            return _redacted(value, placed)
        case list():
            return [_cleared(item, placed) for item in value]
        case dict():
            return {_redacted(key, placed): _cleared(item, placed) for key, item in value.items()}
        case _:
            return value


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


class _RefusalError(Exception):
    """The error value of a call whose arguments a tool refuses, raised to the call that returns it."""

    def __init__(self, value: JSONValue) -> None:
        super().__init__()
        self.value = value


class _Table(Generic[ClientT]):
    """What the sync and asyncio tables share: their tools by name, the search, the schemas, and a call's values."""

    plans: ClassVar[tuple[ToolPlan, ...]] = ()

    def __init__(self, client: ClientT, *, read_only: bool = False) -> None:
        """Keep the client the tools call, and the tools, without those of unsafe methods when read-only."""
        self.client = client
        self.read_only = read_only
        self._tools = {plan.name: plan for plan in self.plans if plan.safe or not read_only}

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
        inputs: list[tuple[str, JsonSchemaMode, TypeAdapter[object]]] = [
            (argument.name, "validation", _adapter(argument.codec)) for argument in plan.arguments
        ]
        schemas, definitions = TypeAdapter.json_schemas(inputs)
        properties: dict[str, JSONValue] = {
            argument.name: schemas[argument.name, "validation"] for argument in plan.arguments
        }
        arguments: dict[str, JSONValue] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required := [argument.name for argument in plan.arguments if argument.required]:
            arguments["required"] = [*required]
        return {
            "name": plan.name,
            "description": plan.description,
            "input_schema": {**arguments, **definitions},
            "output_schema": _adapter(plan.result).json_schema(mode="serialization"),
        }

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
        try:
            plan, resource, values = self._prepared(name, arguments)
        except _RefusalError as refusal:
            return refusal.value
        with placed_credentials() as placed:
            try:
                result = getattr(resource, plan.method)(**values)
            except SDKError as error:
                return failed(error, placed)
        return _dumped(plan, result)


class AsyncToolTable(_Table[ClientT]):
    """The tools of an asyncio client."""

    async def call(self, name: str, arguments: Mapping[str, object]) -> JSONValue:
        """Call a tool with JSON arguments and return its JSON result, or the error value of a refusal or failure.

        A name the table holds no tool of raises UnknownToolError.
        """
        try:
            plan, resource, values = self._prepared(name, arguments)
        except _RefusalError as refusal:
            return refusal.value
        with placed_credentials() as placed:
            try:
                result = await getattr(resource, plan.method)(**values)
            except SDKError as error:
                return failed(error, placed)
        return _dumped(plan, result)


def _adapter(codec: ToolCodec) -> TypeAdapter[object]:
    """Return the shared type adapter of a codec's type, which describes the type as the codec validates it."""
    return adapter(codec.type)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _dumped(plan: ToolPlan, result: Never) -> JSONValue:
    """Return a tool's result as the JSON its codec writes, parsed into builtin values."""
    return json_value(plan.result.encode(result))


def _entry(plan: ToolPlan) -> dict[str, JSONValue]:
    """Return how a search lists a tool: its name, route, tags, description, and signature."""
    return {
        "name": plan.name,
        "route": plan.route,
        "tags": list(plan.tags),
        "description": plan.description,
        "signature": plan.signature,
    }
