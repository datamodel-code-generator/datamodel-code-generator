"""Plan the server-sent event stream helpers of a client target and the checks they must pass.

An SSE helper decodes the JSON data of each event by the schema its discriminator maps the event to. Each schema is
bound as a use of the helper's stream response at the schema's location, typed as the schema's own value use, so its
codec reads a received value; these uses join the codec plan before it is made, as the parts of a body do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client.pagination import (  # pyright: ignore[reportPrivateUsage]
    _label,
    _listed,
    _Pages,
    _problem,
)
from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    DeclarationId,
    SourceLocation,
    TypeUseBinding,
    TypeUseId,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import Diagnostic
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ResponseSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._codec_declarations import SchemaRef
    from datamodel_code_generator._generation_contract import SourceDocumentId
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan

__all__ = ("StreamSpec", "plan_streams", "stream_uses")

_EVENT_STREAM: Final = "text/event-stream"


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamSpec:
    """An SSE helper ready to render: its operation and stream media, each event and error schema's use, its schemas.

    `media` is the event stream media type the operation declares, which the helper requests. An event's key is its
    discriminator value, None for a helper with one event schema. `schemas` holds the manifest reference of every
    schema the helper decodes, in the order of its settings.
    """

    helper: Helper
    operation: OperationSpec
    media: str
    events: tuple[tuple[str | None, TypeUseBinding], ...]
    errors: tuple[tuple[str, TypeUseBinding], ...]
    schemas: tuple[Mapping[str, str], ...]

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


def _stream_response(spec: OperationSpec) -> ResponseSpec | None:
    """Return the first success response of an operation that declares an event stream, whatever its parameters."""
    return next(
        (
            response
            for response in spec.responses
            if response.success and any(_essence(item.media_type) == _EVENT_STREAM for item in response.media)
        ),
        None,
    )


class _Streams:
    """Check every enabled SSE helper against its operation and bind the schemas of its events."""

    def __init__(self, protocols: Protocols, request: TargetRequest, wire: WirePlan) -> None:
        """Index the documents by manifest pointer and the value use of each schema by its location."""
        self.protocols = protocols
        self.request = request
        self.wire = wire
        self.documents = {pointer: document for document, pointer in request.documents.pointers.items()}
        self.schemas: dict[tuple[SourceDocumentId, str], TypeUseBinding] = {
            (use.id.use_site.document, use.id.use_site.pointer): use
            for use in request.batch.type_uses
            if use.id.role == "schema" and use.id.projection == "value"
        }

    def location(self, reference: SchemaRef) -> SourceLocation | None:
        """Return the resolved location of a schema reference, or None when its document has no such pointer."""
        location = SourceLocation(self.documents[self.protocols.documents[reference]], reference.pointer, "schema")
        try:
            self.request.lease.borrow(location)
        except BindingCaptureError:
            return None
        return self.wire.schema(location)[0]

    def helper(self, helper: Helper, spec: OperationSpec) -> tuple[StreamSpec | None, list[Diagnostic]]:
        """Check one enabled helper's settings against its operation, and bind its schemas when every check passes."""
        tree = helper.tree
        at, name, media = helper.at, helper.name, tree["media"]
        problems: list[Diagnostic] = []
        if tree["resume"]["enabled"]:
            message = f"The SSE helper {name!r} resumes its stream, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.resume", message, spec))
        response = None
        if _essence(media) != _EVENT_STREAM:
            message = f"The media type {media!r} of {name!r} is not {_EVENT_STREAM}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.media", message, spec))
        elif (response := _stream_response(spec)) is None:
            message = f"{_label(spec)} declares no {_EVENT_STREAM} success response for the SSE helper {name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
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
        media = next(item.media_type for item in response.media if _essence(item.media_type) == _EVENT_STREAM)
        return StreamSpec(
            helper=helper,
            operation=spec,
            media=media,
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
        schema = self.schemas.get((location.document, location.pointer))
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
    """Check every enabled SSE helper before its codecs are planned, returning the helpers, their uses, and problems.

    Each use appears once, however many helpers decode its schema from the same response.
    """
    if protocols is None:
        return (), (), {}
    streams = _Streams(protocols, request, wire)
    operations = {spec.contract.id: spec for spec in plan.operations}
    specs: list[StreamSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "sse":
            continue
        spec = operations[protocols.operations[helper.links[0].ref].id]
        planned, problems[helper.name] = streams.helper(helper, spec)
        if planned is not None:
            specs.append(planned)
    uses = {use.id: use for spec in specs for use in spec.uses}
    return tuple(specs), tuple(uses.values()), problems


def plan_streams(  # noqa: PLR0913, PLR0917
    specs: tuple[StreamSpec, ...],
    protocols: Protocols | None,
    codecs: CodecPlan,
    wire: WirePlan,
    request: TargetRequest,
    problems: dict[str, list[Diagnostic]],
) -> tuple[StreamSpec, ...]:
    """Plan every SSE helper whose events have native codecs and whose body discriminator each schema declares.

    The problems found are added to each helper's.
    """
    if protocols is None or not specs:
        return ()
    bindings = dict(codecs.bindings)
    pages = _Pages(protocols, codecs, wire, request)
    planned: list[StreamSpec] = []
    for spec in specs:
        helper, operation = spec.helper, spec.operation
        found = problems[helper.name]
        for (where, _), use in zip(_references(helper), spec.uses, strict=True):
            binding = bindings[use.id]
            if binding.projection_mode != "native" or binding.converter_strategy == "registered_adapter":
                message = (
                    f"The SSE helper {helper.name!r} decodes an envelope-projected event, which is not supported yet"
                )
                found.append(_problem("E_CLIENT_UNSUPPORTED", "target", where, message, operation))
        found.extend(_discriminated(helper, spec, pages))
        if not found:
            planned.append(spec)
    return tuple(planned)


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
