"""Cursor, offset, page-number, next-URL, and Link pagination: pages, their pagers, and the session their calls share.

A pager sends nothing until it is iterated and fetches a page only once the previous one is consumed. Every page is
read, decoded, and closed inside its own child call, so abandoning a pager holds no response.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.errors import (
    BudgetExceededError,
    ProtocolConfigurationError,
    ProtocolSizeError,
    RequestEncodingError,
    SDKError,
)
from ..client.options import RequestOptions
from ..client.paths import dot_segment, path_segments
from ..client.responses import ResponseInfo
from ..client.timing import SessionOptions
from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json
from ..model_codecs.unset import UNSET, Unset
from .errors import PaginationCycleError, ProtocolDataError, ProtocolStateError, ResumeStateError, SessionLimitError
from .options import PaginationOptions, layered
from .records import (
    BodySelector,
    Continuation,
    HeaderSelector,
    ParameterTarget,
    ProtocolProgress,
    RequestTarget,
    Sealed,
    Selector,
    canonical_json,
    continuation_json,
    frozen_wire,
    record_instance,
)
from .resume import MalformedStateError as _MalformedError
from .resume import ResumeState, helper_state, state_fields
from .resume import require_state as _require
from .resume import state_array as _array
from .resume import state_count as _count
from .resume import state_text as _text
from .values import MISSING, Missing, RepeatedValueError, resolve, selected, written

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Iterator, Sequence
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan
    from ..client.responses import HeadersView
    from ..client.timing import Deadline
    from ..client.urls import Origin
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .references import OperationRef
    from .writes import ReadPaths

__all__ = (
    "AsyncPager",
    "CountPlan",
    "CursorPlan",
    "LinkPlan",
    "NextUrlPlan",
    "Page",
    "PageBinding",
    "Pager",
    "PaginationPlan",
    "afirst_page",
    "afollowing_page",
    "aiterate_pages",
    "aresume_pages",
    "first_page",
    "following_page",
    "iterate_pages",
    "resent",
    "resume_pages",
    "saved_request",
)

T = TypeVar("T")
P = TypeVar("P")
R = TypeVar("R")
T_co = TypeVar("T_co", covariant=True, default=object)
P_co = TypeVar("P_co", covariant=True, default=object)

_BOOLEANS: Final = MappingProxyType({"true": True, "false": False})
_MAX_URL_BYTES: Final = 8192
_REFERENCE: Final = re.compile(
    r"(?:(?:[A-Za-z][A-Za-z0-9+.\-]*:)?//\[[0-9A-Fa-f:.]+\](?::[0-9]*)?)?"
    r"(?:[A-Za-z0-9\-._~:/?@!$&'()*+,;=]|%[0-9A-Fa-f]{2})*"
)
_USERINFO: Final = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.\-]*:)?//[^/?#]*@")
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_STATE: Final = frozenset({"arguments", "body", "page"})
_PAGE_FIELDS: Final = 7
_LEFT_FIELDS: Final = 3
_BODY_FIELDS: Final = 3
_PAIR: Final = 2


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
class NextUrlPlan:
    """How a helper reads the URL of the next page from a page, and which values end the traversal.

    An absent URL or JSON null ends it only when its end condition says so, and `end_values` are compared by their
    canonical JSON. Each request after the first is a GET of the URL, or the operation's method with the caller's body
    when `repeat_request_body` is set.
    """

    read: Selector
    end_missing: bool = False
    end_null: bool = False
    end_values: tuple[WireValue, ...] = ()
    repeat_request_body: bool = False
    kind: Literal["next_url"] = field(default="next_url", init=False)
    ends: frozenset[bytes] = field(init=False)

    def __post_init__(self) -> None:
        """Keep the canonical JSON of each end value."""
        object.__setattr__(self, "ends", frozenset(canonical_json(value) for value in self.end_values))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class LinkPlan:
    """Which RFC 8288 Link header field and relation give the URL of the next page; a page without one is the last.

    Each request after the first is a GET of the URL; `read` names every value of the header, for errors to locate.
    """

    header: str
    rel: str = "next"
    kind: Literal["link"] = field(default="link", init=False)
    read: HeaderSelector = field(init=False)

    def __post_init__(self) -> None:
        """Select every value of the header."""
        object.__setattr__(self, "read", HeaderSelector(name=self.header, occurrence="all"))


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


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationPlan(Generic[T, P]):
    """Everything fixed about one generated pagination helper: its identity, call, items, continuation, and bindings.

    Pages after the first call `continued`, the same operation taking each binding's value and then the cursor or
    position as wire values: a parameter's replaces its argument, and a querystring property or a JSON body member
    is written into the caller's encoded value. They skip the schema and argument checks, since the server chose them.
    A helper that `follows` a server's URLs writes no cursor and sends each later page to the URL with GET and no body,
    unless it repeats the request body with the operation's method. `headers` and `queries` name the header and query
    parameters it writes, which a call's options must not patch, and `dotted` the path segments a read value is written
    to, by each parameter's name, write, selector, and position, a caller's argument without a write; such a segment
    must not encode to a dot segment. Generation refuses a helper writing a cookie or a credential position.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[P, object]
    items: Callable[[P], Sequence[T] | None]
    items_selector: BodySelector
    continuation: CursorPlan | CountPlan | NextUrlPlan | LinkPlan
    fingerprint: str
    bindings: tuple[PageBinding, ...] = ()
    follows: bool = field(init=False)
    writes: tuple[tuple[int | None, str | None], ...] = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)
    dotted: ReadPaths = field(init=False)
    continued: OperationPlan[P, object] = field(init=False)

    def __post_init__(self) -> None:
        """Find where each binding and then the continuation is written, and derive the operation of later pages.

        A write is a parameter's argument position with no pointer, a querystring's with a pointer into its value, or
        no position with a pointer into the JSON body. Every media of a body written to is JSON.
        """
        from .writes import read_paths, targeted  # noqa: PLC0415 - Only a plan loads the operation runtime.

        rule = self.continuation
        follows = isinstance(rule, (NextUrlPlan, LinkPlan))
        sources = tuple((binding.target, binding.selector) for binding in self.bindings)
        if isinstance(rule, (CursorPlan, CountPlan)):
            sources = (*sources, (rule.write, rule.read if isinstance(rule, CursorPlan) else None))
        continued, writes, headers, queries = targeted(self.call, (target for target, _ in sources))
        object.__setattr__(self, "follows", follows)
        object.__setattr__(self, "writes", writes)
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "queries", queries)
        object.__setattr__(self, "dotted", read_paths(self.call, sources))
        if follows and not (isinstance(rule, NextUrlPlan) and rule.repeat_request_body):
            continued = replace(continued, method="GET", body=None, retry_safety="method_default", idempotency=None)
        object.__setattr__(self, "continued", continued)


