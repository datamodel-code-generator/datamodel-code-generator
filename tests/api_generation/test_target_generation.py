"""Render fixture targets through the single-target coordinator: settings, models, selection, and ownership."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from datamodel_code_generator import _api_manifest
from datamodel_code_generator.remote_lock import RemoteReferenceLock
from tests.conftest import assert_output
from tests.data.python.target_generation import SOURCE, target_config_report, target_render_report
from tests.test_http import _SchemaHandler, local_http_server  # noqa: F401 - Register the existing fixture.

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/targets"


@pytest.mark.parametrize(
    "case",
    [
        "first-run",
        "ownership",
        "deletion",
        "conflict",
        "state",
        "verify",
        "selection",
        "inputs",
        "validation",
        "empty",
        "modular",
        "provenance",
        "references",
        "locks",
    ],
)
def test_target_render(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate models once, select operations, and plan owned, create-only, and management files."""
    assert_output(target_render_report(case, tmp_path, monkeypatch), EXPECTED / f"{case}.txt")


def test_target_render_http(
    local_http_server: str,  # noqa: F811 - Request the imported fixture.
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record URL documents by origin and content, merging equal documents read from one origin."""
    documents = SOURCE / "spec" / "http"
    routes = {f"/{path.relative_to(documents).as_posix()}": path for path in documents.rglob("*.json")}
    for route, path in routes.items():
        _SchemaHandler.routes[route] = (200, {"content-type": "application/json"}, path.read_bytes())
    try:
        assert_output(target_render_report("http", tmp_path, monkeypatch, local_http_server), EXPECTED / "http.txt")
    finally:
        for route in routes:
            del _SchemaHandler.routes[route]


def test_target_render_lock_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate a failure to stage the remote lock update after releasing the accepted sources."""

    def fail(*_args: object) -> None:
        msg = "No space left on device"
        raise OSError(msg)

    monkeypatch.setattr(RemoteReferenceLock, "stage", fail)
    assert_output(target_render_report("lock-failure", tmp_path, monkeypatch), EXPECTED / "lock-failure.txt")


def test_target_render_relative_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject layouts whose persistent paths cannot be relative to the target root, such as other drives."""

    def fail(*_args: object) -> str:
        msg = "path is on mount 'C:', start on mount 'D:'"
        raise ValueError(msg)

    monkeypatch.setattr(_api_manifest, "os", SimpleNamespace(path=SimpleNamespace(relpath=fail)))
    assert_output(target_render_report("relative-failure", tmp_path, monkeypatch), EXPECTED / "relative-failure.txt")


@pytest.mark.parametrize(
    "case",
    [
        "defaults",
        "invalid-values",
        "selection-type",
        "selection-problems",
        "standalone-missing",
        "embedded-standalone-fields",
        "standalone",
        "toml-syntax",
        "toml-version",
        "toml-version-bool",
        "toml-version-missing",
        "toml-unknown",
        "toml-missing",
        "toml-values",
        "toml-formatters",
        "toml-selection",
        "toml-selection-unknown",
        "toml-records",
        "toml-record-errors",
        "toml-record-values",
        "toml-record-backend",
        "toml-kwargs-date",
        "toml-output-override",
    ],
)
def test_target_config(case: str, tmp_path: Path) -> None:
    """Validate shared target settings from Python values and from a flat TOML file."""
    assert_output(target_config_report(case, tmp_path), EXPECTED / "configs" / f"{case}.txt")
