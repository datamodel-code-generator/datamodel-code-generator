"""Read the WebSocket channels of an AsyncAPI document, which a client opens through their handshakes.

This module reads one loaded document as written and nothing else: the servers each channel is served on, its address
and parameters, the messages its operations carry and whether the described application sends or receives each, and
why a client cannot open a channel. AsyncAPI 2 names the application's sends `subscribe` and its receives `publish`;
AsyncAPI 3 names them by the operation's `action`. Which of them a client sends is the client's setting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._document_reader import as_mapping
from datamodel_code_generator._json_pointer import join_pointer, reference_tokens

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._source import YamlValue

__all__ = ("ReadChannel", "ReadMessage", "ReadParameter", "ReadServer", "read")

Tokens: TypeAlias = tuple[str, ...]
Action: TypeAlias = Literal["send", "receive"]

_SCHEMES: Final = {"ws": "http", "wss": "https"}
_V2_ACTIONS: Final[tuple[tuple[str, Action], ...]] = (("subscribe", "send"), ("publish", "receive"))
_JSON_FORMATS: Final = re.compile(r"application/(vnd\.aai\.asyncapi|schema)(\+(json|yaml))?(;.*)?")
_SCHEME: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_DEFAULT_MEDIA: Final = "application/json"


@dataclass(frozen=True, slots=True)
class ReadServer:
    """A WebSocket server of a channel: its name, its handshake URL template, and the variables the template uses."""

    name: str
    url: str
    variables: YamlValue


@dataclass(frozen=True, slots=True)
class ReadParameter:
    """A parameter of a channel's address: its name, where it is declared, and its schema (AsyncAPI 2), or None."""

    name: str
    declaration: Tokens
    schema: Tokens | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ReadMessage:
    """A message a channel carries: where it is declared, what the application does, its codec, and its payload.

    `payload` is where the payload schema is written, and `payload_ref` the `$ref` of a payload that is only one, which
    the message binds as the schema it names.
    """

    declaration: Tokens
    action: Action
    codec: Literal["json", "utf8", "bytes"]
    payload: Tokens
    payload_ref: str | None


@dataclass(frozen=True, slots=True)
class ReadChannel:
    """A channel as a client opens it, or the features and reasons that leave it out (`skips`).

    `declaration` is where the channel is written and `use_site` where the document lists it. `name` is the channel's
    id (AsyncAPI 3) or its address (AsyncAPI 2).
    """

    use_site: Tokens
    declaration: Tokens
    name: str
    address: str | None
    servers: tuple[ReadServer, ...] = ()
    parameters: tuple[ReadParameter, ...] = ()
    messages: tuple[ReadMessage, ...] = ()
    skips: tuple[tuple[str | None, str], ...] = field(default=())


class _Document:
    """One loaded document and the local references in it, followed to the values they name."""

    def __init__(self, raw: Mapping[str, YamlValue]) -> None:
        self.raw = raw

    def value(self, tokens: Tokens) -> YamlValue:
        """Return the value at a pointer's tokens through mappings, or None where the document has none."""
        node: YamlValue = dict(self.raw)
        for token in tokens:
            node = as_mapping(node).get(token)
        return node

    def resolved(self, value: YamlValue, tokens: Tokens) -> tuple[YamlValue, Tokens] | None:
        """Return a value and its pointer, following local references to what they name.

        None is a reference to another document, or one that loops; a local reference to nothing names None.
        """
        seen: set[Tokens] = set()
        while isinstance(value, dict) and isinstance(ref := value.get("$ref"), str):
            if not ref.startswith("#") or (target := reference_tokens(ref[1:])) is None or target in seen:
                return None
            seen.add(target)
            value, tokens = self.value(target), target
        return value, tokens


def _media(message: Mapping[str, YamlValue], default: str) -> str:
    content = message.get("contentType")
    return (content if isinstance(content, str) else default).partition(";")[0].strip().lower()


def _json(media: str) -> bool:
    return media == _DEFAULT_MEDIA or media.endswith("+json")