@dataclass(frozen=True, slots=True)
class _Request:
    """The arguments, body, and body media type the first page of a helper call was requested with.

    A call resumed from a checkpoint gives them as its codecs build them from their saved wire values.
    """

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
    link and the digests of its line, never the earlier pages. A link restored from a checkpoint has no response.
    """

    fingerprint: str
    request: _Request
    index: int
    items: int
    cursor: WireValue
    bound: tuple[WireValue, ...]
    digest: bytes | None
    seen: int | None
    response: ResponseInfo | None
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
        max_pages=layered(kinds, "max_pages", _DEFAULTS.max_pages),
        max_items=layered(kinds, "max_items", _DEFAULTS.max_items),
        max_page_bytes=layered(kinds, "max_page_bytes", _DEFAULTS.max_page_bytes),
        max_cursor_bytes=layered(kinds, "max_cursor_bytes", _DEFAULTS.max_cursor_bytes),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


def _data_error(
    plan: PaginationPlan[T, P],
    info: ResponseInfo | None,
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
    try:
        return selected(read, wire, info)
    except RepeatedValueError:
        raise _data_error(plan, info, "malformed", read) from None


def _size_error(
    plan: PaginationPlan[T, P], info: ResponseInfo | None, kind: Literal["cursor", "headers"], limit: int, size: int
) -> ProtocolSizeError:
    return ProtocolSizeError(
        kind=kind,
        limit=limit,
        observed=size,
        unit="bytes",
        helper_id=plan.helper_id,
        operation=plan.operation,
        info=info,
    )


def _ended(
    plan: PaginationPlan[T, P], rule: CursorPlan | NextUrlPlan, wire: WireValue, info: ResponseInfo
) -> WireValue | Missing:
    """Return the value a page's cursor or next URL reads, or MISSING when an end condition ends the traversal there."""
    read = rule.read
    value = _selected(plan, read, wire, info)
    if value is MISSING or value is None:
        if rule.end_missing if value is MISSING else rule.end_null:
            return MISSING
        raise _data_error(plan, info, _absence(value), read)
    if (
        isinstance(rule, CursorPlan) and isinstance(value, str) and not value and rule.empty_string_ends
    ) or canonical_json(value) in rule.ends:
        return MISSING
    return value


def _cursor(
    plan: PaginationPlan[T, P], rule: CursorPlan, wire: WireValue, info: ResponseInfo, limit: int
) -> WireValue | Missing:
    """Return the cursor a page gives, or MISSING when an end condition ends the traversal at this page.

    A cursor over its size limit, in UTF-8 bytes for a string and canonical JSON bytes otherwise, is refused.
    """
    if isinstance(value := _ended(plan, rule, wire, info), Missing):
        return value
    _sized(plan, value, info, limit)
    return value


