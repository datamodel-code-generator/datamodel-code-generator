"""Send one OAuth request through a provider-owned transport and classify what the token or device endpoint answered.

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
from time import monotonic
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeVar, get_args
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit

from typing_extensions import TypeIs

from ..model_codecs.unset import Unset
from .auth import AccessToken, ApiKeyCredential, CredentialContext
from .errors import (
    MAX_STATUS,
    MIN_STATUS,
    OAUTH_ERROR_CODES,
    AdapterContractError,
    AuthConfigurationError,
    AuthProviderClosedError,
    AuthProviderExecutionError,
    AuthRefreshError,
    DeliveryState,
    OAuthErrorCode,
    PhaseTimeoutError,
    TokenExpiredError,
    TransportError,
    UnsupportedAsyncBackendError,
)
from .scopes import scope_tuple
from .timing import ResolvedTimeoutOptions, absolute_deadline, finite_number
from .transports import (
    AsyncTransportAdapter,
    AttemptIOContext,
    AttemptTrace,
    OwnedTransportAdapter,
    PreparedRequest,
    TransportAdapter,
    TransportCapabilities,
    is_adapter,
    is_async_adapter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator, Mapping

    from .auth import AsyncCredentialProvider, CredentialProvider
    from .bodies import EncodedAttempt
    from .options import ResolvedTransportOptions, TimeoutOptions, TransportOptions
    from .responses import HeadersView
    from .timing import Deadline

ClientAuthMethod = Literal["none", "client_secret_basic", "client_secret_post"]
Outcome = Literal["success", "rejected", "http_status", "malformed_response", "unsent", "lost"]
SecretT = TypeVar("SecretT")
AdapterT = TypeVar("AdapterT", bound="TransportAdapter | AsyncTransportAdapter")
T = TypeVar("T")

_LOOPBACK: Final = frozenset({"localhost", "127.0.0.1", "::1"})
_AUTHORIZATION_PARAMETERS: Final = frozenset({
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "code_challenge",
    "code_challenge_method",
})
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
_DEFAULT_INTERVAL: Final = 5.0
DEVICE_GRANT_TYPE: Final = "urn:ietf:params:oauth:grant-type:device_code"


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
        raise AuthConfigurationError(field_path=(name,), condition="invalid_type")
    if "#" in value or _UNSAFE.search(value):
        raise AuthConfigurationError(field_path=(name,), condition="invalid_url")
    try:
        urlsplit(value)
        origin = canonical_origin(value)
    except (URLValidationError, ValueError):
        raise AuthConfigurationError(field_path=(name,), condition="invalid_url") from None
    scheme, host, _ = origin
    if scheme == "http" and not (allow_insecure_loopback and host in _LOOPBACK):
        raise AuthConfigurationError(field_path=(name,), condition="insecure_url")
    return Endpoint(value, origin_text(origin))


def authorization_url(value: object, *, allow_insecure_loopback: bool) -> str:
    """Accept an authorization endpoint whose own query leaves every authorization request parameter to the flow."""
    url = endpoint_url(value, "authorization_url", allow_insecure_loopback=allow_insecure_loopback).url
    if any(name in _AUTHORIZATION_PARAMETERS for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
        raise AuthConfigurationError(field_path=("authorization_url",), condition="reserved_parameter")
    return url


def redirect_uri(value: object) -> str:
    """Accept an absolute redirect URI without a fragment, as RFC 6749 requires of a redirection endpoint."""
    if not isinstance(value, str):
        raise AuthConfigurationError(field_path=("redirect_uri",), condition="invalid_type")
    if not uri_reference(value, absolute=True):
        raise AuthConfigurationError(field_path=("redirect_uri",), condition="invalid_url")
    return value


def request_url(base: str, query: list[tuple[str, str]]) -> str:
    """Append parameters to a URL, keeping the query it already has."""
    separator = "" if base.endswith(("?", "&")) else "&" if urlsplit(base).query else "?"
    return f"{base}{separator}{urlencode(query)}"


def uri_reference(value: str, *, absolute: bool = False) -> bool:
    """Check RFC 3986 URI-reference syntax, or absolute-URI syntax, in time linear in the value's length."""
    if not _URI_TEXT.fullmatch(value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if absolute and (not parts.scheme or "#" in value):
        return False
    if not parts.scheme and ":" in parts.path.partition("/")[0]:
        return False
    return (
        _AUTHORITY.fullmatch(parts.netloc) is not None
        and "#" not in parts.fragment
        and not any(bracket in f"{parts.path}{parts.query}{parts.fragment}" for bracket in "[]")
    )


def authorization_code(value: object) -> str:
    """Accept an authorization code of visible ASCII characters, as RFC 6749 defines it."""
    if not isinstance(value, str) or not _VSCHAR.fullmatch(value):
        raise AuthConfigurationError(field_path=("code",), condition="invalid_value")
    return value


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
        raise AuthConfigurationError(field_path=("client_id",), condition="invalid_value")
    if not isinstance(method, str) or not _is_method(method):
        raise AuthConfigurationError(field_path=("client_auth_method",), condition="invalid_value")
    if method == "none":
        if secret is not None:
            raise AuthConfigurationError(field_path=("client_secret",), condition="forbidden_value")
        return ClientAuthentication(client_id, method, None)
    if secret is None:
        raise AuthConfigurationError(field_path=("client_secret",), condition="missing_value")
    if not accepts(secret):
        raise AuthConfigurationError(field_path=("client_secret",), condition="invalid_mode")
    return ClientAuthentication(client_id, method, secret)


def token_transport_options(transport: TransportOptions, token_transport: object) -> ResolvedTransportOptions:
    """Refuse unverified TLS, transport-owned retries, an unavailable HTTP/2, and settings beside an injection."""
    import importlib.util  # noqa: PLC0415
    import ssl  # noqa: PLC0415

    from .options import DEFAULT_TRANSPORT, resolve_transport_options  # noqa: PLC0415

    resolved = resolve_transport_options(transport)
    if not resolved.verify:
        raise AuthConfigurationError(field_path=("options", "transport", "verify"), condition="insecure_transport")
    if (context := resolved.ssl_context) is not None and (
        context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname
    ):
        raise AuthConfigurationError(field_path=("options", "transport", "ssl_context"), condition="insecure_transport")
    if resolved.retry_owner != "sdk":
        raise AuthConfigurationError(field_path=("options", "transport", "retry_owner"), condition="transport_retries")
    if resolved.http2 and importlib.util.find_spec("h2") is None:
        raise AuthConfigurationError(field_path=("options", "transport", "http2"), condition="unavailable")
    if not isinstance(token_transport, Unset) and resolved != DEFAULT_TRANSPORT:
        raise AuthConfigurationError(field_path=("options", "transport"), condition="injected_transport")
    return resolved


def injected_adapter(
    token_transport: AdapterT | OwnedTransportAdapter[AdapterT] | Unset, accepts: Callable[[object], TypeIs[AdapterT]]
) -> tuple[AdapterT | None, bool]:
    """Return an injected token adapter of the flow's mode and whether the provider owns it, requiring no retries."""
    adapter, owned = (
        (token_transport.adapter, True)
        if isinstance(token_transport, OwnedTransportAdapter)
        else (token_transport, False)
    )
    if isinstance(adapter, Unset):
        return None, True
    if not accepts(adapter):
        raise AuthConfigurationError(field_path=("token_transport",), condition="invalid_mode")
    capabilities = getattr(adapter, "capabilities", None)
    if not isinstance(capabilities, TransportCapabilities):
        raise AuthConfigurationError(field_path=("token_transport",), condition="invalid_capabilities")
    if capabilities.internal_retry_limit != 0:
        raise AuthConfigurationError(field_path=("token_transport",), condition="transport_retries")
    return adapter, owned


def _secret_context(origin: str, deadline: Deadline) -> CredentialContext:
    """Describe a client secret acquisition for a token endpoint itself, never for a resource caller."""
    return CredentialContext(
        scheme="oauth_client_secret",
        required_scopes=(),
        audience=None,
        origin=origin,
        deadline=deadline,
        cancel_token=None,
    )


def _secret_value(value: object) -> str:
    """Accept only visible ASCII API key material as a client secret, closing a coroutine a sync provider returned."""
    if isinstance(value, ApiKeyCredential) and _VSCHAR.fullmatch(value.value):
        return value.value
    if inspect.iscoroutine(value):
        value.close()
    raise AuthConfigurationError(field_path=("client_secret",), condition="invalid_material")


def _provider_failure(error: Exception, state: str) -> Exception:
    """Keep classified auth failures of the secret provider and wrap any other exception it raised."""
    if isinstance(error, (AuthConfigurationError, AuthRefreshError)):
        return error
    return AuthProviderExecutionError(callback="get", state=state, delivery_state=DeliveryState.NOT_SENT, cause=error)


def token_request(
    url: str, client_id: str, method: ClientAuthMethod, fields: tuple[tuple[str, str], ...], secret: str | None
) -> PreparedRequest[EncodedAttempt]:
    """Encode the form once and authenticate the client as its method prescribes, form-encoding Basic values."""
    from .bodies import EncodedAttempt  # noqa: PLC0415
    from .responses import HeadersView  # noqa: PLC0415

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
    return PreparedRequest(method="POST", url=url, headers=HeadersView(headers), body=body)


def _phase(value: float | Unset | None) -> float:
    """Return a phase cap that OAuthProviderOptions already resolved to a positive number."""
    assert isinstance(value, float)
    return value


@dataclass(frozen=True, slots=True)
class Session:
    """One exchange's provider-owned budget: its absolute deadline and each phase's configured cap."""

    deadline: Deadline
    total: float
    phases: tuple[float, float, float, float]
    limited: bool = False

    @classmethod
    def start(cls, refresh_timeout: float, phase_timeout: TimeoutOptions, limit: Deadline | None = None) -> Session:
        """Start the budget now, independently of any resource caller's deadline, ending by an explicit limit."""
        phases = (
            _phase(phase_timeout.connect),
            _phase(phase_timeout.read),
            _phase(phase_timeout.write),
            _phase(phase_timeout.pool),
        )
        if limit is not None and limit.at < monotonic() + refresh_timeout:
            return cls(limit, refresh_timeout, phases, limited=True)
        return cls(absolute_deadline(monotonic() + refresh_timeout), refresh_timeout, phases)

    def context(self, trace: AttemptTrace) -> tuple[AttemptIOContext, tuple[bool, ...]]:
        """Clamp each phase to the remaining session and record which caps the session selected."""
        remaining = self.deadline.remaining()
        connect, read, write, pool = (min(value, remaining) for value in self.phases)
        timeout = ResolvedTimeoutOptions(connect=connect, read=read, write=write, pool=pool)
        return AttemptIOContext(timeout, trace, deadline=self.deadline), tuple(
            remaining <= value for value in self.phases
        )


@dataclass(slots=True)
class Progress:
    """How far an exchange got, kept by its caller so an interruption is classified by what was sent."""

    sent: bool = False
    answered: bool = False

    @property
    def delivery(self) -> DeliveryState:
        """Return the delivery the exchange provably reached."""
        if self.answered:
            return DeliveryState.RESPONSE_STARTED
        return DeliveryState.MAYBE_SENT if self.sent else DeliveryState.NOT_SENT


@dataclass(frozen=True, slots=True)
class Exchanged:
    """What one token request established: the endpoint's answer, or how far the request provably got."""

    outcome: Outcome
    delivery: DeliveryState
    status_code: int | None = None
    fields: Mapping[str, object] | None = field(default=None, repr=False)
    oauth_error: OAuthErrorCode | None = None
    error: str | None = None
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
    delivery = DeliveryState.RESPONSE_STARTED
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
    return Exchanged("rejected", delivery, status, oauth_error=known, error=code, receipt=receipt)


def _head(status: object, headers: object) -> tuple[int, HeadersView]:
    """Return a response's status and headers, refusing values outside the adapter contract."""
    from .responses import HeadersView  # noqa: PLC0415

    if type(status) is not int or not MIN_STATUS <= status <= MAX_STATUS or not isinstance(headers, HeadersView):
        raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
    return status, headers


def _claimed(error: BaseException, trace: AttemptTrace, *, trusted: bool) -> DeliveryState:
    """Return how far a failed send provably got: an adapter without delivery evidence cannot prove NOT_SENT."""
    if trace.response_started:
        return DeliveryState.RESPONSE_STARTED
    if isinstance(error, TransportError) and (trusted or error.delivery_state is not DeliveryState.NOT_SENT):
        return error.delivery_state
    return DeliveryState.NOT_SENT if trace.proven_not_sent else DeliveryState.MAYBE_SENT


def _timed_out(error: BaseException) -> bool:
    if isinstance(error, PhaseTimeoutError):
        return True
    import httpx2  # noqa: PLC0415

    return isinstance(error, TransportError) and isinstance(error.cause, httpx2.TimeoutException)


def _failed(
    error: BaseException,
    delivery: DeliveryState,
    session_caps: tuple[bool, ...],
    session: Session,
    status: int | None = None,
) -> Exchanged:
    """Classify a failure by its delivery and whether the session, or one phase's cap, ran out of time."""
    phase = error.phase if isinstance(error, TransportError) else "unknown"
    outcome: Outcome = "unsent" if delivery is DeliveryState.NOT_SENT else "lost"
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
    session: Session, delivery: DeliveryState, status: int | None = None, cause: BaseException | None = None
) -> Exchanged:
    """Classify the session deadline expiring by how far the exchange provably got."""
    outcome: Outcome = "unsent" if delivery is DeliveryState.NOT_SENT else "lost"
    return Exchanged(outcome, delivery, status, cause=cause, timeout=session.total, timeout_kind="provider")


