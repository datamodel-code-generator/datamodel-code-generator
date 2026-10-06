"""Resume tokens: the small state a helper continues from, its exported JSON, and the import that checks its form."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, TypeAlias, cast, final, get_args

from ..client.errors import ProtocolError, error_choice
from ..client.responses import ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from ..model_codecs.media import json_value
from ..model_codecs.plain import JSONValue  # noqa: TC001 - Public annotations support get_type_hints().
from .records import Sealed, canonical_json, wire_string
from .references import OperationRef  # noqa: TC001 - Public annotations support get_type_hints().

__all__ = (
    "MalformedStateError",
    "ResumeState",
    "ResumeStateError",
    "import_state",
    "require_state",
    "saved_expiry",
    "state_array",
    "state_count",
    "state_expiry",
    "state_fields",
    "state_text",
)

_ResumeCondition: TypeAlias = Literal["version", "fingerprint", "expired", "malformed"]

_RESUME_CONDITIONS: Final = get_args(_ResumeCondition)
_FIELDS: Final = frozenset({"helper", "state", "version"})


class ResumeStateError(ProtocolError):
    """Resume state rejected before any send because of its version, helper, expiry, or form."""

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


@final
class ResumeState(Sealed):
    """A version 1 token a helper resumes from: the helper's identity and its small state, which export() reveals."""

    __slots__ = ("_helper", "_state_json")

    _helper: str
    _state_json: bytes

    def __init__(self, *, helper: str, state: JSONValue) -> None:
        """Keep the identity of the helper and the canonical JSON of its state."""
        wire_string(helper, "helper")
        _assign(self, helper, canonical_json(state))

    def export(self) -> bytes:
        """Return the token as canonical JSON with the members `helper`, `state`, and `version`."""
        return b"".join((b'{"helper":', canonical_json(self._helper), b',"state":', self._state_json, b',"version":1}'))

    def __repr__(self) -> str:
        """Name the token's version only, never its helper or state."""
        return "ResumeState(version=1)"


def _assign(state: ResumeState, helper: str, state_json: bytes) -> ResumeState:
    object.__setattr__(state, "_helper", helper)  # noqa: PLC2801 - Initialize the opaque immutable value.
    object.__setattr__(state, "_state_json", state_json)  # noqa: PLC2801 - Initialize the opaque immutable value.
    return state


def state_fields(state: ResumeState) -> tuple[str, bytes]:
    """Return a token's helper identity and the canonical JSON of its state."""
    return state._helper, state._state_json  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def import_state(data: bytes) -> ResumeState:
    """Rebuild an exported token, rejecting in order its form, its version, and its members."""
    if not isinstance(envelope := _envelope(data), Mapping):
        raise ResumeStateError(condition="malformed")
    if type(version := envelope.get("version")) is int and version != 1:
        raise ResumeStateError(condition="version")
    if frozenset(envelope) != _FIELDS or type(version) is not int or not isinstance(helper := envelope["helper"], str):
        raise ResumeStateError(condition="malformed")
    try:
        return _assign(object.__new__(ResumeState), wire_string(helper, "helper"), canonical_json(envelope["state"]))
    except ValueError:
        raise ResumeStateError(condition="malformed") from None


def _envelope(data: object) -> JSONValue:
    try:
        return json_value(data) if isinstance(data, bytes) else None
    except (ValueError, RecursionError):
        return None


class MalformedStateError(Exception):
    """A token whose state does not fit the helper resuming it; the helper raises it as malformed."""


def require_state(condition: bool) -> None:  # noqa: FBT001
    """Refuse a token's state that breaks a condition of its helper."""
    if not condition:
        raise MalformedStateError


def state_array(value: JSONValue) -> list[JSONValue]:
    """Return a saved array, refusing any other value."""
    require_state(isinstance(value, list))
    return cast("list[JSONValue]", value)


def state_count(value: JSONValue, limit: int | None = None) -> int:
    """Return a saved nonnegative integer no larger than any limit, refusing any other value."""
    require_state(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0 and (limit is None or value <= limit)
    )
    return cast("int", value)


def state_text(value: JSONValue) -> str | None:
    """Return a saved string or null, refusing any other value."""
    require_state(value is None or isinstance(value, str))
    return cast("str | None", value)


def saved_expiry(expires_at: datetime | None) -> JSONValue:
    """Return how a token saves a server's expiry: its ISO 8601 text, or null without one."""
    return None if expires_at is None else expires_at.isoformat()


def state_expiry(value: JSONValue) -> datetime | None:
    """Return a saved server expiry, refusing anything but null or the timezone-aware text `saved_expiry` writes."""
    if (text := state_text(value)) is None:
        return None
    try:
        expires_at = datetime.fromisoformat(text)
    except ValueError:
        raise MalformedStateError from None
    require_state(expires_at.utcoffset() is not None and expires_at.isoformat() == text)
    return expires_at
