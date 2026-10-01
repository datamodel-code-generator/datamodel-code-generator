"""Circuit breaking of grouped operations: the builtin memory stores and each call's admission and outcome.

A client with an enabled breaker admits every call of an operation with a circuit group through its store before any
credential, limiter permit, or send, and records the call's outcome once, after its retries: transport failures of the
connect, read, and write phases and final 500, 502, 503, and 504 responses are failures, other responses successes,
and everything else, including 429 and cancellation, neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from itertools import count
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

from ..client.errors import (
    CircuitStoreError,
    HTTPStatusError,
    ProtocolConfigurationError,
    TransportError,
    UnexpectedStatusError,
    add_secondary,
)
from ..client.urls import request_origin
from ..model_codecs.unset import Unset
from .errors import CircuitOpenError
from .options import (
    CircuitKey,
    CircuitOutcome,
    CircuitPermit,
    CircuitSnapshot,
    CircuitState,
    Origin,
    ProtocolSecurityContext,
    ResolvedCircuitBreakerOptions,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from typing_extensions import TypeVar

    from .options import AsyncCircuitStore, CircuitStore, ProtocolClientOptions

    R = TypeVar("R")

__all__ = ("AsyncMemoryCircuitStore", "Breaker", "MemoryCircuitStore", "breaker")

ANONYMOUS: Final = "anonymous"
_FAILURE_STATUSES: Final = frozenset({500, 502, 503, 504})
_TOO_MANY_REQUESTS: Final = 429
_FAILED_PHASES: Final = frozenset({"connect", "read", "write"})
_METHODS: Final = ("admit", "record", "reset", "snapshot")
_Action: TypeAlias = Literal["admit", "record", "reset"]


def now() -> float:
    """Return the monotonic time circuits are measured in; every SDK reading of circuit time goes through here."""
    return monotonic()


def outcome(error: BaseException | None, status: int | None) -> CircuitOutcome:
    """Classify a completed call by its failure, or by its final response status when it returned one."""
    if isinstance(error, TransportError):
        return "failure" if error.phase in _FAILED_PHASES else "neutral"
    if isinstance(error, (HTTPStatusError, UnexpectedStatusError)):
        status = error.status_code
    elif error is not None:
        return "neutral"
    if status in _FAILURE_STATUSES:
        return "failure"
    return "neutral" if status is None or status == _TOO_MANY_REQUESTS else "success"


def _checked_key(value: object) -> None:
    if not isinstance(value, CircuitKey):
        raise ProtocolConfigurationError(field_path=("key",), condition="invalid_value")


def _checked_permit(value: object) -> None:
    if not isinstance(value, CircuitPermit):
        raise ProtocolConfigurationError(field_path=("permit",), condition="invalid_value")


@dataclass(slots=True)
class _Circuit:
    options: ResolvedCircuitBreakerOptions | None = None
    state: CircuitState = "closed"
    failures: int = 0
    retry_at: float | None = None
    generation: int = 0
    probe: str | None = field(default=None, repr=False)

    def move(self, state: CircuitState, retry_at: float | None = None) -> None:
        self.state, self.retry_at, self.probe = state, retry_at, None
        self.generation += 1
        if state == "closed":
            self.failures = 0


class _Circuits:
    __slots__ = ("_circuits", "_ids", "_lock")

    def __init__(self) -> None:
        self._circuits: dict[CircuitKey, _Circuit] = {}
        self._ids = count(1)
        self._lock = Lock()

    def admit(self, key: CircuitKey, now: float, options: ResolvedCircuitBreakerOptions) -> CircuitPermit:
        _checked_key(key)
        with self._lock:
            circuit = self._circuits.setdefault(key, _Circuit())
            circuit.options = options
            probe = circuit.state != "closed"
            if circuit.state == "open":
                assert circuit.retry_at is not None
                if now < circuit.retry_at:
                    raise CircuitOpenError(key=key, retry_at=circuit.retry_at)
                circuit.move("half_open")
            elif circuit.probe is not None:
                raise CircuitOpenError(key=key, retry_at=now)
            permit_id = str(next(self._ids))
            if probe:
                circuit.probe = permit_id
            return CircuitPermit(key=key, generation=circuit.generation, probe=probe, permit_id=permit_id)

    def record(self, permit: CircuitPermit, result: CircuitOutcome, now: float) -> None:
        _checked_permit(permit)
        with self._lock:
            circuit = self._circuits.get(permit.key)
            if circuit is None or circuit.options is None or circuit.generation != permit.generation:
                return
            if permit.probe:
                if circuit.probe != permit.permit_id:
                    return
                circuit.probe = None
            if result == "success":
                circuit.failures = 0
                if permit.probe:
                    circuit.move("closed")
            elif result == "failure":
                circuit.failures += 1
                if permit.probe or circuit.failures >= circuit.options.failure_threshold:
                    circuit.move("open", now + circuit.options.cooldown)

    def reset(self, key: CircuitKey) -> None:
        _checked_key(key)
        with self._lock:
            self._circuits.setdefault(key, _Circuit()).move("closed")

    def snapshot(self, key: CircuitKey) -> CircuitSnapshot:
        _checked_key(key)
        with self._lock:
            circuit = self._circuits.get(key) or _Circuit()
            return CircuitSnapshot(
                state=circuit.state,
                consecutive_failures=circuit.failures,
                retry_at=circuit.retry_at,
                generation=circuit.generation,
            )


class MemoryCircuitStore:
    """Keep the circuits of one client in this process; a store passed to several clients shares their circuits.

    Each operation is atomic under one lock. A circuit opens after the threshold of consecutive failures, admits one
    probe once its cooldown passed, and closes when that probe succeeds; every transition and reset advances its
    generation, so outcomes of calls admitted before it are ignored.
    """

    __slots__ = ("_circuits",)

    def __init__(self) -> None:
        """Create an empty store; no thread, clock, or network resource is created."""
        self._circuits = _Circuits()

    def admit(self, key: CircuitKey, *, now: float, options: ResolvedCircuitBreakerOptions) -> CircuitPermit:
        """Admit one call, taking the single half-open probe slot when due, or raise CircuitOpenError."""
        return self._circuits.admit(key, now, options)

    def record(self, permit: CircuitPermit, outcome: CircuitOutcome, *, now: float) -> None:
        """Apply a call's outcome once, ignoring a permit of another generation."""
        self._circuits.record(permit, outcome, now)

    def reset(self, key: CircuitKey) -> None:
        """Close a circuit and advance its generation, so permits admitted before cannot change it."""
        self._circuits.reset(key)

    def snapshot(self, key: CircuitKey) -> CircuitSnapshot:
        """Return the circuit's current state; an unknown circuit is closed at generation 0."""
        return self._circuits.snapshot(key)


