"""Provider-owned shared token acquisition: one job at a time per token family, joined by every caller needing it.

A job runs on the provider's own worker thread or task under its own deadline. Callers only wait for its outcome, so a
caller that leaves never cancels an admitted job, and every waiter receives its own instance of a shared failure. At
most one job of a family has no outcome yet, and it is the family's active job.
"""

from __future__ import annotations

import contextvars
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol
from uuid import uuid4

from .auth import BearerCredential, CredentialContext, RefreshInfo, TokenVersion, checked_scopes
from .errors import (
    AuthBudgetExceededError,
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
from .timing import TOKEN_INTERVAL, real_end, wait_left

if TYPE_CHECKING:
    from asyncio import Future, Task, TimerHandle
    from collections.abc import Awaitable, Callable, Coroutine
    from concurrent.futures import Future as WorkFuture
    from concurrent.futures import ThreadPoolExecutor

    from .admission import CallAdmission
    from .auth import RefreshState
    from .errors import AuthBudgetKind
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


@dataclass(frozen=True, slots=True)
class Again:
    """A job that changed the family instead of acquiring material for its waiters, who claim again."""


Outcome = Published | Failed | Again


class _Waiter(Protocol):
    @property
    def admission(self) -> CallAdmission | None:
        """Return the call the caller waits for, if a client call accounts for the acquisition."""

    def deliver(self, outcome: Outcome) -> None:
        """Hand a committed outcome to the waiting caller."""


class Job:
    """One shared acquisition: QUEUED until admitted, then run once; its outcome is committed exactly once.

    It stays outstanding until its work returns, or until it ends without being admitted.
    """

    __slots__ = (
        "charged",
        "ends",
        "exchanges",
        "finished",
        "outcome",
        "outstanding",
        "progress",
        "refresh_id",
        "session",
        "waiters",
    )

    def __init__(self, *, exchanges: bool = True) -> None:
        """Start queued under a new refresh id, without waiters or a session.

        A job that sends no token request is never charged to a call.
        """
        self.exchanges = exchanges
        self.refresh_id = str(uuid4())
        self.waiters: list[_Waiter] = []
        self.session: Session | None = None
        self.ends = 0.0
        self.progress = Progress()
        self.outcome: Outcome | None = None
        self.finished = False
        self.outstanding = True
        self.charged: CallAdmission | None = None

    @property
    def until(self) -> float | None:
        """Return when waiters stop waiting for an admitted job that has not finished its work."""
        return None if self.session is None else self.session.deadline.at + GRACE

    def left(self, now: float) -> float | None:
        """Return how long an admitted job may still run: until its session and grace end, or as long in real time.

        now is the provider's clock, and ends is the real time its session and grace measured when it was admitted.
        """
        return None if (until := self.until) is None else wait_left(until - now, self.ends)


@dataclass(slots=True, eq=False)
class SyncWaiter:
    """A synchronous caller waiting on the family's condition for a job's outcome."""

    admission: CallAdmission | None = None
    outcome: Outcome | None = None

    def deliver(self, outcome: Outcome) -> None:
        """Hand the outcome over; committing it wakes the caller."""
        self.outcome = outcome


@dataclass(slots=True, eq=False)
class AsyncWaiter:
    """An asyncio caller waiting for a job's outcome through its own future."""

    future: Future[Outcome]
    admission: CallAdmission | None = None

    def deliver(self, outcome: Outcome) -> None:
        """Resolve the caller's future."""
        self.future.set_result(outcome)


class Receipts:
    """Snapshots of jobs by refresh id: queued and running ones, then the latest ended ones for a while."""

    __slots__ = ("_done", "_monotonic", "_pending")

    def __init__(self, monotonic: Callable[[], float]) -> None:
        """Start without receipts, aging ended ones on the provider's clock."""
        self._monotonic = monotonic
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
        self._done[job.refresh_id] = (self._monotonic(), info)
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
        now = self._monotonic()
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
        "monotonic",
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
        self.monotonic = monotonic = options.clock.monotonic
        self.provider_id = str(uuid4())
        self.lifecycle: Literal["OPEN", "CLOSING", "CLOSED"] = "OPEN"
        self.state: RefreshState | Literal["UNINITIALIZED"] = "UNINITIALIZED"
        self.cache: Published | None = None
        self.receipts = Receipts(monotonic)
        self._active: Job | None = None
        self._outstanding: set[Job] = set()
        self._running: set[Job] = set()

    def claim(
        self, waiter: _Waiter, context: CredentialContext, *, force: bool
    ) -> Published | tuple[Job, bool, bool, Published | None]:
        """Return usable cached material, or attach the waiter to the family's job.

        The result says whether to start the job, whether another caller started it, and the material that serves the
        caller while the job renews it. Past its renewal time, a token that has not expired serves an unforced caller
        without waiting, which starts the renewal only when it can be admitted and paid for at once. The caller holds
        the lock. A new job is admitted at once while fewer jobs than the concurrency limit run. A call that may pay for
        an admission, starting a job or joining a queued one, needs room in its budgets first.
        """
        if (lifecycle := self.lifecycle) != "OPEN":
            raise AuthProviderClosedError(
                state="CLOSING" if lifecycle == "CLOSING" else "CLOSED",
                delivery_state=DeliveryState.NOT_SENT,
                phase="admission",
                provider_id=self.provider_id,
            )
        if (active := self._active) is not None:
            self.expire(active)
        if (usable := self.usable(context, force=force)) is not None:
            return usable
        if (
            not force
            and (cache := self.cache) is not None
            and cache.expires_at is not None
            and self.monotonic() < cache.expires_at
        ):
            return self._renewing(waiter, cache)
        admission = waiter.admission
        if (job := self._active) is None:
            job = self.next_job()
            if job.exchanges and admission is not None and (shortfall := admission.shortfall()) is not None:
                raise self.over_budget(shortfall)
            return job, self.enlist(job, waiter), False, None
        if job.session is None and job.exchanges and admission is not None and (shortfall := admission.shortfall()):
            raise self.over_budget(shortfall, job.refresh_id)
        self.join(job, waiter)
        return job, False, True, None

    def join(self, job: Job, waiter: _Waiter) -> None:
        """Attach a waiter to the active job, within the waiter limit; the caller holds the lock."""
        if len(job.waiters) >= (limit := self.options.max_waiters):
            raise AuthConcurrencyLimitError(
                limit_kind="waiters",
                limit=limit,
                state=self.state,
                delivery_state=DeliveryState.NOT_SENT,
                phase="admission",
                provider_id=self.provider_id,
                refresh_id=job.refresh_id,
            )
        job.waiters.append(waiter)
        if (admission := waiter.admission) is not None:
            admission.joined(job)

    def next_job(self) -> Job:  # noqa: PLR6301 - A family of another grant starts other kinds of jobs.
        """Return the job a claim starts when none is active; the caller holds the lock."""
        return Job()

    def enlist(self, job: Job, waiter: _Waiter) -> bool:
        """Make a new job the active one with its first waiter, admitting it while fewer jobs than the limit run.

        Return whether it was admitted; the caller holds the lock.
        """
        options = self.options
        if len(self._outstanding) >= options.max_pending_refreshes:
            raise AuthConcurrencyLimitError(
                limit_kind="pending_refreshes",
                limit=options.max_pending_refreshes,
                state=self.state,
                delivery_state=DeliveryState.NOT_SENT,
                phase="admission",
                provider_id=self.provider_id,
            )
        self._active = job
        self._outstanding.add(job)
        self.receipts.started(job)
        job.waiters.append(waiter)
        if (admission := waiter.admission) is not None:
            admission.joined(job)
        if start := len(self._running) < options.max_concurrent_refreshes:
            self.admit(job)
        return start

    def _renewing(self, waiter: _Waiter, cache: Published) -> Published | tuple[Job, bool, bool, Published]:
        """Serve a token past its renewal time, starting its renewal only if it can be admitted and paid for at once."""
        options = self.options
        if (
            self._active is not None
            or len(self._running) >= options.max_concurrent_refreshes
            or len(self._outstanding) >= options.max_pending_refreshes
            or ((admission := waiter.admission) is not None and admission.shortfall() is not None)
        ):
            return cache
        job = self.next_job()
        return job, self.enlist(job, waiter), False, cache

    def usable(self, context: CredentialContext, *, force: bool) -> Published | None:
        """Return the cached material a caller may use without an acquisition, or None; the caller holds the lock."""
        del context
        if (
            not force
            and (cache := self.cache) is not None
            and (cache.refresh_at is None or self.monotonic() < cache.refresh_at)
        ):
            return cache
        return None

    def over_budget(self, shortfall: tuple[AuthBudgetKind, int, int], refresh_id: str | None = None) -> SDKError:
        """Return the refusal of a call without room for the acquisition it would pay for."""
        kind, limit, used = shortfall
        return AuthBudgetExceededError(
            budget_kind=kind,
            limit=limit,
            used=used,
            state=self.state,
            delivery_state=DeliveryState.NOT_SENT,
            phase="admission",
            provider_id=self.provider_id,
            refresh_id=refresh_id,
        )

    def admit(self, job: Job) -> None:
        """Start a job's provider-owned session, charged to the call of its oldest waiter; the caller holds the lock."""
        options = self.options
        job.session = session = Session.start(options.refresh_timeout, options.phase_timeout, options.clock)
        job.ends = real_end(session.deadline.at + GRACE - self.monotonic())
        job.progress.guard = partial(self._sending, job)
        self._running.add(job)
        if job.exchanges and (charged := job.waiters[0].admission) is not None:
            charged.charge()
            job.charged = charged

    def _sending(self, job: Job) -> bool:
        """Mark a job's token request sent, unless the job already ended; its expiry reads this under the lock."""
        with self.lock:
            if job.outcome is not None:
                return False
            job.progress.sent = True
            return True

    def commit(self, job: Job, outcome: Outcome) -> None:
        """Commit a job's outcome once, publish material before anyone wakes, and deliver it in arrival order."""
        if job.outcome is not None:
            return
        job.outcome = outcome
        self._active = None
        state, failure = self.settle(job, outcome)
        self.receipts.completed(job, RefreshInfo(job.refresh_id, state, int(job.progress.sent), failure))
        waiters, job.waiters = job.waiters, []
        for waiter in waiters:
            waiter.deliver(outcome)
        self.changed.notify_all()

    def settle(self, job: Job, outcome: Outcome) -> tuple[RefreshState, str | None]:
        """Apply a job's outcome to the family, and return the state its receipt reports with its failure."""
        del job
        if isinstance(outcome, Published):
            self.cache = outcome
            self.state = "READY"
            return "READY", None
        assert isinstance(outcome, Failed)
        self.state = outcome.state
        return outcome.state, outcome.error.reason_code

    def leave(self, waiter: _Waiter, job: Job) -> None:
        """Detach a departing waiter, counting the token request its call paid for, if the job sent it by now.

        A job nobody waits for is dropped unless it was already admitted.
        """
        if waiter in job.waiters:
            job.waiters.remove(waiter)
        if (admission := waiter.admission) is not None and job.charged is admission:
            job.charged = None
            if job.progress.sent:
                admission.exchanged()
        if job.session is None and not job.waiters and job.outcome is None:
            self._active = None
            self._outstanding.remove(job)
            self.receipts.dropped(job)
            job.outstanding = False

    def expire(self, job: Job) -> None:
        """Fail an admitted job whose work outlived its session and grace period, without stopping that work."""
        if (left := job.left(self.monotonic())) is not None and not left > 0:
            self.overdue(job)

    def overdue(self, job: Job) -> None:
        """Fail a job that has not ended although its session and grace period have."""
        if job.outcome is None:
            self.commit(job, self.timed_out(job))

    def timed_out(self, job: Job) -> Outcome:
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

    def failed(self, error: BaseException, job: Job) -> Outcome:
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
        job.outstanding = False
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
                queued.outstanding = False

    def exchange_needed(self, version: object) -> bool:
        """Return whether replacing a rejected version needs a new acquisition.

        None is needed while an admitted job runs, unless its session and grace ended, or while a newer usable token is
        held.
        """
        with self.lock:
            if (active := self._active) is not None:
                self.expire(active)
            if (active := self._active) is not None and active.session is not None and active.exchanges:
                return False
            return (
                (cache := self.cache) is None
                or cache.material.version is version
                or (cache.refresh_at is not None and self.monotonic() >= cache.refresh_at)
            )

    def busy(self) -> bool:
        """Return whether an admitted job's work has not returned yet."""
        return bool(self._running)

    def drained(self) -> float | None:
        """Expire running jobs whose session ended, and return how long closing still waits for the others."""
        waits: list[float] = []
        for job in self._outstanding:
            self.expire(job)
            if job.outcome is None and (left := job.left(self.monotonic())) is not None:
                waits.append(left)
        return min(waits) if waits else None


def _detached(coroutine: Coroutine[object, object, None]) -> Task[None]:
    """Run a coroutine as a task that sees none of the starting caller's context variables."""
    from asyncio import get_running_loop  # noqa: PLC0415

    loop = get_running_loop()
    return contextvars.Context().run(lambda: loop.create_task(coroutine))


def _outcome(outcome: Published | Failed) -> BearerCredential:
    if isinstance(outcome, Failed):
        raise clone_error(outcome.error)
    return outcome.material


def _within(context: CredentialContext, started: float, admission: CallAdmission | None, shared: SharedRefresh) -> None:
    """Raise the caller's own error once it was cancelled, ran out of time, or its client closed."""
    if admission is None:
        _waited(context, started, shared)
    else:
        admission.observe()


def _waited(context: CredentialContext, started: float, shared: SharedRefresh) -> float | None:
    """Return how long a caller may wait before checking again, raising once it was cancelled or ran out of time.

    The caller's deadline is read on its own clock, and the time it waited on the provider's.
    """
    if (token := context.cancel_token) is not None and token.cancelled:
        raise RequestCancelledError(source="cancel_token", delivery_state=DeliveryState.NOT_SENT)
    if (deadline := context.deadline) is None:
        return None if token is None else TOKEN_INTERVAL
    if (remaining := deadline.remaining()) <= 0:
        raise DeadlineExceededError(
            deadline_at=deadline.at,
            elapsed=shared.monotonic() - started,
            delivery_state=DeliveryState.NOT_SENT,
            phase="auth",
        )
    return remaining if token is None else min(remaining, TOKEN_INTERVAL)


class SyncSharedRefresh:
    """Runs a family's jobs on a lazily created worker pool and lets synchronous callers wait for them."""

    __slots__ = ("_acquire", "_close", "_executor", "_released", "_releasing", "shared")

    def __init__(self, shared: SharedRefresh, acquire: Callable[[Job], Outcome], close: Callable[[], None]) -> None:
        """Keep the family, how a job acquires, and how the owned transport closes; no thread exists yet."""
        self.shared = shared
        self._acquire = acquire
        self._close = close
        self._executor: ThreadPoolExecutor | None = None
        self._released: WorkFuture[None] | None = None
        self._releasing = False

    def obtain(
        self, context: CredentialContext, *, force: bool, admission: CallAdmission | None = None
    ) -> BearerCredential:
        """Return usable material, joining or starting the family's job and waiting within the caller's limits.

        A job that changed the family instead of acquiring material sends its waiters to claim again within their
        limits; a call reports waiting for another caller's job once.
        """
        shared = self.shared
        started = shared.monotonic()
        announced = False
        while True:
            waiter = SyncWaiter(admission)
            with shared.lock:
                claimed = shared.claim(waiter, context, force=force)
                if isinstance(claimed, Published):
                    return claimed.material
                job, start, joined, serving = claimed
            if start:
                self._start(job)
            if serving is not None:
                with shared.lock:
                    shared.leave(waiter, job)
                return serving.material
            if joined and admission is not None and not announced:
                announced = True
                try:
                    admission.waiting()
                except BaseException:
                    with shared.lock:
                        shared.leave(waiter, job)
                    raise
            if not isinstance(outcome := self._awaited(context, started, waiter, job), Again):
                return _outcome(outcome)
            _within(context, started, admission, shared)

    def _awaited(self, context: CredentialContext, started: float, waiter: SyncWaiter, job: Job) -> Outcome:
        """Wait for a job's outcome within the caller's limits, expiring the job once its session and grace end."""
        shared = self.shared
        admission = waiter.admission
        with shared.lock:
            try:
                while (outcome := waiter.outcome) is None:
                    if admission is None:
                        remaining = _waited(context, started, shared)
                    else:
                        admission.observe()
                        remaining = (
                            TOKEN_INTERVAL
                            if (deadline := context.deadline) is None
                            else min(TOKEN_INTERVAL, deadline.remaining())
                        )
                    if (left := job.left(shared.monotonic())) is not None:
                        remaining = left if remaining is None else min(remaining, left)
                    shared.changed.wait(None if remaining is None else min(remaining, threading.TIMEOUT_MAX))
                    shared.expire(job)
            finally:
                shared.leave(waiter, job)
        return outcome

    def perform(self, job: Job, waiter: SyncWaiter, *, start: bool = True) -> Outcome:
        """Run an explicit operation's admitted job, or join it, and wait for its outcome, bounded by its session."""
        if start:
            self._start(job)
        shared = self.shared
        with shared.lock:
            try:
                while (outcome := waiter.outcome) is None:
                    left = job.left(shared.monotonic())
                    assert left is not None
                    shared.changed.wait(min(left, threading.TIMEOUT_MAX))
                    shared.expire(job)
            finally:
                shared.leave(waiter, job)
        return outcome

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
            release = self._release_due()
        if queued is not None:
            self._start(queued)
        if release:
            self._release()

    def _requested(self) -> WorkFuture[None]:
        """Stop admitting jobs and return the release every closer awaits; the caller holds the lock."""
        from concurrent.futures import Future as WorkFuture  # noqa: PLC0415

        self.shared.closing()
        if (released := self._released) is None:
            released = self._released = WorkFuture()
            released.set_running_or_notify_cancel()
        return released

    def _release_due(self) -> bool:
        """Return whether closing was requested and no admitted job's work runs; the caller holds the lock."""
        return self._released is not None and not self._releasing and not self.shared.busy()

    def _release(self) -> None:
        """Close the owned transport and the worker pool once, then complete the release for every closer.

        Whichever closer or worker claims the release first runs it, and an interruption propagates once settled.
        """
        with self.shared.lock:
            if self._releasing:
                return
            self._releasing = True
        try:
            self._close()
        except Exception as error:  # noqa: BLE001 - Every closer receives the failure through the release.
            self._settle(error)
        except BaseException as error:
            self._settle(error)
            raise
        else:
            self._settle(None)

    def _settle(self, failure: BaseException | None) -> None:
        if (executor := self._executor) is not None:
            executor.shutdown(wait=False)
        shared = self.shared
        with shared.lock:
            shared.lifecycle = "CLOSED"
            shared.changed.notify_all()
        released = self._released
        assert released is not None
        if failure is None:
            released.set_result(None)
        else:
            released.set_exception(failure)

    def _drain(self, released: WorkFuture[None]) -> None:
        """Release once the running jobs return or their sessions end, unless another closer already released."""
        shared = self.shared
        with shared.lock:
            while not released.done() and (left := shared.drained()) is not None:
                shared.changed.wait(min(left, threading.TIMEOUT_MAX))
        self._release()

    def request_close(self) -> WorkFuture[None]:
        """Refuse new jobs and end the queued one without waiting, and return the release every closer awaits.

        An idle provider releases on its worker or a thread of its own, and in the calling thread only when neither
        can start, leaving any failure on the release. Otherwise a thread of its own releases once the running jobs
        return or their sessions end.
        """
        with self.shared.lock:
            first = self._released is None
            released = self._requested()
            busy = self.shared.busy()
        if not first:
            return released
        if busy:
            with suppress(RuntimeError):
                threading.Thread(target=self._drain, args=(released,), name="oauth-release", daemon=True).start()
            return released
        if (executor := self._executor) is not None:
            with suppress(RuntimeError):
                executor.submit(self._release)
                return released
        with suppress(RuntimeError):
            threading.Thread(target=self._release_quietly, name="oauth-release", daemon=True).start()
            return released
        self._release_quietly()
        return released

    def _release_quietly(self) -> None:
        """Release without raising, since every closer receives the failure through the release."""
        with suppress(BaseException):
            self._release()

    def close(self) -> None:
        """Refuse new jobs, end the queued one, wait for running jobs until their sessions end, then release once.

        Work still running past its session does not delay the release, and every close raises its failure.
        """
        with self.shared.lock:
            released = self._requested()
        self._drain(released)
        released.result()


class AsyncSharedRefresh:
    """Runs a family's jobs as tasks on one event loop and lets asyncio callers await them."""

    __slots__ = ("_acquire", "_close", "_finalizer", "_tasks", "shared")

    def __init__(
        self, shared: SharedRefresh, acquire: Callable[[Job], Awaitable[Outcome]], close: Callable[[], Awaitable[None]]
    ) -> None:
        """Keep the family, how a job acquires, and how the owned transport closes; no task exists yet."""
        self.shared = shared
        self._acquire = acquire
        self._close = close
        self._tasks: set[Task[None]] = set()
        self._finalizer: Task[None] | None = None

    async def obtain(
        self, context: CredentialContext, *, force: bool, admission: CallAdmission | None = None
    ) -> BearerCredential:
        """Return usable material, joining or starting the family's job and awaiting it within the caller's limits.

        A job that changed the family instead of acquiring material sends its waiters to claim again within their
        limits; a call reports waiting for another caller's job once.
        """
        from asyncio import get_running_loop, wait  # noqa: PLC0415

        shared = self.shared
        started = shared.monotonic()
        announced = False
        while True:
            waiter = AsyncWaiter(get_running_loop().create_future(), admission)
            with shared.lock:
                claimed = shared.claim(waiter, context, force=force)
                if isinstance(claimed, Published):
                    return claimed.material
                job, start, joined, serving = claimed
            if start:
                self._start(job)
            if serving is not None:
                with shared.lock:
                    shared.leave(waiter, job)
                return serving.material
            future = waiter.future
            try:
                if joined and admission is not None and not announced:
                    announced = True
                    await admission.awaiting()
                while not future.done():
                    await wait({future}, timeout=None if admission is not None else _waited(context, started, shared))
            finally:
                with shared.lock:
                    shared.leave(waiter, job)
            if not isinstance(outcome := future.result(), Again):
                return _outcome(outcome)
            _within(context, started, admission, shared)

    async def perform(self, job: Job, waiter: AsyncWaiter, *, start: bool = True) -> Outcome:
        """Run an explicit operation's admitted job, or join it, and await its outcome, bounded by its session."""
        from asyncio import wait  # noqa: PLC0415

        if start:
            self._start(job)
        try:
            await wait({waiter.future})
        finally:
            with self.shared.lock:
                self.shared.leave(waiter, job)
        return waiter.future.result()

    def _start(self, job: Job) -> None:
        """Run an admitted job as a task outside every caller's context, failing it once its session and grace end."""
        from asyncio import get_running_loop  # noqa: PLC0415

        loop = get_running_loop()
        left = job.left(self.shared.monotonic())
        assert left is not None
        timer = loop.call_later(max(0.0, left), self._overdue, job)
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
            return self.shared.drained()


def _invalidate(shared: SharedRefresh, version: object) -> None:
    """Forget the cached material an attempt was rejected with; another version stays usable."""
    if not isinstance(version, TokenVersion):
        raise AuthConfigurationError(field_path=("version",), condition="invalid_type")
    with shared.lock:
        if (cache := shared.cache) is not None and cache.material.version is version:
            shared.cache = None


def checked_audience(audience: object) -> str | None:
    """Refuse a fixed audience that is neither None nor a nonempty string."""
    if audience is not None and (not isinstance(audience, str) or not audience):
        raise AuthConfigurationError(field_path=("audience",), condition="invalid_value")
    return audience


def checked_context(context: object, audience: str | None) -> CredentialContext:
    """Refuse a context of another type or one requiring an audience other than the configured one."""
    if not isinstance(context, CredentialContext):
        raise AuthConfigurationError(field_path=("context",), condition="invalid_type")
    if context.audience is not None and context.audience != audience:
        raise AuthConfigurationError(field_path=("audience",), condition="audience_mismatch")
    return context


class ClientCredentialsGrant:
    """The client credentials grant's configuration, its request form, and how an answer becomes an outcome."""

    __slots__ = ("audience", "form", "scopes")

    def __init__(self, method: object, scopes: object, audience: object) -> None:
        """Refuse public clients, then validate the requested scopes and the fixed audience."""
        if method == "none":
            raise AuthConfigurationError(field_path=("client_auth_method",), condition="invalid_value")
        self.scopes = checked_scopes(scopes, "scopes")
        self.audience = checked_audience(audience)
        self.form = (
            ("grant_type", "client_credentials"),
            *((("scope", " ".join(self.scopes)),) if self.scopes else ()),
            *((("audience", self.audience),) if self.audience is not None else ()),
        )

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


class SyncTokens(ABC):
    """A synchronous token family of a provider of the SDK: the shared job engine around one token endpoint."""

    __slots__ = ("_audience", "_endpoint", "_refresh")

    def __init__(self, shared: SharedRefresh, endpoint: TokenEndpoint, audience: str | None) -> None:
        """Keep the family, its endpoint, and its audience; nothing runs until the first acquisition."""
        self._endpoint = endpoint
        self._audience = audience
        self._refresh = SyncSharedRefresh(shared, self._acquire, endpoint.close)

    @abstractmethod
    def _acquire(self, job: Job) -> Outcome:
        """Run one admitted job's exchange on a worker and return its outcome."""

    def obtain(self, context: object, *, force: bool, admission: CallAdmission | None = None) -> BearerCredential:
        """Return usable material or acquire it, after checking the caller's context."""
        return self._refresh.obtain(checked_context(context, self._audience), force=force, admission=admission)

    def exchange_needed(self, version: object) -> bool:
        """Return whether replacing a rejected version needs a new acquisition."""
        return self._refresh.shared.exchange_needed(version)

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

    def request_close(self) -> WorkFuture[None]:
        """Start closing the family without waiting."""
        return self._refresh.request_close()


class AsyncTokens(ABC):
    """An asyncio token family of a provider of the SDK, bound to one event loop."""

    __slots__ = ("_audience", "_endpoint", "_refresh")

    def __init__(self, shared: SharedRefresh, endpoint: AsyncTokenEndpoint, audience: str | None) -> None:
        """Keep the family, its endpoint, and its audience; nothing runs until the first acquisition."""
        self._endpoint = endpoint
        self._audience = audience
        self._refresh = AsyncSharedRefresh(shared, self._acquire, endpoint.aclose)

    @abstractmethod
    async def _acquire(self, job: Job) -> Outcome:
        """Run one admitted job's exchange in a task and return its outcome."""

    async def obtain(self, context: object, *, force: bool, admission: CallAdmission | None = None) -> BearerCredential:
        """Return usable material or acquire it, after checking the caller's context and event loop."""
        checked = checked_context(context, self._audience)
        self._endpoint.bind()
        return await self._refresh.obtain(checked, force=force, admission=admission)

    def exchange_needed(self, version: object) -> bool:
        """Return whether replacing a rejected version needs a new acquisition."""
        return self._refresh.shared.exchange_needed(version)

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


class SyncClientCredentials(SyncTokens):
    """A synchronous client credentials family."""

    __slots__ = ("_grant",)

    def __init__(self, options: OAuthProviderOptions, endpoint: TokenEndpoint, grant: ClientCredentialsGrant) -> None:
        """Keep the endpoint and grant; nothing runs until the first acquisition."""
        self._grant = grant
        super().__init__(SharedRefresh(options), endpoint, grant.audience)

    def _acquire(self, job: Job) -> Outcome:
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        exchanged = endpoint.exchange(self._grant.form, job.session, job.progress, "FAILED_NOT_SENT")
        return self._grant.outcome(exchanged, job, self._refresh.shared.provider_id)


class AsyncClientCredentials(AsyncTokens):
    """An asyncio client credentials family."""

    __slots__ = ("_grant",)

    def __init__(
        self, options: OAuthProviderOptions, endpoint: AsyncTokenEndpoint, grant: ClientCredentialsGrant
    ) -> None:
        """Keep the endpoint and grant; nothing runs until the first acquisition."""
        self._grant = grant
        super().__init__(SharedRefresh(options), endpoint, grant.audience)

    async def _acquire(self, job: Job) -> Outcome:
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        exchanged = await endpoint.exchange(self._grant.form, job.session, job.progress, "FAILED_NOT_SENT")
        return self._grant.outcome(exchanged, job, self._refresh.shared.provider_id)
