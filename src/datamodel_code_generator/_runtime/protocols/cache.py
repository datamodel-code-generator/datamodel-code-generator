"""Conditional fetches through a private response cache, and the explicit invalidation of its entries.

A fetch answers from a fresh stored representation without sending, revalidates a stale one with its validator, or
sends its request as an ordinary call; a stored body is decoded again at every use. Entries live only in the store the
client's protocol settings lend the helper, keyed by the request and by the credentials it carries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, cast, final
from uuid import uuid4

from typing_extensions import TypeVar

from ..client.client import stored_value
from ..client.errors import ProtocolConfigurationError, add_secondary
from ..client.media import normalized
from ..client.options import RequestOptions
from ..client.responses import HeadersView, Response, ResponseInfo
from ..client.retry import http_timestamp
from ..client.transports import PreparedRequest
from ..model_codecs.unset import UNSET, Unset
from .caches import CacheEntry, CacheResult, CacheSource, bytes_tuple, string_tuple
from .errors import CacheInvalidationError, CacheProtocolError, CacheStoreError, CacheValidatorConflictError
from .options import CacheOptions
from .records import canonical_json

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..client.bodies import EncodedAttempt
    from ..client.client import AsyncClientCore, ClientCore
    from ..client.operations import OperationPlan
    from ..client.options import Settings
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .caches import AsyncCacheStore, CacheStore
    from .references import OperationRef

__all__ = (
    "CacheMutationPlan",
    "CachePlan",
    "afetch",
    "ainvalidate",
    "amutate",
    "fetch",
    "invalidate",
    "mutate",
)

T = TypeVar("T")
V = TypeVar("V")

Tag = tuple[str | int, ...]
_Limits = tuple[int, float, RequestOptions | None]

_HOP_BY_HOP: Final = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authentication-info",
    "proxy-authorization",
    "proxy-connection",
    "te",  # codespell:ignore te
    "trailer",
    "transfer-encoding",
    "upgrade",
})
_UNSTORED: Final = _HOP_BY_HOP | {"age", "content-encoding", "content-length"}
_UNMERGED: Final = _UNSTORED | {"content-type"}
_RESPONSE_DIRECTIVES: Final = frozenset({
    "max-age",
    "must-revalidate",
    "must-understand",
    "no-cache",
    "no-store",
    "no-transform",
    "private",
    "proxy-revalidate",
    "public",
    "s-maxage",
})
_DELTAS: Final = frozenset({"max-age", "s-maxage"})
_FIELD_LISTS: Final = frozenset({"no-cache", "private"})
_REQUEST_DIRECTIVES: Final = frozenset({"max-age", "no-cache", "no-store"})
_REFUSED: Final = ("Range", "If-Range", "If-Match", "If-Unmodified-Since")
_NOT_VARIED: Final = frozenset({"cache-control", "if-modified-since", "if-none-match"})
_VARIED_LOCATIONS: Final = frozenset({"cookie", "header"})
_VALIDATORS: Final[tuple[tuple[Literal["If-None-Match", "If-Modified-Since"], str], ...]] = (
    ("If-None-Match", "etag"),
    ("If-Modified-Since", "last-modified"),
)
_ITEM: Final = re.compile(
    r"[ \t]*(?:([!#$%&'*+.^_`|~0-9A-Za-z-]+)(?:=([!#$%&'*+.^_`|~0-9A-Za-z-]+|\"(?:[^\"\\]|\\.)*\"))?)?[ \t]*(?:,|\Z)"
)
_DELTA: Final = re.compile(r"[0-9]+")
_MAX_DELTA: Final = 2**31
_DELTA_DIGITS: Final = 10
_MAX_ENTRY_BYTES: Final = 2 * 1024 * 1024
_MAX_TTL: Final = 300.0
_NOT_MODIFIED: Final = 304


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CachePlan(Generic[T]):
    """Everything fixed about one generated cache helper: its identity, call, validators, statuses, Vary, and tags.

    A tag is literal text and the positions of the arguments whose wire values fill it in. Vary names are lowercase.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[T, object]
    validator: Literal["etag", "last_modified", "both"]
    authenticated: bool
    fingerprint: str
    statuses: frozenset[int] = frozenset({200})
    vary_allowlist: frozenset[str] = frozenset()
    tags: tuple[Tag, ...] = ()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CacheMutationPlan(Generic[T]):
    """One explicit mutation of a cache helper: the operation it calls and the tags its success invalidates."""

    helper_id: str
    operation: OperationRef
    call: OperationPlan[T, object]
    tags: tuple[Tag, ...]


