"""Read the channels of an AsyncAPI document, which a client opens through their handshakes.

A channel is a WebSocket, opened by its handshake, or a server-sent event stream, opened by a GET and served on
`http`, `https` or `sse` servers: one whose messages include a `text/event-stream` one, one an `sse` server serves, or
one whose HTTP binding asks for `text/event-stream`. This module reads one loaded document as written: the servers
each channel is served on, its address and parameters, the query and header parameters its binding gives its
handshake, which are the properties the models hold of the binding's objects (`members`), the security the handshake
needs, the messages its operations carry and whether the described application sends or receives each, and why a
client cannot open a channel. AsyncAPI 2 names the application's sends `subscribe`
and its receives `publish`; AsyncAPI 3 names them by the operation's `action`. Which of them a client sends on a
WebSocket is the client's setting, and a client receives every message of a stream.

A client opens the channels at one address on the same servers as one connection, which carries the messages of
them all, the replies of their AsyncAPI 3 operations among them. A connection is opened only when everything a client
needs to open it resolves: the operations of its channels and their traits, its messages and their traits, and the
schemas of their payloads. Traits are merged as the document's version merges them first. A reference leads where the
parser resolves it (`target`), into any document the generation loaded; one that leads into no loaded document, or
one that loops, leaves the channel out, and an operation it names, which may be on any channel, leaves every channel
out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._document_reader import (
    CHANNEL_OPERATIONS,
    as_mapping,
    held_schema,
    schema_format_media_type,
    trait_layers,
)
from datamodel_code_generator._openapi_wire_plan import EVENT_STREAM
from datamodel_code_generator._runtime.model_codecs.media import is_token
from datamodel_code_generator._target_naming import PATH_PLACEHOLDER, path_placeholders
from datamodel_code_generator.parser.asyncapi import schema_format_kind
from datamodel_code_generator.reference import SchemaLocation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import LiteralScalar, TypeView
    from datamodel_code_generator.types import DataType

__all__ = (
    "Members",
    "ReadChannel",
    "ReadDocument",
    "ReadHandshakeParameter",
    "ReadMember",
    "ReadMessage",
    "ReadParameter",
    "ReadScheme",
    "ReadServer",
    "Requirement",
    "Walk",
    "WalkedMessage",
    "read",
)

Action: TypeAlias = Literal["send", "receive"]
Requirement: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
"""One alternative of a security requirement: each scheme it needs, by name, with the scopes it needs of it."""
_Unordered: TypeAlias = frozenset[frozenset[tuple[str, frozenset[str]]]]
"""A security's alternatives, each the schemes it needs with their scopes, in no order."""
Skips: TypeAlias = "list[tuple[str | None, str]]"
_Operation: TypeAlias = "tuple[SchemaLocation, SchemaLocation, Mapping[str, YamlValue]]"
"""An AsyncAPI 3 operation: where its channel is, where it is, and the operation as written."""
_Layers: TypeAlias = "list[tuple[Mapping[str, YamlValue], SchemaLocation]]"
"""An operation or a message and its traits, each with where it is, in the order they apply (`_layers`)."""
_Security: TypeAlias = "tuple[YamlValue, SchemaLocation]"
"""A security as written, and where the object that holds it is, which its references resolve from."""

Kind: TypeAlias = Literal["websocket", "sse"]
"""How a client opens a channel: as a WebSocket, or as a server-sent event stream."""

_SCHEMES: Final[dict[str, tuple[str | None, Kind]]] = {
    "ws": ("http", "websocket"),
    "wss": ("https", "websocket"),
    "http": ("http", "sse"),
    "https": ("https", "sse"),
    "sse": (None, "sse"),
}
"""The scheme of the URL a client opens a server's channels at, and how it opens them, by the server's protocol.

The scheme is None for a protocol that says nothing of TLS (`_joined`)."""
_PROTOCOLS: Final[dict[Kind, str]] = {"websocket": "ws or wss", "sse": "http, https or sse"}
"""The protocols of the servers that serve a channel, by how a client opens it."""
_URL_SCHEMES: Final = {"ws": "http", "wss": "https", "http": "http", "https": "https"}
"""The scheme of the URL a client opens a server's channels at, by the scheme the server's URL is written with: a
WebSocket's `wss` is TLS, as `https` is, and its `ws` plain, as `http` is."""
_V2_ACTIONS: Final[dict[str, Action]] = {"publish": "receive", "subscribe": "send"}
"""What the application does with the messages of an AsyncAPI 2 channel's operation, by the operation's field; the
operations are read in the parser's order (`CHANNEL_OPERATIONS`)."""
_CODED_FORMATS: Final = frozenset(("asyncapi", "jsonschema", "openapi"))
"""The kinds of `schemaFormat`, the parser's, whose payloads a client codes: those it reads in place, not the Avro,
Protocol Buffers and XML Schema ones it converts; the format a trait gives, which the parser does not read, too."""
_SCHEME: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_DEFAULT_MEDIA: Final = "application/json"
_TRAIT_FIELDS: Final = frozenset(("contentType", "schemaFormat", "security"))
"""The fields a trait gives an operation or a message that the reader decides on."""
_SCHEME_TYPES: Final = {"httpApiKey": "apiKey", "http": "http", "oauth2": "oauth2", "openIdConnect": "openIdConnect"}
"""The security scheme types a handshake sends, an HTTP request's, by the type OpenAPI names each."""
_SCHEME_FIELDS: Final = ("description", "name", "in", "scheme", "bearerFormat", "openIdConnectUrl")
_BINDINGS: Final[dict[Kind, tuple[str, str, str]]] = {
    "websocket": ("ws", "WebSocket binding", "websocket_bindings"),
    "sse": ("http", "HTTP binding", "server_sent_events"),
}
"""The binding that gives a channel's handshake its inputs, by how a client opens it: its key, its name in a message,
and the feature it is."""
_HANDSHAKE_INPUTS: Final[tuple[tuple[str, Literal["query", "header"]], ...]] = (
    ("query", "query"),
    ("headers", "header"),
)
_SCHEMES_POINTER: Final = ("components", "securitySchemes")
_MANAGED_HEADERS: Final = frozenset({
    "connection",
    "content-length",
    "host",
    "sec-websocket-accept",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
    "transfer-encoding",
    "upgrade",
})
"""The lowercase headers a client's WebSocket handshake writes itself, which the runtime refuses from a call."""
_STREAM_HEADERS: Final = frozenset({"connection", "content-length", "host", "transfer-encoding"})
"""The lowercase headers the GET that opens an event stream frames itself, which the runtime refuses from a call."""
_NEGOTIATED: Final = frozenset({"accept", "content-type"})
"""The lowercase headers of a request's media, which a client writes itself, as OpenAPI ignores them as parameters."""
_SUBPROTOCOL: Final = "sec-websocket-protocol"
_INLINE: Final = "a scheme written inline"
_FOREIGN: Final = "a scheme in another document"


