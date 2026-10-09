"""Checkpoints: the plain JSON state a helper continues from, the checks of its form, and its expiry error."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast

from ..client.errors import ProtocolError, error_choice
from ..client.responses import ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from .references import OperationRef  # noqa: TC001 - Public annotations support get_type_hints().

if TYPE_CHECKING:
    from ..model_codecs.media import JSONValue

__all__ = (
    "MalformedStateError",
    "ResumeStateError",
    "require_state",
    "saved_expiry",
    "state_array",
    "state_count",
    "state_expiry",
    "state_text",
)


class ResumeStateError(ProtocolError):
    """A checkpoint past the expiry its server declared, rejected before any send; its condition is always expired."""

    condition: Literal["expired"]

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: Literal["expired"],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep only the rejection category, never the state's contents."""
        error_choice(condition, ("expired",), "condition")
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.condition = condition

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class MalformedStateError(Exception):
    """A checkpoint whose state does not fit the helper resuming it; the helper refuses it as an invalid value."""


def require_state(condition: bool) -> None:  # noqa: FBT001
    """Refuse a checkpoint's state that breaks a condition of its helper."""
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
    """Return how a checkpoint keeps a server's expiry: its ISO 8601 text, or null without one."""
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