@dataclass(frozen=True, slots=True)
class _Directives:
    """What a fetch's own Cache-Control asks: no stored response, none unvalidated, or none older than max-age."""

    no_store: bool = False
    no_cache: bool = False
    max_age: int | None = None


@dataclass(frozen=True, slots=True)
class _Received(Generic[T]):
    """A network response a fetch may store: its decoded result, its body after content decoding, and stored headers.

    A revalidation's result carries the merged representation, and its status is the stored one.
    """

    response: Response[T]
    body: bytes
    headers: HeadersView
    source: CacheSource
    received: HeadersView


def _option(layers: tuple[object, ...], name: str, default: V) -> V:
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def _directives(values: tuple[str, ...]) -> dict[str, str | None] | None:
    """Return Cache-Control directives by lowercase name, or None when one is malformed or repeated differently."""
    text = ",".join(values)
    found: dict[str, str | None] = {}
    position = 0
    while position < len(text):
        if (match := _ITEM.match(text, position)) is None:
            return None
        position = match.end()
        if match[1] is not None and found.setdefault(match[1].lower(), match[2]) != match[2]:
            return None
    return found


def _delta(value: str | None) -> int | None:
    """Return delta-seconds, capped at 2**31 as RFC 9111 requires, or None for anything else."""
    if value is None or not _DELTA.fullmatch(value := value.strip()):
        return None
    return _MAX_DELTA if len(value) > _DELTA_DIGITS else min(int(value), _MAX_DELTA)


def _well_formed(directives: dict[str, str | None]) -> bool:
    """Return whether each response directive is supported, with a delta, a field list, or no value as it takes."""
    return all(
        name in _RESPONSE_DIRECTIVES
        and (
            _delta(value) is not None
            if name in _DELTAS
            else value is None or (name in _FIELD_LISTS and value.startswith('"'))
        )
        for name, value in directives.items()
    )


def _text(value: WireValue) -> str:
    return value if isinstance(value, str) else canonical_json(value).decode()


def _opaque(etag: str | None) -> str | None:
    return None if etag is None else etag.strip().removeprefix("W/")


def _vary(headers: HeadersView) -> tuple[str, ...]:
    names = {name.strip().lower() for value in headers.get_all("vary") for name in value.split(",")}
    names.discard("")
    return tuple(sorted(names))


