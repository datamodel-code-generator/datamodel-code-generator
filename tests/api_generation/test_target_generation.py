"""Render fixture targets through the single-target coordinator: settings, models, selection, and ownership."""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import _api_manifest, _api_publication, _publication
from datamodel_code_generator.remote_lock import RemoteReferenceLock
from tests.conftest import assert_output
from tests.data.python.target_generation import SOURCE, target_config_report, target_render_report
from tests.test_http import _SchemaHandler, local_http_server  # noqa: F401 - Register the existing fixture.

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable

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
        "publish",
        "publish-verify",
        "publish-lock",
        "busy",
        "collision",
        "interference",
        "directory",
    ],
)
def test_target_render(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate models once, select operations, plan target files, and publish them under the resource locks."""
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


def _failing(original: Callable[..., None], failures: dict[int, BaseException]) -> Callable[..., None]:
    calls = count()

    def call(*args: object) -> None:
        if (failure := failures.get(next(calls))) is not None:
            raise failure
        original(*args)

    return call


@pytest.mark.parametrize(
    "failure", [OSError("No space left on device"), KeyboardInterrupt()], ids=["error", "interrupt"]
)
def test_target_generate_rollback(failure: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo every journaled change in reverse order and keep the original failure, including interrupts."""
    monkeypatch.setattr(
        _publication, "_replace_source", _failing(_publication._replace_source, {4: failure, 13: failure})
    )
    assert_output(
        target_render_report("publish-failure", tmp_path, monkeypatch),
        EXPECTED / f"publish-failure-{type(failure).__name__}.txt",
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows restores backups through the lexical fallback")
@pytest.mark.parametrize(("case", "failed_call"), [("rollback-created", 1), ("rollback-backup", 7)])
def test_target_generate_rollback_failure(
    case: str, failed_call: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report the destinations and backups a failed rollback could not restore, keeping the cause."""

    def fail(*_args: object, **_kwargs: object) -> None:
        msg = "Permission denied"
        raise OSError(msg)

    monkeypatch.setattr(
        _publication, "_replace_source", _failing(_publication._replace_source, {failed_call: OSError("full")})
    )
    monkeypatch.setattr(_publication, "_restore_backup_at", fail)
    if case == "rollback-created":
        monkeypatch.setattr(_publication, "_unlink", fail)
        monkeypatch.setattr(_publication, "_rmdir", fail)
    assert_output(target_render_report(case, tmp_path, monkeypatch), EXPECTED / f"{case}.txt")


@pytest.mark.skipif(os.name == "nt", reason="Windows checks destinations through the lexical fallback")
@pytest.mark.parametrize("failure", ["anchor", "staging"])
def test_target_generate_publication_checks(failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop when staging fails, and undo the journal when a destination directory is replaced while publishing."""

    def fail(*_args: object) -> None:
        msg = "Input/output error"
        raise OSError(msg)

    match failure:
        case "anchor":
            checks = count()
            matches = _publication._directory_fd_matches_path
            monkeypatch.setattr(
                _publication, "_directory_fd_matches_path", lambda *args: next(checks) != 2 and matches(*args)
            )
        case _:
            monkeypatch.setattr(_publication, "os", SimpleNamespace(**{**vars(_publication.os), "fsync": fail}))
    assert_output(
        target_render_report("publication-check", tmp_path, monkeypatch), EXPECTED / f"publication-{failure}.txt"
    )


def test_target_generate_lock_unsupported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to publish without an advisory lock instead of writing unlocked."""
    fcntl = pytest.importorskip("fcntl")

    def fail(*_args: object) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", fail)
    assert_output(target_render_report("lock-unsupported", tmp_path, monkeypatch), EXPECTED / "lock-unsupported.txt")


def test_target_generate_state_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recheck every planned file inside the locks and refuse files another writer changed."""
    locks = _api_publication.resource_locks

    @contextmanager
    def interfere(resources: Iterable[Path]) -> Generator[None, None, None]:
        with locks(resources):
            (tmp_path / "models.py").write_bytes(b"# Written by another generator.\n")
            yield

    monkeypatch.setattr(_api_publication, "resource_locks", interfere)
    assert_output(target_render_report("state-changed", tmp_path, monkeypatch), EXPECTED / "state-changed.txt")


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
