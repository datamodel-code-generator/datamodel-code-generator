"""Opaque resume state, its exported JSON envelope, and the explicit import that validates it before any send."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from time import time
from typing import Final, Literal, TypeAlias, cast, final, get_args

from ..client.errors import ProtocolConfigurationError, ProtocolError, error_choice, error_count
from ..client.responses import ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json, encode_json
from ..model_codecs.wire import WireValue  # noqa: TC001 - Public annotations support get_type_hints().
from .records import Sealed, canonical_json, record_instance, wire_string
from .references import OperationRef  # noqa: TC001 - Public annotations support get_type_hints().

__all__ = (
    "MalformedStateError",
    "ResumeState",
    "ResumeStateError",
    "ResumeStateTooLargeError",
    "helper_state",
    "import_state",
    "require_state",
    "server_expiry",
    "state_array",
    "state_count",
    "state_fields",
    "state_text",
)

_ResumeCondition: TypeAlias = Literal["version", "fingerprint", "security", "expired", "malformed", "checksum", "size"]

MAX_STATE_BYTES: Final = 16 * 1024 * 1024
_RESUME_CONDITIONS: Final = get_args(_ResumeCondition)
_FIELDS: Final = frozenset({
    "expires_at",
    "helper_fingerprint",
    "payload",
    "security_fingerprint",
    "sha256",
    "state",
    "version",
})
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_RFC3339: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class ResumeStateError(ProtocolError):
    """Resume state rejected before any send because of its version, fingerprints, expiry, form, checksum, or size."""

    condition: _ResumeCondition

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: _ResumeCondition,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep only the rejection category, never the state's contents."""
        error_choice(condition, _RESUME_CONDITIONS, "condition")
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.condition = condition

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class ResumeStateTooLargeError(ResumeStateError):
    """Resume state larger than its limit; its condition is always size."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        limit: int,
        observed_bytes: int,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the limit and the observed size."""
        error_count(limit, "limit")
        error_count(observed_bytes, "observed_bytes")
        super().__init__(
            condition="size",
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.limit = limit
        self.observed_bytes = observed_bytes

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("limit", self.limit), ("observed_bytes", self.observed_bytes))


def _expiry(value: object) -> None:
    record_instance(value, (datetime, type(None)), "expires_at must be a datetime or None")
    if isinstance(value, datetime) and value.utcoffset() is None:
        msg = "expires_at must be timezone-aware"
        raise ValueError(msg)


@final
class ResumeState(Sealed):
    """Opaque version 1 state that a helper resumes from; only export() reveals its contents."""

    __slots__ = (
        "_expires_at",
        "_exportable",
        "_helper_fingerprint",
        "_payload",
        "_security_fingerprint",
        "_state_json",
    )

    _helper_fingerprint: str
    _security_fingerprint: str
    _state_json: bytes
    _payload: bytes
    _expires_at: datetime | None
    _exportable: bool

    def __init__(
        self,
        *,
        helper_fingerprint: str,
        security_fingerprint: str,
        state: WireValue,
        payload: bytes = b"",
        expires_at: datetime | None = None,
    ) -> None:
        """Keep the fingerprints, the canonical JSON of the protocol state, unconsumed bytes, and an aware expiry."""
        wire_string(helper_fingerprint, "helper_fingerprint")
        wire_string(security_fingerprint, "security_fingerprint")
        record_instance(payload, bytes, "payload must be bytes")
        _expiry(expires_at)
        _assign(
            self,
            helper_fingerprint=helper_fingerprint,
            security_fingerprint=security_fingerprint,
            state_json=canonical_json(state),
            payload=payload,
            expires_at=expires_at,
        )

    def export(self) -> bytes:
        """Return the JSON envelope; its SHA-256 detects corruption only and is not a signature.

        Raises ResumeStateTooLargeError when the envelope exceeds the 16 MiB that import_state accepts, and
        ProtocolConfigurationError for the state of a call that may authenticate without a credential partition.
        """
        if not self._exportable:
            raise ProtocolConfigurationError(field_path=("protocols", "security"), condition="security_partition")
        head, tail = _members(
            self._expires_at, self._helper_fingerprint, self._payload, self._security_fingerprint, self._state_json
        )
        envelope = b"".join((b"{", head, b',"sha256":"', _checksum(head, tail).encode(), b'",', tail))
        if len(envelope) > MAX_STATE_BYTES:
            raise ResumeStateTooLargeError(limit=MAX_STATE_BYTES, observed_bytes=len(envelope))
        return envelope

    def __repr__(self) -> str:
        """Name the envelope version only, never fingerprints, state, or payload."""
        return "ResumeState(version=1)"


def _assign(  # noqa: PLR0913
    state: ResumeState,
    *,
    helper_fingerprint: str,
    security_fingerprint: str,
    state_json: bytes,
    payload: bytes,
    expires_at: datetime | None,
    exportable: bool = True,
) -> None:
    for name, value in (
        ("_helper_fingerprint", helper_fingerprint),
        ("_security_fingerprint", security_fingerprint),
        ("_state_json", state_json),
        ("_payload", payload),
        ("_expires_at", expires_at),
        ("_exportable", exportable),
    ):
        object.__setattr__(state, name, value)  # noqa: PLC2801 - Initialize the opaque immutable value.


