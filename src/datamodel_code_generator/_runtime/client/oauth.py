"""Send one OAuth request through a provider-owned transport and classify what the token endpoint answered.

Errors built here keep no token value, client secret, response body, or OAuth error description.
"""

from __future__ import annotations

import base64
import inspect
import json
import re
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeVar, get_args
from urllib.parse import quote_plus, urlencode, urlsplit

import httpx2
from typing_extensions import TypeIs

from ..model_codecs.unset import Unset  # noqa: TC001
from .auth import AccessToken, ApiKeyCredential, CredentialContext
from .errors import (
    OAUTH_ERROR_CODES,
    AuthError,
    ConfigurationError,
    OAuthErrorCode,
    is_auth_classified,
    is_phase_timeout,
    is_transport,
)
from .logical import Delivery
from .native import (
    async_response_bytes,
    delivery,
    io_phase,
    native_async_client,
    native_client,
    native_error,
    response_bytes,
)
from .responses import HeadersView
from .scopes import scope_tuple
from .timing import Budget, finite_number

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine, Iterator, Mapping

    from .auth import AsyncCredentialProvider, CredentialProvider
    from .hooks import IOPhase
    from .options import ResolvedTransportOptions, TimeoutOptions, TransportOptions
    from .timing import Clock

ClientAuthMethod = Literal["none", "client_secret_basic", "client_secret_post"]
Outcome = Literal["success", "rejected", "http_status", "malformed_response", "unsent", "lost"]
SecretT = TypeVar("SecretT")
T = TypeVar("T")

