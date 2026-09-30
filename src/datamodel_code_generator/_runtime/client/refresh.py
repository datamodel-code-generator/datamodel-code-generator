"""Provider-owned shared token acquisition: one job at a time per token family, joined by every caller needing it.

A job runs on the provider's own worker thread or task under its own deadline. Callers only wait for its outcome, so a
caller that leaves never cancels an admitted job, and every waiter receives its own instance of a shared failure. At
most one job of a family has no outcome yet, and it is the family's active job.
"""

from __future__ import annotations

import contextvars
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol
from uuid import uuid4

from .auth import BearerCredential, CredentialContext, RefreshInfo, TokenVersion, checked_scopes
from .errors import (
    AuthConcurrencyLimitError,
    AuthConfigurationError,
    AuthProviderClosedError,
    AuthProviderExecutionError,
    AuthRefreshError,
    AuthTimeoutError,
    DeadlineExceededError,
    DeliveryState,
    OAuthExchangeError,
    RequestCancelledError,
    SDKError,
    TokenExpiredError,
)
from .oauth import Progress, Session, token_material
from .timing import TOKEN_INTERVAL

if TYPE_CHECKING:
    from asyncio import Future, Task, TimerHandle
    from collections.abc import Awaitable, Callable, Coroutine
    from concurrent.futures import ThreadPoolExecutor

    from .auth import RefreshState
    from .oauth import AsyncTokenEndpoint, Exchanged, TokenEndpoint
    from .options import OAuthProviderOptions

GRACE: Final = 1.0
_RECEIPTS: Final = 128
_RECEIPT_AGE: Final = 300.0


_CALL_FIELDS: Final = frozenset({
    "operation_id",
    "call_id",
    "parent_session_id",
    "info",
    "resource_attempt_count",
    "redirect_count",
    "auth_exchange_count",
    "network_send_count",
    "network_send_budget_used",
    "auth_exchange_budget_used",
    "auth_refresh_ids",
    "auth_refresh_pending",
    "wire_send_count",
})


def _own_fields(error: SDKError) -> dict[str, Any]:
    import inspect  # noqa: PLC0415

    return {
        name: getattr(error, name)
        for name, parameter in inspect.signature(type(error)).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and name not in _CALL_FIELDS
    }


def clone_error(error: SDKError) -> SDKError:
    """Return a new instance of an error's class with its fields, for another caller of one shared failure.

    Call identity and counters are left for the call that publishes the copy to fill in; the cause is shared.
    """
    constructor: Callable[..., SDKError] = type(error)
    return constructor(**_own_fields(error))


def identified(error: AuthRefreshError, provider_id: str, refresh_id: str) -> AuthRefreshError:
    """Return a copy of an auth failure naming the provider and the acquisition it ended."""
    constructor: Callable[..., AuthRefreshError] = type(error)
    fields = _own_fields(error)
    fields.update(provider_id=provider_id, refresh_id=refresh_id)
    return constructor(**fields)


def refresh_margin(ttl: float) -> float:
    """Return how long before its expiry a token is renewed: a tenth of its lifetime, at most thirty seconds."""
    return min(30.0, ttl * 0.1)


@dataclass(frozen=True, slots=True)
class Published:
    """Material a job obtained, the monotonic time from which a caller renews it, and when its token expires.

    Until its token expires, the material serves unforced callers while a renewal runs; None keeps no such time.
    """

    material: BearerCredential
    refresh_at: float | None
    expires_at: float | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class Failed:
    """A job's failure, kept as a template each waiter receives a new instance of, and the state it leaves."""

    error: SDKError
    state: RefreshState


Outcome = Published | Failed


class _Waiter(Protocol):
    def deliver(self, outcome: Outcome) -> None:
        """Hand a committed outcome to the waiting caller."""


class Job:
    """One shared acquisition: QUEUED until admitted, then run once; its outcome is committed exactly once."""

    __slots__ = ("finished", "outcome", "progress", "refresh_id", "session", "waiters")

    def __init__(self) -> None:
        """Start queued under a new refresh id, without waiters or a session."""
        self.refresh_id = str(uuid4())
        self.waiters: list[_Waiter] = []
        self.session: Session | None = None
        self.progress = Progress()
        self.outcome: Outcome | None = None
        self.finished = False

    @property
    def until(self) -> float | None:
        """Return when waiters stop waiting for an admitted job that has not finished its work."""
        return None if self.session is None else self.session.deadline.at + GRACE


