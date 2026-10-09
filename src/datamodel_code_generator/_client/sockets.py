"""Plan the WebSocket helpers of a client target and the checks they must pass.

A WebSocket helper opens its channel through a GET operation's handshake and codes its JSON messages by their schemas.
The sent schema is bound as a request use of the operation at the schema's location and the received one as a
response use, each typed as the schema's own value use, so their codecs encode and decode messages; these uses join the
codec plan before it is made, as stream events do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client.pagination import _label, _problem  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator._client.plan import schema_use
from datamodel_code_generator._client.streams import _Streams  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator._target_contract import DeclarationId, TypeUseBinding, TypeUseId

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import Diagnostic, SchemaRef
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._target_contract import SourceLocation

__all__ = ("DEPENDENCY", "SocketSpec", "plan_sockets", "socket_uses")

DEPENDENCY: Final = "websockets>=17.1"
_JSON: Final = "application/json"
_DIRECTIONS: Final = ("send", "receive")


@dataclass(frozen=True, slots=True, kw_only=True)
class SocketSpec:
    """A WebSocket helper ready to render: its handshake operation, the use of each JSON message, and its schemas.

    A direction whose messages are UTF-8 text or bytes has no use. `schemas` holds the manifest reference of every
    schema the helper codes, sent before received.
    """

    helper: Helper
    operation: OperationSpec
    send: TypeUseBinding | None
    receive: TypeUseBinding | None
    schemas: tuple[Mapping[str, str], ...]

    @property
    def uses(self) -> tuple[TypeUseBinding, ...]:
        """Return the use of every JSON message, sent before received."""
        return tuple(use for use in (self.send, self.receive) if use is not None)


def _references(helper: Helper) -> Iterator[tuple[str, str, SchemaRef]]:
    """Yield the direction of each JSON message of a helper, where its schema is set, and the schema."""
    for direction in _DIRECTIONS:
        if (schema := helper.tree[direction].get("schema")) is not None:
            yield direction, f"{helper.at}.{direction}.schema", schema


class _Sockets(_Streams):
    """Check every enabled WebSocket helper against its operation and bind the schemas of its messages."""

    def socket(self, helper: Helper, spec: OperationSpec) -> tuple[SocketSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operation and target, binding its schemas when every check passes."""
        at, name = helper.at, helper.name
        problems: list[Diagnostic] = []
        if spec.contract.method != "get":
            message = f"The WebSocket helper {name!r} opens {_label(spec)}, which is not a GET operation"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
        elif spec.body is not None:
            message = f"The WebSocket helper {name!r} opens {_label(spec)}, which takes a request body"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
        locations: dict[str, SourceLocation] = {}
        for direction, where, reference in _references(helper):
            if (location := self.location(reference)) is None:
                message = f"The schema {reference.pointer!r} of {name!r} does not exist in its document"
                problems.append(_problem("E_CONFIG_VALUE", "config", where, message, spec))
            else:
                locations[direction] = location
        if problems:
            return None, problems
        return SocketSpec(
            helper=helper,
            operation=spec,
            send=None if (sent := locations.get("send")) is None else self.message(spec, sent, sent=True),
            receive=None if (received := locations.get("receive")) is None else self.message(spec, received),
            schemas=tuple(
                {"document": self.protocols.documents[reference], "pointer": reference.pointer}
                for _, _, reference in _references(helper)
            ),
        ), problems

    def message(self, spec: OperationSpec, location: SourceLocation, *, sent: bool = False) -> TypeUseBinding:
        """Return the use that codes a schema as the channel's sent or received messages, bound as its value use is."""
        schema = schema_use(self.schemas, location, "request" if sent else "response")
        use = TypeUseId(
            owner=spec.contract.id,
            role="request_body" if sent else "response_body",
            use_site=location,
            schema_site=location,
            declaration=DeclarationId(location) if schema is None else schema.id.declaration,
            direction="request" if sent else "response",
            media=_JSON,
        )
        if schema is None:
            return TypeUseBinding(use, "not_generated", None, None, schema=location)
        return TypeUseBinding(use, schema.state, schema.type, schema.reason, schema=location)


def socket_uses(
    protocols: Protocols | None, plan: ClientPlan, request: TargetRequest
) -> tuple[tuple[SocketSpec, ...], tuple[TypeUseBinding, ...], dict[str, list[Diagnostic]]]:
    """Check every enabled WebSocket helper before its codecs are planned, returning the helpers, uses, and problems.

    Each use appears once, however many helpers code its schema through the same operation.
    """
    if protocols is None or not any(helper.enabled and helper.kind == "websocket" for helper in protocols.helpers):
        return (), (), {}
    sockets = _Sockets(protocols, request)
    operations = {spec.contract.id: spec for spec in plan.operations}
    specs: list[SocketSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "websocket":
            continue
        spec = operations[protocols.operations[helper.links[0].ref].id]
        planned, problems[helper.name] = sockets.socket(helper, spec)
        if planned is not None:
            specs.append(planned)
    uses = {use.id: use for spec in specs for use in spec.uses}
    return tuple(specs), tuple(uses.values()), problems


def plan_sockets(specs: tuple[SocketSpec, ...], problems: dict[str, list[Diagnostic]]) -> tuple[SocketSpec, ...]:
    """Plan every WebSocket helper without a problem."""
    return tuple(spec for spec in specs if not problems[spec.helper.name])
