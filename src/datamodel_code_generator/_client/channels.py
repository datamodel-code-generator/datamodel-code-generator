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

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef, SchemaRef
from datamodel_code_generator._client.config import OPTION_PREFIX
from datamodel_code_generator._client.naming import RESERVED_MEMBERS
from datamodel_code_generator._client.protocol_plan import Protocols, class_clashes
from datamodel_code_generator._client.protocols import Helper, Link
from datamodel_code_generator._client.sockets import direction_messages
from datamodel_code_generator._target_naming import NameScope
from datamodel_code_generator._target_operations import CLIENT, local, resolve_settings
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
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


def _members(messages: tuple[MessageContract, ...], action: str) -> list[MessageContract]:
    """Return the messages of one direction, each JSON payload once and each text or bytes codec once.

    Messages of one payload, and text or bytes messages, carry values of one type, so the first stands for the rest.
    """
    found: dict[object, MessageContract] = {}
    for message in messages:
        if message.action == action:
            found.setdefault(message.payload if message.codec == "json" else message.codec, message)
    return list(found.values())


def _definition(message: MessageContract, request: TargetRequest) -> dict[str, Any]:
    """Return the definition of one message: its codec, its frame, and the schema of a JSON message's payload."""
    definition: dict[str, Any] = {"codec": message.codec, "frame": _FRAMES.get(message.codec, "text")}
    if message.codec == "json" and (payload := message.payload) is not None:
        definition["schema"] = SchemaRef(pointer=payload.pointer, document=request.documents.pointers[payload.document])
    return definition


def _direction(members: list[MessageContract], request: TargetRequest) -> dict[str, Any]:
    """Return the message definition of one direction: its one message's, `none` without one, else all of them."""
    if not members:
        return {"codec": "none", "frame": "text"}
    if len(members) == 1:
        return _definition(members[0], request)
    return {"messages": [_definition(message, request) for message in members]}


def _names(request: TargetRequest, config: ClientGenerationConfig, channels: tuple[ChannelContract, ...]) -> list[str]:
    """Name each channel's attribute: the `resource` its `--client-operations` entry gives, else its derived name.

    A channel's name derives from the words of its id or address, `root` for an address without any. Derived names are
    told apart from each other and from the members of the clients. A given `resource` names one attribute, so it is
    refused when it is dotted or when another channel's takes it too; a name that gives the class an earlier name gave
    is refused at its channel's `resource`.
    """
    problems: list[Diagnostic] = []
    entries = (
        ("operation setting", f"operations[{index}].ref", item.ref, (index, item))
        for index, item in enumerate(config.operations)
    )
    settings = resolve_settings(CLIENT, request, entries, problems)
    names = request.batch.names
    assert names is not None
    given: dict[int, tuple[str, str]] = {}
    candidates = []
    taken = NameScope(folded=False, target_python_version=names.python_version)
    refused: list[Diagnostic] = []
    pointers = [channel.use_site.pointer for channel in channels]
    for index, channel in enumerate(channels):
        handshake = channel.handshake
        assert handshake is not None
        if (found := settings.get(pointers[index])) is not None and (resource := found[1].resource):
            given[index] = resource, (at := f"operations[{found[0]}].resource")
            if "." in resource:
                refused.append(_refusal("E_CONFIG_VALUE", at, "A channel's resource must be one Python identifier"))
            elif not taken.take(resource):
                message = f"The resource {stable_repr(resource)} names more than one channel"
                refused.append(_refusal("E_NAME_COLLISION", at, message))
        else:
            basis = "_".join(_WORDS.findall(channel.name)) or "root"
            candidates.append((index, (names.function(basis), local(handshake), ())))
    scope = NameScope(
        {*RESERVED_MEMBERS, *(name for name, _ in given.values())},
        folded=False,
        target_python_version=names.python_version,
    )
    claimed = dict(
        zip((index for index, _ in candidates), names.claim(scope, [item for _, item in candidates]), strict=True)
    )
    chosen = [given[index][0] if index in given else claimed[index] for index in range(len(channels))]
    if not refused:
        refused.extend(
            _refusal(
                "E_NAME_COLLISION", given.get(index, ("", f"operations[{pointers[index]!r}].resource"))[1], message
            )
            for index, message in class_clashes(((name, "websocket") for name in chosen), names.python_version)
        )
    if refused:
        raise APIGenerationError((*problems, *refused), option_prefix=OPTION_PREFIX)
    return chosen


def _refusal(code: str, at: str, message: str) -> Diagnostic:
    """Return the refusal of a channel's setting."""
    return Diagnostic(code=code, severity="error", stage="config", message=message, option_path=at)


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
        at = f"operations[{handshake.id.use_site.pointer!r}]"
        reference = OperationRef(pointer=handshake.id.use_site.pointer)
        operations[reference] = handshake
        members = {"send": _members(channel.messages, sent), "receive": _members(channel.messages, received)}
        tree: dict[str, Any] = {
            "kind": "websocket",
            "enabled": True,
            "operation": reference,
            "subprotocols": list(channel.subprotocols),
            **{direction: _direction(found, request) for direction, found in members.items()},
        }
        schemas = []
        for direction, found in members.items():
            for (where, definition), message in zip(
                direction_messages(f"{at}.{direction}", tree[direction]), found, strict=True
            ):
                if (schema := definition.get("schema")) is not None:
                    schemas.append((f"{where}.schema", schema))
                    documents[schema] = schema.document
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