@dataclass(frozen=True, slots=True)
class ReadServer:
    """A server of a channel: its name, its handshake URL template, the variables the template uses, and its kind.

    `kind` is how a client opens the channels it serves: a `ws` or `wss` server's as WebSockets, and an `http`,
    `https` or `sse` server's as server-sent event streams. `protocol` is the server's protocol, lowercase.
    """

    name: str
    url: str
    variables: YamlValue
    kind: Kind = "websocket"
    protocol: str = ""


@dataclass(frozen=True, slots=True)
class ReadParameter:
    """A parameter of a channel's address: its name, where it is declared, and its schema (AsyncAPI 2), or None."""

    name: str
    declaration: SchemaLocation
    schema: SchemaLocation | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ReadMember:
    """A property of an object schema as the models hold it: a field of the object's model.

    `required` is whether the model requires the field, `fixed` the values its type admits where it admits literal
    values alone, else None (``TypeTables.fixed``), `type` the field's type, `bound` that type as the models bind it
    (``ModelFieldFacts.type``), `schema` where the models declare the property, `written` its schema there as
    written, and `default` the model field's default (``ModelFieldFacts.default``).
    """

    name: str
    required: bool
    fixed: tuple[object, ...] | None
    type: DataType
    bound: TypeView
    schema: SchemaLocation
    written: YamlValue
    default: LiteralScalar | None = None


Members: TypeAlias = "Callable[[SchemaLocation], tuple[ReadMember, ...] | str]"
"""The properties the models hold of the object schema at a location, or why no parameters stand for it, as a
phrase."""


@dataclass(frozen=True, slots=True)
class ReadHandshakeParameter:
    """A query or header parameter of a channel's handshake: a property of its binding's `query` or `headers`."""

    location: Literal["query", "header"]
    member: ReadMember


@dataclass(frozen=True, slots=True)
class ReadScheme:
    """A security scheme a handshake can send: its name, where it is declared, and its fields as OpenAPI spells them."""

    name: str
    declaration: SchemaLocation
    fields: Mapping[str, YamlValue]


@dataclass(frozen=True, slots=True)
class ReadMessage:
    """A message a channel carries: where it is declared, what the application does, its media type, and its payload.

    `media` is the essence of its content type, the document's default where it gives none. `payload` is where the
    payload schema is written, and `payload_ref` the `$ref` of a payload that is only one, which the message binds as
    the schema it names. How the message travels is the bound models' to decide (`_target_binding.py`).
    """

    declaration: SchemaLocation
    action: Action
    media: str
    payload: SchemaLocation
    payload_ref: str | None


@dataclass(frozen=True, slots=True)
class WalkedMessage:
    """A Message Object the parser's walk reached: where it is, where its payload schema is read, and its use.

    `payload` is None for a message without a payload, and `payload_ref` is the `$ref` the payload is used as, the model
    it names, as an OpenAPI client binds a body (`OpenAPIParser._reference_use`).
    """

    declaration: SchemaLocation
    payload: SchemaLocation | None
    payload_ref: str | None


@dataclass(frozen=True, slots=True)
class Walk:
    """How the parser read a document: by AsyncAPI 2 (`v2`) or 3, and the messages its walk reached (`messages`).

    `messages` gives those reached from the message value written at a location, None where the walk read none.
    """

    v2: bool
    messages: Callable[[SchemaLocation], tuple[WalkedMessage, ...] | None]


@dataclass(frozen=True, slots=True)
class ReadChannel:
    """A connection as a client opens it, or the features and reasons that leave it out (`skips`).

    A connection is a channel, or the channels at one address on the same servers: it carries the messages of them all,
    and is otherwise the first of them. `declaration` is where that channel is written and `use_site` where the
    document lists it. `name` is the channel's id (AsyncAPI 3) or its address (AsyncAPI 2), and the address of a
    connection of several channels. `address` is the path the channel is at relative to its servers, which
    always starts with `/`, as both versions read an address without one. A channel of a document that declares no
    servers is served on none, so a client opens it at the base URL it is given. `security` holds the alternatives
    of the security its handshake needs, as OpenAPI's requirements list them, and is None where it needs none.
    `subprotocols` are those its handshake offers, which its binding's `Sec-WebSocket-Protocol` header fixes. `kind`
    is how a client opens it (`_kind`): a server-sent event stream is served on the document's `http`,
    `https` and `sse` servers, and a WebSocket on its `ws` and `wss` servers.
    """

    use_site: SchemaLocation
    declaration: SchemaLocation
    name: str
    address: str | None
    servers: tuple[ReadServer, ...] = ()
    parameters: tuple[ReadParameter, ...] = ()
    handshake: tuple[ReadHandshakeParameter, ...] = ()
    security: tuple[Requirement, ...] | None = None
    subprotocols: tuple[str, ...] = ()
    messages: tuple[ReadMessage, ...] = ()
    skips: tuple[tuple[str | None, str], ...] = field(default=())
    kind: Kind = "websocket"


@dataclass(frozen=True, slots=True)
class ReadDocument:
    """The channels of a document, in document order, and the security schemes a handshake can send, by name."""

    channels: tuple[ReadChannel, ...]
    schemes: tuple[ReadScheme, ...]