def _instant(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def _age(entry: CacheEntry, now: float) -> float:
    return entry.initial_age_seconds + max(0.0, now - entry.response_time.timestamp())


def _without(headers: HeadersView, names: frozenset[str], length: int) -> list[tuple[str, str]]:
    kept = [(name, value) for name, value in headers if name.lower() not in names]
    kept.append(("Content-Length", str(length)))
    return kept


def _merged(stored: HeadersView, update: HeadersView, length: int) -> HeadersView:
    """Return stored headers with every field a 304 gives replaced by its values, except fields it may not update."""
    replaced = {name.lower() for name, _ in update if name.lower() not in _UNMERGED}
    kept = [(name, value) for name, value in stored if name.lower() not in replaced]
    added = [(name, value) for name, value in update if name.lower() in replaced]
    return HeadersView(_without(HeadersView((*kept, *added)), frozenset({"content-length"}), length))


class _Fetch(Generic[T]):
    """One fetch of a cache helper: its request, base key, directives, looked-up entry, and the response it got."""

    __slots__ = (
        "base_key",
        "clock",
        "credential_headers",
        "directives",
        "entry",
        "implicit",
        "max_entry_bytes",
        "max_ttl",
        "options",
        "plan",
        "request",
        "requested_at",
        "settings",
        "started",
        "usable",
    )

    def __init__(
        self, core: ClientCore | AsyncClientCore, plan: CachePlan[T], arguments: tuple[object, ...], limits: _Limits
    ) -> None:
        """Prepare the fetch's request, refuse what a cache cannot answer, and derive its base key.

        A request carrying credentials needs the client's credential partition and a helper declared authenticated,
        and an anonymous one a helper declared anonymous. The base key names the headers its entries vary on beyond a
        response's Vary: each one a header patch or a header parameter fills, and Cookie for a cookie parameter. A
        request patching other headers than a stored one never shares its entries, whichever was stored first.
        """
        self.plan, self.clock = plan, core.clock
        self.started = self.clock.monotonic()
        self.requested_at = 0.0
        self.entry: CacheEntry | None = None
        self.usable: CacheEntry | None = None
        self.max_entry_bytes, self.max_ttl, self.options = limits
        prepared = core.cache_request(plan.call, arguments, self.options)
        self.settings, self.request, self.credential_headers = (
            prepared.settings,
            prepared.request,
            prepared.credential_headers,
        )
        credentials, partition = prepared.credentials, prepared.partition
        headers = self.request.headers
        if (refused := next((name for name in _REFUSED if name in headers), None)) is not None:
            raise _invalid(plan, ("headers", refused))
        self.directives = _requested(plan, headers.get_all("cache-control"))
        if prepared.foreign_auth:
            raise _configuration(plan, ("options", "auth"), "security_partition")
        if (credentials is not None) != plan.authenticated:
            raise _configuration(plan, ("auth",), "binding_mismatch")
        if credentials is not None and partition is None:
            raise _configuration(plan, ("protocols", "security"), "security_partition")
        patched = {name.lower() for layer in self.settings.headers for name, _ in layer}
        declared = {
            spec.plan.name.lower() if spec.plan.location == "header" else "cookie"
            for spec in plan.call.parameters
            if spec.plan.location in _VARIED_LOCATIONS
        }
        self.implicit = implicit = frozenset(patched | declared) - _NOT_VARIED
        url = prepared.url
        self.base_key = sha256(
            canonical_json({
                "method": self.request.method,
                "url": url,
                "accept": headers.get("accept"),
                "partition": "anonymous" if partition is None else partition,
                "credentials": credentials,
                "varied": sorted(implicit),
            })
        ).digest()

    def found(self, entry: object) -> CacheEntry | None:
        """Keep a looked-up entry, refusing another result, and return it when this helper may use it.

        A usable entry refuses a validator header the caller gave other than once with the entry's value.
        """
        if entry is not None and not isinstance(entry, CacheEntry):
            raise _store_error(self.plan, "lookup")
        self.entry, plan = entry, self.plan
        if entry is None or entry.schema_fingerprint != plan.fingerprint or entry.status_code not in plan.statuses:
            return None
        headers = self.request.headers
        for header, stored in _VALIDATORS:
            if (given := headers.get_all(header)) and given != (entry.headers.get(stored),):
                raise CacheValidatorConflictError(
                    header_name=header, helper_id=plan.helper_id, operation=plan.operation
                )
        self.usable = entry
        return entry

    def fresh(self, entry: CacheEntry | None) -> CacheResult[T] | None:
        """Return the decoded result of a fresh usable entry, or None when the fetch must send."""
        directives = self.directives
        if entry is None or directives.no_cache:
            return None
        age = _age(entry, self.clock.time())
        if age >= min(entry.freshness_seconds, self.max_ttl) or (
            directives.max_age is not None and age > directives.max_age
        ):
            return None
        info = self.info(entry.status_code, entry.headers)
        data = stored_value(self.plan.call, info, entry.body, self.settings)
        return CacheResult(
            data=data,
            source="fresh_cache",
            response=replace(info, elapsed=self.clock.monotonic() - self.started),
            network_status=None,
        )

    def info(self, status: int, headers: HeadersView, received: ResponseInfo | None = None) -> ResponseInfo:
        """Return a stored representation's metadata: a revalidation's call with its status and headers, or no call."""
        content_type = headers.get("content-type")
        named = self.plan.call.request_id_header
        content = None if content_type is None else normalized(content_type)
        request_id = None if named is None else headers.get(named)
        if received is not None:
            return replace(received, status_code=status, headers=headers, content_type=content, request_id=request_id)
        return ResponseInfo(
            status_code=status,
            headers=headers,
            call_id=str(uuid4()),
            elapsed=0.0,
            content_type=content,
            request_id=request_id,
            resource_attempt_count=0,
            network_send_count=0,
            network_send_budget_used=0,
            wire_send_count=0,
        )

    def conditional(self, entry: CacheEntry | None) -> PreparedRequest[EncodedAttempt]:
        """Return the request to send, adding the validator of a usable stale entry the caller did not give."""
        request = self.request
        self.requested_at = self.clock.time()
        validator = None if entry is None else _validator(self.plan, entry.headers)
        if validator is None or validator[0] in request.headers:
            return request
        return PreparedRequest(
            method=request.method,
            url=request.url,
            headers=HeadersView((*request.headers.items(), validator)),
            body=request.body,
        )

    @staticmethod
    def modified(response: Response[T], body: bytes) -> tuple[Response[T], _Received[T]]:
        """Keep a decoded network response with its body and the headers an entry of it stores."""
        received = response.info.headers
        return response, _Received(
            response, body, HeadersView(_without(received, _UNSTORED, len(body))), "network", received
        )

    def not_modified(self, info: ResponseInfo) -> tuple[Response[T], _Received[T]]:
        """Decode the looked-up representation a 304 validates, with its headers merged, or refuse the 304.

        A 304 needs a usable entry, the request URL itself rather than a redirect's, and no validator other than the
        entry's; a strong and a weak ETag of the same opaque tag match.
        """
        entry, plan = self.usable, self.plan
        if entry is None or info.redirect_count or not _validates(entry.headers, info.headers):
            raise CacheProtocolError(helper_id=plan.helper_id, operation=plan.operation, info=info)
        headers = _merged(entry.headers, info.headers, len(entry.body))
        merged = self.info(entry.status_code, headers, info)
        response = Response(data=stored_value(plan.call, merged, entry.body, self.settings), info=merged)
        return response, _Received(response, entry.body, headers, "revalidated", info.headers)

    def stored(self, received: _Received[T], arguments: tuple[object, ...]) -> dict[str, Any] | None:
        """Return the fields of the entry a response becomes but its Vary fingerprints, or None to store nothing.

        It is refused for an unlisted status, a redirect, a body over the limit, a Set-Cookie, an unsupported or
        malformed Cache-Control, no-store, a Vary outside the allowlist or `*`, a Vary naming a header credentials
        travel in, a revalidation whose Vary changed, and a response that is neither fresh nor revalidatable. The
        entry varies on the response's Vary and on the headers its base key names, and its date and age are those of
        the response received, so a 304 without Age makes the entry's age 0.
        """
        plan, headers, now = self.plan, received.headers, self.clock.time()
        directives = _directives(headers.get_all("cache-control"))
        vary = _vary(headers)
        if (
            directives is None
            or not _well_formed(directives)
            or not self.fits(received, vary)
            or not plan.vary_allowlist.issuperset(vary)
            or not self.credential_headers.isdisjoint(vary)
        ):
            return None
        origin = received.received
        date = None if (value := origin.get("date")) is None else http_timestamp(value, now)
        date = now if date is None else date
        lifetime = 0.0
        if "no-cache" not in directives:
            if (seconds := _delta(directives.get("max-age"))) is not None:
                lifetime = float(seconds)
            elif (expires := headers.get("expires")) is not None:
                lifetime = 0.0 if (expiry := http_timestamp(expires, now)) is None else max(0.0, expiry - date)
        freshness = min(lifetime, self.max_ttl)
        if "no-store" in directives or (freshness <= 0 and _validator(plan, headers) is None):
            return None
        age = _delta(origin.get("age")) if "age" in origin else 0
        initial = max(now - date, (_MAX_DELTA if age is None else age) + (now - self.requested_at), 0.0)
        return {
            "version": uuid4().hex,
            "vary": tuple(sorted(self.implicit.union(vary))),
            "status_code": received.response.info.status_code,
            "headers": headers,
            "body": received.body,
            "request_time": _instant(self.requested_at),
            "response_time": _instant(now),
            "stored_at": _instant(now),
            "freshness_seconds": freshness,
            "initial_age_seconds": initial,
            "tags": _tags(plan.call, plan.tags, arguments, self.settings),
            "schema_fingerprint": plan.fingerprint,
        }

    def fits(self, received: _Received[T], vary: tuple[str, ...]) -> bool:
        """Return whether the status, origin, size, and cookies, and a revalidation's Vary, let a response be stored."""
        usable = self.usable
        return (
            received.response.info.status_code in self.plan.statuses
            and not received.response.info.redirect_count
            and len(received.body) <= self.max_entry_bytes
            and "set-cookie" not in received.headers
            and (received.source == "network" or usable is None or vary == _vary(usable.headers))
        )

    def expected(self, vary: tuple[str, ...], fingerprints: tuple[bytes, ...]) -> str | None:
        """Return the version a new entry's slot holds as far as the fetch knows: the looked-up entry's, if the same."""
        current = self.entry
        if current is None or (current.vary, current.vary_fingerprints) != (vary, fingerprints):
            return None
        return current.version

    @staticmethod
    def result(received: _Received[T]) -> CacheResult[T]:
        """Return a network or revalidated result."""
        response = received.response
        network = received.source == "network"
        return CacheResult(
            data=response.data,
            source=received.source,
            response=response.info,
            network_status=response.info.status_code if network else _NOT_MODIFIED,
        )


def _validates(stored: HeadersView, update: HeadersView) -> bool:
    """Return whether a 304 names no validator other than the stored one's; ETags compare by their opaque tags."""
    etag, modified = update.get("etag"), update.get("last-modified")
    return (etag is None or _opaque(etag) == _opaque(stored.get("etag"))) and (
        modified is None or modified == stored.get("last-modified")
    )


def _validator(plan: CachePlan[T], headers: HeadersView) -> tuple[str, str] | None:
    """Return the validator header that revalidates a representation as the helper declares, or None."""
    for (header, stored), kind in zip(_VALIDATORS, ("etag", "last_modified"), strict=True):
        if plan.validator in {kind, "both"} and (value := headers.get(stored)) is not None:
            return header, value
    return None


def _tags(
    call: OperationPlan[object, object], templates: tuple[Tag, ...], arguments: tuple[object, ...], settings: Settings
) -> tuple[str, ...]:
    """Return each tag with every argument position filled in by that argument's wire value as text."""
    if not templates:
        return ()
    mode = settings.validation.request
    return tuple(
        "".join(
            part if isinstance(part, str) else _text(call.parameters[part].encode(arguments[part], mode))
            for part in template
        )
        for template in templates
    )


def _configuration(
    plan: CachePlan[T] | CacheMutationPlan[T],
    path: tuple[str, ...],
    condition: Literal["invalid_value", "binding_mismatch", "security_partition", "missing_adapter"],
) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition=condition, helper_id=plan.helper_id, operation=plan.operation
    )


