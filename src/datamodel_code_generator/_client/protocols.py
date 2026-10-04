"""Helper configuration of a client target: its Python records, its YAML file, and the one validation of both.

Python records are projected into the tree a YAML file gives, so both inputs share every diagnostic and the same
normalized metadata. Selectors and request targets are the runtime records generated helpers use.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from functools import partial
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, TypeAlias

from datamodel_code_generator._api_manifest import canonical_bytes
from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.naming import folded, helper_name_problem, token
from datamodel_code_generator._codec_declarations import OperationRef, SchemaRef
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset
from datamodel_code_generator._target_config import _diagnostic  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from datamodel_code_generator._api_types import OperationSelector
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._runtime.protocols.records import RequestTarget, Selector

__all__ = (
    "AdapterSignature",
    "Binding",
    "BodyHmacSignature",
    "CacheHelper",
    "Continuation",
    "Converter",
    "CountContinuation",
    "CursorContinuation",
    "EndCondition",
    "EventDiscriminator",
    "EventMapping",
    "Helper",
    "HelperDefinition",
    "HelperKind",
    "ImmediateResult",
    "InlineResult",
    "LengthCompletion",
    "Link",
    "LinkContinuation",
    "LiteralValue",
    "NextUrlContinuation",
    "NoResult",
    "NoSignature",
    "OperationCompletion",
    "OperationResult",
    "PaginationHelper",
    "PollInterval",
    "PollingHelper",
    "ProtocolConfiguration",
    "PublicKeySignature",
    "RemoteCancel",
    "ResumableUploadHelper",
    "Source",
    "SourceValue",
    "Spec",
    "StandardWebhooksSignature",
    "StreamCompletion",
    "StreamHelper",
    "StreamResume",
    "StripeStyleSignature",
    "Tree",
    "UploadAbort",
    "UploadAppend",
    "UploadChecksum",
    "UploadCreate",
    "UploadProbe",
    "WebSocketHelper",
    "WebSocketMessage",
    "WebhookHelper",
    "load_protocols",
    "project",
    "protocol_problems",
    "validate",
)

HelperKind: TypeAlias = Literal[
    "pagination", "polling", "sse", "ndjson", "websocket", "webhook", "cache", "resumable_upload"
]
Source: TypeAlias = Literal["input", "initial", "previous", "state", "item"]
Converter: TypeAlias = "Callable[[object, str], object]"
Spec: TypeAlias = "Mapping[str, tuple[Converter, object]]"
Tree: TypeAlias = "dict[str, Any]"

KINDS: Final = (
    "pagination",
    "polling",
    "sse",
    "ndjson",
    "websocket",
    "webhook",
    "cache",
    "resumable_upload",
)
_LATER: Final = frozenset({"batch"})
_PUBLIC_KEY_SIGNATURES: Final = ("ed25519", "rsa-pss-sha256")
_SOURCES: Final = ("input", "initial", "previous")
_BRACKETED: Final = frozenset({"helpers", "mapping", "error_events"})
_KEYS: Final = {"from_": "from"}
_ROOT: Final = "protocols"
_MERGE: Final = "tag:yaml.org,2002:merge"
_EMPTY_STRING: Final = {"kind": "value", "value": ""}
_STATE_SETS: Final = ("pending", "succeeded", "failed", "cancelled")
_FRAMES: Final = {"json": None, "utf8": "text", "bytes": "binary"}
_MAX_DEPTH: Final = 64
_VALIDATOR_KINDS: Final = ("etag", "last_modified", "both")


class _Mark(Enum):
    REQUIRED = "required"
    OMITTED = "omitted"
    FOREIGN = "foreign"
    DEEP = "deep"


class _Invalid(Enum):
    INVALID = "invalid"


REQUIRED: Final = _Mark.REQUIRED
OMITTED: Final = _Mark.OMITTED
FOREIGN: Final = _Mark.FOREIGN
DEEP: Final = _Mark.DEEP
INVALID: Final = _Invalid.INVALID


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceValue:
    """Read a binding's value with a selector from the helper's input, a response, or its state."""

    source: Source
    selector: Selector


@dataclass(frozen=True, slots=True, kw_only=True)
class LiteralValue:
    """Give a binding a fixed JSON value."""

    literal: JSONValue


@dataclass(frozen=True, slots=True, kw_only=True)
class Binding:
    """Write one value into a request target of the operation a helper sends next."""

    target: RequestTarget
    value: SourceValue | LiteralValue


@dataclass(frozen=True, slots=True, kw_only=True)
class EndCondition:
    """End a sequence when a selected value is missing, JSON null, or equals the given JSON value."""

    kind: Literal["missing", "null", "value"]
    value: JSONValue | Unset = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class CursorContinuation:
    """Read a cursor from each page and write it into the next request until an end condition holds."""

    kind: ClassVar[Literal["cursor"]] = "cursor"

    read: Selector
    write: RequestTarget
    end: tuple[EndCondition, ...]
    empty_string: Literal["value", "end"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CountContinuation:
    """Write an offset or page number, advanced by a literal step or the page's item count."""

    kind: Literal["offset", "page"]
    write: RequestTarget
    first: int
    step: int | Literal["page_items_count"]
    has_more: Selector | None = None
    total: Selector | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NextUrlContinuation:
    """Follow the URL each page selects until an end condition holds."""

    kind: ClassVar[Literal["next_url"]] = "next_url"

    read: Selector
    end: tuple[EndCondition, ...]
    repeat_request_body: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkContinuation:
    """Follow the link of one relation in a response header."""

    kind: ClassVar[Literal["link"]] = "link"

    header: str
    rel: str = "next"


