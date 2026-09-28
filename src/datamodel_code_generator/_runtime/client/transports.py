"""Transport adapters: how a client hands one prepared request to an HTTP implementation and reads the response.

The clients send through HTTPX2 unless a transport adapter is given; an adapter is borrowed unless it is wrapped in
`OwnedTransportAdapter`, which closing the client closes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass, field
from time import monotonic, time
from typing import TYPE_CHECKING, Final, Generic, Protocol, get_args

from typing_extensions import TypeIs, TypeVar

from .bodies import AsyncBodyAttempt, BodyAttempt  # noqa: TC001 - Public annotations support get_type_hints().
from .errors import IOPhase
from .responses import HeadersView
from .timing import (
    CancelToken,
    Deadline,
    ResolvedTimeoutOptions,
)

if TYPE_CHECKING:
    from .evidence import ConnectFailureEvidence

__all__ = (
    "AsyncTransportAdapter",
    "AsyncTransportResponse",
    "AttemptIOContext",
    "IOPhase",
    "OwnedTransportAdapter",
    "PreparedRequest",
    "ResolvedTimeoutOptions",
    "TransportAdapter",
    "TransportCapabilities",
    "TransportResponse",
    "TransportTraceSink",
)

AttemptT_co = TypeVar("AttemptT_co", bound="BodyAttempt | AsyncBodyAttempt", covariant=True)
AdapterT_co = TypeVar("AdapterT_co", bound="TransportAdapter | AsyncTransportAdapter", covariant=True)

PHASES: Final[frozenset[str]] = frozenset(get_args(IOPhase))
_MIN_STATUS: Final = 100
_MAX_STATUS: Final = 599


@dataclass(frozen=True, slots=True, kw_only=True)
class TransportCapabilities:
    """What an adapter declares: its own retries (0 for none, None when unknown), delivery evidence, and versions."""

    internal_retry_limit: int | None
    delivery_evidence: bool
    http_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedRequest(Generic[AttemptT_co]):
    """One attempt's request, fully built: method, absolute URL, ordered headers, and the body to read, if any."""

    method: str
    url: str
    headers: HeadersView
    body: AttemptT_co | None


class TransportTraceSink(Protocol):
    """Where an adapter reports how far an attempt got; an adapter without that evidence reports nothing."""

    def phase_started(self, phase: IOPhase) -> None:
        """Record that an I/O phase started."""
        ...

    def request_headers_started(self) -> None:
        """Record that the request headers started to go out."""
        ...

    def response_headers_received(self, *, http_version: str, status_code: int, headers: HeadersView) -> None:
        """Record that the response headers arrived."""
        ...

    def wire_send(self) -> None:
        """Record that request bytes reached the network."""
        ...


class AttemptIOContext:
    """What one attempt's adapter may read: the current phase, the phase timeouts, and the trace sink."""

    __slots__ = ("_cancel_token", "_deadline", "_timeout", "_trace")

    def __init__(
        self,
        timeout: ResolvedTimeoutOptions,
        trace: AttemptTrace,
        *,
        deadline: Deadline | None = None,
        cancel_token: CancelToken | None = None,
    ) -> None:
        """Bind the resolved timeouts, termination signals, and the attempt's trace."""
        self._timeout = timeout
        self._trace = trace
        self._deadline = deadline
        self._cancel_token = cancel_token

    @property
    def phase(self) -> IOPhase:
        """Return the phase the attempt reached last."""
        return self._trace.phase

    @property
    def timeout(self) -> ResolvedTimeoutOptions:
        """Return the timeout of each phase."""
        return self._timeout

    @property
    def deadline(self) -> Deadline | None:
        """Return the deadline that bounds this attempt or its handed-off stream."""
        return self._deadline

    @property
    def cancel_token(self) -> CancelToken | None:
        """Return the caller's explicit cancellation signal, when supplied."""
        return self._cancel_token

    @property
    def trace(self) -> TransportTraceSink:
        """Return the sink the adapter reports its evidence to."""
        return self._trace


def set_io_timing(context: AttemptIOContext, timeout: ResolvedTimeoutOptions, deadline: Deadline | None) -> None:
    """Update the adapter's live timing view when acquisition hands off a stream."""
    object.__setattr__(context, "_timeout", timeout)  # noqa: PLC2801 - Update the readonly adapter view.
    object.__setattr__(context, "_deadline", deadline)  # noqa: PLC2801


