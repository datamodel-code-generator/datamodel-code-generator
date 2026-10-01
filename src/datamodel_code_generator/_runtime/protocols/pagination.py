"""Cursor, offset, and page-number pagination: pages, the pagers over them, and the session their child calls share.

A pager sends nothing until it is iterated and fetches a page only once the previous one is consumed. Every page is
read, decoded, and closed inside its own child call, so abandoning a pager holds no response.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.errors import BudgetExceededError, ProtocolConfigurationError, ProtocolSizeError
from ..client.options import RequestOptions
from ..client.responses import ResponseInfo
from ..client.timing import SessionOptions
from ..model_codecs.unset import UNSET, Unset
from .errors import PaginationCycleError, ProtocolDataError, ProtocolStateError, SessionLimitError
from .options import PaginationOptions
from .records import (
    BodySelector,
    BodyTarget,
    Continuation,
    HeaderSelector,
    ParameterTarget,
    ProtocolProgress,
    QuerystringTarget,
    RequestTarget,
    Sealed,
    Selector,
    canonical_json,
    continuation_json,
    frozen_wire,
    record_instance,
)
from .values import MISSING, Missing, Patch, resolve

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Iterator, Sequence
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .references import OperationRef

__all__ = (
    "AsyncPager",
    "CountPlan",
    "CursorPlan",
    "Page",
    "PageBinding",
    "Pager",
    "PaginationPlan",
    "afirst_page",
    "afollowing_page",
    "aiterate_pages",
    "first_page",
    "following_page",
    "iterate_pages",
)

T = TypeVar("T")
P = TypeVar("P")
R = TypeVar("R")
V = TypeVar("V")
T_co = TypeVar("T_co", covariant=True, default=object)
P_co = TypeVar("P_co", covariant=True, default=object)

_DOT_SEGMENTS: Final = (".", "..")
_BOOLEANS: Final = MappingProxyType({"true": True, "false": False})


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CursorPlan:
    """How a helper reads the next cursor from a page, where it writes it, and which values end the traversal.

    An absent cursor, JSON null, or the empty string ends it only when its end condition says so; `end_values` are
    compared by their canonical JSON, so their JSON types must match exactly.
    """

    read: Selector
    write: RequestTarget
    end_missing: bool = False
    end_null: bool = False
    end_values: tuple[WireValue, ...] = ()
    empty_string_ends: bool = False
    kind: Literal["cursor"] = field(default="cursor", init=False)
    ends: frozenset[bytes] = field(init=False)

    def __post_init__(self) -> None:
        """Keep the canonical JSON of each end value."""
        object.__setattr__(self, "ends", frozenset(canonical_json(value) for value in self.end_values))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CountPlan:
    """How a helper counts the offset or page number each request after the first writes, and what ends the traversal.

    The first request is the caller's, at the position the caller's own value for the target gives, or else at
    `first`; the request after a page writes the page's position advanced by `step`, or by its item count when `step`
    is None. The page is the last when `has_more` reads false, or once the items before the next position reach what
    `total` reads: the offsets past `first` for an offset, the items delivered since the start for a page number, which
    also ends at a page without items. A header spells a JSON boolean or a signed decimal count.
    """

    kind: Literal["offset", "page"]
    write: RequestTarget
    first: int
    step: int | None
    has_more: Selector | None = None
    total: Selector | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PageBinding:
    """A value every request after the first writes into a target: a literal, or what a selector reads.

    A selector reads the `initial` page's response, or the `previous` one's; without one, `literal` is written,
    frozen as a wire value.
    """

    target: RequestTarget
    source: Literal["initial", "previous"] = "previous"
    selector: Selector | None = None
    literal: WireValue = None

    def __post_init__(self) -> None:
        """Freeze the literal."""
        object.__setattr__(self, "literal", frozen_wire(self.literal))


def _position(call: OperationPlan[P, object], location: str, name: str) -> int:
    """Return the argument position of a declared parameter, matching a header's name without regard to case."""

    def key(value: str) -> str:
        return value.lower() if location == "header" else value

    wanted = key(name)
    return next(
        index
        for index, spec in enumerate(call.parameters)
        if spec.plan.location == location and key(spec.plan.name) == wanted
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationPlan(Generic[T, P]):
    """Everything fixed about one generated pagination helper: its identity, call, items, continuation, and bindings.

    Pages after the first call `continued`, the same operation taking each binding's value and then the cursor or
    position as wire values: a parameter's replaces its argument, and a querystring property or a JSON body member
    is written into the caller's encoded value. They skip the schema and argument checks, since the server chose them.
    `headers` and `queries` name the header, cookie, and query parameters it writes, which a call's options must not
    patch, and `dotted` the writes of read values into path parameters, which must not be dot segments.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[P, object]
    items: Callable[[P], Sequence[T] | None]
    items_selector: BodySelector
    continuation: CursorPlan | CountPlan
    fingerprint: str
    bindings: tuple[PageBinding, ...] = ()
    writes: tuple[tuple[int | None, str | None], ...] = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)
    dotted: tuple[tuple[int, Selector], ...] = field(init=False)
    continued: OperationPlan[P, object] = field(init=False)

    def __post_init__(self) -> None:
        """Find where each binding and then the continuation is written, and derive the operation of later pages.

        A write is a parameter's argument position with no pointer, a querystring's with a pointer into its value, or
        no position with a pointer into the JSON body. Every media of a body written to is JSON.
        """
        from .writes import PatchedMedia, PatchedParameter  # noqa: PLC0415 - Only a plan loads the operation runtime.

        call = self.call
        parameters = list(call.parameters)
        writes: list[tuple[int | None, str | None]] = []
        headers: set[str] = set()
        queries: set[str] = set()
        patched = False
        rule = self.continuation
        read = rule.read if isinstance(rule, CursorPlan) else None
        sources = (*((binding.target, binding.selector) for binding in self.bindings), (rule.write, read))
        for target, _ in sources:
            if isinstance(target, BodyTarget):
                writes.append((None, target.pointer))
                patched = True
                continue
            if isinstance(target, QuerystringTarget):
                position, pointer = _position(call, "querystring", target.name), target.pointer
            else:
                position, pointer = _position(call, target.location, target.name), None
                if (location := target.location) == "header":
                    headers.add(target.name.lower())
                elif location == "cookie":
                    headers.add("cookie")
                elif location == "query":
                    queries.add(target.name)
            spec = parameters[position]
            parameters[position] = (
                replace(spec, encoder=None)
                if pointer is None
                else PatchedParameter(plan=spec.plan, encoder=spec.encoder)
            )
            writes.append((position, pointer))
        body = call.body
        if patched:
            assert body is not None
            body = replace(
                body,
                media=tuple(
                    PatchedMedia(media_type=media.media_type, kind=media.kind, encoder=media.encoder)
                    for media in body.media
                ),
            )
        object.__setattr__(self, "writes", tuple(writes))
        object.__setattr__(self, "headers", frozenset(headers))
        object.__setattr__(self, "queries", frozenset(queries))
        object.__setattr__(
            self,
            "dotted",
            tuple(
                (index, selector)
                for index, (target, selector) in enumerate(sources)
                if isinstance(target, ParameterTarget) and target.location == "path" and selector is not None
            ),
        )
        object.__setattr__(self, "continued", replace(call, parameters=tuple(parameters), body=body, checks=()))


@dataclass(frozen=True, slots=True)
class _Request:
    """The typed arguments, body, and body media type the first page of a helper call was requested with."""

    arguments: tuple[object, ...]
    body: object
    media_type: str | MediaSelector | None


class _History:
    """The continuations the pages of one line of a helper call returned, by digest, and the index of its last page.

    Pages continued from an earlier page than the last one start a line of their own with the history up to it.
    """

    __slots__ = ("last", "seen")

    def __init__(self, seen: dict[bytes, int], last: int) -> None:
        """Keep the index of the page that first returned each continuation, and the last page's index."""
        self.seen = seen
        self.last = last


@dataclass(frozen=True, slots=True)
class _Link:
    """Where a page stands among the pages of one helper call, and what continuing after it takes.

    `digest` is the SHA-256 of the canonical JSON of the page's continuation, None on the last page, and `seen` the
    index of an earlier page that returned the same continuation. `bound` holds the values of the helper's bindings
    the next request writes, none on the last page. Only the page itself keeps its link, so a pager holds its last
    link and the digests of its line, never the earlier pages.
    """

    fingerprint: str
    request: _Request
    index: int
    items: int
    cursor: WireValue
    bound: tuple[WireValue, ...]
    digest: bytes | None
    seen: int | None
    response: ResponseInfo
    history: _History


@final
class Page(Sealed, Generic[T_co, P_co]):
    """One page of a helper: its items, its decoded response, the response's metadata, and the continuation after it.

    A page equals only itself, and its representation names its item count but never its data or items. Only a page
    a helper fetched can be continued with its `next_page`.
    """

    __slots__ = ("_continuation", "_data", "_items", "_link", "_response")

    _items: tuple[T_co, ...]
    _data: P_co
    _response: ResponseInfo
    _continuation: Continuation | None
    _link: _Link | None

    def __init__(
        self,
        *,
        items: tuple[T_co, ...],
        data: P_co,
        response: ResponseInfo,
        continuation: Continuation | None = None,
    ) -> None:
        """Keep the page's values; the items must be a tuple and the continuation a Continuation or None."""
        record_instance(items, tuple, "items must be a tuple")
        record_instance(response, ResponseInfo, "response must be a ResponseInfo")
        record_instance(continuation, (Continuation, type(None)), "continuation must be a Continuation or None")
        for name, value in (
            ("_items", items),
            ("_data", data),
            ("_response", response),
            ("_continuation", continuation),
            ("_link", None),
        ):
            object.__setattr__(self, name, value)

    @property
    def items(self) -> tuple[T_co, ...]:
        """Return the page's items."""
        return self._items

    @property
    def data(self) -> P_co:
        """Return the page's decoded response."""
        return self._data

    @property
    def response(self) -> ResponseInfo:
        """Return the metadata of the page's response."""
        return self._response

    @property
    def continuation(self) -> Continuation | None:
        """Return the position after this page, or None on the last page."""
        return self._continuation

    def __repr__(self) -> str:
        """Name the item count, the response metadata, and the continuation's kind only."""
        return f"Page(items={len(self._items)}, response={self._response!r}, continuation={self._continuation!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one helper call, each from the first layer that sets it; None removes a limit."""

    max_pages: int | None = 1000
    max_items: int | None = 100000
    max_page_bytes: int = 8 * 1024 * 1024
    max_cursor_bytes: int = 64 * 1024
    total_timeout: float | None = 300.0
    deadline: Deadline | None = None
    max_network_sends: int | None = 3000
    options: RequestOptions | None = None


_DEFAULTS: Final = _Limits()


def _first(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return a limit from the first options layer that sets it, or its default."""
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def _invalid(plan: PaginationPlan[T, P], path: tuple[str, ...]) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition="invalid_value", helper_id=plan.helper_id, operation=plan.operation
    )


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: PaginationPlan[T, P],
    pagination_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    Effective options fixing an idempotency key, the client's or a view's included, are refused, since every page is
    a request of its own that needs its own key, and so are the call's header or query patches of a parameter the
    helper writes, which would replace the values it writes.
    """
    for name, value, kind in (
        ("pagination_options", pagination_options, PaginationOptions),
        ("options", options, RequestOptions),
        ("session_options", session_options, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise _invalid(plan, (name,))
    request = options if isinstance(options, RequestOptions) else None
    if core.fixes_key(request):
        raise _invalid(plan, ("options", "idempotency_key"))
    if request is not None:
        for name, _ in request.headers:
            if name.lower() in plan.headers:
                raise _invalid(plan, ("options", "headers", name))
        for name, _ in request.query:
            if name in plan.queries:
                raise _invalid(plan, ("options", "query", name))
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (pagination_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    return _Limits(
        max_pages=_first(kinds, "max_pages", _DEFAULTS.max_pages),
        max_items=_first(kinds, "max_items", _DEFAULTS.max_items),
        max_page_bytes=_first(kinds, "max_page_bytes", _DEFAULTS.max_page_bytes),
        max_cursor_bytes=_first(kinds, "max_cursor_bytes", _DEFAULTS.max_cursor_bytes),
        total_timeout=_first(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=_first(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=_first(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


def _data_error(
    plan: PaginationPlan[T, P],
    info: ResponseInfo,
    condition: Literal["missing", "null", "type", "value", "malformed", "inconsistent"],
    location: Selector,
) -> ProtocolDataError:
    return ProtocolDataError(
        condition=condition, location=location, helper_id=plan.helper_id, operation=plan.operation, info=info
    )


def _absence(value: WireValue | Missing) -> Literal["missing", "null"]:
    return "missing" if value is MISSING else "null"


def _unfit(value: WireValue | Missing) -> Literal["missing", "null", "type"]:
    return "type" if value is not MISSING and value is not None else _absence(value)


def _selected(plan: PaginationPlan[T, P], read: Selector, wire: WireValue, info: ResponseInfo) -> WireValue | Missing:
    """Return what a selector reads from a page, or MISSING; every occurrence of a header is an array of them.

    A header selected once that the response repeats is refused.
    """
    if isinstance(read, BodySelector):
        return resolve(wire, read.pointer)
    if isinstance(read, HeaderSelector):
        values = info.headers.get_all(read.name)
        if read.occurrence == "all":
            return tuple(values) if values else MISSING
        if len(values) > 1:
            raise _data_error(plan, info, "malformed", read)
        return values[0] if values else MISSING
    return info.status_code


def _cursor(
    plan: PaginationPlan[T, P], rule: CursorPlan, wire: WireValue, info: ResponseInfo, limit: int
) -> WireValue | Missing:
    """Return the cursor a page gives, or MISSING when an end condition ends the traversal at this page.

    A cursor over its size limit, in UTF-8 bytes for a string and canonical JSON bytes otherwise, is refused.
    """
    read = rule.read
    value = _selected(plan, read, wire, info)
    if value is MISSING or value is None:
        if rule.end_missing if value is MISSING else rule.end_null:
            return MISSING
        raise _data_error(plan, info, _absence(value), read)
    encoded = canonical_json(value)
    if (isinstance(value, str) and not value and rule.empty_string_ends) or encoded in rule.ends:
        return MISSING
    if (size := len(value.encode()) if isinstance(value, str) else len(encoded)) > limit:
        raise ProtocolSizeError(
            kind="cursor",
            limit=limit,
            observed=size,
            unit="bytes",
            helper_id=plan.helper_id,
            operation=plan.operation,
            info=info,
        )
    return value


def _more(plan: PaginationPlan[T, P], read: Selector, wire: WireValue, info: ResponseInfo) -> bool:
    """Return whether a page says more pages follow: a JSON boolean, or a header spelling one."""
    value = _selected(plan, read, wire, info)
    if isinstance(read, HeaderSelector) and isinstance(value, str):
        value = _BOOLEANS.get(value, value)
    if not isinstance(value, bool):
        raise _data_error(plan, info, _unfit(value), read)
    return value


def _total(plan: PaginationPlan[T, P], read: Selector, wire: WireValue, info: ResponseInfo) -> int:
    """Return the total item count a page gives: a nonnegative JSON integer, or a header of signed decimal digits."""
    value = _selected(plan, read, wire, info)
    if (
        isinstance(read, HeaderSelector)
        and isinstance(value, str)
        and (digits := value.removeprefix("-")).isascii()
        and digits.isdigit()
    ):
        try:
            value = int(value)
        except ValueError:
            raise _data_error(plan, info, "value", read) from None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _data_error(plan, info, _unfit(value), read)
    if value < 0:
        raise _data_error(plan, info, "value", read)
    return value


class _Walk(Generic[T, P]):
    """The pages of one helper call: its request, limits, session, the last page's link, and the items delivered.

    Its continuations are remembered by digest along the line of pages it extends.
    """

    __slots__ = ("delivered", "limits", "link", "plan", "request", "session", "start")

    def __init__(
        self, plan: PaginationPlan[T, P], request: _Request, limits: _Limits, link: _Link | None = None
    ) -> None:
        """Start after a page's link, or before the first page."""
        self.plan = plan
        self.request = request
        self.limits = limits
        self.link = link
        self.delivered = 0 if link is None else link.items
        self.session: OperationSession | None = None
        self.start: int | None = None

    def progress(self) -> ProtocolProgress:
        """Return the pages fetched, the items delivered, and the session's sends so far."""
        session = self.session
        return MappingProxyType({
            "pages": 0 if (link := self.link) is None else link.index + 1,
            "items": self.delivered,
            "network_send_count": 0 if session is None else session.network_send_count,
            "network_send_budget_used": 0 if session is None else session.network_send_budget_used,
        })

    def session_id(self) -> str | None:
        """Return the identifier of the walk's session once it started."""
        return None if (session := self.session) is None else session.session_id

    def limit(
        self, limit: int, kind: Literal["items", "pages", "network_sends"], refused: BudgetExceededError | None = None
    ) -> SessionLimitError:
        """Return the error of a session limit reached while pages remain, with the child call a send was refused."""
        plan = self.plan
        if refused is None:
            return SessionLimitError(
                kind=kind,
                limit=limit,
                progress=self.progress(),
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self.session_id(),
            )
        return SessionLimitError(
            kind=kind,
            limit=limit,
            progress=self.progress(),
            helper_id=plan.helper_id,
            operation=plan.operation,
            operation_id=refused.operation_id,
            call_id=refused.call_id,
            parent_session_id=refused.parent_session_id,
            info=refused.info,
            cause=refused,
            resource_attempt_count=refused.resource_attempt_count,
            redirect_count=refused.redirect_count,
            auth_exchange_count=refused.auth_exchange_count,
            network_send_count=refused.network_send_count,
            network_send_budget_used=refused.network_send_budget_used,
            auth_exchange_budget_used=refused.auth_exchange_budget_used,
            auth_refresh_ids=refused.auth_refresh_ids,
            auth_refresh_pending=refused.auth_refresh_pending,
            wire_send_count=refused.wire_send_count,
        )

    def state_error(self, action: str, state: str) -> ProtocolStateError:
        """Return the refusal of an action the pager's state forbids."""
        plan = self.plan
        return ProtocolStateError(
            state=state,
            action=action,
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self.session_id(),
        )

    def ready(self) -> OperationSession | None:
        """Return the session the next page is fetched in, or None when no page remains.

        A limit reached while pages remain and a continuation seen before raise instead, sending nothing; the session
        starts with the first fetch.
        """
        link = self.link
        if link is not None and link.digest is None:
            return None
        limits = self.limits
        if (limit := limits.max_items) is not None and self.delivered >= limit:
            if link is None:
                return None
            raise self.limit(limit, "items")
        if link is not None and (limit := limits.max_pages) is not None and link.index + 1 >= limit:
            raise self.limit(limit, "pages")
        if link is not None and link.seen is not None:
            plan = self.plan
            rule = plan.continuation
            assert isinstance(rule, CursorPlan)
            raise PaginationCycleError(
                page_index=link.index,
                first_seen_page_index=link.seen,
                location=rule.read,
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self.session_id(),
                info=link.response,
            )
        if (session := self.session) is None:
            from ..client.logical import OperationSession  # noqa: PLC0415 - Only a fetch loads the call runtime.

            session = self.session = OperationSession(
                total_timeout=limits.total_timeout,
                deadline=limits.deadline,
                max_network_sends=limits.max_network_sends,
            )
        if (limit := session.send_limit) is not None and session.network_send_budget_used >= limit:
            raise self.limit(limit, "network_sends")
        return session

    def operation(self) -> OperationPlan[P, object]:
        """Return the operation the next page calls: the helper's for the first page, its continued one after it.

        The first page of an offset or page-number helper reads the position the caller's own value for its target
        gives, from the parameter or the JSON body media the position is written to.
        """
        plan = self.plan
        if self.link is not None:
            return plan.continued
        if isinstance(plan.continuation, CursorPlan):
            return plan.call
        from .writes import ReadMedia, ReadParameter  # noqa: PLC0415 - Only a plan loads the operation runtime.

        call = plan.call
        read = self.started
        if (position := plan.writes[-1][0]) is None:
            body = call.body
            assert body is not None
            media = tuple(
                ReadMedia(media_type=media.media_type, kind=media.kind, encoder=media.encoder, read=read)
                for media in body.media
            )
            return replace(call, body=replace(body, media=media))
        parameters = list(call.parameters)
        spec = parameters[position]
        parameters[position] = ReadParameter(plan=spec.plan, encoder=spec.encoder, read=read)
        return replace(call, parameters=tuple(parameters))

    def started(self, wire: WireValue) -> None:
        """Start at the position the caller's first request sends, refusing one that is not an integer.

        The value is read where the position is written; a querystring or body without that member sends none.
        """
        plan = self.plan
        pointer = plan.writes[-1][1]
        if (value := wire if pointer is None else resolve(wire, pointer)) is MISSING:
            return
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolDataError(
                condition="type", location=plan.continuation.write, helper_id=plan.helper_id, operation=plan.operation
            )
        self.start = value

    def advance(self, rule: CountPlan, count: int, wire: WireValue, info: ResponseInfo) -> int | Missing:
        """Return the offset or page number the request after a page writes, or MISSING when the page is the last.

        The first page is at the walk's start. A page number ends at a page without items once a total counts the
        items, since none of the pages after it can bring the total nearer. A page that continues without items while
        each step counts the items would ask for its own position again, so it is refused.
        """
        plan, link = self.plan, self.link
        position = (rule.first if self.start is None else self.start) if link is None else cast("int", link.cursor)
        following = position + (count if (step := rule.step) is None else step)
        if (read := rule.has_more) is not None:
            more = _more(plan, read, wire, info)
        else:
            assert rule.total is not None
            total = _total(plan, rule.total, wire, info)
            if rule.kind == "offset":
                more = following - rule.first < total
            else:
                more = bool(count) and count + (0 if link is None else link.items) < total
        if not more:
            return MISSING
        if step is None and not count:
            raise _data_error(plan, info, "inconsistent", plan.items_selector)
        return following

    def next_request(self) -> tuple[tuple[object, ...], object]:
        """Return the next page's arguments and body: the first page's, with the last page's writes.

        Each binding's value and then the cursor or position replace a parameter's argument, or are written by pointer
        into the caller's querystring or body.
        """
        request = self.request
        if (link := self.link) is None:
            return request.arguments, request.body
        arguments = list(request.arguments)
        patches: dict[int | None, list[tuple[str, WireValue]]] = {}
        for (position, pointer), value in zip(self.plan.writes, (*link.bound, link.cursor), strict=True):
            if pointer is None:
                arguments[cast("int", position)] = value
            else:
                patches.setdefault(position, []).append((pointer, value))
        body = request.body
        for position, writes in patches.items():
            if position is None:
                body = Patch(body, tuple(writes))
            else:
                arguments[position] = Patch(arguments[position], tuple(writes))
        return tuple(arguments), body

    def bound(self, wire: WireValue, info: ResponseInfo) -> tuple[WireValue, ...]:
        """Return the values of the helper's bindings the request after a page writes, refusing a missing one.

        An `initial` binding reads the first page and keeps its value; a `previous` one reads every page.
        """
        plan, link = self.plan, self.link
        values: list[WireValue] = []
        for index, binding in enumerate(plan.bindings):
            if (selector := binding.selector) is None:
                values.append(binding.literal)
            elif binding.source == "initial" and link is not None:
                values.append(link.bound[index])
            elif (value := _selected(plan, selector, wire, info)) is MISSING:
                raise _data_error(plan, info, "missing", selector)
            else:
                values.append(value)
        return tuple(values)

    def build(
        self, data: P, wire: WireValue, info: ResponseInfo
    ) -> tuple[Page[T, P], WireValue | Missing, tuple[WireValue, ...]]:
        """Return a decoded page, what the next request writes, and its bindings' values, refusing bad items and ends.

        The items are checked in the wire value before the page's accessor reads them from the decoded one; a value of
        another type fails its response's validation first, in every mode. What the next request writes is the cursor
        or position, MISSING after the last page. The bindings are read only when a page follows, and a dot segment
        read for a path parameter is refused.
        """
        plan = self.plan
        selector = plan.items_selector
        if (
            (selected := resolve(wire, selector.pointer)) is MISSING
            or selected is None
            or (native := plan.items(data)) is None
        ):
            raise _data_error(plan, info, _absence(selected), selector)
        items = tuple(native)
        rule = plan.continuation
        cursor = (
            _cursor(plan, rule, wire, info, self.limits.max_cursor_bytes)
            if isinstance(rule, CursorPlan)
            else self.advance(rule, len(items), wire, info)
        )
        if isinstance(cursor, Missing):
            return Page(items=items, data=data, response=info), cursor, ()
        bound = self.bound(wire, info)
        written = (*bound, cursor)
        for index, read in plan.dotted:
            if written[index] in _DOT_SEGMENTS:
                raise _data_error(plan, info, "value", read)
        continuation = Continuation(kind=rule.kind, value=cursor)
        page = Page(items=items, data=data, response=info, continuation=continuation)
        return page, cursor, bound

    def record(self, page: Page[T, P], cursor: WireValue | Missing, bound: tuple[WireValue, ...]) -> Page[T, P]:
        """Link a fetched page after the last one, noting whether its continuation was seen before on its line."""
        previous = self.link
        index = 0 if previous is None else previous.index + 1
        continuation = page.continuation
        digest = None if continuation is None else sha256(continuation_json(continuation)).digest()
        history = _line(previous)
        first = None if digest is None else history.seen.setdefault(digest, index)
        history.last = index
        link = self.link = _Link(
            self.plan.fingerprint,
            self.request,
            index,
            len(page.items) + (0 if previous is None else previous.items),
            None if isinstance(cursor, Missing) else cursor,
            bound,
            digest,
            None if first == index else first,
            page.response,
            history,
        )
        object.__setattr__(page, "_link", link)  # noqa: PLC2801 - Link the sealed page once, as its helper fetched it.
        return page

    @contextmanager
    def mapped(self) -> Generator[None, None, None]:
        """Raise a child call's refusal for want of a session send slot as the session's limit error."""
        try:
            yield
        except BudgetExceededError as error:
            if error.budget_kind != "parent_network":
                raise
            raise self.limit(error.limit, "network_sends", error) from None


def _line(link: _Link | None) -> _History:
    """Return the history a page after a link extends: its line's, or a copy up to it for an earlier page."""
    if link is None:
        return _History({}, -1)
    history = link.history
    if history.last == link.index:
        return history
    return _History({digest: index for digest, index in history.seen.items() if index <= link.index}, link.index)


def _page_link(plan: PaginationPlan[T, P], page: object) -> _Link:
    """Return the link of a page this helper fetched, refusing any other page."""
    link = page._link if isinstance(page, Page) else None  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    if link is None or link.fingerprint != plan.fingerprint:
        raise ProtocolConfigurationError(
            field_path=("page",), condition="binding_mismatch", helper_id=plan.helper_id, operation=plan.operation
        )
    return link


class _State(Enum):
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


class _Traversal(Generic[T, P]):
    """What the synchronous and asyncio pagers share: their state, mode, buffered page, and walk."""

    __slots__ = ("_items", "_lock", "_mode", "_position", "_state", "_walk")

    def __init__(self, walk: _Walk[T, P]) -> None:
        """Keep the walk; nothing is sent until the first page is requested."""
        self._walk = walk
        self._lock = threading.Lock()
        self._mode: Literal["items", "pages"] | None = None
        self._state = _State.READY
        self._items: tuple[T, ...] = ()
        self._position = 0

    @property
    def progress(self) -> ProtocolProgress:
        """Return the pages fetched, the items delivered, and the session's sends so far."""
        return self._walk.progress()

    def _enter(self, mode: Literal["items", "pages"], action: str) -> None:
        """Take the pager for one step in a mode, fixing the mode on first use.

        A concurrent step, a closed or failed pager, and then a step in the other mode are refused, in that order.
        """
        if not self._lock.acquire(blocking=False):
            raise self._walk.state_error(action, "fetching")
        if (state := self._state) is not _State.READY:
            self._lock.release()
            raise self._walk.state_error(action, state.value)
        if (current := self._mode) is not None and current != mode:
            self._lock.release()
            raise self._walk.state_error(action, current)
        self._mode = mode

    def _select(self, mode: Literal["items", "pages"], action: str) -> None:
        """Fix the pager's mode without stepping it."""
        self._enter(mode, action)
        self._lock.release()

    def _ready(self) -> OperationSession | None:
        """Return the session of the next fetch, or None once no page remains; a refusal fails the pager."""
        try:
            return self._walk.ready()
        except (SessionLimitError, PaginationCycleError):
            self._state = _State.FAILED
            raise

    def _take(self) -> T:
        """Return the next buffered item, refusing it once the delivered items reached the item limit."""
        walk = self._walk
        if (limit := walk.limits.max_items) is not None and walk.delivered >= limit:
            self._state = _State.FAILED
            raise walk.limit(limit, "items")
        walk.delivered += 1
        self._position += 1
        return self._items[self._position - 1]

    def _buffer(self, page: Page[T, P]) -> None:
        self._items = page.items
        self._position = 0

    def _close(self, action: str) -> None:
        if not self._lock.acquire(blocking=False):
            raise self._walk.state_error(action, "fetching")
        self._state = _State.CLOSED
        self._lock.release()


@final
class _PageIterator(Generic[R]):
    """Iterate over a pager's pages through the pager itself."""

    __slots__ = ("_step",)

    def __init__(self, step: Callable[[], R]) -> None:
        """Keep the pager's step that returns its next page."""
        self._step = step

    def __iter__(self) -> Self:
        """Return this iterator."""
        return self

    def __next__(self) -> R:
        """Return the next page."""
        return self._step()


@final
class _AsyncPageIterator(Generic[R]):
    """Iterate over an asyncio pager's pages through the pager itself."""

    __slots__ = ("_step",)

    def __init__(self, step: Callable[[], Awaitable[R]]) -> None:
        """Keep the pager's step that returns its next page."""
        self._step = step

    def __aiter__(self) -> Self:
        """Return this iterator."""
        return self

    async def __anext__(self) -> R:
        """Return the next page."""
        return await self._step()


@final
class Pager(_Traversal[T, P]):
    """Iterate over a helper's items, or over its pages with `iter_pages`, fetching each page only when it is needed.

    A pager is one helper session. After a failure it refuses to continue, and mixing item and page iteration or
    consuming it from two threads at once raises ProtocolStateError.
    """

    __slots__ = ("_core",)

    def __init__(self, core: ClientCore, walk: _Walk[T, P]) -> None:
        """Keep the client core the pages are fetched through."""
        super().__init__(walk)
        self._core = core

    def __iter__(self) -> Self:
        """Iterate over the items."""
        self._select("items", "iter")
        return self

    def __next__(self) -> T:
        """Return the next item, fetching the next page once the current one is consumed."""
        self._enter("items", "next")
        try:
            while self._position >= len(self._items):
                if (session := self._ready()) is None:
                    raise StopIteration
                self._buffer(self._fetch(session))
            return self._take()
        finally:
            self._lock.release()

    def iter_pages(self) -> Iterator[Page[T, P]]:
        """Return an iterator over the pages, each fetched when it is requested."""
        self._select("pages", "iter_pages")
        return _PageIterator(self._page)

    def _page(self) -> Page[T, P]:
        self._enter("pages", "next")
        try:
            if (session := self._ready()) is None:
                raise StopIteration
            page = self._fetch(session)
            self._walk.delivered += len(page.items)
            return page
        finally:
            self._lock.release()

    def _fetch(self, session: OperationSession) -> Page[T, P]:
        walk = self._walk
        try:
            with walk.mapped():
                return walk.record(*_fetch(self._core, walk, session))
        except BaseException:
            self._state = _State.FAILED
            raise

    def close(self) -> None:
        """Stop the pager; later steps raise ProtocolStateError, and closing again does nothing."""
        self._close("close")

    def __enter__(self) -> Self:
        """Return this pager, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the pager."""
        self.close()


@final
class AsyncPager(_Traversal[T, P]):
    """Iterate over a helper's items with asyncio, or over its pages with `iter_pages`, fetching pages when needed.

    An asyncio pager is one helper session. After a failure it refuses to continue, and mixing item and page iteration
    or consuming it from two tasks at once raises ProtocolStateError.
    """

    __slots__ = ("_core",)

    def __init__(self, core: AsyncClientCore, walk: _Walk[T, P]) -> None:
        """Keep the asyncio client core the pages are fetched through."""
        super().__init__(walk)
        self._core = core

    def __aiter__(self) -> Self:
        """Iterate over the items."""
        self._select("items", "aiter")
        return self

    async def __anext__(self) -> T:
        """Return the next item, fetching the next page once the current one is consumed."""
        self._enter("items", "anext")
        try:
            while self._position >= len(self._items):
                if (session := self._ready()) is None:
                    raise StopAsyncIteration
                self._buffer(await self._fetch(session))
            return self._take()
        finally:
            self._lock.release()

    def iter_pages(self) -> AsyncIterator[Page[T, P]]:
        """Return an asyncio iterator over the pages, each fetched when it is requested."""
        self._select("pages", "iter_pages")
        return _AsyncPageIterator(self._page)

    async def _page(self) -> Page[T, P]:
        self._enter("pages", "anext")
        try:
            if (session := self._ready()) is None:
                raise StopAsyncIteration
            page = await self._fetch(session)
            self._walk.delivered += len(page.items)
            return page
        finally:
            self._lock.release()

    async def _fetch(self, session: OperationSession) -> Page[T, P]:
        walk = self._walk
        try:
            with walk.mapped():
                return walk.record(*await _afetch(self._core, walk, session))
        except BaseException:
            self._state = _State.FAILED
            raise

    async def aclose(self) -> None:
        """Stop the pager; later steps raise ProtocolStateError, and closing again does nothing."""
        self._close("aclose")

    async def __aenter__(self) -> Self:
        """Return this pager, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the pager."""
        await self.aclose()


def _fetch(
    core: ClientCore, walk: _Walk[T, P], session: OperationSession
) -> tuple[Page[T, P], WireValue | Missing, tuple[WireValue, ...]]:
    """Fetch the walk's next page as a child call of its session."""
    request, limits = walk.request, walk.limits
    return core.execute_page(
        walk.plan,
        walk.operation(),
        walk.next_request,
        walk.build,
        body=request.body,
        media_type=request.media_type,
        options=limits.options,
        session=session,
        max_page_bytes=limits.max_page_bytes,
    )


async def _afetch(
    core: AsyncClientCore, walk: _Walk[T, P], session: OperationSession
) -> tuple[Page[T, P], WireValue | Missing, tuple[WireValue, ...]]:
    """Fetch the walk's next page as an asyncio child call of its session."""
    request, limits = walk.request, walk.limits
    return await core.execute_page(
        walk.plan,
        walk.operation(),
        walk.next_request,
        walk.build,
        body=request.body,
        media_type=request.media_type,
        options=limits.options,
        session=session,
        max_page_bytes=limits.max_page_bytes,
    )


def first_page(  # noqa: PLR0913
    core: ClientCore,
    plan: PaginationPlan[T, P],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> Page[T, P]:
    """Fetch the first page of a helper in a session of its own."""
    walk = _Walk(
        plan, _Request(arguments, body, media_type), _limits(core, plan, pagination_options, options, session_options)
    )
    if (session := walk.ready()) is None:
        raise walk.limit(0, "items")
    with walk.mapped():
        return walk.record(*_fetch(core, walk, session))


async def afirst_page(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PaginationPlan[T, P],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> Page[T, P]:
    """Fetch the first page of a helper with asyncio, in a session of its own."""
    walk = _Walk(
        plan, _Request(arguments, body, media_type), _limits(core, plan, pagination_options, options, session_options)
    )
    if (session := walk.ready()) is None:
        raise walk.limit(0, "items")
    with walk.mapped():
        return walk.record(*await _afetch(core, walk, session))


def iterate_pages(  # noqa: PLR0913
    core: ClientCore,
    plan: PaginationPlan[T, P],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> Pager[T, P]:
    """Return a pager over a helper's items, checking its options now; it sends nothing until it is iterated."""
    limits = _limits(core, plan, pagination_options, options, session_options)
    return Pager(core, _Walk(plan, _Request(arguments, body, media_type), limits))


def aiterate_pages(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PaginationPlan[T, P],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncPager[T, P]:
    """Return an asyncio pager over a helper's items, checking its options now; it sends nothing until iterated."""
    limits = _limits(core, plan, pagination_options, options, session_options)
    return AsyncPager(core, _Walk(plan, _Request(arguments, body, media_type), limits))


def following_page(  # noqa: PLR0913
    core: ClientCore,
    plan: PaginationPlan[T, P],
    page: object,
    *,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> Page[T, P] | None:
    """Fetch the page after a page this helper fetched, in a session of its own, or return None after the last one.

    The pages before it count toward the item and page limits, and a continuation they returned is a cycle.
    """
    limits = _limits(core, plan, pagination_options, options, session_options)
    link = _page_link(plan, page)
    walk = _Walk(plan, link.request, limits, link)
    if (session := walk.ready()) is None:
        return None
    with walk.mapped():
        return walk.record(*_fetch(core, walk, session))


async def afollowing_page(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PaginationPlan[T, P],
    page: object,
    *,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> Page[T, P] | None:
    """Fetch the page after a page this helper fetched with asyncio, or return None after the last one.

    The pages before it count toward the item and page limits, and a continuation they returned is a cycle.
    """
    limits = _limits(core, plan, pagination_options, options, session_options)
    link = _page_link(plan, page)
    walk = _Walk(plan, link.request, limits, link)
    if (session := walk.ready()) is None:
        return None
    with walk.mapped():
        return walk.record(*await _afetch(core, walk, session))
