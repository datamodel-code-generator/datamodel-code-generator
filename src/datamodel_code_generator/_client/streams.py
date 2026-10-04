"""Plan the server-sent event and NDJSON stream helpers of a client target and the checks they must pass.

A stream helper decodes the JSON data of each event, or record, by the schema its discriminator maps it to. Each schema
is bound as a use of the helper's stream response at the schema's location, typed as the schema's own value use, so its
codec reads a received value; these uses join the codec plan before it is made, as the parts of a body do. A helper that
declares resumption is checked against the operation it reopens the stream with: where its cursor and bindings are
written, what they read, and the stream response the reopen must declare.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.pagination import (  # pyright: ignore[reportPrivateUsage]
    _label,
    _listed,
    _Pages,
    _problem,
    _target_key,
    credential_place,
)
from datamodel_code_generator._client.plan import schema_use, schema_uses
from datamodel_code_generator._client.polling import _Polls, _Source  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator._generation_contract import BindingCaptureError
from datamodel_code_generator._target_contract import DeclarationId, SourceLocation, TypeUseBinding, TypeUseId

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import SchemaRef
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ResponseSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan

__all__ = ("StreamSpec", "plan_streams", "stream_uses")

_MEDIA: Final = {
    "sse": ("text/event-stream",),
    "ndjson": (
        "application/jsonl",
        "application/jsonlines",
        "application/ndjson",
        "application/x-jsonl",
        "application/x-jsonlines",
        "application/x-ndjson",
    ),
}
_LABELS: Final = {"sse": "SSE", "ndjson": "NDJSON"}
_TARGETS: Final = {"body": "the request body", "querystring": "a querystring"}


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamSpec:
    """A stream helper ready to render: its operation and stream media, each event and error schema's use, its schemas.

    `media` is the event stream or NDJSON media type the operation declares in `response`, which the helper requests.
    An event's key is its discriminator value, None for a helper with one event schema. `schemas` holds the manifest
    reference of every schema the helper decodes, in the order of its settings. A helper declaring resumption reopens
    its stream with `reopen`, requesting the `reopen_media` its `reopen_response` declares.
    """

    helper: Helper
    operation: OperationSpec
    media: str
    response: ResponseSpec
    events: tuple[tuple[str | None, TypeUseBinding], ...]
    errors: tuple[tuple[str, TypeUseBinding], ...]
    schemas: tuple[Mapping[str, str], ...]
    reopen: OperationSpec | None = None
    reopen_response: ResponseSpec | None = None
    reopen_media: str | None = None

    @property
    def own(self) -> bool:
        """Return whether the helper reopens its stream with its own operation, repeating the caller's request."""
        return self.reopen is not None and self.reopen.contract.id == self.operation.contract.id

    @property
    def uses(self) -> tuple[TypeUseBinding, ...]:
        """Return the use of every event and error schema, in order."""
        return (*(use for _, use in self.events), *(use for _, use in self.errors))


def _events(helper: Helper) -> Iterator[tuple[str, str | None, SchemaRef]]:
    """Yield where each event schema of a helper is set, its discriminator value, None for the only one, and itself."""
    at = helper.at
    if isinstance(schema := helper.tree["event_schema"], dict):
        for key, reference in schema["mapping"].items():
            yield f"{at}.event_schema.mapping[{key!r}]", key, reference
    else:
        yield f"{at}.event_schema", None, schema


def _errors(helper: Helper) -> Iterator[tuple[str, str, SchemaRef]]:
    """Yield where each error event schema of a helper is set, its discriminator value, and itself."""
    for key, reference in helper.tree["error_events"].items():
        yield f"{helper.at}.error_events[{key!r}]", key, reference


def _references(helper: Helper) -> Iterator[tuple[str, SchemaRef]]:
    """Yield where each schema of a helper is set and itself, its events' before its errors'."""
    for where, _, reference in (*_events(helper), *_errors(helper)):
        yield where, reference


def _essence(media: str) -> str:
    """Return a media type's type and subtype in lowercase, without its parameters."""
    return media.partition(";")[0].strip().lower()


def _stream_response(spec: OperationSpec, essence: str) -> ResponseSpec | None:
    """Return the first success response of an operation that declares a media type, whatever its parameters."""
    return next(
        (
            response
            for response in spec.responses
            if response.success and any(_essence(item.media_type) == essence for item in response.media)
        ),
        None,
    )


