"""The tool table of a generated client: one tool per operation, searched, described, and called with JSON values.

A tool's JSON schemas come from the model types of its arguments and result, through the models' own library, and a
call validates each argument through the codec of its type, calls the client's method, and returns the result as its
codec writes it. A read-only table leaves out every tool whose HTTP method is not safe. A failure of the client returns
its class, reason, status, operation, request ID, and bounded body, never its headers, URL, or cause, so no credential
the client sends reaches the caller; arguments a tool refuses return each problem.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from operator import itemgetter
from typing import TYPE_CHECKING, ClassVar, Final, Generic, Protocol

from pydantic import TypeAdapter, ValidationError
from typing_extensions import TypeIs, TypeVar

from ..model_codecs.media import JSONValue, json_value
from ..model_codecs.pydantic_adapters import adapter
from .errors import APIStatusError, SDKError
from .tool_search import score, words

if TYPE_CHECKING:
    from pydantic.json_schema import JsonSchemaMode
    from typing_extensions import Never, TypeForm

ClientT = TypeVar("ClientT")
_NAME: Final = 3
_SITE: Final = 2


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


def failed(error: SDKError) -> JSONValue:
    """Return the error value of a call the client failed: the allow-listed metadata of its error."""
    return {
        "error": {
            "type": type(error).__name__,
            "reason": error.reason,
            "status_code": None if error.info is None else error.info.status_code,
            "operation_id": error.operation_id,
            "request_id": error.request_id,
            "body": _body(error) if isinstance(error, APIStatusError) else None,
        }
    }


def _body(error: APIStatusError) -> JSONValue:
    """Return the bounded body of a refused call: its JSON value, else its text, or None without one."""
    content = error.body_bytes
    if not content:
        return None
    if not error.truncated:
        try:
            value = json_value(content)
        except ValueError:
            pass
        else:
            return value
    return content.decode("utf-8", errors="replace")


class ToolCodec(Protocol):
    """The codec of a model type, as a Pydantic backend's: its type, its validation, and its JSON encoding.

    `encode` takes a value of the codec's own type, which a table of several tools does not name: what the client's
    method returned.
    """

    @property
    def type(self) -> TypeForm[object]:
        """Return the model type the codec validates and encodes."""
        raise NotImplementedError

    def convert(self, value: object) -> object:
        """Construct a value of the type from a parsed JSON value, validating it."""
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

        Each argument is validated through the codec of its type; an argument the tool has not, one it requires that is
        missing, and each value its type refuses is a problem, all reported together.
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
                values[key] = argument.codec.convert(given[key])
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
        try:
            result = getattr(resource, plan.method)(**values)
        except SDKError as error:
            return failed(error)
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
        try:
            result = await getattr(resource, plan.method)(**values)
        except SDKError as error:
            return failed(error)
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