def _invalid(plan: CachePlan[T], path: tuple[str, ...]) -> ProtocolConfigurationError:
    return _configuration(plan, path, "invalid_value")


def _requested(plan: CachePlan[T], values: tuple[str, ...]) -> _Directives:
    """Return the directives a fetch's own Cache-Control gives, refusing any a stored response cannot honor."""
    directives = _directives(values)
    if directives is None or not all(
        name in _REQUEST_DIRECTIVES and (_delta(value) is not None if name == "max-age" else value is None)
        for name, value in directives.items()
    ):
        raise _invalid(plan, ("headers", "Cache-Control"))
    return _Directives("no-store" in directives, "no-cache" in directives, _delta(directives.get("max-age")))


def _store_error(
    plan: CachePlan[T] | CacheMutationPlan[T],
    action: Literal["lookup", "fingerprint_vary", "compare_exchange", "delete"],
    cause: Exception | None = None,
) -> CacheStoreError:
    return CacheStoreError(action=action, helper_id=plan.helper_id, operation=plan.operation, cause=cause)


def _limits(core: ClientCore | AsyncClientCore, plan: CachePlan[T], cache_options: object, options: object) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    A request coding the call selects is refused before any lookup: a fetch sends a bodyless request.
    """
    for name, value, kind in (("cache_options", cache_options, CacheOptions), ("options", options, RequestOptions)):
        if value is not None and not isinstance(value, kind):
            raise _invalid(plan, (name,))
    defaults = core.protocol_defaults(plan.helper_id)
    layers = (cache_options, UNSET if defaults is None else defaults.options)
    request = options if isinstance(options, RequestOptions) else None
    if request is not None and isinstance(selected := request.compression, str):
        from ..client.compression import helper_children  # noqa: PLC0415 - Only a selected coding loads the encoder.

        helper_children(selected, ())
    return _option(layers, "max_entry_bytes", _MAX_ENTRY_BYTES), _option(layers, "max_ttl", _MAX_TTL), request


def _store(core: ClientCore | AsyncClientCore, plan: CachePlan[T] | CacheMutationPlan[T]) -> Any:
    """Return the store the client lends the helper, refusing a client without one before anything is sent."""
    if (store := core.cache_store(plan.helper_id)) is None:
        raise _configuration(plan, ("protocols", "cache_stores", plan.helper_id), "missing_adapter")
    return store


def _checked(
    plan: CachePlan[T],
    action: Literal["lookup", "fingerprint_vary", "compare_exchange", "delete"],
    valid: bool,  # noqa: FBT001
) -> None:
    if not valid:
        raise _store_error(plan, action)


def _fingerprints(plan: CachePlan[T], names: tuple[str, ...], value: object) -> tuple[bytes, ...]:
    """Return a store's Vary fingerprints, refusing anything but one bytes value per name."""
    if not bytes_tuple(value) or len(value) != len(names):
        raise _store_error(plan, "fingerprint_vary")
    return value


