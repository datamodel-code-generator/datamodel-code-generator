"""Generate the FastAPI target through the public entry points: settings, models, and written files."""

from __future__ import annotations

import errno
import os
import shutil
import sys
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import _api_manifest, _publication
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.remote_lock import RemoteReferenceLock
from datamodel_code_generator.util import get_yaml_backend
from tests.conftest import assert_output, freeze_time
from tests.data.python.target_generation import SOURCE, target_config_report, target_render_report
from tests.main.conftest import run_main_and_assert, run_main_with_args
from tests.test_http import _SchemaHandler, local_http_server  # ruff: ignore[unused-import] - Register the existing fixture.

if TYPE_CHECKING:
    from collections.abc import Callable

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/targets"


@pytest.mark.parametrize(
    "case",
    [
        "first-run",
        "newlines",
        "overwrite",
        "stale",
        "include-paths",
        "path-items",
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
        "publish-lock",
        "collision",
        "nested-models",
        "nested-package",
        "directory",
        "layout-embedded",
        "model-dependencies",
        "format-errors",
        "target-grammar-modern",
        "target-grammar-misplaced",
        "python-floor",
    ],
)
def test_target_render(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate models once, plan target files, and write them like model output: overwrite, never delete."""
    expected = f"{case}.txt"
    if case.startswith("input-cycle"):
        expected = {"pyyaml": f"{case}.txt", "ryaml": f"{case}-ryaml.txt"}[get_yaml_backend()]
    assert_output(target_render_report(case, tmp_path, monkeypatch), EXPECTED / expected)


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


def _failing(original: Callable[..., None], failures: dict[int, OSError], destination: str) -> Callable[..., None]:
    calls = count()

    def call(file: _publication.StagedFile, *args: object) -> None:
        if file.target.name == destination and (failure := failures.get(next(calls))) is not None:
            raise failure
        original(file, *args)

    return call


@pytest.mark.abnormal_path("publishing fails only on an I/O error such as a full disk")
def test_target_generate_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the models, the package, the metadata and the lock together, and discard the staged lock update."""
    discarded: list[bool] = []
    discard, failure = RemoteReferenceLock.discard_stage, OSError("No space left on device")

    def recorded(lock: RemoteReferenceLock) -> None:
        discarded.append(lock._staged_source is not None)
        discard(lock)

    monkeypatch.setattr(RemoteReferenceLock, "discard_stage", recorded)
    monkeypatch.setattr(
        _publication,
        "_replace_source",
        _failing(_failing(_publication._replace_source, {0: failure}, "remote.lock"), {2: failure}, "models.json"),
    )
    report = target_render_report("publish-failure", tmp_path, monkeypatch)
    assert_output(f"{report}staged lock updates discarded: {discarded.count(True)}\n", EXPECTED / "publish-failure.txt")


@pytest.mark.parametrize(
    ("case", "mounted"), [("mount-points", ["models", "server"]), ("nested-mount-point", ["server/routers"])]
)
@pytest.mark.abnormal_path("a rename fails with EXDEV only between filesystems, which needs a mounted output")
def test_target_generate_mount_points(
    case: str, mounted: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write into output directories that are mount points: each file is staged inside the directory it goes to.

    The replacement refuses, as the operating system does, every move from outside a mount point into it. A mount
    point below an output directory is such a move, which the journal rolls back, as it does for model output.
    """
    mounts = {tmp_path.resolve() / name for name in mounted}
    replace = _publication._replace_source

    def replaced(file: _publication.StagedFile, *args: object) -> None:
        source = None if file.staged_file is None else file.staged_file.resolve()
        for mount in mounts.intersection(file.resolved_target.parents):
            if source is not None and mount not in source.parents:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
        replace(file, *args)

    monkeypatch.setattr(_publication, "_replace_source", replaced)
    assert_output(target_render_report(case, tmp_path, monkeypatch), EXPECTED / f"{case}.txt")


@pytest.mark.abnormal_path("publishing a batch fails only on an I/O error such as a full disk")
def test_target_jobs_rollback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish nothing of a batch of a model job and a server job when a file of the last job cannot be written.

    The first batch fails on a new tree; the second fails over edited files of both jobs, which keep their edits.
    """
    fixtures = SOURCE.parent / "fastapi"
    monkeypatch.chdir(tmp_path)
    shutil.copy2(fixtures / "pets.yaml", "pets.yaml")
    shutil.copy2(fixtures / "cli" / "pyproject-mixed-jobs.toml", "pyproject.toml")
    replace, failure = _publication._replace_source, OSError("No space left on device")
    lines: list[str] = []

    def batch(title: str, failures: dict[int, OSError]) -> None:
        monkeypatch.setattr(_publication, "_replace_source", _failing(replace, failures, "services.py"))
        run_main_with_args(["--all-jobs"], expected_exit=Exit.ERROR if failures else Exit.OK)
        stderr = capsys.readouterr().err
        files = sorted(path.relative_to(tmp_path).parts[0] for path in tmp_path.rglob("*") if path.is_file())
        lines.extend((f"# {title}", stderr.replace(tmp_path.resolve().as_posix(), "<root>").rstrip("\n")))
        lines.append(f"  outputs {sorted(set(files) - {'pets.yaml', 'pyproject.toml'})}")

    batch("the server job's services.py cannot be written", {0: failure})
    batch("the batch publishes", {})
    edited = dict.fromkeys(("schemas.py", "models.py", "server/services.py"), b"# Edited.\n")
    for name, content in edited.items():
        Path(name).write_bytes(content)
    batch("the server job's services.py cannot be replaced", {0: failure})
    lines.extend(f"  {name} still edited {Path(name).read_bytes() == content}" for name, content in edited.items())
    assert_output("\n".join(lines) + "\n", EXPECTED / "jobs-publish-failure.txt")


@pytest.mark.skipif(os.name == "nt", reason="directory symlink creation requires elevated privileges")
def test_target_render_nested_models_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Render models written through a symlink to the target directory without staging anything inside it.

    The target directory is read-only, so a private directory created there would fail the render.
    """
    assert_output(
        target_render_report("nested-models-symlink", tmp_path, monkeypatch), EXPECTED / "nested-models-symlink.txt"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes and umask do not apply on Windows")
def test_target_generate_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create new files with the umask default, as model output does, while preserving replacement permissions."""
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
    ],
)
def test_target_config(case: str) -> None:
    """Validate the shared target settings of the public configuration, reporting every problem in field order."""
    assert_output(target_config_report(case), EXPECTED / "configs" / f"{case}.txt")


def test_target_pyproject_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish the package where --server-output points instead of the pyproject.toml server output."""
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SOURCE / "spec", tmp_path / "spec")
    run_main_and_assert(
        input_path=Path("spec/api.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[
            *("--target-python-version", "3.11", "--openapi-scopes", "api", "--disable-timestamp"),
            *("--formatters", "builtin", "--server-output", "elsewhere"),
        ],
        copy_files=[(SOURCE / "configs" / "pyproject-output-override.toml", tmp_path / "pyproject.toml")],
        file_should_not_exist=tmp_path / "server",
    )
    published = sorted(
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / "elsewhere").rglob("*")
        if path.is_file() and "_runtime" not in path.parts
    )
    assert_output(
        capsys.readouterr().err + "\n".join(published) + "\n",
        EXPECTED / "configs" / "pyproject-output-override.txt",
    )