@dataclass(slots=True, eq=False)
class SyncWaiter:
    """A synchronous caller waiting on the family's condition for a job's outcome."""

    outcome: Outcome | None = None

    def deliver(self, outcome: Outcome) -> None:
        """Hand the outcome over; committing it wakes the caller."""
        self.outcome = outcome


@dataclass(slots=True, eq=False)
class AsyncWaiter:
    """An asyncio caller waiting for a job's outcome through its own future."""

    future: Future[Outcome]

    def deliver(self, outcome: Outcome) -> None:
        """Resolve the caller's future."""
        self.future.set_result(outcome)


class Receipts:
    """Snapshots of jobs by refresh id: queued and running ones, then the latest ended ones for a while."""

    __slots__ = ("_done", "_pending")

    def __init__(self) -> None:
        """Start without receipts."""
        self._pending: dict[str, Job] = {}
        self._done: OrderedDict[str, tuple[float, RefreshInfo]] = OrderedDict()

    def started(self, job: Job) -> None:
        """Record a new job."""
        self._pending[job.refresh_id] = job

    def dropped(self, job: Job) -> None:
        """Forget a queued job nobody waits for anymore."""
        del self._pending[job.refresh_id]

    def completed(self, job: Job, info: RefreshInfo) -> None:
        """Replace a job's pending receipt by its terminal one."""
        del self._pending[job.refresh_id]
        self._done[job.refresh_id] = (monotonic(), info)
        self._evict()

    def get(self, refresh_id: object) -> RefreshInfo | None:
        """Return a job's snapshot, or None for an unknown or evicted id."""
        if not isinstance(refresh_id, str):
            return None
        self._evict()
        if (job := self._pending.get(refresh_id)) is not None:
            return RefreshInfo(refresh_id, "PENDING", int(job.progress.sent), None)
        return None if (entry := self._done.get(refresh_id)) is None else entry[1]

    def _evict(self) -> None:
        now = monotonic()
        while self._done and (len(self._done) > _RECEIPTS or now - next(iter(self._done.values()))[0] > _RECEIPT_AGE):
            self._done.popitem(last=False)


