"""HTTP utilities for fetching remote schema files.

Provides functions to fetch schema content from URLs and join URL references.
HTTP(S) operations require either the stable `datamodel-code-generator[http]` extra
(`httpx` with `httpcore`) or the experimental `datamodel-code-generator[httpx2]`
extra (`httpx2` with `httpcore2`). Backend selection is lazy and process-cached.
The default policy selects stable HTTPX when its client module is installed and
only tries experimental HTTPX2 when that module is absent. Explicit selections
never fall back.
Missing paired or internal dependencies are reported instead of being hidden.
Joining file:// URLs uses a dependency-free local fast path.
"""

from __future__ import annotations

import contextlib
import socket
import ssl
from _thread import allocate_lock  # noqa: PLC2701
from collections import OrderedDict
from collections.abc import Callable, Sequence  # noqa: TC003  # Runtime public annotation introspection
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address, IPv6Network, ip_address
from types import MappingProxyType
from typing import TYPE_CHECKING, Generic, Literal, TypeVar, cast
from urllib.parse import urlparse

from typing_extensions import Self

from datamodel_code_generator import SchemaFetchError
from datamodel_code_generator.enums import HTTPBackend

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from types import TracebackType
    from typing import Any, Protocol, overload

    class _ResponseHeaders(Protocol):
        @overload
        def get(self, key: str) -> str | None: ...

        @overload
        def get(self, key: str, default: str) -> str: ...

    class _HTTPResponse(Protocol):
        status_code: int
        headers: _ResponseHeaders
        text: str
        content: bytes


_HTTPTransportT_co = TypeVar("_HTTPTransportT_co", bound="_HTTPTransport", covariant=True)


if TYPE_CHECKING:

    class _HTTPTransportURL(Protocol):
        @property
        def raw_scheme(self) -> bytes: ...

        @property
        def raw_host(self) -> bytes: ...

        @property
        def port(self) -> int | None: ...

        @property
        def raw_path(self) -> bytes: ...

    class _HTTPTransportHeaders(Protocol):
        @property
        def raw(self) -> Sequence[tuple[bytes, bytes]]: ...

    # Extension payloads are dynamically typed upstream. Keep them opaque so values must be narrowed before use.
    _Extensions = Mapping[str, object]

    class _HTTPTransportRequest(Protocol):
        @property
        def method(self) -> str: ...

        @property
        def url(self) -> _HTTPTransportURL: ...

        @property
        def headers(self) -> _HTTPTransportHeaders: ...

        @property
        def stream(self) -> Iterable[bytes]: ...

        @property
        def extensions(self) -> _Extensions: ...

    class _HTTPTransport(Protocol):
        def handle_request(self, request: _HTTPTransportRequest) -> _HTTPResponse: ...

        def close(self) -> None: ...

    _SocketOption = tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]

    class _HTTPTransportFactory(Protocol[_HTTPTransportT_co]):
        def __call__(
            self,
            *,
            pinned_host: str,
            pinned_ips: tuple[IPv4Address | IPv6Address, ...],
            verify: bool,
        ) -> _HTTPTransportT_co: ...


if TYPE_CHECKING:

    class _HTTPCookies(Protocol):
        def clear(self) -> None: ...

    class _HTTPXClient(Protocol):
        cookies: _HTTPCookies

        def __enter__(self) -> Self: ...

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None: ...

        def get(
            self,
            url: str,
            *,
            headers: Sequence[tuple[str, str]] | None,
            follow_redirects: bool,
            params: Sequence[tuple[str, str]] | None,
        ) -> _HTTPResponse: ...

        def close(self) -> None: ...

    class _HTTPXClientFactory(Protocol):
        def __call__(
            self,
            *,
            transport: _HTTPTransport | None = None,
            timeout: float,
            verify: bool = True,
        ) -> _HTTPXClient: ...

    class _HTTPXURLJoiner(Protocol):
        def join(self, url: str) -> _HTTPXURLJoiner: ...

        def __str__(self) -> str: ...

    class _HTTPXURLFactory(Protocol):
        def __call__(self, url: str) -> _HTTPXURLJoiner: ...

    class _HTTPXResponseFactory(Protocol):
        def __call__(
            self,
            status_code: int,
            *,
            headers: Sequence[tuple[bytes, bytes]],
            content: bytes,
            extensions: _Extensions,
            request: _HTTPTransportRequest,
        ) -> _HTTPResponse: ...

    class _HTTPXModule(Protocol):
        BaseTransport: type[_HTTPTransport]
        Client: _HTTPXClientFactory
        Response: _HTTPXResponseFactory
        URL: _HTTPXURLFactory

        def get(  # noqa: PLR0913
            self,
            url: str,
            *,
            headers: Sequence[tuple[str, str]] | None,
            verify: bool,
            follow_redirects: bool,
            params: Sequence[tuple[str, str]] | None,
            timeout: float,
        ) -> _HTTPResponse: ...


_HTTPBackendName = Literal["httpx", "httpx2"]
_HTTPCoreBackendName = Literal["httpcore", "httpcore2"]


_NetworkStreamT_co = TypeVar("_NetworkStreamT_co", bound="_NetworkStream", covariant=True)
_NetworkStreamT = TypeVar("_NetworkStreamT", bound="_NetworkStream")


