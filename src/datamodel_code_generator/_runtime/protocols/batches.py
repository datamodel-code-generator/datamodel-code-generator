"""Batches: a caller's items sent through one batch operation in bounded requests, one result per item in input order.

`iterate` returns an iterator that reads, encodes, and sends nothing until it is consumed. It measures each item's
encoded bytes as it reads it, groups items into requests within the server's and the caller's count and byte limits,
keeps at most `parallelism` requests in flight, and returns each item's success, error, or unknown delivery record in
the order the items were given. Item errors are results, never retried; a request is retried only as the shared
policy allows for its operation.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import AsyncIterable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, final

from typing_extensions import Self, TypeVar

from ..client.errors import (
    BudgetExceededError,
    DeliveryState,
    ProtocolConfigurationError,
    RequestEncodingError,
    ResponseDecodeError,
    ResponseTooLargeError,
    SDKError,
    TransportError,
)
from ..client.operations import DATA_ERRORS
from ..client.options import RequestOptions
from ..client.timing import SessionOptions
from ..model_codecs.media import encode_json
from ..model_codecs.unset import UNSET
from .errors import (
    BatchDeliveryUnknownError,
    BatchItemTooLargeError,
    BatchProtocolError,
    ProtocolDataError,
    ProtocolStateError,
    SessionLimitError,
)
from .options import BatchOptions, layered
from .records import BodySelector, BodyTarget, canonical_json
from .values import MISSING, Patch, resolve

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import AsyncIterator, Callable, Iterator
    from concurrent.futures import Future, ThreadPoolExecutor
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import OperationSession
    from ..client.operations import Encoder, OperationPlan
    from ..client.options import RequestValidation
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.wire import WireValue
    from .records import ProtocolProgress
    from .references import OperationRef

__all__ = ("AsyncBatchIterator", "BatchIterator", "BatchPlan", "aiterate_batches", "iterate_batches")

InputT = TypeVar("InputT")
R = TypeVar("R")

MAX_REQUEST_BYTES: Final = 32 * 1024 * 1024
_UNKNOWN: Final = frozenset({DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED})
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299


def _escaped(name: str) -> str:
    return name.replace("~", "~0").replace("/", "~1")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class BatchPlan(Generic[InputT, R]):
    """Everything fixed about one generated batch helper: its operation, how it reads results, and its limits.

    Items are written into the request body's property `items_member`, or are the body itself without one, wrapped in
    the root model `items_root` when the body is one. `results` reads the decoded response's result items, whose wire
    values `results_selector` selects; `success` and `error` read the members of one result item that
    `success_pointer` and `error_pointer` name. `succeeded`, `failed`, and `unknown` build the helper's records. An
    item's ID is read at `input_id` of its wire value and a result's at `result_id`; without them results answer the
    items by position.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[Any, object]
    results: Callable[[Any], Sequence[Any] | None]
    results_selector: BodySelector
    success: Callable[[Any], object]
    success_pointer: str
    error: Callable[[Any], object]
    error_pointer: str
    succeeded: Callable[..., R]
    failed: Callable[..., R]
    unknown: Callable[..., R]
    max_items: int
    max_request_bytes: int
    fingerprint: str
    items_member: str | None = None
    items_root: Callable[[list[Any]], object] | None = None
    input_id: str | None = None
    result_id: str | None = None
    sent: OperationPlan[Any, object] = field(init=False, repr=False)
    pointer: str = field(init=False, repr=False)
    overhead: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Derive the operation that sends a request's items as a wire value, and the bytes of an empty request."""
        from .writes import targeted  # noqa: PLC0415 - Only a plan loads the operation runtime.

        member = self.items_member
        pointer = "" if member is None else f"/{_escaped(member)}"
        empty: WireValue = () if member is None else {member: ()}
        object.__setattr__(self, "sent", targeted(self.call, (BodyTarget(pointer=pointer),))[0])
        object.__setattr__(self, "pointer", pointer)
        object.__setattr__(self, "overhead", len(encode_json(empty)))

    def encoder(self) -> Encoder:
        """Return the encoder of the request body, which encodes each item once."""
        body = self.call.body
        assert body is not None
        encoder = body.media[0].encoder
        assert encoder is not None
        return encoder


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one helper call, each from the first layer that sets it; None removes a limit."""

    batch_size: int = 100
    parallelism: int = 4
    max_items: int | None = 100000
    max_item_bytes: int = 8388608
    max_buffer_bytes: int = 33554432
    raise_on_error: bool = False
    total_timeout: float | None = 600.0
    deadline: Deadline | None = None
    max_network_sends: int | None = 10000
    options: RequestOptions | None = None


_DEFAULTS: Final = _Limits()
_KIND_FIELDS: Final = ("batch_size", "parallelism", "max_items", "max_item_bytes", "max_buffer_bytes", "raise_on_error")


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: BatchPlan[Any, Any],
    batch_options: object,
    options: object,
    session: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    Effective options fixing an idempotency key are refused, since each request of the batch needs a key of its own.
    """

    def invalid(path: tuple[str, ...]) -> ProtocolConfigurationError:
        return ProtocolConfigurationError(
            field_path=path, condition="invalid_value", helper_id=plan.helper_id, operation=plan.operation
        )

    for name, value, kind in (
        ("batch_options", batch_options, BatchOptions),
        ("options", options, RequestOptions),
        ("session_options", session, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise invalid((name,))
    request = options if isinstance(options, RequestOptions) else None
    if core.fixes_key(request):
        raise invalid(("options", "idempotency_key"))
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (batch_options, UNSET if defaults is None else defaults.options)
    sessions = (session, UNSET if defaults is None else defaults.session)
    return _Limits(
        **{name: layered(kinds, name, getattr(_DEFAULTS, name)) for name in _KIND_FIELDS},
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


@dataclass(frozen=True, slots=True)
class _Item:
    """One read item: its input index, encoded wire value and bytes, and its declared ID's wire value and key."""

    index: int
    wire: WireValue
    size: int
    item_id: WireValue | None = None
    key: bytes | None = None


@dataclass(slots=True)
class _Batch(Generic[R]):
    """The items of one request, the bytes its body takes and its items hold in the buffer, and the work sending it."""

    items: tuple[_Item, ...]
    size: int
    held: int
    work: Any = None


@dataclass(frozen=True, slots=True)
class _Raise:
    """A failure to raise once the records queued before it have been returned."""

    error: Exception


@dataclass(frozen=True, slots=True)
class _Done(Generic[R]):
    """What one request gave: a record per item, and the failure that left its delivery unknown, if any.

    `state` is how far an unknown delivery got, and `terminal` says the failure also ends the iteration, such as a
    deadline, a cancellation, or a closed client.
    """

    records: list[R]
    unknown: BaseException | None = None
    state: DeliveryState = DeliveryState.MAYBE_SENT
    terminal: bool = False


def _counters(error: BaseException) -> dict[str, Any]:
    """Return the call identity and counters an SDK failure carries, or nothing for any other failure."""
    if not isinstance(error, SDKError):
        return {}
    return {
        "operation_id": error.operation_id,
        "call_id": error.call_id,
        "parent_session_id": error.parent_session_id,
        "resource_attempt_count": error.resource_attempt_count,
        "redirect_count": error.redirect_count,
        "auth_exchange_count": error.auth_exchange_count,
        "network_send_count": error.network_send_count,
        "network_send_budget_used": error.network_send_budget_used,
        "auth_exchange_budget_used": error.auth_exchange_budget_used,
        "auth_refresh_ids": error.auth_refresh_ids,
        "auth_refresh_pending": error.auth_refresh_pending,
        "wire_send_count": error.wire_send_count,
    }


def _applied(error: Exception) -> bool:
    """Return whether a failure arrived with a success response, which the server applied before its body failed."""
    info = error.info if isinstance(error, (ResponseDecodeError, ResponseTooLargeError)) else None
    return info is not None and _MIN_SUCCESS <= info.status_code <= _MAX_SUCCESS


class _Batches(Generic[R]):
    """What the synchronous and asyncio iterators share: reading and grouping items, sending, and matching results.

    Items are read only while a request slot is free and the buffer has room, and a request cut short by a full buffer
    waits for the oldest request; a request is held until every one of its results was returned, so results come back
    in input order. A failed request is raised in its turn, after which no item is read or sent, and the requests
    already in flight still return their records in order.
    """

    __slots__ = (
        "_arguments",
        "_buffered",
        "_cancelled",
        "_checked",
        "_count",
        "_done",
        "_encoder",
        "_exhausted",
        "_failure",
        "_halted",
        "_item_bytes",
        "_limits",
        "_lock",
        "_mode",
        "_pending",
        "_plan",
        "_read",
        "_ready",
        "_request_bytes",
        "_returned",
        "_session",
        "_slots",
    )

    def __init__(
        self,
        plan: BatchPlan[Any, R],
        arguments: tuple[object, ...],
        limits: _Limits,
        session: OperationSession,
        mode: RequestValidation,
    ) -> None:
        """Keep the plan, the shared arguments, limits, and session; the item byte limit follows the request's."""
        self._plan = plan
        self._arguments = arguments
        self._limits = limits
        self._session = session
        self._mode: RequestValidation = mode
        self._encoder = plan.encoder()
        self._lock = threading.Lock()
        self._count = min(limits.batch_size, plan.max_items)
        self._request_bytes = min(plan.max_request_bytes, MAX_REQUEST_BYTES, limits.max_buffer_bytes)
        self._item_bytes = max(0, min(limits.max_item_bytes, self._request_bytes - plan.overhead))
        self._slots: deque[_Batch[R]] = deque()
        self._ready: deque[R | _Raise] = deque()
        self._pending: deque[_Item] = deque()
        self._failure: Exception | None = None
        self._buffered = 0
        self._read = 0
        self._returned = 0
        self._exhausted = False
        self._halted = False
        self._cancelled = False
        self._checked = False
        self._done = False

    def __repr__(self) -> str:
        """Name the items read and returned only, never their values."""
        return f"{type(self).__name__}(read={self._read}, returned={self._returned})"

    @property
    def progress(self) -> ProtocolProgress:
        """Return the results returned so far and the session's sends."""
        session = self._session
        return MappingProxyType({
            "items": self._returned,
            "network_send_count": session.network_send_count,
            "network_send_budget_used": session.network_send_budget_used,
        })

    def _enter(self, action: str) -> None:
        """Take the iterator for one step, refusing a concurrent one."""
        if not self._lock.acquire(blocking=False):
            plan = self._plan
            raise ProtocolStateError(
                state="iterating",
                action=action,
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self._session.session_id,
            )

    def _check(self, core: ClientCore | AsyncClientCore) -> None:
        """Check the shared arguments once, before anything is sent, as the operation's own calls are checked."""
        if not self._checked:
            self._arguments = core.checked_arguments(self._plan.call, self._arguments, self._limits.options)
            self._checked = True

    def _item(self, value: object, index: int) -> _Item:
        """Encode one item as the request body's codec encodes it, and measure and identify it.

        An item over the byte limit raises BatchItemTooLargeError, and one without a declared ID BatchProtocolError.
        """
        plan, encoder = self._plan, self._encoder
        try:
            if (name := plan.items_member) is None:
                wrap = plan.items_root
                wire = encoder.encode([value] if wrap is None else wrap([value]), self._mode)
            else:
                encoded = encoder.assemble({name: [value]}, self._mode)
                assert isinstance(encoded, Mapping)
                wire = encoded[name]
        except (*DATA_ERRORS, ValueError, TypeError) as error:
            raise RequestEncodingError(
                location=("items", index), operation_id=plan.call.operation_id, cause=error
            ) from None
        assert isinstance(wire, Sequence)
        item = wire[0]
        if (size := len(encode_json(item))) > self._item_bytes:
            raise BatchItemTooLargeError(
                index=index,
                limit=self._item_bytes,
                observed=size,
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self._session.session_id,
            )
        if (pointer := plan.input_id) is None:
            return _Item(index, item, size)
        if (found := resolve(item, pointer)) is MISSING or found is None:
            raise BatchProtocolError(
                indices=(index,),
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self._session.session_id,
            )
        return _Item(index, item, size, found, canonical_json(found))

    def _accepted(self, value: object) -> _Item | None:
        """Take one item read from the source, or stop reading at the item limit or a failure to prepare it.

        The failure is kept for after every earlier item's result.
        """
        index = self._read
        self._read += 1
        if (limit := self._limits.max_items) is not None and index >= limit:
            self._stop_reading(
                SessionLimitError(
                    kind="items",
                    limit=limit,
                    progress=self.progress,
                    helper_id=self._plan.helper_id,
                    operation=self._plan.operation,
                    parent_session_id=self._session.session_id,
                )
            )
            return None
        try:
            return self._item(value, index)
        except Exception as error:  # noqa: BLE001 - The failure is raised in order, after earlier results.
            self._stop_reading(error)
        return None

    def _stop_reading(self, error: Exception | None) -> None:
        """Read no more items, keeping the failure that stopped reading, if any."""
        self._exhausted = True
        if error is not None:
            self._failure = error

    def _halt(self) -> None:
        """Read and send nothing more; the requests in flight still return their records."""
        self._halted = True
        self._exhausted = True
        self._pending.clear()

    def _hold(self, item: _Item) -> None:
        """Keep an item read for the next request, counting its bytes in the buffer."""
        self._pending.append(item)
        self._buffered += item.size

    def _front(self) -> tuple[list[_Item], int, bool]:
        """Return the held items the next request takes, its body bytes, and whether it is complete.

        A request is complete at the item count, or when the next held item exceeds its bytes or repeats an ID in it.
        """
        items: list[_Item] = []
        keys: set[bytes] = set()
        size = self._plan.overhead
        for item in self._pending:
            grown = size + item.size + (1 if items else 0)
            if len(items) >= self._count or grown > self._request_bytes or (item.key is not None and item.key in keys):
                return items, size, True
            items.append(item)
            size = grown
            if item.key is not None:
                keys.add(item.key)
        return items, size, len(items) >= self._count

    def _wants(self) -> bool:
        """Return whether one more item is read: the next request is incomplete, and input and buffer remain."""
        return not self._front()[2] and not self._exhausted and self._buffered < self._limits.max_buffer_bytes

    def _cut(self) -> _Batch[R] | None:
        """Return the next request, or None when no item is held or a request cut short by the buffer should wait."""
        items, size, complete = self._front()
        if not items or (not complete and not self._exhausted and self._slots):
            return None
        for _ in items:
            self._pending.popleft()
        return _Batch(tuple(items), size, sum(item.size for item in items))

    def _request(self, batch: _Batch[R]) -> Callable[[], tuple[tuple[object, ...], object, None]]:
        """Return how a request is built: the shared arguments and a body holding the items' wire values."""
        body = Patch(UNSET, ((self._plan.pointer, tuple(item.wire for item in batch.items)),))
        arguments = self._arguments
        return lambda: (arguments, body, None)

    def _refused(self, error: BudgetExceededError) -> SessionLimitError:
        """Return the session's limit error for a request its session had no send slot for."""
        plan = self._plan
        return SessionLimitError(
            kind="network_sends",
            limit=error.limit,
            progress=self.progress,
            helper_id=plan.helper_id,
            operation=plan.operation,
            operation_id=error.operation_id,
            call_id=error.call_id,
            parent_session_id=error.parent_session_id,
            info=error.info,
            cause=error,
            resource_attempt_count=error.resource_attempt_count,
            redirect_count=error.redirect_count,
            auth_exchange_count=error.auth_exchange_count,
            network_send_count=error.network_send_count,
            network_send_budget_used=error.network_send_budget_used,
            auth_exchange_budget_used=error.auth_exchange_budget_used,
            auth_refresh_ids=error.auth_refresh_ids,
            auth_refresh_pending=error.auth_refresh_pending,
            wire_send_count=error.wire_send_count,
        )

    def _failed(self, batch: _Batch[R], error: Exception) -> _Done[R]:
        """Return the records of a request whose delivery stays unknown, or raise its failure.

        A request may have reached the server when its failure says so, and its server applied it when a success
        response's body failed; a failure other than a transport one, such as a deadline, a cancellation, or a closed
        client, also ends the iteration after the records.
        """
        if isinstance(error, BudgetExceededError) and error.budget_kind == "parent_network":
            raise self._refused(error) from None
        if isinstance(error, SDKError) and (state := getattr(error, "delivery_state", None)) in _UNKNOWN:
            return self._unknown_records(batch, error, state, terminal=not isinstance(error, TransportError))
        if _applied(error):
            return self._unknown_records(batch, error, DeliveryState.RESPONSE_STARTED, terminal=False)
        raise error

    def _unknown_records(
        self, batch: _Batch[R], error: BaseException, state: DeliveryState, *, terminal: bool
    ) -> _Done[R]:
        """Return an unknown delivery record per item of a request, with the failure that left it unknown."""
        plan = self._plan
        records = [plan.unknown(index=item.index, item_id=item.item_id, response=None) for item in batch.items]
        return _Done(records, error, state, terminal)

    def _mismatch(self, indices: tuple[int, ...], info: ResponseInfo) -> BatchProtocolError:
        plan = self._plan
        return BatchProtocolError(
            indices=indices,
            location=plan.results_selector,
            helper_id=plan.helper_id,
            operation=plan.operation,
            info=info,
        )

    def _matched(self, items: tuple[_Item, ...], found: Sequence[WireValue], info: ResponseInfo) -> list[int]:
        """Return the position of each item's result: by position, or by the declared IDs of items and results.

        Another count, or a result whose ID is missing, unknown, or repeated, or an item without a result, raises
        BatchProtocolError naming the items concerned.
        """
        plan = self._plan
        everything = tuple(item.index for item in items)
        if (pointer := plan.result_id) is None:
            if len(found) != len(items):
                raise self._mismatch(everything, info)
            return list(range(len(items)))
        owners = {item.key: item.index for item in items}
        positions: dict[bytes, int] = {}
        for position, result in enumerate(found):
            if (value := resolve(result, pointer)) is MISSING or (key := canonical_json(value)) not in owners:
                raise self._mismatch(everything, info)
            if key in positions:
                raise self._mismatch((owners[key],), info)
            positions[key] = position
        if missing := tuple(item.index for item in items if item.key not in positions):
            raise self._mismatch(missing, info)
        return [positions[item.key] for item in items if item.key is not None]

    def _received(
        self,
        batch: _Batch[R],
        data: object,
        wire: WireValue,
        _content: bytes,
        info: ResponseInfo,
        _url: str,
        _managed: frozenset[str],
    ) -> _Done[R]:
        """Return a record per item of a request: its success or error member, matched with the item.

        A result item must carry exactly one of the success and error members, neither missing nor null.
        """
        plan = self._plan
        typed = plan.results(data)
        found = resolve(wire, plan.results_selector.pointer)
        if typed is None or found is MISSING or found is None:
            raise ProtocolDataError(
                condition="missing" if found is MISSING else "null",
                location=plan.results_selector,
                helper_id=plan.helper_id,
                operation=plan.operation,
                info=info,
            )
        assert isinstance(found, Sequence)
        records: list[R] = []
        for item, position in zip(batch.items, self._matched(batch.items, found, info), strict=True):
            result = found[position]
            succeeded = _present(result, plan.success_pointer)
            if succeeded == _present(result, plan.error_pointer):
                raise self._mismatch((item.index,), info)
            build, read, name = (
                (plan.succeeded, plan.success, "value") if succeeded else (plan.failed, plan.error, "error")
            )
            records.append(
                build(index=item.index, item_id=item.item_id, response=info, **{name: read(typed[position])})
            )
        return _Done(records)

    def _unknown(self, batch: _Batch[R], done: _Done[R]) -> BatchDeliveryUnknownError[R]:
        """Return the error of a request whose delivery stays unknown, holding its records."""
        plan, error = self._plan, done.unknown
        assert error is not None
        return BatchDeliveryUnknownError(
            partial_results=tuple(done.records),
            batch_indices=tuple(item.index for item in batch.items),
            delivery_state=done.state,
            helper_id=plan.helper_id,
            operation=plan.operation,
            cause=error,
            **_counters(error),
        )

    def _settled(self, batch: _Batch[R], done: _Done[R]) -> None:
        """Release a returned request's buffer bytes and queue its records, or raise its unknown delivery.

        An unknown delivery raised, or one whose failure ends the iteration, stops reading and sending; the failure
        that ends it is raised after the request's records.
        """
        self._buffered -= batch.held
        if (error := done.unknown) is None:
            self._ready.extend(done.records)
            return
        if self._limits.raise_on_error:
            self._halt()
            raise self._unknown(batch, done)
        self._ready.extend(done.records)
        if done.terminal and isinstance(error, Exception):
            self._halt()
            self._ready.append(_Raise(error))

    def _next_ready(self) -> R | None:
        """Return the next queued record, raise a queued failure, or return None when nothing is queued."""
        if not self._ready:
            return None
        if isinstance(item := self._ready.popleft(), _Raise):
            raise item.error
        self._returned += 1
        return item

    def _ended(self) -> Exception | None:
        """End iteration once no request is left, returning the failure to raise after every result.

        After a cancellation, items read and not sent are reported by a ProtocolStateError of state `cancelled`.
        """
        self._done = True
        failure, self._failure = self._failure, None
        if failure is None and self._cancelled:
            plan = self._plan
            failure = ProtocolStateError(
                state="cancelled",
                action="next",
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self._session.session_id,
            )
        return failure


@final
class BatchIterator(_Batches[R]):
    """The results of a batch helper's items in input order, sent in bounded requests on a private thread pool.

    `close` stops new requests, cancels those not started, and waits for those in flight; it never closes the
    client. Iterating from two threads at once raises ProtocolStateError.
    """

    __slots__ = ("_core", "_executor", "_source")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        core: ClientCore,
        plan: BatchPlan[Any, R],
        arguments: tuple[object, ...],
        source: Iterator[object],
        limits: _Limits,
        session: OperationSession,
    ) -> None:
        """Keep the client core the requests are sent through and the caller's items."""
        mode = core.request_validation(limits.options, plan.call.operation_id)
        super().__init__(plan, arguments, limits, session, mode)
        self._core = core
        self._source = source
        self._executor: ThreadPoolExecutor | None = None

    def _take(self) -> _Item | None:
        """Read the next item, or return None once reading stopped."""
        try:
            value = next(self._source)
        except StopIteration:
            self._stop_reading(None)
            return None
        except Exception as error:  # noqa: BLE001 - The caller's failure is raised after earlier results.
            self._stop_reading(error)
            return None
        return self._accepted(value)

    def _group(self) -> _Batch[R] | None:
        """Read items until the next request is complete, input ends, or the buffer is full, and cut it."""
        while self._wants():
            if (item := self._take()) is not None:
                self._hold(item)
        return self._cut()

    def _send(self, batch: _Batch[R]) -> _Done[R]:
        """Send one request as a child call of the session and return its records."""
        plan = self._plan
        try:
            return self._core.execute_page(
                plan,
                plan.sent,
                self._request(batch),
                partial(self._received, batch),
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )
        except Exception as error:  # noqa: BLE001 - An unknown delivery becomes records; anything else is raised.
            return self._failed(batch, error)

    def _head(self) -> _Batch[R] | None:
        """Submit requests while a slot is free and items are ready, then return the oldest one once it completed."""
        while not self._halted and len(self._slots) < self._limits.parallelism and (batch := self._group()) is not None:
            if (executor := self._executor) is None:
                from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - Only a sent batch needs threads.

                executor = self._executor = ThreadPoolExecutor(
                    max_workers=self._limits.parallelism, thread_name_prefix="batch"
                )
            batch.work = executor.submit(self._send, batch)
            self._slots.append(batch)
        if not self._slots:
            return None
        work: Future[_Done[R]] = self._slots[0].work
        work.exception()
        return self._slots.popleft()

    def _finish(self) -> None:
        """Stop sending: cancel requests not started, wait for those in flight, and drop their results."""
        self._done = True
        self._halt()
        slots, self._slots = self._slots, deque()
        for batch in slots:
            batch.work.cancel()
        if (executor := self._executor) is not None:
            self._executor = None
            executor.shutdown(wait=True, cancel_futures=True)

    def _step(self) -> R:
        self._check(self._core)
        while (record := self._next_ready()) is None:
            if self._done:
                raise StopIteration
            try:
                batch = self._head()
            except BaseException:
                self._finish()
                raise
            if batch is None:
                self._finish()
                if (failure := self._ended()) is not None:
                    raise failure
                raise StopIteration
            work: Future[_Done[R]] = batch.work
            if (error := work.exception()) is not None:
                self._halt()
                raise error
            self._settled(batch, work.result())
        return record

    def __iter__(self) -> Self:
        """Return this iterator."""
        return self

    def __next__(self) -> R:
        """Return the next item's result, sending requests as slots free up; errors are raised in input order."""
        self._enter("next")
        try:
            return self._step()
        finally:
            self._lock.release()

    def close(self) -> None:
        """Stop iterating: no new request is sent and later results are dropped; closing again does nothing."""
        self._enter("close")
        try:
            self._finish()
            self._ready.clear()
        finally:
            self._lock.release()

    def __enter__(self) -> Self:
        """Return this iterator, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the iterator."""
        self.close()


@final
class AsyncBatchIterator(_Batches[R]):
    """The results of an asyncio batch helper's items in input order, sent in bounded requests as tasks.

    A cancelled step cancels the requests in flight, whose records become unknown deliveries, and nothing is read or
    sent after it. `aclose` stops new requests and cancels and awaits those in flight; it never closes the client.
    Iterating from two tasks at once raises ProtocolStateError.
    """

    __slots__ = ("_aiterator", "_core", "_iterator", "_source")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        core: AsyncClientCore,
        plan: BatchPlan[Any, R],
        arguments: tuple[object, ...],
        source: Iterable[object] | AsyncIterable[object],
        limits: _Limits,
        session: OperationSession,
    ) -> None:
        """Keep the client core the requests are sent through and the caller's items, synchronous or asynchronous."""
        mode = core.request_validation(limits.options, plan.call.operation_id)
        super().__init__(plan, arguments, limits, session, mode)
        self._core = core
        self._source = source
        self._iterator: Iterator[object] | None = None
        self._aiterator: AsyncIterator[object] | None = None

    async def _value(self) -> object:
        """Return the next item of the caller's items, raising StopAsyncIteration after the last one."""
        if self._iterator is None and self._aiterator is None:
            source = self._source
            if isinstance(source, AsyncIterable):
                self._aiterator = aiter(source)
            else:
                self._iterator = iter(source)
        if (iterator := self._iterator) is not None:
            try:
                return next(iterator)
            except StopIteration:
                raise StopAsyncIteration from None
        assert self._aiterator is not None
        return await anext(self._aiterator)

    async def _take(self) -> _Item | None:
        """Read the next item, or return None once reading stopped."""
        try:
            value = await self._value()
        except StopAsyncIteration:
            self._stop_reading(None)
            return None
        except Exception as error:  # noqa: BLE001 - The caller's failure is raised after earlier results.
            self._stop_reading(error)
            return None
        return self._accepted(value)

    async def _group(self) -> _Batch[R] | None:
        """Read items until the next request is complete, input ends, or the buffer is full, and cut it."""
        while self._wants():
            if (item := await self._take()) is not None:
                self._hold(item)
        return self._cut()

    async def _send(self, batch: _Batch[R]) -> _Done[R]:
        """Send one request as a child call of the session and return its records."""
        plan = self._plan
        try:
            return await self._core.execute_page(
                plan,
                plan.sent,
                self._request(batch),
                partial(self._received, batch),
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )
        except Exception as error:  # noqa: BLE001 - An unknown delivery becomes records; anything else is raised.
            return self._failed(batch, error)

    async def _head(self) -> _Batch[R] | None:
        """Start requests while a slot is free and items are ready, then return the oldest one once it completed."""
        from asyncio import ensure_future, wait  # noqa: PLC0415 - Only an asyncio iterator starts tasks.

        while not self._halted and len(self._slots) < self._limits.parallelism and (batch := await self._group()):
            batch.work = task = ensure_future(self._send(batch))
            task.add_done_callback(_retrieved)
            self._slots.append(batch)
        if not self._slots:
            return None
        await wait((self._slots[0].work,))
        return self._slots.popleft()

    def _abort(self) -> None:
        """Stop reading and sending at a cancellation, cancelling every request in flight; their tasks stay to await."""
        self._halt()
        self._cancelled = True
        for batch in self._slots:
            batch.work.cancel()

    async def _finish(self) -> None:
        """Stop sending, then cancel and await the requests in flight, dropping their results."""
        from asyncio import gather  # noqa: PLC0415 - Only an asyncio iterator awaits tasks.

        self._done = True
        self._halt()
        slots, self._slots = self._slots, deque()
        for batch in slots:
            batch.work.cancel()
        await gather(*(batch.work for batch in slots), return_exceptions=True)

    def _outcome(self, batch: _Batch[R]) -> _Done[R]:
        """Return a completed request's records: unknown deliveries when it was cancelled, or raise its failure."""
        from asyncio import CancelledError  # noqa: PLC0415 - Only an asyncio iterator cancels tasks.

        task: Task[_Done[R]] = batch.work
        if task.cancelled():
            return self._unknown_records(batch, CancelledError(), DeliveryState.MAYBE_SENT, terminal=False)
        if (error := task.exception()) is not None:
            self._halt()
            raise error
        return task.result()

    async def _step(self) -> R:
        self._check(self._core)
        while (record := self._next_ready()) is None:
            if self._done:
                raise StopAsyncIteration
            try:
                batch = await self._head()
            except BaseException:
                self._abort()
                raise
            if batch is None:
                await self._finish()
                if (failure := self._ended()) is not None:
                    raise failure
                raise StopAsyncIteration
            self._settled(batch, self._outcome(batch))
        return record

    def __aiter__(self) -> Self:
        """Return this iterator."""
        return self

    async def __anext__(self) -> R:
        """Return the next item's result, sending requests as slots free up; errors are raised in input order."""
        self._enter("next")
        try:
            return await self._step()
        finally:
            self._lock.release()

    async def aclose(self) -> None:
        """Stop iterating: no new request is sent and later results are dropped; closing again does nothing."""
        self._enter("aclose")
        try:
            await self._finish()
            self._ready.clear()
        finally:
            self._lock.release()

    async def __aenter__(self) -> Self:
        """Return this iterator, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the iterator."""
        await self.aclose()


def _present(result: WireValue, pointer: str) -> bool:
    """Return whether a result item carries a member, neither missing nor null."""
    return (value := resolve(result, pointer)) is not MISSING and value is not None


def _retrieved(task: Task[Any]) -> None:
    """Retrieve a dropped request's outcome, so that its failure is never reported as unretrieved."""
    from asyncio import CancelledError  # noqa: PLC0415 - Only an asyncio iterator drops tasks.

    with suppress(CancelledError):
        task.exception()


def _session(limits: _Limits) -> OperationSession:
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only an iterating helper loads the call runtime.

    return OperationSession(
        total_timeout=limits.total_timeout, deadline=limits.deadline, max_network_sends=limits.max_network_sends
    )


def _items(plan: BatchPlan[Any, Any], items: object, kinds: tuple[type, ...]) -> None:
    if not isinstance(items, kinds) or isinstance(items, (str, bytes, bytearray, Mapping)):
        raise ProtocolConfigurationError(
            field_path=("items",), condition="invalid_value", helper_id=plan.helper_id, operation=plan.operation
        )


def iterate_batches(  # noqa: PLR0913
    core: ClientCore,
    plan: BatchPlan[InputT, R],
    arguments: tuple[object, ...],
    items: Iterable[InputT],
    *,
    batch_options: object = None,
    options: object = None,
    session_options: object = None,
) -> BatchIterator[R]:
    """Return an iterator of a helper's results in a session of its own; it sends nothing until it is iterated."""
    _items(plan, items, (Iterable,))
    limits = _limits(core, plan, batch_options, options, session_options)
    return BatchIterator(core, plan, arguments, iter(items), limits, _session(limits))


def aiterate_batches(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: BatchPlan[InputT, R],
    arguments: tuple[object, ...],
    items: Iterable[InputT] | AsyncIterable[InputT],
    *,
    batch_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncBatchIterator[R]:
    """Return an asyncio iterator of a helper's results in a session of its own; it sends nothing until iterated."""
    _items(plan, items, (Iterable, AsyncIterable))
    limits = _limits(core, plan, batch_options, options, session_options)
    return AsyncBatchIterator(core, plan, arguments, items, limits, _session(limits))
