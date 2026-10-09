"""Report the public protocol errors of a generated package and their retained, private helper context."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final, get_origin, get_type_hints

from tests.data.python.client_runtime import record

if TYPE_CHECKING:
    from types import ModuleType

_SECRET: Final = "private-protocol-marker"
_CLASSES: Final = (
    "ProtocolDataError",
    "ProtocolStateError",
    "SessionLimitError",
    "StreamResumeExhaustedError",
    "ResumeStateError",
    "PaginationCycleError",
    "PollingStateError",
    "PollWaitLimitError",
    "OperationFailedError",
    "OperationCancelledError",
    "StreamDecodeError",
    "StreamInterruptedError",
    "IncompleteFrameError",
    "StreamRemoteError",
    "ConcurrentReceiveError",
    "WebSocketClosedError",
    "WebSocketHandshakeError",
    "WebSocketProxyError",
    "HandshakeResponse",
    "DeliveryUnknownError",
)


def protocol_errors(package: ModuleType, lines: list[str]) -> None:
    """Inspect generated public classes, their constructor contracts, and their safe representations."""
    errors, protocols, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "protocols", "responses")
    )
    secret = _SECRET
    operation = protocols.OperationRef(pointer=f"/paths/{secret}/get", document=f"https://example.com/{secret}")
    info = responses.ResponseInfo(
        status_code=200,
        headers=responses.HeadersView((("x-private", secret),)),
        call_id="call-1",
        elapsed=0.25,
        content_type="application/json",
    )
    data = {"secret": secret}
    snapshot = protocols.PollSnapshot(state=secret, terminal=True, data=data, response=info)
    selector = protocols.BodySelector(pointer=f"/{secret}")
    cases = (
        ("ProtocolDataError", {"condition": "missing", "location": selector}),
        ("ProtocolStateError", {"state": "receiving", "action": "receive"}),
        ("SessionLimitError", {"kind": "pages", "limit": 3, "progress": {"pages": 3, "items": 30}}),
        ("StreamResumeExhaustedError", {"kind": "reconnects", "limit": 5, "progress": {"reconnects": 5}}),
        ("ResumeStateError", {"condition": "expired"}),
        (
            "PaginationCycleError",
            {
                "page_index": 4,
                "first_seen_page_index": 1,
                "location": protocols.HeaderSelector(name="X-Cursor"),
            },
        ),
        ("PollingStateError", {"condition": "type", "location": selector}),
        ("PollWaitLimitError", {"kind": "wait", "required_wait": 120, "limit": 60.5}),
        ("OperationFailedError", {"snapshot": snapshot}),
        ("OperationCancelledError", {"snapshot": snapshot}),
        (
            "StreamDecodeError",
            {
                "sequence": 7,
                "raw_prefix": secret.encode(),
                "truncated": True,
                "condition": "type",
                "location": protocols.BodyTarget(pointer=f"/{secret}"),
            },
        ),
        ("StreamInterruptedError", {"condition": "transport", "sequence": 3}),
        ("IncompleteFrameError", {"buffered_bytes": 12, "sequence": 3}),
        ("StreamRemoteError", {"event_type": secret, "data": data, "sequence": 9}),
        ("ConcurrentReceiveError", {}),
        ("WebSocketClosedError", {"code": 4001, "reason": secret, "clean": False}),
        ("WebSocketHandshakeError", {"condition": "negotiation", "delivery_state": errors.DeliveryState.RESPONSE_STARTED}),
        ("WebSocketProxyError", {"proxy_status_code": 407}),
        (
            "HandshakeResponse",
            {
                "status_code": 503,
                "headers": responses.HeadersView((("retry-after", secret),)),
                "body_prefix": secret.encode(),
                "truncated": False,
            },
        ),
        ("DeliveryUnknownError", {"delivery_state": errors.DeliveryState.MAYBE_SENT, "message_id": secret}),
    )
    cause = RuntimeError(secret)
    cleanup = RuntimeError(f"{secret}-cleanup")
    for name, fields in cases:
        secondary = [cleanup]
        progress = fields.get("progress")
        error_type = getattr(errors, name)
        error = error_type(
            **fields,
            helper_id=secret,
            operation=operation,
            operation_id="protocol",
            call_id="call-1",
            parent_session_id="session-1",
            info=info,
            cause=cause,
            secondary_errors=secondary,
        )
        secondary.clear()
        retained = all(getattr(error, key) is value or getattr(error, key) == value for key, value in fields.items())
        if isinstance(progress, dict):
            progress.clear()
        hints = get_type_hints(error_type.__init__)
        text = f"{error} {error!r}"
        lines.extend((
            f"  {name}: base={error_type.__bases__[0].__name__} sdk={isinstance(error, errors.SDKError)} "
            f"reason={error.reason_code}",
            f"    chain={[item.__name__ for item in error_type.__mro__ if issubclass(item, errors.SDKError)]}",
            f"    fields={','.join(sorted(vars(error)))}",
            f"    context={error.helper_id == secret}/{error.operation is operation}/{error.info is info}/"
            f"{error.cause is cause}/{error.secondary_errors == (cleanup,)} "
            f"call={error.operation_id}/{error.call_id}/{error.parent_session_id}",
            f"    measurements={(error.attempt_count, error.elapsed, error.request_id)}",
            f"    hints={hints['operation'] == protocols.OperationRef | None}/{hints['helper_id'] == str | None}/"
            f"{hints.get('info') == responses.ResponseInfo | None} "
            f"retained={retained} progress copied={getattr(error, 'progress', {}) != {} or progress is None}",
            f"    str={error} repr={error!r} secret={secret in text} progress shown={'progress' in text}",
        ))
        if "location" in hints:
            lines.append(
                f"    location hint={hints['location'] == protocols.Selector | protocols.RequestTarget | None} "
                f"identity={error.location is fields.get('location')}"
            )
    _progress(errors, protocols, lines)
    _payloads(errors, protocols, snapshot, data, lines)
    _defaults(errors, snapshot, lines)
    _choices(errors, lines)
    _rejections(errors, protocols, responses, operation, lines)


def _progress(errors: ModuleType, protocols: ModuleType, lines: list[str]) -> None:
    """Copy progress into a read-only mapping of the declared keys."""
    source = {"pages": 1, "items": 0, "polls": 2}
    error = errors.SessionLimitError(kind="items", limit=0, progress=source)
    source["pages"] = 9
    hints = get_type_hints(errors.SessionLimitError.__init__)
    lines.append(
        f"  progress copy={dict(error.progress)} type={type(error.progress).__name__} "
        f"hint={hints['progress'] == protocols.ProtocolProgress}"
    )
    record(lines, "progress mutation", lambda: error.progress.__setitem__("pages", 2))
    every = dict.fromkeys(protocols.ProgressKey.__args__, 1)
    record(lines, "every progress key", lambda: len(errors.SessionLimitError(kind="parts", limit=1, progress=every).progress))
    record(lines, "empty progress", lambda: dict(errors.SessionLimitError(kind="polls", limit=0, progress={}).progress))


def _payloads(errors: ModuleType, protocols: ModuleType, snapshot: object, data: object, lines: list[str]) -> None:
    """Keep snapshots and error events as read-only properties, typed as object after a bare catch."""
    failed = errors.OperationFailedError(snapshot=snapshot)
    cancelled = errors.OperationCancelledError(snapshot=snapshot)
    remote = errors.StreamRemoteError(event_type=None, data=data, sequence=0)
    try:
        raise failed
    except errors.OperationFailedError as caught:
        lines.append(f"  caught snapshot identity={caught.snapshot is snapshot}/{caught.snapshot.data is data}")
    lines.append(
        f"  payload identities={cancelled.snapshot is snapshot}/{remote.data is data} event type={remote.event_type}"
    )
    for label, error, name in (
        ("failed snapshot", failed, "snapshot"),
        ("cancelled snapshot", cancelled, "snapshot"),
        ("remote data", remote, "data"),
    ):
        record(lines, f"{label} read-only", lambda error=error, name=name: setattr(error, name, None))
    for name in ("OperationFailedError", "OperationCancelledError", "StreamRemoteError"):
        error_type = getattr(errors, name)
        parameter = error_type.__parameters__[0]
        hint = get_type_hints(getattr(error_type, "snapshot" if name != "StreamRemoteError" else "data").fget)["return"]
        lines.append(
            f"  {name} covariance={parameter.__covariant__} default={getattr(parameter, '__default__', None)} "
            f"subscripted={get_origin(error_type[int]) is error_type} "
            f"property hint={get_origin(hint) is protocols.PollSnapshot if name != 'StreamRemoteError' else hint is parameter}"
        )


def _defaults(errors: ModuleType, snapshot: object, lines: list[str]) -> None:
    """Report the shared context defaults and each safe message."""
    for label, create in (
        ("data", lambda: errors.ProtocolDataError()),
        ("state", lambda: errors.ProtocolStateError(state="finished", action="wait")),
        ("session limit", lambda: errors.SessionLimitError(kind="pages", limit=0, progress={})),
        ("resume exhausted", lambda: errors.StreamResumeExhaustedError(kind="reconnects", limit=16, progress={})),
        ("resume", lambda: errors.ResumeStateError(condition="expired")),
        ("cycle", lambda: errors.PaginationCycleError(page_index=0, first_seen_page_index=0)),
        ("polling", lambda: errors.PollingStateError()),
        ("wait", lambda: errors.PollWaitLimitError(kind="deadline", required_wait=0, limit=0)),
        ("failed", lambda: errors.OperationFailedError(snapshot=snapshot)),
        ("cancelled", lambda: errors.OperationCancelledError(snapshot=snapshot)),
        ("decode", lambda: errors.StreamDecodeError(sequence=0, raw_prefix=b"", truncated=False)),
        ("interrupted", lambda: errors.StreamInterruptedError(condition="eof", sequence=0)),
        ("incomplete", lambda: errors.IncompleteFrameError(buffered_bytes=0, sequence=0)),
        ("remote", lambda: errors.StreamRemoteError(event_type="error", data=None, sequence=0)),
    ):
        error = create()
        defaults = (
            error.helper_id,
            error.operation,
            error.operation_id,
            error.call_id,
            error.parent_session_id,
            error.info,
            error.cause,
            error.secondary_errors,
            getattr(error, "location", "-"),
            error.attempt_count,
            error.request_id,
        )
        lines.append(f"  {label} defaults: {defaults!r} {error}")


def _choices(errors: ModuleType, lines: list[str]) -> None:
    """Accept every declared literal of each constructor."""
    for name, field, values, required in (
        ("ProtocolDataError", "condition", ("missing", "null", "type", "value", "malformed", "inconsistent"), {}),
        (
            "SessionLimitError",
            "kind",
            ("pages", "items", "polls", "reconnects", "parts"),
            {"limit": 1, "progress": {}},
        ),
        ("StreamResumeExhaustedError", "kind", ("reconnects",), {"limit": 1, "progress": {}}),
        ("ResumeStateError", "condition", ("expired",), {}),
        ("PollingStateError", "condition", ("type", "value"), {}),
        ("PollWaitLimitError", "kind", ("wait", "deadline"), {"required_wait": 1, "limit": 0}),
        (
            "StreamDecodeError",
            "condition",
            ("missing", "null", "type", "value", "malformed", "inconsistent"),
            {"sequence": 0, "raw_prefix": b"", "truncated": False},
        ),
        ("StreamInterruptedError", "condition", ("eof", "transport"), {"sequence": 0}),
    ):
        error_type = getattr(errors, name)
        accepted = [getattr(error_type(**{field: value}, **required), field) for value in values]
        lines.append(f"  {name} {field}: {accepted}")
    fixed = (
        errors.PaginationCycleError(page_index=1, first_seen_page_index=0).condition,
        errors.IncompleteFrameError(buffered_bytes=1, sequence=0).condition,
        errors.StreamDecodeError(sequence=0, raw_prefix=b"", truncated=False).condition,
        errors.PollingStateError().condition,
    )
    lines.append(f"  fixed and default conditions: {fixed}")


def _rejections(
    errors: ModuleType, protocols: ModuleType, responses: ModuleType, operation: object, lines: list[str]
) -> None:
    """Reject undeclared literals, wrong types, fixed fields, and positional messages without revealing values."""
    secret = _SECRET
    snapshot_info = {"status_code": 200}
    for label, create in (
        ("data condition unknown", lambda: errors.ProtocolDataError(condition="unknown")),
        ("data condition None", lambda: errors.ProtocolDataError(condition=None)),
        ("data location pointer", lambda: errors.ProtocolDataError(location=f"/{secret}")),
        ("data location operation", lambda: errors.ProtocolDataError(location=operation)),
        ("data location origin", lambda: errors.ProtocolDataError(
            location=protocols.Origin(scheme="https", host="example.com", port=443)
        )),
        ("state missing action", lambda: errors.ProtocolStateError(state="receiving")),
        ("state type", lambda: errors.ProtocolStateError(state=1, action="receive")),
        ("action type", lambda: errors.ProtocolStateError(state="receiving", action=None)),
        ("limit kind unknown", lambda: errors.SessionLimitError(kind="bytes", limit=1, progress={})),
        ("limit bool", lambda: errors.SessionLimitError(kind="pages", limit=True, progress={})),
        ("limit negative", lambda: errors.SessionLimitError(kind="pages", limit=-1, progress={})),
        ("limit float", lambda: errors.SessionLimitError(kind="pages", limit=1.0, progress={})),
        ("limit missing progress", lambda: errors.SessionLimitError(kind="pages", limit=1)),
        ("progress key", lambda: errors.SessionLimitError(kind="pages", limit=1, progress={secret: 1})),
        ("progress negative", lambda: errors.SessionLimitError(kind="pages", limit=1, progress={"pages": -1})),
        ("progress bool", lambda: errors.SessionLimitError(kind="pages", limit=1, progress={"pages": True})),
        ("progress list", lambda: errors.SessionLimitError(kind="pages", limit=1, progress=[("pages", 1)])),
        ("progress None", lambda: errors.SessionLimitError(kind="pages", limit=1, progress=None)),
        ("resume state field", lambda: errors.SessionLimitError(
            kind="pages", limit=1, progress={}, resume_state=secret.encode()
        )),
        ("exhausted kind pages", lambda: errors.StreamResumeExhaustedError(kind="pages", limit=1, progress={})),
        ("resume condition unknown", lambda: errors.ResumeStateError(condition="malformed")),
        ("resume missing condition", lambda: errors.ResumeStateError()),
        ("cycle fixed condition", lambda: errors.PaginationCycleError(
            condition="inconsistent", page_index=1, first_seen_page_index=0
        )),
        ("cycle negative", lambda: errors.PaginationCycleError(page_index=-1, first_seen_page_index=0)),
        ("cycle cursor field", lambda: errors.PaginationCycleError(
            page_index=1, first_seen_page_index=0, continuation=secret
        )),
        ("cycle resume field", lambda: errors.PaginationCycleError(
            page_index=1, first_seen_page_index=0, resume_state=secret
        )),
        ("polling condition missing", lambda: errors.PollingStateError(condition="missing")),
        ("wait kind unknown", lambda: errors.PollWaitLimitError(kind="interval", required_wait=1, limit=0)),
        ("wait nan", lambda: errors.PollWaitLimitError(kind="wait", required_wait=float("nan"), limit=0)),
        ("wait infinity", lambda: errors.PollWaitLimitError(kind="wait", required_wait=1, limit=float("inf"))),
        ("wait negative", lambda: errors.PollWaitLimitError(kind="wait", required_wait=-1, limit=0)),
        ("wait bool", lambda: errors.PollWaitLimitError(kind="wait", required_wait=True, limit=0)),
        ("wait string", lambda: errors.PollWaitLimitError(kind="wait", required_wait="1", limit=0)),
        ("failed missing snapshot", lambda: errors.OperationFailedError()),
        ("failed snapshot mapping", lambda: errors.OperationFailedError(snapshot=snapshot_info)),
        ("cancelled snapshot None", lambda: errors.OperationCancelledError(snapshot=None)),
        ("decode prefix limit", lambda: len(errors.StreamDecodeError(
            sequence=0, raw_prefix=b"x" * 65536, truncated=True
        ).raw_prefix)),
        ("decode prefix over limit", lambda: errors.StreamDecodeError(
            sequence=0, raw_prefix=b"x" * 65537, truncated=True
        )),
        ("decode prefix text", lambda: errors.StreamDecodeError(sequence=0, raw_prefix=secret, truncated=False)),
        ("decode truncated integer", lambda: errors.StreamDecodeError(sequence=0, raw_prefix=b"", truncated=1)),
        ("decode sequence negative", lambda: errors.StreamDecodeError(sequence=-1, raw_prefix=b"", truncated=False)),
        ("interrupted condition", lambda: errors.StreamInterruptedError(condition="closed", sequence=0)),
        ("interrupted sequence", lambda: errors.StreamInterruptedError(condition="eof", sequence=None)),
        ("incomplete fixed condition", lambda: errors.IncompleteFrameError(
            condition="eof", buffered_bytes=1, sequence=0
        )),
        ("incomplete buffered", lambda: errors.IncompleteFrameError(buffered_bytes=-1, sequence=0)),
        ("remote event type", lambda: errors.StreamRemoteError(event_type=1, data=None, sequence=0)),
        ("remote missing data", lambda: errors.StreamRemoteError(event_type=None, sequence=0)),
        ("remote sequence", lambda: errors.StreamRemoteError(event_type=None, data=None, sequence=1.5)),
        ("closed reason over 123 bytes", lambda: errors.WebSocketClosedError(code=1000, reason="\u00e9" * 62, clean=True)),
        ("closed code negative", lambda: errors.WebSocketClosedError(code=-1, reason="", clean=True)),
        ("closed clean integer", lambda: errors.WebSocketClosedError(code=None, reason="", clean=1)),
        ("handshake condition unknown", lambda: errors.WebSocketHandshakeError(
            condition="tls", delivery_state=errors.DeliveryState.RESPONSE_STARTED
        )),
        ("handshake operation pointer", lambda: errors.WebSocketHandshakeError(
            condition="upgrade", delivery_state=errors.DeliveryState.RESPONSE_STARTED, operation=f"/{secret}"
        )),
        ("proxy status", lambda: errors.WebSocketProxyError(proxy_status_code=99)),
        ("handshake response headers", lambda: errors.HandshakeResponse(
            status_code=503, headers={}, body_prefix=b"", truncated=False
        )),
        ("handshake response prefix", lambda: errors.HandshakeResponse(
            status_code=503, headers=responses.HeadersView(()), body_prefix=b"x" * 65537, truncated=False
        )),
        ("delivery unknown not sent", lambda: errors.DeliveryUnknownError(delivery_state=errors.DeliveryState.NOT_SENT)),
        ("context helper", lambda: errors.ProtocolStateError(state="s", action="a", helper_id=1)),
        ("context measurements", lambda: errors.ProtocolStateError(state="s", action="a", attempt_count=1)),
        ("positional message", lambda: errors.ProtocolStateError(secret, state="s", action="a")),
        ("readonly reason", lambda: setattr(errors.ResumeStateError(condition="expired"), "reason_code", "changed")),
    ):
        record(lines, label, create)
    lines.append(f"  exported={all(hasattr(errors, name) and name in errors.__all__ for name in _CLASSES)}")
