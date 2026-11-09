"""Plan the WebSocket helpers of a client target and the checks they must pass.

A WebSocket helper opens its channel through a GET operation's handshake and codes its JSON messages by their schemas.
The sent schemas are bound as request uses of the operation at the schemas' locations and the received ones as
response uses, each typed as the schema's own value use, so their codecs encode and decode messages; these uses join the
codec plan before it is made, as stream events do.

A direction of an AsyncAPI channel can carry several messages. A binary frame is its bytes message and a text frame
its text or JSON message; several JSON messages are told apart by a tag the models give them, a required field of one
string value that every model declares under one wire name, with distinct values. Without a tag, Pydantic models
decode or encode them as the union of their types, and a sent value of other models takes the message of its class;
a channel whose messages none of these tells apart is left out, as is one whose client receives text and JSON
messages, since any JSON is also text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator._api_types import Diagnostic, OperationRef
from datamodel_code_generator._client.pagination import _label, _problem  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator._client.streams import _Streams  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator._json_pointer import join_pointer
from datamodel_code_generator._runtime.model_codecs.media import JSON_MEDIA_TYPE
from datamodel_code_generator._target_contract import (
    DeclarationId,
    GeneratedEnumMember,
    GeneratedSymbolType,
    LiteralType,
    TypeUseBinding,
    TypeUseId,
    UnionType,
    literal_text,
)
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import SchemaRef
    from datamodel_code_generator._client.codec_plan import CodecBackend
    from datamodel_code_generator._client.model_facts import ModelFacts, ModelField
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._target_contract import TypeView
    from datamodel_code_generator._target_documents import JSONObject

__all__ = ("DEPENDENCY", "MessageRoute", "SocketSpec", "direction_messages", "plan_sockets", "socket_uses")

DEPENDENCY: Final = "httpx2[ws]>=2.13.0"
_DIRECTIONS: Final = (("send", "request"), ("receive", "response"))
_PYDANTIC: Final = frozenset({"pydantic_v2.BaseModel", "pydantic_v2.dataclass"})


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageRoute:
    """One message a WebSocket helper sends or receives: its codec and frame, and its JSON payload's use.

    A message of a direction that carries several also says what tells it apart: `tag`, the value the direction's
    discriminator reads from a received message, or that a sent mapping holds under the key `key`; or `type`, the class
    of the values it sends.
    """

    codec: str
    frame: str
    use: TypeUseBinding | None = None
    tag: str | None = None
    type: TypeView | None = None
    key: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SocketSpec:
    """A WebSocket helper ready to render: its handshake operation, the messages of each direction, and its schemas.

    A message of UTF-8 text or bytes has no use. `discriminator` is the pointer of the tag that tells the received
    JSON messages apart. `schemas` holds the source reference of every schema the helper codes, sent before received.
    """

    helper: Helper
    operation: OperationSpec
    sends: tuple[MessageRoute, ...]
    receives: tuple[MessageRoute, ...]
    schemas: tuple[JSONObject, ...]
    discriminator: str | None = None

    @property
    def uses(self) -> tuple[TypeUseBinding, ...]:
        """Return the use of every JSON message, sent before received."""
        return tuple(route.use for route in (*self.sends, *self.receives) if route.use is not None)


def direction_messages(at: str, definition: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Yield where each message of a WebSocket direction is defined, and its definition; `none` defines no message."""
    if (several := definition.get("messages")) is not None:
        for index, item in enumerate(several):
            yield f"{at}.messages[{index}]", item
    elif definition["codec"] != "none":
        yield at, definition


def _references(helper: Helper) -> Iterator[tuple[str, str, SchemaRef]]:
    """Yield where each JSON message of a helper is defined, where its schema is set, and the schema."""
    for direction, _ in _DIRECTIONS:
        for where, definition in direction_messages(f"{helper.at}.{direction}", helper.tree[direction]):
            if (schema := definition.get("schema")) is not None:
                yield where, f"{where}.schema", schema