def _quiet(close: Callable[[], None]) -> None:
    with suppress(Exception):
        close()


async def _aquiet(close: Callable[[], Awaitable[None]]) -> None:
    with suppress(Exception):
        await close()


def _read(chunks: Iterator[bytes], deadline: Deadline) -> bytes | None:
    """Read at most the body limit, checking the session deadline at every chunk and once the body ends."""
    body = bytearray()
    for chunk in chunks:
        if not _taken(body, chunk, deadline):
            return None
    return _ended(body, deadline)


async def _aread(chunks: AsyncIterator[bytes], deadline: Deadline) -> bytes | None:
    """Read at most the body limit as the synchronous endpoint does, checking the session deadline the same way."""
    body = bytearray()
    async for chunk in chunks:
        if not _taken(body, chunk, deadline):
            return None
    return _ended(body, deadline)


def _taken(body: bytearray, chunk: bytes, deadline: Deadline) -> bool:
    """Append a chunk received within the session, and return whether the body stays within its limit."""
    if deadline.remaining() <= 0:
        raise _SessionExpiredError
    body.extend(chunk)
    return len(body) <= _MAX_BODY


def _ended(body: bytearray, deadline: Deadline) -> bytes:
    """Return a body that ended within the session."""
    if deadline.remaining() <= 0:
        raise _SessionExpiredError
    return bytes(body)


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