def _sized(plan: PaginationPlan[T, P], value: WireValue, info: ResponseInfo | None, limit: int) -> None:
    """Refuse a cursor over its size limit, in UTF-8 bytes for a string and canonical JSON bytes otherwise."""
    if (size := len(value.encode()) if isinstance(value, str) else len(canonical_json(value))) > limit:
        raise _size_error(plan, info, "cursor", limit, size)


def _linked(plan: PaginationPlan[T, P], rule: LinkPlan, info: ResponseInfo, limit: int) -> str | Missing:
    """Return the target of the one link of the helper's relation a page's Link header gives, or MISSING without one.

    The header's values together must stay within the cursor size limit in UTF-8 bytes, parse as RFC 8288 links, and
    give the relation at most once.
    """
    from .links import related  # noqa: PLC0415 - Only a Link helper parses Link headers.

    values = info.headers.get_all(rule.header)
    if (size := sum(len(value.encode()) for value in values)) > limit:
        raise _size_error(plan, info, "headers", limit, size)
    if (targets := related(values, rule.rel)) is None:
        raise _data_error(plan, info, "malformed", rule.read)
    if len(targets) > 1:
        raise _data_error(plan, info, "inconsistent", rule.read)
    return targets[0] if targets else MISSING


def _followed(  # noqa: PLR0913, PLR0917
    plan: PaginationPlan[T, P],
    read: Selector,
    reference: str,
    url: str,
    info: ResponseInfo | None,
    limit: int,
    origins: frozenset[Origin],
    stripped: frozenset[str],
) -> str:
    """Return the absolute URL of a server's next-page reference, resolved against the URL that returned the page.

    The reference must be an RFC 3986 URI reference without a fragment, whose brackets enclose only an IP literal
    host, and give an HTTP or HTTPS URL without user information at one of the origins. The URL is returned without
    the stripped query fields, those of the package's security schemes and its auth, and must stay within 8 KiB of
    UTF-8, or the cursor size limit when that is smaller.
    """
    from ..client.urls import URLValidationError, redirect_target, strip_query  # noqa: PLC0415 - Parse only here.

    if "#" in reference or _USERINFO.match(reference):
        raise _data_error(plan, info, "value", read)
    if not _REFERENCE.fullmatch(reference):
        raise _data_error(plan, info, "malformed", read)
    try:
        target = redirect_target(url, reference)
    except URLValidationError:
        raise _data_error(plan, info, "value", read) from None
    if target.origin not in origins:
        raise _data_error(plan, info, "value", read)
    followed = strip_query(target.url, stripped)
    if (size := len(followed.encode())) > (limit := min(limit, _MAX_URL_BYTES)):
        raise _size_error(plan, info, "cursor", limit, size)
    return followed


def _more(plan: PaginationPlan[T, P], read: Selector, wire: WireValue, info: ResponseInfo) -> bool:
    """Return whether a page says more pages follow: a JSON boolean, or a header spelling one."""
    value = _selected(plan, read, wire, info)
    if isinstance(read, HeaderSelector) and isinstance(value, str):
        value = _BOOLEANS.get(value, value)
    if not isinstance(value, bool):
        raise _data_error(plan, info, _unfit(value), read)
    return value


def _integer(value: object) -> int | None:
    """Return a finite integer value without rounding it or treating a boolean as a count."""
    if isinstance(value, int):
        return None if isinstance(value, bool) else value
    match value:
        case float() if value.is_integer():
            return int(value)
        case Decimal() if value.is_finite() and value == value.to_integral_value():
            return int(value)
        case _:
            pass
    return None


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
    if (count := _integer(value)) is None:
        raise _data_error(plan, info, _unfit(value), read)
    if count < 0:
        raise _data_error(plan, info, "value", read)
    return count