def _tag_value(facts: ModelFacts, field: ModelField | None) -> str | None:
    """Return the one string a required field holds: a literal's or a one-member enum's, else None."""
    if field is None or not field.required:
        return None
    value = facts.plain(field.type)
    if isinstance(value, LiteralType) and len(value.values) == 1:
        if not isinstance(item := value.values[0], GeneratedEnumMember):
            return literal_text(item)
        symbol = facts.symbols[item.symbol]
        return next(
            literal_text(member)
            for slot, member in zip(symbol.fields, symbol.values, strict=True)
            if slot.field == item.field
        )
    if isinstance(value, GeneratedSymbolType) and len(values := facts.symbols[value.symbol].values) == 1:
        return literal_text(values[0])
    return None


def _tag(facts: ModelFacts, types: list[TypeView]) -> tuple[str, list[ModelField], list[str]] | None:
    """Return the tag of several messages' models: its wire name, each model's field of it, and each one's value.

    The tag is the first wire name of the first model's fields that every model declares as a required field of one
    string value, the values all distinct; None when the models declare none, or a type is no object model.
    """
    models = [facts.model(value) for value in types]
    fields = [{field.wire_name: field for field in facts.fields(model.id)} for model in models if model is not None]
    if len(fields) < len(types):
        return None
    for wire_name in fields[0]:
        found = [declared.get(wire_name) for declared in fields]
        values = [_tag_value(facts, field) for field in found]
        if None not in values and len(set(values)) == len(values):
            return wire_name, [field for field in found if field is not None], [str(value) for value in values]
    return None


def _replaced(members: list[MessageRoute], jsons: Sequence[MessageRoute | None]) -> tuple[MessageRoute, ...]:
    """Return the messages of a direction with each JSON message in turn replaced, None leaving it out."""
    replacing = iter(jsons)
    found = (next(replacing) if member.codec == "json" else member for member in members)
    return tuple(member for member in found if member is not None)


