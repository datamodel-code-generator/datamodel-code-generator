"""Closed version 1 capability records that codec adapters declare and the core compares before any request."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Final, Generic, Literal, TypeAlias, TypeVar

from typing_extensions import TypeIs

from .bindings import BackendId, NativeKind  # noqa: TC001 - Public annotations support get_type_hints().
from .context import Direction, Surface  # noqa: TC001 - Public annotations support get_type_hints().
from .parameters import ParameterLocation  # noqa: TC001 - Public annotations support get_type_hints().

WireKind: TypeAlias = Literal["null", "boolean", "integer", "number", "string", "array", "object"]
PresenceCapability: TypeAlias = Literal["native", "unavailable"]

MAX_SUBJECT_BYTES: Final = 1048576
MAX_TOTAL_SUBJECT_BYTES: Final = 33554432
_BACKENDS: Final = frozenset({
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
})
_NATIVE_KINDS: Final = frozenset({
    "model",
    "dataclass",
    "typed_dict",
    "struct",
    "scalar",
    "enum",
    "literal",
    "union",
    "array",
    "map",
    "root",
    "alias",
})
_WIRE_KINDS: Final = frozenset({"null", "boolean", "integer", "number", "string", "array", "object"})
_LOCATIONS: Final = frozenset({"path", "query", "querystring", "header", "cookie"})
_DIRECTIONS: Final = frozenset({"request", "response"})
_SURFACES: Final = frozenset({"client", "server"})
_BOTH_DIRECTIONS: Final[tuple[Direction, ...]] = ("request", "response")
_BOTH_SURFACES: Final[tuple[Surface, ...]] = ("client", "server")


def _members(name: str, values: tuple[object, ...], allowed: frozenset[str] | None, *, nonempty: bool) -> None:
    if nonempty and not values:
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    if len(set(values)) != len(values):
        msg = f"{name} must not repeat a value"
        raise ValueError(msg)
    if allowed is not None and not allowed.issuperset(values):
        msg = f"{name} contains an unknown value"
        raise ValueError(msg)


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _canonical(record: AnyCapabilities) -> tuple[object, ...]:
    return (
        type(record),
        *(
            tuple(sorted(value, key=repr)) if _is_tuple(value := getattr(record, field.name)) else value
            for field in fields(record)
        ),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CodecCapabilities:
    """Declare the backends, native kinds, directions, and surfaces a model adapter supports."""

    backends: tuple[BackendId, ...]
    native_kinds: tuple[NativeKind, ...]
    directions: tuple[Direction, ...] = _BOTH_DIRECTIONS
    surfaces: tuple[Surface, ...] = _BOTH_SURFACES
    presence: PresenceCapability = "unavailable"

    def __post_init__(self) -> None:
        """Reject empty, repeated, or unknown values."""
        _members("backends", self.backends, _BACKENDS, nonempty=True)
        _members("native_kinds", self.native_kinds, _NATIVE_KINDS, nonempty=True)
        _members("directions", self.directions, _DIRECTIONS, nonempty=True)
        _members("surfaces", self.surfaces, _SURFACES, nonempty=True)
        if self.presence not in {"native", "unavailable"}:
            msg = "presence must be 'native' or 'unavailable'"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterCodecCapabilities:
    """Declare the parameter locations, styles, explode values, wire kinds, and media a parameter adapter supports."""

    locations: tuple[ParameterLocation, ...]
    styles: tuple[str, ...]
    explode_values: tuple[bool, ...]
    value_kinds: tuple[WireKind, ...]
    media_types: tuple[str, ...] = ()
    directions: tuple[Direction, ...] = _BOTH_DIRECTIONS
    surfaces: tuple[Surface, ...] = _BOTH_SURFACES
    supports_empty_containers: bool = False

    def __post_init__(self) -> None:
        """Require style and explode sets together, and content media when neither is declared."""
        _members("locations", self.locations, _LOCATIONS, nonempty=True)
        _members("styles", self.styles, None, nonempty=False)
        _members("explode_values", self.explode_values, None, nonempty=False)
        _members("value_kinds", self.value_kinds, _WIRE_KINDS, nonempty=True)
        _members("media_types", self.media_types, None, nonempty=not self.styles)
        _members("directions", self.directions, _DIRECTIONS, nonempty=True)
        _members("surfaces", self.surfaces, _SURFACES, nonempty=True)
        if bool(self.styles) != bool(self.explode_values):
            msg = "styles and explode_values must both be declared or both be empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaCodecCapabilities:
    """Declare the dialects, vocabularies, keywords, pattern dialects, and limits a schema adapter supports."""

    dialects: tuple[str, ...]
    vocabularies: tuple[str, ...]
    keywords: tuple[str, ...]
    pattern_dialects: tuple[str, ...] = ()
    directions: tuple[Direction, ...] = _BOTH_DIRECTIONS
    surfaces: tuple[Surface, ...] = _BOTH_SURFACES
    max_subject_bytes: int = MAX_SUBJECT_BYTES
    max_total_subject_bytes: int = MAX_TOTAL_SUBJECT_BYTES

    def __post_init__(self) -> None:
        """Keep both byte limits positive and within the common limits."""
        _members("dialects", self.dialects, None, nonempty=True)
        _members("vocabularies", self.vocabularies, None, nonempty=False)
        _members("keywords", self.keywords, None, nonempty=False)
        _members("pattern_dialects", self.pattern_dialects, None, nonempty=False)
        _members("directions", self.directions, _DIRECTIONS, nonempty=True)
        _members("surfaces", self.surfaces, _SURFACES, nonempty=True)
        if not (0 < self.max_subject_bytes <= MAX_SUBJECT_BYTES) or not (
            0 < self.max_total_subject_bytes <= MAX_TOTAL_SUBJECT_BYTES
        ):
            msg = "schema adapter byte limits must be positive and within the common limits"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientMediaCodecCapabilities:
    """Declare the media and directions a buffered client media adapter supports."""

    media_types: tuple[str, ...]
    directions: tuple[Direction, ...] = _BOTH_DIRECTIONS
    surfaces: tuple[Literal["client"]] = ("client",)
    supports_sync: bool = True
    supports_async: bool = True
    incremental_request: bool = False
    incremental_response: bool = False

    def __post_init__(self) -> None:
        """Require one client surface, both call styles, and buffered processing."""
        _members("media_types", self.media_types, None, nonempty=True)
        _members("directions", self.directions, _DIRECTIONS, nonempty=True)
        if self.surfaces != ("client",) or not (self.supports_sync and self.supports_async):
            msg = "a client media adapter registers the client surface and supports sync and async calls"
            raise ValueError(msg)
        if self.incremental_request or self.incremental_response:
            msg = "incremental media processing has no adapter contract yet"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerMediaCodecCapabilities:
    """Declare the media and directions a buffered server media adapter supports."""

    media_types: tuple[str, ...]
    directions: tuple[Direction, ...] = _BOTH_DIRECTIONS
    surfaces: tuple[Literal["server"]] = ("server",)

    def __post_init__(self) -> None:
        """Require one server surface."""
        _members("media_types", self.media_types, None, nonempty=True)
        _members("directions", self.directions, _DIRECTIONS, nonempty=True)
        if self.surfaces != ("server",):
            msg = "a server media adapter registers the server surface"
            raise ValueError(msg)


MediaCodecCapabilities: TypeAlias = ClientMediaCodecCapabilities | ServerMediaCodecCapabilities
AnyCapabilities: TypeAlias = (
    CodecCapabilities | ParameterCodecCapabilities | SchemaCodecCapabilities | MediaCodecCapabilities
)
CapabilitiesT_co = TypeVar("CapabilitiesT_co", bound=AnyCapabilities, covariant=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterManifest(Generic[CapabilitiesT_co]):
    """Pin one adapter registration's identity and declared capabilities in the generated bindings."""

    name: str
    import_ref: str
    capabilities: CapabilitiesT_co  # type: ignore[misc, unused-ignore]


def canonical_capabilities(record: AnyCapabilities) -> tuple[object, ...]:
    """Return the record kind and values with each value set in a fixed order, so equal declarations compare equal."""
    return _canonical(record)