class AsyncMemoryCircuitStore:
    """Apply the same in-process circuits through asynchronous methods that do only memory work."""

    __slots__ = ("_circuits",)

    def __init__(self) -> None:
        """Create an empty store; no task, clock, or network resource is created."""
        self._circuits = _Circuits()

    async def admit(self, key: CircuitKey, *, now: float, options: ResolvedCircuitBreakerOptions) -> CircuitPermit:
        """Admit one call, taking the single half-open probe slot when due, or raise CircuitOpenError."""
        return self._circuits.admit(key, now, options)

    async def record(self, permit: CircuitPermit, outcome: CircuitOutcome, *, now: float) -> None:
        """Apply a call's outcome once, ignoring a permit of another generation."""
        self._circuits.record(permit, outcome, now)

    async def reset(self, key: CircuitKey) -> None:
        """Close a circuit and advance its generation, so permits admitted before cannot change it."""
        self._circuits.reset(key)

    async def snapshot(self, key: CircuitKey) -> CircuitSnapshot:
        """Return the circuit's current state; an unknown circuit is closed at generation 0."""
        return self._circuits.snapshot(key)


def _store_failure(action: _Action, error: Exception) -> Exception:
    return (
        error
        if isinstance(error, (CircuitOpenError, CircuitStoreError))
        else CircuitStoreError(action=action, cause=error)
    )


def _stored(action: _Action, call: Callable[[], R]) -> R:
    try:
        return call()
    except Exception as error:  # noqa: BLE001
        raise _store_failure(action, error) from None


async def _astored(action: _Action, call: Callable[[], Awaitable[R]]) -> R:
    try:
        return await call()
    except Exception as error:  # noqa: BLE001
        raise _store_failure(action, error) from None