class _Reader:
    """Read the channels of one document by its AsyncAPI version."""

    def __init__(self, raw: Mapping[str, YamlValue]) -> None:
        self.document = _Document(raw)
        version = raw.get("asyncapi")
        self.v2 = isinstance(version, str) and version.startswith("2.")
        default = raw.get("defaultContentType")
        self.default_media = default if isinstance(default, str) else _DEFAULT_MEDIA
        self.servers = self.read_servers()

    def read_servers(self) -> dict[str, tuple[ReadServer | None, bool]]:
        """Return each server by name: its WebSocket form (None for another protocol), and whether it needs security."""
        servers: dict[str, tuple[ReadServer | None, bool]] = {}
        holder = ("servers",)
        for key, value in as_mapping(self.document.value(holder)).items():
            if (found := self.document.resolved(value, (*holder, name := str(key)))) is None:
                continue
            server = as_mapping(found[0])
            secured = bool(server.get("security"))
            if (scheme := _SCHEMES.get(str(server.get("protocol", "")).lower())) is None:
                servers[name] = None, secured
                continue
            if self.v2:
                written = str(server.get("url", ""))
                location = written[match.end() :] if (match := _SCHEME.match(written)) else written
            else:
                location = f"{server.get('host', '')}{server.get('pathname') or ''}"
            variables = self.document.resolved(server.get("variables"), ())
            servers[name] = (
                ReadServer(name, f"{scheme}://{location}", None if variables is None else variables[0]),
                secured,
            )
        return servers

    def channels(self) -> Iterator[ReadChannel]:
        """Yield every channel the document lists, in document order."""
        holder = ("channels",)
        operations = [] if self.v2 else self.operations()
        for key, value in as_mapping(self.document.value(holder)).items():
            use_site = (*holder, name := str(key))
            if (found := self.document.resolved(value, use_site)) is None:
                yield ReadChannel(use_site, use_site, name, None, skips=((None, "it references another document"),))
                continue
            channel, declaration = as_mapping(found[0]), found[1]
            yield self.channel(use_site, declaration, name, channel, operations)

    def operations(self) -> list[tuple[Tokens, Mapping[str, YamlValue]]]:
        """Return each AsyncAPI 3 operation with the pointer of the channel its reference names, through references."""
        found: list[tuple[Tokens, Mapping[str, YamlValue]]] = []
        holder = ("operations",)
        for key, value in as_mapping(self.document.value(holder)).items():
            if (operation := self.document.resolved(value, (*holder, str(key)))) is None:
                continue
            raw = as_mapping(operation[0])
            if (channel := self.document.resolved(raw.get("channel"), ())) is not None and channel[1]:
                found.append((channel[1], raw))
        return found

    def channel(
        self,
        use_site: Tokens,
        declaration: Tokens,
        key: str,
        channel: Mapping[str, YamlValue],
        operations: list[tuple[Tokens, Mapping[str, YamlValue]]],
    ) -> ReadChannel:
        """Read one channel: its servers, address, parameters and messages, and every reason a client cannot open it."""
        skips: list[tuple[str | None, str]] = []
        address = key if self.v2 else channel.get("address")
        if not isinstance(address, str):
            skips.append(("channel_addresses", "it has no address"))
        servers, secured = self.channel_servers(channel)
        if not servers:
            skips.append(("server_protocols", "it is served on no ws or wss server"))
        if secured:
            skips.append(("security_requirements", "a server it is served on requires security"))
        binding = as_mapping(as_mapping(channel.get("bindings")).get("ws"))
        if str(binding.get("method", "GET")).upper() != "GET":
            skips.append(("websocket_bindings", f"its WebSocket binding opens it with {binding.get('method')}"))
        skips.extend(
            ("websocket_bindings", f"its WebSocket binding declares {name}")
            for name in ("query", "headers")
            if name in binding
        )
        if self.v2:
            messages = list(self.v2_messages(declaration, channel, skips))
        else:
            messages = list(self.v3_messages(declaration, channel, operations, skips))
        for action in ("send", "receive"):
            if len({message.declaration for message in messages if message.action == action}) > 1:
                verb = "sends" if action == "send" else "receives"
                skips.append(("several_messages", f"the application {verb} more than one message on it"))
        if not messages and not skips:
            skips.append((None, "no operation sends or receives a message on it"))
        return ReadChannel(
            use_site,
            declaration,
            key,
            address if isinstance(address, str) else None,
            tuple(servers),
            tuple(self.parameters(declaration, channel)),
            tuple(dict.fromkeys(messages)),
            tuple(dict.fromkeys(skips)),
        )

    def channel_servers(self, channel: Mapping[str, YamlValue]) -> tuple[list[ReadServer], bool]:
        """Return the WebSocket servers a channel is served on, and whether any of them requires security.

        A channel that lists no servers is served on every server of the document.
        """
        listed = channel.get("servers")
        if not isinstance(listed, list):
            names = list(self.servers)
        elif self.v2:
            names = [str(name) for name in listed]
        else:
            names = [
                tokens[1]
                for item in listed
                if isinstance(ref := as_mapping(item).get("$ref"), str)
                and ref.startswith("#")
                and (tokens := reference_tokens(ref[1:])) is not None
                and len(tokens) == 2  # noqa: PLR2004
                and tokens[0] == "servers"
            ]
        found = [self.servers[name] for name in names if name in self.servers]
        return [server for server, _ in found if server is not None], any(
            secured for server, secured in found if server is not None
        )

    def parameters(self, declaration: Tokens, channel: Mapping[str, YamlValue]) -> Iterator[ReadParameter]:
        """Yield the parameters of a channel's address, with the schema an AsyncAPI 2 parameter declares."""
        holder = (*declaration, "parameters")
        for key, value in as_mapping(channel.get("parameters")).items():
            if (found := self.document.resolved(value, (*holder, name := str(key)))) is None:
                continue
            parameter, written = as_mapping(found[0]), found[1]
            schema = (*written, "schema") if self.v2 and "schema" in parameter else None
            description = parameter.get("description")
            yield ReadParameter(name, written, schema, description if isinstance(description, str) else None)

    def v2_messages(
        self, declaration: Tokens, channel: Mapping[str, YamlValue], skips: list[tuple[str | None, str]]
    ) -> Iterator[ReadMessage]:
        """Yield the messages of an AsyncAPI 2 channel's operations, and their alternatives under `oneOf`."""
        for name, action in _V2_ACTIONS:
            if not isinstance(operation := channel.get(name), dict):
                continue
            if operation.get("security"):
                skips.append(("security_requirements", f"its {name} operation requires security"))
            holder = (*declaration, name, "message")
            if (found := self.document.resolved(operation.get("message"), holder)) is None:
                skips.append((None, "a message of it references another document"))
                continue
            message, written = found
            alternatives = as_mapping(message).get("oneOf")
            if isinstance(alternatives, list):
                for index, item in enumerate(alternatives):
                    yield from self.message(item, (*written, "oneOf", str(index)), action, skips)
            elif message is not None:
                yield from self.message(message, written, action, skips)

    def v3_messages(
        self,
        declaration: Tokens,
        channel: Mapping[str, YamlValue],
        operations: list[tuple[Tokens, Mapping[str, YamlValue]]],
        skips: list[tuple[str | None, str]],
    ) -> Iterator[ReadMessage]:
        """Yield the messages of the AsyncAPI 3 operations on a channel: those an operation lists, else all of them."""
        holder = (*declaration, "messages")
        own = [(str(key), value) for key, value in as_mapping(channel.get("messages")).items()]
        for target, operation in operations:
            if target != declaration:
                continue
            action = operation.get("action")
            if action not in {"send", "receive"}:
                continue
            if operation.get("security"):
                skips.append(("security_requirements", "an operation on it requires security"))
            if "reply" in operation:
                skips.append(("operation_replies", "an operation on it declares a reply"))
            listed = operation.get("messages")
            items: list[tuple[YamlValue, Tokens]] = (
                [(value, ()) for value in listed]
                if isinstance(listed, list)
                else [(value, (*holder, key)) for key, value in own]
            )
            for item, written in items:
                yield from self.message(item, written, action, skips)

    def message(
        self, value: YamlValue, written: Tokens, action: object, skips: list[tuple[str | None, str]]
    ) -> Iterator[ReadMessage]:
        """Yield one message as the application sends or receives it, recording why a client cannot code it."""
        if (found := self.document.resolved(value, written)) is None:
            skips.append((None, "a message of it references another document"))
            return
        message, declaration = as_mapping(found[0]), found[1]
        pointer = join_pointer(declaration)
        payload = message.get("payload")
        schema_format = message.get("schemaFormat")
        tokens: Tokens = (*declaration, "payload")
        if isinstance(payload, dict) and "schemaFormat" in payload and "schema" in payload:
            schema_format, payload, tokens = payload["schemaFormat"], payload["schema"], (*tokens, "schema")
        if isinstance(schema_format, str) and not _JSON_FORMATS.fullmatch(schema_format):
            skips.append(("message_content_types", f"the payload of message {pointer} is a {schema_format} schema"))
            return
        if payload is None:
            skips.append(("message_content_types", f"message {pointer} declares no payload"))
            return
        ref = as_mapping(payload).get("$ref")
        payload_ref = ref if isinstance(ref, str) and len(as_mapping(payload)) == 1 else None
        media = _media(message, self.default_media)
        target = self.document.resolved(payload, tokens)
        schema = as_mapping(None if target is None else target[0])
        binary = schema.get("format") == "binary" or "contentEncoding" in schema
        if media == "application/octet-stream" or (binary and not _json(media)):
            codec: Literal["json", "utf8", "bytes"] = "bytes"
        elif media == "text/plain" and schema.get("type") == "string":
            codec = "utf8"
        elif _json(media):
            codec = "json"
        else:
            skips.append(("message_content_types", f"message {pointer} has content type {media}"))
            return
        yield ReadMessage(declaration, "send" if action == "send" else "receive", codec, tokens, payload_ref)


def read(raw: Mapping[str, YamlValue]) -> tuple[ReadChannel, ...]:
    """Return every channel of a loaded AsyncAPI document, in document order."""
    return tuple(_Reader(raw).channels())
