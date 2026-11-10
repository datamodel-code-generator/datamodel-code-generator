"""Open the channels of an AsyncAPI input as the client's WebSocket helpers.

Each channel a client can open becomes a WebSocket helper whose entry operation is the channel's handshake, a GET of
its address on its WebSocket servers, and whose messages are the channel's: so its plans, sessions, limits and errors
are those of every WebSocket helper. The client exposes each one as an attribute of its own, named after the channel.
`asyncapi_perspective` says which side the document describes: the client of a server's document sends what the
server receives.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator._api_types import OperationRef, SchemaRef
from datamodel_code_generator._client.naming import RESERVED_MEMBERS
from datamodel_code_generator._client.protocol_plan import Protocols
from datamodel_code_generator._client.protocols import Helper, Link
from datamodel_code_generator._target_naming import NameScope
from datamodel_code_generator._target_operations import CLIENT, local, resolve_settings

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import Diagnostic
    from datamodel_code_generator._client.config import ClientGenerationConfig
    from datamodel_code_generator._target_contract import (
        ChannelContract,
        MessageContract,
        OperationContract,
        SourceLocation,
    )

__all__ = ("channel_protocols",)

_FRAMES: Final = {"bytes": "binary"}
_WORDS: Final = re.compile(r"[^\W_]+")
_SENT: Final = {"server": "receive", "client": "send"}
"""The action of the described application whose messages the client sends, by the side the document describes."""


def _message(messages: tuple[MessageContract, ...], action: str, request: TargetRequest) -> dict[str, Any]:
    """Return the message definition of one direction: the codec of its one message, or `none` without one."""
    found = next((message for message in messages if message.action == action), None)
    if found is None:
        return {"codec": "none", "frame": "text"}
    definition: dict[str, Any] = {"codec": found.codec, "frame": _FRAMES.get(found.codec, "text")}
    if found.codec == "json" and (payload := found.payload) is not None:
        definition["schema"] = SchemaRef(pointer=payload.pointer, document=request.documents.pointers[payload.document])
    return definition


def _names(request: TargetRequest, config: ClientGenerationConfig, channels: tuple[ChannelContract, ...]) -> list[str]:
    """Name each channel's attribute: the `resource` its `--client-operations` entry gives, else its derived name.

    A channel's name derives from the words of its id or address, `root` for an address without any. Derived names are
    told apart from each other and from the members of the clients.
    """
    problems: list[Diagnostic] = []
    entries = (
        ("operation setting", f"operations[{index}].ref", item.ref, item)
        for index, item in enumerate(config.operations)
    )
    settings = resolve_settings(CLIENT, request, entries, problems)
    names = request.batch.names
    assert names is not None
    given: dict[int, str] = {}
    candidates = []
    for index, channel in enumerate(channels):
        handshake = channel.handshake
        assert handshake is not None
        if (setting := settings.get(handshake.id.use_site.pointer)) is not None and setting.resource is not None:
            given[index] = setting.resource
        else:
            basis = "_".join(_WORDS.findall(channel.name)) or "root"
            candidates.append((index, (names.function(basis), local(handshake), ())))
    scope = NameScope({*RESERVED_MEMBERS, *given.values()}, folded=False, target_python_version=names.python_version)
    claimed = dict(
        zip((index for index, _ in candidates), names.claim(scope, [item for _, item in candidates]), strict=True)
    )
    return [given.get(index) or claimed[index] for index in range(len(channels))]


def channel_protocols(
    request: TargetRequest, config: ClientGenerationConfig, channels: tuple[ChannelContract, ...]
) -> Protocols:
    """Return the WebSocket helpers of the channels a client opens, each linked to its handshake operation."""
    sent = _SENT[config.asyncapi_perspective]
    received = "send" if sent == "receive" else "receive"
    helpers: list[Helper] = []
    operations: dict[OperationRef, OperationContract] = {}
    documents: dict[SchemaRef, str] = {}
    locations: dict[SchemaRef, SourceLocation] = {}
    for channel, name in zip(channels, _names(request, config, channels), strict=True):
        handshake = channel.handshake
        assert handshake is not None
        at = f"channels[{handshake.path!r}]"
        reference = OperationRef(pointer=handshake.id.use_site.pointer)
        operations[reference] = handshake
        tree: dict[str, Any] = {
            "kind": "websocket",
            "enabled": True,
            "operation": reference,
            "subprotocols": [],
            "send": _message(channel.messages, sent, request),
            "receive": _message(channel.messages, received, request),
        }
        schemas = []
        for direction in ("send", "receive"):
            if (schema := tree[direction].get("schema")) is not None:
                schemas.append((f"{at}.{direction}.schema", schema))
                documents[schema] = schema.document
                message = next(
                    item for item in channel.messages if item.action == (sent if direction == "send" else received)
                )
                assert message.payload is not None
                locations[schema] = message.payload
        helpers.append(
            Helper(
                name=name,
                kind="websocket",
                enabled=True,
                at=at,
                tree=tree,
                links=(Link(at=f"{at}.operation", ref=reference),),
                schemas=tuple(schemas),
            )
        )
    return Protocols(
        helpers=tuple(helpers),
        operations=operations,
        documents=documents,
        locations=locations,
        python_version=request.model_config.target_python_version,
    )