def _tags_argument(plan: CachePlan[T], tags: object) -> tuple[str, ...]:
    if not string_tuple(tags):
        raise _invalid(plan, ("tags",))
    return tags


def _invalidation(
    plan: CachePlan[T] | CacheMutationPlan[T], tags: tuple[str, ...], result: object, cause: Exception | None
) -> CacheInvalidationError[object]:
    return CacheInvalidationError(
        tags=tags, completed_result=result, helper_id=plan.helper_id, operation=plan.operation, cause=cause
    )


def _removed(plan: CachePlan[T] | CacheMutationPlan[T], tags: tuple[str, ...], result: object, count: object) -> int:
    """Return the number of entries an invalidation removed, refusing anything but a nonnegative integer."""
    if type(count) is not int or count < 0:
        raise _invalidation(plan, tags, result, None)
    return count


def _run(
    plan: CachePlan[T],
    action: Literal["lookup", "fingerprint_vary", "compare_exchange", "delete"],
    run: Callable[[], V],
) -> V:
    """Run a store method, raising its failure as a cache store error that keeps the cause."""
    try:
        return run()
    except CacheStoreError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _store_error(plan, action, error) from None


async def _arun(
    plan: CachePlan[T],
    action: Literal["lookup", "fingerprint_vary", "compare_exchange", "delete"],
    run: Callable[[], Awaitable[V]],
) -> V:
    """Await a store method, raising its failure as a cache store error that keeps the cause."""
    try:
        return await run()
    except CacheStoreError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _store_error(plan, action, error) from None


