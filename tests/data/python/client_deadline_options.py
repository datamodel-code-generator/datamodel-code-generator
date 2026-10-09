"""Exercise generated deadline and error surfaces through public package modules."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, get_type_hints

import httpx2

from tests.data.python.client_runtime import Exchange, raw_response, record

if TYPE_CHECKING:
    from types import ModuleType

_INVALID_SECONDS = (
    ("bool", True),
    ("negative", -1),
    ("nan", float("nan")),
    ("infinity", float("inf")),
    ("negative infinity", float("-inf")),
    ("overflow", 10**400),
)


def _values(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, value in _INVALID_SECONDS:
        for field in ("timeout", "total_timeout"):
            record(
                lines,
                f"option {field} {label}",
                lambda field=field, value=value: options.RequestOptions(**{field: value}),
            )
    with httpx2.Client() as native:
        for field in ("timeout", "total_timeout"):
            record(
                lines,
                f"root {field} negative",
                lambda field=field: package.Client(http_client=native, **{field: -1}),
            )
            with package.Client(http_client=native) as api:
                record(lines, f"view {field} bool", lambda field=field, api=api: api.with_options(**{field: True}))
    lines.extend(
        f"  public {label} exported={hasattr(options, name)}"
        for label, name in (("deadline", "Deadline"), ("timeout options", "TimeoutOptions"))
    )
    for removed in ("deadline", "stream_idle_timeout", "stream_total_timeout"):
        record(lines, f"removed option {removed}", lambda removed=removed: options.RequestOptions(**{removed: None}))
    values = options.RequestOptions(timeout=0, total_timeout=0)
    phased = options.RequestOptions(timeout=httpx2.Timeout(None, read=1), total_timeout=None)
    lines.extend((
        f"  seconds timeout={values.timeout!r} total={values.total_timeout!r}",
        f"  phases timeout={phased.timeout!r} total={phased.total_timeout!r}",
        f"  slotted {hasattr(values, '__dict__')}",
    ))


def _errors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(status_code=200, headers=headers, elapsed=0.5, content_type=None, attempt_count=3)
    for error in (
        errors.SDKError(attempt_count=1, elapsed=2),
        errors.APITimeoutError(reason="phase_timeout"),
        errors.APITimeoutError(reason="deadline_exceeded"),
    ):
        lines.append(
            f"  error {error} reason={error.reason} attempts={error.attempt_count} elapsed={error.elapsed}"
            f" request={error.request_id}"
        )
        error.info = info
        error.cause = RuntimeError("private-secret")
        lines.append(f"  with response {error} safe={'private-secret' not in repr(error) + str(error)}")
    answered = errors.SDKError(info=info, attempt_count=9, elapsed=9)
    lines.extend((
        f"  response measurements attempts={answered.attempt_count} elapsed={answered.elapsed}",
        f"  hierarchy timeout={issubclass(errors.APITimeoutError, errors.APIConnectionError)}",
    ))


def _hints(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for label, function in (
        ("Client", package.Client.__init__),
        ("ClientView.with_options", package.ClientView.with_options),
        ("RequestOptions", options.RequestOptions.__init__),
        ("Clock", options.Clock.__init__),
        ("SDKError", errors.SDKError.__init__),
        ("APIConnectionError", errors.APIConnectionError.__init__),
        ("APITimeoutError", errors.APITimeoutError.__init__),
    ):
        record(lines, f"hints {label}", lambda function=function: tuple(get_type_hints(function)))


def _live_calls(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Report the phases each call hands HTTPX2 as the root, a view, and the call give or inherit a timeout."""
    exchange = Exchange(lines)

    def inspect_timeout(request: httpx2.Request) -> None:
        lines.append(f"  native timeout {request.extensions['timeout']}")

    def send(label: str, api: Any, path: str, call: object = None) -> None:
        exchange.respond(raw_response(200, path.encode()))
        record(lines, label, lambda: api.request_raw("GET", f"https://example.com/{path}", options=call).read())

    hooks = {"request": [inspect_timeout]}
    with exchange.client(event_hooks=hooks) as native, package.Client(http_client=native) as api:
        send("injected default timeout kept", api, "default")
    injected = httpx2.Timeout(9, connect=4)
    with exchange.client(event_hooks=hooks, timeout=injected) as native:
        with package.Client(http_client=native) as api:
            send("injected own timeout kept", api, "injected")
        with package.Client(http_client=native, timeout=2.5) as api:
            send("root number limits every phase", api, "number")
        with package.Client(http_client=native, timeout=None) as api:
            send("root none lifts every phase", api, "unlimited")
        root = httpx2.Timeout(5, connect=3, write=7, pool=2)
        with package.Client(http_client=native, timeout=root, total_timeout=None) as api:
            view = api.with_options(max_retries=0)
            send("root phases inherited by view and call", view, "inherited")
            numbered = view.with_options(timeout=7)
            send("view number over root phases", numbered, "view-number")
            call = options.RequestOptions(timeout=httpx2.Timeout(None, read=8))
            send("call phases over view number", numbered, "call-phases", call)
            cleared = numbered.with_options(timeout=None)
            send("view none inherited by call", cleared, "cleared")
            send("call number over view none", cleared, "call-number", options.RequestOptions(timeout=1))
            send("call none over root phases", api, "call-none", options.RequestOptions(timeout=None))


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
    retry = options.RetryOptions(initial_delay=0, jitter="none")
    with (
        httpx2.Client(transport=refusing) as native,
        package.Client(http_client=native, total_timeout=3600, timeout=None, max_retries=1, retry=retry) as api,
    ):
        record(lines, "refused attempts", lambda: api.request_raw("GET", "https://example.com/failure"))
    first, second = refusing.timeouts
    lines.append(
        f"  attempts={len(refusing.timeouts)} phases share the remaining time={len({*first.values()}) == 1}"
        f" within the deadline={0 < first['read'] <= 3600} shrinking={second['read'] <= first['read']}"
    )


