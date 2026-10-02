"""Descriptor-bound staging and atomic publication for generated artifacts.

This module deliberately has no generator or command-line dependencies.  Both
the public API and CLI use the same journal so remote locks cannot follow a
replaced staging parent or publish independently from generated artifacts.
"""

# ruff: noqa: EM101, EM102, ERA001, PERF203, PERF401, TRY003, TRY301

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, NamedTuple, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


# Keep filesystem failure seams local to this module.  In particular, pathlib
# binds its Windows accessors at import time, so patching os does not exercise
# path-based fallback operations consistently.
_unlink = os.unlink
_replace = os.replace
_rmdir = os.rmdir


class PublicationAnchor(NamedTuple):
    """The existing directory inode that anchored a planned publication path."""

    path: Path
    identity: tuple[int, int]
    directory_fd: int | None


class StagedFile(NamedTuple):
    """One staged source and its immutable concrete publication destination."""

    staged_file: Path | None
    target: Path
    resolved_target: Path
    anchor: PublicationAnchor | None = None
    source_directory_fd: int | None = None
    source_name: str | None = None


class _PublishedFile(NamedTuple):
    target: Path
    backup: Path | None


class _BoundPublishedFile(NamedTuple):
    target: Path
    directory_fd: int
    name: str
    backup_name: str | None


class _CreatedDirectoryAt(NamedTuple):
    parent_fd: int
    name: str
    path: Path


class _StagingFallback(NamedTuple):
    """Windows identity checks for a lexical private staging location."""

    path_identity: tuple[int, int]
    anchor_identity: tuple[int, int]


def _validate_anchor_path(anchor: PublicationAnchor) -> None:
    """Reject a lexical fallback whose planned parent was replaced after preflight."""
    try:
        path_stat = anchor.path.stat()
    except OSError as exc:
        raise OSError(f"publication destination changed: {anchor.path}") from exc
    if (path_stat.st_dev, path_stat.st_ino) != anchor.identity:
        raise OSError(f"publication destination changed: {anchor.path}")


def _private_name(prefix: str) -> str:
    """Return one unpredictable private directory or file component."""
    return f"{prefix}{token_hex(16)}"