@dataclass(frozen=True, slots=True)
class _Document:
    """A loaded document, its root at `root`, and the references in the loaded documents, followed as the parser does.

    `value` reads a location of a loaded document, None where it holds nothing, and `target` returns where a `$ref` of
    a document leads, None where it leads into no loaded document.
    """

    root: SchemaLocation
    value: Callable[[SchemaLocation], YamlValue]
    target: Callable[[str, str], SchemaLocation | None]

    def resolved(self, value: YamlValue, at: SchemaLocation) -> tuple[YamlValue, SchemaLocation] | None:
        """Return a value at `at` and its location, following its references to what they name.

        None is a reference into no loaded document, or one that loops. A reference to nothing names None.
        """
        seen: set[SchemaLocation] = set()
        while isinstance(value, dict) and isinstance(ref := value.get("$ref"), str):
            if (target := self.target(at.document, ref)) is None or target in seen:
                return None
            seen.add(target)
            value, at = self.value(target), target
        return value, at

    def named(self, item: YamlValue, at: SchemaLocation, holder: tuple[str, ...]) -> str | None:
        """Return the name of the object of the document's `holder` that a reference at `at` names, else None.

        A reference names the entry it is written to, else the first entry that leads where the reference leads.
        """
        ref = as_mapping(item).get("$ref")
        if not isinstance(ref, str) or (target := self.target(at.document, ref)) is None:
            return None
        catalogue = self.root.child(*holder)
        if target.document == catalogue.document and target.tokens[:-1] == catalogue.tokens:
            return target.tokens[-1]
        if (leads := self.resolved(self.value(target), target)) is None:
            return None
        for key, value in as_mapping(self.value(catalogue)).items():
            if (entry := self.resolved(value, catalogue.child(name := str(key)))) is not None and entry[1] == leads[1]:
                return name
        return None


def _media(message: Mapping[str, YamlValue], default: str) -> str:
    content = message.get("contentType")
    return schema_format_media_type(content if isinstance(content, str) else default)


def _scheme_of(url: str) -> tuple[str | None, str]:
    """Return the scheme a client opens a URL at, by the scheme it is written with, and the URL without that scheme.

    The scheme is None for a URL written without one, or with one a client opens nothing at (`_URL_SCHEMES`).
    """
    if (match := _SCHEME.match(url)) is None:
        return None, url
    return _URL_SCHEMES.get(match.group().removesuffix("://").lower()), url[match.end() :]


def _joined(written: str | None, protocol: str | None) -> str:
    """Return the scheme a client opens a URL at, by the scheme it is written with and the scheme of its protocol.

    Neither downgrades the other: the URL is opened with TLS when either says so, and without it only when one says so
    and neither says TLS. A URL that neither gives a scheme is opened with TLS.
    """
    return "https" if "https" in {written, protocol} else written or protocol or "https"


def _opened_at(written: str, variables: YamlValue, scheme: str | None) -> tuple[str, YamlValue]:
    """Return the URL template a client opens a server's channels at, and the variables the template uses.

    The scheme the URL is written with and the protocol's `scheme` are joined (`_joined`), so a `wss` or `https` URL
    or protocol is opened with TLS whatever the other says. A variable the URL starts with gives its scheme the same
    way when its default is written with one: the scheme is taken out of the default and out of each allowed value
    written with a scheme opened alike once joined with the protocol's. A variable that is the URL's scheme,
    `{name}://`, keeps its place, its default and allowed values written as the schemes a client opens them at, each
    joined with the protocol's.
    """
    given, location = _scheme_of(written)
    declared = as_mapping(variables)
    if (leading := PATH_PLACEHOLDER.match(location)) is None or not isinstance(
        variable := declared.get(name := leading[1]), dict
    ):
        return f"{_joined(given, scheme)}://{location}", variables
    enum = variable.get("enum")
    values = [variable.get("default"), *(enum if isinstance(enum, list) else ())]
    if (
        given is None
        and location[leading.end() :].startswith("://")
        and all(opened := [_URL_SCHEMES.get(value.lower()) if isinstance(value, str) else None for value in values])
    ):
        default, *allowed = (_joined(value, scheme) for value in opened)
        return location, {**declared, name: _revalued(variable, [default, *dict.fromkeys(allowed)])}
    if isinstance(values[0], str) and (own := _scheme_of(values[0])[0]) is not None:
        stripped = [
            split[1]
            if isinstance(value, str)
            and (split := _scheme_of(value))[0] is not None
            and _joined(split[0], scheme) == _joined(own, scheme)
            else value
            for value in values
        ]
        return f"{_joined(given or own, scheme)}://{location}", {**declared, name: _revalued(variable, stripped)}
    return f"{_joined(given, scheme)}://{location}", variables


def _revalued(variable: Mapping[str, YamlValue], values: Sequence[YamlValue]) -> dict[str, YamlValue]:
    """Return a server variable with another default, `values[0]`, and other allowed values, the rest."""
    return {**variable, "default": values[0], **({"enum": list(values[1:])} if "enum" in variable else {})}


def _unsubstituted(server: ReadServer) -> str | None:
    """Return why a server's URL has a variable a client cannot substitute, or None where it has none.

    A client substitutes the `default` of each variable, as OpenAPI requires every variable to declare one; AsyncAPI
    makes it optional.
    """
    variables = as_mapping(server.variables)
    for name in dict.fromkeys(path_placeholders(server.url)):
        if name not in variables:
            return f"the URL of server {server.name} uses the variable {name}, which the server does not declare"
        if not isinstance(as_mapping(variables[name]).get("default"), str):
            return f"the variable {name} of server {server.name} has no default"
    return None


def _combined(parts: list[tuple[Requirement, ...] | None]) -> tuple[Requirement, ...] | None:
    """Return the alternatives a handshake meets by meeting an alternative of each part that needs security.

    Each is one choice of an alternative per part, needing every scheme the choice needs with all the scopes it needs
    of it; None is no part needing security.
    """
    present = [part for part in parts if part is not None]
    if not present:
        return None
    combined: tuple[Requirement, ...] = ((),)
    for part in present:
        combined = tuple(dict.fromkeys(_merged(chosen, alternative) for chosen in combined for alternative in part))
    return combined


def _merged(chosen: Requirement, alternative: Requirement) -> Requirement:
    """Return what meeting both requirements needs: every scheme of either, with all the scopes either needs of it."""
    merged: dict[str, dict[str, None]] = {}
    for name, scopes in (*chosen, *alternative):
        merged.setdefault(name, {}).update(dict.fromkeys(scopes))
    return tuple((name, tuple(scopes)) for name, scopes in merged.items())