def _clocks(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Use the client's clock for its optional budget, which a view or a call may remove or exhaust."""
    for name in ("monotonic", "time", "random", "sleep", "asleep"):
        record(lines, f"clock {name} type", lambda name=name: options.Clock(**{name: 1.0}))
    fake = options.Clock(monotonic=lambda: 100.0)

    class Unhashable:
        __hash__ = None

        def __call__(self) -> float:
            return 100.0

    odd = options.Clock(monotonic=Unhashable())
    lines.append(f"  hashing ignores sources clock={hash(odd) == hash(fake)}")
    refusing = _Refusing()
    with (
        httpx2.Client(transport=refusing) as native,
        package.Client(http_client=native, total_timeout=5, clock=fake, timeout=None, max_retries=0) as api,
    ):
        unbounded = api.with_options(total_timeout=None)
        for label, view, call in (
            ("budget on the client clock", api, None),
            ("view none removes the budget", unbounded, None),
            ("call none removes the budget", api, options.RequestOptions(total_timeout=None)),
            ("call budget over a view without one", unbounded, options.RequestOptions(total_timeout=2)),
            ("call zero budget", api, options.RequestOptions(total_timeout=0)),
            ("view zero budget", api.with_options(total_timeout=0), None),
        ):
            refusing.timeouts.clear()
            record(lines, label, lambda view=view, call=call: view.request_raw("GET", "https://x.test/", options=call))
            lines.append(f"  native phases {refusing.timeouts}")


def deadline_options(package: ModuleType, lines: list[str]) -> None:
    """Report public option validation, error shape, annotations, and timeout inheritance over real TLS."""
    options, errors, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "errors", "responses")
    )
    _values(package, options, lines)
    _errors(errors, responses, lines)
    _hints(package, options, errors, lines)
    _live_calls(package, options, lines)
    _attempt_timeout(package, options, lines)
    _clocks(package, options, lines)