class TokenEndpoint:
    """A synchronous token endpoint reached through one lazily created or injected transport."""

    __slots__ = ("_adapter", "_lock", "_owned", "_transport", "authentication", "closed", "endpoint")

    def __init__(
        self,
        endpoint: Endpoint,
        authentication: ClientAuthentication[CredentialProvider],
        transport: TransportOptions,
        token_transport: TransportAdapter | OwnedTransportAdapter[TransportAdapter] | Unset,
    ) -> None:
        """Validate the transport settings without creating an HTTP client."""
        import threading  # noqa: PLC0415

        self.endpoint = endpoint
        self.authentication = authentication
        self._transport = token_transport_options(transport, token_transport)
        self._adapter, self._owned = injected_adapter(token_transport, is_adapter)
        self.closed = False
        self._lock = threading.Lock()

    def _open(self) -> None:
        if self.closed:
            raise AuthProviderClosedError(state="CLOSED", delivery_state=DeliveryState.NOT_SENT)

    def prepare(self) -> None:
        """Create the SDK-owned transport once, before any exchange consumes its credential, unless already closed."""
        with self._lock:
            self._open()
            if self._adapter is None:
                from .native import Httpx2Transport, native_client  # noqa: PLC0415

                self._adapter = Httpx2Transport(
                    native_client(self._transport), trusted_default=True, http2=self._transport.http2
                )

    def exchange(
        self,
        fields: tuple[tuple[str, str], ...],
        session: Session,
        progress: Progress,
        state: str,
        endpoint: Endpoint | None = None,
    ) -> Exchanged:
        """Acquire the client secret, then send the form once, without redirects or retries, reading a bounded body.

        The form goes to the token endpoint unless another endpoint of the grant is given. A secret provider's
        failure propagates; `state` names the state it leaves the caller in.
        """
        target = endpoint or self.endpoint
        authentication = self.authentication
        secret = None
        if (provider := authentication.secret) is not None:
            try:
                secret = _secret_value(provider.get(_secret_context(target.origin, session.deadline)))
            except Exception as error:  # noqa: BLE001 - Classified by the secret provider's failure contract.
                raise _provider_failure(error, state) from None
            if session.deadline.remaining() <= 0:
                return expired(session, DeliveryState.NOT_SENT)
        request = token_request(target.url, authentication.client_id, authentication.method, fields, secret)
        trace = AttemptTrace()
        context, caps = session.context(trace)
        with self._lock:
            self._open()
            adapter = self._adapter
            progress.sent = True
        assert adapter is not None
        try:
            response = adapter.send(request, context)
        except Exception as error:  # noqa: BLE001 - Every adapter failure is classified by its evidence.
            return _failed(error, _claimed(error, trace, trusted=adapter.capabilities.delivery_evidence), caps, session)
        progress.answered = True
        status = None
        try:
            status, headers = _head(response.status_code, response.headers)
            body = _read(response.iter_raw_bytes(), session.deadline)
            received, receipt = datetime.now(timezone.utc), monotonic()
        except _SessionExpiredError:
            return expired(session, DeliveryState.RESPONSE_STARTED, status)
        except Exception as error:  # noqa: BLE001 - A failure after the response started leaves the outcome unknown.
            return _failed(error, DeliveryState.RESPONSE_STARTED, caps, session, status)
        finally:
            _quiet(response.close)
        return _answered(status, headers, body, received, receipt)

    def close(self) -> None:
        """Close an owned transport once; a borrowed one stays open, and no transport is created afterwards."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
            adapter = self._adapter
        if self._owned and adapter is not None:
            adapter.close()


class AsyncTokenEndpoint:
    """An asyncio token endpoint reached through one lazily created or injected transport, bound to one loop."""

    __slots__ = ("_adapter", "_loop", "_owned", "_transport", "authentication", "closed", "endpoint")

    def __init__(
        self,
        endpoint: Endpoint,
        authentication: ClientAuthentication[AsyncCredentialProvider],
        transport: TransportOptions,
        token_transport: AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter] | Unset,
    ) -> None:
        """Validate the transport settings without creating an HTTP client, binding to a loop already running."""
        import asyncio  # noqa: PLC0415

        self.endpoint = endpoint
        self.authentication = authentication
        self._transport = token_transport_options(transport, token_transport)
        self._adapter, self._owned = injected_adapter(token_transport, is_async_adapter)
        self.closed = False
        self._loop: object = None
        with suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()

    def _open(self) -> None:
        if self.closed:
            raise AuthProviderClosedError(state="CLOSED", delivery_state=DeliveryState.NOT_SENT)

    def bind(self) -> None:
        """Refuse a caller outside asyncio or on another event loop than the one the endpoint belongs to."""
        import asyncio  # noqa: PLC0415

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise UnsupportedAsyncBackendError from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise UnsupportedAsyncBackendError(loop_mismatch=True)

    def prepare(self) -> None:
        """Refuse other loops and backends, then create the SDK-owned transport once unless already closed."""
        self.bind()
        self._open()
        if self._adapter is None:
            from .native import AsyncHttpx2Transport, native_async_client  # noqa: PLC0415

            self._adapter = AsyncHttpx2Transport(
                native_async_client(self._transport), trusted_default=True, http2=self._transport.http2
            )

    async def exchange(
        self,
        fields: tuple[tuple[str, str], ...],
        session: Session,
        progress: Progress,
        state: str,
        endpoint: Endpoint | None = None,
    ) -> Exchanged:
        """Acquire the client secret and send the form once, both within the session deadline."""
        try:
            return await within(
                self._exchange(fields, session, progress, state, endpoint or self.endpoint),
                session.deadline.remaining(),
            )
        except TimeoutError as error:
            return expired(session, progress.delivery, cause=error)

    async def _exchange(
        self, fields: tuple[tuple[str, str], ...], session: Session, progress: Progress, state: str, target: Endpoint
    ) -> Exchanged:
        authentication = self.authentication
        secret = None
        if (provider := authentication.secret) is not None:
            try:
                secret = _secret_value(await provider.get(_secret_context(target.origin, session.deadline)))
            except Exception as error:  # noqa: BLE001 - Classified by the secret provider's failure contract.
                raise _provider_failure(error, state) from None
            if session.deadline.remaining() <= 0:
                return expired(session, DeliveryState.NOT_SENT)
        request = token_request(target.url, authentication.client_id, authentication.method, fields, secret)
        trace = AttemptTrace()
        context, caps = session.context(trace)
        self._open()
        adapter = self._adapter
        assert adapter is not None
        progress.sent = True
        try:
            response = await adapter.send(request, context)
        except Exception as error:  # noqa: BLE001 - Every adapter failure is classified by its evidence.
            return _failed(error, _claimed(error, trace, trusted=adapter.capabilities.delivery_evidence), caps, session)
        progress.answered = True
        status = None
        try:
            status, headers = _head(response.status_code, response.headers)
            body = await _aread(response.iter_raw_bytes(), session.deadline)
            received, receipt = datetime.now(timezone.utc), monotonic()
        except _SessionExpiredError:
            return expired(session, DeliveryState.RESPONSE_STARTED, status)
        except Exception as error:  # noqa: BLE001 - A failure after the response started leaves the outcome unknown.
            return _failed(error, DeliveryState.RESPONSE_STARTED, caps, session, status)
        finally:
            await _aquiet(response.aclose)
        return _answered(status, headers, body, received, receipt)

    async def aclose(self) -> None:
        """Close an owned transport once; a borrowed one stays open, and no transport is created afterwards."""
        if self.closed:
            return
        self.closed = True
        if self._owned and (adapter := self._adapter) is not None:
            await adapter.aclose()


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


def _invalid_expiry() -> TokenExpiredError:
    return TokenExpiredError(condition="invalid_expiry", delivery_state=DeliveryState.RESPONSE_STARTED)


def _expiry(value: object, received: datetime) -> datetime:
    """Map a present expires_in to an expiry at receipt; a nonpositive or invalid value is a TokenExpiredError."""
    if (number := finite_number(value)) is None:
        raise _invalid_expiry()
    if number <= 0:
        raise TokenExpiredError(condition="nonpositive_expiry", delivery_state=DeliveryState.RESPONSE_STARTED)
    try:
        return received + timedelta(seconds=number)
    except OverflowError:
        raise _invalid_expiry() from None


def token_material(
    fields: Mapping[str, object], received: datetime, snapshot: tuple[str, ...] | None
) -> tuple[AccessToken, str | None, bool]:
    """Build the access token, the refresh token, and whether the response carried one, from a 2xx response.

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
        "refresh_token" in fields,
    )


