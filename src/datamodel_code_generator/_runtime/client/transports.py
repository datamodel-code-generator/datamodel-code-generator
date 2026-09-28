"""Transport adapters: how a client hands one prepared request to an HTTP implementation and reads the response.

The clients send through HTTPX2 unless a transport adapter is given; an adapter is borrowed unless it is wrapped in
`OwnedTransportAdapter`, which closing the client closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Generic, Protocol, get_args

from typing_extensions import TypeIs, TypeVar

from .bodies import AsyncBodyAttempt, BodyAttempt  # noqa: TC001 - Public annotations support get_type_hints().
from .errors import IOPhase

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from .responses import HeadersView

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


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedTimeoutOptions:
    """The effective timeout of each I/O phase in seconds; None leaves that phase unlimited."""

    connect: float | None
    read: float | None
    write: float | None
    pool: float | None


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

    __slots__ = ("_timeout", "_trace")

    def __init__(self, timeout: ResolvedTimeoutOptions, trace: AttemptTrace) -> None:
        """Bind the resolved timeouts and the attempt's trace."""
        self._timeout = timeout
        self._trace = trace

    @property
    def phase(self) -> IOPhase:
        """Return the phase the attempt reached last."""
        return self._trace.phase

    @property
    def timeout(self) -> ResolvedTimeoutOptions:
        """Return the timeout of each phase."""
        return self._timeout

    @property
    def trace(self) -> TransportTraceSink:
        """Return the sink the adapter reports its evidence to."""
        return self._trace


def _is_phase(value: object) -> TypeIs[IOPhase]:
    return value in PHASES


class AttemptTrace:
    """The client's record of one attempt's evidence; arguments outside the contract mark the adapter as broken."""

    __slots__ = ("broken", "headers_started", "phase", "response_started", "wire_sent")

    def __init__(self) -> None:
        """Start before any I/O."""
        self.phase: IOPhase = "unknown"
        self.headers_started = False
        self.wire_sent = False
        self.response_started = False
        self.broken = False

    def phase_started(self, phase: object) -> None:
        """Record the phase an adapter reports."""
        if _is_phase(phase):
            self.phase = phase
        else:
            self.broken = True

    def request_headers_started(self) -> None:
        """Record that the request headers started to go out."""
        self.headers_started = True

    def response_headers_received(self, *, http_version: object, status_code: object, headers: object) -> None:
        """Record that the response headers arrived, checking the values an adapter reports."""
        del headers
        self.response_started = True
        self.broken = self.broken or not isinstance(http_version, str) or type(status_code) is not int

    def wire_send(self) -> None:
        """Record that request bytes reached the network."""
        self.wire_sent = True


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