def _media_problem(helper: Helper) -> str | None:
    """Return why a helper's media type does not fit its kind, an event stream for SSE and JSON lines for NDJSON."""
    media, accepted = helper.tree["media"], _MEDIA[helper.kind]
    if _essence(media) in accepted:
        return None
    expected = accepted[0] if len(accepted) == 1 else f"one of {_listed(list(accepted))}"
    return f"The media type {media!r} of {helper.name!r} is not {expected}"


class _Streams:
    """Check every enabled stream helper against its operation and bind the schemas of its events."""

    def __init__(self, protocols: Protocols, request: TargetRequest, wire: WirePlan) -> None:
        """Index the documents by manifest pointer and the value use of each schema by its location and direction."""
        self.protocols = protocols
        self.request = request
        self.wire = wire
        self.documents = {pointer: document for document, pointer in request.documents.pointers.items()}
        self.schemas = schema_uses(request.batch.type_uses)

    def location(self, reference: SchemaRef) -> SourceLocation | None:
        """Return the resolved location of a schema reference, or None when its document has no such pointer."""
        location = SourceLocation(self.documents[self.protocols.documents[reference]], reference.pointer, "schema")
        try:
            self.request.lease.borrow(location)
        except BindingCaptureError:
            return None
        return self.wire.schema(location)[0]

    def helper(
        self, helper: Helper, spec: OperationSpec, reopen: OperationSpec | None
    ) -> tuple[StreamSpec | None, list[Diagnostic]]:
        """Check one enabled helper's settings against its operation, and bind its schemas when every check passes.

        A helper declaring resumption also needs a success response of its media type from the operation it reopens
        its stream with.
        """
        tree = helper.tree
        at, name, label = helper.at, helper.name, _LABELS[helper.kind]
        essence = _essence(tree["media"])
        problems: list[Diagnostic] = []
        response = reopened = None
        if (message := _media_problem(helper)) is not None:
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.media", message, spec))
        else:
            if (response := _stream_response(spec, essence)) is None:
                message = f"{_label(spec)} declares no {essence} success response for the {label} helper {name!r}"
                problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
            if reopen is not None and (reopened := _stream_response(reopen, essence)) is None:
                message = (
                    f"{_label(reopen)} declares no {essence} success response for the {label} helper {name!r} to "
                    "reopen its stream with"
                )
                where = f"{at}.resume.reopen_operation"
                problems.append(_problem("E_CONFIG_VALUE", "config", where, message, reopen))
        problems.extend(self.terminal(helper, spec))
        locations: dict[str, SourceLocation] = {}
        for where, reference in _references(helper):
            if (location := self.location(reference)) is None:
                message = f"The schema {reference.pointer!r} of {name!r} does not exist in its document"
                problems.append(_problem("E_CONFIG_VALUE", "config", where, message, spec))
            else:
                locations[where] = location
        if problems or response is None:
            return None, problems
        media = next(item.media_type for item in response.media if _essence(item.media_type) == essence)
        return StreamSpec(
            helper=helper,
            operation=spec,
            media=media,
            response=response,
            reopen=reopen,
            reopen_response=reopened,
            reopen_media=None
            if reopened is None
            else next(item.media_type for item in reopened.media if _essence(item.media_type) == essence),
            events=tuple((key, self.use(spec, response, media, locations[where])) for where, key, _ in _events(helper)),
            errors=tuple((key, self.use(spec, response, media, locations[where])) for where, key, _ in _errors(helper)),
            schemas=tuple(
                {"document": self.protocols.documents[reference], "pointer": reference.pointer}
                for _, reference in _references(helper)
            ),
        ), problems

    @staticmethod
    def terminal(helper: Helper, spec: OperationSpec) -> Iterator[Diagnostic]:
        """Refuse a completion event type that the event type discriminator also maps to an event or an error."""
        tree = helper.tree
        completion, schema = tree["completion"], tree["event_schema"]
        discriminator = schema["discriminator"]["from"] if isinstance(schema, dict) else None
        if completion["kind"] != "event_type" or discriminator != "event_type":
            return
        value = completion["value"]
        for setting, keys in (("event_schema.mapping", schema["mapping"]), ("error_events", tree["error_events"])):
            if value in keys:
                where = f"{helper.at}.completion.value"
                message = f"The completion event type {value!r} of {helper.name!r} is also a key of its {setting}"
                yield _problem("E_CONFIG_CONFLICT", "config", where, message, spec)

    def use(self, spec: OperationSpec, response: ResponseSpec, media: str, location: SourceLocation) -> TypeUseBinding:
        """Return the use that reads a schema as the stream response's events, bound as the schema's value use is."""
        schema = schema_use(self.schemas, location, "response")
        use = TypeUseId(
            owner=spec.contract.id,
            role="response_body",
            use_site=location,
            schema_site=location,
            declaration=DeclarationId(location) if schema is None else schema.id.declaration,
            direction="response",
            status=response.status,
            media=media,
        )
        if schema is None:
            return TypeUseBinding(use, "not_generated", None, None, schema=location)
        return TypeUseBinding(use, schema.state, schema.type, schema.reason, schema=location)