if TYPE_CHECKING:

    class _ClosableByteStream(Iterable[bytes], Protocol):
        def close(self) -> None: ...

    class _NetworkStream(Protocol):
        def read(self, max_bytes: int, timeout: float | None = None) -> bytes: ...

        def write(self, buffer: bytes, timeout: float | None = None) -> None: ...

        def close(self) -> None: ...

        def start_tls(
            self,
            ssl_context: ssl.SSLContext,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> Self: ...

    class _NetworkBackend(Protocol[_NetworkStreamT_co]):
        def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Iterable[_SocketOption] | None = None,
        ) -> _NetworkStreamT_co: ...

        def connect_unix_socket(
            self,
            path: str,
            timeout: float | None = None,
            socket_options: Iterable[_SocketOption] | None = None,
        ) -> _NetworkStreamT_co: ...

        def sleep(self, seconds: float) -> None: ...


if TYPE_CHECKING:

    class _HTTPCoreURL(Protocol):
        scheme: bytes
        host: bytes
        port: int | None
        target: bytes

    class _HTTPCoreRequest(Protocol):
        method: bytes
        url: _HTTPCoreURL
        headers: Sequence[tuple[bytes, bytes]]
        stream: Iterable[bytes]
        extensions: _Extensions

    class _HTTPCoreResponse(Protocol):
        status: int
        headers: Sequence[tuple[bytes, bytes]]
        stream: _ClosableByteStream
        extensions: _Extensions

    class _HTTPCoreURLFactory(Protocol):
        def __call__(
            self,
            *,
            scheme: bytes,
            host: bytes,
            port: int | None,
            target: bytes,
        ) -> _HTTPCoreURL: ...

    class _HTTPCoreRequestFactory(Protocol):
        def __call__(
            self,
            method: str,
            url: _HTTPCoreURL,
            *,
            headers: Sequence[tuple[bytes, bytes]],
            content: Iterable[bytes],
            extensions: _Extensions,
        ) -> _HTTPCoreRequest: ...

    class _HTTPCoreConnectionPool(Protocol):
        def handle_request(self, request: _HTTPCoreRequest) -> _HTTPCoreResponse: ...

        def close(self) -> None: ...

    class _HTTPCoreConnectionPoolFactory(Protocol):
        def __call__(
            self,
            *,
            ssl_context: ssl.SSLContext | None,
            network_backend: _NetworkBackend[_NetworkStream],
        ) -> _HTTPCoreConnectionPool: ...

    class _NetworkBackendFactory(Protocol):
        def __call__(self) -> _NetworkBackend[_NetworkStream]: ...

    class _HTTPCoreModule(Protocol):
        ConnectionPool: _HTTPCoreConnectionPoolFactory
        Request: _HTTPCoreRequestFactory
        SyncBackend: _NetworkBackendFactory
        URL: _HTTPCoreURLFactory


@dataclass(slots=True)
class _HTTPStack(Generic[_HTTPTransportT_co]):
    """One matched HTTP client/core pair selected for this process."""

    backend: _HTTPBackendName
    httpx: _HTTPXModule
    core_backend: _HTTPCoreBackendName
    _transport_type: _HTTPTransportFactory[_HTTPTransportT_co] | None = field(default=None, init=False, repr=False)

    @property
    def transport_type(self) -> _HTTPTransportFactory[_HTTPTransportT_co]:
        """Return the process-cached transport factory after importing the paired core."""
        if (transport_type := self._transport_type) is not None:
            return transport_type

        with _HTTP_CACHE_LOCK:
            if (transport_type := self._transport_type) is None:  # pragma: no branch - False only after a cache race.
                from importlib import import_module  # noqa: PLC0415

                httpcore_module = cast("_HTTPCoreModule", import_module(self.core_backend))
                transport_type = cast(
                    "_HTTPTransportFactory[_HTTPTransportT_co]",
                    _create_pinned_transport_type(self.httpx, httpcore_module),
                )
                self._transport_type = transport_type
        return transport_type


_HTTP_CACHE_LOCK = allocate_lock()
_HTTP_STACKS: dict[_HTTPBackendName, _HTTPStack[_HTTPTransport]] = {}
_AUTO_HTTP_STACK: _HTTPStack[_HTTPTransport] | None = None


DEFAULT_HTTP_TIMEOUT = 30.0
MAX_HTTP_REDIRECTS = 20
_HTTP_BACKEND_PRIORITY: tuple[_HTTPBackendName, ...] = ("httpx", "httpx2")
_HTTP_FETCH_CLIENT_CACHE_MAX_SIZE = 32
_HTTP_FETCH_DNS_CACHE_MAX_SIZE = 128
_HTTP_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_REDIRECT_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
_UNSAFE_HOST_NAMES = frozenset({"localhost"})
_IPV4_PART_COUNT = 4
_IPV4_OCTET_MAX = 0xFF
_IPV4_THREE_PART_TAIL_MAX = 0xFFFF
_IPV4_TWO_PART_TAIL_MAX = 0xFFFFFF
_IPV4_ADDRESS_MAX = 0xFFFFFFFF
_DEFAULT_PORTS = MappingProxyType({"http": 80, "https": 443})
_ADDR_INFO_MIN_LENGTH = 5
_EMBEDDED_IPV4_PREFIXES = (
    IPv6Network("::/96"),
    IPv6Network("::ffff:0:0:0/96"),
    IPv6Network("64:ff9b::/96"),
)


