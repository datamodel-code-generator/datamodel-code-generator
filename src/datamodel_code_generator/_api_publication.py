"""Publish one target's candidates under OS advisory locks through a reversible journal."""

from __future__ import annotations

import os
import threading
import unicodedata
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._api_manifest import observe_file, sha256
from datamodel_code_generator._api_types import APIGenerationError, ArtifactRecord, Diagnostic, GenerationReport

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping

    from datamodel_code_generator._api_manifest import Observed
    from datamodel_code_generator._api_types import GeneratedArtifact, GeneratedProject
    from datamodel_code_generator._publication import BatchEntry, PublicationAnchor, StagingDirectory
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

LOCK_DIRECTORY: Final = PurePosixPath(".dcg-api-state", "locks")

_HELD: dict[Path, threading.Lock] = {}
_HELD_GUARD = threading.Lock()


def lock_path(resource: Path) -> Path:
    """Return the persistent lockfile that coordinates writers of one canonical resource."""
    name = unicodedata.normalize("NFC", resource.name).casefold()
    return resource.parent.joinpath(*LOCK_DIRECTORY.parts, f"{sha256(name.encode())}.lock")


def _failure(*, code: str, path: Path, message: str) -> APIGenerationError:
    return APIGenerationError((
        Diagnostic(code=code, severity="error", stage="publication", message=message, artifact_path=path.as_posix()),
    ))