class SharedRefresh:
    """A family's shared state; every transition happens under its lock, and waiters wake on its condition."""

    __slots__ = (
        "_active",
        "_outstanding",
        "_running",
        "cache",
        "changed",
        "lifecycle",
        "lock",
        "options",
        "provider_id",
        "receipts",
        "state",
    )

    def __init__(self, options: OAuthProviderOptions) -> None:
        """Start open, uninitialized, and without jobs."""
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.options = options
        self.provider_id = str(uuid4())
        self.lifecycle: Literal["OPEN", "CLOSING", "CLOSED"] = "OPEN"
        self.state: RefreshState | Literal["UNINITIALIZED"] = "UNINITIALIZED"
        self.cache: Published | None = None
        self.receipts = Receipts()
        self._active: Job | None = None
        self._outstanding: set[Job] = set()
        self._running: set[Job] = set()

    def claim(self, waiter: _Waiter, *, force: bool) -> Published | tuple[Job, bool, Published | None]:
        """Return usable cached material, or attach the waiter to the family's job and say whether to start it.

        Past its renewal time, a token that has not expired still serves an unforced caller, returned beside the job
        renewing it, which the caller starts without waiting for it. The caller holds the lock. A new job is admitted at
        once while fewer jobs than the concurrency limit run.
        """
        options = self.options
        if (lifecycle := self.lifecycle) != "OPEN":
            raise AuthProviderClosedError(
                state="CLOSING" if lifecycle == "CLOSING" else "CLOSED",
                delivery_state=DeliveryState.NOT_SENT,
                phase="admission",
                provider_id=self.provider_id,
            )
        if (active := self._active) is not None:
            self.expire(active)
        now, cache = monotonic(), self.cache
        if not force and cache is not None and (cache.refresh_at is None or now < cache.refresh_at):
            return cache
        serving = None if force or cache is None or cache.expires_at is None or now >= cache.expires_at else cache
        start = False
        if (job := self._active) is None:
            if len(self._outstanding) >= options.max_pending_refreshes:
                raise AuthConcurrencyLimitError(
                    limit_kind="pending_refreshes",
                    limit=options.max_pending_refreshes,
                    state=self.state,
                    delivery_state=DeliveryState.NOT_SENT,
                    phase="admission",
                    provider_id=self.provider_id,
                )
            job = self._active = Job()
            self._outstanding.add(job)
            self.receipts.started(job)
            if start := len(self._running) < options.max_concurrent_refreshes:
                self.admit(job)
        elif serving is not None:
            return job, False, serving
        elif len(job.waiters) >= options.max_waiters:
            raise AuthConcurrencyLimitError(
                limit_kind="waiters",
                limit=options.max_waiters,
                state=self.state,
                delivery_state=DeliveryState.NOT_SENT,
                phase="admission",
                provider_id=self.provider_id,
                refresh_id=job.refresh_id,
            )
        job.waiters.append(waiter)
        return job, start, serving

    def admit(self, job: Job) -> None:
        """Start a job's provider-owned session; the caller holds the lock."""
        options = self.options
        job.session = Session.start(options.refresh_timeout, options.phase_timeout)
        self._running.add(job)

    def commit(self, job: Job, outcome: Outcome) -> None:
        """Commit a job's outcome once, publish material before anyone wakes, and deliver it in arrival order."""
        if job.outcome is not None:
            return
        job.outcome = outcome
        self._active = None
        state: RefreshState
        if isinstance(outcome, Published):
            self.cache = outcome
            state, failure = "READY", None
        else:
            state, failure = outcome.state, outcome.error.reason_code
        self.state = state
        self.receipts.completed(job, RefreshInfo(job.refresh_id, state, int(job.progress.sent), failure))
        waiters, job.waiters = job.waiters, []
        for waiter in waiters:
            waiter.deliver(outcome)
        self.changed.notify_all()

    def leave(self, waiter: _Waiter, job: Job) -> None:
        """Detach a departing waiter; a job nobody waits for is dropped unless it was already admitted."""
        if waiter in job.waiters:
            job.waiters.remove(waiter)
        if job.session is None and not job.waiters and job.outcome is None:
            self._active = None
            self._outstanding.remove(job)
            self.receipts.dropped(job)

    def expire(self, job: Job) -> None:
        """Fail an admitted job whose work outlived its session and grace period, without stopping that work."""
        if (until := job.until) is not None and monotonic() >= until:
            self.overdue(job)

    def overdue(self, job: Job) -> None:
        """Fail a job that has not ended although its session and grace period have."""
        if job.outcome is None:
            self.commit(job, self.timed_out(job))

    def timed_out(self, job: Job) -> Failed:
        """Return the failure of a job whose session ended before its work did."""
        sent = job.progress.sent
        state: RefreshState = "EXCHANGE_REJECTED" if sent else "FAILED_NOT_SENT"
        return Failed(
            AuthTimeoutError(
                effective_timeout=self.options.refresh_timeout,
                timeout_kind="provider",
                state=state,
                delivery_state=job.progress.delivery,
                provider_id=self.provider_id,
                refresh_id=job.refresh_id,
            ),
            state,
        )

    def failed(self, error: BaseException, job: Job) -> Failed:
        """Keep an SDK error a job raised, naming the job when it names none; any other exception is a provider failure.

        Subclasses from outside the SDK are wrapped too, since each waiter receives a copy made by the SDK.
        """
        state: RefreshState = "EXCHANGE_REJECTED" if job.progress.sent else "FAILED_NOT_SENT"
        if isinstance(error, SDKError) and type(error).__module__ == SDKError.__module__:
            if isinstance(error, AuthRefreshError) and error.provider_id is None and error.refresh_id is None:
                return Failed(identified(error, self.provider_id, job.refresh_id), state)
            return Failed(error, state)
        return Failed(
            AuthProviderExecutionError(
                callback="get",
                state=state,
                delivery_state=job.progress.delivery,
                cause=error,
                provider_id=self.provider_id,
                refresh_id=job.refresh_id,
            ),
            state,
        )

    def finish(self, job: Job, outcome: Outcome) -> Job | None:
        """Commit a returned job, free its slot even when finished twice, and return the queued job to start next."""
        self.commit(job, outcome)
        job.finished = True
        self._running.discard(job)
        self._outstanding.discard(job)
        self.changed.notify_all()
        if (
            self.lifecycle != "OPEN"
            or (queued := self._active) is None
            or queued.session is not None
            or len(self._running) >= self.options.max_concurrent_refreshes
        ):
            return None
        self.admit(queued)
        return queued

    def closing(self) -> None:
        """Stop admitting jobs and end the queued one; admitted jobs keep running."""
        if self.lifecycle == "OPEN":
            self.lifecycle = "CLOSING"
            if (queued := self._active) is not None and queued.session is None:
                self.commit(
                    queued,
                    Failed(
                        AuthProviderClosedError(
                            state="CLOSING",
                            delivery_state=DeliveryState.NOT_SENT,
                            phase="wait",
                            provider_id=self.provider_id,
                            refresh_id=queued.refresh_id,
                        ),
                        "FAILED_NOT_SENT",
                    ),
                )
                self._outstanding.remove(queued)

    def drained(self, now: float) -> float | None:
        """Expire running jobs whose session ended, and return how long closing still waits for the others."""
        waits: list[float] = []
        for job in self._outstanding:
            self.expire(job)
            if job.outcome is None and (until := job.until) is not None:
                waits.append(until - now)
        return min(waits) if waits else None