def _embedded_ipv4(ip: IPv4Address | IPv6Address) -> IPv4Address | None:
    """Return the IPv4 address embedded in an IPv6 address, if any.

    IPv6 can encode an IPv4 target several ways, including IPv4-mapped, IPv4-compatible,
    IPv4-translated, and NAT64 well-known prefix addresses. Unwrapping them lets the SSRF guard
    apply the same private-address checks it already applies to plain and legacy IPv4 literals.
    """
    if not isinstance(ip, IPv6Address):
        return None
    if (mapped := ip.ipv4_mapped) is not None:
        return mapped
    for network in _EMBEDDED_IPV4_PREFIXES:
        if ip in network:
            return IPv4Address(int(ip) & _IPV4_ADDRESS_MAX)
    return None


def _is_safe_ip(ip: IPv4Address | IPv6Address) -> bool:
    """Return whether an address is allowed for default remote schema fetches.

    Only globally routable addresses are accepted so untrusted schema URLs cannot reach local, private,
    link-local, reserved, or otherwise non-public networks unless the caller explicitly opts in. IPv6
    addresses that embed an IPv4 target are unwrapped so the embedded address is validated too.
    """
    if (embedded := _embedded_ipv4(ip)) is not None:
        return ip.is_global and embedded.is_global
    return ip.is_global


def _parse_legacy_ipv4_int(value: str) -> int | None:
    """Parse one component of a legacy IPv4 literal.

    Some network stacks accept decimal, octal, or hexadecimal IPv4 spellings that `ip_address()` rejects.
    Parsing them here prevents those spellings from bypassing the private-address checks.
    """
    try:
        if value.lower().startswith("0x"):
            return int(value, 0)
        if len(value) > 1 and value.startswith("0"):
            return int(value[1:], 8)
        return int(value, 10)
    except ValueError:
        return None


def _parse_legacy_ipv4_address(host: str) -> IPv4Address | None:
    """Convert one- to four-part legacy IPv4 literals into a canonical IPv4 address.

    Legacy forms such as `127.1`, `2130706433`, or `0x7f000001` can still describe loopback or private
    targets, so the SSRF guard treats them as IP literals before considering DNS.
    """
    parts = host.split(".")
    if len(parts) > _IPV4_PART_COUNT:
        return None

    numbers: list[int] = []
    for part in parts:
        if (number := _parse_legacy_ipv4_int(part)) is None:
            return None
        numbers.append(number)

    ipv4_value: int | None
    match numbers:
        case [a] if 0 <= a <= _IPV4_ADDRESS_MAX:
            ipv4_value = a
        case [a, b] if 0 <= a <= _IPV4_OCTET_MAX and 0 <= b <= _IPV4_TWO_PART_TAIL_MAX:
            ipv4_value = (a << 24) | b
        case [a, b, c] if (
            0 <= a <= _IPV4_OCTET_MAX and 0 <= b <= _IPV4_OCTET_MAX and 0 <= c <= _IPV4_THREE_PART_TAIL_MAX
        ):
            ipv4_value = (a << 24) | (b << 16) | c
        case [a, b, c, d] if all(0 <= part <= _IPV4_OCTET_MAX for part in (a, b, c, d)):
            ipv4_value = (a << 24) | (b << 16) | (c << 8) | d
        case _:
            ipv4_value = None

    return IPv4Address(ipv4_value) if ipv4_value is not None else None


def _get_ips_from_host(host: str) -> tuple[IPv4Address | IPv6Address, ...]:
    """Resolve a host to the exact address set used by the fetch guard.

    The function handles IP literals, scoped IPv6 literals, legacy IPv4 spellings, and DNS names before any
    HTTP connection is opened. The returned addresses are later pinned to prevent DNS rebinding between
    validation and the actual TCP connect.
    """
    normalized_host = host.split("%", maxsplit=1)[0]
    try:
        return (ip_address(normalized_host),)
    except ValueError:
        pass

    if (legacy_ipv4 := _parse_legacy_ipv4_address(normalized_host)) is not None:
        return (legacy_ipv4,)

    try:
        addr_infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return ()

    return _deduplicate_ips(_get_addr_info_ip(addr_info) for addr_info in addr_infos)


def _get_addr_info_ip(addr_info: object) -> IPv4Address | IPv6Address | None:
    """Extract an IP address from a resolver record.

    Malformed records are ignored because the guard should rely only on valid resolver output and then fail
    closed if no usable public address remains.
    """
    if not isinstance(addr_info, tuple) or len(addr_info) < _ADDR_INFO_MIN_LENGTH:
        return None
    sockaddr = addr_info[4]
    if not isinstance(sockaddr, tuple) or not sockaddr:
        return None
    try:
        raw_ip = str(sockaddr[0]).split("%", maxsplit=1)[0]
        return ip_address(raw_ip)
    except ValueError:
        return None


def _deduplicate_ips(ips: Iterable[IPv4Address | IPv6Address | None]) -> tuple[IPv4Address | IPv6Address, ...]:
    """Return unique IP addresses while preserving resolver order.

    Keeping the original order makes connection attempts match the validated DNS result while avoiding repeated
    attempts for duplicate records.
    """
    deduplicated: list[IPv4Address | IPv6Address] = []
    seen: set[IPv4Address | IPv6Address] = set()
    for ip in ips:
        if ip is None or ip in seen:
            continue
        seen.add(ip)
        deduplicated.append(ip)
    return tuple(deduplicated)