def fetch(
    core: ClientCore,
    plan: CachePlan[T],
    arguments: tuple[object, ...],
    *,
    cache_options: CacheOptions | None = None,
    options: RequestOptions | None = None,
) -> CacheResult[T]:
    """Answer a fetch from a fresh stored entry, or send it, revalidating a stale entry, and store what may be stored.

    A response that may not be stored removes the entry it supersedes. Store failures after the network answered
    raise CacheStoreError; the request is never sent again for them.
    """
    limits = _limits(core, plan, cache_options, options)
    store: CacheStore = _store(core, plan)
    state = _Fetch(core, plan, arguments, limits)
    headers, key = state.request.headers, state.base_key
    found = None
    if not state.directives.no_store:
        found = state.found(_run(plan, "lookup", lambda: store.lookup(key, headers)))
    if (hit := state.fresh(found)) is not None:
        return hit
    try:
        received = core.execute_cached(
            plan.call, state.conditional(found), state.settings, state.modified, state.not_modified, state.options
        )
    except CacheProtocolError as error:
        if (usable := state.usable) is not None:
            version = usable.version
            try:
                _checked(plan, "delete", type(_run(plan, "delete", lambda: store.delete(key, version))) is bool)
            except CacheStoreError as failure:
                add_secondary(error, failure)
        raise
    if state.directives.no_store:
        return state.result(received)
    current, expected = state.entry, None
    if (fields := state.stored(received, arguments)) is not None:
        names = fields["vary"]
        prints = _run(plan, "fingerprint_vary", lambda: store.fingerprint_vary(names, headers))
        fingerprints = _fingerprints(plan, names, prints)
        entry = CacheEntry(vary_fingerprints=fingerprints, **fields)
        expected = state.expected(names, fingerprints)
        stored = _run(plan, "compare_exchange", lambda: store.compare_exchange(key, expected, entry))
        _checked(plan, "compare_exchange", type(stored) is bool)
    if current is not None and expected is None:
        version = current.version
        _checked(plan, "delete", type(_run(plan, "delete", lambda: store.delete(key, version))) is bool)
    return state.result(received)