class _Sockets(_Streams):
    """Check every enabled WebSocket helper against its operation and bind the schemas of its messages."""

    def __init__(
        self, protocols: Protocols, plan: ClientPlan, request: TargetRequest, facts: ModelFacts, backend: CodecBackend
    ) -> None:
        """Keep the models' facts and backend, which tell several messages of a direction apart."""
        super().__init__(protocols, plan, request)
        self.facts = facts
        self.backend = backend

    def socket(self, helper: Helper, spec: OperationSpec) -> tuple[SocketSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operation and target, binding its schemas when every check passes."""
        at, name = helper.at, helper.name
        problems: list[Diagnostic] = []
        if spec.contract.method != "get":
            message = f"The WebSocket helper {stable_repr(name)} opens {_label(spec)}, which is not a GET operation"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
        elif spec.body is not None:
            message = f"The WebSocket helper {stable_repr(name)} opens {_label(spec)}, which takes a request body"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
        locations = self.locations(helper, spec, _references(helper), problems)
        if problems:
            return None, problems
        routes: dict[str, tuple[MessageRoute, ...]] = {}
        discriminator = None
        for direction, role in _DIRECTIONS:
            members = [
                MessageRoute(
                    codec=definition["codec"],
                    frame=definition["frame"],
                    use=None
                    if (place := locations.get(where)) is None
                    else self.bind(spec, place, role, JSON_MEDIA_TYPE),
                )
                for where, definition in direction_messages(f"{at}.{direction}", helper.tree[direction])
            ]
            found = self.told_apart(spec, direction, members)
            if isinstance(found, Diagnostic):
                return None, [found]
            routes[direction], pointer = found
            discriminator = discriminator or pointer
        return SocketSpec(
            helper=helper,
            operation=spec,
            sends=routes["send"],
            receives=routes["receive"],
            schemas=tuple(self.protocols.source(reference) for _, _, reference in _references(helper)),
            discriminator=discriminator,
        ), problems

    def told_apart(
        self, spec: OperationSpec, direction: str, members: list[MessageRoute]
    ) -> tuple[tuple[MessageRoute, ...], str | None] | Diagnostic:
        """Return the messages of a direction as told apart, with the received tag's pointer, or why they are not.

        Received JSON messages are told apart by their tag, sent ones by their class, or by their tag for TypedDict
        values, which are dicts; else Pydantic models code them as one union, at the place of the first. The messages
        keep their order, a class the first message of it.
        """
        jsons = [member for member in members if member.codec == "json"]
        received = direction == "receive"
        if received and jsons and any(member.codec == "utf8" for member in members):
            return self.skip(
                spec, "the client receives text and JSON messages on it, which a text frame cannot tell apart"
            )
        if len(jsons) < 2:  # noqa: PLR2004
            return tuple(members), None
        types = [member.use.type for member in jsons if member.use is not None and member.use.type is not None]
        typed = len(types) == len(jsons)
        if typed and (received or self.backend == "typing.TypedDict"):
            if (tag := _tag(self.facts, types)) is not None:
                wire_name, fields, values = tag
                tagged = [
                    replace(member, tag=value, key=None if received else field.name)
                    for member, field, value in zip(jsons, fields, values, strict=True)
                ]
                return _replaced(members, tagged), join_pointer((wire_name,)) if received else None
        elif typed and (classes := self.classes(types)) is not None:
            firsts = {value: member for member, value in reversed(list(zip(jsons, classes, strict=True)))}
            classed = [
                replace(member, type=value) if firsts[value] is member else None
                for member, value in zip(jsons, classes, strict=True)
            ]
            return _replaced(members, classed), None
        if typed and self.backend in _PYDANTIC:
            return _replaced(members, [self.union(spec, jsons, types), *(None for _ in jsons[1:])]), None
        verb = "receives" if received else "sends"
        return self.skip(
            spec,
            f"the client {verb} JSON messages on it that neither a tag nor a class tells apart, which only Pydantic "
            "models code as a union",
        )

    def classes(self, types: list[TypeView]) -> list[TypeView] | None:
        """Return the class each sent type stands for: a model or root model through aliases, else None."""
        found = []
        for value in types:
            plain = self.facts.plain(value)
            if self.facts.symbol(plain) is None:
                return None
            found.append(plain)
        return found

    @staticmethod
    def union(spec: OperationSpec, jsons: list[MessageRoute], types: list[TypeView]) -> MessageRoute:
        """Return the one JSON message that codes several by the union of their types, a use at the channel itself."""
        first = jsons[0].use
        assert first is not None
        location = spec.contract.id.use_site
        members = tuple(
            dict.fromkeys(
                item for value in types for item in (value.members if isinstance(value, UnionType) else (value,))
            )
        )
        use = TypeUseId(
            owner=spec.contract.id,
            role=first.id.role,
            use_site=location,
            schema_site=location,
            declaration=DeclarationId(location),
            direction=first.id.direction,
            media=JSON_MEDIA_TYPE,
        )
        return MessageRoute(
            codec="json",
            frame="text",
            use=TypeUseBinding(use, "bound", UnionType(members, preserve_order=True), None, schema=location),
        )

    @staticmethod
    def skip(spec: OperationSpec, message: str) -> Diagnostic:
        """Return why a channel is left out: messages of a direction that its client cannot tell apart."""
        pointer = spec.contract.id.use_site.pointer
        return Diagnostic(
            code="E_CLIENT_UNSUPPORTED",
            severity="error",
            stage="target",
            message=message,
            source_pointer=pointer,
            operation=OperationRef(pointer=pointer),
            feature="several_messages",
        )


def socket_uses(
    protocols: Protocols | None, plan: ClientPlan, request: TargetRequest, facts: ModelFacts, backend: CodecBackend
) -> tuple[tuple[SocketSpec, ...], tuple[TypeUseBinding, ...], dict[str, list[Diagnostic]]]:
    """Check every enabled WebSocket helper before its codecs are planned, returning the helpers, uses, and problems.

    Each use appears once, however many helpers code its schema through the same operation.
    """
    if protocols is None or not any(helper.enabled and helper.kind == "websocket" for helper in protocols.helpers):
        return (), (), {}
    sockets = _Sockets(protocols, plan, request, facts, backend)
    return sockets.coded(("websocket",), sockets.socket)


def plan_sockets(specs: tuple[SocketSpec, ...], problems: dict[str, list[Diagnostic]]) -> tuple[SocketSpec, ...]:
    """Plan every WebSocket helper without a problem."""
    return tuple(spec for spec in specs if not problems[spec.helper.name])
