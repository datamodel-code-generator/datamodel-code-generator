"""Preserve protocol records, options, resume state, and error payload types through the public contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pets.errors import (
    OperationCancelledError,
    OperationFailedError,
    PaginationCycleError,
    PollWaitLimitError,
    ProtocolDataError,
    ResumeStateError,
    SessionLimitError,
    StreamRemoteError,
    StreamResumeExhaustedError,
)
from pets.model_codecs import WireValue
from pets.options import UNSET, ClientOptions, ProtocolClientOptions, SessionOptions, Unset
from pets.protocols import (
    AsyncCacheStore,
    BodySelector,
    BodyTarget,
    CacheOptions,
    CacheStore,
    Continuation,
    HeaderSelector,
    Origin,
    PaginationOptions,
    ParameterTarget,
    PollOptions,
    PollSnapshot,
    ProgressKey,
    ProtocolDefaults,
    ProtocolProgress,
    ProtocolSecurityContext,
    QuerystringTarget,
    RequestTarget,
    ResumeState,
    Selector,
    StatusSelector,
    StreamOptions,
    WSOptions,
    import_state,
)
from pets.responses import ResponseInfo
from pets_models import Pet
from typing_extensions import assert_type


def records(snapshot: PollSnapshot[Pet], selector: Selector, target: RequestTarget) -> None:
    """Keep the poll type, the closed selector and target unions, and the continuation kinds."""
    assert_type(snapshot.data, Pet)
    assert_type(snapshot.state, WireValue)
    assert_type(snapshot.terminal, bool)
    assert_type(snapshot.response, ResponseInfo)
    accepts_snapshot(snapshot)
    selectors: tuple[Selector, ...] = (BodySelector(pointer="/next"), HeaderSelector(name="Link"), StatusSelector())
    targets: tuple[RequestTarget, ...] = (
        ParameterTarget(location="query", name="cursor"),
        QuerystringTarget(name="filter", pointer="/cursor"),
        BodyTarget(pointer="/page"),
    )
    assert_type(HeaderSelector(name="Link", occurrence="all").occurrence, Literal["single", "all"])
    assert_type(target, ParameterTarget | QuerystringTarget | BodyTarget)
    assert_type(selector, BodySelector | HeaderSelector | StatusSelector)
    del selectors, targets
    continuation = Continuation(kind="cursor", value={"after": "pet-1", "size": 2})
    assert_type(continuation.kind, Literal["cursor", "offset", "page", "next_url", "link"])
    origin = Origin(scheme="https", host="api.example.com", port=443)
    assert_type(origin.port, int)
    key: ProgressKey = "network_send_budget_used"
    progress: ProtocolProgress = {key: 1, "pages": 2}
    assert_type(progress[key], int)


def accepts_snapshot(snapshot: PollSnapshot[object]) -> None:
    """Accept a more specific immutable poll through the covariant snapshot contract."""
    assert_type(snapshot.data, object)


def options(origin: Origin) -> ClientOptions:
    """Expose each option field type and construct client-level protocol settings."""
    pagination = PaginationOptions(max_pages=None, max_items=0, max_page_bytes=1024)
    assert_type(pagination.max_items, int | Unset | None)
    assert_type(pagination.max_page_bytes, int | Unset)
    polling = PollOptions(interval=0.5, max_wait=None)
    assert_type(polling.interval, float | Unset)
    assert_type(polling.max_wait, float | Unset | None)
    stream = StreamOptions(reconnect=True, max_reconnects=0, idle_timeout=UNSET)
    assert_type(stream.reconnect, bool | Unset)
    assert_type(stream.max_reconnect_wait, float | Unset | None)
    security = ProtocolSecurityContext(credential_partition="tenant", allowed_origins=(origin,))
    assert_type(security.allowed_origins, tuple[Origin, ...])
    defaults = ProtocolDefaults(session=SessionOptions(max_network_sends=3), options=pagination)
    assert_type(defaults.options, PaginationOptions | PollOptions | StreamOptions | CacheOptions | WSOptions | Unset)
    protocols = ProtocolClientOptions(security=security, defaults={"users.all": defaults, "jobs": ProtocolDefaults()})
    assert_type(protocols.security, ProtocolSecurityContext | Unset | None)
    assert_type(protocols.defaults, Mapping[str, ProtocolDefaults] | Unset)
    assert_type(protocols.cache_stores, Mapping[str, CacheStore | AsyncCacheStore] | Unset)
    client = ClientOptions(protocols=protocols)
    assert_type(client.protocols, ProtocolClientOptions | Unset | None)
    return ClientOptions(protocols=None)


def resume(state: ResumeState) -> ResumeState:
    """Export opaque state as bytes and import it back as the same opaque type."""
    exported = state.export()
    assert_type(exported, bytes)
    restored = import_state(exported)
    assert_type(restored, ResumeState)
    return ResumeState(helper_fingerprint="helper", security_fingerprint="security", state={"page": (1, 2)})


def errors(snapshot: PollSnapshot[Pet], state: ResumeState) -> None:
    """Keep typed references, but widen bare catches to object without Any."""
    failure = OperationFailedError[Pet](snapshot=snapshot)
    assert_type(failure.snapshot.data, Pet)
    text_failure = OperationFailedError[str](
        snapshot=PollSnapshot(state="failed", terminal=True, data="reason", response=snapshot.response)
    )
    assert_type(text_failure.snapshot, PollSnapshot[str])
    remote = StreamRemoteError[Pet](event_type="error", data=snapshot.data, sequence=1)
    assert_type(remote.data, Pet)
    limit = SessionLimitError(kind="pages", limit=10, progress={"pages": 10}, resume_state=state)
    assert_type(limit.progress, Mapping[ProgressKey, int])
    assert_type(limit.resume_state, ResumeState | None)
    exhausted = StreamResumeExhaustedError(kind="reconnects", limit=5, progress={"reconnects": 5})
    assert_type(exhausted.kind, Literal["network_sends", "pages", "items", "polls", "reconnects", "parts"])
    cycle = PaginationCycleError(page_index=2, first_seen_page_index=0, location=BodySelector(pointer="/next"))
    assert_type(cycle.location, Selector | RequestTarget | None)
    wait = PollWaitLimitError(kind="wait", required_wait=120, limit=60)
    assert_type(wait.required_wait, float)
    try:
        raise failure
    except OperationFailedError as caught:
        failed(caught)
    try:
        raise remote
    except StreamRemoteError as caught_remote:
        remote_event(caught_remote)
    try:
        raise ResumeStateError(condition="expired")
    except ProtocolDataError as data_error:
        assert_type(data_error.location, Selector | RequestTarget | None)
    except ResumeStateError as resume_error:
        assert_type(
            resume_error.condition,
            Literal["version", "fingerprint", "security", "expired", "malformed", "checksum", "size"],
        )


def failed(error: OperationFailedError, cancelled: OperationCancelledError | None = None) -> None:
    """Widen the payload of an unparameterized failure to object, never Any."""
    assert_type(error.snapshot, PollSnapshot[object])
    assert_type(error.snapshot.data, object)
    if cancelled is not None:
        assert_type(cancelled.snapshot.data, object)


def remote_event(error: StreamRemoteError) -> None:
    """Widen the data of an unparameterized error event to object, never Any."""
    assert_type(error.data, object)