def _normalize_dns_host(host: bytes | str | None) -> str | None:
    """Normalize a URL or transport host to the ASCII DNS name used for pinning.

    The HTTP client core connects with an ASCII DNS name, including IDNA encoding for Unicode hostnames. Using the same
    comparison form prevents Unicode and punycode spelling differences from bypassing the pinned host check.
    """
    if host is None:
        return None
    try:
        if isinstance(host, bytes):
            host = host.decode("ascii")
        normalized_host = host.rstrip(".").lower()
        if normalized_host.isascii():
            return normalized_host
        import idna  # noqa: PLC0415

        return idna.encode(normalized_host).decode("ascii")
    except UnicodeError:
        return None


class _PinnedNetworkBackend(Generic[_NetworkStreamT]):
    """HTTP network backend that connects only to previously validated addresses.

    URL validation happens before the HTTP request, but DNS could otherwise be resolved again during the TCP
    connect. This backend closes that gap by refusing host changes and trying only the validated IP set.
    """

    def __init__(
        self,
        *,
        pinned_host: str,
        pinned_ips: tuple[IPv4Address | IPv6Address, ...],
        backend: _NetworkBackend[_NetworkStreamT],
    ) -> None:
        """Store the validated host, validated IPs, and wrapped backend.

        The host is normalized once so URL validation and the client's connection host are compared in the same
        ASCII DNS form.
        """
        self._pinned_host = _normalize_dns_host(pinned_host)
        self._pinned_ips = pinned_ips
        self._backend = backend

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> _NetworkStreamT:
        """Open a TCP connection through the pinned addresses for the validated host.

        A host mismatch raises before any DNS fallback. That keeps redirects, IDNA edge cases, or transport
        behavior from triggering a fresh lookup after validation.
        """
        if _normalize_dns_host(host) != self._pinned_host:
            msg = f"Requested DNS host {host} does not match the validated host"
            raise OSError(msg)

        last_error: Exception | None = None
        for ip in self._pinned_ips:
            try:
                return self._backend.connect_tcp(
                    str(ip),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # noqa: BLE001, PERF203
                last_error = exc
        if last_error is not None:
            raise last_error
        msg = f"No validated DNS addresses are available for {host}"
        raise OSError(msg)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> _NetworkStreamT:
        """Delegate Unix socket connections to the wrapped backend.

        DNS pinning only applies to TCP hostnames, but the HTTP core expects a complete network backend interface.
        """
        return self._backend.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    def sleep(self, seconds: float) -> None:
        """Delegate backend sleeps unchanged.

        This preserves the HTTP core's backend contract while keeping the pinning logic scoped to TCP connects.
        """
        self._backend.sleep(seconds)


def _create_ssl_context(*, verify: bool) -> ssl.SSLContext | None:
    """Create the SSL context used by the pinned transport.

    Returning `None` preserves the HTTP core's normal certificate verification. A custom context is needed only when
    the caller requested the existing `ignore_tls` behavior.
    """
    if verify:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _create_pinned_transport_type(
    httpx_module: _HTTPXModule,
    httpcore_module: _HTTPCoreModule,
) -> _HTTPTransportFactory[_HTTPTransport]:
    """Create one transport class for a matched HTTP client/core pair."""

    class _PinnedHTTPTransport(cast("Any", httpx_module.BaseTransport)):
        """Transport bound to a DNS-pinned HTTP connection pool."""

        __slots__ = ("_pool",)

        def __init__(
            self,
            *,
            pinned_host: str,
            pinned_ips: tuple[IPv4Address | IPv6Address, ...],
            verify: bool,
        ) -> None:
            """Create a pool whose TCP connections use only validated addresses."""
            network_backend = _PinnedNetworkBackend(
                pinned_host=pinned_host,
                pinned_ips=pinned_ips,
                backend=httpcore_module.SyncBackend(),
            )
            self._pool = httpcore_module.ConnectionPool(
                ssl_context=_create_ssl_context(verify=verify),
                network_backend=network_backend,
            )

        def handle_request(self, request: _HTTPTransportRequest) -> _HTTPResponse:
            """Send a client request through the matched core and eagerly consume its stream."""
            req = httpcore_module.Request(
                method=request.method,
                url=httpcore_module.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=dict(request.extensions),
            )
            resp = self._pool.handle_request(req)
            stream = resp.stream
            try:
                content = b"".join(stream)
            finally:
                stream.close()
            return httpx_module.Response(
                status_code=resp.status,
                headers=resp.headers,
                content=content,
                extensions=resp.extensions,
                request=request,
            )

        def close(self) -> None:
            """Close the pinned connection pool and release sockets."""
            self._pool.close()

    return _PinnedHTTPTransport


def _load_matched_http_stack(
    backend: _HTTPBackendName,
    core_backend: _HTTPCoreBackendName,
) -> _HTTPStack[_HTTPTransport]:
    """Import the selected client while retaining its exact paired core name."""
    from importlib import import_module  # noqa: PLC0415

    httpx_module = cast("_HTTPXModule", import_module(backend))
    return _HTTPStack(
        backend=backend,
        httpx=httpx_module,
        core_backend=core_backend,
    )


def _load_http_stack(backend: _HTTPBackendName) -> _HTTPStack[_HTTPTransport]:
    """Select one matched HTTP client/core pair without probing package metadata."""
    match backend:
        case "httpx":
            return _load_matched_http_stack(backend, "httpcore")
        case "httpx2":
            return _load_matched_http_stack(backend, "httpcore2")
        case _:
            msg = f"Unexpected HTTP backend: {backend!r}"
            raise AssertionError(msg)


def _get_or_load_http_stack(backend: _HTTPBackendName) -> _HTTPStack[_HTTPTransport]:
    """Return one backend-specific stack, importing its client at most once."""
    if (stack := _HTTP_STACKS.get(backend)) is not None:
        return stack
    with _HTTP_CACHE_LOCK:
        if (stack := _HTTP_STACKS.get(backend)) is None:  # pragma: no branch - False only after a cache race.
            stack = _load_http_stack(backend)
            _HTTP_STACKS[backend] = stack
    return stack


def _get_http_stack(http_backend: HTTPBackend = HTTPBackend.AUTO) -> _HTTPStack[_HTTPTransport]:
    """Return the lazily selected stack for an automatic or explicit policy."""
    global _AUTO_HTTP_STACK  # noqa: PLW0603

    if http_backend is HTTPBackend.AUTO:
        if (stack := _AUTO_HTTP_STACK) is not None:
            return stack
        for backend in _HTTP_BACKEND_PRIORITY:
            try:
                stack = _get_or_load_http_stack(backend)
            except ModuleNotFoundError as exc:
                match exc.name:
                    case missing_backend if missing_backend == backend:
                        continue
                    case _:
                        raise
            _AUTO_HTTP_STACK = stack
            return stack

        msg = (
            "Please run `$pip install 'datamodel-code-generator[http]'` to resolve HTTP(S) URL references, "
            "or install the experimental `datamodel-code-generator[httpx2]` extra"
        )
        raise Exception(msg) from None  # noqa: TRY002

    match http_backend:
        case HTTPBackend.HTTPX:
            backend: _HTTPBackendName = "httpx"
            extra = "http"
        case HTTPBackend.HTTPX2:
            backend = "httpx2"
            extra = "httpx2"
        case _:
            msg = f"Unexpected HTTP backend policy: {http_backend!r}"
            raise AssertionError(msg)

    try:
        return _get_or_load_http_stack(backend)
    except ModuleNotFoundError as exc:
        if exc.name != backend:
            raise
        msg = (
            f"The explicitly selected {backend} HTTP backend is not installed. "
            f"Please run `$pip install 'datamodel-code-generator[{extra}]'`."
        )
        raise ModuleNotFoundError(msg, name=backend) from None


def _get_httpx(http_backend: HTTPBackend = HTTPBackend.AUTO) -> _HTTPXModule:
    """Return the selected HTTP client module for compatibility with internal callers."""
    return _get_http_stack(http_backend).httpx


def _build_pinned_transport(
    http_stack: _HTTPStack[_HTTPTransport],
    *,
    pinned_host: str,
    pinned_ips: tuple[IPv4Address | IPv6Address, ...],
    verify: bool,
) -> _HTTPTransport:
    """Build a transport whose TCP connections use the validated address set."""
    return http_stack.transport_type(pinned_host=pinned_host, pinned_ips=pinned_ips, verify=verify)


class _HTTPFetchSession:
    """Reuse validated DNS results and HTTP connection pools for one parser run.

    The session is deliberately private and parser-scoped. Public ``get_body()``
    calls keep their request-scoped behavior, while parsers can reuse connections
    across distinct remote references without sharing network state globally.
    """

    def __init__(
        self,
        http_backend: HTTPBackend = HTTPBackend.AUTO,
        *,
        response_observer: Callable[
            [str, Sequence[tuple[str, str]] | None, Sequence[tuple[str, str]] | None, bytes], None
        ]
        | None = None,
    ) -> None:
        """Initialize empty size- and parser-lifetime-bounded caches."""
        self._http_backend = http_backend
        self._response_observer = response_observer
        self.http_stack = _get_http_stack(http_backend)
        self._dns_cache: OrderedDict[str, tuple[IPv4Address | IPv6Address, ...]] = OrderedDict()
        self._clients: OrderedDict[
            tuple[str | None, tuple[IPv4Address | IPv6Address, ...], bool, float],
            _HTTPXClient,
        ] = OrderedDict()

    def __enter__(self) -> Self:
        """Return this parser-scoped session."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close every cached connection pool."""
        self.close()

    def get_ips_from_host(self, host: str) -> tuple[IPv4Address | IPv6Address, ...]:
        """Return a successful DNS result cached for this parser run."""
        if (cached_ips := self._dns_cache.get(host)) is not None:
            self._dns_cache.move_to_end(host)
            return cached_ips
        if not (resolved_ips := _get_ips_from_host(host)):
            return resolved_ips
        self._dns_cache[host] = resolved_ips
        if len(self._dns_cache) > _HTTP_FETCH_DNS_CACHE_MAX_SIZE:
            self._dns_cache.popitem(last=False)
        return resolved_ips

    def get_response(  # noqa: PLR0913
        self,
        http_stack: _HTTPStack[_HTTPTransport],
        url: str,
        *,
        headers: Sequence[tuple[str, str]] | None,
        verify: bool,
        follow_redirects: bool,
        query_parameters: Sequence[tuple[str, str]] | None,
        timeout: float,
        pinned_host: str | None,
        pinned_ips: tuple[IPv4Address | IPv6Address, ...],
    ) -> _HTTPResponse:
        """Fetch through a connection pool isolated by DNS pin and TLS policy."""
        key = (pinned_host, pinned_ips, verify, timeout)
        if (client := self._clients.get(key)) is None:
            if pinned_host is not None and pinned_ips:
                transport = _build_pinned_transport(
                    http_stack,
                    pinned_host=pinned_host,
                    pinned_ips=pinned_ips,
                    verify=verify,
                )
                client = http_stack.httpx.Client(transport=transport, timeout=timeout)
            else:
                client = http_stack.httpx.Client(timeout=timeout, verify=verify)
            self._clients[key] = client
            if len(self._clients) > _HTTP_FETCH_CLIENT_CACHE_MAX_SIZE:
                _, evicted_client = self._clients.popitem(last=False)
                with contextlib.suppress(Exception):
                    evicted_client.close()
        else:
            self._clients.move_to_end(key)
        try:
            return client.get(
                url,
                headers=headers,
                follow_redirects=follow_redirects,
                params=query_parameters,
            )
        finally:
            cookie_cleanup_failed = True
            with contextlib.suppress(Exception):
                client.cookies.clear()
                cookie_cleanup_failed = False
            if cookie_cleanup_failed:
                if self._clients.get(key) is client:
                    del self._clients[key]
                with contextlib.suppress(Exception):
                    client.close()

    def get_body(  # noqa: PLR0913
        self,
        url: str,
        headers: Sequence[tuple[str, str]] | None = None,
        ignore_tls: bool = False,  # noqa: FBT001, FBT002
        query_parameters: Sequence[tuple[str, str]] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        *,
        allow_private_network: bool = False,
        encoding: str = "utf-8",
    ) -> str:
        """Fetch one URL while reusing this parser run's validated resources."""
        return _get_body(
            url,
            headers,
            ignore_tls,
            query_parameters,
            timeout,
            allow_private_network=allow_private_network,
            http_backend=self._http_backend,
            session=self,
            response_observer=self._response_observer,
            encoding=encoding,
        )

    def close(self) -> None:
        """Close cached clients and discard validated DNS results."""
        clients, self._clients = self._clients, OrderedDict()
        self._dns_cache.clear()
        for client in clients.values():
            with contextlib.suppress(Exception):
                client.close()


def _get_http_response(  # noqa: PLR0913
    http_stack: _HTTPStack[_HTTPTransport],
    url: str,
    *,
    headers: Sequence[tuple[str, str]] | None,
    verify: bool,
    follow_redirects: bool,
    query_parameters: Sequence[tuple[str, str]] | None,
    timeout: float,
    pinned_host: str | None,
    pinned_ips: tuple[IPv4Address | IPv6Address, ...],
) -> _HTTPResponse:
    """Fetch a URL, using DNS pinning when validation produced public addresses.

    The unpinned path preserves the selected client's behavior for trusted private-network opt-in and tests. The pinned
    path ensures the host resolved during validation is the only host used by the actual TCP connect.
    """
    if pinned_host is None or not pinned_ips:
        return http_stack.httpx.get(
            url,
            headers=headers,
            verify=verify,
            follow_redirects=follow_redirects,
            params=query_parameters,
            timeout=timeout,
        )

    transport = _build_pinned_transport(
        http_stack,
        pinned_host=pinned_host,
        pinned_ips=pinned_ips,
        verify=verify,
    )
    with http_stack.httpx.Client(transport=transport, timeout=timeout) as client:
        return client.get(
            url,
            headers=headers,
            follow_redirects=follow_redirects,
            params=query_parameters,
        )


def _format_private_network_error(
    *,
    url: str,
    hostname: str,
    reason: str,
    resolved_ips: tuple[IPv4Address | IPv6Address, ...] = (),
) -> str:
    """Build an actionable error for blocked network targets.

    The message includes both the security reason and the opt-in path so users with trusted internal schema
    endpoints can fix their invocation without guessing which option is required.
    """
    ip_details = f" Resolved IPs: {', '.join(str(ip) for ip in resolved_ips)}." if resolved_ips else ""
    return (
        f"Blocked unsafe URL host: {hostname}\n"
        f"Reason: {reason}.{ip_details}\n"
        "datamodel-code-generator blocks local, private, link-local, reserved, and otherwise non-public "
        "network targets by default to reduce SSRF risk.\n"
        f"URL: {url}\n"
        "If this is a trusted internal schema endpoint, pass --allow-private-network "
        "or set allow_private_network=True when using the Python API."
    )


def _validate_url_for_fetch(
    url: str,
    *,
    allow_private_network: bool,
    resolve_host: Callable[[str], tuple[IPv4Address | IPv6Address, ...]] = _get_ips_from_host,
) -> tuple[str, tuple[IPv4Address | IPv6Address, ...]] | None:
    """Validate a fetch URL and return DNS pinning data when pinning is required.

    Every initial URL and redirect target passes through this guard before fetching. It rejects unsupported
    schemes, invalid hosts, unresolved names, and non-public addresses unless private-network access is
    explicitly allowed.
    """
    parsed_url = urlparse(url)
    match parsed_url.scheme:
        case "http" | "https":
            pass
        case _:
            msg = f"Unsupported URL scheme for HTTP fetch: {url}"
            raise SchemaFetchError(msg)

    if (hostname := parsed_url.hostname) is None:
        msg = f"Missing URL host for HTTP fetch: {url}"
        raise SchemaFetchError(msg)

    host = _normalize_dns_host(hostname)
    if host is None:
        msg = f"Invalid URL host for HTTP fetch: {hostname}"
        raise SchemaFetchError(msg)

    if allow_private_network:
        return None

    if host in _UNSAFE_HOST_NAMES or host.endswith(".localhost"):
        msg = _format_private_network_error(
            url=url,
            hostname=hostname,
            reason="localhost names resolve to the local machine",
        )
        raise SchemaFetchError(msg)

    ips = resolve_host(host)
    if not ips:
        msg = _format_private_network_error(
            url=url,
            hostname=hostname,
            reason="the host could not be resolved to a public IP address",
        )
        raise SchemaFetchError(msg)

    if all(_is_safe_ip(ip) for ip in ips):
        return host, ips

    msg = _format_private_network_error(
        url=url,
        hostname=hostname,
        reason="the host resolves to a non-public address",
        resolved_ips=ips,
    )
    raise SchemaFetchError(msg)


def _get_redirect_url(httpx_module: _HTTPXModule, current_url: str, response: _HTTPResponse) -> str | None:
    """Return the absolute redirect target for a redirect response.

    Redirects are handled manually so each target can be revalidated and re-pinned before the next HTTP request
    instead of allowing the client to follow it automatically.
    """
    if response.status_code not in _HTTP_REDIRECT_STATUS_CODES:
        return None
    if not (location := response.headers.get("location")):
        msg = f"Redirect response from {current_url} is missing a Location header"
        raise SchemaFetchError(msg)
    return str(httpx_module.URL(current_url).join(location))


def _get_url_origin(url: str) -> tuple[str, str, int | None] | None:
    """Return the normalized URL origin used to decide whether redirect headers are scoped.

    The origin is represented as scheme, canonical DNS host, and effective port. Default ports are normalized
    so `https://example.com` and `https://example.com:443` are treated as the same scope, and IDN hosts are
    compared in the same ASCII form used by the DNS pinning guard.

    Sensitive headers are safe to keep only when the origin is unchanged. Invalid origins return `None` so
    redirect handling can strip scoped credentials instead of guessing.
    """
    parsed_url = urlparse(url)
    host = _normalize_dns_host(parsed_url.hostname)
    if host is None:
        return None
    try:
        port = parsed_url.port
    except ValueError:
        return None
    return (
        parsed_url.scheme.lower(),
        host,
        port or _DEFAULT_PORTS.get(parsed_url.scheme.lower()),
    )


def _get_redirect_headers(
    headers: Sequence[tuple[str, str]] | None,
    current_url: str,
    redirect_url: str,
) -> Sequence[tuple[str, str]] | None:
    """Drop scoped credentials when a redirect crosses origins.

    The input headers are the headers that would be sent on the next redirect hop. If there are no headers, or
    the redirect target has the same normalized origin, the existing headers are reused unchanged.

    Authorization and cookie headers are scoped to the original origin. Keeping them for a different or
    unparsable redirect target can leak credentials to an attacker-controlled host, so this function fails
    closed and removes those headers unless both origins are valid and equal.
    """
    current_origin = _get_url_origin(current_url)
    redirect_origin = _get_url_origin(redirect_url)
    if not headers or (current_origin is not None and current_origin == redirect_origin):
        return headers
    return [(name, value) for name, value in headers if name.lower() not in _SENSITIVE_REDIRECT_HEADERS]


def get_body(  # noqa: PLR0913
    url: str,
    headers: Sequence[tuple[str, str]] | None = None,
    ignore_tls: bool = False,  # noqa: FBT001, FBT002
    query_parameters: Sequence[tuple[str, str]] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    *,
    allow_private_network: bool = False,
    http_backend: HTTPBackend = HTTPBackend.AUTO,
    response_observer: Callable[[str, Sequence[tuple[str, str]] | None, Sequence[tuple[str, str]] | None, bytes], None]
    | None = None,
    encoding: str = "utf-8",
) -> str:
    """Fetch schema content from a URL with redirect validation and DNS pinning.

    The function validates the original URL and each redirect target before connecting. Public DNS results are
    pinned into the transport so a host cannot resolve to a safe address during validation and a private address
    during the actual connection.

    Redirects are followed manually rather than by the HTTP client so each hop can be revalidated and sensitive headers
    can be narrowed before the next request. Once a redirect crosses origins, scoped credentials are removed
    from `current_headers` and are not restored on later hops.

    The default ``HTTPBackend.AUTO`` policy uses stable `httpx`/`httpcore` when
    available and only tries experimental `httpx2`/`httpcore2` when the stable
    client module is absent. Explicit selections do not fall back. Client imports,
    paired-core validation, and process caches stay lazy until HTTP work needs them.
    """
    return _get_body(
        url,
        headers,
        ignore_tls,
        query_parameters,
        timeout,
        allow_private_network=allow_private_network,
        http_backend=http_backend,
        response_observer=response_observer,
        encoding=encoding,
    )


def _get_body(  # noqa: PLR0913, PLR0914
    url: str,
    headers: Sequence[tuple[str, str]] | None,
    ignore_tls: bool,  # noqa: FBT001
    query_parameters: Sequence[tuple[str, str]] | None,
    timeout: float,
    *,
    allow_private_network: bool,
    http_backend: HTTPBackend,
    session: _HTTPFetchSession | None = None,
    response_observer: Callable[[str, Sequence[tuple[str, str]] | None, Sequence[tuple[str, str]] | None, bytes], None]
    | None = None,
    encoding: str = "utf-8",
) -> str:
    """Fetch one schema body, optionally reusing parser-scoped network state."""
    http_stack = session.http_stack if session is not None else _get_http_stack(http_backend)
    # Joining URLs needs only the selected client. A fetch validates the exact paired
    # core eagerly so a broken experimental installation never falls back to stable.
    _ = http_stack.transport_type
    resolve_host = _get_ips_from_host
    get_response = _get_http_response
    if session is not None:
        resolve_host = session.get_ips_from_host
        get_response = session.get_response

    original_url = url
    original_headers = headers
    current_url = url
    current_headers = headers
    for redirect_count in range(MAX_HTTP_REDIRECTS + 1):
        validated_host = _validate_url_for_fetch(
            current_url,
            allow_private_network=allow_private_network,
            resolve_host=resolve_host,
        )
        pinned_host, pinned_ips = validated_host if validated_host is not None else (None, ())
        request_query_parameters = query_parameters if redirect_count == 0 else None
        try:
            response = get_response(
                http_stack,
                current_url,
                headers=current_headers,
                verify=not ignore_tls,
                follow_redirects=False,
                query_parameters=request_query_parameters,
                timeout=timeout,
                pinned_host=pinned_host,
                pinned_ips=pinned_ips,
            )
        except Exception as exc:
            msg = f"Failed to fetch {current_url}: {exc}"
            raise SchemaFetchError(msg) from exc
        if (redirect_url := _get_redirect_url(http_stack.httpx, current_url, response)) is None:
            break
        current_headers = _get_redirect_headers(current_headers, current_url, redirect_url)
        current_url = redirect_url
    else:
        msg = f"Too many redirects fetching {url}"
        raise SchemaFetchError(msg)

    if response.status_code >= 400:  # noqa: PLR2004
        msg = f"HTTP {response.status_code} error fetching {current_url}"
        raise SchemaFetchError(msg)
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type:
        msg = (
            f"Unexpected HTML response from {current_url} "
            f"(Content-Type: {content_type}). Expected JSON or YAML schema content."
        )
        raise SchemaFetchError(msg)
    # Decode the raw entity explicitly. HTTP client charset heuristics must not
    # change how the same locked bytes are interpreted between runs.
    body = response.content
    if not isinstance(body, bytes):  # pragma: no cover - compatibility for text-only HTTP client test doubles
        body = response.text.encode(encoding)
    if response_observer is not None:
        # Redirects are transport details. The lock identity remains the
        # caller's original logical request while the digest covers only the
        # final successful entity body.
        response_observer(original_url, original_headers, query_parameters, body)
    try:
        return body.decode(encoding)
    except UnicodeDecodeError as exc:
        msg = f"Unable to decode response from {current_url} using {encoding}: {exc}"
        raise SchemaFetchError(msg) from exc


def join_url(  # noqa: PLR0912
    url: str,
    ref: str = ".",
    *,
    http_backend: HTTPBackend = HTTPBackend.AUTO,
) -> str:
    """Join a base URL with a relative reference.

    File URLs use a dependency-free local fast path because client URL joining is
    HTTP-oriented. HTTP(S) URLs use the selected policy lazily. The default
    ``HTTPBackend.AUTO`` policy prefers stable HTTPX, while explicit selections
    require their exact backend without fallback.
    """
    if url.startswith("file://"):
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(url)

        if ref.startswith("file://"):
            return ref

        ref_path, *frag = ref.split("#", 1)

        # Fragment-only ref: keep the original path
        if not ref_path:
            joined = url.split("#", maxsplit=1)[0]
            if frag:
                joined += f"#{frag[0]}"
            return joined

        if ref_path.startswith("/"):
            joined_path = ref_path
        else:
            base_segments = parsed.path.lstrip("/").split("/")
            if base_segments and not base_segments[0]:
                base_segments = []
            if base_segments:
                base_segments = base_segments[:-1]

            min_depth = 1 if parsed.netloc else 0
            for segment in ref_path.split("/"):
                if segment in {"", "."}:
                    continue
                if segment == "..":
                    if len(base_segments) > min_depth:
                        base_segments.pop()
                    continue
                base_segments.append(segment)

            joined_path = "/" + "/".join(base_segments)
            if ref_path.endswith("/"):
                joined_path += "/"

        joined = f"file://{parsed.netloc}{joined_path}"
        if frag:
            joined += f"#{frag[0]}"
        return joined
    return str(_get_http_stack(http_backend).httpx.URL(url).join(ref))
