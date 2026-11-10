"""Read the channels of an AsyncAPI document, which a client opens through their handshakes.

A channel is a WebSocket, opened by its handshake, or a server-sent event stream, opened by a GET and served on
`http`, `https` or `sse` servers: one whose messages include a `text/event-stream` one, one an `sse` server serves, or
one whose HTTP binding asks for `text/event-stream`. This module reads one loaded document as written: the servers
each channel is served on, its address and parameters, the query and header parameters its binding gives its
handshake, the security the handshake needs, the messages its operations carry and whether the described application
sends or receives each, and why a client cannot open a channel. AsyncAPI 2 names the application's sends `subscribe`
and its receives `publish`; AsyncAPI 3 names them by the operation's `action`. Which of them a client sends on a
WebSocket is the client's setting, and a client receives every message of a stream.

A client opens the channels at one address on the same servers as one connection, which carries the messages of
them all, the replies of their AsyncAPI 3 operations among them. A connection is opened only when everything a client
needs to open it resolves: the operations of its channels and their traits, its messages and their traits, and the
schemas of their payloads. Traits are merged as the document's version merges them first. The declarations are read
from the document itself, so one in another document leaves the channel out, and an operation in another document,
which may be on any channel, leaves every channel out. A payload schema in another document is looked up in the
documents the generation loaded (`loaded`), as the models bind it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._document_reader import as_mapping, descend
from datamodel_code_generator._json_pointer import join_pointer, pointer_tokens, reference_tokens
from datamodel_code_generator._runtime.model_codecs.media import is_token
from datamodel_code_generator._target_naming import PATH_PLACEHOLDER, path_placeholders

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from datamodel_code_generator._source import YamlValue

__all__ = (
    "ReadChannel",
    "ReadDocument",
    "ReadHandshakeParameter",
    "ReadMessage",
    "ReadParameter",
    "ReadScheme",
    "ReadServer",
    "Requirement",
    "read",
)

Tokens: TypeAlias = tuple[str, ...]
Action: TypeAlias = Literal["send", "receive"]
Requirement: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
"""One alternative of a security requirement: each scheme it needs, by name, with the scopes it needs of it."""
_Unordered: TypeAlias = frozenset[frozenset[tuple[str, frozenset[str]]]]
"""A security's alternatives, each the schemes it needs with their scopes, in no order."""
Skips: TypeAlias = "list[tuple[str | None, str]]"
_Operation: TypeAlias = "tuple[Tokens, str, Mapping[str, YamlValue]]"
"""An AsyncAPI 3 operation: the pointer of its channel, its own pointer, and the operation as written."""

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
_EVENT_STREAM: Final = "text/event-stream"
_V2_ACTIONS: Final[tuple[tuple[str, Action], ...]] = (("subscribe", "send"), ("publish", "receive"))
_JSON_FORMATS: Final = re.compile(r"application/(vnd\.aai\.asyncapi|schema)(\+(json|yaml))?(;.*)?")
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
_OBJECT_FIELDS: Final = frozenset(("type", "properties", "required", "additionalProperties", "title", "description"))
"""The keywords of a binding's `query` or `headers` schema that its properties stand for as parameters."""
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
    declaration: Tokens
    schema: Tokens | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ReadHandshakeParameter:
    """A query or header parameter of a channel's handshake: a property of its binding's `query` or `headers`.

    `schema` is where the property's schema is written, and `required` whether the object requires the property.
    """

    name: str
    location: Literal["query", "header"]
    schema: Tokens
    required: bool
    description: str | None


@dataclass(frozen=True, slots=True)
class ReadScheme:
    """A security scheme a handshake can send: its name, where it is declared, and its fields as OpenAPI spells them."""

    name: str
    declaration: Tokens
    fields: Mapping[str, YamlValue]