def helper_state(  # noqa: PLR0913
    *,
    helper_fingerprint: str,
    security_fingerprint: str,
    state: WireValue,
    payload: bytes,
    exportable: bool,
    expires_at: datetime | None = None,
) -> ResumeState:
    """Return the state a helper saved, which exports only when `exportable` and expires at a server's expiry."""
    saved = object.__new__(ResumeState)
    _assign(
        saved,
        helper_fingerprint=helper_fingerprint,
        security_fingerprint=security_fingerprint,
        state_json=canonical_json(state),
        payload=payload,
        expires_at=expires_at,
        exportable=exportable,
    )
    return saved


def state_fields(state: ResumeState) -> tuple[str, str, bytes, bytes, datetime | None]:
    """Return a state's helper and security fingerprints, the canonical JSON of its state, its payload, and expiry."""
    return (
        state._helper_fingerprint,  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        state._security_fingerprint,  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        state._state_json,  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        state._payload,  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        state._expires_at,  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    )


def server_expiry(value: str) -> datetime | None:
    """Return the UTC time an RFC 3339 date-time with an offset or an HTTP date gives, or None for another value."""
    try:
        if _RFC3339.fullmatch(text := value.upper()) is not None:
            return datetime.fromisoformat(text).astimezone(timezone.utc)
        from ..client.retry import http_date  # noqa: PLC0415 - Only a helper with an expiry parses HTTP dates.

        return http_date(value, time())
    except (ValueError, OverflowError):
        return None


class MalformedStateError(Exception):
    """A checkpoint whose state or saved bodies do not fit the helper resuming it; the helper raises it as malformed."""


def require_state(condition: bool) -> None:  # noqa: FBT001
    """Refuse a checkpoint's state that breaks a condition of its helper."""
    if not condition:
        raise MalformedStateError


def state_array(value: WireValue) -> tuple[WireValue, ...]:
    """Return a saved array, refusing any other value."""
    require_state(isinstance(value, tuple))
    return cast("tuple[WireValue, ...]", value)


def state_count(value: WireValue, limit: int | None = None) -> int:
    """Return a saved nonnegative integer no larger than any limit, refusing any other value."""
    require_state(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0 and (limit is None or value <= limit)
    )
    return cast("int", value)


def state_text(value: WireValue) -> str | None:
    """Return a saved string or null, refusing any other value."""
    require_state(value is None or isinstance(value, str))
    return cast("str | None", value)


def _members(
    expires_at: datetime | None, helper_fingerprint: str, payload: bytes, security_fingerprint: str, state_json: bytes
) -> tuple[bytes, bytes]:
    """Return the envelope members before and from `state`, in canonical JSON with names in sorted order."""
    from base64 import b64encode  # noqa: PLC0415

    head = b",".join(
        encode_json(name) + b":" + encode_json(value)
        for name, value in (
            ("expires_at", None if expires_at is None else expires_at.isoformat()),
            ("helper_fingerprint", helper_fingerprint),
            ("payload", b64encode(payload).decode("ascii")),
            ("security_fingerprint", security_fingerprint),
        )
    )
    return head, b'"state":' + state_json + b',"version":1}'


def _checksum(head: bytes, tail: bytes) -> str:
    """Return the SHA-256 of the canonical JSON of every envelope member except `sha256`."""
    from hashlib import sha256  # noqa: PLC0415

    return sha256(b"{" + head + b"," + tail).hexdigest()


def import_state(data: bytes) -> ResumeState:
    """Rebuild exported state, rejecting in order its size, form, version, fields, checksum, and a passed expiry."""
    if (size := _size(data)) > MAX_STATE_BYTES:
        raise ResumeStateTooLargeError(limit=MAX_STATE_BYTES, observed_bytes=size)
    if not isinstance(envelope := _envelope(data), Mapping):
        raise ResumeStateError(condition="malformed")
    if type(version := envelope.get("version")) is int and version != 1:
        raise ResumeStateError(condition="version")
    if (fields := _fields(envelope)) is None:
        raise ResumeStateError(condition="malformed")
    expires_at, helper, payload, security, state_json, digest = fields
    if _checksum(*_members(expires_at, helper, payload, security, state_json)) != digest:
        raise ResumeStateError(condition="checksum")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise ResumeStateError(condition="expired")
    state = object.__new__(ResumeState)
    _assign(
        state,
        helper_fingerprint=helper,
        security_fingerprint=security,
        state_json=state_json,
        payload=payload,
        expires_at=expires_at,
    )
    return state


def _size(data: object) -> int:
    return len(data) if isinstance(data, bytes) else 0


def _envelope(data: object) -> WireValue:
    try:
        return decode_json(data) if isinstance(data, bytes) else None
    except CodecError:
        return None


def _fields(envelope: Mapping[str, WireValue]) -> tuple[datetime | None, str, bytes, str, bytes, str] | None:
    from base64 import b64decode, b64encode  # noqa: PLC0415

    if frozenset(envelope) != _FIELDS:
        return None
    version, helper, security, payload, expires, digest = (
        envelope[name]
        for name in ("version", "helper_fingerprint", "security_fingerprint", "payload", "expires_at", "sha256")
    )
    if not (
        version == 1
        and type(version) is int
        and isinstance(helper, str)
        and isinstance(security, str)
        and isinstance(payload, str)
        and (expires is None or isinstance(expires, str))
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest)
    ):
        return None
    try:
        expires_at = None if expires is None else datetime.fromisoformat(expires)
        decoded = b64decode(payload, validate=True)
        state_json = canonical_json(envelope["state"])
    except ValueError:
        return None
    canonical = b64encode(decoded).decode("ascii") == payload and (
        expires_at is None or (expires_at.utcoffset() is not None and expires_at.isoformat() == expires)
    )
    return (expires_at, helper, decoded, security, state_json, digest) if canonical else None
