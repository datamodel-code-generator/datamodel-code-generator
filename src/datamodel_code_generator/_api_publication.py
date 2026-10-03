"""Publish target candidates through staged sources and a reversible journal."""

# ruff: noqa: EM101, EM102, SLF001, PERF203, TRY003, TRY301

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING, Literal, NamedTuple, TypeAlias, cast

from datamodel_code_generator._api_manifest import observe_file, sha256
from datamodel_code_generator._api_types import (
    APIGenerationError,
    ArtifactRecord,
    Diagnostic,
    GenerationReport,
    PublicationRollbackError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator import _publication
    from datamodel_code_generator._api_manifest import Observed
    from datamodel_code_generator._api_types import GeneratedArtifact, GeneratedProject
    from datamodel_code_generator._publication import PublicationAnchor, StagedFile, StagingDirectory
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

BatchAction: TypeAlias = Literal["write", "delete"]


class BatchEntry(NamedTuple):
    """One journaled change: replace with a staged source, or delete."""

    action: BatchAction
    file: StagedFile


def new_file_mode(directory_fd: int | None, directory: Path) -> int:
    """Return the read and write bits a new file takes from its directory; the umask still applies."""
    return stat.S_IMODE((directory.stat() if directory_fd is None else os.fstat(directory_fd)).st_mode) & 0o666


def _create_staged_file(staging: StagingDirectory, *, prefix: str, mode: int) -> tuple[int, str]:
    """Create a target source using its directory mode and the current umask."""
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    if staging._closed:
        raise OSError("private staging directory is already closed")
    if staging.directory_fd is None:  # pragma: no cover - Windows lexical fallback
        return staging.create_file(prefix=prefix)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        name = _publication._private_name(prefix)
        try:
            file_fd = os.open(name, flags, mode, dir_fd=staging.directory_fd)
        except FileExistsError:
            continue
        staging._files.add(name)
        return file_fd, name
    raise FileExistsError(f"could not reserve private staged file under {staging.path}")


def stage_content(staging: StagingDirectory, content: bytes, file: StagedFile) -> StagedFile:
    """Stage target bytes with the required publication anchor and destination mode."""
    anchor = cast("PublicationAnchor", file.anchor)
    mode = new_file_mode(anchor.directory_fd, anchor.path)
    file_fd, name = _create_staged_file(staging, prefix=f".{file.resolved_target.name}.", mode=mode)
    try:
        with os.fdopen(file_fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(OSError):
            staging.discard_file(name)
        raise
    if staging.directory_fd is None:  # pragma: no cover - Windows lexical fallback
        return file._replace(staged_file=staging.path / name)
    return file._replace(staged_file=None, source_directory_fd=staging.directory_fd, source_name=name)


def _backup_before_change_at(entry: BatchEntry, directory_fd: int) -> tuple[str | None, int | None]:
    """Back up the destination a write replaces or a deletion removes, returning the backup and the mode to keep."""
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    name = entry.file.resolved_target.name
    try:
        target_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if entry.action == "delete":
            raise
        return None, None
    if stat.S_ISDIR(target_stat.st_mode):
        raise IsADirectoryError(f"[Errno 21] Is a directory: '{entry.file.target}'")
    mode = stat.S_IMODE(target_stat.st_mode) if stat.S_ISREG(target_stat.st_mode) else None
    return _publication._backup_existing_target_at(directory_fd, name, target_stat), mode


def _publish_entry_at(
    entry: BatchEntry,
    journal: list[tuple[_publication._BoundPublishedFile, Path]],
    created_directories: list[_publication._CreatedDirectoryAt],
) -> None:
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    file = entry.file
    _publication._validate_publication_anchor(file)
    parent = file.resolved_target.parent
    directory_fd = _publication._open_target_directory(
        parent, created_directories, create_missing=entry.action != "delete"
    )
    name = file.resolved_target.name
    journaled = False
    try:
        backup_name, mode = _backup_before_change_at(entry, directory_fd)
        journal.append((_publication._BoundPublishedFile(file.target, directory_fd, name, backup_name), parent))
        journaled = True
        if entry.action == "write":
            if mode is not None:
                _publication._set_staged_mode(file, mode)
            _publication._replace_source(file, name, directory_fd)
        else:
            _publication._unlink(name, dir_fd=directory_fd)
        _publication._validate_publication_anchor(file)
        if not _publication._directory_fd_matches_path(directory_fd, parent):
            raise OSError(f"batch output destination changed during publication: {file.target}")
    finally:
        if not journaled:
            os.close(directory_fd)


def _roll_back_batch_at(
    journal: list[tuple[_publication._BoundPublishedFile, Path]],
    created_directories: list[_publication._CreatedDirectoryAt],
) -> tuple[list[Path], list[Path]]:
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    unrestored: list[Path] = []
    backups: list[Path] = []
    for published_file, parent in reversed(journal):
        if failures := _publication._rollback_bound_file(published_file):
            unrestored.extend(failures)
            if published_file.backup_name is not None:
                backups.append(parent / published_file.backup_name)
    for directory in reversed(created_directories):
        try:
            _publication._rmdir(directory.name, dir_fd=directory.parent_fd)
        except OSError:
            unrestored.append(directory.path)
    return unrestored, backups


def _publish_batch_at(entries: Sequence[BatchEntry]) -> None:
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    journal: list[tuple[_publication._BoundPublishedFile, Path]] = []
    created_directories: list[_publication._CreatedDirectoryAt] = []
    try:
        for entry in entries:
            _publish_entry_at(entry, journal, created_directories)
    except BaseException as failure:
        unrestored, backups = _roll_back_batch_at(journal, created_directories)
        if unrestored:
            raise PublicationRollbackError(tuple(unrestored), tuple(backups)) from failure
        raise
    else:
        for published_file, _ in journal:
            if published_file.backup_name is not None:
                with suppress(OSError):
                    _publication._unlink(published_file.backup_name, dir_fd=published_file.directory_fd)
    finally:
        for published_file, _ in journal:
            os.close(published_file.directory_fd)
        for directory in created_directories:
            os.close(directory.parent_fd)


def _publish_entry_by_path(
    entry: BatchEntry, journal: list[_publication._PublishedFile], created_directories: list[Path]
) -> None:  # pragma: no cover - Windows fallback
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    file = entry.file
    _publication._validate_publication_anchor(file)
    _publication._validate_planned_target(file)
    if entry.action != "delete":
        _publication._create_target_parent(file.target, created_directories)
    match entry.action:
        case "write":
            if file.target.is_dir():
                raise IsADirectoryError(f"[Errno 21] Is a directory: '{file.target}'")
            exists = file.target.exists() or file.target.is_symlink()
            backup = _publication._backup_existing_target(file.target) if exists else None
            journal.append(_publication._PublishedFile(file.target, backup))
            if backup is not None and file.staged_file is not None:
                _publication._preserve_target_mode(file.staged_file, file.target)
            _publication._replace_source(file, file.target, None)
        case _:
            journal.append(_publication._PublishedFile(file.target, _publication._backup_existing_target(file.target)))
            _publication._unlink(file.target)
    _publication._validate_planned_target(file)
    _publication._validate_publication_anchor(file)


def _publish_batch_by_path(entries: Sequence[BatchEntry]) -> None:  # pragma: no cover - Windows fallback
    """Publish a batch through the checked Windows backup and rollback journal."""
    from datamodel_code_generator import _publication  # ruff: ignore[import-outside-top-level]

    journal: list[_publication._PublishedFile] = []
    created_directories: list[Path] = []
    try:
        for entry in entries:
            _publish_entry_by_path(entry, journal, created_directories)
    except BaseException as failure:
        unrestored: list[Path] = []
        for published_file in reversed(journal):
            unrestored.extend(_publication._rollback_published_file(published_file))
        for directory in reversed(created_directories):
            unrestored.extend(_publication._remove_created_directory(directory))
        if unrestored:
            backups = tuple(
                published_file.backup
                for published_file in journal
                if published_file.backup is not None and published_file.backup in unrestored
            )
            raise PublicationRollbackError(tuple(unrestored), backups) from failure
        raise
    for published_file in journal:
        if published_file.backup is not None:
            with suppress(OSError):
                _publication._unlink(published_file.backup)


def publish_batch(entries: Sequence[BatchEntry]) -> None:
    """Apply writes and deletions of distinct destinations in order, undoing all on failure."""
    if os.name == "nt":  # pragma: no cover - Windows keeps a checked lexical fallback
        _publish_batch_by_path(entries)
        return
    _publish_batch_at(entries)


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
        from datamodel_code_generator._publication import close_anchor, publication_anchor  # ruff: ignore[import-outside-top-level]

        if (anchor := self.anchors.get(base)) is None:
            anchor = self.anchors[base] = publication_anchor(base)
            self.stack.callback(close_anchor, anchor)
        return anchor

    def staging(self, base: Path) -> StagingDirectory:
        from datamodel_code_generator._publication import StagingDirectory  # ruff: ignore[import-outside-top-level]

        if (staging := self.stagings.get(base)) is None:
            staging = self.stagings[base] = StagingDirectory.create(self.anchor(base), prefix=".datamodel-codegen-")
            self.stack.callback(_quietly, staging.cleanup)
        return staging

    def entry(self, artifact: GeneratedArtifact, target: Path, lock: RemoteReferenceLock | None) -> BatchEntry:
        from datamodel_code_generator._publication import StagedFile  # ruff: ignore[import-outside-top-level]

        resolved = target.parent.resolve(strict=False) / target.name
        file = StagedFile(None, target, resolved, self.anchor(base := _base(resolved.parent)))
        match artifact.action, artifact.content:
            case "write", _ if lock is not None and isinstance(staged := lock.stage(self.staging(base)), StagedFile):
                return BatchEntry("write", staged._replace(anchor=file.anchor))
            case "write", bytes() as content:
                return BatchEntry("write", stage_content(self.staging(base), content, file))
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
    lock: RemoteReferenceLock | None,
) -> GenerationReport:
    """Stage every change, recheck all planned files, then publish one reversible journal."""
    with ExitStack() as stack:
        batch = _Batch(stack)
        try:
            entries = [
                batch.entry(artifact, cwd / artifact.path, lock if artifact.kind == "remote_lock" else None)
                for artifact in project.artifacts
                if artifact.action != "unchanged"
            ]
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
        dependencies=project.dependencies,
    )
