"""Exercise generated deadline, limiter, and error surfaces through public package modules."""

from __future__ import annotations

import importlib
from dataclasses import fields
from typing import TYPE_CHECKING, get_type_hints

import httpx2

from tests.data.python.client_runtime import Exchange, outcome, raw_response, record

if TYPE_CHECKING:
    from types import ModuleType


def _values(options: ModuleType, hooks: ModuleType, lines: list[str]) -> None:
    for label, value in (
        ("bool", True),
        ("negative", -1),
        ("nan", float("nan")),
        ("infinity", float("inf")),
        ("negative infinity", float("-inf")),
        ("text", "1"),
        ("overflow", 10**400),
    ):
        for field in ("connect", "read", "write", "pool"):
            record(
                lines,
                f"phase {field} {label}",
                lambda field=field, value=value: options.TimeoutOptions(**{field: value}),
            )
        for field in ("total_timeout", "stream_idle_timeout", "stream_total_timeout"):
            record(
                lines,
                f"option {field} {label}",
                lambda field=field, value=value: options.RequestOptions(**{field: value}),
            )
        record(lines, f"deadline {label}", lambda value=value: options.Deadline.after(value))
    for field, value in (
        ("timeout", False),
        ("deadline", 0),
        ("limiter", object()),
        ("clock", object()),
    ):
        record(lines, f"option {field} type", lambda field=field, value=value: options.ClientOptions(**{field: value}))
    record(lines, "timeout unset", options.TimeoutOptions)
    record(lines, "timeout zero", lambda: options.TimeoutOptions(connect=0, read=0.0, write=0, pool=0))
    record(lines, "timeout mixed", lambda: options.TimeoutOptions(connect=None, read=1, write=2.5))
    record(lines, "deadline constructor", options.Deadline)
    elapsed = options.Deadline.after(0)
    future = options.Deadline.after(3600)
    lines.append(
        f"  deadlines expired={elapsed.remaining()} future={0 < future.remaining() <= 3600} ordered={future.at > elapsed.at}"
    )
    deadline_at = future.at
    try:
        setattr(future, "at", deadline_at + 1)
    except (AttributeError, TypeError):
        rejected = True
    else:
        rejected = False
    lines.append(f"  deadline readonly rejected={rejected} unchanged={future.at == deadline_at}")
    options.ClientOptions(
        timeout=None,
        total_timeout=None,
        deadline=None,
        limiter=None,
        stream_idle_timeout=None,
        stream_total_timeout=None,
    )
    values = options.RequestOptions(
        total_timeout=0,
        stream_idle_timeout=1,
        stream_total_timeout=2.5,
        deadline=future,
    )
    lines.append(f"  seconds {values.total_timeout}/{values.stream_idle_timeout}/{values.stream_total_timeout}")
    context = hooks.LimiterContext(
        operation_id=None,
        origin="https://example.com",
        call_id="safe-call",
        parent_session_id=None,
        remaining_timeout=None,
    )
    lines.append(
        f"  limiter context fields={tuple(item.name for item in fields(context))} readonly={outcome(lambda: setattr(context, 'origin', 'changed'))}"
    )
    lines.append(
        f"  slotted {tuple(hasattr(value, '__dict__') for value in (future, values, context, options.TimeoutOptions()))}"
    )