def _open_lockfile(path: Path) -> int:
    from datamodel_code_generator._publication import new_file_mode, open_directory  # noqa: PLC0415

    if os.name == "nt":  # pragma: no cover - Windows opens lockfiles by path
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, new_file_mode(None, path.parent))
    else:
        directory_fd = open_directory(path.parent)
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
            descriptor = os.open(path.name, flags, new_file_mode(directory_fd, path.parent), dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
    if not os.fstat(descriptor).st_size:
        os.write(descriptor, b"\0")
    return descriptor


def _acquire(descriptor: int, path: Path) -> None:
    if os.name == "nt":  # pragma: no cover - Windows locks the first byte
        import msvcrt  # noqa: PLC0415

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise _failure(
                code="E_OUTPUT_BUSY", path=path, message="Another generation holds the output lock"
            ) from None
        return
    import fcntl  # noqa: PLC0415

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise _failure(code="E_OUTPUT_BUSY", path=path, message="Another generation holds the output lock") from None
    except OSError:
        raise _failure(
            code="E_OUTPUT_LOCK_UNSUPPORTED", path=path, message="The file system does not support advisory locks"
        ) from None


def _release(descriptor: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows unlocks the first byte
        import msvcrt  # noqa: PLC0415

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl  # noqa: PLC0415

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _locked(path: Path, resource: Path) -> Generator[None, None, None]:
    with _HELD_GUARD:
        held = _HELD.setdefault(path, threading.Lock())
    if not held.acquire(blocking=False):
        raise _failure(
            code="E_OUTPUT_BUSY", path=resource, message="Another generation in this process holds the output lock"
        )
    try:
        descriptor = _open_lockfile(path)
        try:
            _acquire(descriptor, resource)
            try:
                yield
            finally:
                _release(descriptor)
        finally:
            os.close(descriptor)
    finally:
        held.release()


@contextmanager
def resource_locks(resources: Iterable[Path]) -> Generator[None, None, None]:
    """Hold one nonblocking lock per distinct resource, acquired in canonical-path order."""
    with ExitStack() as stack:
        for path, resource in sorted({lock_path(resource): resource for resource in resources}.items()):
            stack.enter_context(_locked(path, resource))
        yield


def _base(path: Path) -> Path:
    while not path.is_dir():
        path = path.parent
    return path


class _Batch:
    def __init__(self, stack: ExitStack) -> None:
        self.stack = stack
        self.anchors: dict[Path, PublicationAnchor] = {}
        self.stagings: dict[Path, StagingDirectory] = {}

    def anchor(self, base: Path) -> PublicationAnchor:
        from datamodel_code_generator._publication import close_anchor, publication_anchor  # noqa: PLC0415

        if (anchor := self.anchors.get(base)) is None:
            anchor = self.anchors[base] = publication_anchor(base)
            self.stack.callback(close_anchor, anchor)
        return anchor

    def staging(self, base: Path) -> StagingDirectory:
        from datamodel_code_generator._publication import StagingDirectory  # noqa: PLC0415

        if (staging := self.stagings.get(base)) is None:
            staging = self.stagings[base] = StagingDirectory.create(self.anchor(base), prefix=".datamodel-codegen-")
            self.stack.callback(_quietly, staging.cleanup)
        return staging

    def entry(self, artifact: GeneratedArtifact, target: Path, lock: RemoteReferenceLock | None) -> BatchEntry:
        from datamodel_code_generator._publication import BatchEntry, StagedFile, stage_content  # noqa: PLC0415

        resolved = target.parent.resolve(strict=False) / target.name
        file = StagedFile(None, target, resolved, self.anchor(base := _base(resolved.parent)))
        match artifact.action, artifact.content:
            case "write", _ if lock is not None and isinstance(staged := lock.stage(self.staging(base)), StagedFile):
                return BatchEntry("write", staged._replace(anchor=file.anchor))
            case "write", bytes() as content:
                return BatchEntry("write", stage_content(self.staging(base), content, file))
            case _:
                return BatchEntry("delete", file)


def _quietly(cleanup: Callable[[], None]) -> None:
    with suppress(OSError):
        cleanup()


def _record(artifact: GeneratedArtifact, digest: str, size: int) -> ArtifactRecord:
    return ArtifactRecord(
        path=artifact.path, kind=artifact.kind, sha256=digest, size=size, target_id=artifact.target_id
    )


def publish_project(
    project: GeneratedProject,
    observed: Mapping[Path, Observed],
    *,
    cwd: Path,
    resources: Iterable[Path],
    lock: RemoteReferenceLock | None,
) -> GenerationReport:
    """Recheck every planned file inside the resource locks, then publish the changes as one journal."""
    from datamodel_code_generator._publication import publish_batch  # noqa: PLC0415

    with resource_locks(resources):
        if changed := [
            artifact
            for artifact in project.artifacts
            if observe_file(location := cwd / artifact.path) != observed[location]
        ]:
            raise APIGenerationError(
                tuple(
                    Diagnostic(
                        code="E_STATE_CHANGED",
                        severity="error",
                        stage="publication",
                        message="The file changed after the target was planned",
                        artifact_path=artifact.path.as_posix(),
                        target_id=artifact.target_id,
                    )
                    for artifact in changed
                )
            )
        with ExitStack() as stack:
            batch = _Batch(stack)
            entries = [
                batch.entry(artifact, cwd / artifact.path, lock if artifact.kind == "remote_lock" else None)
                for artifact in project.artifacts
                if artifact.action != "unchanged"
            ]
            try:
                publish_batch(entries)
            except BaseException:
                if lock is not None:
                    with suppress(OSError):
                        lock.discard_stage()
                raise
        if lock is not None:
            lock.mark_committed()
    return GenerationReport(
        target=project.target,
        written_files=tuple(
            _record(artifact, sha256(content), len(content))
            for artifact in project.artifacts
            if artifact.action == "write" and (content := artifact.content) is not None
        ),
        unchanged_files=tuple(
            _record(artifact, sha256(content), len(content))
            for artifact in project.artifacts
            if artifact.action == "unchanged" and (content := artifact.content) is not None
        ),
        deleted_files=tuple(
            _record(artifact, *state)
            for artifact in project.artifacts
            if artifact.action == "delete" and (state := observed[cwd / artifact.path]) is not None
        ),
        diagnostics=project.diagnostics,
        generator_version=project.generator_version,
        runtime_revision=project.runtime_revision,
    )
