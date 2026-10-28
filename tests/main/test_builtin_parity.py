"""Tests for built-in formatter parity helpers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.main._builtin_parity import _http_get_is_mocked


@pytest.mark.parametrize("module_name", ["httpx2", "httpx"])
@pytest.mark.allow_direct_assert
def test_http_mock_detection_checks_each_loaded_backend(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    """Detect a mocked get call without importing either optional HTTP backend."""
    backend = ModuleType(module_name)
    backend.get = SimpleNamespace(mock_calls=())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, backend)

    assert _http_get_is_mocked()