@dataclass(frozen=True, slots=True)
class ReadMessage:
    """A message a channel carries: where it is declared, what the application does, its codec, and its payload.

    `payload` is where the payload schema is written, and `payload_ref` the `$ref` of a payload that is only one, which
    the message binds as the schema it names. The codec `event` is a server-sent event's, whose data the payload's
    models decode as JSON.
    """

    declaration: Tokens
    action: Action
    codec: Literal["json", "utf8", "bytes", "event"]
    payload: Tokens
    payload_ref: str | None


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

    use_site: Tokens
    declaration: Tokens
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


class _Document:
    """One loaded document and the local references in it, followed to the values they name."""

    def __init__(self, raw: Mapping[str, YamlValue]) -> None:
        self.raw = raw

    def value(self, tokens: Tokens) -> YamlValue:
        """Return the value at a pointer's tokens, through mappings and lists, or None where the document has none."""
        return descend(dict(self.raw), tokens, "canonical")

    def resolved(
        self, value: YamlValue, tokens: Tokens, loaded: Callable[[str], YamlValue] | None = None
    ) -> tuple[YamlValue, Tokens] | None:
        """Return a value and its pointer, following local references to what they name.

        None is a reference that loops, or one to another document; with `loaded`, a reference to another document
        names what `loaded` finds for it in the loaded documents, at no pointer of this one, and None where it finds
        nothing. A local reference to nothing names None.
        """
        seen: set[Tokens] = set()
        while isinstance(value, dict) and isinstance(ref := value.get("$ref"), str):
            if not ref.startswith("#") or (target := reference_tokens(ref[1:])) is None or target in seen:
                found = None if loaded is None or ref.startswith("#") else loaded(ref)
                return None if found is None else (found, ())
            seen.add(target)
            value, tokens = self.value(target), target
        return value, tokens


def _media(message: Mapping[str, YamlValue], default: str) -> str:
    content = message.get("contentType")
    return _essence(content if isinstance(content, str) else default)


def _essence(media: str) -> str:
    """Return a media type without its parameters, lowercase."""
    return media.partition(";")[0].strip().lower()


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


def _json(media: str) -> bool:
    return media == _DEFAULT_MEDIA or media.endswith("+json")


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


def _properties(value: YamlValue) -> tuple[dict[str, YamlValue], list[YamlValue]] | None:
    """Return the properties of an object schema and the names it requires, or None where it says more of the object.

    Only its type `object`, its titles and the keywords of its properties (`_OBJECT_FIELDS`) and extensions may be
    beside them, and every name it requires must be one of its properties.
    """
    if not isinstance(value, dict) or value.get("type", "object") != "object":
        return None
    properties, required = value.get("properties", {}), value.get("required", [])
    if (
        not isinstance(properties, dict)
        or not isinstance(required, list)
        or not all(isinstance(name, str) and name in properties for name in required)
        or not _OBJECT_FIELDS.issuperset(key for key in map(str, value) if not key.startswith("x-"))
    ):
        return None
    return properties, required


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


def _declared(document: _Document, parameter: ReadHandshakeParameter) -> tuple[bool, YamlValue]:
    """Return how a query or header parameter of a handshake is declared, as written but for its description."""
    return parameter.required, _undescribed(document.value(parameter.schema))


def _handshake_inputs(document: _Document, channel: ReadChannel) -> tuple[object, ...]:
    """Return what a channel's handshake sends, as written but for descriptions, to tell whether two agree.

    Those are the parameters of its address, its query and header parameters, its subprotocols and its security.
    """
    return (
        {parameter.name: _undescribed(document.value(parameter.declaration)) for parameter in channel.parameters},
        {(parameter.location, parameter.name): _declared(document, parameter) for parameter in channel.handshake},
        channel.subprotocols,
        _unordered(channel.security),
    )