def _detached(coroutine: Coroutine[object, object, None]) -> Task[None]:
    """Run a coroutine as a task that sees none of the starting caller's context variables."""
    from asyncio import get_running_loop  # noqa: PLC0415

    loop = get_running_loop()
    return contextvars.Context().run(lambda: loop.create_task(coroutine))


def _outcome(outcome: Outcome) -> BearerCredential:
    if isinstance(outcome, Failed):
        raise clone_error(outcome.error)
    return outcome.material


def _waited(context: CredentialContext, started: float) -> float | None:
    """Return how long a caller may wait before checking again, raising once it was cancelled or ran out of time."""
    if (token := context.cancel_token) is not None and token.cancelled:
        raise RequestCancelledError(source="cancel_token", delivery_state=DeliveryState.NOT_SENT)
    if (deadline := context.deadline) is None:
        return None if token is None else TOKEN_INTERVAL
    if (remaining := deadline.at - (now := monotonic())) <= 0:
        raise DeadlineExceededError(
            deadline_at=deadline.at, elapsed=now - started, delivery_state=DeliveryState.NOT_SENT, phase="auth"
        )
    return remaining if token is None else min(remaining, TOKEN_INTERVAL)


class SyncSharedRefresh:
    """Runs a family's jobs on a lazily created worker pool and lets synchronous callers wait for them."""

    __slots__ = ("_acquire", "_close", "_executor", "_releasing", "shared")

    def __init__(
        self,
        options: OAuthProviderOptions,
        acquire: Callable[[Job], Outcome],
        close: Callable[[], None],
    ) -> None:
        """Keep how a job acquires and how the owned transport closes; no thread exists yet."""
        self.shared = SharedRefresh(options)
        self._acquire = acquire
        self._close = close
        self._executor: ThreadPoolExecutor | None = None
        self._releasing = False

    def obtain(self, context: CredentialContext, *, force: bool) -> BearerCredential:
        """Return usable material, joining or starting the family's job and waiting within the caller's limits."""
        started = monotonic()
        shared = self.shared
        waiter = SyncWaiter()
        with shared.lock:
            claimed = shared.claim(waiter, force=force)
            if isinstance(claimed, Published):
                return claimed.material
            job, start, serving = claimed
        if start:
            self._start(job)
        if serving is not None:
            with shared.lock:
                shared.leave(waiter, job)
            return serving.material
        with shared.lock:
            try:
                while (outcome := waiter.outcome) is None:
                    remaining = _waited(context, started)
                    if (until := job.until) is not None:
                        left = until - monotonic()
                        remaining = left if remaining is None else min(remaining, left)
                    shared.changed.wait(None if remaining is None else min(remaining, threading.TIMEOUT_MAX))
                    shared.expire(job)
            finally:
                shared.leave(waiter, job)
        return _outcome(outcome)

    def _start(self, job: Job) -> None:
        """Submit an admitted job to the provider's workers, outside every caller's context.

        A pool that cannot run the job, as while the interpreter shuts down, fails it instead of keeping its slot, and
        an interruption fails it before propagating.
        """
        try:
            if (executor := self._executor) is None:
                from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

                executor = self._executor = ThreadPoolExecutor(
                    max_workers=self.shared.options.max_concurrent_refreshes, thread_name_prefix="oauth-refresh"
                )
            executor.submit(contextvars.Context().run, self._run, job)
        except RuntimeError as error:
            self._finish(job, self.shared.failed(error, job))
        except BaseException as error:
            self._finish(job, self.shared.failed(error, job))
            raise

    def _run(self, job: Job) -> None:
        """Acquire and hand the outcome to the waiters; an interruption fails the job before it propagates.

        A job that already failed because the pool could not start a worker for it is skipped.
        """
        with self.shared.lock:
            if job.finished:
                return
        try:
            outcome = self._acquire(job)
        except Exception as error:  # noqa: BLE001 - A job's failure reaches its waiters, never the worker.
            outcome = self.shared.failed(error, job)
        except BaseException as error:
            self._finish(job, self.shared.failed(error, job))
            raise
        self._finish(job, outcome)

    def _finish(self, job: Job, outcome: Outcome) -> None:
        with self.shared.lock:
            queued = self.shared.finish(job, outcome)
        if queued is not None:
            self._start(queued)

    def close(self) -> None:
        """Refuse new jobs, end the queued one, wait for running jobs until their sessions end, then release once.

        A concurrent close returns once that release has finished.
        """
        shared = self.shared
        with shared.lock:
            shared.closing()
            while (left := shared.drained(monotonic())) is not None:
                shared.changed.wait(min(left, threading.TIMEOUT_MAX))
            releasing, self._releasing = self._releasing, True
            if releasing:
                while shared.lifecycle != "CLOSED":
                    shared.changed.wait()
                return
        try:
            self._close()
        finally:
            if (executor := self._executor) is not None:
                executor.shutdown(wait=False)
            with shared.lock:
                shared.lifecycle = "CLOSED"
                shared.changed.notify_all()


