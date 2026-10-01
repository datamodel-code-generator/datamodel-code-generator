"""Exercise generated deadline, cancellation, limiter, and error surfaces through public package modules."""

from __future__ import annotations

import importlib
import threading
from dataclasses import fields
from typing import TYPE_CHECKING, get_type_hints

import httpx2

from tests.data.python.client_runtime import Exchange, outcome, raw_response, record

if TYPE_CHECKING:
    from types import ModuleType


_COUNTERS = (
    "resource_attempt_count",
    "redirect_count",
    "auth_exchange_count",
    "network_send_count",
    "network_send_budget_used",
    "auth_exchange_budget_used",
    "auth_refresh_ids",
    "auth_refresh_pending",
    "wire_send_count",
)


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
        ("cancel_token", object()),
        ("limiter", object()),
        ("max_network_sends", True),
        ("max_network_sends", -1),
        ("max_network_sends", 0.5),
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
    token = options.CancelToken()
    lines.append(f"  cancellation initial={token.cancelled}")
    worker = threading.Thread(target=token.cancel)
    worker.start()
    worker.join()
    token.cancel()
    lines.append(
        f"  cancellation after-thread={token.cancelled} readonly={outcome(lambda: setattr(token, 'cancelled', False))}"
    )
    options.ClientOptions(
        timeout=None,
        total_timeout=None,
        deadline=None,
        cancel_token=None,
        limiter=None,
        max_network_sends=None,
        stream_idle_timeout=None,
        stream_total_timeout=None,
    )
    values = options.RequestOptions(
        total_timeout=0,
        stream_idle_timeout=1,
        stream_total_timeout=2.5,
        max_network_sends=0,
        deadline=future,
        cancel_token=token,
    )
    lines.append(
        f"  seconds {values.total_timeout}/{values.stream_idle_timeout}/{values.stream_total_timeout} count={values.max_network_sends}"
    )
    context = hooks.LimiterContext(
        operation_id=None,
        origin="https://example.com",
        call_id="safe-call",
        parent_session_id=None,
        remaining_timeout=None,
        cancel_token=None,
    )
    lines.append(
        f"  limiter context fields={tuple(item.name for item in fields(context))} readonly={outcome(lambda: setattr(context, 'origin', 'changed'))}"
    )
    lines.append(
        f"  slotted {tuple(hasattr(value, '__dict__') for value in (future, token, values, context, options.TimeoutOptions()))}"
    )


