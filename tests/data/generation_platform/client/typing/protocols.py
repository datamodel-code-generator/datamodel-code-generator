"""Preserve protocol records, options, and error payload types through the public contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pets import AsyncClient, Client
from pets.errors import ConfigurationError, ProtocolDataError, SessionLimitError, StreamInterruptedError
from pets.model_codecs import JSONValue
from pets.options import UNSET, ProtocolClientOptions, SessionOptions
from pets.protocols import (
    AsyncCacheStore,
    BodySelector,
    BodyTarget,
    CacheOptions,
    CacheStore,
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
    Selector,
    StatusSelector,
    StreamOptions,
    UploadOptions,
    WSOptions,
)
from pets.responses import ResponseInfo
from pets_models import Pet
from typing_extensions import assert_type


def records(snapshot: PollSnapshot[Pet], selector: Selector, target: RequestTarget) -> None:
    """Keep the poll type, the closed selector and target unions, and the continuation kinds."""
    assert_type(snapshot.data, Pet)
    assert_type(snapshot.state, JSONValue)
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
    origin = Origin(scheme="https", host="api.example.com", port=443)
    assert_type(origin.port, int)
    key: ProgressKey = "pages"
    progress: ProtocolProgress = {key: 1, "pages": 2}
    assert_type(progress[key], int)


def accepts_snapshot(snapshot: PollSnapshot[object]) -> None:
    """Accept a more specific immutable poll through the covariant snapshot contract."""
    assert_type(snapshot.data, object)


def options(origin: Origin) -> Client:
    """Expose each option field type and construct client-level protocol settings."""
    pagination = PaginationOptions(max_pages=None, max_items=0)
    assert_type(pagination.max_items, int | UNSET | None)
    assert_type(pagination.max_pages, int | UNSET | None)
    polling = PollOptions(interval=0.5, max_wait=None)
    assert_type(polling.interval, float | UNSET)
    assert_type(polling.max_wait, float | UNSET | None)
    stream = StreamOptions(reconnect=True, max_reconnects=0, idle_timeout=UNSET)
    assert_type(stream.reconnect, bool | UNSET)
    assert_type(stream.max_reconnect_wait, float | UNSET | None)
    security = ProtocolSecurityContext(credential_partition="tenant", allowed_origins=(origin,))
    assert_type(security.allowed_origins, tuple[Origin, ...])
    defaults = ProtocolDefaults(session=SessionOptions(total_timeout=30), options=pagination)
    assert_type(
        defaults.options,
        PaginationOptions | PollOptions | StreamOptions | CacheOptions | WSOptions | UploadOptions | UNSET,
    )
    protocols = ProtocolClientOptions(security=security, defaults={"users.all": defaults, "jobs": ProtocolDefaults()})
    assert_type(protocols.security, ProtocolSecurityContext | UNSET | None)
    assert_type(protocols.defaults, Mapping[str, ProtocolDefaults] | UNSET)
    assert_type(protocols.cache_stores, Mapping[str, CacheStore | AsyncCacheStore] | UNSET)
    AsyncClient(protocols=None)
    return Client(protocols=protocols)


def errors(snapshot: PollSnapshot[Pet]) -> None:
    """Keep the helper errors' fields, with received data as object, never Any."""
    failure = ProtocolDataError(reason="operation_failed", data=snapshot.data, info=snapshot.response)
    assert_type(failure.data, object)
    assert_type(failure.reason, str)
    limit = SessionLimitError(reason="pages", limit=10, progress={"pages": 10})
    assert_type(limit.progress, Mapping[ProgressKey, int])
    assert_type(limit.limit, float)
    wait = SessionLimitError(reason="wait", limit=60, required_wait=120)
    assert_type(wait.required_wait, float | None)
    cycle = ProtocolDataError(reason="pagination_cycle", location=BodySelector(pointer="/next"))
    assert_type(cycle.location, Selector | RequestTarget | None)
    interrupted = StreamInterruptedError(reason="eof", sequence=3)
    assert_type(interrupted.sequence, int)
    try:
        raise ConfigurationError(field_path=("state",), reason="expired")  # ruff: ignore[raise-within-try] - Exercise exception type narrowing.
    except ProtocolDataError as data_error:
        assert_type(data_error.location, Selector | RequestTarget | None)
    except ConfigurationError as resume_error:
        assert_type(resume_error.reason, str)