def _permit(value: object, key: CircuitKey) -> CircuitPermit:
    if not isinstance(value, CircuitPermit) or value.key != key:
        raise CircuitStoreError(action="admit")
    return value


class Breaker:
    """The circuit breaking of one client and its views: its store, its limits, and its credential partition."""

    __slots__ = ("_keys", "_options", "_partition", "_store")

    def __init__(
        self,
        store: CircuitStore | AsyncCircuitStore,
        options: ResolvedCircuitBreakerOptions,
        partition: str | None,
    ) -> None:
        """Borrow the store; keys of the client's partition, or of anonymous use without one, are made on first use."""
        self._store = store
        self._options = options
        self._partition = partition
        self._keys: dict[tuple[tuple[str, str, int], str], CircuitKey] = {}

    def key(self, origin: tuple[str, str, int], group: str) -> CircuitKey:
        """Return the key of a group's circuit at an origin, for this client's partition."""
        if (key := self._keys.get((origin, group))) is None:
            scheme, host, port = origin
            key = self._keys[origin, group] = CircuitKey(
                origin=Origin(scheme=scheme, host=host, port=port),
                credential_partition=self._partition or ANONYMOUS,
                group=group,
            )
        return key

    def call_key(self, url: str, group: str, *, authenticated: bool) -> CircuitKey:
        """Return the key of a call to a URL, refusing an authenticated call when the client has no partition."""
        if authenticated and self._partition is None:
            raise ProtocolConfigurationError(field_path=("protocols", "security"), condition="security_partition")
        return self.key(request_origin(url), group)

    def admit(self, key: CircuitKey) -> CircuitPermit:
        """Admit a call through a synchronous store."""
        store, options = cast("CircuitStore", self._store), self._options
        return _permit(_stored("admit", lambda: store.admit(key, now=now(), options=options)), key)

    async def aadmit(self, key: CircuitKey) -> CircuitPermit:
        """Admit a call through an asynchronous store."""
        store, options = cast("AsyncCircuitStore", self._store), self._options
        return _permit(await _astored("admit", lambda: store.admit(key, now=now(), options=options)), key)

    def record(self, permit: CircuitPermit, error: BaseException | None, status: int | None) -> None:
        """Record a call's outcome; a store failure is the call's secondary error, or raised after a success."""
        store, result = cast("CircuitStore", self._store), outcome(error, status)
        try:
            _stored("record", lambda: store.record(permit, result, now=now()))
        except CircuitStoreError as failure:
            if error is None:
                raise
            add_secondary(error, failure)

    async def arecord(self, permit: CircuitPermit, error: BaseException | None, status: int | None) -> None:
        """Record a call's outcome through an asynchronous store, with the same failure rules."""
        store, result = cast("AsyncCircuitStore", self._store), outcome(error, status)
        try:
            await _astored("record", lambda: store.record(permit, result, now=now()))
        except CircuitStoreError as failure:
            if error is None:
                raise
            add_secondary(error, failure)

    def reset(self, key: CircuitKey) -> None:
        """Reset a circuit through a synchronous store."""
        store = cast("CircuitStore", self._store)
        _stored("reset", lambda: store.reset(key))

    async def areset(self, key: CircuitKey) -> None:
        """Reset a circuit through an asynchronous store."""
        store = cast("AsyncCircuitStore", self._store)
        await _astored("reset", lambda: store.reset(key))


def _capable(store: object, *, asynchronous: bool) -> None:
    if not all(
        callable(method := getattr(store, name, None)) and iscoroutinefunction(method) is asynchronous
        for name in _METHODS
    ):
        raise ProtocolConfigurationError(field_path=("protocols", "circuit_store"), condition="wrong_capability")


def breaker(protocols: ProtocolClientOptions, *, asynchronous: bool) -> Breaker | None:
    """Return a client's breaker, or None unless its protocol settings enable one; a missing store is created."""
    if isinstance(circuit := protocols.circuit, Unset) or not circuit.enabled:
        return None
    store = protocols.circuit_store
    if store is None or isinstance(store, Unset):
        store = AsyncMemoryCircuitStore() if asynchronous else MemoryCircuitStore()
    else:
        _capable(store, asynchronous=asynchronous)
    security = protocols.security
    partition = security.credential_partition if isinstance(security, ProtocolSecurityContext) else None
    return Breaker(store, circuit.resolved(), partition)