_LOOPBACK: Final = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_BODY: Final = 65536
_UNSAFE: Final = re.compile(r"[\x00-\x20\x7f]")
_VSCHAR: Final = re.compile(r"[\x20-\x7e]+")
_ERROR_CODE: Final = re.compile(r"[\x20\x21\x23-\x5b\x5d-\x7e]+")
_ERROR_TEXT: Final = re.compile(r"[\x20\x21\x23-\x5b\x5d-\x7e]*")
_URI_TEXT: Final = re.compile(r"(?:[A-Za-z0-9\-._~!$&'()*+,;=:@/?#\[\]]|%[0-9A-Fa-f]{2})*")
_AUTHORITY: Final = re.compile(r"(?:[^@]*@)?(?:\[[^\]]*\]|[^:@\[\]]*)(?::[0-9]*)?")
_SCOPE_ATOM: Final = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")
_BAD_REQUEST: Final = 400
_UNAUTHORIZED: Final = 401
_SUCCESS: Final = range(200, 300)
_PHASES: Final[tuple[str, ...]] = ("connect", "read", "write", "pool")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A validated OAuth endpoint URL and the canonical origin a client secret acquisition for it names."""

    url: str
    origin: str


def endpoint_url(value: object, name: str, *, allow_insecure_loopback: bool) -> Endpoint:
    """Accept an absolute HTTPS URL, or plain HTTP only to an explicitly permitted loopback host.

    The URL is parsed as requests will parse it, so its origin, port included, is known before any exchange.
    """
    from .urls import URLValidationError, canonical_origin, origin_text  # noqa: PLC0415

    if not isinstance(value, str):
        raise ConfigurationError(field_path=(name,), reason="invalid_type")
    if "#" in value or _UNSAFE.search(value):
        raise ConfigurationError(field_path=(name,), reason="invalid_url")
    try:
        urlsplit(value)
        origin = canonical_origin(value)
    except (URLValidationError, ValueError):
        raise ConfigurationError(field_path=(name,), reason="invalid_url") from None
    scheme, host, _ = origin
    if scheme == "http" and not (allow_insecure_loopback and host in _LOOPBACK):
        raise ConfigurationError(field_path=(name,), reason="insecure_url")
    return Endpoint(value, origin_text(origin))


def uri_reference(value: str) -> bool:
    """Check RFC 3986 URI-reference syntax in time linear in the value's length."""
    if not _URI_TEXT.fullmatch(value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if not parts.scheme and ":" in parts.path.partition("/")[0]:
        return False
    return (
        _AUTHORITY.fullmatch(parts.netloc) is not None
        and "#" not in parts.fragment
        and not any(bracket in f"{parts.path}{parts.query}{parts.fragment}" for bracket in "[]")
    )


def _is_method(value: str) -> TypeIs[ClientAuthMethod]:
    return value in get_args(ClientAuthMethod)


def _is_oauth_error(value: str) -> TypeIs[OAuthErrorCode]:
    return value in OAUTH_ERROR_CODES


@dataclass(frozen=True, slots=True)
class ClientAuthentication(Generic[SecretT]):
    """How a token request identifies its client: the fixed id, the method, and the secret's explicit provider."""

    client_id: str
    method: ClientAuthMethod
    secret: SecretT | None


def client_authentication(
    client_id: object, method: object, secret: object, accepts: Callable[[object], TypeIs[SecretT]]
) -> ClientAuthentication[SecretT]:
    """Validate the client identity, and that a secret provider of the flow's mode exists when the method sends one."""
    if not isinstance(client_id, str) or not _VSCHAR.fullmatch(client_id):
        raise ConfigurationError(field_path=("client_id",), reason="invalid_value")
    if not isinstance(method, str) or not _is_method(method):
        raise ConfigurationError(field_path=("client_auth_method",), reason="invalid_value")
    if method == "none":
        if secret is not None:
            raise ConfigurationError(field_path=("client_secret",), reason="forbidden_value")
        return ClientAuthentication(client_id, method, None)
    if secret is None:
        raise ConfigurationError(field_path=("client_secret",), reason="missing_value")
    if not accepts(secret):
        raise ConfigurationError(field_path=("client_secret",), reason="invalid_mode")
    return ClientAuthentication(client_id, method, secret)


def http_client_options(transport: TransportOptions, http_client: object) -> ResolvedTransportOptions:
    """Refuse unverified TLS, an unavailable HTTP/2, and transport settings beside an injected client."""
    import importlib.util  # noqa: PLC0415
    import ssl  # noqa: PLC0415

    from .options import DEFAULT_TRANSPORT, resolve_transport_options  # noqa: PLC0415

    resolved = resolve_transport_options(transport)
    if not resolved.verify:
        raise ConfigurationError(field_path=("options", "transport", "verify"), reason="insecure_transport")
    if (context := resolved.ssl_context) is not None and (
        context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname
    ):
        raise ConfigurationError(field_path=("options", "transport", "ssl_context"), reason="insecure_transport")
    if resolved.http2 and importlib.util.find_spec("h2") is None:
        raise ConfigurationError(field_path=("options", "transport", "http2"), reason="unavailable")
    if http_client is not None and resolved != DEFAULT_TRANSPORT:
        raise ConfigurationError(field_path=("options", "transport"), reason="injected_transport")
    return resolved


def _secret_context(origin: str, deadline: Budget) -> CredentialContext:
    """Describe a client secret acquisition for a token endpoint itself, never for a resource caller."""
    return CredentialContext(
        scheme="oauth_client_secret",
        required_scopes=(),
        audience=None,
        origin=origin,
        deadline=deadline,
    )


def _secret_value(value: object) -> str:
    """Accept only visible ASCII API key material as a client secret, closing a coroutine a sync provider returned."""
    if isinstance(value, ApiKeyCredential) and _VSCHAR.fullmatch(value.value):
        return value.value
    if inspect.iscoroutine(value):
        value.close()
    raise ConfigurationError(field_path=("client_secret",), reason="invalid_material")


def _provider_failure(error: Exception) -> Exception:
    """Keep classified auth failures of the secret provider and wrap any other exception it raised."""
    if is_auth_classified(error):
        return error
    return AuthError(reason="provider_failed", cause=error)


def token_request(  # noqa: PLR0913
    url: str,
    client_id: str,
    method: ClientAuthMethod,
    fields: tuple[tuple[str, str], ...],
    secret: str | None,
    *,
    timeout: httpx2.Timeout,
) -> httpx2.Request:
    """Encode the form once and authenticate the client as its method prescribes, form-encoding Basic values."""
    from .bodies import EncodedAttempt  # noqa: PLC0415

    form = list(fields)
    headers = [
        ("Accept", "application/json"),
        ("Accept-Encoding", "identity"),
        ("Content-Type", "application/x-www-form-urlencoded"),
    ]
    match method:
        case "client_secret_basic":
            assert secret is not None
            pair = f"{quote_plus(client_id, safe='')}:{quote_plus(secret, safe='')}"
            headers.append(("Authorization", f"Basic {base64.b64encode(pair.encode()).decode('ascii')}"))
        case "client_secret_post":
            assert secret is not None
            form.extend((("client_id", client_id), ("client_secret", secret)))
        case _:
            form.append(("client_id", client_id))
    body = EncodedAttempt(urlencode(form).encode("ascii"), "application/x-www-form-urlencoded")
    return httpx2.Request("POST", url, headers=headers, content=body.content, extensions={"timeout": timeout.as_dict()})


def _phase(value: float | Unset | None) -> float:
    """Return a phase cap that OAuthProviderOptions already resolved to a positive number."""
    assert isinstance(value, float)
    return value


def receipt(clock: Clock) -> tuple[datetime, float]:
    """Return the UTC wall-clock time and the monotonic time of a receipt on the clock."""
    return datetime.fromtimestamp(clock.time(), timezone.utc), clock.monotonic()


@dataclass(frozen=True, slots=True)
class Session:
    """One exchange's provider-owned budget: its absolute deadline, each phase's configured cap, and its clock."""

    deadline: Budget
    total: float
    phases: tuple[float, float, float, float]
    clock: Clock

    @classmethod
    def start(
        cls, refresh_timeout: float, phase_timeout: TimeoutOptions, clock: Clock, limit: Budget | None = None
    ) -> Session:
        """Start the budget now on the provider's clock, ending by an explicit limit moved onto that clock."""
        phases = (
            _phase(phase_timeout.connect),
            _phase(phase_timeout.read),
            _phase(phase_timeout.write),
            _phase(phase_timeout.pool),
        )
        now = clock.monotonic()
        duration = refresh_timeout if limit is None else min(refresh_timeout, limit.remaining())
        return cls(Budget(now + duration, clock), refresh_timeout, phases, clock)

    def timeout(self) -> tuple[httpx2.Timeout, tuple[bool, ...]]:
        """Clamp phases to the remaining provider deadline before request construction."""
        remaining = self.deadline.remaining()
        connect, read, write, pool = (min(value, remaining) for value in self.phases)
        return httpx2.Timeout(connect=connect, read=read, write=write, pool=pool), tuple(
            remaining <= value for value in self.phases
        )


@dataclass(frozen=True, slots=True)
class Exchanged:
    """What one token request established: the endpoint's answer, or how far the request provably got."""

    outcome: Outcome
    delivery: Delivery
    status_code: int | None = None
    fields: Mapping[str, object] | None = field(default=None, repr=False)
    oauth_error: OAuthErrorCode | None = None
    received: datetime | None = None
    receipt: float | None = None
    cause: BaseException | None = None
    phase: Literal["connect", "read", "write", "pool", "unknown"] = "unknown"
    timeout: float | None = None
    timeout_kind: Literal["phase", "provider"] | None = None


class _SessionExpiredError(Exception):
    """The session ran out while the response body was still arriving."""


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for name, value in pairs:
        if name in fields:
            msg = "The response repeats a member."
            raise ValueError(msg)
        fields[name] = value
    return fields


def _constant(value: str) -> object:
    msg = f"The response contains the non-standard number {value}."
    raise ValueError(msg)


def _is_object(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict)


def _json_value(body: bytes) -> object:
    """Parse UTF-8 JSON, replacing a parser failure with one that says where the body broke but keeps none of it."""
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except UnicodeDecodeError as error:
        failure = ValueError(f"The response is not UTF-8 at byte {error.start}.")
    except json.JSONDecodeError as error:
        failure = ValueError(f"The response is not JSON at character {error.pos}.")
    except RecursionError:
        failure = ValueError("The response nests too deeply.")
    raise failure


def _json_object(headers: HeadersView, body: bytes) -> Mapping[str, object]:
    if any(value.strip().lower() not in {"", "identity"} for value in headers.get_all("content-encoding")):
        msg = "The response carries a content coding the request did not accept."
        raise ValueError(msg)
    media, _, parameters = (headers.get("content-type") or "").partition(";")
    charset = next(
        (
            value.strip().strip('"').lower()
            for name, _, value in (item.partition("=") for item in parameters.split(";"))
            if name.strip().lower() == "charset"
        ),
        "utf-8",
    )
    if media.strip().lower() != "application/json" or charset != "utf-8":
        msg = "The response is not UTF-8 JSON."
        raise ValueError(msg)
    if not _is_object(parsed := _json_value(body)):
        msg = "The response is not a JSON object."
        raise ValueError(msg)
    return parsed


def _error_code(fields: Mapping[str, object]) -> str | None:
    """Return the code of a well-formed RFC 6749 section 5.2 error object, known or not, or None for any defect."""
    error = fields.get("error")
    if (
        not isinstance(error, str)
        or not _ERROR_CODE.fullmatch(error)
        or "access_token" in fields
        or "refresh_token" in fields
    ):
        return None
    description, uri = fields.get("error_description", ""), fields.get("error_uri", "")
    if not isinstance(description, str) or not _ERROR_TEXT.fullmatch(description):
        return None
    if not isinstance(uri, str) or not uri_reference(uri):
        return None
    return error


def _answered(status: int, headers: HeadersView, body: bytes | None, received: datetime, receipt: float) -> Exchanged:
    """Classify a complete response by status, then by its body's conformance to the success or error format.

    A well-formed error object is a rejection whatever its code; each grant decides which codes it accepts.
    """
    delivery = Delivery.RESPONSE_STARTED
    if status not in _SUCCESS and status not in {_BAD_REQUEST, _UNAUTHORIZED}:
        return Exchanged("http_status", delivery, status)
    defect: Outcome = "http_status" if status == _UNAUTHORIZED else "malformed_response"
    if body is None:
        return Exchanged(defect, delivery, status, cause=ValueError("The response exceeds its size limit."))
    try:
        fields = _json_object(headers, body)
    except ValueError as error:
        return Exchanged(defect, delivery, status, cause=error)
    if status in _SUCCESS:
        return Exchanged("success", delivery, status, fields, received=received, receipt=receipt)
    code = _error_code(fields)
    if code is None or (status == _UNAUTHORIZED and code != "invalid_client"):
        return Exchanged(defect, delivery, status)
    known = code if _is_oauth_error(code) else None
    return Exchanged("rejected", delivery, status, oauth_error=known)


def _timed_out(error: BaseException) -> bool:
    if is_phase_timeout(error):
        return True
    import httpx2  # noqa: PLC0415

    return is_transport(error) and isinstance(error.cause, httpx2.TimeoutException)


def _failed(
    error: BaseException,
    delivery: Delivery,
    session_caps: tuple[bool, ...],
    session: Session,
    status: int | None = None,
) -> Exchanged:
    """Classify a failure by its delivery and whether the session, or one phase's cap, ran out of time."""
    phase: IOPhase = io_phase(error) if is_transport(error) else "unknown"
    outcome: Outcome = "unsent" if delivery is Delivery.NOT_SENT else "lost"
    if not _timed_out(error) or (phase == "unknown" and session.deadline.remaining() > 0):
        return Exchanged(outcome, delivery, status, cause=error, phase=phase)
    index = None if phase == "unknown" else _PHASES.index(phase)
    if index is None or session_caps[index]:
        return Exchanged(
            outcome, delivery, status, cause=error, phase=phase, timeout=session.total, timeout_kind="provider"
        )
    return Exchanged(
        outcome, delivery, status, cause=error, phase=phase, timeout=session.phases[index], timeout_kind="phase"
    )


def expired(
    session: Session, delivery: Delivery, status: int | None = None, cause: BaseException | None = None
) -> Exchanged:
    """Classify the session deadline expiring by how far the exchange provably got."""
    outcome: Outcome = "unsent" if delivery is Delivery.NOT_SENT else "lost"
    return Exchanged(outcome, delivery, status, cause=cause, timeout=session.total, timeout_kind="provider")


def _read(chunks: Iterator[bytes], deadline: Budget) -> bytes | None:
    """Read at most the body limit, checking the session deadline at every chunk and once the body ends."""
    body = bytearray()
    for chunk in chunks:
        if not _taken(body, chunk, deadline):
            return None
    return _ended(body, deadline)


async def _aread(chunks: AsyncIterator[bytes], deadline: Budget) -> bytes | None:
    """Read at most the body limit as the synchronous endpoint does, checking the session deadline the same way."""
    body = bytearray()
    async for chunk in chunks:
        if not _taken(body, chunk, deadline):
            return None
    return _ended(body, deadline)


def _taken(body: bytearray, chunk: bytes, deadline: Budget) -> bool:
    """Append a chunk received within the session, and return whether the body stays within its limit."""
    if deadline.remaining() <= 0:
        raise _SessionExpiredError
    body.extend(chunk)
    return len(body) <= _MAX_BODY


def _ended(body: bytearray, deadline: Budget) -> bytes:
    """Return a body that ended within the session."""
    if deadline.remaining() <= 0:
        raise _SessionExpiredError
    return bytes(body)


class TokenEndpoint:
    """A synchronous token endpoint reached through one borrowed or lazily created HTTPX2 client."""

    __slots__ = ("_client", "_created", "_lock", "_transport", "authentication", "closed", "endpoint")

    def __init__(
        self,
        endpoint: Endpoint,
        authentication: ClientAuthentication[CredentialProvider],
        transport: TransportOptions,
        http_client: object,
    ) -> None:
        """Validate the transport settings and the client's mode without creating an HTTP client."""
        import threading  # noqa: PLC0415

        self.endpoint = endpoint
        self.authentication = authentication
        self._transport = http_client_options(transport, http_client)
        if http_client is not None and not isinstance(http_client, httpx2.Client):
            raise ConfigurationError(field_path=("http_client",), reason="invalid_mode")
        self._client: httpx2.Client | None = http_client
        self._created = False
        self.closed = False
        self._lock = threading.Lock()

    def _open(self) -> None:
        if self.closed:
            raise AuthError(reason="provider_closed")

    def prepare(self) -> None:
        """Create the SDK-owned client once, before any exchange consumes its credential, unless already closed."""
        with self._lock:
            self._open()
            if self._client is None:
                self._client = native_client(self._transport)
                self._created = True

    def exchange(self, fields: tuple[tuple[str, str], ...], session: Session) -> Exchanged:
        """Acquire the client secret, then send the form once, without redirects or retries, reading a bounded body.

        The form is never sent once the session ended or the provider closed, and a secret provider's failure
        propagates.
        """
        target = self.endpoint
        authentication = self.authentication
        secret = None
        if (provider := authentication.secret) is not None:
            try:
                secret = _secret_value(provider.get(_secret_context(target.origin, session.deadline)))
            except Exception as error:  # noqa: BLE001 - Classified by the secret provider's failure contract.
                raise _provider_failure(error) from None
        timeout, caps = session.timeout()
        request = token_request(
            target.url, authentication.client_id, authentication.method, fields, secret, timeout=timeout
        )
        with self._lock:
            self._open()
            if session.deadline.remaining() <= 0:
                return expired(session, Delivery.NOT_SENT)
            client = self._client
        assert client is not None
        try:
            response = client.send(request, stream=True, auth=None, follow_redirects=False)
        except Exception as error:  # noqa: BLE001 - Every native failure is classified by its exception type.
            return _failed(native_error(error), delivery(error, send_started=True), caps, session)
        status = response.status_code
        try:
            body = _read(response_bytes(response), session.deadline)
            received, received_at = receipt(session.clock)
        except _SessionExpiredError:
            return expired(session, Delivery.RESPONSE_STARTED, status)
        except Exception as error:  # noqa: BLE001 - A failure after the response started leaves the outcome unknown.
            return _failed(native_error(error), Delivery.RESPONSE_STARTED, caps, session, status)
        finally:
            with suppress(Exception):
                response.close()
        return _answered(status, HeadersView(response.headers.multi_items()), body, received, received_at)

    def close(self) -> None:
        """Close the client the endpoint created, once; a borrowed one stays open, and none is created afterwards."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
            client = self._client if self._created else None
        if client is not None:
            client.close()


@dataclass(slots=True)
class Progress:
    """How far an asyncio exchange got, so the session deadline cutting it short is classified by what was sent."""

    sent: bool = False
    answered: bool = False

    @property
    def delivery(self) -> Delivery:
        """Return the delivery the exchange reached."""
        if self.answered:
            return Delivery.RESPONSE_STARTED
        return Delivery.MAYBE_SENT if self.sent else Delivery.NOT_SENT


async def within(operation: Coroutine[object, object, T], seconds: float) -> T:
    """Await the operation for at most the seconds, raising TimeoutError then without swallowing a cancellation."""
    import asyncio  # noqa: PLC0415

    if sys.version_info >= (3, 11):
        async with asyncio.timeout(seconds):
            return await operation
    else:  # pragma: <3.11 cover
        try:
            return await asyncio.wait_for(operation, seconds)
        except asyncio.TimeoutError as error:
            raise TimeoutError from error


class AsyncTokenEndpoint:
    """An asyncio token endpoint reached through one borrowed or lazily created HTTPX2 client, bound to one loop."""

    __slots__ = ("_client", "_created", "_loop", "_transport", "authentication", "closed", "endpoint")

    def __init__(
        self,
        endpoint: Endpoint,
        authentication: ClientAuthentication[AsyncCredentialProvider],
        transport: TransportOptions,
        http_client: object,
    ) -> None:
        """Validate the transport settings and the client's mode without creating one, binding to a running loop."""
        import asyncio  # noqa: PLC0415

        self.endpoint = endpoint
        self.authentication = authentication
        self._transport = http_client_options(transport, http_client)
        if http_client is not None and not isinstance(http_client, httpx2.AsyncClient):
            raise ConfigurationError(field_path=("http_client",), reason="invalid_mode")
        self._client: httpx2.AsyncClient | None = http_client
        self._created = False
        self.closed = False
        self._loop: object = None
        with suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()

    def _open(self) -> None:
        if self.closed:
            raise AuthError(reason="provider_closed")

    def bind(self) -> None:
        """Refuse a caller outside asyncio or on another event loop than the one the endpoint belongs to."""
        import asyncio  # noqa: PLC0415

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as error:
            raise AuthError(reason="provider_failed", cause=error) from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise AuthError(reason="provider_failed", cause=RuntimeError("The provider belongs to another event loop"))

    def prepare(self) -> None:
        """Refuse other loops, then create the SDK-owned client once unless already closed."""
        self.bind()
        self._open()
        if self._client is None:
            self._client = native_async_client(self._transport)
            self._created = True

    async def exchange(self, fields: tuple[tuple[str, str], ...], session: Session) -> Exchanged:
        """Acquire the client secret and send the form once, both within the session deadline."""
        progress = Progress()
        try:
            return await within(self._exchange(fields, session, progress), session.deadline.remaining())
        except TimeoutError as error:
            return expired(session, progress.delivery, cause=error)

    async def _exchange(self, fields: tuple[tuple[str, str], ...], session: Session, progress: Progress) -> Exchanged:
        target = self.endpoint
        authentication = self.authentication
        secret = None
        if (provider := authentication.secret) is not None:
            try:
                secret = _secret_value(await provider.get(_secret_context(target.origin, session.deadline)))
            except Exception as error:  # noqa: BLE001 - Classified by the secret provider's failure contract.
                raise _provider_failure(error) from None
        timeout, caps = session.timeout()
        request = token_request(
            target.url, authentication.client_id, authentication.method, fields, secret, timeout=timeout
        )
        self._open()
        if session.deadline.remaining() <= 0:
            return expired(session, Delivery.NOT_SENT)
        client = self._client
        assert client is not None
        progress.sent = True
        try:
            response = await client.send(request, stream=True, auth=None, follow_redirects=False)
        except Exception as error:  # noqa: BLE001 - Every native failure is classified by its exception type.
            return _failed(native_error(error), delivery(error, send_started=True), caps, session)
        progress.answered = True
        status = response.status_code
        try:
            body = await _aread(async_response_bytes(response), session.deadline)
            received, received_at = receipt(session.clock)
        except _SessionExpiredError:
            return expired(session, Delivery.RESPONSE_STARTED, status)
        except Exception as error:  # noqa: BLE001 - A failure after the response started leaves the outcome unknown.
            return _failed(native_error(error), Delivery.RESPONSE_STARTED, caps, session, status)
        finally:
            with suppress(Exception):
                await response.aclose()
        return _answered(status, HeadersView(response.headers.multi_items()), body, received, received_at)

    async def aclose(self) -> None:
        """Close the client the endpoint created, once; a borrowed one stays open, and none is created afterwards."""
        if self.closed:
            return
        self.closed = True
        if self._created and (client := self._client) is not None:
            await client.aclose()


class InvalidTokenResponseError(ValueError):
    """A successful token response whose members cannot form usable token material."""


def _invalid(message: str) -> InvalidTokenResponseError:
    return InvalidTokenResponseError(message)


def response_scopes(value: object) -> tuple[str, ...]:
    """Split a response scope on single spaces into canonical tokens, refusing null, empty, and spacing defects."""
    if not isinstance(value, str) or not value:
        msg = "The token response scope is not a nonempty string."
        raise _invalid(msg)
    atoms = value.split(" ")
    if not all(_SCOPE_ATOM.fullmatch(atom) for atom in atoms):
        msg = "The token response scope is not space-separated scope tokens."
        raise _invalid(msg)
    return scope_tuple(atoms)


def _invalid_expiry() -> AuthError:
    return AuthError(reason="invalid_expiry")


def _expiry(value: object, received: datetime) -> datetime:
    """Map a present expires_in to an expiry at receipt; a nonpositive or invalid value is an invalid expiry."""
    if (number := finite_number(value)) is None:
        raise _invalid_expiry()
    if number <= 0:
        raise _invalid_expiry()
    try:
        return received + timedelta(seconds=number)
    except OverflowError:
        raise _invalid_expiry() from None


def token_material(
    fields: Mapping[str, object], received: datetime, snapshot: tuple[str, ...] | None
) -> tuple[AccessToken, str | None]:
    """Build the access token and the refresh token from a 2xx response.

    An omitted scope member inherits the grant's snapshot; a present one, even null, must be valid on its own.
    """
    if "error" in fields:
        msg = "The token response mixes success and error members."
        raise ValueError(msg)
    access = fields.get("access_token")
    token_type = fields.get("token_type")
    if not isinstance(access, str) or not access:
        msg = "The token response has no access token."
        raise _invalid(msg)
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        msg = "The token response does not declare the Bearer token type."
        raise _invalid(msg)
    expires_at = _expiry(fields["expires_in"], received) if "expires_in" in fields else None
    scopes = response_scopes(fields["scope"]) if "scope" in fields else snapshot
    refresh = fields.get("refresh_token")
    if "refresh_token" in fields and (not isinstance(refresh, str) or not refresh):
        msg = "The token response refresh token is not a nonempty string."
        raise _invalid(msg)
    return (
        AccessToken(access, token_type="Bearer", expires_at=expires_at, scopes=scopes),  # noqa: S106
        refresh if isinstance(refresh, str) else None,
    )