def _errors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    state = errors.DeliveryState.NOT_SENT
    counters = dict(zip(_COUNTERS, (1, 2, 3, 4, 5, 6, ("refresh",), 7, 8)))
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(status_code=200, headers=headers, call_id="safe-call", elapsed=0, content_type=None)
    for error in (
        errors.SDKError(**counters),
        errors.PhaseTimeoutError(effective_timeout=1, phase="connect", delivery_state=state, **counters),
        errors.DeadlineExceededError(deadline_at=-1, elapsed=2, phase="encode", delivery_state=state, **counters),
        errors.RequestCancelledError(source="cancel_token", delivery_state=state, **counters),
        errors.RequestCancelledError(source="parent_cancel_token", delivery_state=state, **counters),
        errors.BudgetExceededError(budget_kind="network", limit=0, used=0, **counters),
        errors.BudgetExceededError(budget_kind="parent_network", limit=1, used=1, **counters),
        errors.LimiterExecutionError(action="acquire", **counters),
        errors.LimiterExecutionError(action="release", **counters),
    ):
        error.info = info
        error.cause = RuntimeError("private-secret")
        error.secondary_errors = (RuntimeError("private-secret"),)
        lines.append(
            f"  error {error} code={error.reason_code} counters={tuple(getattr(error, name) for name in _COUNTERS)} safe={'private-secret' not in repr(error) + str(error)}"
        )
        for name in _COUNTERS:
            lines.append(
                f"  readonly {type(error).__name__}.{name} {outcome(lambda error=error, name=name: setattr(error, name, 0))}"
            )
    phase = errors.PhaseTimeoutError(effective_timeout=0, phase="pool", delivery_state=state)
    deadline = errors.DeadlineExceededError(deadline_at=-1, elapsed=0, delivery_state=state)
    lines.append(
        f"  hierarchy phase={isinstance(phase, errors.TransportError)} deadline={isinstance(deadline, errors.TransportError)} effective={phase.effective_timeout} absolute={deadline.deadline_at} elapsed={deadline.elapsed}"
    )
    lines.append(f"  default counters={tuple(getattr(deadline, name) for name in _COUNTERS)}")
    for phase_name in ("read", "write"):
        value = errors.PhaseTimeoutError(effective_timeout=2.5, phase=phase_name, delivery_state=state)
        lines.append(f"  phase {value.phase} cap={value.effective_timeout}")
    identifiers = ["first"]
    error = errors.SDKError(auth_refresh_ids=identifiers)
    identifiers.append("second")
    lines.append(f"  copied refresh ids {error.auth_refresh_ids}")
    for label, constructor, values in (
        (
            "phase unknown",
            errors.PhaseTimeoutError,
            {"phase": "unknown", "effective_timeout": 1, "delivery_state": state},
        ),
        ("phase type", errors.PhaseTimeoutError, {"phase": 1, "effective_timeout": 1, "delivery_state": state}),
        (
            "deadline phase",
            errors.DeadlineExceededError,
            {"deadline_at": 1, "elapsed": 0, "delivery_state": state, "phase": "connect"},
        ),
        ("cancel source", errors.RequestCancelledError, {"source": "task", "delivery_state": state}),
        ("delivery", errors.RequestCancelledError, {"source": "cancel_token", "delivery_state": "NOT_SENT"}),
        ("budget kind", errors.BudgetExceededError, {"budget_kind": "auth", "limit": 1, "used": 0}),
        ("budget count", errors.BudgetExceededError, {"budget_kind": "network", "limit": -1, "used": 0}),
        ("budget bool", errors.BudgetExceededError, {"budget_kind": "network", "limit": 1, "used": True}),
        ("limiter action", errors.LimiterExecutionError, {"action": "wait"}),
        ("counter type", errors.SDKError, {"network_send_count": 1.5}),
        ("counter ids type", errors.SDKError, {"auth_refresh_ids": "refresh"}),
        ("counter ids item", errors.SDKError, {"auth_refresh_ids": (1,)}),
        ("wire count", errors.SDKError, {"wire_send_count": -1}),
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
            lambda value=value: errors.PhaseTimeoutError(effective_timeout=value, phase="read", delivery_state=state),
        )
    for label, value in (("negative", -1), ("infinity", float("inf"))):
        record(
            lines,
            f"invalid elapsed {label}",
            lambda value=value: errors.DeadlineExceededError(deadline_at=1, elapsed=value, delivery_state=state),
        )