def _kind(
    document: _Document,
    served: list[ReadServer],
    messages: list[ReadMessage],
    bound: list[tuple[Tokens, Mapping[str, YamlValue], bool]],
) -> Kind:
    """Return how a client opens a channel: as a server-sent event stream, or as a WebSocket.

    A channel is a stream when a message of it is `text/event-stream`. Served on no `ws` or `wss` server (of
    those it is `served` on), it is one as well when an `sse` server serves it, or when the HTTP binding of it or
    of an operation on it (`bound`) fixes the `Accept` or `Content-Type` header of its GET to `text/event-stream`.
    Any other is a WebSocket, as a channel of HTTP servers whose GET answers otherwise is no stream a client opens.
    """
    if any(message.codec == "event" for message in messages):
        return "sse"
    if any(server.kind == "websocket" for server in served):
        return "websocket"
    if any(server.protocol == "sse" for server in served) or any(
        _streams(document, holder, owner) for holder, owner, _ in bound
    ):
        return "sse"
    return "websocket"


def _streams(document: _Document, holder: Tokens, owner: Mapping[str, YamlValue]) -> bool:
    """Return whether the HTTP binding of a channel or an operation fixes a media header to `text/event-stream`.

    The header is `Accept` or `Content-Type` (`_NEGOTIATED`), a property of the binding's `headers` object, and
    it is fixed by its `const`, or by an `enum` of that one value.
    """
    bindings = document.resolved(owner.get("bindings"), holder)
    binding = None if bindings is None else document.resolved(as_mapping(bindings[0]).get("http"), ())
    headers = None if binding is None else document.resolved(as_mapping(binding[0]).get("headers"), ())
    if headers is None or (shape := _properties(headers[0])) is None:
        return False
    return any(
        str(name).lower() in _NEGOTIATED
        and len(fixed := _fixed_values(document, value)) == 1
        and isinstance(media := fixed[0], str)
        and _essence(media) == _EVENT_STREAM
        for name, value in shape[0].items()
    )


def _layers(
    document: _Document, written: Mapping[str, YamlValue], tokens: Tokens, *, v2: bool
) -> list[tuple[Mapping[str, YamlValue], Tokens]] | None:
    """Return an operation or a message at `tokens` and its traits, each with its pointer, the strongest last.

    AsyncAPI 2 (`v2`) merges each trait into the object in order, by JSON Merge Patch, so a trait's field wins over the
    object's; AsyncAPI 3 applies the traits in order and then the object, whose own fields win. None is a trait that
    does not resolve.
    """
    if not isinstance(traits := written.get("traits"), list):
        return [(written, tokens)]
    layers: list[tuple[Mapping[str, YamlValue], Tokens]] = []
    for index, trait in enumerate(traits):
        if (found := document.resolved(trait, (*tokens, "traits", str(index)))) is None:
            return None
        layers.append((as_mapping(found[0]), found[1]))
    return [(written, tokens), *layers] if v2 else [*layers, (written, tokens)]


def _bound(
    document: _Document, written: Mapping[str, YamlValue], tokens: Tokens, *, v2: bool
) -> tuple[Tokens, Mapping[str, YamlValue]]:
    """Return where the bindings of an operation at `tokens` that give it its HTTP binding are, and their holder.

    That is the strongest of its `_layers` whose bindings set the `http` one, which Merge Patch keeps whatever bindings
    for other protocols a stronger layer has, or clear it (a null, which removes it); one whose bindings reference
    another document is taken as well, as the handshake then leaves the channel out. Without such a layer, or where a
    trait does not resolve, it is the operation's own.
    """
    protocol = _BINDINGS["sse"][0]
    chosen = (written, tokens)
    for layer, at in _layers(document, written, tokens, v2=v2) or ():
        if "bindings" not in layer:
            continue
        found = document.resolved(layer["bindings"], (*at, "bindings"))
        if found is None or found[0] is None or protocol in as_mapping(found[0]):
            chosen = (layer, at)
    return (*chosen[1], "bindings"), chosen[0]