def stream_uses(
    protocols: Protocols | None, plan: ClientPlan, request: TargetRequest, wire: WirePlan
) -> tuple[tuple[StreamSpec, ...], tuple[TypeUseBinding, ...], dict[str, list[Diagnostic]]]:
    """Check every enabled stream helper before its codecs are planned, returning the helpers, uses, and problems.

    Each use appears once, however many helpers decode its schema from the same response.
    """
    if protocols is None:
        return (), (), {}
    streams = _Streams(protocols, request, wire)
    operations = {spec.contract.id: spec for spec in plan.operations}
    specs: list[StreamSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind not in _LABELS:
            continue
        spec, *reopened = (operations[protocols.operations[link.ref].id] for link in helper.links)
        planned, problems[helper.name] = streams.helper(helper, spec, reopened[0] if reopened else None)
        if planned is not None:
            specs.append(planned)
    uses = {use.id: use for spec in specs for use in spec.uses}
    return tuple(specs), tuple(uses.values()), problems


def plan_streams(  # noqa: PLR0913, PLR0917
    specs: tuple[StreamSpec, ...],
    protocols: Protocols | None,
    plan: ClientPlan,
    codecs: CodecPlan,
    wire: WirePlan,
    request: TargetRequest,
    problems: dict[str, list[Diagnostic]],
) -> tuple[StreamSpec, ...]:
    """Plan every stream helper whose events have native codecs and whose body discriminator each schema declares.

    A helper declaring resumption also needs its cursor, bindings, and expiry to fit. The problems found are added to
    each helper's.
    """
    if protocols is None or not specs:
        return ()
    pages = _Pages(protocols, plan, codecs, wire, request)
    polls = _Polls(pages, {spec.contract.id: spec for spec in plan.operations}, codecs, protocols)
    planned: list[StreamSpec] = []
    for spec in specs:
        helper = spec.helper
        found = problems[helper.name]
        found.extend(_discriminated(helper, spec, pages))
        found.extend(_resumed(helper, spec, pages, polls))
        if not found:
            planned.append(spec)
    return tuple(planned)


def _resumed(helper: Helper, spec: StreamSpec, pages: _Pages, polls: _Polls) -> Iterator[Diagnostic]:
    """Check a declared resumption: its cursor and where it is written, its bindings, and the expiry it reads.

    The bindings read the stream responses, the open one for `initial` and the latest open or reopen one for
    `previous`, whose bodies are the stream, so only their headers and status give a value. A reopen with another
    operation sends only what is written, so it must write each of that operation's required parameters and body.
    """
    if (reopen := spec.reopen) is None:
        return
    resume, at = helper.tree["resume"], f"{helper.at}.resume"
    opened = _Source(spec.operation, spec.response, None)
    reopened = _Source(reopen, cast("ResponseSpec", spec.reopen_response), None)
    yield from _cursor(helper, spec, reopen, pages)
    written = ((_target_key(resume["write"]), "cursor"),)
    sources = {"initial": [opened], "previous": [opened, reopened]}
    bindings = resume["bindings"]
    yield from polls.bindings(
        helper, reopen, bindings, f"{at}.bindings", sources, "binding", written=written, complete=not spec.own
    )
    if (read := resume.get("expires_at")) is None:
        return
    where = f"{at}.expires_at"
    if read["from"] != "header":
        message = f"The expiry of {helper.name!r} reads a {read['from']}, where only a header of a stream gives one"
        yield _problem("E_CONFIG_VALUE", "config", where, message, spec.operation)
    elif isinstance(found := polls.read(helper, [opened], read, where, "expiry"), Diagnostic):
        yield found


def _cursor(helper: Helper, spec: StreamSpec, reopen: OperationSpec, pages: _Pages) -> Iterator[Diagnostic]:
    """Check a resumption's cursor: what it reads from the events, and where it is written, the types it accepts.

    A cursor is never written where a request carries credentials, nor to a path parameter, and one that can be
    cleared, an SSE event ID or a body cursor whose null clears it, only to an optional header or query parameter, which
    a reopen then omits.
    """
    resume, name, label = helper.tree["resume"], helper.name, _label(reopen)
    at, write, cursor = f"{helper.at}.resume", helper.tree["resume"]["write"], helper.tree["resume"]["cursor"]
    where, place = f"{at}.write", f"the {write['in']} parameter {write.get('name')!r} of {label}"
    if (secret := credential_place(write, pages.secret_headers, pages.secret_queries, reopen)) is not None:
        message = f"The cursor of {name!r} writes {secret}, which carries credentials no helper writes"
        yield _problem("E_CONFIG_VALUE", "config", where, message, reopen)
        return
    if write["in"] == "path":
        message = f"The cursor of {name!r} is written to {place}, which is not supported yet"
        yield _problem("E_CLIENT_UNSUPPORTED", "target", where, message, reopen)
        return
    if cursor == "event_id" or resume["null"] == "clear":
        if write["in"] not in {"header", "query"}:
            message = (
                f"The cursor of {name!r} can be cleared, which only a header or query parameter can omit; writing it "
                f"to {_TARGETS[write['in']]} of {label} is not supported yet"
            )
            yield _problem("E_CLIENT_UNSUPPORTED", "target", where, message, reopen)
            return
        key = _target_key(write)
        if any(
            item.required and _target_key({"in": item.location, "name": item.wire_name}) == key
            for item in reopen.parameters
        ):
            message = f"The cursor of {name!r} can be cleared, which {place}, a required one, cannot omit"
            yield _problem("E_CONFIG_VALUE", "config", where, message, reopen)
            return
    if cursor == "event_id":
        types: frozenset[str] | None = frozenset({"string"})
    else:
        found = _read(helper, spec, cursor, pages)
        if isinstance(found, Diagnostic):
            yield found
            return
        types = found
    yield from pages.fits(helper, reopen, write, types, where, "cursor", null=False)


def _read(
    helper: Helper, spec: StreamSpec, cursor: Mapping[str, Any], pages: _Pages
) -> frozenset[str] | Diagnostic | None:
    """Return the JSON types a body cursor reads from the events, or why it reads none.

    Every event schema must declare the property under `missing: error`, and at least one under `inherit`.
    """
    name, at = helper.name, f"{helper.at}.resume.cursor"
    if cursor["from"] != "body":
        message = f"The cursor of {name!r} reads a {cursor['from']}, where only a body pointer reads an event's cursor"
        return _problem("E_CONFIG_VALUE", "config", at, message, spec.operation)
    pointer, inherit = cursor["pointer"], helper.tree["resume"]["missing"] == "inherit"
    found: set[str] | None = set()
    declared = False
    for _, use in spec.events:
        location = cast("SourceLocation", use.schema)
        if (member := pages.declared(location, pointer)) is None:
            if inherit:
                continue
            message = f"The cursor pointer {pointer!r} of {name!r} names no property of {location.pointer!r}"
            return _problem("E_CONFIG_VALUE", "config", f"{at}.pointer", message, spec.operation)
        declared = True
        types = pages.types(member)
        found = None if types is None or found is None else found | types
    if not declared:
        message = f"The cursor pointer {pointer!r} of {name!r} names no property of any event schema"
        return _problem("E_CONFIG_VALUE", "config", f"{at}.pointer", message, spec.operation)
    return None if found is None else frozenset(found)


def _discriminated(helper: Helper, spec: StreamSpec, pages: _Pages) -> Iterator[Diagnostic]:
    """Refuse a schema that does not declare its body discriminator as a property whose values can be strings."""
    schema = helper.tree["event_schema"]
    if not isinstance(schema, dict) or (selector := schema["discriminator"])["from"] != "body":
        return
    pointer, where = selector["pointer"], f"{helper.at}.event_schema.discriminator.pointer"
    for location in dict.fromkeys(use.schema for use in spec.uses if use.schema is not None):
        label = location.pointer
        if (member := pages.declared(location, pointer)) is None:
            message = f"The discriminator pointer {pointer!r} of {helper.name!r} names no property of {label!r}"
            yield _problem("E_CONFIG_VALUE", "config", where, message, spec.operation)
        elif (types := pages.types(member)) is not None and "string" not in types:
            message = (
                f"The discriminator of {helper.name!r} reads {_listed(sorted(types))} values from {label!r}, where "
                "only string values fit"
            )
            yield _problem("E_CONFIG_VALUE", "config", where, message, spec.operation)