def _hints(
    options: ModuleType, errors: ModuleType, hooks: ModuleType, transports: ModuleType, lines: list[str]
) -> None:
    for owner in (
        options.TimeoutOptions,
        options.ClientOptions,
        options.RequestOptions,
        options.Deadline,
        options.Clock,
        options.CancelToken,
        hooks.LimiterContext,
        transports.AttemptIOContext,
        transports.ResolvedTimeoutOptions,
        errors.SDKError,
        errors.PhaseTimeoutError,
        errors.DeadlineExceededError,
        errors.RequestCancelledError,
        errors.BudgetExceededError,
        errors.LimiterExecutionError,
    ):
        lines.append(f"  hints {owner.__name__} {tuple(get_type_hints(owner.__init__))}")
    for method in (
        options.Deadline.after,
        options.Deadline.remaining,
        options.Deadline.at.fget,
        options.Deadline.clock.fget,
        options.CancelToken.cancel,
        options.CancelToken.cancelled.fget,
        hooks.Limiter.acquire,
        hooks.AsyncLimiter.acquire,
        hooks.Permit.release,
        hooks.AsyncPermit.release,
        transports.TransportAdapter.send,
        transports.AsyncTransportAdapter.send,
        transports.TransportResponse.iter_raw_bytes,
        transports.AsyncTransportResponse.iter_raw_bytes,
        transports.TransportTraceSink.response_headers_received,
        transports.AttemptIOContext.deadline.fget,
        transports.AttemptIOContext.cancel_token.fget,
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
            view = api.with_options(
                options.RequestOptions(timeout=options.TimeoutOptions(write=7), max_network_sends=None)
            )
            exchange.respond(raw_response(200, b"layered"))
            call = options.RequestOptions(timeout=options.TimeoutOptions(pool=2))
            record(
                lines,
                "nested timeout merge",
                lambda: view.request_raw("GET", "https://example.com/layered", options=call).read(),
            )
            cleared = view.with_options(options.RequestOptions(timeout=None))
            exchange.respond(raw_response(200, b"cleared"))
            call = options.RequestOptions(timeout=options.TimeoutOptions(read=8), deadline=None, cancel_token=None)
            record(
                lines,
                "nested timeout clear",
                lambda: cleared.request_raw("GET", "https://example.com/cleared", options=call).read(),
            )


def _adapter_failure(
    package: ModuleType, options: ModuleType, errors: ModuleType, transports: ModuleType, lines: list[str]
) -> None:
    deadline = options.Deadline.after(3600)
    token = options.CancelToken()

    class FailingAdapter:
        capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1",)
        )

        def send(self, request: object, context: object) -> object:
            del request
            absolute = getattr(context, "deadline")
            lines.append(
                f"  attempt context deadline={absolute.at == deadline.at} remaining={0 < absolute.remaining() <= 3600} token={getattr(context, 'cancel_token') is token} phase={getattr(context, 'phase')} slotted={not hasattr(context, '__dict__')}"
            )
            raise errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")

        def close(self) -> None:
            pass

    with package.Client(
        transport_adapter=FailingAdapter(),
        options=options.ClientOptions(total_timeout=None, deadline=deadline, cancel_token=token),
    ) as api:
        record(lines, "adapter failure keeps context", lambda: api.request_raw("GET", "https://example.com/failure"))


def _clocks(
    package: ModuleType, options: ModuleType, errors: ModuleType, transports: ModuleType, lines: list[str]
) -> None:
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
    seen: list[object] = []

    class ContextAdapter:
        capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1",)
        )

        def send(self, request: object, context: object) -> object:
            del request
            seen.append(getattr(context, "deadline"))
            raise errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")

        def close(self) -> None:
            pass

    system = options.Deadline.after(3600)
    with package.Client(
        transport_adapter=ContextAdapter(), options=options.ClientOptions(total_timeout=None, clock=fake)
    ) as api:
        for label, given in (("same clock", deadline), ("system clock", system)):
            record(
                lines,
                f"deadline on the {label}",
                lambda given=given: api.request_raw(
                    "GET", "https://example.com/clock", options=options.RequestOptions(deadline=given)
                ),
            )
    same, moved = seen
    lines.append(
        f"  kept={same is deadline} moved onto the call's clock={moved.clock is fake}"
        f" by its remaining time={system.remaining() <= moved.at - 100 <= 3600}"
    )


def deadline_options(package: ModuleType, lines: list[str]) -> None:
    """Report public option validation, error shape, annotations, and timeout inheritance over real TLS."""
    options, errors, hooks, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("options", "errors", "hooks", "transports", "responses")
    )
    _values(options, hooks, lines)
    _errors(errors, responses, lines)
    _hints(options, errors, hooks, transports, lines)
    _live_calls(package, options, lines)
    _adapter_failure(package, options, errors, transports, lines)
    _clocks(package, options, errors, transports, lines)