def _undescribed(value: YamlValue) -> YamlValue:
    """Return a declaration without its `description`, which documents what it is but changes nothing a client sends."""
    return {key: item for key, item in value.items() if key != "description"} if isinstance(value, dict) else value


def _unordered(security: tuple[Requirement, ...] | None) -> _Unordered | None:
    """Return a security's alternatives as a set, each the set of its schemes with their scopes, in no order."""
    if security is None:
        return None
    return frozenset(frozenset((name, frozenset(scopes)) for name, scopes in alternative) for alternative in security)


def _declared(parameter: ReadHandshakeParameter) -> tuple[bool, YamlValue, TypeView]:
    """Return how a query or header parameter of a handshake is declared, as written but for its description.

    The type the models bind tells apart properties written alike whose references name other schemas, such as
    `#/definitions/Id` in two documents.
    """
    member = parameter.member
    return member.required, _undescribed(member.written), member.bound


def _handshake_inputs(document: _Document, channel: ReadChannel) -> tuple[object, ...]:
    """Return what a channel's handshake sends, as written but for descriptions, to tell whether two agree.

    Those are the parameters of its address, its query and header parameters, its subprotocols and its security.
    """
    return (
        {parameter.name: _undescribed(document.value(parameter.declaration)) for parameter in channel.parameters},
        {(parameter.location, parameter.member.name): _declared(parameter) for parameter in channel.handshake},
        channel.subprotocols,
        _unordered(channel.security),
    )


def _kind(
    document: _Document,
    members: Members,
    served: list[ReadServer],
    messages: list[ReadMessage],
    bound: list[tuple[SchemaLocation, Mapping[str, YamlValue]]],
) -> Kind:
    """Return how a client opens a channel: as a server-sent event stream, or as a WebSocket.

    A channel is a stream when a message of it is `text/event-stream`. Served on no `ws` or `wss` server (of
    those it is `served` on), it is one as well when an `sse` server serves it, or when the HTTP binding of it or
    of an operation on it (`bound`) fixes the `Accept` or `Content-Type` header of its GET to `text/event-stream`.
    Any other is a WebSocket, as a channel of HTTP servers whose GET answers otherwise is no stream a client opens.
    """
    if any(message.media == EVENT_STREAM for message in messages):
        return "sse"
    if any(server.kind == "websocket" for server in served):
        return "websocket"
    if any(server.protocol == "sse" for server in served) or any(
        _streams(document, members, holder, owner) for holder, owner in bound
    ):
        return "sse"
    return "websocket"


def _streams(document: _Document, members: Members, holder: SchemaLocation, owner: Mapping[str, YamlValue]) -> bool:
    """Return whether the HTTP binding of a channel or an operation fixes a media header to `text/event-stream`.

    The header is `Accept` or `Content-Type` (`_NEGOTIATED`), a property of the binding's `headers` object, and
    it is fixed where its type admits that one value alone, as the models hold it (`members`).
    """
    bindings = document.resolved(owner.get("bindings"), holder)
    binding = (
        None if bindings is None else document.resolved(as_mapping(bindings[0]).get("http"), bindings[1].child("http"))
    )
    headers = (
        None
        if binding is None
        else document.resolved(as_mapping(binding[0]).get("headers"), binding[1].child("headers"))
    )
    if headers is None or isinstance(found := members(headers[1]), str):
        return False
    return any(
        member.name.lower() in _NEGOTIATED
        and member.fixed is not None
        and len(member.fixed) == 1
        and isinstance(media := member.fixed[0], str)
        and schema_format_media_type(media) == EVENT_STREAM
        for member in found
    )


def _layers(document: _Document, written: Mapping[str, YamlValue], at: SchemaLocation, *, v2: bool) -> _Layers | None:
    """Return an operation or a message at `at` and its traits, each with where it is, the strongest last.

    They are in the order of `trait_layers`, the object's own fields winning except under AsyncAPI 2 (`v2`). None is a
    trait that does not resolve.
    """
    if not isinstance(traits := written.get("traits"), list):
        return [(written, at)]
    layers: _Layers = []
    for index, trait in enumerate(traits):
        if (found := document.resolved(trait, at.child("traits", str(index)))) is None:
            return None
        layers.append((as_mapping(found[0]), found[1]))
    return trait_layers((written, at), layers, own_wins=not v2)


def _bound(
    document: _Document, written: Mapping[str, YamlValue], at: SchemaLocation, layers: _Layers | None
) -> tuple[SchemaLocation, Mapping[str, YamlValue]]:
    """Return where the bindings of an operation at `at` that give it its HTTP binding are, and their holder.

    That is the strongest of its `layers` whose bindings set the `http` one, which Merge Patch keeps whatever bindings
    for other protocols a stronger layer has, or clear it (a null, which removes it); one whose bindings do not resolve
    is taken as well, as the handshake then leaves the channel out. Without such a layer, or where a trait does not
    resolve, it is the operation's own.
    """
    protocol = _BINDINGS["sse"][0]
    chosen = (written, at)
    for layer, where in layers or ():
        if "bindings" not in layer:
            continue
        found = document.resolved(layer["bindings"], where.child("bindings"))
        if found is None or found[0] is None or protocol in as_mapping(found[0]):
            chosen = (layer, where)
    return chosen[1].child("bindings"), chosen[0]


def _traited(written: Mapping[str, YamlValue], layers: _Layers) -> Mapping[str, YamlValue]:
    """Return an operation or a message with the fields its `layers` give it.

    Only the fields the reader decides on are merged (`_TRAIT_FIELDS`), each from the strongest layer that has it. A
    null field, which Merge Patch removes, reads as an absent one.
    """
    merged = dict(written)
    for layer, _ in layers:
        merged.update((key, layer[key]) for key in _TRAIT_FIELDS & layer.keys())
    return merged


def _listed(
    listed: YamlValue, at: SchemaLocation, channel: tuple[SchemaLocation, Mapping[str, YamlValue]]
) -> list[SchemaLocation]:
    """Return where the messages an operation or a reply at `at` lists are written, else those of its `channel`."""
    declaration, written = channel
    if isinstance(listed, list):
        return [at.child("messages", str(index)) for index in range(len(listed))]
    return [declaration.child("messages", str(key)) for key in as_mapping(written.get("messages"))]