def _errors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    state = errors.DeliveryState.NOT_SENT
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(
        status_code=200, headers=headers, call_id="safe-call", elapsed=0.5, content_type=None, attempt_count=3
    )
    for error in (
        errors.SDKError(attempt_count=1, elapsed=2),
        errors.APITimeoutError(reason="phase_timeout", effective_timeout=1, phase="connect", delivery_state=state),
        errors.APITimeoutError(reason="deadline_exceeded", deadline_at=-1, phase="encode", delivery_state=state),
    ):
        lines.append(
            f"  error {error} code={error.reason_code} attempts={error.attempt_count} elapsed={error.elapsed}"
            f" request={error.request_id}"
        )
        error.info = info
        error.cause = RuntimeError("private-secret")
        error.secondary_errors = (RuntimeError("private-secret"),)
        lines.append(f"  with response {error} safe={'private-secret' not in repr(error) + str(error)}")
    answered = errors.SDKError(info=info, attempt_count=9, elapsed=9)
    lines.append(f"  response measurements attempts={answered.attempt_count} elapsed={answered.elapsed}")
    phase = errors.APITimeoutError(effective_timeout=0, phase="pool", delivery_state=state)
    deadline = errors.APITimeoutError(deadline_at=-1, delivery_state=state)
    lines.append(
        f"  hierarchy phase={isinstance(phase, errors.APIConnectionError)} deadline={isinstance(deadline, errors.APIConnectionError)} effective={phase.effective_timeout} absolute={deadline.deadline_at}"
    )
    for phase_name in ("read", "write"):
        value = errors.APITimeoutError(effective_timeout=2.5, phase=phase_name, delivery_state=state)
        lines.append(f"  phase {value.phase} cap={value.effective_timeout}")
    for label, constructor, values in (
        ("attempt count", errors.SDKError, {"attempt_count": -1}),
        ("attempt bool", errors.SDKError, {"attempt_count": True}),
        ("deadline without its expiry", errors.APITimeoutError, {"reason": "deadline_exceeded"}),
    ):
        record(lines, f"invalid error {label}", lambda constructor=constructor, values=values: constructor(**values))
    for label, value in (
        ("bool", False),
        ("negative", -1),
        ("nan", float("nan")),
        ("infinity", float("inf")),
        ("text", "1"),
        ("overflow", 10**400),
    ):
        record(
            lines,
            f"invalid effective timeout {label}",
            lambda value=value: errors.APITimeoutError(effective_timeout=value, phase="read", delivery_state=state),
        )
    record(lines, "invalid deadline", lambda: errors.APITimeoutError(deadline_at=float("nan")))
    for label, value in (("negative", -1), ("infinity", float("inf"))):
        record(lines, f"invalid elapsed {label}", lambda value=value: errors.SDKError(elapsed=value))


def _hints(options: ModuleType, errors: ModuleType, hooks: ModuleType, lines: list[str]) -> None:
    for owner in (
        options.TimeoutOptions,
        options.ClientOptions,
        options.RequestOptions,
        options.Deadline,
        options.Clock,
        hooks.LimiterContext,
        errors.SDKError,
        errors.APIConnectionError,
        errors.APITimeoutError,
    ):
        lines.append(f"  hints {owner.__name__} {tuple(get_type_hints(owner.__init__))}")
    for method in (
        options.Deadline.after,
        options.Deadline.remaining,
        options.Deadline.at.fget,
        options.Deadline.clock.fget,
        hooks.Limiter.acquire,
        hooks.AsyncLimiter.acquire,
        hooks.Permit.release,
        hooks.AsyncPermit.release,
    ):
        lines.append(f"  hints {method.__qualname__} {tuple(get_type_hints(method))}")


def _live_calls(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)

    def inspect_timeout(request: httpx2.Request) -> None:
        lines.append(f"  native timeout {request.extensions['timeout']}")

    with exchange.client(event_hooks={"request": [inspect_timeout]}) as native:
        with package.Client(http_client=native) as api:
            exchange.respond(raw_response(200, b"default"))
            record(
                lines, "default total and phases", lambda: api.request_raw("GET", "https://example.com/default").read()
            )
        client = options.ClientOptions(timeout=options.TimeoutOptions(connect=3, read=5), total_timeout=None)
        with package.Client(http_client=native, options=client) as api:
            view = api.with_options(options.RequestOptions(timeout=options.TimeoutOptions(write=7)))
            exchange.respond(raw_response(200, b"layered"))
            call = options.RequestOptions(timeout=options.TimeoutOptions(pool=2))
            record(
                lines,
                "nested timeout merge",
                lambda: view.request_raw("GET", "https://example.com/layered", options=call).read(),
            )
            cleared = view.with_options(options.RequestOptions(timeout=None))
            exchange.respond(raw_response(200, b"cleared"))
            call = options.RequestOptions(timeout=options.TimeoutOptions(read=8), deadline=None)
            record(
                lines,
                "nested timeout clear",
                lambda: cleared.request_raw("GET", "https://example.com/cleared", options=call).read(),
            )


