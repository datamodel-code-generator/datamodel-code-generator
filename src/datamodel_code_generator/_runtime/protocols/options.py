"""Helper limits, the step lock of a helper's handle, and the helper settings of a client; UNSET takes the default."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from inspect import iscoroutinefunction
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Final, TypeAlias, cast

from typing_extensions import Self, TypeVar

from ..client.errors import ConfigurationError
from ..client.options import RequestOptions
from ..client.timing import SYSTEM_CLOCK, check_limits
from ..model_codecs.unset import UNSET

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import TracebackType

    from ..client.logical import OperationSession
    from ..client.timing import Clock
    from .origins import Origin

V = TypeVar("V")

_SESSION: Final = ("total_timeout", True, True, True)
_PAGINATION: Final = (
    ("max_pages", False, True, False),
    ("max_items", False, True, True),
    _SESSION,
)
_POLL: Final = (
    ("max_polls", False, True, False),
    ("interval", True, False, False),
    ("max_wait", True, True, False),
    _SESSION,
)
_STREAM: Final = (
    ("idle_timeout", True, True, False),
    ("max_reconnects", False, True, True),
    ("max_reconnect_wait", True, True, False),
    _SESSION,
)
_UPLOAD: Final = (("chunk_bytes", False, False, False), _SESSION)
_WS: Final = (
    ("open_timeout", True, True, False),
    ("idle_timeout", True, True, False),
    ("max_message_bytes", False, False, False),
    ("ping_interval", True, True, False),
    ("pong_timeout", True, True, False),
    _SESSION,
)
_CACHE: Final = (("max_ttl", True, False, False),)
_CACHE_METHODS: Final = ("get", "set", "delete")


@dataclass(frozen=True, slots=True, kw_only=True)
class HelperLimits:
    """The limits every helper call has: its session's total timeout, the call's options, and the client's clock."""

    total_timeout: float | None = None
    options: RequestOptions | None = None
    clock: Clock = SYSTEM_CLOCK


def layered(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return an option from the first layer that sets it, skipping None and UNSET layers, or its default."""
    for layer in layers:
        if layer is not None and layer is not UNSET and (value := getattr(layer, name)) is not UNSET:
            return cast("V", value)
    return default


def checked_options(helper_id: str, name: str, value: object, kind: type, options: object) -> RequestOptions | None:
    """Refuse a call's helper options, then its request options, of another type; return the request options."""
    for field, given, expected in ((name, value, kind), ("options", options, RequestOptions)):
        if given is not None and not isinstance(given, expected):
            raise ConfigurationError(field_path=(field,), reason="invalid_value", helper_id=helper_id)
    return options if isinstance(options, RequestOptions) else None


def new_session(limits: HelperLimits) -> OperationSession:
    """Start the session of one helper call."""
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a started helper loads the call runtime.

    return OperationSession(total_timeout=limits.total_timeout, clock=limits.clock)


class StepLock:
    """The one step a helper's handle runs at a time, and the refusals of a step its state forbids.

    A step while another one runs is refused with the state `_busy` names, and a step of a closed handle with `closed`.
    """

    __slots__ = ("_closed", "_helper_id", "_lock")

    _busy: ClassVar[str]

    def __init__(self, helper_id: str) -> None:
        """Start an open handle that runs no step."""
        self._helper_id = helper_id
        self._lock = threading.Lock()
        self._closed = False

    def _state_error(self, action: str, state: str) -> ConfigurationError:
        from .errors import state_error  # noqa: PLC0415 - Only a refused step loads the helper errors.

        return state_error(self._helper_id, action, state)

    def _refused(self) -> str | None:
        """Return the state that refuses every step of the handle, or None."""
        return "closed" if self._closed else None

    def _enter(self, action: str) -> None:
        """Take the handle for one step, refusing a concurrent step and a handle whose state refuses steps."""
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, self._busy)
        if (state := self._refused()) is not None:
            self._lock.release()
            raise self._state_error(action, state)

    def _close(self, action: str, *, quiet: bool = False) -> None:
        """Close the handle, refusing while a step runs, or leaving it open then when `quiet`."""
        if not self._lock.acquire(blocking=False):
            if quiet:
                return
            raise self._state_error(action, self._busy)
        self._closed = True
        self._lock.release()


class StepHandle(StepLock):
    """A handle that leaving its `with` block closes; while another step runs, the block's error is not masked."""

    __slots__ = ()

    def __enter__(self) -> Self:
        """Return this handle, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the handle; while another step runs, leave it open rather than mask the block's error."""
        self._close("close", quiet=exc is not None)


class AsyncStepHandle(StepLock):
    """An asyncio handle that leaving its `async with` block closes, as a `StepHandle` does."""

    __slots__ = ()

    async def __aenter__(self) -> Self:
        """Return this handle, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the handle; while another step runs, leave it open rather than mask the block's error."""
        self._close("aclose", quiet=exc is not None)


def _instance(value: object, kinds: tuple[type, ...], name: str) -> None:
    if value is not UNSET and not isinstance(value, kinds):
        raise ConfigurationError(field_path=(name,), reason="invalid_value")


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationOptions:
    """Pagination limits; max_pages, max_items, and total_timeout take None, and max_items and total_timeout take 0.

    `total_timeout` bounds the whole session in seconds, counted from its start.
    """

    max_pages: int | UNSET | None = UNSET
    max_items: int | UNSET | None = UNSET
    total_timeout: float | UNSET | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _PAGINATION)