def _offered(fixed: tuple[object, ...] | None) -> tuple[str, ...]:
    """Return the subprotocol tokens a header's type fixes, in order, else none.

    A client offers each subprotocol it speaks and the server selects one, so every value its type admits is offered;
    a value that is no token offers none.
    """
    values = fixed or ()
    offered = [item for item in values if isinstance(item, str) and is_token(item)]
    return tuple(dict.fromkeys(offered)) if len(offered) == len(values) else ()


class _Reader:
    """Read the channels of one document by its AsyncAPI version, from the messages the parser's walk reached (`walk`).

    The document is read as `_Document` reads it, and `members` gives the properties the models hold of an object
    schema.
    """

    def __init__(self, document: _Document, members: Members, walk: Walk) -> None:
        self.document = document
        self.members = members
        self.v2 = walk.v2
        self.walked = walk.messages
        raw = as_mapping(document.value(document.root))
        default = raw.get("defaultContentType")
        self.default_media = default if isinstance(default, str) else _DEFAULT_MEDIA
        self.schemes = self.read_schemes()
        self.servers = self.read_servers()
        self.declares_servers = bool(as_mapping(raw.get("servers")))
        self.operations, self.foreign = ([], ()) if self.v2 else self.read_operations()
        self.replies: dict[SchemaLocation, list[tuple[str, SchemaLocation]]] = {}
        """The operations on each channel whose reply is on another channel, each with that channel's declaration."""

    def read_schemes(self) -> dict[str, ReadScheme | str]:
        """Return each security scheme of the document by name: as a handshake sends it, else its AsyncAPI type.

        A handshake is an HTTP request, so it sends an `httpApiKey`, `http`, `oauth2` or `openIdConnect` scheme, whose
        fields OpenAPI spells alike but for the `availableScopes` of an AsyncAPI 3 flow, OpenAPI's `scopes`. A scheme
        in no loaded document is one a handshake cannot send, as no plan resolves it.
        """
        schemes: dict[str, ReadScheme | str] = {}
        holder = self.document.root.child(*_SCHEMES_POINTER)
        for key, value in as_mapping(self.document.value(holder)).items():
            if (found := self.document.resolved(value, holder.child(name := str(key)))) is None:
                schemes[name] = _FOREIGN
                continue
            scheme, declaration = as_mapping(found[0]), found[1]
            if (kind := _SCHEME_TYPES.get(written := str(scheme.get("type")))) is None:
                schemes[name] = written
                continue
            fields: dict[str, YamlValue] = {
                "type": kind,
                **{item: scheme[item] for item in _SCHEME_FIELDS if item in scheme},
            }
            if isinstance(flows := scheme.get("flows"), dict):
                fields["flows"] = {
                    flow: {
                        "scopes" if entry == "availableScopes" else entry: item
                        for entry, item in as_mapping(declared).items()
                    }
                    for flow, declared in flows.items()
                }
            schemes[name] = ReadScheme(name, declaration, fields)
        return schemes

    def read_servers(self) -> dict[str, tuple[ReadServer | None, _Security]]:
        """Return each server by name: the form a client opens it in (None for no such protocol), and its security.

        Its URL is its AsyncAPI 2 `url`, or its AsyncAPI 3 `host` and `pathname`, opened with TLS when the scheme it is
        written with or the scheme of its protocol says so (`_SCHEMES`, `_opened_at`).
        """
        servers: dict[str, tuple[ReadServer | None, _Security]] = {}
        holder = self.document.root.child("servers")
        for key, value in as_mapping(self.document.value(holder)).items():
            if (found := self.document.resolved(value, holder.child(name := str(key)))) is None:
                continue
            server = as_mapping(found[0])
            security = server.get("security"), found[1]
            if (opened := _SCHEMES.get(protocol := str(server.get("protocol", "")).lower())) is None:
                servers[name] = None, security
                continue
            scheme, kind = opened
            written = (
                str(server.get("url", "")) if self.v2 else f"{server.get('host', '')}{server.get('pathname') or ''}"
            )
            variables = self.document.resolved(server.get("variables"), found[1].child("variables"))
            url, used = _opened_at(written, None if variables is None else variables[0], scheme)
            servers[name] = ReadServer(name, url, used, kind, protocol), security
        return servers

    def channels(self) -> Iterator[ReadChannel]:
        """Yield every channel the document lists, in document order."""
        holder = self.document.root.child("channels")
        for key, value in as_mapping(self.document.value(holder)).items():
            use_site = holder.child(name := str(key))
            if (found := self.document.resolved(value, use_site)) is None:
                yield ReadChannel(use_site, use_site, name, None, skips=((None, "it references another document"),))
                continue
            yield self.channel(use_site, found[1], name, as_mapping(found[0]))

    def connections(self) -> Iterator[ReadChannel]:
        """Yield every connection a client opens, in document order: the channels at one address on the same servers.

        The servers are the same when they are the same set, whatever order a channel lists them in, a channel that
        lists none being served on all of them. A channel without an address, or served on no server of a document that
        declares servers, is one of its own, and a server-sent event stream is never one with a WebSocket.
        """
        connections: dict[object, list[ReadChannel]] = {}
        for channel in self.channels():
            key = (
                (channel.address, frozenset(server.name for server in channel.servers), channel.kind)
                if channel.address is not None and (channel.servers or not self.declares_servers)
                else channel.use_site
            )
            connections.setdefault(key, []).append(channel)
        for channels in connections.values():
            yield self.connection(channels)

    def connection(self, channels: list[ReadChannel]) -> ReadChannel:
        """Join the channels at one address on the same servers as one connection, which carries all their messages.

        The connection is named after the address when it joins several channels, and opened by the handshake of the
        first: so the handshakes of the others must take the same parameters, offer the same subprotocols and need the
        same security, or the connection is left out. It is left out as well where a reply of an operation on it is on
        a channel it does not join, and where every channel it joins is.
        """
        first = channels[0]
        skips: Skips = [skip for channel in channels for skip in channel.skips]
        handshake = _handshake_inputs(self.document, first)
        for channel in channels[1:]:
            if _handshake_inputs(self.document, channel) != handshake:
                skips.append((
                    "shared_addresses",
                    f"the handshakes of channels {first.name} and {channel.name} at its address differ",
                ))
        joined = {channel.declaration for channel in channels}
        for channel in channels:
            for pointer, target in self.replies.get(channel.declaration, ()):
                if target not in joined:
                    message = f"the reply of operation {pointer} is on channel {target.pointer}, at another address"
                    skips.append(("operation_replies", message))
        messages = tuple(dict.fromkeys(message for channel in channels for message in channel.messages))
        if not messages and not skips:
            skips.append((None, "no operation sends or receives a message on it"))
        return replace(
            first,
            name=first.name if len(channels) == 1 else str(first.address),
            messages=messages,
            skips=tuple(dict.fromkeys(skips)),
        )

    def read_operations(self) -> tuple[list[_Operation], tuple[str, ...]]:
        """Return each AsyncAPI 3 operation on a channel of the document, and those that do not resolve.

        Each operation comes with where the channel its reference names is, through references, and where it is. An
        operation that does not resolve may be on any channel, so it leaves every channel out.
        """
        found: list[_Operation] = []
        foreign: list[str] = []
        holder = self.document.root.child("operations")
        for key, value in as_mapping(self.document.value(holder)).items():
            if (operation := self.document.resolved(value, use_site := holder.child(str(key)))) is None:
                foreign.append(use_site.pointer)
                continue
            raw, at = as_mapping(operation[0]), operation[1]
            channel = self.document.resolved(raw.get("channel"), SchemaLocation(at.document, ()))
            if channel is not None and channel[1].tokens:
                found.append((channel[1], at, raw))
        return found, tuple(foreign)

    def channel(
        self,
        use_site: SchemaLocation,
        declaration: SchemaLocation,
        key: str,
        channel: Mapping[str, YamlValue],
    ) -> ReadChannel:
        """Read one channel: its servers, address, parameters, handshake inputs, security and messages.

        Every reason a client cannot open it is a skip. The handshake needs the security of the servers the channel is
        served on, which must agree, and that of each operation on it, as a connection carries them all. How a client
        opens it is its `_kind`: the handshake of a server-sent event stream takes the inputs of the HTTP bindings of
        the channel and of its operations, which must declare each parameter alike.
        """
        skips: Skips = [(None, f"operation {pointer} references another document") for pointer in self.foreign]
        address = key if self.v2 else channel.get("address")
        if not isinstance(address, str):
            skips.append(("channel_addresses", "it has no address"))
        carried: Skips = []
        operations: list[tuple[SchemaLocation, Mapping[str, YamlValue]]] = []
        securities: list[tuple[Requirement, ...] | None] = []
        if self.v2:
            messages = list(self.v2_messages(declaration, channel, carried, securities, operations))
        else:
            messages = list(self.v3_messages(declaration, channel, carried, securities, operations))
        bound = [(declaration.child("bindings"), channel), *operations]
        served = self.channel_servers(declaration, channel)
        kind = _kind(self.document, self.members, [server for server, _ in served], messages, bound)
        servers = [(server, written) for server, written in served if server.kind == kind]
        if not servers and self.declares_servers:
            skips.append(("server_protocols", f"it is served on no {_PROTOCOLS[kind]} server"))
        skips.extend(("server_variables", problem) for server, _ in servers if (problem := _unsubstituted(server)))
        distinct: dict[_Unordered | None, tuple[Requirement, ...] | None] = {}
        for server, written in servers:
            needed = self.requirements(written, f"server {server.name}", skips)
            distinct.setdefault(_unordered(needed), needed)
        security = list(distinct.values())
        if len(security) > 1:
            skips.append(("security_requirements", "the servers it is served on require different security"))
        subprotocols: list[str] = []
        handshake = self.handshakes(bound if kind == "sse" else bound[:1], kind, skips, subprotocols)
        skips.extend(carried)
        securities[:0] = security[:1]
        return ReadChannel(
            use_site,
            declaration,
            key,
            f"/{address.removeprefix('/')}" if isinstance(address, str) else None,
            tuple(server for server, _ in servers),
            tuple(self.parameters(declaration, channel)),
            handshake,
            _combined(securities),
            tuple(subprotocols),
            tuple(dict.fromkeys(messages)),
            tuple(dict.fromkeys(skips)),
            kind,
        )

    def handshakes(
        self,
        bound: list[tuple[SchemaLocation, Mapping[str, YamlValue]]],
        kind: Kind,
        skips: Skips,
        subprotocols: list[str],
    ) -> tuple[ReadHandshakeParameter, ...]:
        """Return the query and header parameters of the bindings that give a channel's handshake its inputs.

        Those are the bindings of the channel and of its operations (`bound`, each with where it is), which must
        declare each parameter alike: one declared otherwise by another binding leaves the channel out.
        """
        _, label, feature = _BINDINGS[kind]
        found: dict[tuple[str, str], ReadHandshakeParameter] = {}
        for holder, owner in bound:
            for parameter in self.handshake(holder, owner, skips, subprotocols, kind):
                name = parameter.member.name
                earlier = found.setdefault((parameter.location, name), parameter)
                if earlier is not parameter and _declared(earlier) != _declared(parameter):
                    message = f"its {label}s declare its {parameter.location} parameter {name} differently"
                    skips.append((feature, message))
        return tuple(found.values())

    def handshake(
        self,
        holder: SchemaLocation,
        owner: Mapping[str, YamlValue],
        skips: Skips,
        subprotocols: list[str],
        kind: Kind,
    ) -> Iterator[ReadHandshakeParameter]:
        """Yield the query and header parameters of the binding of a channel or an operation, which opens it with a GET.

        A WebSocket's binding is the channel's `ws` one, and a server-sent event stream's the `http` one of the channel
        and of each operation on it (`_BINDINGS`). Its `query` and `headers` are object schemas, whose properties, as
        the models hold them (`members`), are the parameters, required where the models require them; one no
        parameters stand for leaves the channel out. A header the handshake writes itself is no parameter. For a
        WebSocket (`_MANAGED_HEADERS`), the tokens a `Sec-WebSocket-Protocol` fixes are the subprotocols the
        handshake offers, added to `subprotocols`; the media headers of a stream's GET (`_NEGOTIATED`) are left
        unsent, as OpenAPI ignores them; any other such header (`_STREAM_HEADERS`) is left unsent where it is
        optional, and leaves the channel out where it is required.
        """
        protocol, label, feature = _BINDINGS[kind]
        if (bindings := self.document.resolved(owner.get("bindings"), holder)) is None:
            skips.append((None, "its bindings reference another document"))
            return
        found = self.document.resolved(as_mapping(bindings[0]).get(protocol), bindings[1].child(protocol))
        if found is None:
            skips.append((None, f"its {label} references another document"))
            return
        binding, written = as_mapping(found[0]), found[1]
        if str(binding.get("method", "GET")).upper() != "GET":
            skips.append((feature, f"its {label} opens it with {binding.get('method')}"))
        managed_headers = _MANAGED_HEADERS if kind == "websocket" else _STREAM_HEADERS | _NEGOTIATED
        for name, location in _HANDSHAKE_INPUTS:
            if name not in binding:
                continue
            if (target := self.document.resolved(binding[name], written.child(name))) is None:
                skips.append((None, f"the {name} schema of its {label} references another document"))
                continue
            if isinstance(members := self.members(target[1]), str):
                skips.append((feature, f"the {name} schema of its {label} {members}"))
                continue
            for member in members:
                if location == "header" and (managed := member.name.lower()) in managed_headers:
                    if managed == _SUBPROTOCOL and (offered := _offered(member.fixed)):
                        subprotocols.extend(offered)
                    elif member.required and managed not in _NEGOTIATED:
                        message = f"its {label} sets the header {member.name}, which the handshake writes itself"
                        skips.append((feature, message))
                    continue
                yield ReadHandshakeParameter(location, member)

    def requirements(self, security: _Security, owner: str, skips: Skips) -> tuple[Requirement, ...] | None:
        """Return the alternatives of a server's or an operation's security, None where it needs none.

        AsyncAPI 2 lists requirement objects, each scheme's name with the scopes it needs, as OpenAPI does; AsyncAPI 3
        lists the schemes, each an alternative by a reference to one of `components/securitySchemes`, which declares
        the scopes it needs. An alternative that needs a scheme a handshake cannot send, or an AsyncAPI 3 scheme written
        inline, which no requirement can name, is left out; with none left, or for a list of anything else, the channel
        is.
        """
        value, holder = security
        if value is None or value == []:
            return None
        found: list[Requirement] = []
        unsent: dict[str, None] = {}
        for item in value if isinstance(value, list) else (None,):
            if not self.v2 and isinstance(item, dict) and "type" in item and "$ref" not in item:
                unsent[_INLINE] = None
                continue
            if (alternative := self.alternative(item, holder)) is None:
                listed = "requirement objects" if self.v2 else "references to components/securitySchemes"
                skips.append(("security_requirements", f"the security of {owner} is not a list of {listed}"))
                return ()
            if kinds := [kind for name, _ in alternative if isinstance(kind := self.schemes.get(name), str)]:
                unsent.update(dict.fromkeys(kinds))
            else:
                found.append(alternative)
        if not found:
            needed = ", ".join(unsent)
            skips.append((
                "security_requirements",
                f"the security of {owner} needs {needed}, which a handshake cannot send",
            ))
        return tuple(found)

    def alternative(self, item: YamlValue, holder: SchemaLocation) -> Requirement | None:
        """Return the schemes and scopes an item of a security list at `holder` needs, None where it is no such item."""
        if self.v2:
            if not isinstance(item, dict):
                return None
            needed = {
                str(name): tuple(map(str, scopes))
                for name, scopes in item.items()
                if isinstance(scopes, list) and all(isinstance(scope, str) for scope in scopes)
            }
            return tuple(needed.items()) if len(needed) == len(item) else None
        if (name := self.document.named(item, holder, _SCHEMES_POINTER)) is None:
            return None
        scheme = self.schemes.get(name)
        scopes = (
            as_mapping(self.document.value(scheme.declaration)).get("scopes") if isinstance(scheme, ReadScheme) else ()
        )
        return ((name, tuple(scope for scope in scopes if isinstance(scope, str)) if isinstance(scopes, list) else ()),)

    def channel_servers(
        self, declaration: SchemaLocation, channel: Mapping[str, YamlValue]
    ) -> list[tuple[ReadServer, _Security]]:
        """Return the servers a channel is served on that a client opens, each with its security as written.

        A channel that lists no servers is served on every server of the document.
        """
        listed = channel.get("servers")
        if not isinstance(listed, list):
            names = list(self.servers)
        elif self.v2:
            names = [str(name) for name in listed]
        else:
            names = [
                name for item in listed if (name := self.document.named(item, declaration, ("servers",))) is not None
            ]
        found = [self.servers[name] for name in names if name in self.servers]
        return [(server, security) for server, security in found if server is not None]

    def parameters(self, declaration: SchemaLocation, channel: Mapping[str, YamlValue]) -> Iterator[ReadParameter]:
        """Yield the parameters of a channel's address, with the schema an AsyncAPI 2 parameter declares."""
        holder = declaration.child("parameters")
        for key, value in as_mapping(channel.get("parameters")).items():
            if (found := self.document.resolved(value, holder.child(name := str(key)))) is None:
                continue
            parameter, written = as_mapping(found[0]), found[1]
            schema = written.child("schema") if self.v2 and "schema" in parameter else None
            description = parameter.get("description")
            yield ReadParameter(name, written, schema, description if isinstance(description, str) else None)

    def v2_messages(
        self,
        declaration: SchemaLocation,
        channel: Mapping[str, YamlValue],
        skips: Skips,
        securities: list[tuple[Requirement, ...] | None],
        operations: list[tuple[SchemaLocation, Mapping[str, YamlValue]]],
    ) -> Iterator[ReadMessage]:
        """Yield the messages of an AsyncAPI 2 channel's operations, those the parser's walk reached from each.

        The security of each operation is added to `securities`, and the operation, with where its bindings are, to
        `operations`.
        """
        for name in CHANNEL_OPERATIONS:
            action = _V2_ACTIONS[name]
            if not isinstance(written := channel.get(name), dict):
                continue
            if (traited := self.operation(written, at := declaration.child(name), operations)) is None:
                skips.append((None, f"a trait of its {name} operation references another document"))
                continue
            operation, security = traited
            securities.append(self.requirements(security, f"its {name} operation", skips))
            if "message" in operation:
                yield from self.messages((at.child("message"),), action, skips)

    def v3_messages(
        self,
        declaration: SchemaLocation,
        channel: Mapping[str, YamlValue],
        skips: Skips,
        securities: list[tuple[Requirement, ...] | None],
        operations: list[tuple[SchemaLocation, Mapping[str, YamlValue]]],
    ) -> Iterator[ReadMessage]:
        """Yield the messages of the AsyncAPI 3 operations on a channel: those an operation lists, else all of them.

        The security of each operation is added to `securities`, and the operation, with where its bindings are, to
        `operations`.
        """
        for target, at, written in self.operations:
            if target != declaration:
                continue
            action = written.get("action")
            if action not in {"send", "receive"}:
                continue
            if (traited := self.operation(written, at, operations)) is None:
                skips.append((None, f"a trait of operation {at.pointer} references another document"))
                continue
            operation, security = traited
            securities.append(self.requirements(security, f"operation {at.pointer}", skips))
            yield from self.messages(_listed(operation.get("messages"), at, (declaration, channel)), action, skips)
            if "reply" in operation:
                yield from self.reply(declaration, channel, at, operation, skips)

    def messages(self, uses: Iterable[SchemaLocation], action: object, skips: Skips) -> Iterator[ReadMessage]:
        """Yield the messages the parser's walk reached from the message values written at `uses`, in its order."""
        for use in uses:
            if (walked := self.walked(use)) is None:
                skips.append((None, f"the messages at {use.pointer} were not read"))
                continue
            for message in walked:
                yield from self.message(message, action, skips)

    def reply(
        self,
        declaration: SchemaLocation,
        channel: Mapping[str, YamlValue],
        at: SchemaLocation,
        operation: Mapping[str, YamlValue],
        skips: Skips,
    ) -> Iterator[ReadMessage]:
        """Yield the messages of an AsyncAPI 3 operation's reply, which go the other way on the operation's connection.

        The reply is on the channel it names, else on the operation's own; a reply on another channel is recorded in
        `replies`, as the connection carries it only where that channel is at the same address on the same servers.
        Its messages are those it lists, else every message of its channel. A reply sent to an address that a runtime
        expression gives (`address`) is on no connection a client opens, so it leaves the channel out; no request is
        correlated with its reply.
        """
        pointer = at.pointer
        if (found := self.document.resolved(operation["reply"], at.child("reply"))) is None:
            skips.append((None, f"the reply of operation {pointer} references another document"))
            return
        reply = as_mapping(found[0])
        if "address" in reply:
            address = self.document.resolved(reply["address"], found[1].child("address"))
            location = as_mapping(None if address is None else address[0]).get("location")
            given = f" by {location}" if isinstance(location, str) else ""
            skips.append(("operation_replies", f"the reply of operation {pointer} goes to an address given{given}"))
            return
        target, written = declaration, channel
        if "channel" in reply:
            if (named := self.document.resolved(reply["channel"], found[1].child("channel"))) is None:
                skips.append((None, f"the reply channel of operation {pointer} references another document"))
                return
            target, written = named[1], as_mapping(named[0])
        if target != declaration:
            self.replies.setdefault(declaration, []).append((pointer, target))
        action = "receive" if operation["action"] == "send" else "send"
        yield from self.messages(_listed(reply.get("messages"), found[1], (target, written)), action, skips)

    def operation(
        self,
        written: Mapping[str, YamlValue],
        at: SchemaLocation,
        operations: list[tuple[SchemaLocation, Mapping[str, YamlValue]]],
    ) -> tuple[Mapping[str, YamlValue], _Security] | None:
        """Return an operation at `at` with the fields its traits give it, and its security, None where a trait fails.

        A trait fails where it does not resolve. The security is that of the strongest layer that has one, where its
        references resolve from. Where the bindings that give the operation its HTTP binding are, and their holder, are
        added to `operations` (`_bound`).
        """
        layers = _layers(self.document, written, at, v2=self.v2)
        operations.append(_bound(self.document, written, at, layers))
        if layers is None:
            return None
        operation = _traited(written, layers)
        holder = next((where for layer, where in reversed(layers) if "security" in layer), at)
        return operation, (operation.get("security"), holder)

    def message(self, walked: WalkedMessage, action: object, skips: Skips) -> Iterator[ReadMessage]:
        """Yield one message as the application sends or receives it, recording why a client cannot read its payload."""
        declaration = walked.declaration
        message = as_mapping(self.document.value(declaration))
        pointer = declaration.pointer
        if (layers := _layers(self.document, message, declaration, v2=self.v2)) is None:
            skips.append((None, f"a trait of message {pointer} references another document"))
            return
        message = _traited(message, layers)
        payload = message.get("payload")
        schema_format = message.get("schemaFormat")
        if (held := held_schema(payload, schema_format)) is not None:
            payload, schema_format = held
        if isinstance(schema_format, str) and schema_format_kind(schema_format) not in _CODED_FORMATS:
            skips.append(("message_content_types", f"the payload of message {pointer} is a {schema_format} schema"))
            return
        if payload is None or (at := walked.payload) is None:
            skips.append(("message_payloads", f"message {pointer} declares no payload"))
            return
        if self.document.resolved(payload, at) is None:
            skips.append((None, f"the payload of message {pointer} does not resolve in the loaded documents"))
            return
        media = _media(message, self.default_media)
        yield ReadMessage(declaration, "send" if action == "send" else "receive", media, at, walked.payload_ref)


def read(
    document: str,
    value: Callable[[SchemaLocation], YamlValue],
    target: Callable[[str, str], SchemaLocation | None],
    members: Members,
    walk: Walk,
) -> ReadDocument:
    """Return every connection of a loaded AsyncAPI document, in document order, and the schemes a handshake can send.

    `value` reads a location of a loaded document and `target` returns where a `$ref` of a document leads, as the
    parser resolves it, None where it leads into no loaded document; `members` gives the properties the models hold of
    an object schema, or why no parameters stand for it; `walk` is how the parser read the document.
    """
    reader = _Reader(_Document(SchemaLocation(document, ()), value, target), members, walk)
    return ReadDocument(
        tuple(reader.connections()),
        tuple(scheme for scheme in reader.schemes.values() if isinstance(scheme, ReadScheme)),
    )