def attempt_trace(context: AttemptIOContext) -> AttemptTrace:
    """Read the private trace record without adding native-only evidence to the adapter protocol."""
    trace = context.trace
    assert isinstance(trace, AttemptTrace)
    return trace


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseHead:
    """Retain the resource's final headers and their receipt clocks before native response processing."""

    http_version: str
    status_code: int
    headers: HeadersView = field(repr=False)
    received_at: float
    received_wall_time: float


def response_head(trace: AttemptTrace) -> ResponseHead | None:
    """Return the private header snapshot, when valid resource headers were observed."""
    return trace.head


def _is_phase(value: object) -> TypeIs[IOPhase]:
    return isinstance(value, str) and value in PHASES


class AttemptTrace:
    """The client's record of one attempt's evidence; arguments outside the contract mark the adapter as broken."""

    __slots__ = (
        "broken",
        "connect_failure",
        "head",
        "headers_started",
        "phase",
        "proven_not_sent",
        "response_started",
        "wire_sent",
    )

    def __init__(self) -> None:
        """Start before any I/O."""
        self.phase: IOPhase = "unknown"
        self.headers_started = False
        self.wire_sent = False
        self.response_started = False
        self.broken = False
        self.proven_not_sent = False
        self.head: ResponseHead | None = None
        self.connect_failure: ConnectFailureEvidence | None = None

    def phase_started(self, phase: object) -> None:
        """Record the phase an adapter reports."""
        if _is_phase(phase):
            self.phase = phase
        else:
            self.broken = True
            self.proven_not_sent = False

    def request_headers_started(self) -> None:
        """Record that the request headers started to go out."""
        self.headers_started = True
        self.proven_not_sent = False

    def response_headers_received(self, *, http_version: object, status_code: object, headers: object) -> None:
        """Record that the response headers arrived, checking the values an adapter reports."""
        self.response_started = True
        self.proven_not_sent = False
        if (
            not isinstance(http_version, str)
            or not http_version
            or type(status_code) is not int
            or not _MIN_STATUS <= status_code <= _MAX_STATUS
            or not isinstance(headers, HeadersView)
        ):
            self.broken = True
        elif self.head is None:
            self.head = ResponseHead(
                http_version=http_version,
                status_code=status_code,
                headers=headers,
                received_at=monotonic(),
                received_wall_time=time(),
            )

    def wire_send(self) -> None:
        """Record that request bytes reached the network."""
        self.wire_sent = True
        self.proven_not_sent = False


class TransportResponse(Protocol):
    """A response whose headers arrived and whose body is still to be read, content codings included."""

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        ...

    @property
    def headers(self) -> HeadersView:
        """Return the ordered response headers."""
        ...

    def iter_raw_bytes(self) -> Iterator[bytes]:
        """Yield the body as it arrived, before removing any content coding."""
        ...

    def close(self) -> None:
        """Release the response; closing twice does nothing."""
        ...


class AsyncTransportResponse(Protocol):
    """An async response whose headers arrived and whose body is still to be read, content codings included."""

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        ...

    @property
    def headers(self) -> HeadersView:
        """Return the ordered response headers."""
        ...

    def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        """Yield the body as it arrived, before removing any content coding."""
        ...

    async def aclose(self) -> None:
        """Release the response; closing twice does nothing."""
        ...


class TransportAdapter(Protocol):
    """Send one prepared request and return its response once the headers arrived."""

    def send(self, request: PreparedRequest[BodyAttempt], context: AttemptIOContext) -> TransportResponse:
        """Send the request; raise the client's TransportError for a classified I/O failure."""
        ...

    def close(self) -> None:
        """Release the adapter's connections."""
        ...

    @property
    def capabilities(self) -> TransportCapabilities:
        """Return what the adapter declares about retries, delivery evidence, and HTTP versions."""
        ...


class AsyncTransportAdapter(Protocol):
    """Send one prepared request with asyncio and return its response once the headers arrived."""

    async def send(
        self, request: PreparedRequest[AsyncBodyAttempt], context: AttemptIOContext
    ) -> AsyncTransportResponse:
        """Send the request; raise the client's TransportError for a classified I/O failure."""
        ...

    async def aclose(self) -> None:
        """Release the adapter's connections."""
        ...

    @property
    def capabilities(self) -> TransportCapabilities:
        """Return what the adapter declares about retries, delivery evidence, and HTTP versions."""
        ...


@dataclass(frozen=True, slots=True)
class OwnedTransportAdapter(Generic[AdapterT_co]):
    """An adapter whose ownership moves to the client, so closing the client closes it."""

    adapter: AdapterT_co