Continuation: TypeAlias = CursorContinuation | CountContinuation | NextUrlContinuation | LinkContinuation


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationHelper:
    """Iterate the items of one operation's pages."""

    kind: ClassVar[Literal["pagination"]] = "pagination"

    operation: OperationSelector
    items: Selector
    item_schema: SchemaRef
    continuation: Continuation
    bindings: tuple[Binding, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class InlineResult:
    """Read a long-running operation's result from its final poll response."""

    kind: ClassVar[Literal["inline"]] = "inline"

    selector: Selector
    schema: SchemaRef


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResult:
    """Fetch a long-running operation's result with another operation."""

    kind: ClassVar[Literal["operation"]] = "operation"

    operation: OperationSelector
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class NoResult:
    """A long-running operation without a result."""

    kind: ClassVar[Literal["none"]] = "none"


@dataclass(frozen=True, slots=True, kw_only=True)
class PollInterval:
    """The wait between polls, and the response header that may lengthen it."""

    seconds: float = 1.0
    retry_after_header: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteCancel:
    """The operation that cancels a long-running operation remotely."""

    operation: OperationSelector
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ImmediateResult:
    """Statuses of the create response that already carry the result."""

    statuses: tuple[int, ...]
    selector: Selector
    schema: SchemaRef


@dataclass(frozen=True, slots=True, kw_only=True)
class PollingHelper:
    """Create a long-running operation and poll it until its state is terminal."""

    kind: ClassVar[Literal["polling"]] = "polling"

    create: OperationSelector
    poll: OperationSelector
    accepted_statuses: tuple[int, ...]
    bindings: tuple[Binding, ...]
    state: Selector
    pending: tuple[JSONValue, ...]
    succeeded: tuple[JSONValue, ...]
    result: InlineResult | OperationResult | NoResult
    failed: tuple[JSONValue, ...] = ()
    cancelled: tuple[JSONValue, ...] = ()
    interval: PollInterval = field(default_factory=PollInterval)
    remote_cancel: RemoteCancel | None = None
    immediate_result: ImmediateResult | None = None
    expires_at: Selector | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class EventDiscriminator:
    """Where an event's type is read: the SSE event field, or a body member."""

    from_: Literal["event_type", "body"]
    pointer: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EventMapping:
    """The schema of each event type."""

    discriminator: EventDiscriminator
    mapping: Mapping[str, SchemaRef]

    def __post_init__(self) -> None:
        """Keep a read-only copy of the mapping."""
        object.__setattr__(self, "mapping", _frozen(self.mapping))


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamCompletion:
    """How a stream ends: at EOF, at a sentinel, or at an SSE event type."""

    kind: Literal["eof", "sentinel", "event_type"]
    value: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamResume:
    """How an interrupted stream is reopened from the last delivered cursor."""

    cursor: Selector | Literal["event_id"]
    write: RequestTarget
    reopen_operation: OperationSelector
    delivery: Literal["at_least_once"]
    bindings: tuple[Binding, ...] = ()
    missing: Literal["inherit", "error"] | None = None
    null: Literal["clear", "error"] | None = None
    reconnect_on: tuple[Literal["transport_interruption", "incomplete_eof"], ...] = ("transport_interruption",)
    expires_at: Selector | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamHelper:
    """Open an SSE or NDJSON stream and decode its events."""

    kind: Literal["sse", "ndjson"]
    operation: OperationSelector
    media: str
    event_schema: SchemaRef | EventMapping
    completion: StreamCompletion
    final_line: Literal["require_newline", "allow_eof"] | None = None
    unknown: Literal["error", "raw"] = "error"
    error_events: Mapping[str, SchemaRef] = field(default_factory=lambda: MappingProxyType({}))
    resume: StreamResume | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        """Keep a read-only copy of the error events."""
        object.__setattr__(self, "error_events", _frozen(self.error_events))


@dataclass(frozen=True, slots=True, kw_only=True)
class StandardWebhooksSignature:
    """HMAC-SHA256 over the Standard Webhooks delivery id, timestamp, and body."""

    kind: ClassVar[Literal["standard_webhooks"]] = "standard_webhooks"


@dataclass(frozen=True, slots=True, kw_only=True)
class StripeStyleSignature:
    """HMAC-SHA256 over a Stripe-style timestamp and body."""

    kind: ClassVar[Literal["stripe_style"]] = "stripe_style"
    header: str = "Stripe-Signature"


@dataclass(frozen=True, slots=True, kw_only=True)
class BodyHmacSignature:
    """A hex or base64 HMAC signature over the raw body."""

    kind: ClassVar[Literal["body_hmac"]] = "body_hmac"
    header: str
    algorithm: Literal["hmac-sha256", "hmac-sha512"] = "hmac-sha256"
    encoding: Literal["hex", "base64"] = "hex"
    prefix: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicKeySignature:
    """An Ed25519 or RSA-PSS signature over the raw body."""

    kind: Literal["ed25519", "rsa-pss-sha256"]
    header: str
    encoding: Literal["hex", "base64", "base64url"] = "hex"
    prefix: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterSignature:
    """A signature the caller's verifier checks, and whether it must return each fact; `none` declares it absent."""

    kind: ClassVar[Literal["adapter"]] = "adapter"

    timestamp: Literal["required", "none"]
    delivery_id: Literal["required", "none"]


@dataclass(frozen=True, slots=True, kw_only=True)
class NoSignature:
    """No signature: the helper only decodes an event, whose delivery nothing authenticates."""

    kind: ClassVar[Literal["none"]] = "none"


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookHelper:
    """Verify a signed webhook delivery and decode its event, or only decode an unsigned one."""

    kind: ClassVar[Literal["webhook"]] = "webhook"

    event_schema: SchemaRef | EventMapping
    signature: (
        StandardWebhooksSignature
        | StripeStyleSignature
        | BodyHmacSignature
        | PublicKeySignature
        | AdapterSignature
        | NoSignature
    )
    enabled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class WebSocketMessage:
    """How the messages of one direction are coded: JSON by a schema, UTF-8 text, or bytes, and in which frames.

    The frame defaults to text for JSON and UTF-8 text, and to binary for bytes.
    """

    codec: Literal["json", "utf8", "bytes"]
    schema: SchemaRef | None = None
    frame: Literal["text", "binary"] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class WebSocketHelper:
    """Open a WebSocket through an operation's handshake, and send and receive typed messages on it.

    The operation is a GET without a request body; the subprotocols are offered in order, one of which the server must
    select when any is offered, and compression permits the caller's deflate.
    """

    kind: ClassVar[Literal["websocket"]] = "websocket"

    operation: OperationSelector
    send: WebSocketMessage
    receive: WebSocketMessage
    subprotocols: tuple[str, ...] = ()
    compression: bool = False
    enabled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheHelper:
    """Fetch one operation's responses through a private cache that revalidates stale entries with their validator."""

    kind: ClassVar[Literal["cache"]] = "cache"

    operation: OperationSelector
    validator: Literal["etag", "last_modified", "both"]
    authenticated: bool
    statuses: tuple[int, ...] = (200,)
    vary_allowlist: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadCreate:
    """The operation that starts an upload, where it writes the content's size, and what its response gives."""

    operation: OperationSelector
    size: RequestTarget | None = None
    session_url: Selector | None = None
    expires_at: Selector | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadProbe:
    """The operation that reads the server's offset of an upload."""

    operation: OperationSelector
    remote_offset: Selector
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadChecksum:
    """Where an append writes the checksum of the bytes it sends, by which algorithm, and how it is spelled.

    `algorithm_prefix` writes the algorithm's name and a space before the digest, as the tus checksum extension does.
    """

    target: RequestTarget
    algorithm: Literal["md5", "sha1", "sha256", "sha512"]
    encoding: Literal["base64", "hex"] = "base64"
    algorithm_prefix: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadAppend:
    """The operation that appends one chunk, and where it writes the chunk's offset, length, and checksum."""

    operation: OperationSelector
    offset: RequestTarget
    length: RequestTarget | None = None
    checksum: UploadChecksum | None = None
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class LengthCompletion:
    """Complete an upload once the server holds every byte."""

    kind: ClassVar[Literal["length"]] = "length"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationCompletion:
    """Complete an upload with an operation, whose response is the upload's result."""

    kind: ClassVar[Literal["operation"]] = "operation"

    operation: OperationSelector
    result_schema: SchemaRef
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadAbort:
    """The operation that aborts an upload remotely."""

    operation: OperationSelector
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumableUploadHelper:
    """Upload content in chunks the server confirms, resuming from its confirmed offset."""

    kind: ClassVar[Literal["resumable_upload"]] = "resumable_upload"

    profile: Literal["offset", "parts"]
    create: UploadCreate
    probe: UploadProbe
    append: UploadAppend
    max_chunk_bytes: int
    partial_commit: Literal["allowed", "forbidden"]
    completion: LengthCompletion | OperationCompletion
    abort: UploadAbort | None = None
    enabled: bool = True


HelperDefinition: TypeAlias = (
    PaginationHelper
    | PollingHelper
    | StreamHelper
    | WebhookHelper
    | WebSocketHelper
    | CacheHelper
    | ResumableUploadHelper
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolConfiguration:
    """The helpers of a client target by their names, in declaration order."""

    schema_version: Literal[1] = 1
    helpers: Mapping[str, HelperDefinition]

    def __post_init__(self) -> None:
        """Keep a read-only copy of the helpers."""
        object.__setattr__(self, "helpers", _frozen(self.helpers))


_RECORDS: Final = frozenset({
    SourceValue,
    LiteralValue,
    Binding,
    EndCondition,
    CursorContinuation,
    NextUrlContinuation,
    LinkContinuation,
    PaginationHelper,
    InlineResult,
    OperationResult,
    NoResult,
    PollInterval,
    RemoteCancel,
    ImmediateResult,
    PollingHelper,
    EventDiscriminator,
    EventMapping,
    StreamCompletion,
    StreamHelper,
    StandardWebhooksSignature,
    StripeStyleSignature,
    BodyHmacSignature,
    PublicKeySignature,
    AdapterSignature,
    NoSignature,
    WebhookHelper,
    WebSocketMessage,
    WebSocketHelper,
    CacheHelper,
    UploadCreate,
    UploadProbe,
    UploadChecksum,
    UploadAppend,
    LengthCompletion,
    OperationCompletion,
    UploadAbort,
    ResumableUploadHelper,
    ProtocolConfiguration,
})
_ROLES: Final[Mapping[tuple[type, str], str]] = {
    (LiteralValue, "literal"): "json",
    (EndCondition, "value"): "json",
    (PollingHelper, "pending"): "json",
    (PollingHelper, "succeeded"): "json",
    (PollingHelper, "failed"): "json",
    (PollingHelper, "cancelled"): "json",
    (EventMapping, "mapping"): "mapping",
    (StreamHelper, "error_events"): "mapping",
    (ProtocolConfiguration, "helpers"): "mapping",
}


def _frozen(value: object) -> object:
    return MappingProxyType(dict(value)) if isinstance(value, Mapping) else value


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    """An operation a helper sends, and the request targets it writes into that operation."""

    at: str
    ref: OperationRef
    targets: tuple[tuple[str, Mapping[str, Any]], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Helper:
    """One validated helper: its normalized metadata, operation links from its entry on, and schema references."""

    name: str
    kind: HelperKind
    enabled: bool
    at: str
    tree: Mapping[str, Any]
    links: tuple[Link, ...]
    schemas: tuple[tuple[str, SchemaRef], ...]


def _plain(value: object, depth: int = 1) -> object:
    """Project a Python JSON value, cutting containers nested too deep for any valid literal."""
    match value:
        case None | str() | bool() | int() | float():
            return value
        case list() | tuple() | Mapping() if depth > _MAX_DEPTH:
            return DEEP
        case list() | tuple():
            return [_plain(item, depth + 1) for item in value]
        case Mapping():
            return {key: _plain(item, depth + 1) for key, item in value.items()}
        case _:
            pass
    return FOREIGN


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _literal_problem(value: object) -> str | None:
    """Return why a value is no JSON literal: another type, a lone surrogate, or containers nested too deep.

    Projection cuts Python containers nested too deep, and reading a file refuses deep nesting before this runs.
    """
    pending = [value]
    while pending:
        match item := pending.pop():
            case None | bool() | int():
                continue
            case float() if isfinite(item):
                continue
            case str():
                if not _encodable(item):
                    return "has text that is not UTF-8"
                continue
            case list():
                pending.extend(item)
                continue
            case dict() if all(isinstance(key, str) and _encodable(key) for key in item):
                pending.extend(item.values())
                continue
            case _:
                pass
        return f"nests deeper than {_MAX_DEPTH} levels" if item is DEEP else "must be a JSON value"
    return None


def project(configuration: ProtocolConfiguration) -> object:
    """Project Python records into the tree a YAML file gives; any other object becomes a value validation refuses."""
    from datamodel_code_generator._runtime.protocols.records import (  # noqa: PLC0415
        BodySelector,
        BodyTarget,
        HeaderSelector,
        ParameterTarget,
        QuerystringTarget,
        StatusSelector,
    )

    def members(value: object) -> dict[object, object]:
        kind = getattr(type(value), "kind", None)
        tree: dict[object, object] = {"kind": kind} if isinstance(kind, str) else {}
        for item in fields(value):  # ty: ignore[invalid-argument-type]
            role = _ROLES.get((type(value), item.name), "record")
            if (member := getattr(value, item.name)) is UNSET or (member is None and item.default is None):
                continue
            tree[_KEYS.get(item.name, item.name)] = _plain(member) if role == "json" else convert(member, role)
        return tree

    def reference(value: Any) -> object:
        return {"pointer": value.pointer, **({} if value.document is None else {"document": value.document})}

    def signature(value: PublicKeySignature) -> object:
        """Project a signature record whose class allows its kind, and refuse any other as validation refuses."""
        kinds = _PUBLIC_KEY_SIGNATURES
        return members(value) if value.kind in kinds else FOREIGN

    def step(value: object) -> object:
        return {"page_items_count": True} if value == "page_items_count" else {"literal": convert(value)}

    shapes: dict[type, Callable[[Any], object]] = {
        **dict.fromkeys(_RECORDS, members),
        PublicKeySignature: signature,
        CountContinuation: lambda value: {**members(value), "step": step(value.step)},
        StreamResume: lambda value: {"enabled": True, **members(value)},
        BodySelector: lambda value: {"from": "body", "pointer": value.pointer},
        HeaderSelector: lambda value: {"from": "header", "name": value.name, "occurrence": value.occurrence},
        StatusSelector: lambda _: {"from": "status"},
        ParameterTarget: lambda value: {"in": value.location, "name": value.name},
        QuerystringTarget: lambda value: {"in": "querystring", "name": value.name, "pointer": value.pointer},
        BodyTarget: lambda value: {"in": "body", "pointer": value.pointer},
        OperationRef: reference,
        SchemaRef: reference,
    }

    def convert(value: object, role: str = "record") -> object:
        match value:
            case str() | bool() | int() | float():
                return value
            case list() | tuple() if role != "item":
                return [convert(item, "item") for item in value]
            case Mapping() if role == "mapping":
                return {key: convert(item) for key, item in value.items()}
            case _ if (shape := shapes.get(type(value))) is not None:
                return shape(value)
            case _:
                pass
        return FOREIGN

    return convert(configuration)


def protocol_problems(configuration: ProtocolConfiguration) -> list[Diagnostic]:
    """Return every problem of a Python helper configuration that needs no accepted input."""
    return validate(project(configuration))[1]


def load_protocols(
    source: Path | ProtocolConfiguration, cwd: Path
) -> tuple[tuple[Helper, ...], Path, list[Diagnostic]]:
    """Read or project a helper configuration and validate it, with the directory its documents resolve against.

    A file's relative documents resolve against its directory, and a Python record's against the working directory.
    """
    if isinstance(source, ProtocolConfiguration):
        tree, base, problems = project(source), cwd, []
    else:
        path = cwd / source.expanduser()
        tree, problems = _read(path)
        base = path.parent
    if problems:
        return (), base, problems
    helpers, problems = validate(tree)
    return helpers, base, problems


def _file_problem(message: str) -> list[Diagnostic]:
    return [_diagnostic("E_CONFIG_VALUE", _ROOT, f"The protocol configuration file {message}")]


def _read(path: Path) -> tuple[object, list[Diagnostic]]:
    """Read one UTF-8 YAML document without anchors, aliases, merge keys, repeated keys, or deep nesting."""
    from datamodel_code_generator.util import (  # noqa: PLC0415
        get_safe_loader,
        get_yaml_parse_errors,
        record_watch_dependency,
    )

    record_watch_dependency(path)
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError:
        return None, _file_problem("cannot be read")
    except UnicodeDecodeError:
        return None, _file_problem("is not UTF-8")
    try:
        return _compose(get_safe_loader(), text)
    except (*get_yaml_parse_errors(), RecursionError):
        return None, _file_problem("is not YAML")


def _compose(loader_type: Any, text: str) -> tuple[object, list[Diagnostic]]:
    """Check the document's events, then compose, check, and construct it; a loader may refuse text when built."""
    events = loader_type(text)
    try:
        problem = _event_problem(events)
    finally:
        events.dispose()
    if problem is not None:
        return None, _file_problem(problem)
    loader = loader_type(text)
    try:
        if (node := loader.get_single_node()) is None:
            return None, []
        if problems := _node_problems(node):
            return None, problems
        return loader.construct_document(node), []
    finally:
        loader.dispose()


def _event_problem(loader: Any) -> str | None:
    """Refuse anchors, which aliases need, and nesting deeper than any composer handles alike, from the events."""
    import yaml  # noqa: PLC0415

    depth = 0
    while loader.check_event():
        event = loader.get_event()
        if getattr(event, "anchor", None) is not None:
            return "uses a YAML anchor or alias"
        if isinstance(event, yaml.CollectionStartEvent) and (depth := depth + 1) > _MAX_DEPTH:
            return f"nests collections deeper than {_MAX_DEPTH} levels"
        if isinstance(event, yaml.CollectionEndEvent):
            depth -= 1
    return None


def _node_problems(root: object) -> list[Diagnostic]:
    """Walk the composed nodes once, refusing merge keys and keys a mapping repeats."""
    import yaml  # noqa: PLC0415

    problems: list[Diagnostic] = []
    pending: deque[tuple[object, str]] = deque([(root, _ROOT)])
    while pending:
        node, at = pending.popleft()
        if isinstance(node, yaml.SequenceNode):
            pending.extend((item, f"{at}[{index}]") for index, item in enumerate(node.value))
        elif isinstance(node, yaml.MappingNode):
            keys: set[tuple[str, str]] = set()
            bracketed = at.rpartition(".")[2] in _BRACKETED
            for key, item in node.value:
                if not isinstance(key, yaml.ScalarNode):
                    pending.extend(((key, at), (item, at)))
                    continue
                if key.tag == _MERGE:
                    return [*problems, *_file_problem("uses a YAML merge key")]
                if (key.tag, key.value) in keys:
                    problems.append(_diagnostic("E_CONFIG_VALUE", at, f"{at} repeats the key {key.value!r}"))
                keys.add((key.tag, key.value))
                pending.append((item, f"{at}[{key.value!r}]" if bracketed else f"{at}.{key.value}"))
    return problems


def validate(tree: object) -> tuple[tuple[Helper, ...], list[Diagnostic]]:
    """Validate a helper configuration tree, returning its valid helpers and every problem in declaration order."""
    validator = _Validator()
    envelope = validator.record(
        tree,
        _ROOT,
        {"schema_version": (validator.version, REQUIRED), "helpers": (validator.helpers, REQUIRED)},
        "a mapping",
    )
    return (() if envelope is INVALID else envelope["helpers"]), validator.problems


def _choices(values: tuple[str, ...]) -> str:
    quoted = [repr(value) for value in values]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _collides(name: str, other: str) -> bool:
    return name == other or name.startswith(f"{other}.") or other.startswith(f"{name}.")


class _Validator:  # noqa: PLR0904
    """Validate helper configuration trees, collecting problems and the schema references of the current helper."""

    def __init__(self) -> None:
        self.problems: list[Diagnostic] = []
        self.schemas: list[tuple[str, SchemaRef]] = []

    def value(self, at: str, message: str) -> _Invalid:
        self.problems.append(_diagnostic("E_CONFIG_VALUE", at, message))
        return INVALID

    def conflict(self, at: str, message: str) -> None:
        self.problems.append(_diagnostic("E_CONFIG_CONFLICT", at, message))

    def record(self, value: object, at: str, spec: Spec, noun: str) -> Tree | _Invalid:
        """Convert a mapping by the spec, refusing unknown and missing keys; any invalid member invalidates it."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be {noun}")
        for key in value:
            if not isinstance(key, str):
                self.value(at, f"{at} has the key {key!r}, which is not a string")
            elif key not in spec:
                self.problems.append(_diagnostic("E_CONFIG_UNKNOWN", f"{at}.{key}", f"{at} has no key {key!r}"))
        result: Tree = {}
        for key, (convert, default) in spec.items():
            if key in value:
                result[key] = convert(value[key], f"{at}.{key}")
            elif default is REQUIRED:
                result[key] = self.value(f"{at}.{key}", f"{at} needs {key!r}")
            elif default is not OMITTED:
                result[key] = default
        known = all(isinstance(key, str) and key in spec for key in value)
        return result if known and all(item is not INVALID for item in result.values()) else INVALID

    def tagged(self, value: object, at: str, key: str, variants: Mapping[str, Spec], noun: str) -> Tree | _Invalid:
        """Convert a mapping whose tag key selects the spec of its other keys."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be {noun}")
        if key not in value:
            return self.value(f"{at}.{key}", f"{at} needs {key!r}")
        if not isinstance(tag := value[key], str) or tag not in variants:
            return self.value(f"{at}.{key}", f"{at}.{key} must be {_choices(tuple(variants))}")
        return self.record(value, at, {key: (_keep, REQUIRED), **variants[tag]}, noun)

    def items(self, value: object, at: str, convert: Converter, *, nonempty: bool = False) -> object:
        if not isinstance(value, list) or (nonempty and not value):
            return self.value(at, f"{at} must be a {'nonempty ' if nonempty else ''}list")
        result = [convert(item, f"{at}[{index}]") for index, item in enumerate(value)]
        return INVALID if any(item is INVALID for item in result) else result

    def distinct(self, at: str, values: object, what: str) -> object:
        if not isinstance(values, list):
            return values
        seen: set[bytes] = set()
        for index, item in enumerate(values):
            if (key := canonical_bytes(item)) in seen:
                return self.value(f"{at}[{index}]", f"{at}[{index}] repeats {what}")
            seen.add(key)
        return values

    def version(self, value: object, at: str) -> object:
        return value if type(value) is int and value == 1 else self.value(at, f"{at} must be 1")

    def boolean(self, value: object, at: str) -> object:
        return value if type(value) is bool else self.value(at, f"{at} must be a boolean")

    def text(self, value: object, at: str) -> object:
        valid = isinstance(value, str) and _encodable(value)
        return value if valid else self.value(at, f"{at} must be a UTF-8 string")

    def nonempty(self, value: object, at: str) -> object:
        valid = isinstance(value, str) and value and _encodable(value)
        return value if valid else self.value(at, f"{at} must be a nonempty UTF-8 string")

    def line(self, value: object, at: str) -> object:
        if isinstance(value, str) and "\n" in value:
            return self.value(at, f"{at} must not contain a line feed")
        return self.nonempty(value, at)

    def header(self, value: object, at: str) -> object:
        return value if token(value) else self.value(at, f"{at} must be a header name")

    def optional_header(self, value: object, at: str) -> object:
        return None if value is None else self.header(value, at)

    def media(self, value: object, at: str) -> object:
        if isinstance(value, str):
            try:
                return normalize_media_type(value)
            except ValueError:
                pass
        return self.value(at, f"{at} must be a media type")

    def natural(self, value: object, at: str) -> object:
        valid = type(value) is int and value >= 0
        return value if valid else self.value(at, f"{at} must be an integer of at least 0")

    def positive(self, value: object, at: str) -> object:
        return value if type(value) is int and value > 0 else self.value(at, f"{at} must be a positive integer")

    def true(self, value: object, at: str) -> object:
        return value if value is True else self.value(at, f"{at} must be true")

    def seconds(self, value: object, at: str) -> object:
        if isinstance(value, int | float) and not isinstance(value, bool) and isfinite(value) and value > 0:
            return float(value)
        return self.value(at, f"{at} must be positive finite seconds")

    def literal(self, value: object, at: str) -> object:
        return value if (problem := _literal_problem(value)) is None else self.value(at, f"{at} {problem}")

    def pointer(self, value: object, at: str) -> object:
        from datamodel_code_generator._runtime.protocols.records import BodySelector  # noqa: PLC0415

        try:
            BodySelector(pointer=value)  # ty: ignore[invalid-argument-type]
        except (TypeError, ValueError):
            return self.value(at, f"{at} must be an RFC 6901 pointer")
        return value if self.text(value, at) is not INVALID else INVALID

    def choice(self, *choices: str) -> Converter:
        def convert(value: object, at: str) -> object:
            valid = isinstance(value, str) and value in choices
            return value if valid else self.value(at, f"{at} must be {_choices(choices)}")

        return convert

    def operation(self, value: object, at: str) -> object:
        if isinstance(value, str):
            return INVALID if self.pointer(value, at) is INVALID else OperationRef(pointer=value)
        spec = {"pointer": (self.pointer, REQUIRED), "document": (self.text, OMITTED)}
        reference = self.record(value, at, spec, "an operation reference")
        return INVALID if reference is INVALID else OperationRef(**reference)

    def schema(self, value: object, at: str) -> object:
        spec = {"pointer": (self.pointer, REQUIRED), "document": (self.text, OMITTED)}
        if (reference := self.record(value, at, spec, "a schema reference")) is INVALID:
            return INVALID
        self.schemas.append((at, schema := SchemaRef(**reference)))
        return schema

    def selector(self, value: object, at: str, *, every: bool = False, body: bool = False) -> object:
        """Convert a selector, which may select every occurrence of a header only in a binding value."""
        from datamodel_code_generator._runtime.protocols.records import BodySelector, HeaderSelector  # noqa: PLC0415

        variants: dict[str, Spec] = {"body": {"pointer": (self.text, REQUIRED)}}
        if not body:
            variants["header"] = {
                "name": (self.text, REQUIRED),
                "occurrence": (self.choice("single", "all"), "single"),
            }
            variants["status"] = {}
        if (selected := self.tagged(value, at, "from", variants, "a selector")) is INVALID:
            return INVALID
        if selected.get("occurrence") == "all" and not every:
            return self.value(f"{at}.occurrence", f"{at}.occurrence can be 'all' only in a binding value")
        try:
            if selected["from"] == "body":
                BodySelector(pointer=selected["pointer"])
            elif selected["from"] == "header":
                HeaderSelector(name=selected["name"], occurrence=selected["occurrence"])
        except ValueError as error:
            return self.value(at, f"{at}: {error}")
        return selected

    def target(self, value: object, at: str) -> object:
        from datamodel_code_generator._runtime.protocols.records import (  # noqa: PLC0415
            BodyTarget,
            ParameterTarget,
            QuerystringTarget,
        )

        named: Spec = {"name": (self.text, REQUIRED)}
        selected = self.tagged(
            value,
            at,
            "in",
            {
                "path": named,
                "query": named,
                "header": named,
                "cookie": named,
                "querystring": {"name": (self.text, REQUIRED), "pointer": (self.text, REQUIRED)},
                "body": {"pointer": (self.text, REQUIRED)},
            },
            "a request target",
        )
        if selected is INVALID:
            return INVALID
        try:
            match selected["in"]:
                case "querystring":
                    QuerystringTarget(name=selected["name"], pointer=selected["pointer"])
                case "body":
                    BodyTarget(pointer=selected["pointer"])
                case _:
                    ParameterTarget(location=selected["in"], name=selected["name"])
        except ValueError as error:
            return self.value(at, f"{at}: {error}")
        return selected

    def bindings(self, value: object, at: str) -> object:
        return self.items(value, at, self.binding)

    def binding(self, value: object, at: str) -> object:
        return self.record(value, at, {"target": (self.target, REQUIRED), "value": (self.bound, REQUIRED)}, "a binding")

    def bound(self, value: object, at: str) -> object:
        if isinstance(value, Mapping) and "literal" in value:
            return self.record(value, at, {"literal": (self.literal, REQUIRED)}, "a binding value")
        spec: Spec = {
            "source": (self.choice(*_SOURCES), REQUIRED),
            "selector": (partial(self.selector, every=True), REQUIRED),
        }
        return self.record(value, at, spec, "a binding value")

    def end(self, value: object, at: str) -> object:
        condition = self.tagged(
            value, at, "kind", {"missing": {}, "null": {}, "value": {"value": (self.literal, REQUIRED)}}, "an end"
        )
        if condition is not INVALID and condition["kind"] == "value" and condition["value"] is None:
            return self.value(f"{at}.value", f"{at}.value is null; use kind 'null'")
        return condition

    def ends(self, value: object, at: str) -> object:
        return self.distinct(at, self.items(value, at, self.end, nonempty=True), "an end condition")

    def statuses(self, value: object, at: str) -> object:
        def status(item: object, where: str) -> object:
            valid = type(item) is int and 100 <= item <= 599  # noqa: PLR2004
            return item if valid else self.value(where, f"{where} must be a status from 100 to 599")

        return self.distinct(at, self.items(value, at, status, nonempty=True), "a status")

    def states(self, *, nonempty: bool) -> Converter:
        def convert(value: object, at: str) -> object:
            return self.distinct(at, self.items(value, at, self.literal, nonempty=nonempty), "a state")

        return convert

    def helpers(self, value: object, at: str) -> object:
        """Validate each helper in declaration order, refusing invalid and colliding names."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be a mapping")
        names: list[tuple[str, str]] = []
        helpers: list[Helper] = []
        for name, body in value.items():
            here = f"{at}[{name!r}]"
            if not isinstance(name, str):
                self.value(here, f"{at} has the key {name!r}, which is not a string")
                continue
            if (problem := helper_name_problem(name)) is not None:
                self.value(here, problem)
            elif other := next((other for key, other in names if _collides(folded(name), key)), None):
                message = f"The helper names {other!r} and {name!r} collide"
                self.problems.append(_diagnostic("E_NAME_COLLISION", here, message))
            else:
                names.append((folded(name), name))
            if (helper := self.helper(name, body, here)) is not None:
                helpers.append(helper)
        return tuple(helpers)

    def helper(self, name: str, value: object, at: str) -> Helper | None:
        """Validate one helper and its kind-specific settings."""
        if not isinstance(value, Mapping):
            self.value(at, f"{at} must be a helper definition")
            return None
        if "kind" not in value:
            self.value(f"{at}.kind", f"{at} needs 'kind'")
            return None
        if not isinstance(kind := value["kind"], str) or kind not in KINDS:
            self.value(f"{at}.kind", f"{at}.kind must be {_choices(KINDS)}")
            return None
        self.schemas = []
        match kind:
            case "pagination":
                tree = self.pagination(value, at)
            case "polling":
                tree = self.polling(value, at)
            case "webhook":
                tree = self.webhook(value, at)
            case "websocket":
                tree = self.websocket(value, at)
            case "cache":
                tree = self.cache(value, at)
            case "resumable_upload":
                tree = self.upload(value, at, name)
            case _:
                tree = self.stream(value, at, sse=kind == "sse")
        if tree is INVALID:
            return None
        return Helper(
            name=name,
            kind=kind,
            enabled=tree["enabled"],
            at=at,
            tree=tree,
            links=tuple(_links(kind, tree, at)),
            schemas=tuple(self.schemas),
        )

    def pagination(self, value: object, at: str) -> Tree | _Invalid:
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "items": (partial(self.selector, body=True), REQUIRED),
                "item_schema": (self.schema, REQUIRED),
                "continuation": (self.continuation, REQUIRED),
                "bindings": (self.bindings, ()),
            },
            "a helper definition",
        )

    def continuation(self, value: object, at: str) -> object:
        """Convert a continuation, refusing an end at the empty string and counts without exactly one end evidence."""
        count: Spec = {
            "write": (self.target, REQUIRED),
            "first": (self.natural, REQUIRED),
            "step": (self.step, REQUIRED),
            "has_more": (self.selector, OMITTED),
            "total": (self.selector, OMITTED),
        }
        variants: dict[str, Spec] = {
            "cursor": {
                "read": (self.selector, REQUIRED),
                "write": (self.target, REQUIRED),
                "end": (self.ends, REQUIRED),
                "empty_string": (self.choice("value", "end"), REQUIRED),
            },
            "offset": count,
            "page": count,
            "next_url": {
                "read": (self.selector, REQUIRED),
                "end": (self.ends, REQUIRED),
                "repeat_request_body": (self.boolean, False),
            },
            "link": {"header": (self.header, REQUIRED), "rel": (self.nonempty, "next")},
        }
        if (selected := self.tagged(value, at, "kind", variants, "a continuation")) is INVALID:
            return INVALID
        if selected["kind"] == "cursor" and _EMPTY_STRING in selected["end"]:
            where = f"{at}.end[{selected['end'].index(_EMPTY_STRING)}]"
            self.conflict(where, f"{where} ends at the empty string, which empty_string decides alone")
            return INVALID
        if selected["kind"] in {"offset", "page"} and ("has_more" in selected) == ("total" in selected):
            if "total" in selected:
                self.conflict(at, f"{at} needs has_more or total, not both")
                return INVALID
            return self.value(at, f"{at} needs has_more or total")
        return selected

    def step(self, value: object, at: str) -> object:
        spec: Spec = {"literal": (self.positive, OMITTED), "page_items_count": (self.true, OMITTED)}
        if (step := self.record(value, at, spec, "a step")) is not INVALID and len(step) != 1:
            return self.value(at, f"{at} needs exactly one of 'literal' and 'page_items_count'")
        return step

    def polling(self, value: object, at: str) -> Tree | _Invalid:
        """Convert a polling helper, refusing state sets that share a value and immediate statuses it accepts."""
        polling = self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "create": (self.operation, REQUIRED),
                "accepted_statuses": (self.statuses, REQUIRED),
                "poll": (self.operation, REQUIRED),
                "bindings": (self.bindings, REQUIRED),
                "state": (self.selector, REQUIRED),
                "pending": (self.states(nonempty=True), REQUIRED),
                "succeeded": (self.states(nonempty=True), REQUIRED),
                "failed": (self.states(nonempty=False), ()),
                "cancelled": (self.states(nonempty=False), ()),
                "result": (self.result, REQUIRED),
                "interval": (self.interval, {"seconds": 1.0, "retry_after_header": None}),
                "remote_cancel": (self.remote_cancel, OMITTED),
                "immediate_result": (self.immediate, OMITTED),
                "expires_at": (self.selector, OMITTED),
            },
            "a helper definition",
        )
        if polling is INVALID:
            return INVALID
        owners: dict[bytes, str] = {}
        for name in _STATE_SETS:
            for index, state in enumerate(polling[name]):
                if (other := owners.setdefault(canonical_bytes(state), name)) != name:
                    where = f"{at}.{name}[{index}]"
                    self.conflict(where, f"{where} is a state {at}.{other} also lists")
                    return INVALID
        if shared := sorted(
            set(polling.get("immediate_result", {}).get("statuses", ())) & set(polling["accepted_statuses"])
        ):
            where = f"{at}.immediate_result.statuses"
            self.conflict(where, f"{where} lists the accepted statuses {shared}")
            return INVALID
        return polling

    def result(self, value: object, at: str) -> object:
        variants: dict[str, Spec] = {
            "inline": {"selector": (self.selector, REQUIRED), "schema": (self.schema, REQUIRED)},
            "operation": {"operation": (self.operation, REQUIRED), "bindings": (self.bindings, ())},
            "none": {},
        }
        return self.tagged(value, at, "kind", variants, "a result")

    def interval(self, value: object, at: str) -> object:
        spec: Spec = {"seconds": (self.seconds, 1.0), "retry_after_header": (self.optional_header, None)}
        return self.record(value, at, spec, "an interval")

    def remote_cancel(self, value: object, at: str) -> object:
        spec: Spec = {"operation": (self.operation, REQUIRED), "bindings": (self.bindings, ())}
        return self.record(value, at, spec, "a remote cancel")

    def immediate(self, value: object, at: str) -> object:
        spec: Spec = {
            "statuses": (self.statuses, REQUIRED),
            "selector": (self.selector, REQUIRED),
            "schema": (self.schema, REQUIRED),
        }
        return self.record(value, at, spec, "an immediate result")

    def upload(self, value: Mapping[object, object], at: str, name: str) -> Tree | _Invalid:
        """Convert a resumable upload helper of the offset profile; the parts profile is not supported yet."""
        if value.get("profile") == "parts":
            message = f"The parts profile of the resumable_upload helper {name!r} is not supported yet"
            self.problems.append(_unsupported(f"{at}.profile", message))
            return INVALID
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "profile": (self.choice("offset"), REQUIRED),
                "create": (self.upload_create, REQUIRED),
                "probe": (self.upload_probe, REQUIRED),
                "append": (self.upload_append, REQUIRED),
                "max_chunk_bytes": (self.positive, REQUIRED),
                "partial_commit": (self.choice("allowed", "forbidden"), REQUIRED),
                "completion": (self.upload_completion, REQUIRED),
                "abort": (self.remote_cancel, OMITTED),
            },
            "a helper definition",
        )

    def upload_create(self, value: object, at: str) -> object:
        spec: Spec = {
            "operation": (self.operation, REQUIRED),
            "size": (self.target, OMITTED),
            "session_url": (self.selector, OMITTED),
            "expires_at": (self.selector, OMITTED),
        }
        return self.record(value, at, spec, "an upload create")

    def upload_probe(self, value: object, at: str) -> object:
        spec: Spec = {
            "operation": (self.operation, REQUIRED),
            "bindings": (self.bindings, ()),
            "remote_offset": (self.selector, REQUIRED),
        }
        return self.record(value, at, spec, "an upload probe")

    def upload_append(self, value: object, at: str) -> object:
        spec: Spec = {
            "operation": (self.operation, REQUIRED),
            "bindings": (self.bindings, ()),
            "offset": (self.target, REQUIRED),
            "length": (self.target, OMITTED),
            "checksum": (self.upload_checksum, OMITTED),
        }
        return self.record(value, at, spec, "an upload append")

    def upload_checksum(self, value: object, at: str) -> object:
        spec: Spec = {
            "target": (self.target, REQUIRED),
            "algorithm": (self.choice("md5", "sha1", "sha256", "sha512"), REQUIRED),
            "encoding": (self.choice("base64", "hex"), "base64"),
            "algorithm_prefix": (self.boolean, False),
        }
        return self.record(value, at, spec, "an upload checksum")

    def upload_completion(self, value: object, at: str) -> object:
        variants: dict[str, Spec] = {
            "length": {},
            "operation": {
                "operation": (self.operation, REQUIRED),
                "bindings": (self.bindings, ()),
                "result_schema": (self.schema, REQUIRED),
            },
        }
        return self.tagged(value, at, "kind", variants, "a completion")

    def stream(self, value: object, at: str, *, sse: bool) -> Tree | _Invalid:
        """Convert an SSE or NDJSON helper, refusing raw and error events without a discriminator."""
        final: Spec = {} if sse else {"final_line": (self.choice("require_newline", "allow_eof"), REQUIRED)}
        stream = self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "media": (self.media, REQUIRED),
                "event_schema": (partial(self.event_schema, sse=sse), REQUIRED),
                "completion": (partial(self.completion, sse=sse), REQUIRED),
                **final,
                "unknown": (self.choice("error", "raw"), "error"),
                "error_events": (self.event_schemas, {}),
                "resume": (partial(self.resume, sse=sse), {"enabled": False}),
            },
            "a helper definition",
        )
        if stream is INVALID:
            return INVALID
        mapping = stream["event_schema"]["mapping"] if isinstance(stream["event_schema"], dict) else None
        if stream["unknown"] == "raw" and mapping is None:
            self.conflict(f"{at}.unknown", f"{at}.unknown can be 'raw' only with a discriminator")
            return INVALID
        if not (events := stream["error_events"]):
            return stream
        if mapping is None:
            self.conflict(f"{at}.error_events", f"{at}.error_events need a discriminator")
            return INVALID
        if shared := next((key for key in events if key in mapping), None):
            where = f"{at}.error_events[{shared!r}]"
            self.conflict(where, f"{where} is also an event of {at}.event_schema.mapping")
            return INVALID
        return stream

    def event_schema(self, value: object, at: str, *, sse: bool) -> object:
        if isinstance(value, Mapping) and ("discriminator" in value or "mapping" in value):
            spec: Spec = {
                "discriminator": (partial(self.discriminator, sse=sse), REQUIRED),
                "mapping": (partial(self.event_schemas, nonempty=True), REQUIRED),
            }
            return self.record(value, at, spec, "an event schema")
        return self.schema(value, at)

    def discriminator(self, value: object, at: str, *, sse: bool) -> object:
        variants: dict[str, Spec] = {"event_type": {}} if sse else {}
        variants["body"] = {"pointer": (self.pointer, REQUIRED)}
        return self.tagged(value, at, "from", variants, "a discriminator")

    def event_schemas(self, value: object, at: str, *, nonempty: bool = False) -> object:
        if not isinstance(value, Mapping) or (nonempty and not value):
            return self.value(at, f"{at} must be a {'nonempty ' if nonempty else ''}mapping")
        schemas: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and key and _encodable(key):
                schemas[key] = self.schema(item, f"{at}[{key!r}]")
            else:
                schemas[""] = self.value(at, f"{at} has the key {key!r}, which is not a nonempty UTF-8 string")
        return INVALID if any(item is INVALID for item in schemas.values()) else schemas

    def completion(self, value: object, at: str, *, sse: bool) -> object:
        """Convert a completion; an NDJSON sentinel is a whole line, so it cannot contain a line feed."""
        sentinel = self.nonempty if sse else self.line
        variants: dict[str, Spec] = {"eof": {}, "sentinel": {"value": (sentinel, REQUIRED)}}
        if sse:
            variants["event_type"] = {"value": (self.nonempty, REQUIRED)}
        return self.tagged(value, at, "kind", variants, "a completion")

    def resume(self, value: object, at: str, *, sse: bool) -> object:
        """Convert a resume setting; a disabled one takes no other key, and an event ID cursor is SSE only."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be a mapping")
        if (enabled := value.get("enabled", False)) is False:
            for key in (extra := [key for key in value if key != "enabled"]):
                self.conflict(f"{at}.{key}", f"{at}.{key} applies only when resume is enabled")
            return INVALID if extra else {"enabled": False}
        if enabled is not True:
            return self.value(f"{at}.enabled", f"{at}.enabled must be a boolean")
        event_id = sse and value.get("cursor") == "event_id"
        cursor: Spec = (
            {}
            if event_id
            else {
                "missing": (self.choice("inherit", "error"), REQUIRED),
                "null": (self.choice("clear", "error"), REQUIRED),
            }
        )
        spec: Spec = {
            "enabled": (_keep, REQUIRED),
            "cursor": (_keep if event_id else self.selector, REQUIRED),
            "write": (self.target, REQUIRED),
            "reopen_operation": (self.operation, REQUIRED),
            "delivery": (self.choice("at_least_once"), REQUIRED),
            "bindings": (self.bindings, ()),
            **cursor,
            "reconnect_on": (self.reasons, ("transport_interruption",)),
            "expires_at": (self.selector, OMITTED),
        }
        return self.record(value, at, spec, "a resume")

    def reasons(self, value: object, at: str) -> object:
        reasons = self.items(value, at, self.choice("transport_interruption", "incomplete_eof"), nonempty=True)
        return self.distinct(at, reasons, "a reconnect reason")

    def websocket(self, value: object, at: str) -> Tree | _Invalid:
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "subprotocols": (self.subprotocols, ()),
                "compression": (self.boolean, False),
                "send": (self.message, REQUIRED),
                "receive": (self.message, REQUIRED),
            },
            "a helper definition",
        )

    def subprotocols(self, value: object, at: str) -> object:
        def subprotocol(item: object, where: str) -> object:
            return item if token(item) else self.value(where, f"{where} must be a subprotocol token")

        return self.distinct(at, self.items(value, at, subprotocol), "a subprotocol")

    def message(self, value: object, at: str) -> object:
        """Convert a message definition, giving it the frame its codec defaults to and refusing a mismatched one."""
        spec: Spec = {
            "codec": (self.choice(*_FRAMES), REQUIRED),
            "frame": (self.choice("text", "binary"), OMITTED),
            "schema": (self.schema, OMITTED),
        }
        if (message := self.record(value, at, spec, "a message definition")) is INVALID:
            return INVALID
        codec, fixed = message["codec"], _FRAMES[message["codec"]]
        if (codec == "json") != ("schema" in message):
            where = f"{at}.schema"
            if codec == "json":
                return self.value(where, f"{at} needs 'schema' for JSON messages")
            self.conflict(where, f"{where} applies only to JSON messages")
            return INVALID
        frame = message.setdefault("frame", fixed or "text")
        if fixed is not None and frame != fixed:
            self.conflict(f"{at}.frame", f"{at}.frame must be {fixed!r} for {codec} messages")
            return INVALID
        return message

    def cache(self, value: object, at: str) -> Tree | _Invalid:
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "validator": (self.choice(*_VALIDATOR_KINDS), REQUIRED),
                "authenticated": (self.boolean, REQUIRED),
                "statuses": (self.statuses, [200]),
                "vary_allowlist": (self.vary_names, []),
            },
            "a helper definition",
        )

    def vary_names(self, value: object, at: str) -> object:
        """Convert Vary header names, refusing `*` and a name repeated in another case."""
        names = self.items(value, at, self.vary_name)
        seen: set[str] = set()
        for index, name in enumerate(names if isinstance(names, list) else ()):
            if (folded := name.lower()) in seen:
                return self.value(f"{at}[{index}]", f"{at}[{index}] repeats a header name")
            seen.add(folded)
        return names

    def vary_name(self, value: object, at: str) -> object:
        return self.value(at, f"{at} must name a header, not '*'") if value == "*" else self.header(value, at)

    def webhook(self, value: object, at: str) -> Tree | _Invalid:
        """Convert a webhook helper with a fixed signature preset or application verifier."""
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "event_schema": (partial(self.event_schema, sse=False), REQUIRED),
                "signature": (self.signature, REQUIRED),
            },
            "a helper definition",
        )

    def signature(self, value: object, at: str) -> object:
        """Convert fixed signature settings without interpreting arbitrary signed parts."""
        body: Spec = {
            "header": (self.header, REQUIRED),
            "encoding": (self.choice("hex", "base64"), "hex"),
            "prefix": (partial(self.visible, empty=True), ""),
        }
        public: Spec = {**body, "encoding": (self.choice("hex", "base64", "base64url"), "hex")}
        fact = self.choice("required", "none")
        return self.tagged(
            value,
            at,
            "kind",
            {
                "standard_webhooks": {},
                "stripe_style": {"header": (self.header, "Stripe-Signature")},
                "body_hmac": {**body, "algorithm": (self.choice("hmac-sha256", "hmac-sha512"), "hmac-sha256")},
                **dict.fromkeys(_PUBLIC_KEY_SIGNATURES, public),
                "adapter": {"timestamp": (fact, REQUIRED), "delivery_id": (fact, REQUIRED)},
                "none": {},
            },
            "a signature",
        )

    def visible(self, value: object, at: str, *, empty: bool = False) -> object:
        valid = isinstance(value, str) and (empty or value) and all("!" <= char <= "~" for char in value)
        return value if valid else self.value(at, f"{at} must be {'' if empty else 'nonempty '}visible ASCII text")


