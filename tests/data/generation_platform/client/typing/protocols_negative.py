"""Reject payload narrowing, record mutation, undeclared literals, invalid options, and request-level protocols."""

from __future__ import annotations

from pets.errors import (
    IncompleteFrameError,
    OperationFailedError,
    PaginationCycleError,
    PollWaitLimitError,
    ProtocolDataError,
    SessionLimitError,
    StreamDecodeError,
    StreamInterruptedError,
    StreamRemoteError,
    StreamResumeExhaustedError,
)
from pets.options import ClientOptions, ProtocolClientOptions, RequestOptions
from pets.protocols import (
    HeaderSelector,
    Origin,
    PaginationOptions,
    ParameterTarget,
    PollOptions,
    PollSnapshot,
    ProtocolDefaults,
    ProtocolSecurityContext,
    RequestTarget,
    ResumeState,
    Selector,
    StatusSelector,
    StreamOptions,
    WebhookOptions,
    import_state,
)
from pets_models import Pet


def wrong_records(snapshot: PollSnapshot[Pet], origin: Origin, pet: Pet) -> None:
    """Reject other payload types, mutation, undeclared literals, and positional records."""
    other: PollSnapshot[int] = snapshot  # error
    snapshot.data = pet  # error
    origin.port = 8443  # error
    HeaderSelector(name="X-Cursor", occurrence="many")  # error
    ParameterTarget(location="body", name="cursor")  # error
    StatusSelector("status")  # error
    Origin(scheme="https", host="api.example.com", port="443")  # error
    selector: Selector = ParameterTarget(location="query", name="cursor")  # error
    target: RequestTarget = HeaderSelector(name="X-Cursor")  # error
    del other, selector, target


def wrong_options(security: ProtocolSecurityContext) -> None:
    """Reject None or other types where a limit cannot be disabled, and protocol settings outside a client."""
    PaginationOptions(max_pages="2")  # error
    PaginationOptions(interval=1)  # error
    PollOptions(interval=None)  # error
    StreamOptions(reconnect=1)  # error
    ProtocolSecurityContext()  # error
    ProtocolDefaults(options=WebhookOptions())  # error
    ProtocolClientOptions(defaults={"users": PaginationOptions()})  # error
    ClientOptions(protocols=security)  # error
    RequestOptions(protocols=ProtocolClientOptions())  # error


def wrong_resume(state: ResumeState) -> None:
    """Keep resume state opaque bytes on export and bytes on import."""
    exported: str = state.export()  # error
    import_state("state")  # error
    ResumeState(helper="helper", state=object())  # error
    del exported


def wrong_errors(snapshot: PollSnapshot[Pet], failure: OperationFailedError[Pet], state: ResumeState) -> None:
    """Reject undeclared literals, fixed fields, mutation, and payloads narrowed without a decision."""
    SessionLimitError(kind="bytes", limit=1, progress={})  # error
    SessionLimitError(kind="pages", limit=1, progress={"bytes": 1})  # error
    StreamResumeExhaustedError(kind="pages", limit=1, progress={})  # error
    PaginationCycleError(page_index=1, first_seen_page_index=0, condition="inconsistent")  # error
    PaginationCycleError(page_index=1, first_seen_page_index=0, resume_state=state)  # error
    IncompleteFrameError(buffered_bytes=1, sequence=0, condition="eof")  # error
    PollWaitLimitError(kind="interval", required_wait=1, limit=0)  # error
    StreamInterruptedError(condition="closed", sequence=0, resume_state=state)  # error
    StreamDecodeError(sequence=0, raw_prefix="text", truncated=False)  # error
    ProtocolDataError(location="/cursor")  # error
    wrong: OperationFailedError[int] = failure  # error
    failure.snapshot = snapshot  # error
    limit = SessionLimitError(kind="pages", limit=1, progress={"pages": 1})
    limit.progress["pages"] = 2  # error
    del wrong


def wrong_payloads(failure: OperationFailedError, remote: StreamRemoteError) -> None:
    """Refuse model use of an unparameterized payload without an explicit decision."""
    typed: Pet = failure.snapshot.data  # error
    message: str = remote.data  # error
    del typed, message
