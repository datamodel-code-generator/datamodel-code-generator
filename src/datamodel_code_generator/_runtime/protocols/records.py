"""Selectors, request targets, continuations, poll snapshots, cancel receipts, progress keys, and canonical JSON."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, field
from typing import Final, Generic, Literal, NoReturn, TypeAlias, final, get_args

from typing_extensions import Self, TypeVar

from ..client.responses import ResponseInfo
from ..model_codecs.media import json_value
from ..model_codecs.plain import JSONValue  # noqa: TC001 - Public annotations support get_type_hints().

__all__ = (
    "BodySelector",
    "BodyTarget",
    "CancelReceipt",
    "Continuation",
    "HeaderSelector",
    "ParameterTarget",
    "PollSnapshot",
    "ProgressKey",
    "ProtocolProgress",
    "QuerystringTarget",
    "RequestTarget",
    "Selector",
    "StatusSelector",
)

P_co = TypeVar("P_co", covariant=True, default=object)
C_co = TypeVar("C_co", covariant=True, default=object)

ContinuationKind: TypeAlias = Literal["cursor", "offset", "page", "next_url", "link"]
ProgressKey: TypeAlias = Literal[
    "pages",
    "items",
    "polls",
    "reconnects",
    "parts",
    "confirmed_bytes",
    "network_send_count",
    "network_send_budget_used",
    "messages_sent",
    "messages_received",
]
ProtocolProgress: TypeAlias = Mapping[ProgressKey, int]

PROGRESS_KEYS: Final = get_args(ProgressKey)
_CONTINUATION_KINDS: Final = get_args(ContinuationKind)
_OCCURRENCES: Final = ("single", "all")
_PARAMETER_LOCATIONS: Final = ("path", "query", "header", "cookie")
_POINTER: Final = re.compile(r"(?:/(?:[^~/]|~[01])*)*")
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_SURROGATE: Final = re.compile(r"[\ud800-\udfff]")
_NESTING: Final = "A wire value must not be nested beyond the interpreter recursion limit"
_SURROGATES: Final = "A wire string must not contain lone surrogates"


def record_string(value: object, name: str) -> str:
    """Return a record field that must be a string, refusing another type with TypeError."""
    if isinstance(value, str):
        return value
    msg = f"{name} must be a string"
    raise TypeError(msg)


def wire_string(value: object, name: str) -> str:
    """Return a record field that must be UTF-8 encodable wire text, refusing lone surrogates with ValueError."""
    if (text := record_string(value, name)).isascii() or _SURROGATE.search(text) is None:
        return text
    raise ValueError(_SURROGATES)


def record_instance(value: object, kinds: type | tuple[type, ...], message: str) -> None:
    """Refuse a record field of another type with TypeError."""
    if not isinstance(value, kinds):
        raise TypeError(message)


def _matching(value: object, name: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(record_string(value, name)):
        msg = f"{name} is not in its required form"
        raise ValueError(msg)


def _nonempty(value: object, name: str) -> None:
    if not record_string(value, name):
        msg = f"{name} must not be empty"
        raise ValueError(msg)


def _choice(value: object, name: str, choices: tuple[str, ...]) -> None:
    if record_string(value, name) not in choices:
        msg = f"{name} must be one of its declared values"
        raise ValueError(msg)


def _dumped(value: object, *, sort_keys: bool) -> bytes:
    try:
        text = json.dumps(value, sort_keys=sort_keys, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return text.encode()
    except RecursionError:
        raise ValueError(_NESTING) from None
    except UnicodeEncodeError:
        raise ValueError(_SURROGATES) from None


def plain_copy(value: object) -> JSONValue:
    """Copy a JSON value through its UTF-8 JSON text, refusing what `canonical_json` refuses."""
    return json_value(_dumped(value, sort_keys=False))


def canonical_json(value: object) -> bytes:
    """Encode a JSON value compactly as UTF-8, with members sorted by name, refusing non-finite numbers.

    A value nested beyond the interpreter recursion limit, a lone surrogate, or an integer over the interpreter's
    decimal conversion limit raises ValueError, and a value that is not JSON raises TypeError.
    """
    return _dumped(value, sort_keys=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class BodySelector:
    """Select a decoded JSON body member by its RFC 6901 pointer over wire names; an empty pointer is the body."""

    pointer: str

    def __post_init__(self) -> None:
        """Require an RFC 6901 pointer, never JSONPath, expressions, or wildcards."""
        _matching(self.pointer, "pointer", _POINTER)


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderSelector:
    """Select a response header by its case-insensitive name, requiring one value unless `all` is declared."""

    name: str
    occurrence: Literal["single", "all"] = "single"

    def __post_init__(self) -> None:
        """Require an HTTP token name, kept as given, and a declared occurrence."""
        _matching(self.name, "name", _TOKEN)
        _choice(self.occurrence, "occurrence", _OCCURRENCES)


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusSelector:
    """Select the final response status code."""


Selector: TypeAlias = BodySelector | HeaderSelector | StatusSelector


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterTarget:
    """Write a path, query, header, or cookie parameter by its wire name."""

    location: Literal["path", "query", "header", "cookie"]
    name: str

    def __post_init__(self) -> None:
        """Require a declared location and a nonempty name, an HTTP token for headers and cookies."""
        _choice(self.location, "location", _PARAMETER_LOCATIONS)
        if self.location in {"header", "cookie"}:
            _matching(self.name, "name", _TOKEN)
        else:
            _nonempty(self.name, "name")


@dataclass(frozen=True, slots=True, kw_only=True)
class QuerystringTarget:
    """Write the whole declared querystring value, or one of its properties by an RFC 6901 pointer."""

    name: str
    pointer: str

    def __post_init__(self) -> None:
        """Require the nonempty declaration identity, which is never sent, and an RFC 6901 pointer."""
        _nonempty(self.name, "name")
        _matching(self.pointer, "pointer", _POINTER)


@dataclass(frozen=True, slots=True, kw_only=True)
class BodyTarget:
    """Write a request body member by its RFC 6901 pointer; an empty pointer is the whole body."""

    pointer: str

    def __post_init__(self) -> None:
        """Require an RFC 6901 pointer."""
        _matching(self.pointer, "pointer", _POINTER)


RequestTarget: TypeAlias = ParameterTarget | QuerystringTarget | BodyTarget


class Sealed:
    """Refuse attribute assignment, deletion, and pickling; a copy is the value itself."""

    __slots__ = ()

    def __setattr__(self, name: str, value: object) -> NoReturn:
        """Refuse assignment, because the value is immutable."""
        msg = f"{type(self).__name__} is immutable"
        raise FrozenInstanceError(msg)

    def __delattr__(self, name: str) -> NoReturn:
        """Refuse deletion, because the value is immutable."""
        msg = f"{type(self).__name__} is immutable"
        raise FrozenInstanceError(msg)

    def __copy__(self) -> Self:
        """Return the value itself, since it never changes."""
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        """Return the value itself, since it never changes."""
        return self

    def __reduce__(self) -> NoReturn:
        """Refuse pickling, because the value is bound to this process's helpers."""
        msg = f"{type(self).__name__} cannot be pickled"
        raise TypeError(msg)