class _Walk(Generic[T, P]):
    """The pages of one helper call: its request, limits, session, the last page's link, and the items delivered.

    Its continuations are remembered by digest along the line of pages it extends. A walk that follows a server's URLs
    keeps the origins it may follow them to once its first fetch resolves them. It keeps the body, status, and media
    type of its last page, which a checkpoint saves while the page has items left.
    """

    __slots__ = (
        "core",
        "delivered",
        "limits",
        "link",
        "origins",
        "paths",
        "plan",
        "request",
        "saved",
        "seed",
        "session",
        "start",
    )

    def __init__(
        self,
        plan: PaginationPlan[T, P],
        request: _Request,
        limits: _Limits,
        core: ClientCore | AsyncClientCore,
        link: _Link | None = None,
    ) -> None:
        """Start after a page's link, or before the first page."""
        self.plan = plan
        self.request = request
        self.limits = limits
        self.core = core
        self.link = link
        self.delivered = 0 if link is None else link.items
        self.session: OperationSession | None = None
        self.start: int | None = None
        self.origins: frozenset[Origin] = frozenset()
        self.seed: bytes | None = None
        self.saved: tuple[bytes, int, str | None] | None = None
        self.paths: dict[str, str] | None = None

    def progress(self) -> ProtocolProgress:
        """Return the pages fetched, the items delivered, and the session's sends so far."""
        session = self.session
        return MappingProxyType({
            "pages": 0 if (link := self.link) is None else link.index + 1,
            "items": self.delivered,
            "network_send_count": 0 if session is None else session.network_send_count,
            "network_send_budget_used": 0 if session is None else session.network_send_budget_used,
        })

    def callers(self) -> dict[str, str]:
        """Return the texts of the caller's path arguments that share a segment with a read value, once a walk.

        Each argument the first page sent is encoded again by its parameter's codec, without validation.
        """
        if (texts := self.paths) is None:
            plan, arguments = self.plan, self.request.arguments
            parameters = plan.call.parameters
            texts = self.paths = {
                name: parameters[position].path_text(parameters[position].encode(arguments[position], "none"))
                for _, parts in plan.dotted
                for name, index, _, position in parts
                if index is None
            }
        return texts

    def dotted(self, written: tuple[WireValue, ...], info: ResponseInfo | None) -> None:
        """Refuse a read value among those the next request writes that makes a path segment a dot segment."""
        from .writes import dotted_read  # noqa: PLC0415 - A plan loaded the operation runtime.

        plan = self.plan
        parameters = plan.call.parameters
        for segment, parts in plan.dotted:
            if (read := dotted_read(parameters, segment, parts, written, self.callers)) is not None:
                raise _data_error(plan, info, "value", read)

    def session_id(self) -> str | None:
        """Return the identifier of the walk's session once it started."""
        return None if (session := self.session) is None else session.session_id

    def limit(
        self,
        limit: int,
        kind: Literal["items", "pages", "network_sends"],
        refused: BudgetExceededError | None = None,
        remaining: int = 0,
    ) -> SessionLimitError:
        """Return the error of a session limit reached while pages remain, with the child call a send was refused.

        It keeps a checkpoint of the walk with the items of its last page left, when the call can be checkpointed.
        """
        plan = self.plan
        if refused is None:
            return SessionLimitError(
                kind=kind,
                limit=limit,
                progress=self.progress(),
                resume_state=self.resumable(remaining),
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self.session_id(),
            )
        return SessionLimitError(
            kind=kind,
            limit=limit,
            progress=self.progress(),
            resume_state=self.resumable(remaining),
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
            assert not isinstance(rule, CountPlan)
            raise PaginationCycleError(
                page_index=link.index,
                first_seen_page_index=link.seen,
                resume_state=self.resumable(),
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
        if not isinstance(plan.continuation, CountPlan):
            return plan.call
        call = plan.call
        if (
            (position := plan.writes[-1][0]) is not None
            and call.parameters[position].plan.location in {"query", "header"}
            and isinstance(self.request.arguments[position], Unset)
        ):
            return call
        from .writes import ReadMedia, ReadParameter  # noqa: PLC0415 - Only a plan loads the operation runtime.

        read = self.started
        if position is None:
            body = call.body
            assert body is not None
            media = tuple(
                ReadMedia(media_type=media.media_type, kind=media.kind, encoder=media.encoder, read=read)
                for media in body.media
            )
            return replace(call, body=replace(body, media=media))
        parameters = list(call.parameters)
        spec = parameters[position]
        parameters[position] = ReadParameter(plan=spec.plan, encoder=spec.encoder, adapter=spec.adapter, read=read)
        return replace(call, parameters=tuple(parameters))

    def started(self, wire: WireValue) -> None:
        """Start at the position the caller's first request sends, refusing one that is not an integer.

        The value is read where the position is written; a querystring or body without that member sends none.
        """
        plan = self.plan
        pointer = plan.writes[-1][1]
        value = wire if pointer is None else resolve(wire, pointer)
        if value is MISSING:
            return
        if (position := _integer(value)) is None:
            rule = plan.continuation
            assert isinstance(rule, CountPlan)
            raise ProtocolDataError(
                condition="type", location=rule.write, helper_id=plan.helper_id, operation=plan.operation
            )
        self.start = position

    def sent(self, url: str, headers: HeadersView) -> None:
        """Read a first count request's default position after all query and header patches have been applied.

        An encoded typed start overrides client and view patches, and call patches of the target are refused, so it
        already gives the sent position without reparsing the request.
        """
        if self.start is not None or self.link is not None or not isinstance(rule := self.plan.continuation, CountPlan):
            return
        match rule.write:
            case ParameterTarget(location="query", name=name):
                from urllib.parse import unquote_plus, urlsplit  # noqa: PLC0415 - Only a first query is inspected.

                values = tuple(
                    unquote_plus(parts[2])
                    for pair in urlsplit(url).query.split("&")
                    if unquote_plus((parts := pair.partition("="))[0]) == name
                )
            case ParameterTarget(location="header", name=name):
                values = headers.get_all(name)
            case _:
                return
        self.start = None
        if not values:
            return
        wire: WireValue = values
        if len(values) == 1:
            try:
                wire = Decimal(values[0])
            except InvalidOperation:
                wire = values[0]
        self.started(wire)

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

    def next_request(self) -> tuple[tuple[object, ...], object, str | None]:
        """Return the next page's arguments, body, and followed URL: the first page's, with the last page's writes.

        Each binding's value and then the cursor or position replace a parameter's argument, or are written by pointer
        into the caller's querystring or body; a helper that follows a server's URLs writes no cursor and returns the
        URL the last page gave.
        """
        request = self.request
        if (link := self.link) is None:
            return request.arguments, request.body, None
        plan = self.plan
        follows = plan.follows
        values = link.bound if follows else (*link.bound, link.cursor)
        arguments, body = written(plan.writes, request.arguments, request.body, values)
        return arguments, body, cast("str", link.cursor) if follows else None

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

    def follow(
        self, rule: NextUrlPlan | LinkPlan, wire: WireValue, info: ResponseInfo, url: str, stripped: frozenset[str]
    ) -> str | Missing:
        """Return the absolute URL of the page after a page, or MISSING when the page is the last.

        A next URL must be a string; its reference resolves against the URL of the hop that returned the page. The
        first page's own URL is kept as the continuation that led to it, so a page that gives it again is a cycle.
        """
        plan, limit = self.plan, self.limits.max_cursor_bytes
        if self.link is None:
            from ..client.urls import absolute_target, strip_query  # noqa: PLC0415 - Only a followed URL is parsed.

            first = strip_query(absolute_target(url).url, stripped)
            self.seed = sha256(continuation_json(Continuation(kind=rule.kind, value=first))).digest()
        reference = _linked(plan, rule, info, limit) if isinstance(rule, LinkPlan) else _ended(plan, rule, wire, info)
        if isinstance(reference, Missing):
            return reference
        if not isinstance(reference, str):
            raise _data_error(plan, info, "type", rule.read)
        return _followed(plan, rule.read, reference, url, info, limit, self.origins, stripped)

    def build(  # noqa: PLR0913, PLR0917
        self, data: P, wire: WireValue, content: bytes, info: ResponseInfo, url: str, stripped: frozenset[str]
    ) -> tuple[Page[T, P], WireValue | Missing, tuple[WireValue, ...], bytes]:
        """Return a decoded page, what the next request writes, its bindings' values, and the page's body.

        The items are checked in the wire value before the page's accessor reads them from the decoded one; a value of
        another type fails its response's validation first, in every mode. What the next request writes is the cursor
        or position, or the URL it follows without the stripped query fields, MISSING after the last page.
        The bindings are read only when a page follows, and a read value that makes a path segment a dot segment once
        encoded is refused.
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
        if isinstance(rule, CursorPlan):
            cursor = _cursor(plan, rule, wire, info, self.limits.max_cursor_bytes)
        elif isinstance(rule, CountPlan):
            cursor = self.advance(rule, len(items), wire, info)
        else:
            cursor = self.follow(rule, wire, info, url, stripped)
        if isinstance(cursor, Missing):
            return Page(items=items, data=data, response=info), cursor, (), content
        bound = self.bound(wire, info)
        self.dotted(bound if plan.follows else (*bound, cursor), info)
        continuation = Continuation(kind=rule.kind, value=cursor)
        page = Page(items=items, data=data, response=info, continuation=continuation)
        return page, cursor, bound, content

    def record(
        self, page: Page[T, P], cursor: WireValue | Missing, bound: tuple[WireValue, ...], content: bytes
    ) -> Page[T, P]:
        """Link a fetched page after the last one, noting whether its continuation was seen before on its line.

        The line of a walk that follows URLs starts with the first page's own URL, as if the first page continued from
        it, so a first page that gives its own URL is a cycle too. The page's body is kept for checkpoints.
        """
        info = page.response
        self.saved = content, info.status_code, info.content_type
        previous = self.link
        index = 0 if previous is None else previous.index + 1
        continuation = page.continuation
        digest = None if continuation is None else sha256(continuation_json(continuation)).digest()
        history = _line(previous)
        if (seed := self.seed) is not None and previous is None:
            history.seen[seed] = 0
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
            None if first == index and not (index == 0 and digest == seed) else first,
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

    def checkpoint(self, remaining: int) -> ResumeState:
        """Return a checkpoint of the walk, with the given number of its last page's items left to deliver.

        It saves the wire values of the caller's arguments and of the JSON body the next request sends, the last
        page's position, continuation, bindings' values, and the history of its line, and that page's body while items
        are left, bound to the helper's fingerprint and the security the call runs under. The caller's options and
        whatever its auth adds are never saved.
        """
        plan, link, request, core = self.plan, self.link, self.request, self.core
        options = self.limits.options
        body = request.body if link is None or plan.continued.body is not None else UNSET
        arguments, saved_body = core.saved_request(
            plan, plan.call, request.arguments, body, request.media_type, options
        )
        facts, exportable = core.checkpoint_security(plan.call, options)
        saved = self.saved if remaining else None
        page: WireValue = None
        if link is not None:
            history = sorted((index, digest.hex()) for digest, index in _line(link).seen.items())
            page = (
                link.index,
                link.items,
                () if link.digest is None else (link.cursor,),
                link.bound,
                link.seen,
                tuple((digest, index) for index, digest in history),
                None if saved is None else (remaining, *saved[1:]),
            )
        kept = saved_request(arguments, saved_body)
        state: WireValue = {"arguments": kept[0], "body": kept[1], "page": page}
        return helper_state(
            helper_fingerprint=plan.fingerprint,
            security_fingerprint=sha256(canonical_json(facts)).hexdigest(),
            state=state,
            payload=b"" if saved is None else saved[0],
            exportable=exportable,
        )

    def resumable(self, remaining: int = 0) -> ResumeState | None:
        """Return a checkpoint of the walk for an error to keep, or None when the call cannot be checkpointed.

        Whatever keeps the call from being checkpointed never replaces the error that keeps the checkpoint.
        """
        try:
            return self.checkpoint(remaining)
        except Exception:  # noqa: BLE001
            return None


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


def _digest(value: WireValue) -> bytes:
    _require(isinstance(value, str) and _DIGEST.fullmatch(value) is not None)
    return bytes.fromhex(cast("str", value))


def _resume_error(
    plan: PaginationPlan[T, P], condition: Literal["fingerprint", "security", "expired", "malformed"]
) -> ResumeStateError:
    return ResumeStateError(condition=condition, helper_id=plan.helper_id, operation=plan.operation)


def _restored(
    core: ClientCore | AsyncClientCore, plan: PaginationPlan[T, P], state: object, limits: _Limits
) -> tuple[_Walk[T, P], tuple[T, ...], int]:
    """Return the walk a checkpoint continues, with the items it left of its last page and the position among them.

    The checkpoint must be this helper's, made under the security the call runs with, and unexpired. Its continuation
    is checked as one a server just gave, and a state or saved page that does not fit the helper is malformed.
    """
    if not isinstance(state, ResumeState):
        raise _invalid(plan, ("state",))
    helper, security, state_json, payload, expires_at = state_fields(state)
    if helper != plan.fingerprint:
        raise _resume_error(plan, "fingerprint")
    if security != sha256(canonical_json(core.checkpoint_security(plan.call, limits.options)[0])).hexdigest():
        raise _resume_error(plan, "security")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise _resume_error(plan, "expired")
    try:
        return _walked(core, plan, decode_json(state_json), payload, limits)
    except _MalformedError:
        raise _resume_error(plan, "malformed") from None


def saved_request(
    arguments: tuple[WireValue | Unset, ...], body: tuple[WireValue, str, str | None] | None
) -> tuple[WireValue, WireValue]:
    """Return how a checkpoint keeps a request's wire values: each argument in an array, empty when omitted.

    The body is kept as its wire value with its declared and concrete media types, or as an empty array without one.
    """
    return tuple(() if isinstance(value, Unset) else (value,) for value in arguments), () if body is None else body


def resent(
    core: ClientCore | AsyncClientCore, call: OperationPlan[object, object], arguments: WireValue, body: WireValue
) -> _Request:
    """Return the request a checkpoint saved, its arguments and any JSON body built as a caller builds them.

    An argument a checkpoint never saves is refused, and so is a value its codec refuses, path arguments that make a
    segment a dot segment once encoded, a body the operation does not take, and a media type its select method refuses.
    """
    saved = [_array(argument) for argument in _array(arguments)]
    _require(len(saved) == len(call.parameters) and all(len(argument) <= 1 for argument in saved))
    wire = tuple(argument[0] if argument else UNSET for argument in saved)
    _require(core.unsaved_argument(call, wire) is None)
    sent = _array(body)
    given: tuple[WireValue, str, str | None] | None = None
    if sent:
        _require(len(sent) == _BODY_FIELDS and call.body is not None)
        declared, concrete = _text(sent[1]), _text(sent[2])
        _require(declared is not None)
        given = sent[0], cast("str", declared), concrete
    try:
        restored, restored_body, media_type = core.restored_request(call, wire, given)
        texts = {
            spec.plan.name: spec.path_text(value)
            for spec, value in zip(call.parameters, wire, strict=True)
            if spec.plan.location == "path" and not isinstance(value, Unset)
        }
    except (SDKError, CodecError):
        raise _MalformedError from None
    _require(
        not any(texts.keys() >= {*names} and dot_segment(segment, texts) for segment, names in path_segments(call.path))
    )
    return _Request(restored, restored_body, media_type)


def _checked(core: ClientCore | AsyncClientCore, walk: _Walk[T, P]) -> tuple[str, HeadersView]:
    """Prepare the request a walk sends next as its call would, refusing one whose saved values cannot be sent.

    Only the encoding and validation of the saved values is the checkpoint's; any other refusal, such as one of the
    resumed call's options, is raised as the call raises it. The URL and headers it would send are returned.
    """
    try:
        return core.checked_page(walk.operation(), walk.next_request, walk.request.media_type, walk.limits.options)
    except (RequestEncodingError, ProtocolDataError, CodecError):
        raise _MalformedError from None


def _walked(
    core: ClientCore | AsyncClientCore, plan: PaginationPlan[T, P], state: WireValue, payload: bytes, limits: _Limits
) -> tuple[_Walk[T, P], tuple[T, ...], int]:
    """Return the walk of a checkpoint's decoded state, with the items it left of its last page and their position.

    The next request is prepared as its call would prepare it, without sending, and so is the first one, which gives an
    offset or page number its start as the first page read it, unless a followed URL replaced it without the body the
    checkpoint left out.
    """
    _require(isinstance(state, Mapping) and frozenset(state) == _STATE)
    fields = cast("Mapping[str, WireValue]", state)
    request = resent(core, plan.call, fields["arguments"], fields["body"])
    first = _Walk(plan, request, limits, core)
    if (page := fields["page"]) is None or not plan.follows or plan.continued.body is not None:
        first.sent(*_checked(core, first))
    if page is None:
        _require(not payload)
        return first, (), 0
    saved = _array(page)
    _require(len(saved) == _PAGE_FIELDS)
    link = _relinked(plan, request, saved)
    walk = _Walk(plan, request, limits, core, link)
    if link.digest is not None:
        _continued(core, walk, first.start)
        _checked(core, walk)
    if (left := saved[6]) is None:
        _require(not payload)
        return walk, (), 0
    if (size := len(payload)) > (limit := limits.max_page_bytes):
        raise ProtocolSizeError(
            kind="page", limit=limit, observed=size, unit="bytes", helper_id=plan.helper_id, operation=plan.operation
        )
    rest = _array(left)
    _require(len(rest) == _LEFT_FIELDS)
    remaining, status, content_type = _count(rest[0]), _count(rest[1]), _text(rest[2])
    try:
        data, wire = core.saved_page(plan.call, payload, status, content_type, limits.options)[:2]
    except SDKError:
        raise _MalformedError from None
    native = plan.items(data)
    selected = resolve(wire, plan.items_selector.pointer)
    _require(selected is not MISSING and selected is not None and native is not None)
    entries = tuple(native or ())
    _require(0 < remaining <= len(entries) <= link.items)
    walk.saved = payload, status, content_type
    walk.delivered = link.items - remaining
    return walk, entries, len(entries) - remaining


def _relinked(plan: PaginationPlan[T, P], request: _Request, saved: tuple[WireValue, ...]) -> _Link:
    """Return the link of the last page a checkpoint saved: its position, continuation, bindings, and line history.

    A literal binding's value is the plan's, whatever the state saved.
    """
    index, items = _count(saved[0]), _count(saved[1])
    cursor, bound = _array(saved[2]), _array(saved[3])
    rule = plan.continuation
    value = cursor[0] if cursor else None
    _require(
        len(cursor) <= 1
        and len(bound) == (len(plan.bindings) if cursor else 0)
        and (
            not cursor
            or (
                isinstance(value, str)
                if plan.follows
                else not isinstance(rule, CountPlan) or (isinstance(value, int) and not isinstance(value, bool))
            )
        )
    )
    bound = tuple(
        value if binding.selector is not None else binding.literal
        for binding, value in zip(plan.bindings[: len(bound)], bound, strict=True)
    )
    seen = None if saved[4] is None else _count(saved[4], index)
    history: dict[bytes, int] = {}
    for entry in map(_array, _array(saved[5])):
        _require(len(entry) == _PAIR)
        history[_digest(entry[0])] = _count(entry[1], index)
    digest = sha256(continuation_json(Continuation(kind=rule.kind, value=value))).digest() if cursor else None
    return _Link(plan.fingerprint, request, index, items, value, bound, digest, seen, None, _History(history, index))


def _continued(core: ClientCore | AsyncClientCore, walk: _Walk[T, P], start: int | None) -> None:
    """Check a resumed continuation as one a server just gave: its size, followed URL and origin, and dot segments.

    An offset or page number must be the position the walk reaches from where its first request starts.
    """
    plan, limits, link = walk.plan, walk.limits, cast("_Link", walk.link)
    rule, value = plan.continuation, link.cursor
    if isinstance(rule, CursorPlan):
        _sized(plan, value, None, limits.max_cursor_bytes)
    elif isinstance(rule, CountPlan):
        reached = (rule.first if start is None else start) + (
            link.items if rule.step is None else rule.step * (link.index + 1)
        )
        _require(value == reached >= 0)
    else:
        walk.origins = core.follow_origins(plan.call, limits.options)
        url = cast("str", value)
        _followed(plan, rule.read, url, url, None, limits.max_cursor_bytes, walk.origins, frozenset())
    walk.dotted(link.bound if plan.follows else (*link.bound, value), None)


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
            raise walk.limit(limit, "items", remaining=len(self._items) - self._position)
        walk.delivered += 1
        self._position += 1
        return self._items[self._position - 1]

    def _buffer(self, page: Page[T, P]) -> None:
        self._items = page.items
        self._position = 0

    def _resumed(self, items: tuple[T, ...], position: int) -> Self:
        """Deliver the items a checkpoint left of its last page first, keeping the pager to items."""
        if items:
            self._items, self._position, self._mode = items, position, "items"
        return self

    def checkpoint(self) -> ResumeState:
        """Return a checkpoint the helper's `resume` continues from, sending nothing.

        It holds the items left of the last page and the position after it, the progress so far, and the cycle
        history, but neither the session nor the call's options. A pager that failed or closed is checkpointed as it
        stopped, and one fetching a page refuses with ProtocolStateError.
        """
        action = "checkpoint"
        if not self._lock.acquire(blocking=False):
            raise self._walk.state_error(action, "fetching")
        try:
            return self._walk.checkpoint(len(self._items) - self._position)
        finally:
            self._lock.release()

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


def _origins(core: ClientCore | AsyncClientCore, walk: _Walk[T, P]) -> None:
    """Resolve the origins a walk that follows a server's URLs may follow them to, once, before its first fetch.

    A walk continuing an earlier page with options that select another server refuses the page's URL when its origin
    is no longer one of them, before sending.
    """
    plan = walk.plan
    if not plan.follows or walk.origins:
        return
    origins = walk.origins = core.follow_origins(plan.call, walk.limits.options)
    if (link := walk.link) is None:
        return
    from ..client.urls import request_origin  # noqa: PLC0415 - Only a followed URL is parsed.

    if request_origin(cast("str", link.cursor)) not in origins:
        rule = plan.continuation
        assert not isinstance(rule, (CursorPlan, CountPlan))
        raise _data_error(plan, link.response, "value", rule.read)


def _fetch(
    core: ClientCore, walk: _Walk[T, P], session: OperationSession
) -> tuple[Page[T, P], WireValue | Missing, tuple[WireValue, ...], bytes]:
    """Fetch the walk's next page as a child call of its session."""
    _origins(core, walk)
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
        read_request=walk.sent,
    )


async def _afetch(
    core: AsyncClientCore, walk: _Walk[T, P], session: OperationSession
) -> tuple[Page[T, P], WireValue | Missing, tuple[WireValue, ...], bytes]:
    """Fetch the walk's next page as an asyncio child call of its session."""
    _origins(core, walk)
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
        read_request=walk.sent,
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
    limits = _limits(core, plan, pagination_options, options, session_options)
    walk = _Walk(plan, _Request(arguments, body, media_type), limits, core)
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
    limits = _limits(core, plan, pagination_options, options, session_options)
    walk = _Walk(plan, _Request(arguments, body, media_type), limits, core)
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
    return Pager(core, _Walk(plan, _Request(arguments, body, media_type), limits, core))


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
    return AsyncPager(core, _Walk(plan, _Request(arguments, body, media_type), limits, core))


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
    walk = _Walk(plan, link.request, limits, core, link)
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
    walk = _Walk(plan, link.request, limits, core, link)
    if (session := walk.ready()) is None:
        return None
    with walk.mapped():
        return walk.record(*await _afetch(core, walk, session))


def resume_pages(  # noqa: PLR0913
    core: ClientCore,
    plan: PaginationPlan[T, P],
    state: object,
    *,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> Pager[T, P]:
    """Return a pager continuing a helper's checkpoint in a session of its own, checking the checkpoint now.

    It sends nothing until it is iterated, and delivers the items the checkpoint left of its last page first.
    """
    limits = _limits(core, plan, pagination_options, options, session_options)
    walk, items, position = _restored(core, plan, state, limits)
    return Pager(core, walk)._resumed(items, position)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def aresume_pages(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PaginationPlan[T, P],
    state: object,
    *,
    pagination_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncPager[T, P]:
    """Return an asyncio pager continuing a helper's checkpoint in a session of its own, checking the checkpoint now.

    It sends nothing until it is iterated, and delivers the items the checkpoint left of its last page first.
    """
    limits = _limits(core, plan, pagination_options, options, session_options)
    walk, items, position = _restored(core, plan, state, limits)
    return AsyncPager(core, walk)._resumed(items, position)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