def _directory_open_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _open_target_directory(
    path: Path, created_directories: list[_CreatedDirectoryAt], *, create_missing: bool = True
) -> int:
    """Open a concrete directory without following components, creating missing ones safely."""
    if not path.is_absolute():  # pragma: no cover - all callers pre-resolve destinations
        raise OSError(f"publication destination is not absolute: {path}")
    flags = _directory_open_flags()
    directory_fd = os.open(path.anchor, flags)
    current_path = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current_path /= part
            try:
                next_fd = os.open(part, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create_missing:
                    raise
                try:
                    os.mkdir(part, dir_fd=directory_fd)
                except FileExistsError:  # pragma: no cover - concurrent creation is re-opened and checked
                    pass
                else:
                    created_directories.append(_CreatedDirectoryAt(os.dup(directory_fd), part, current_path))
                next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    except BaseException:
        os.close(directory_fd)
        raise
    return directory_fd


def publication_anchor(path: Path) -> PublicationAnchor:
    """Snapshot and hold the deepest existing concrete directory for *path*."""
    while not path.is_dir():
        path = path.parent
    if os.name == "nt":  # pragma: no cover - Windows uses checked path fallback
        path_stat = path.stat()
        return PublicationAnchor(path, (path_stat.st_dev, path_stat.st_ino), None)
    directory_fd = _open_target_directory(path, [], create_missing=False)
    path_stat = os.fstat(directory_fd)
    return PublicationAnchor(path, (path_stat.st_dev, path_stat.st_ino), directory_fd)


def close_anchor(anchor: PublicationAnchor | None) -> None:
    """Release an anchor after every source using it has been published or discarded."""
    if anchor is not None and anchor.directory_fd is not None:
        os.close(anchor.directory_fd)


class StagingDirectory:
    """A private staging directory held by descriptors rather than a re-resolved path."""

    def __init__(
        self,
        directory_fds: tuple[int | None, int | None],
        name: str,
        path: Path,
        *,
        fallback: _StagingFallback | None = None,
    ) -> None:
        self._parent_fd, self.directory_fd = directory_fds
        self.name = name
        self.path = path
        self._fallback = fallback
        self._files: set[str] = set()
        self._closed = False

    @classmethod
    def create(cls, anchor: PublicationAnchor, *, prefix: str) -> StagingDirectory:
        """Reserve and open a private child of an already-pinned publication anchor."""
        if anchor.directory_fd is None:  # pragma: no cover - Windows uses the checked fallback path
            _validate_anchor_path(anchor)
            path = type(anchor.path)(tempfile.mkdtemp(prefix=prefix, dir=anchor.path))
            try:
                path_stat = path.stat()
                _validate_anchor_path(anchor)
                return cls(
                    (None, None),
                    path.name,
                    path,
                    fallback=_StagingFallback(
                        (path_stat.st_dev, path_stat.st_ino),
                        anchor.identity,
                    ),
                )
            except BaseException:
                with suppress(OSError):
                    _rmdir(path)
                raise
        parent_fd: int | None = os.dup(anchor.directory_fd)
        try:
            for _ in range(100):
                name = _private_name(prefix)
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    continue
                try:
                    # Ownership transfers to StagingDirectory after it is fully constructed; cleanup() closes it.
                    directory_fd = os.open(  # lgtm [py/file-not-closed]
                        name, _directory_open_flags(), dir_fd=parent_fd
                    )
                except BaseException:
                    with suppress(OSError):
                        _rmdir(name, dir_fd=parent_fd)
                    raise
                try:
                    staging_directory = cls((parent_fd, directory_fd), name, anchor.path / name)
                except BaseException:
                    os.close(directory_fd)
                    with suppress(OSError):
                        _rmdir(name, dir_fd=parent_fd)
                    raise
                # From here cleanup() owns both descriptors, including its duplicated parent descriptor.
                parent_fd = None
                return staging_directory
            raise FileExistsError(f"could not reserve private staging under {anchor.path}")
        finally:
            if parent_fd is not None:
                os.close(parent_fd)

    def _validate_path_fallback(self) -> None:  # pragma: no cover - Windows lexical fallback
        """Fail closed if a Windows lexical staging location no longer names the reserved directory."""
        fallback = cast("_StagingFallback", self._fallback)
        try:
            path_stat = self.path.stat()
            anchor_stat = self.path.parent.stat()
        except OSError as exc:
            raise OSError(f"private staging directory changed: {self.path}") from exc
        if (path_stat.st_dev, path_stat.st_ino) != fallback.path_identity or (
            anchor_stat.st_dev,
            anchor_stat.st_ino,
        ) != fallback.anchor_identity:
            raise OSError(f"private staging directory changed: {self.path}")

    def create_file(self, *, prefix: str) -> tuple[int, str]:
        """Create a no-follow, exclusive staged source through the held directory fd."""
        if self._closed:
            raise OSError("private staging directory is already closed")
        if self.directory_fd is None:  # pragma: no cover - Windows lexical fallback
            self._validate_path_fallback()
            file_fd, file_path = tempfile.mkstemp(prefix=prefix, dir=self.path)
            name = type(self.path)(file_path).name
            self._files.add(name)
            return file_fd, name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        for _ in range(100):
            name = _private_name(prefix)
            try:
                file_fd = os.open(name, flags, 0o600, dir_fd=self.directory_fd)
            except FileExistsError:
                continue
            self._files.add(name)
            return file_fd, name
        raise FileExistsError(f"could not reserve private staged file under {self.path}")

    def discard_file(self, name: str) -> None:
        """Discard one staged source through the held directory fd."""
        if self.directory_fd is None:  # pragma: no cover - Windows lexical fallback
            self._validate_path_fallback()
            try:
                _unlink(self.path / name)
            except FileNotFoundError:
                self._files.discard(name)
            else:
                self._files.discard(name)
            return
        try:
            _unlink(name, dir_fd=self.directory_fd)
        except FileNotFoundError:
            self._files.discard(name)
        else:
            self._files.discard(name)

    def cleanup(self) -> None:
        """Best-effort clean staged sources and remove only our pinned directory entry."""
        if self._closed:
            return
        cleanup_error: OSError | None = None
        try:
            if self.directory_fd is None:  # pragma: no cover - Windows lexical fallback
                self._validate_path_fallback()
            cleanup_error = self._discard_files()
            directory_error = self._remove_directory()
            cleanup_error = cleanup_error or directory_error
        finally:
            self._closed = True
            if self.directory_fd is not None:
                os.close(self.directory_fd)
            if self._parent_fd is not None:
                os.close(self._parent_fd)
        if cleanup_error is not None:
            raise cleanup_error

    def _discard_files(self) -> OSError | None:
        """Remove every tracked source while preserving the first cleanup failure."""
        cleanup_error: OSError | None = None
        for name in tuple(self._files):
            try:
                if self.directory_fd is None:  # pragma: no cover - Windows lexical fallback
                    _unlink(self.path / name)
                else:
                    _unlink(name, dir_fd=self.directory_fd)
            except FileNotFoundError:
                self._files.discard(name)
            except OSError as exc:
                cleanup_error = cleanup_error or exc
            else:
                self._files.discard(name)
        return cleanup_error

    def _remove_directory(self) -> OSError | None:
        """Remove the owned empty staging directory through its pinned parent when available."""
        try:
            if self.directory_fd is None:  # pragma: no cover - Windows lexical fallback
                _rmdir(self.path)
            else:
                _rmdir(self.name, dir_fd=cast("int", self._parent_fd))
        except FileNotFoundError:
            return None
        except OSError as exc:
            return exc
        return None


def _backup_name(target_name: str) -> str:
    return f".{target_name}.{token_hex(8)}.bak"


def _backup_names(target_name: str) -> Iterator[str]:
    for _ in range(100):  # pragma: no branch - cryptographic collision is implausible
        yield _backup_name(target_name)


def _open_file_at(path: str | Path, flags: int, mode: int, directory_fd: int | None) -> int:
    return os.open(path, flags, mode) if directory_fd is None else os.open(path, flags, mode, dir_fd=directory_fd)


def _chmod_backup_at(backup: str | Path, backup_fd: int, mode: int, directory_fd: int | None) -> None:
    """Apply destination mode bits through the safest handle supported by the platform."""
    if (fchmod := getattr(os, "fchmod", None)) is not None:
        fchmod(backup_fd, mode)
    elif os.chmod in os.supports_fd:  # pragma: no cover - platform-specific fallback
        os.chmod(backup_fd, mode)
    elif directory_fd is not None:  # pragma: no cover - POSIX exposes fchmod
        os.chmod(backup, mode, dir_fd=directory_fd, follow_symlinks=False)
    elif os.chmod in os.supports_follow_symlinks:  # pragma: no cover - platform-specific fallback
        Path(backup).chmod(mode, follow_symlinks=False)
    else:  # pragma: no cover - Windows lacks fd/no-follow chmod
        Path(backup).chmod(mode)


def _set_backup_times_at(
    backup: str | Path,
    backup_fd: int,
    target_stat: os.stat_result,
    directory_fd: int | None,
) -> None:
    """Preserve destination timestamps through a descriptor or no-follow operation."""
    timestamps = (target_stat.st_atime_ns, target_stat.st_mtime_ns)
    if os.utime in os.supports_fd:
        os.utime(backup_fd, ns=timestamps)
    elif directory_fd is not None:  # pragma: no cover - POSIX exposes fd-based utime
        os.utime(backup, ns=timestamps, dir_fd=directory_fd, follow_symlinks=False)
    elif os.utime in os.supports_follow_symlinks:  # pragma: no cover - platform-specific fallback
        os.utime(backup, ns=timestamps, follow_symlinks=False)
    else:  # pragma: no cover - legacy Windows fallback
        os.utime(backup, ns=timestamps)


def _copy_target(
    source: str | Path, backup: str | Path, target_stat: os.stat_result, *, directory_fd: int | None
) -> None:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    binary = getattr(os, "O_BINARY", 0)
    source_fd = _open_file_at(source, os.O_RDONLY | no_follow | binary, 0, directory_fd)
    backup_fd: int | None = None
    try:
        backup_fd = _open_file_at(
            backup,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow | binary,
            stat.S_IMODE(target_stat.st_mode),
            directory_fd,
        )
        with os.fdopen(os.dup(source_fd), "rb") as source_file, os.fdopen(os.dup(backup_fd), "wb") as backup_file:
            shutil.copyfileobj(source_file, backup_file)
        _chmod_backup_at(backup, backup_fd, stat.S_IMODE(target_stat.st_mode), directory_fd)
        _set_backup_times_at(backup, backup_fd, target_stat, directory_fd)
    except BaseException:
        if backup_fd is not None:
            with suppress(OSError):
                os.close(backup_fd)
            backup_fd = None
            with suppress(OSError):
                if directory_fd is None:
                    _unlink(backup)
                else:
                    _unlink(backup, dir_fd=directory_fd)
        raise
    finally:
        os.close(source_fd)
        if backup_fd is not None:
            os.close(backup_fd)


def _backup_existing_target(target: Path) -> Path:
    target_stat = target.lstat()
    for backup_name in _backup_names(target.name):
        backup = target.parent / backup_name
        try:
            if stat.S_ISLNK(target_stat.st_mode):  # pragma: no cover - Windows fallback
                backup.symlink_to(target.readlink(), target_is_directory=target.is_dir())
            else:
                try:
                    os.link(target, backup, follow_symlinks=False)
                except FileExistsError:
                    continue
                except OSError:
                    _copy_target(target, backup, target_stat, directory_fd=None)
        except FileExistsError:
            continue
        else:
            return backup
    raise FileExistsError(f"could not reserve backup for {target}")


def _planned_staged_file(file: tuple[Path, Path] | StagedFile) -> StagedFile:
    if isinstance(file, StagedFile):
        return file
    staged_file, target = file
    return StagedFile(staged_file, target, target.expanduser().parent.resolve(strict=False) / target.name)


def _create_directory(directory: Path) -> bool:
    """Create one directory and report whether this transaction created it."""
    try:
        directory.mkdir()
    except FileExistsError:
        if not directory.is_dir():
            raise
        return False
    return True


def _create_target_parent(target: Path, created_directories: list[Path]) -> None:
    """Create a target parent while recording only transaction-owned directories."""
    missing_directories: list[Path] = []
    parent = target.parent
    while not parent.exists():
        missing_directories.append(parent)
        parent = parent.parent
    for directory in reversed(missing_directories):
        if _create_directory(directory):
            created_directories.append(directory)


def _preserve_target_mode(staged_file: Path, target: Path) -> None:
    with suppress(OSError):
        staged_file.chmod(stat.S_IMODE(target.stat().st_mode))


def _restore_backup(backup: Path, target: Path) -> None:
    if backup.is_symlink() and target.is_symlink() and backup.readlink() == target.readlink():
        _unlink(backup)
        return
    try:
        if backup.samefile(target):
            _unlink(backup)
            return
    except OSError:
        # The target may have disappeared, but replacement still restores the backup safely.
        _replace(backup, target)
        return
    _replace(backup, target)


def _rollback_published_file(published_file: _PublishedFile) -> list[Path]:
    try:
        if published_file.backup is not None:
            if not (published_file.backup.exists() or published_file.backup.is_symlink()):
                return [published_file.target, published_file.backup]
            _restore_backup(published_file.backup, published_file.target)
        elif published_file.target.exists():
            _unlink(published_file.target)
    except OSError:
        paths = [published_file.target]
        if published_file.backup is not None:
            paths.append(published_file.backup)
        return paths
    return []


def _remove_created_directory(directory: Path) -> list[Path]:
    try:
        _rmdir(directory)
    except OSError:
        return [directory]
    return []


def _validate_planned_target(file: StagedFile) -> None:  # pragma: no cover - Windows fallback
    concrete_target = file.target.expanduser().parent.resolve(strict=False) / file.target.name
    if concrete_target != file.resolved_target:
        raise OSError(f"batch output target changed before publication: {file.target}")


def _directory_fd_matches_path(directory_fd: int, path: Path) -> bool:
    try:
        check_fd = _open_target_directory(path, [], create_missing=False)
    except OSError:
        return False
    try:
        return os.path.samestat(os.fstat(directory_fd), os.fstat(check_fd))
    finally:
        os.close(check_fd)


def _validate_publication_anchor(file: StagedFile) -> None:
    if file.anchor is None:
        return
    if file.anchor.directory_fd is None:  # pragma: no cover - Windows fallback
        try:
            path_stat = file.anchor.path.stat()
        except OSError:
            matches = False
        else:
            matches = (path_stat.st_dev, path_stat.st_ino) == file.anchor.identity
    else:
        anchor_stat = os.fstat(file.anchor.directory_fd)
        matches = (anchor_stat.st_dev, anchor_stat.st_ino) == file.anchor.identity and _directory_fd_matches_path(
            file.anchor.directory_fd, file.anchor.path
        )
    if not matches:
        raise OSError(f"batch output destination anchor changed before publication: {file.target}")


def _backup_existing_target_at(directory_fd: int, target_name: str, target_stat: os.stat_result) -> str:
    for backup_name in _backup_names(target_name):
        try:
            if stat.S_ISLNK(target_stat.st_mode):
                os.symlink(os.readlink(target_name, dir_fd=directory_fd), backup_name, dir_fd=directory_fd)
            else:
                try:
                    os.link(
                        target_name,
                        backup_name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                except OSError:
                    _copy_target(target_name, backup_name, target_stat, directory_fd=directory_fd)
        except FileExistsError:
            continue
        else:
            return backup_name
    raise FileExistsError(f"could not reserve backup for {target_name}")


def _restore_backup_at(published_file: _BoundPublishedFile) -> None:
    backup_name = cast("str", published_file.backup_name)
    backup_stat = os.stat(backup_name, dir_fd=published_file.directory_fd, follow_symlinks=False)
    try:
        target_stat = os.stat(published_file.name, dir_fd=published_file.directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        target_stat = None
    if target_stat is not None and os.path.samestat(backup_stat, target_stat):
        _unlink(backup_name, dir_fd=published_file.directory_fd)
        return
    if (
        stat.S_ISLNK(backup_stat.st_mode)
        and target_stat is not None
        and stat.S_ISLNK(target_stat.st_mode)
        and os.readlink(backup_name, dir_fd=published_file.directory_fd)
        == os.readlink(published_file.name, dir_fd=published_file.directory_fd)
    ):
        _unlink(backup_name, dir_fd=published_file.directory_fd)
        return
    _replace(
        backup_name, published_file.name, src_dir_fd=published_file.directory_fd, dst_dir_fd=published_file.directory_fd
    )


def _rollback_bound_file(published_file: _BoundPublishedFile) -> list[Path]:
    try:
        if published_file.backup_name is not None:
            _restore_backup_at(published_file)
        else:
            with suppress(FileNotFoundError):
                _unlink(published_file.name, dir_fd=published_file.directory_fd)
    except OSError:
        return [published_file.target]
    return []


def _set_staged_mode(file: StagedFile, mode: int) -> None:
    try:
        if file.source_directory_fd is not None:
            # Existing destination permissions are intentionally preserved, including group readability.
            source_name = cast("str", file.source_name)

            os.chmod(
                source_name,
                mode,
                dir_fd=file.source_directory_fd,
                follow_symlinks=False,
                # codeql[py/overly-permissive-file]
            )
        else:
            cast("Path", file.staged_file).chmod(mode)
    except OSError:
        # Preserving an existing destination's mode is best effort; publication must retain its staged content.
        return


def _replace_source(file: StagedFile, destination_name: str | Path, destination_fd: int | None) -> None:
    if (source_directory_fd := file.source_directory_fd) is not None:
        if destination_fd is None:
            raise OSError(f"descriptor-only staging requires a destination descriptor: {file.target}")
        _replace(
            cast("str", file.source_name),
            cast("str", destination_name),
            src_dir_fd=source_directory_fd,
            dst_dir_fd=destination_fd,
        )
        return
    if destination_fd is None:
        _replace(cast("Path", file.staged_file), destination_name)
        return
    _replace(cast("Path", file.staged_file), destination_name, dst_dir_fd=destination_fd)


def _publish_staged_files_at(files: Sequence[StagedFile]) -> None:  # noqa: PLR0912
    journal: list[_BoundPublishedFile] = []
    created_directories: list[_CreatedDirectoryAt] = []
    try:
        for file in files:
            _validate_publication_anchor(file)
            directory_fd = _open_target_directory(file.resolved_target.parent, created_directories)
            journaled = False
            try:
                try:
                    target_stat = os.stat(file.resolved_target.name, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    target_stat = None
                if target_stat is not None and stat.S_ISDIR(target_stat.st_mode):
                    raise IsADirectoryError(f"[Errno 21] Is a directory: '{file.target}'")
                backup_name = (
                    _backup_existing_target_at(directory_fd, file.resolved_target.name, target_stat)
                    if target_stat is not None
                    else None
                )
                journal.append(_BoundPublishedFile(file.target, directory_fd, file.resolved_target.name, backup_name))
                journaled = True
                if target_stat is not None and stat.S_ISREG(target_stat.st_mode):
                    _set_staged_mode(file, stat.S_IMODE(target_stat.st_mode))
                _replace_source(file, file.resolved_target.name, directory_fd)
                _validate_publication_anchor(file)
                if not _directory_fd_matches_path(directory_fd, file.resolved_target.parent):
                    raise OSError(f"batch output destination changed during publication: {file.target}")
            finally:
                if not journaled:
                    os.close(directory_fd)
    except OSError as publish_error:
        failures: list[Path] = []
        for published_file in reversed(journal):
            failures.extend(_rollback_bound_file(published_file))
        for directory in reversed(created_directories):
            try:
                _rmdir(directory.name, dir_fd=directory.parent_fd)
            except OSError:
                failures.append(directory.path)
        if failures:
            paths = ", ".join(path.as_posix() for path in failures)
            raise OSError(f"{publish_error}; failed to roll back batch output: {paths}") from publish_error
        raise
    else:
        for published_file in journal:
            if published_file.backup_name is not None:
                with suppress(OSError):
                    _unlink(published_file.backup_name, dir_fd=published_file.directory_fd)
    finally:
        for published_file in journal:
            os.close(published_file.directory_fd)
        for directory in created_directories:
            os.close(directory.parent_fd)


def _publish_staged_files_by_path(files: Sequence[StagedFile]) -> None:  # pragma: no cover - Windows fallback
    """Publish through the checked Windows backup and rollback journal."""
    journal: list[_PublishedFile] = []
    created_directories: list[Path] = []
    try:
        for file in files:
            _validate_publication_anchor(file)
            _validate_planned_target(file)
            _create_target_parent(file.target, created_directories)
            if file.target.is_dir():
                raise IsADirectoryError(f"[Errno 21] Is a directory: '{file.target}'")
            backup = _backup_existing_target(file.target) if file.target.exists() or file.target.is_symlink() else None
            journal.append(_PublishedFile(file.target, backup))
            if backup is not None and file.staged_file is not None:
                _preserve_target_mode(file.staged_file, file.target)
            _validate_planned_target(file)
            if file.staged_file is None:
                raise OSError(f"descriptor-only staging is unavailable on Windows: {file.target}")
            _replace_source(file, file.target, None)
            _validate_planned_target(file)
            _validate_publication_anchor(file)
    except OSError as publish_error:
        failures: list[Path] = []
        for published_file in reversed(journal):
            failures.extend(_rollback_published_file(published_file))
        for directory in reversed(created_directories):
            failures.extend(_remove_created_directory(directory))
        if failures:
            paths = ", ".join(path.as_posix() for path in failures)
            raise OSError(f"{publish_error}; failed to roll back batch output: {paths}") from publish_error
        raise
    for published_file in journal:
        if published_file.backup is not None:
            with suppress(OSError):
                _unlink(published_file.backup)


def publish_staged_files(files: Iterable[tuple[Path, Path] | StagedFile]) -> None:
    """Publish a validated journal through descriptor-bound destinations on POSIX."""
    planned_files = tuple(_planned_staged_file(file) for file in files)
    seen_targets: set[Path] = set()
    for file in planned_files:
        if file.resolved_target in seen_targets:
            raise OSError(f"duplicate staged publication target: {file.target}")
        seen_targets.add(file.resolved_target)
    if os.name == "nt":  # pragma: no cover - Windows keeps a checked lexical fallback
        _publish_staged_files_by_path(planned_files)
        return
    _publish_staged_files_at(planned_files)