@final
class Continuation(Sealed):
    """An opaque position of a helper session; only its kind is public and each instance equals only itself."""

    __slots__ = ("_json", "_kind")

    _kind: ContinuationKind
    _json: bytes

    def __init__(self, *, kind: ContinuationKind, value: JSONValue) -> None:
        """Keep the kind and only the canonical JSON of the value."""
        _choice(kind, "kind", _CONTINUATION_KINDS)
        encoded = canonical_json(value)
        for name, item in (("_kind", kind), ("_json", encoded)):
            object.__setattr__(self, name, item)

    @property
    def kind(self) -> ContinuationKind:
        """Return how the helper applies this continuation."""
        return self._kind

    def __repr__(self) -> str:
        """Name the kind only, never the continuation value."""
        return f"Continuation(kind={self._kind!r})"


def continuation_json(continuation: Continuation) -> bytes:
    """Return the canonical JSON a helper compares or writes for a continuation."""
    return continuation._json  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


@dataclass(frozen=True, slots=True, kw_only=True)
class PollSnapshot(Generic[P_co]):
    """One poll of a long-running operation: its exact state value, whether it is terminal, and the poll data."""

    state: JSONValue = field(repr=False)
    terminal: bool
    data: P_co = field(repr=False)
    response: ResponseInfo

    def __post_init__(self) -> None:
        """Copy the state value and require a boolean terminal flag and the response metadata."""
        object.__setattr__(self, "state", plain_copy(self.state))
        record_instance(self.terminal, bool, "terminal must be a bool")
        record_instance(self.response, ResponseInfo, "response must be a ResponseInfo")


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelReceipt(Generic[C_co]):
    """The response of a remote cancel request: its decoded body and metadata, never a sign the operation ended."""

    data: C_co = field(repr=False)
    response: ResponseInfo

    def __post_init__(self) -> None:
        """Require the response metadata."""
        record_instance(self.response, ResponseInfo, "response must be a ResponseInfo")