class _Refusing(httpx2.BaseTransport):
    """Record the phase timeouts each attempt is given, then refuse the connection."""

    def __init__(self) -> None:
        self.timeouts: list[dict[str, float | None]] = []

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        self.timeouts.append(request.extensions["timeout"])
        msg = "refused"
        raise httpx2.ConnectError(msg, request=request)


def _attempt_timeout(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Hand each attempt the time left until the absolute deadline as every unlimited phase's timeout."""
    refusing = _Refusing()
    settings = options.ClientOptions(
        total_timeout=None,
        deadline=options.Deadline.after(3600),
        timeout=options.TimeoutOptions(connect=None, read=None, write=None, pool=None),
        retry=options.RetryOptions(max_retries=1, initial_delay=0, jitter="none"),
    )
    with httpx2.Client(transport=refusing) as native, package.Client(http_client=native, options=settings) as api:
        record(lines, "refused attempts", lambda: api.request_raw("GET", "https://example.com/failure"))
    first, second = refusing.timeouts
    lines.append(
        f"  attempts={len(refusing.timeouts)} phases share the remaining time={len({*first.values()}) == 1}"
        f" within the deadline={0 < first['read'] <= 3600} shrinking={second['read'] <= first['read']}"
    )


def _clocks(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Keep deadlines on the clock they were made on, and move a deadline from another clock onto a call's clock."""
    for name in ("monotonic", "time", "random"):
        record(lines, f"clock {name} type", lambda name=name: options.Clock(**{name: 1.0}))
    record(lines, "deadline clock type", lambda: options.Deadline.after(1, clock=object()))
    fake = options.Clock(monotonic=lambda: 100.0)
    deadline = options.Deadline.after(5, clock=fake)
    lines.append(
        f"  fake clock deadline at={deadline.at} remaining={deadline.remaining()} clock={deadline.clock is fake}"
        f" system={options.Deadline.after(1).clock == options.Clock()}"
    )

    class Unhashable:
        __hash__ = None

        def __call__(self) -> float:
            return 100.0

    odd = options.Clock(monotonic=Unhashable())
    held = options.RequestOptions(deadline=options.Deadline.after(5, clock=odd))
    lines.append(f"  hashing ignores sources clock={hash(odd) == hash(fake)} request={hash(held) == hash(held)}")
    refusing = _Refusing()
    system = options.Deadline.after(3600)
    settings = options.ClientOptions(
        total_timeout=None,
        clock=fake,
        timeout=options.TimeoutOptions(connect=None, read=None, write=None, pool=None),
        retry=options.RetryOptions(max_retries=0),
    )
    with httpx2.Client(transport=refusing) as native, package.Client(http_client=native, options=settings) as api:
        for label, given in (("same clock", deadline), ("system clock", system)):
            record(
                lines,
                f"deadline on the {label}",
                lambda given=given: api.request_raw(
                    "GET", "https://example.com/clock", options=options.RequestOptions(deadline=given)
                ),
            )
    same, moved = refusing.timeouts
    lines.append(
        f"  kept={same} moved onto the call's clock by its remaining time={system.remaining() <= moved['read'] <= 3600}"
    )


def deadline_options(package: ModuleType, lines: list[str]) -> None:
    """Report public option validation, error shape, annotations, and timeout inheritance over real TLS."""
    options, errors, hooks, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "errors", "hooks", "responses")
    )
    _values(options, hooks, lines)
    _errors(errors, responses, lines)
    _hints(options, errors, hooks, lines)
    _live_calls(package, options, lines)
    _attempt_timeout(package, options, lines)
    _clocks(package, options, lines)
