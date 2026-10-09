"""Reject payload narrowing, record mutation, undeclared literals, invalid options, and request-level protocols."""

from __future__ import annotations

from pets import Client
from pets.errors import ProtocolDataError, SessionLimitError, StreamInterruptedError
from pets.options import ProtocolClientOptions, RequestOptions
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
    Selector,
    StatusSelector,
    StreamOptions,
    WebhookOptions,
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
    Client(protocols=security)  # error
    RequestOptions(protocols=ProtocolClientOptions())  # error


def wrong_errors(failure: ProtocolDataError) -> None:
    """Reject missing and wrongly typed fields, mutation, and payloads narrowed without a decision."""
    SessionLimitError(limit=1, progress={})  # error
    SessionLimitError(reason="pages", limit=1, progress={"bytes": 1})  # error
    StreamInterruptedError(reason="eof")  # error
    ProtocolDataError(location="/cursor")  # error
    limit = SessionLimitError(reason="pages", limit=1, progress={"pages": 1})
    limit.progress["pages"] = 2  # error
    typed: Pet = failure.data  # error
    del typed
