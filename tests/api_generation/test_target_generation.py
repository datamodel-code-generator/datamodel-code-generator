"""Generate the FastAPI target through the public entry points: settings, models, selection, and ownership."""

from __future__ import annotations

import os
import shutil
import sys
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import _api_manifest, _api_publication, _publication
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.remote_lock import RemoteReferenceLock
from tests.conftest import assert_output, freeze_time
from tests.data.python.target_generation import SOURCE, target_config_report, target_render_report
from tests.main.conftest import run_main_and_assert
from tests.test_http import _SchemaHandler, local_http_server  # ruff: ignore[unused-import] - Register the existing fixture.

if TYPE_CHECKING:
    from collections.abc import Callable

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/targets"


@pytest.mark.parametrize(
    "case",
    [
        "first-run",
        "ownership",
        "deletion",
        "conflict",
        "state",
        "unowned",
        "verify",
        "selection",
        "inputs",
        "input-cycles",
        "input-cycle-reference",
        "input-aliases",
        "validation",
        "empty",
        "modular",
        "references",
        "locks",
        "publish",
        "publish-verify",
        "publish-lock",
        "collision",
        "interference",
        "directory",
        "layout-embedded",
        "layout-standalone",
        "layout-external",
        "model-dependencies",
        "layout-errors",
        "format-errors",
        "target-grammar-modern",
        "target-grammar-misplaced",
        "python-floor",
    ],
)
def test_target_render(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate models once, select operations, plan target files, and publish them through a reversible journal."""
    assert_output(target_render_report(case, tmp_path, monkeypatch), EXPECTED / f"{case}.txt")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type statements require Python 3.12")
def test_target_render_target_grammar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject syntax the running Python accepts but the configured target Python does not."""
    assert_output(target_render_report("target-grammar", tmp_path, monkeypatch), EXPECTED / "target-grammar.txt")


@pytest.mark.abnormal_path("the running interpreter is never older than the supported minimum")
def test_target_render_python_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to render a target on a Python older than every target supports."""
    monkeypatch.setattr("datamodel_code_generator._api_generation._PYTHON_MINIMUM", (99, 0))
    assert_output(target_render_report("python-runtime", tmp_path, monkeypatch), EXPECTED / "python-runtime.txt")


def test_target_render_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Head every Python file with the configured lines and one batch timestamp, then encode it."""
    with freeze_time("2026-09-25 12:00:00"):
        assert_output(target_render_report("format", tmp_path, monkeypatch), EXPECTED / "format.txt")


def test_target_render_http(
    local_http_server: str,  # ruff: ignore[redefined-while-unused] - Request the imported fixture.
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


@pytest.mark.abnormal_path("staging the remote reference lock fails only on an I/O error")
def test_target_render_lock_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate a failure to stage the remote lock update after releasing the accepted sources."""

    def fail(*_args: object) -> None:
        msg = "No space left on device"
        raise OSError(msg)

    monkeypatch.setattr(RemoteReferenceLock, "stage", fail)
    assert_output(target_render_report("lock-failure", tmp_path, monkeypatch), EXPECTED / "lock-failure.txt")


@pytest.mark.abnormal_path("os.path.relpath fails only across Windows drives")
def test_target_render_relative_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject layouts whose persistent paths cannot be relative to the target root, such as other drives."""

    def fail(*_args: object) -> str:
        msg = "path is on mount 'C:', start on mount 'D:'"
        raise ValueError(msg)

    monkeypatch.setattr(_api_manifest, "os", SimpleNamespace(path=SimpleNamespace(relpath=fail)))
    assert_output(target_render_report("relative-failure", tmp_path, monkeypatch), EXPECTED / "relative-failure.txt")


def _failing(
    original: Callable[..., None],
    failures: dict[int, BaseException],
    *,
    existing: bool = False,
    destination: str | None = None,
) -> Callable[..., None]:
    calls = count()

    def call(file: _publication.StagedFile, *args: object) -> None:
        if (
            (not existing or file.target.exists())
            and (destination is None or file.target.name == destination)
            and (failure := failures.get(next(calls))) is not None
        ):
            raise failure
        original(file, *args)

    return call


@pytest.mark.parametrize(
    "failure", [OSError("No space left on device"), KeyboardInterrupt()], ids=["error", "interrupt"]
)
@pytest.mark.abnormal_path("publishing fails only when the disk fills or the process is interrupted")
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
@pytest.mark.parametrize(("case", "existing"), [("rollback-created", False), ("rollback-backup", True)])
@pytest.mark.abnormal_path("rollback fails only when restoring the journal also hits an I/O error")
def test_target_generate_rollback_failure(
    case: str, existing: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report the destinations and backups a failed rollback could not restore, keeping the cause."""

    def fail(*_args: object, **_kwargs: object) -> None:
        msg = "Permission denied"
        raise OSError(msg)

    monkeypatch.setattr(
        _publication,
        "_replace_source",
        _failing(_publication._replace_source, {1: OSError("full")}, existing=existing),
    )
    monkeypatch.setattr(_publication, "_restore_backup_at", fail)
    if case == "rollback-created":
        monkeypatch.setattr(_publication, "_unlink", fail)
        monkeypatch.setattr(_publication, "_rmdir", fail)
    assert_output(target_render_report(case, tmp_path, monkeypatch), EXPECTED / f"{case}.txt")


@pytest.mark.abnormal_path("a later staged file fails after the remote lock update has been staged")
def test_target_generate_lock_discard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discard the staged remote lock update when a file after it fails to stage."""
    discarded: list[bool] = []
    discard, stage = RemoteReferenceLock.discard_stage, _api_publication.stage_content

    def recorded(lock: RemoteReferenceLock) -> None:
        discarded.append(lock._staged_source is not None)
        discard(lock)

    def fail(
        staging: _publication.StagingDirectory, content: bytes, file: _publication.StagedFile
    ) -> _publication.StagedFile:
        if file.target.name == ".dcg-target-manifest.json":
            msg = "No space left on device"
            raise OSError(msg)
        return stage(staging, content, file)

    monkeypatch.setattr(RemoteReferenceLock, "discard_stage", recorded)
    monkeypatch.setattr(_api_publication, "stage_content", fail)
    report = target_render_report("publish-lock", tmp_path, monkeypatch)
    assert_output(f"{report}staged lock updates discarded: {discarded.count(True)}\n", EXPECTED / "lock-discard.txt")


@pytest.mark.skipif(os.name == "nt", reason="Windows checks destinations through the lexical fallback")
@pytest.mark.parametrize("failure", ["anchor", "staging", "stage-collision", "stage-exhausted", "stage-closed"])
@pytest.mark.abnormal_path(
    "staging I/O, occupied private names, closed staging and replaced directory anchors require failure injection"
)
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
        case "staging":
            monkeypatch.setattr(_api_publication, "os", SimpleNamespace(**{**vars(_api_publication.os), "fsync": fail}))
        case _:
            create = _api_publication._create_staged_file
            private_name = _publication._private_name

            def staged(staging: _publication.StagingDirectory, *, prefix: str, mode: int) -> tuple[int, str]:
                if failure == "stage-closed":
                    staging.cleanup()
                    return create(staging, prefix=prefix, mode=mode)
                name = f"{prefix}occupied"
                (staging.path / name).write_bytes(b"occupied staged name")
                staging._files.add(name)
                names = count()
                with monkeypatch.context() as patch:
                    patch.setattr(
                        _publication,
                        "_private_name",
                        lambda value: name if failure == "stage-exhausted" or next(names) == 0 else private_name(value),
                    )
                    return create(staging, prefix=prefix, mode=mode)

            monkeypatch.setattr(_api_publication, "_create_staged_file", staged)
            if failure == "stage-exhausted":
                monkeypatch.setattr(_publication, "_private_name", lambda prefix: f"{prefix}reserved")
    assert_output(
        target_render_report("publication-check", tmp_path, monkeypatch), EXPECTED / f"publication-{failure}.txt"
    )


@pytest.mark.abnormal_path("a foreign writer changes a planned file after the final artifact has been staged")
def test_target_generate_state_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recheck all artifacts after staging and preserve bytes another writer changed."""
    stage = _api_publication.stage_content

    def interfere(
        staging: _publication.StagingDirectory, content: bytes, file: _publication.StagedFile
    ) -> _publication.StagedFile:
        staged = stage(staging, content, file)
        if file.target.name == ".dcg-target-manifest.json":
            (tmp_path / "models.py").write_bytes(b"# Written by another generator.\n")
        return staged

    monkeypatch.setattr(_api_publication, "stage_content", interfere)
    assert_output(target_render_report("state-changed", tmp_path, monkeypatch), EXPECTED / "state-changed.txt")


@pytest.mark.parametrize(
    "failure", [OSError("No space left on device"), KeyboardInterrupt()], ids=["error", "interrupt"]
)
@pytest.mark.abnormal_path("the final manifest fails after writes and stale deletions in the same transaction")
def test_target_generate_transaction_rollback(
    failure: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore models, targets, metadata, remote lock, inventory and manifest after deletion and write failures."""
    monkeypatch.setattr(
        _publication,
        "_replace_source",
        _failing(_publication._replace_source, {1: failure}, destination=".dcg-target-manifest.json"),
    )
    assert_output(
        target_render_report("rollback-transaction", tmp_path, monkeypatch),
        EXPECTED / f"rollback-transaction-{type(failure).__name__}.txt",
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes and umask do not apply on Windows")
def test_target_generate_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use directory bits and umask for new files while preserving replacement permissions."""
    mask = os.umask(0o027)
    try:
        assert_output(
            target_render_report("publication-modes", tmp_path, monkeypatch), EXPECTED / "publication-modes.txt"
        )
    finally:
        os.umask(mask)


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
    ],
)
def test_target_config(case: str) -> None:
    """Validate the shared target settings of the public configuration, reporting every problem in field order."""
    assert_output(target_config_report(case), EXPECTED / "configs" / f"{case}.txt")


def _toml_arguments(*extra: str) -> list[str]:
    return [
        *(
            "--target-python-version",
            "3.11",
            "--openapi-scopes",
            "api",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ),
        *("--generate-server", "fastapi", "--target-config", "target.toml", *extra),
    ]


@pytest.mark.parametrize(
    "case",
    [
        "toml-syntax",
        "toml-version",
        "toml-version-bool",
        "toml-version-missing",
        "toml-unknown",
        "toml-missing",
        "toml-values",
        "toml-formatters",
        "toml-selection-unknown",
        "toml-kwargs-date",
        "toml-records",
    ],
)
def test_target_toml_errors(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a flat target file whose syntax, version, keys, values, or records are invalid, writing nothing."""
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SOURCE / "spec", tmp_path / "spec")
    run_main_and_assert(
        input_path=Path("spec/api.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_toml_arguments(),
        copy_files=[(SOURCE / "configs" / f"{case}.toml", tmp_path / "target.toml")],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(capsys.readouterr().err, EXPECTED / "configs" / f"{case}.txt")


@pytest.mark.parametrize(
    ("case", "arguments", "output"),
    [("toml-selection", [], "server"), ("toml-output-override", ["--target-output", "elsewhere"], "elsewhere")],
)
def test_target_toml_values(
    case: str,
    arguments: list[str],
    output: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read the selection and paths of a flat target file, which decide the published files."""
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SOURCE / "spec", tmp_path / "spec")
    run_main_and_assert(
        input_path=Path("spec/api.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_toml_arguments(*arguments),
        copy_files=[(SOURCE / "configs" / f"{case}.toml", tmp_path / "target.toml")],
    )
    published = sorted(
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / output).rglob("*")
        if path.is_file() and "_runtime" not in path.parts
    )
    assert_output(
        capsys.readouterr().err + "\n".join(published) + "\n",
        EXPECTED / "configs" / f"{case}.txt",
    )