@dataclass(frozen=True, slots=True, kw_only=True)
class PollOptions:
    """Polling limits; max_polls, max_wait, and total_timeout take None, and durations are finite positive seconds.

    `total_timeout`, which also takes 0, bounds the whole session in seconds, counted from its start.
    """

    max_polls: int | UNSET | None = UNSET
    interval: float | UNSET = UNSET
    max_wait: float | UNSET | None = UNSET
    total_timeout: float | UNSET | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, and nonfinite durations."""
        check_limits(self, _POLL)


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamOptions:
    """Stream limits; reconnection stays off unless enabled, and only max_reconnects and total_timeout take 0.

    `total_timeout` bounds the whole session, reconnections included, in seconds counted from its start.
    """

    idle_timeout: float | UNSET | None = UNSET
    reconnect: bool | UNSET = UNSET
    max_reconnects: int | UNSET | None = UNSET
    max_reconnect_wait: float | UNSET | None = UNSET
    total_timeout: float | UNSET | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, a nonboolean reconnect switch, and every forbidden None or zero."""
        check_limits(self, _STREAM)
        _instance(self.reconnect, (bool,), "reconnect")


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadOptions:
    """Upload limits: the positive size of the chunk one append holds in memory, and the session's total timeout.

    `total_timeout` bounds the whole session in seconds, counted from its start; None removes it.
    """

    chunk_bytes: int | UNSET = UNSET
    total_timeout: float | UNSET | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _UPLOAD)


@dataclass(frozen=True, slots=True, kw_only=True)
class WSOptions:
    """WebSocket limits; durations are finite positive seconds, None removes a limit where it is allowed.

    `idle_timeout` bounds a receive waiting for a message and inherits the native read timeout. `max_message_bytes` is
    passed to HTTPX2 as its message size limit, whose own default applies while it is unset. `total_timeout`, which
    also takes 0, bounds the whole session in seconds, counted from its start.
    """

    open_timeout: float | UNSET | None = UNSET
    idle_timeout: float | UNSET | None = UNSET
    max_message_bytes: int | UNSET = UNSET
    ping_interval: float | UNSET | None = UNSET
    pong_timeout: float | UNSET | None = UNSET
    total_timeout: float | UNSET | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, and forbidden None or zero."""
        check_limits(self, _WS)


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheOptions:
    """Cache limits of one fetch: the cap on any entry's freshness."""

    max_ttl: float | UNSET = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, and nonfinite durations."""
        check_limits(self, _CACHE)


HelperOptions: TypeAlias = PaginationOptions | PollOptions | StreamOptions | UploadOptions | CacheOptions | WSOptions
_KIND_OPTIONS: Final[Mapping[str, type]] = MappingProxyType({
    "pagination": PaginationOptions,
    "polling": PollOptions,
    "sse": StreamOptions,
    "ndjson": StreamOptions,
    "resumable_upload": UploadOptions,
    "cache": CacheOptions,
    "websocket": WSOptions,
})


class HelperSettings:
    """The helper settings of one client root, which its views share: helper defaults, lent stores, and origins.

    The defaults and the lent cache stores are keyed by helper name, and duplicate origins are dropped. `created`
    holds, under the empty name, the one memory store the root creates for the cache helpers no store is lent to.
    """

    __slots__ = ("allowed_origins", "cache_stores", "created", "defaults")

    def __init__(
        self,
        *,
        defaults: Mapping[str, HelperOptions] | None = None,
        cache_stores: Mapping[str, object] | None = None,
        allowed_origins: Sequence[Origin] | None = None,
    ) -> None:
        """Copy the mappings and the origins, so that later changes to the caller's leave the client's alone."""
        self.defaults: Mapping[str, HelperOptions] = MappingProxyType(dict(defaults or {}))
        self.cache_stores: Mapping[str, object] = MappingProxyType(dict(cache_stores or {}))
        self.allowed_origins: tuple[Origin, ...] = tuple(dict.fromkeys(allowed_origins or ()))
        self.created: dict[str, object] = {}

    def check_helpers(self, helpers: tuple[tuple[str, str], ...], *, asynchronous: bool) -> None:
        """Refuse defaults or stores for a helper the package lacks, another kind's options, and an unusable store.

        A store must have every method of the cache store contract, coroutine functions for an asyncio client and plain
        functions for a synchronous one.
        """
        kinds = dict(helpers)
        for name, options in self.defaults.items():
            if (kind := kinds.get(name)) is None:
                raise ConfigurationError(field_path=("helper_defaults", name), reason="unknown_field")
            if not isinstance(options, _KIND_OPTIONS[kind]):
                raise ConfigurationError(field_path=("helper_defaults", name), reason="invalid_value")
        for name, store in self.cache_stores.items():
            if kinds.get(name) != "cache":
                raise ConfigurationError(field_path=("cache_stores", name), reason="unknown_field")
            methods = [getattr(store, method, None) for method in _CACHE_METHODS]
            if not all(callable(method) and iscoroutinefunction(method) == asynchronous for method in methods):
                raise ConfigurationError(field_path=("cache_stores", name), reason="wrong_capability")