def _offered(document: _Document, value: YamlValue) -> tuple[str, ...]:
    """Return the subprotocol tokens a header's schema fixes by its `const` or `enum`, in order, else none.

    A client offers each subprotocol it speaks and the server selects one, so every value of an `enum` is offered;
    a value that is no token offers none.
    """
    written = _fixed_values(document, value)
    offered = [item for item in written if isinstance(item, str) and is_token(item)]
    return tuple(dict.fromkeys(offered)) if len(offered) == len(written) else ()


def _fixed_values(document: _Document, value: YamlValue) -> list[YamlValue]:
    """Return the values a schema fixes by its `const` or `enum`, in order, else none."""
    schema = as_mapping(None if (found := document.resolved(value, ())) is None else found[0])
    written = [schema["const"]] if "const" in schema else schema.get("enum")
    return written if isinstance(written, list) else []


class _Reader:
    """Read the channels of one document by its AsyncAPI version.

    `loaded` returns the schema a `$ref` of the document to another one names in the loaded documents, or None.
    """

    def __init__(self, raw: Mapping[str, YamlValue], loaded: Callable[[str], YamlValue]) -> None:
        self.document = _Document(raw)
        self.loaded = loaded
        version = raw.get("asyncapi")
        self.v2 = isinstance(version, str) and version.startswith("2.")
        default = raw.get("defaultContentType")
        self.default_media = default if isinstance(default, str) else _DEFAULT_MEDIA
        self.schemes = self.read_schemes()
        self.servers = self.read_servers()
        self.declares_servers = bool(as_mapping(self.document.value(("servers",))))
        self.operations, self.foreign = ([], ()) if self.v2 else self.read_operations()
        self.replies: dict[Tokens, list[tuple[str, Tokens]]] = {}
        """The operations on each channel whose reply is on another channel, each with that channel's declaration."""

    def read_schemes(self) -> dict[str, ReadScheme | str]:
        """Return each security scheme of the document by name: as a handshake sends it, else its AsyncAPI type.

        A handshake is an HTTP request, so it sends an `httpApiKey`, `http`, `oauth2` or `openIdConnect` scheme, whose
        fields OpenAPI spells alike but for the `availableScopes` of an AsyncAPI 3 flow, OpenAPI's `scopes`. A scheme
        in another document is one a handshake cannot send, as no plan resolves it.
        """
        schemes: dict[str, ReadScheme | str] = {}
        for key, value in as_mapping(self.document.value(_SCHEMES_POINTER)).items():
            if (found := self.document.resolved(value, (*_SCHEMES_POINTER, name := str(key)))) is None:
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

    def read_servers(self) -> dict[str, tuple[ReadServer | None, YamlValue]]:
        """Return each server by name: the form a client opens it in (None for no such protocol), and its security.

        Its URL is its AsyncAPI 2 `url`, or its AsyncAPI 3 `host` and `pathname`, opened with TLS when the scheme it is
        written with or the scheme of its protocol says so (`_SCHEMES`, `_opened_at`).
        """
        servers: dict[str, tuple[ReadServer | None, YamlValue]] = {}
        holder = ("servers",)
        for key, value in as_mapping(self.document.value(holder)).items():
            if (found := self.document.resolved(value, (*holder, name := str(key)))) is None:
                continue
            server = as_mapping(found[0])
            security = server.get("security")
            if (opened := _SCHEMES.get(protocol := str(server.get("protocol", "")).lower())) is None:
                servers[name] = None, security
                continue
            scheme, kind = opened
            written = (
                str(server.get("url", "")) if self.v2 else f"{server.get('host', '')}{server.get('pathname') or ''}"
            )
            variables = self.document.resolved(server.get("variables"), ())
            url, used = _opened_at(written, None if variables is None else variables[0], scheme)
            servers[name] = ReadServer(name, url, used, kind, protocol), security
        return servers

    def channels(self) -> Iterator[ReadChannel]:
        """Yield every channel the document lists, in document order."""
        holder = ("channels",)
        for key, value in as_mapping(self.document.value(holder)).items():
            use_site = (*holder, name := str(key))
            if (found := self.document.resolved(value, use_site)) is None:
                yield ReadChannel(use_site, use_site, name, None, skips=((None, "it references another document"),))
                continue
            channel, declaration = as_mapping(found[0]), found[1]
            yield self.channel(use_site, declaration, name, channel)

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
                    message = (
                        f"the reply of operation {pointer} is on channel {join_pointer(target)}, at another address"
                    )
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
        """Return each AsyncAPI 3 operation on a channel of the document, and the operations in another document.

        Each operation comes with the pointer of the channel its reference names, through references, and its pointer.
        An operation in another document may be on any channel, so it leaves every channel out.
        """
        found: list[_Operation] = []
        foreign: list[str] = []
        holder = ("operations",)
        for key, value in as_mapping(self.document.value(holder)).items():
            if (operation := self.document.resolved(value, use_site := (*holder, str(key)))) is None:
                foreign.append(join_pointer(use_site))
                continue
            raw = as_mapping(operation[0])
            if (channel := self.document.resolved(raw.get("channel"), ())) is not None and channel[1]:
                found.append((channel[1], join_pointer(operation[1]), raw))
        return found, tuple(foreign)

    def channel(
        self,
        use_site: Tokens,
        declaration: Tokens,
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
        operations: list[tuple[Tokens, Mapping[str, YamlValue], bool]] = []
        securities: list[tuple[Requirement, ...] | None] = []
        if self.v2:
            messages = list(self.v2_messages(declaration, channel, carried, securities, operations))
        else:
            messages = list(self.v3_messages(declaration, channel, carried, securities, operations))
        bound = [((*declaration, "bindings"), channel, True), *operations]
        served = self.channel_servers(channel)
        kind = _kind(self.document, [server for server, _ in served], messages, bound)
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
        bound: list[tuple[Tokens, Mapping[str, YamlValue], bool]],
        kind: Kind,
        skips: Skips,
        subprotocols: list[str],
    ) -> tuple[ReadHandshakeParameter, ...]:
        """Return the query and header parameters of the bindings that give a channel's handshake its inputs.

        Those are the bindings of the channel and of its operations (`bound`, each with where it is and whether the
        models type its schemas), which must declare each parameter alike: one declared otherwise by another binding
        leaves the channel out.
        """
        _, label, feature = _BINDINGS[kind]
        found: dict[tuple[str, str], ReadHandshakeParameter] = {}
        for holder, owner, modeled in bound:
            for parameter in self.handshake(holder, owner, skips, subprotocols, kind, modeled=modeled):
                earlier = found.setdefault((parameter.location, parameter.name), parameter)
                if earlier is not parameter and _declared(self.document, earlier) != _declared(
                    self.document, parameter
                ):
                    message = f"its {label}s declare its {parameter.location} parameter {parameter.name} differently"
                    skips.append((feature, message))
        return tuple(found.values())

    def handshake(  # noqa: PLR0913
        self,
        holder: Tokens,
        owner: Mapping[str, YamlValue],
        skips: Skips,
        subprotocols: list[str],
        kind: Kind,
        *,
        modeled: bool,
    ) -> Iterator[ReadHandshakeParameter]:
        """Yield the query and header parameters of the binding of a channel or an operation, which opens it with a GET.

        A WebSocket's binding is the channel's `ws` one, and a server-sent event stream's the `http` one of the channel
        and of each operation on it (`_BINDINGS`). Its `query` and `headers` are object schemas, whose properties are
        the parameters, required where the object requires them. An object constrained otherwise than by its
        properties (`_OBJECT_FIELDS`) leaves the channel out, as parameters cannot stand for it. A header the
        handshake writes itself is no parameter. For a WebSocket (`_MANAGED_HEADERS`), the tokens a
        `Sec-WebSocket-Protocol` fixes are the subprotocols the handshake offers, added to `subprotocols`; the media
        headers of a stream's GET (`_NEGOTIATED`) are left unsent, as OpenAPI ignores them; any other such header
        (`_STREAM_HEADERS`) is left unsent where it is optional, and leaves the channel out where it is required.
        The models type no schema of the bindings of an AsyncAPI 2 operation (`modeled`), so its `query` or `headers`
        leaves the channel out.
        """
        protocol, label, feature = _BINDINGS[kind]
        if (bindings := self.document.resolved(owner.get("bindings"), holder)) is None:
            skips.append((None, "its bindings reference another document"))
            return
        found = self.document.resolved(as_mapping(bindings[0]).get(protocol), (*bindings[1], protocol))
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
            if not modeled:
                skips.append((
                    feature,
                    f"the {label} of an AsyncAPI 2 operation on it declares {name}, which no model types",
                ))
                continue
            if (target := self.document.resolved(binding[name], (*written, name))) is None:
                skips.append((None, f"the {name} schema of its {label} references another document"))
                continue
            if (shape := _properties(target[0])) is None:
                message = f"the {name} schema of its {label} is not an object of properties"
                skips.append((feature, message))
                continue
            properties, required = shape
            for key, value in properties.items():
                if location == "header" and (managed := str(key).lower()) in managed_headers:
                    if managed == _SUBPROTOCOL and (offered := _offered(self.document, value)):
                        subprotocols.extend(offered)
                    elif key in required and managed not in _NEGOTIATED:
                        message = f"its {label} sets the header {key}, which the handshake writes itself"
                        skips.append((feature, message))
                    continue
                description = as_mapping(value).get("description")
                yield ReadHandshakeParameter(
                    str(key),
                    location,
                    (*target[1], "properties", str(key)),
                    key in required,
                    description if isinstance(description, str) else None,
                )

    def requirements(self, value: YamlValue, owner: str, skips: Skips) -> tuple[Requirement, ...] | None:
        """Return the alternatives of a server's or an operation's security, None where it needs none.

        AsyncAPI 2 lists requirement objects, each scheme's name with the scopes it needs, as OpenAPI does; AsyncAPI 3
        lists the schemes, each an alternative by a reference to one of `components/securitySchemes`, which declares
        the scopes it needs. An alternative that needs a scheme a handshake cannot send, or an AsyncAPI 3 scheme written
        inline, which no requirement can name, is left out; with none left, or for a list of anything else, the channel
        is.
        """
        if value is None or value == []:
            return None
        found: list[Requirement] = []
        unsent: dict[str, None] = {}
        for item in value if isinstance(value, list) else (None,):
            if not self.v2 and isinstance(item, dict) and "type" in item and "$ref" not in item:
                unsent[_INLINE] = None
                continue
            if (alternative := self.alternative(item)) is None:
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

    def alternative(self, item: YamlValue) -> Requirement | None:
        """Return the schemes and scopes one item of a security list needs, or None where it is no such item."""
        if self.v2:
            if not isinstance(item, dict):
                return None
            needed = {
                str(name): tuple(map(str, scopes))
                for name, scopes in item.items()
                if isinstance(scopes, list) and all(isinstance(scope, str) for scope in scopes)
            }
            return tuple(needed.items()) if len(needed) == len(item) else None
        ref = as_mapping(item).get("$ref")
        tokens = reference_tokens(ref[1:]) if isinstance(ref, str) and ref.startswith("#") else None
        if tokens is None or len(tokens) != len(_SCHEMES_POINTER) + 1 or tokens[:-1] != _SCHEMES_POINTER:
            return None
        scheme = self.schemes.get(name := tokens[-1])
        scopes = (
            as_mapping(self.document.value(scheme.declaration)).get("scopes") if isinstance(scheme, ReadScheme) else ()
        )
        return ((name, tuple(scope for scope in scopes if isinstance(scope, str)) if isinstance(scopes, list) else ()),)

    def channel_servers(self, channel: Mapping[str, YamlValue]) -> list[tuple[ReadServer, YamlValue]]:
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
                tokens[1]
                for item in listed
                if isinstance(ref := as_mapping(item).get("$ref"), str)
                and ref.startswith("#")
                and (tokens := reference_tokens(ref[1:])) is not None
                and len(tokens) == 2  # noqa: PLR2004
                and tokens[0] == "servers"
            ]
        found = [self.servers[name] for name in names if name in self.servers]
        return [(server, security) for server, security in found if server is not None]

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
        self,
        declaration: Tokens,
        channel: Mapping[str, YamlValue],
        skips: Skips,
        securities: list[tuple[Requirement, ...] | None],
        operations: list[tuple[Tokens, Mapping[str, YamlValue], bool]],
    ) -> Iterator[ReadMessage]:
        """Yield the messages of an AsyncAPI 2 channel's operations, and their alternatives under `oneOf`.

        The security of each operation is added to `securities`, and the operation, with where its bindings are, to
        `operations`, as one whose binding schemas no model types.
        """
        for name, action in _V2_ACTIONS:
            if not isinstance(written := channel.get(name), dict):
                continue
            operations.append((*_bound(self.document, written, (*declaration, name), v2=True), False))
            if (operation := self.traited(written)) is None:
                skips.append((None, f"a trait of its {name} operation references another document"))
                continue
            securities.append(self.requirements(operation.get("security"), f"its {name} operation", skips))
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
        skips: Skips,
        securities: list[tuple[Requirement, ...] | None],
        operations: list[tuple[Tokens, Mapping[str, YamlValue], bool]],
    ) -> Iterator[ReadMessage]:
        """Yield the messages of the AsyncAPI 3 operations on a channel: those an operation lists, else all of them.

        The security of each operation is added to `securities`, and the operation, with where its bindings are, to
        `operations`, as one whose binding schemas the models type.
        """
        holder = (*declaration, "messages")
        own = [(str(key), value) for key, value in as_mapping(channel.get("messages")).items()]
        for target, pointer, written in self.operations:
            if target != declaration:
                continue
            action = written.get("action")
            if action not in {"send", "receive"}:
                continue
            operations.append((*_bound(self.document, written, pointer_tokens(pointer), v2=False), True))
            if (operation := self.traited(written)) is None:
                skips.append((None, f"a trait of operation {pointer} references another document"))
                continue
            securities.append(self.requirements(operation.get("security"), f"operation {pointer}", skips))
            yield from self.listed(operation.get("messages"), holder, own, action, skips)
            if "reply" in operation:
                yield from self.reply(declaration, channel, pointer, operation, skips)

    def listed(
        self,
        listed: YamlValue,
        holder: Tokens,
        own: list[tuple[str, YamlValue]],
        action: object,
        skips: Skips,
    ) -> Iterator[ReadMessage]:
        """Yield the messages an operation or its reply lists, else every message of its channel, `own` at `holder`."""
        items: list[tuple[YamlValue, Tokens]] = (
            [(value, ()) for value in listed]
            if isinstance(listed, list)
            else [(value, (*holder, key)) for key, value in own]
        )
        for item, location in items:
            yield from self.message(item, location, action, skips)

    def reply(
        self,
        declaration: Tokens,
        channel: Mapping[str, YamlValue],
        pointer: str,
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
        if (found := self.document.resolved(operation["reply"], (*pointer_tokens(pointer), "reply"))) is None:
            skips.append((None, f"the reply of operation {pointer} references another document"))
            return
        reply = as_mapping(found[0])
        if "address" in reply:
            address = self.document.resolved(reply["address"], ())
            location = as_mapping(None if address is None else address[0]).get("location")
            given = f" by {location}" if isinstance(location, str) else ""
            skips.append(("operation_replies", f"the reply of operation {pointer} goes to an address given{given}"))
            return
        target, written = declaration, channel
        if "channel" in reply:
            if (named := self.document.resolved(reply["channel"], (*found[1], "channel"))) is None:
                skips.append((None, f"the reply channel of operation {pointer} references another document"))
                return
            target, written = named[1], as_mapping(named[0])
        if target != declaration:
            self.replies.setdefault(declaration, []).append((pointer, target))
        holder = (*target, "messages")
        own = [(str(key), item) for key, item in as_mapping(written.get("messages")).items()]
        yield from self.listed(
            reply.get("messages"), holder, own, "receive" if operation["action"] == "send" else "send", skips
        )

    def traited(self, written: Mapping[str, YamlValue]) -> Mapping[str, YamlValue] | None:
        """Return an operation or a message with the fields its traits give it, or None when a trait does not resolve.

        Only the fields the reader decides on are merged (`_TRAIT_FIELDS`), each from the strongest of its `_layers`
        that has it. A null field, which Merge Patch removes, reads as an absent one.
        """
        if (layers := _layers(self.document, written, (), v2=self.v2)) is None:
            return None
        merged = dict(written)
        for layer, _ in layers:
            merged.update((key, layer[key]) for key in _TRAIT_FIELDS & layer.keys())
        return merged

    def message(self, value: YamlValue, written: Tokens, action: object, skips: Skips) -> Iterator[ReadMessage]:
        """Yield one message as the application sends or receives it, recording why a client cannot code it."""
        if (found := self.document.resolved(value, written)) is None:
            skips.append((None, "a message of it references another document"))
            return
        message, declaration = as_mapping(found[0]), found[1]
        pointer = join_pointer(declaration)
        if (message := self.traited(message)) is None:
            skips.append((None, f"a trait of message {pointer} references another document"))
            return
        payload = message.get("payload")
        schema_format = message.get("schemaFormat")
        tokens: Tokens = (*declaration, "payload")
        if isinstance(payload, dict) and "schemaFormat" in payload and "schema" in payload:
            schema_format, payload, tokens = payload["schemaFormat"], payload["schema"], (*tokens, "schema")
        if isinstance(schema_format, str) and not _JSON_FORMATS.fullmatch(schema_format):
            skips.append(("message_content_types", f"the payload of message {pointer} is a {schema_format} schema"))
            return
        if payload is None:
            skips.append(("message_payloads", f"message {pointer} declares no payload"))
            return
        ref = as_mapping(payload).get("$ref")
        payload_ref = ref if isinstance(ref, str) and len(as_mapping(payload)) == 1 else None
        if (target := self.document.resolved(payload, tokens, self.loaded)) is None:
            skips.append((None, f"the payload of message {pointer} does not resolve in the loaded documents"))
            return
        media = _media(message, self.default_media)
        schema = as_mapping(target[0])
        encoding = schema.get("contentEncoding")
        binary = encoding is None and schema.get("format") == "binary"
        if media == _EVENT_STREAM:
            codec: Literal["json", "utf8", "bytes", "event"] = "event"
        elif media == "application/octet-stream" or (binary and not _json(media)):
            codec = "bytes"
        elif media == "text/plain" and encoding is None and schema.get("type") == "string":
            codec = "utf8"
        elif _json(media):
            codec = "json"
        else:
            content = media if encoding is None else f"{media} encoded as {encoding}"
            skips.append(("message_content_types", f"message {pointer} has content type {content}"))
            return
        yield ReadMessage(declaration, "send" if action == "send" else "receive", codec, tokens, payload_ref)


def read(raw: Mapping[str, YamlValue], loaded: Callable[[str], YamlValue]) -> ReadDocument:
    """Return every connection of a loaded AsyncAPI document, in document order, and the schemes a handshake can send.

    `loaded` returns the schema a `$ref` of the document to another one names in the loaded documents, or None.
    """
    reader = _Reader(raw, loaded)
    return ReadDocument(
        tuple(reader.connections()),
        tuple(scheme for scheme in reader.schemes.values() if isinstance(scheme, ReadScheme)),
    )