def _keep(value: object, _: str) -> object:
    return value


def _unsupported(at: str, message: str) -> Diagnostic:
    return Diagnostic(code="E_CLIENT_UNSUPPORTED", severity="error", stage="target", message=message, option_path=at)


def _targets(bindings: list[Tree], at: str) -> tuple[tuple[str, Tree], ...]:
    return tuple((f"{at}[{index}].target", binding["target"]) for index, binding in enumerate(bindings))


def _links(kind: str, tree: Tree, at: str) -> Iterator[Link]:
    """Yield the operations a valid helper sends, its entry operation first, with the targets each one takes."""
    match kind:
        case "pagination":
            continuation = tree["continuation"]
            written = ((f"{at}.continuation.write", continuation["write"]),) if "write" in continuation else ()
            yield Link(
                at=f"{at}.operation",
                ref=tree["operation"],
                targets=(*written, *_targets(tree["bindings"], f"{at}.bindings")),
            )
        case "webhook":
            return
        case "websocket":
            yield Link(at=f"{at}.operation", ref=tree["operation"])
        case "cache":
            yield Link(at=f"{at}.operation", ref=tree["operation"])
        case "resumable_upload":
            yield from _upload_links(tree, at)
        case "polling":
            yield Link(at=f"{at}.create", ref=tree["create"])
            yield Link(at=f"{at}.poll", ref=tree["poll"], targets=_targets(tree["bindings"], f"{at}.bindings"))
            if (result := tree["result"])["kind"] == "operation":
                where = f"{at}.result"
                targets = _targets(result["bindings"], f"{where}.bindings")
                yield Link(at=f"{where}.operation", ref=result["operation"], targets=targets)
            if (cancel := tree.get("remote_cancel")) is not None:
                where = f"{at}.remote_cancel"
                targets = _targets(cancel["bindings"], f"{where}.bindings")
                yield Link(at=f"{where}.operation", ref=cancel["operation"], targets=targets)
        case _:
            yield Link(at=f"{at}.operation", ref=tree["operation"])
            if (resume := tree["resume"])["enabled"]:
                where = f"{at}.resume"
                targets = ((f"{where}.write", resume["write"]), *_targets(resume["bindings"], f"{where}.bindings"))
                yield Link(at=f"{where}.reopen_operation", ref=resume["reopen_operation"], targets=targets)


def _upload_links(tree: Tree, at: str) -> Iterator[Link]:
    """Yield the operations an upload helper sends, its create operation first, with the targets each one takes."""
    create = tree["create"]
    sized = ((f"{at}.create.size", create["size"]),) if "size" in create else ()
    yield Link(at=f"{at}.create.operation", ref=create["operation"], targets=sized)
    probe = tree["probe"]
    yield Link(
        at=f"{at}.probe.operation", ref=probe["operation"], targets=_targets(probe["bindings"], f"{at}.probe.bindings")
    )
    append = tree["append"]
    written = tuple((f"{at}.append.{name}", append[name]) for name in ("offset", "length") if name in append)
    if (checksum := append.get("checksum")) is not None:
        written = (*written, (f"{at}.append.checksum.target", checksum["target"]))
    yield Link(
        at=f"{at}.append.operation",
        ref=append["operation"],
        targets=(*written, *_targets(append["bindings"], f"{at}.append.bindings")),
    )
    for name in ("completion", "abort"):
        if (part := tree.get(name)) is not None and "operation" in part:
            where = f"{at}.{name}"
            yield Link(
                at=f"{where}.operation", ref=part["operation"], targets=_targets(part["bindings"], f"{where}.bindings")
            )
