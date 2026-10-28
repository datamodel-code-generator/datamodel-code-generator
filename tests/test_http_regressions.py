"""Regression tests for the public HTTP API."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, cast, get_type_hints

import pytest

import datamodel_code_generator.http as http_module
from datamodel_code_generator import HTTPBackend
from datamodel_code_generator.http import _get_http_stack, _HTTPStack, _load_http_stack, get_body

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock import MockerFixture

    from datamodel_code_generator.http import _HTTPTransport, _HTTPTransportFactory


@pytest.mark.allow_direct_assert
def test_get_body_type_hints_are_runtime_resolvable() -> None:
    """Keep public annotations introspectable for framework and IDE consumers."""
    hints = get_type_hints(get_body)

    assert hints["headers"] == Sequence[tuple[str, str]] | None
    assert hints["query_parameters"] == Sequence[tuple[str, str]] | None
    assert hints["return"] is str


@pytest.mark.allow_direct_assert
def test_http_transport_factory_is_process_cached() -> None:
    """Build one matched transport class during a concurrent cold start."""
    worker_count = 8
    barrier = Barrier(worker_count)
    stack = _load_http_stack("httpx")

    def load_transport(_: int) -> _HTTPTransportFactory[_HTTPTransport]:
        barrier.wait()
        return stack.transport_type

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        transport_types = tuple(executor.map(load_transport, range(worker_count)))

    assert all(transport_type is transport_types[0] for transport_type in transport_types)


@pytest.mark.allow_direct_assert
def test_http_stack_is_process_cached_during_concurrent_cold_start(monkeypatch: MonkeyPatch) -> None:
    """Create only one real stack when threads select the same backend concurrently."""
    worker_count = 8
    barrier = Barrier(worker_count)
    monkeypatch.setattr(http_module, "_HTTP_STACKS", {})

    def select_httpx(_: int) -> _HTTPStack[_HTTPTransport]:
        barrier.wait()
        return _get_http_stack(HTTPBackend.HTTPX)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        stacks = tuple(executor.map(select_httpx, range(worker_count)))

    assert all(stack is stacks[0] for stack in stacks)


def test_http_stack_rejects_unknown_public_policy() -> None:
    """Keep the public selector exhaustive if an unvalidated value crosses the typed boundary."""
    with pytest.raises(AssertionError, match="Unexpected HTTP backend policy"):
        _get_http_stack(cast("HTTPBackend", "invalid"))


def test_get_body_validates_the_paired_core_before_fetching(mocker: MockerFixture) -> None:
    """Report a missing paired core instead of fetching or falling back."""
    stack = _load_http_stack("httpx")
    missing_httpcore = ModuleNotFoundError("No module named 'httpcore'", name="httpcore")
    mocker.patch("datamodel_code_generator.http._get_http_stack", return_value=stack)
    mocker.patch("importlib.import_module", side_effect=missing_httpcore)

    with pytest.raises(ModuleNotFoundError, match="httpcore"):
        get_body("https://example.com/schema.json")