class AsyncSharedRefresh:
    """Runs a family's jobs as tasks on one event loop and lets asyncio callers await them."""

    __slots__ = ("_acquire", "_close", "_finalizer", "_tasks", "shared")

    def __init__(
        self,
        options: OAuthProviderOptions,
        acquire: Callable[[Job], Awaitable[Outcome]],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        """Keep how a job acquires and how the owned transport closes; no task exists yet."""
        self.shared = SharedRefresh(options)
        self._acquire = acquire
        self._close = close
        self._tasks: set[Task[None]] = set()
        self._finalizer: Task[None] | None = None

    async def obtain(self, context: CredentialContext, *, force: bool) -> BearerCredential:
        """Return usable material, joining or starting the family's job and awaiting it within the caller's limits."""
        from asyncio import get_running_loop, wait  # noqa: PLC0415

        started = monotonic()
        shared = self.shared
        waiter = AsyncWaiter(get_running_loop().create_future())
        with shared.lock:
            claimed = shared.claim(waiter, force=force)
            if isinstance(claimed, Published):
                return claimed.material
            job, start, serving = claimed
        if start:
            self._start(job)
        if serving is not None:
            with shared.lock:
                shared.leave(waiter, job)
            return serving.material
        future = waiter.future
        try:
            while not future.done():
                await wait({future}, timeout=_waited(context, started))
        finally:
            with shared.lock:
                shared.leave(waiter, job)
        return _outcome(future.result())

    def _start(self, job: Job) -> None:
        """Run an admitted job as a task outside every caller's context, failing it once its session and grace end."""
        from asyncio import get_running_loop  # noqa: PLC0415

        loop = get_running_loop()
        until = job.until
        assert until is not None
        timer = loop.call_at(loop.time() + until - monotonic(), self._overdue, job)
        task = _detached(self._run(job, timer))
        self._tasks.add(task)
        task.add_done_callback(partial(self._done, job, timer))

    def _overdue(self, job: Job) -> None:
        with self.shared.lock:
            self.shared.overdue(job)

    async def _run(self, job: Job, timer: TimerHandle) -> None:
        """Acquire and hand the outcome to the waiters; a cancellation fails the job before it propagates."""
        try:
            outcome = await self._acquire(job)
        except Exception as error:  # noqa: BLE001 - A job's failure reaches its waiters, never the task.
            outcome = self.shared.failed(error, job)
        except BaseException as error:
            self._finish(job, self.shared.failed(error, job), timer)
            raise
        self._finish(job, outcome, timer)

    def _done(self, job: Job, timer: TimerHandle, task: Task[None]) -> None:
        """Forget an ended task, failing its job when the task was cancelled before it started."""
        from asyncio import CancelledError  # noqa: PLC0415

        self._tasks.discard(task)
        if not job.finished:
            self._finish(job, self.shared.failed(CancelledError(), job), timer)

    def _finish(self, job: Job, outcome: Outcome, timer: TimerHandle) -> None:
        timer.cancel()
        with self.shared.lock:
            queued = self.shared.finish(job, outcome)
        if queued is not None:
            self._start(queued)

    async def aclose(self) -> None:
        """Close once in a task of its own, which a cancelled caller leaves running for a later aclose to await.

        A later aclose starts a new finalizer if the previous one was cancelled.
        """
        from asyncio import shield  # noqa: PLC0415

        with self.shared.lock:
            self.shared.closing()
        if (finalizer := self._finalizer) is None or finalizer.cancelled():
            finalizer = self._finalizer = _detached(self._finalize())
        await shield(finalizer)

    async def _finalize(self) -> None:
        from asyncio import FIRST_COMPLETED, wait  # noqa: PLC0415

        while (left := self._drained()) is not None:
            await wait(self._tasks, timeout=left, return_when=FIRST_COMPLETED)
        with self.shared.lock:
            self.shared.lifecycle = "CLOSED"
        await self._close()

    def _drained(self) -> float | None:
        with self.shared.lock:
            return self.shared.drained(monotonic())


def _invalidate(shared: SharedRefresh, version: object) -> None:
    """Forget the cached material an attempt was rejected with; another version stays usable."""
    if not isinstance(version, TokenVersion):
        raise AuthConfigurationError(field_path=("version",), condition="invalid_type")
    with shared.lock:
        if (cache := shared.cache) is not None and cache.material.version is version:
            shared.cache = None


class ClientCredentialsGrant:
    """The client credentials grant's configuration, its request form, and how an answer becomes an outcome."""

    __slots__ = ("audience", "form", "scopes")

    def __init__(self, method: object, scopes: object, audience: object) -> None:
        """Refuse public clients, then validate the requested scopes and the fixed audience."""
        if method == "none":
            raise AuthConfigurationError(field_path=("client_auth_method",), condition="invalid_value")
        self.scopes = checked_scopes(scopes, "scopes")
        if audience is not None and (not isinstance(audience, str) or not audience):
            raise AuthConfigurationError(field_path=("audience",), condition="invalid_value")
        self.audience: str | None = audience
        self.form = (
            ("grant_type", "client_credentials"),
            *((("scope", " ".join(self.scopes)),) if self.scopes else ()),
            *((("audience", audience),) if audience is not None else ()),
        )

    def checked(self, context: object) -> CredentialContext:
        """Refuse a context of another type or one requiring an audience other than the configured one."""
        if not isinstance(context, CredentialContext):
            raise AuthConfigurationError(field_path=("context",), condition="invalid_type")
        if context.audience is not None and context.audience != self.audience:
            raise AuthConfigurationError(field_path=("audience",), condition="audience_mismatch")
        return context

    def outcome(self, exchanged: Exchanged, job: Job, provider_id: str) -> Outcome:
        """Publish a valid token, or fail the job: client credentials never leave anything consumed behind."""
        delivery = exchanged.delivery
        if exchanged.outcome == "success":
            assert exchanged.fields is not None
            assert exchanged.received is not None
            assert exchanged.receipt is not None
            try:
                access, _, _ = token_material(exchanged.fields, exchanged.received, self.scopes)
            except (ValueError, TokenExpiredError) as cause:
                return _rejected(job, provider_id, exchanged, phase="validate", cause=cause)
            if (expires_at := access.expires_at) is None:
                return Published(BearerCredential(access, TokenVersion()), None)
            expires = exchanged.receipt + (ttl := (expires_at - exchanged.received).total_seconds())
            return Published(
                BearerCredential(access, TokenVersion()), expires - refresh_margin(ttl), expires_at=expires
            )
        if exchanged.outcome in {"rejected", "http_status", "malformed_response"}:
            phase: Literal["validate", "unknown"] = (
                "validate" if exchanged.outcome == "malformed_response" else "unknown"
            )
            return _rejected(job, provider_id, exchanged, phase=phase, cause=exchanged.cause)
        state: RefreshState = "FAILED_NOT_SENT" if exchanged.outcome == "unsent" else "EXCHANGE_REJECTED"
        if (timeout_kind := exchanged.timeout_kind) is not None:
            assert exchanged.timeout is not None
            return Failed(
                AuthTimeoutError(
                    effective_timeout=exchanged.timeout,
                    timeout_kind=timeout_kind,
                    state=state,
                    delivery_state=delivery,
                    phase=exchanged.phase,
                    cause=exchanged.cause,
                    provider_id=provider_id,
                    refresh_id=job.refresh_id,
                ),
                state,
            )
        return Failed(
            OAuthExchangeError(
                state=state,
                delivery_state=delivery,
                phase=exchanged.phase,
                cause=exchanged.cause,
                provider_id=provider_id,
                refresh_id=job.refresh_id,
            ),
            state,
        )


def _rejected(
    job: Job,
    provider_id: str,
    exchanged: Exchanged,
    *,
    phase: Literal["validate", "unknown"],
    cause: BaseException | None,
) -> Failed:
    return Failed(
        OAuthExchangeError(
            status_code=exchanged.status_code,
            oauth_error=exchanged.oauth_error,
            state="EXCHANGE_REJECTED",
            delivery_state=exchanged.delivery,
            phase=phase,
            cause=cause,
            provider_id=provider_id,
            refresh_id=job.refresh_id,
        ),
        "EXCHANGE_REJECTED",
    )


class SyncClientCredentials:
    """A synchronous client credentials family: the shared job engine around one token endpoint."""

    __slots__ = ("_endpoint", "_grant", "_refresh")

    def __init__(self, options: OAuthProviderOptions, endpoint: TokenEndpoint, grant: ClientCredentialsGrant) -> None:
        """Keep the endpoint and grant; nothing runs until the first acquisition."""
        self._endpoint = endpoint
        self._grant = grant
        self._refresh = SyncSharedRefresh(options, self._acquire, endpoint.close)

    def obtain(self, context: object, *, force: bool) -> BearerCredential:
        """Return usable material or acquire it, after checking the caller's context."""
        return self._refresh.obtain(self._grant.checked(context), force=force)

    def invalidate(self, version: object) -> None:
        """Forget the cached material if it is the rejected version."""
        _invalidate(self._refresh.shared, version)

    def snapshot(self, refresh_id: object) -> RefreshInfo | None:
        """Return a recent job's snapshot."""
        shared = self._refresh.shared
        with shared.lock:
            return shared.receipts.get(refresh_id)

    def close(self) -> None:
        """Close the family and its owned transport."""
        self._refresh.close()

    def _acquire(self, job: Job) -> Outcome:
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        exchanged = endpoint.exchange(self._grant.form, job.session, job.progress, "FAILED_NOT_SENT")
        return self._grant.outcome(exchanged, job, self._refresh.shared.provider_id)


class AsyncClientCredentials:
    """An asyncio client credentials family, bound to the event loop it was created on or first used from."""

    __slots__ = ("_endpoint", "_grant", "_refresh")

    def __init__(
        self, options: OAuthProviderOptions, endpoint: AsyncTokenEndpoint, grant: ClientCredentialsGrant
    ) -> None:
        """Keep the endpoint and grant; nothing runs until the first acquisition."""
        self._endpoint = endpoint
        self._grant = grant
        self._refresh = AsyncSharedRefresh(options, self._acquire, endpoint.aclose)

    async def obtain(self, context: object, *, force: bool) -> BearerCredential:
        """Return usable material or acquire it, after checking the caller's context and event loop."""
        checked = self._grant.checked(context)
        self._endpoint.bind()
        return await self._refresh.obtain(checked, force=force)

    def invalidate(self, version: object) -> None:
        """Forget the cached material if it is the rejected version."""
        _invalidate(self._refresh.shared, version)

    def snapshot(self, refresh_id: object) -> RefreshInfo | None:
        """Return a recent job's snapshot."""
        shared = self._refresh.shared
        with shared.lock:
            return shared.receipts.get(refresh_id)

    async def aclose(self) -> None:
        """Close the family and its owned transport."""
        self._endpoint.bind()
        await self._refresh.aclose()

    async def _acquire(self, job: Job) -> Outcome:
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        exchanged = await endpoint.exchange(self._grant.form, job.session, job.progress, "FAILED_NOT_SENT")
        return self._grant.outcome(exchanged, job, self._refresh.shared.provider_id)
