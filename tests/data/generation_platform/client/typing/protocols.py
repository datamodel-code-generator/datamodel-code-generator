"""Preserve protocol records, options, and error payload types through the public contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pets import AsyncClient, Client
from pets.errors import ConfigurationError, ProtocolDataError, SessionLimitError, StreamInterruptedError
from pets.model_codecs import JSONValue
from pets.options import UNSET, Unset
from pets.protocols import (
    AsyncMemoryCacheStore,
    BodySelector,
    BodyTarget,
    CacheOptions,
    HeaderSelector,
    Origin,
    PaginationOptions,
    ParameterTarget,
    PollOptions,
    PollSnapshot,
    ProgressKey,
    ProtocolProgress,
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


def options() -> Client:
    """Expose each option field type and construct clients with helper defaults and lent stores."""
    pagination = PaginationOptions(max_pages=None, max_items=0, total_timeout=30)
    assert_type(pagination.max_items, int | Unset | None)
    assert_type(pagination.max_pages, int | Unset | None)
    assert_type(pagination.total_timeout, float | Unset | None)
    polling = PollOptions(interval=0.5, max_wait=None, total_timeout=None)
    assert_type(polling.interval, float | Unset)
    assert_type(polling.max_wait, float | Unset | None)
    stream = StreamOptions(reconnect=True, max_reconnects=0, idle_timeout=UNSET, total_timeout=0)
    assert_type(stream.reconnect, bool | Unset)
    assert_type(stream.max_reconnect_wait, float | Unset | None)
    assert_type(UploadOptions(total_timeout=30).total_timeout, float | Unset | None)
    assert_type(WSOptions(total_timeout=30).total_timeout, float | Unset | None)
    defaults: Mapping[str, PaginationOptions | PollOptions | StreamOptions | CacheOptions] = {
        "pages.all": pagination,
        "jobs.run": polling,
        "events.watch": stream,
        "jobs.cached": CacheOptions(max_ttl=60),
    }
    AsyncClient(helper_defaults=None, cache_stores={"jobs.cached": AsyncMemoryCacheStore()})
    return Client(helper_defaults=defaults, cache_stores=None)


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