async def afetch(
    core: AsyncClientCore,
    plan: CachePlan[T],
    arguments: tuple[object, ...],
    *,
    cache_options: CacheOptions | None = None,
    options: RequestOptions | None = None,
) -> CacheResult[T]:
    """Fetch as `fetch` does, awaiting the asynchronous store and the asyncio call."""
    limits = _limits(core, plan, cache_options, options)
    store: AsyncCacheStore = _store(core, plan)
    state = _Fetch(core, plan, arguments, limits)
    headers, key = state.request.headers, state.base_key
    found = None
    if not state.directives.no_store:
        found = state.found(await _arun(plan, "lookup", lambda: store.lookup(key, headers)))
    if (hit := state.fresh(found)) is not None:
        return hit
    try:
        received = await core.execute_cached(
            plan.call, state.conditional(found), state.settings, state.modified, state.not_modified, state.options
        )
    except CacheProtocolError as error:
        if (usable := state.usable) is not None:
            version = usable.version
            try:
                _checked(plan, "delete", type(await _arun(plan, "delete", lambda: store.delete(key, version))) is bool)
            except CacheStoreError as failure:
                add_secondary(error, failure)
        raise
    if state.directives.no_store:
        return state.result(received)
    current, expected = state.entry, None
    if (fields := state.stored(received, arguments)) is not None:
        names = fields["vary"]
        prints = await _arun(plan, "fingerprint_vary", lambda: store.fingerprint_vary(names, headers))
        fingerprints = _fingerprints(plan, names, prints)
        entry = CacheEntry(vary_fingerprints=fingerprints, **fields)
        expected = state.expected(names, fingerprints)
        stored = await _arun(plan, "compare_exchange", lambda: store.compare_exchange(key, expected, entry))
        _checked(plan, "compare_exchange", type(stored) is bool)
    if current is not None and expected is None:
        version = current.version
        _checked(plan, "delete", type(await _arun(plan, "delete", lambda: store.delete(key, version))) is bool)
    return state.result(received)


def invalidate(core: ClientCore, plan: CachePlan[T], tags: tuple[str, ...]) -> int:
    """Remove every entry of the helper's store that carries any of the tags, returning how many were removed."""
    checked = _tags_argument(plan, tags)
    store: CacheStore = _store(core, plan)
    try:
        count = store.invalidate(checked)
    except Exception as error:  # noqa: BLE001
        raise _invalidation(plan, checked, UNSET, error) from None
    return _removed(plan, checked, UNSET, count)


async def ainvalidate(core: AsyncClientCore, plan: CachePlan[T], tags: tuple[str, ...]) -> int:
    """Remove the tagged entries as `invalidate` does, awaiting the asynchronous store."""
    checked = _tags_argument(plan, tags)
    store: AsyncCacheStore = _store(core, plan)
    try:
        count = await store.invalidate(checked)
    except Exception as error:  # noqa: BLE001
        raise _invalidation(plan, checked, UNSET, error) from None
    return _removed(plan, checked, UNSET, count)


def mutate(  # noqa: PLR0913
    core: ClientCore,
    plan: CacheMutationPlan[T],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    options: RequestOptions | None = None,
) -> T:
    """Call the mutation's operation as its method does, then remove the entries its tags name after it succeeds.

    The tags are rendered before sending, so arguments that cannot fill them fail before the call. A failed call
    removes nothing. A failed removal raises CacheInvalidationError keeping the call's result, which is
    never sent again.
    """
    store: CacheStore = _store(core, plan)
    tags = _tags(plan.call, plan.tags, arguments, core.call_settings(options, plan.call))
    result = core.execute(plan.call, arguments, body=body, media_type=media_type, options=options).data
    try:
        count = store.invalidate(tags)
    except Exception as error:  # noqa: BLE001
        raise _invalidation(plan, tags, result, error) from None
    _removed(plan, tags, result, count)
    return result


async def amutate(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: CacheMutationPlan[T],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    options: RequestOptions | None = None,
) -> T:
    """Call the mutation as `mutate` does, awaiting the asyncio call and the asynchronous store."""
    store: AsyncCacheStore = _store(core, plan)
    tags = _tags(plan.call, plan.tags, arguments, core.call_settings(options, plan.call))
    result = (await core.execute(plan.call, arguments, body=body, media_type=media_type, options=options)).data
    try:
        count = await store.invalidate(tags)
    except Exception as error:  # noqa: BLE001
        raise _invalidation(plan, tags, result, error) from None
    _removed(plan, tags, result, count)
    return result
