"""Exceptions of protocol helpers, copied and exported only for packages that declare a helper.

Transport failures stay APIConnectionError or APITimeoutError, refused settings and calls ConfigurationError, and
values that do not decode as their declared model DecodeError; these classes cover what is left.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from ..client.errors import SDKError
from ..client.responses import ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from .records import ProtocolProgress, RequestTarget, Selector  # noqa: TC001 - Public annotations support get_type_hints().
from .references import OperationRef  # noqa: TC001 - Public annotations support get_type_hints().

__all__ = (
    "HELPER_ERRORS",
    "MAX_CLOSE_REASON",
    "MAX_RAW_PREFIX",
    "ProtocolDataError",
    "SessionLimitError",
    "StreamInterruptedError",
    "WebSocketClosedError",
)

MAX_RAW_PREFIX: Final = 65536
MAX_CLOSE_REASON: Final = 123


class ProtocolDataError(SDKError):
    """Received protocol data a helper cannot use, or a failure the server reports through it.

    `reason` is `missing`, `null`, `type`, `value`, `malformed`, `inconsistent` or `too_large` for data that breaks
    the helper's rules; `pagination_cycle` for a continuation already seen; `operation_failed` or
    `operation_cancelled` for a polled operation that reached that declared state; `error_event` for a declared error
    event of a stream; and `malformed_signature`, `invalid_signature`, `missing_key` or `timestamp_window` for a
    webhook that failed verification. `data` is the value the server sent with it: the final poll's data or the
    event's, decoded as declared.
    """

    reason: str

    def __init__(  # noqa: PLR0913
        self,
        *,
        reason: str = "value",
        location: Selector | RequestTarget | None = None,
        data: object = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Keep which rule the data broke, the selector or target it concerns, and the value that came with it."""
        super().__init__(reason=reason, operation_id=operation_id, info=info, cause=cause)
        self.helper_id = helper_id
        self.operation = operation
        self.location = location
        self.data = data


class SessionLimitError(SDKError):
    """A finite cap of a helper session reached while the operation goes on; partial progress is never a success.

    `reason` names the cap: `pages`, `items`, `polls`, `reconnects` or `parts`, or `wait` or `deadline` for a
    server-required wait, kept in `required_wait`, longer than the allowed wait or the remaining deadline. The handle's
    checkpoint continues it.
    """

    reason: str

    def __init__(  # noqa: PLR0913
        self,
        *,
        reason: str,
        limit: float,
        progress: ProtocolProgress | None = None,
        required_wait: float | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Keep the exhausted cap and a read-only copy of the progress."""
        super().__init__(reason=reason, operation_id=operation_id, info=info, cause=cause)
        self.helper_id = helper_id
        self.operation = operation
        self.limit = limit
        self.progress: ProtocolProgress = MappingProxyType(dict(progress or {}))
        self.required_wait = required_wait

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("limit", self.limit))


class StreamInterruptedError(SDKError):
    """A stream that ended (`eof`) or failed (`transport`) before its declared termination.

    `sequence` is the last record delivered.
    """

    reason: str

    def __init__(  # noqa: PLR0913
        self,
        *,
        reason: str,
        sequence: int,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Keep how the stream stopped and the last sequence delivered."""
        super().__init__(reason=reason, operation_id=operation_id, info=info, cause=cause)
        self.helper_id = helper_id
        self.operation = operation
        self.sequence = sequence

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("sequence", self.sequence))


class WebSocketClosedError(SDKError):
    """A WebSocket connection that closed: the close code and reason received, and whether the closure was normal.

    A normal closure ends a session's iteration; receive raises this class either way. The reason never appears in
    messages.
    """

    reason: str

    def __init__(  # noqa: PLR0913
        self,
        *,
        code: int | None,
        reason: str,
        clean: bool,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Keep the close code, the close reason, and whether the closure was normal."""
        super().__init__(reason=reason, operation_id=operation_id, info=info, cause=cause)
        self.helper_id = helper_id
        self.operation = operation
        self.code = code
        self.clean = clean

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (("operation_id", self.operation_id), ("code", self.code), ("clean", self.clean))


HELPER_ERRORS: Final = (ProtocolDataError, SessionLimitError, StreamInterruptedError, WebSocketClosedError)