def failure_kind(error: BaseException) -> Literal["malformed_response", "invalid_token_response"]:
    """Separate a successful response's member defects, expiry included, from its format defects."""
    if isinstance(error, (InvalidTokenResponseError, TokenExpiredError)):
        return "invalid_token_response"
    return "malformed_response"


class InvalidDeviceResponseError(ValueError):
    """A device authorization response whose members cannot start a device transaction."""


class DeviceAuthorizationEndedError(Exception):
    """The authorization server ended a device transaction: the user denied it, or its device code expired."""

    def __init__(self, reason: str) -> None:
        """Keep the server's error code, one of the two RFC 8628 codes that end a transaction."""
        super().__init__(f"The authorization server ended the device authorization with {reason}.")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DeviceGrant:
    """A validated device authorization response: the codes, where the user goes, and the transaction's timing."""

    device_code: str = field(repr=False)
    user_code: str = field(repr=False)
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: float
    interval: float


def _device_invalid(name: str) -> InvalidDeviceResponseError:
    return InvalidDeviceResponseError(f"The device authorization response has no valid {name}.")


def _device_code(fields: Mapping[str, object], name: str) -> str:
    """Return a code the device sends or shows, which RFC 8628 leaves to visible ASCII characters in practice."""
    value = fields.get(name)
    if not isinstance(value, str) or not _VSCHAR.fullmatch(value):
        raise _device_invalid(name)
    return value


def _device_uri(fields: Mapping[str, object], name: str) -> str:
    value = fields.get(name)
    if not isinstance(value, str) or not uri_reference(value, absolute=True):
        raise _device_invalid(name)
    return value


def _device_seconds(value: object, name: str) -> float:
    if (number := finite_number(value)) is None or number <= 0:
        raise _device_invalid(name)
    return number


def device_grant(fields: Mapping[str, object]) -> DeviceGrant:
    """Validate every RFC 8628 section 3.2 member before a transaction may start, ignoring unknown members.

    expires_in is required with no fallback lifetime; only an omitted interval takes the five-second default.
    """
    if "error" in fields:
        msg = "The device authorization response mixes success and error members."
        raise ValueError(msg)
    return DeviceGrant(
        device_code=_device_code(fields, "device_code"),
        user_code=_device_code(fields, "user_code"),
        verification_uri=_device_uri(fields, "verification_uri"),
        verification_uri_complete=(
            _device_uri(fields, "verification_uri_complete") if "verification_uri_complete" in fields else None
        ),
        expires_in=_device_seconds(fields.get("expires_in"), "expires_in"),
        interval=_device_seconds(fields["interval"], "interval") if "interval" in fields else _DEFAULT_INTERVAL,
    )
