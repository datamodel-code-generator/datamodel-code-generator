"""Integrity locks for remote schema resources.

The lock deliberately records safe display origins and SHA-256 digests only.
``request_sha256`` identifies the normalized URL origin, path, request-header
names, and query-parameter names. Credential and query values never contribute
to a persisted digest. Consequently, distinct responses for the same path and
query-name identity cannot share a lock entry: one generation fails closed if
it observes different bodies. Local mirror paths and response bodies never
leave the generation process either.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard, cast
from urllib.parse import SplitResult, parse_qsl, urlsplit, urlunsplit

from datamodel_code_generator._url_redaction import redact_url

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from datamodel_code_generator._publication import StagedFile, StagingDirectory


_LOCK_VERSION = 1
_SHA256_PREFIX = "sha256:"


class RemoteLockError(Exception):
    """Raised when a remote resource does not match its integrity lock."""


@dataclass(frozen=True, slots=True)
class RemoteLockEntry:
    """One safe-to-persist remote resource identity and its raw body digest."""

    request_sha256: str
    url: str
    body_sha256: str


def _sha256(value: bytes) -> str:
    return f"{_SHA256_PREFIX}{hashlib.sha256(value).hexdigest()}"


def _split_remote_url(url: str, *, persisted: bool = False) -> tuple[SplitResult, int | None]:
    """Parse and validate the HTTP(S) origin fields used by a lock entry."""
    message = "Invalid remote lock resource URL" if persisted else f"Invalid remote lock URL: {redact_url(url)!r}"
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RemoteLockError(message) from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteLockError(message)
    if port is None:
        return parsed, None
    match scheme, port:
        case ("http", 80) | ("https", 443):
            return parsed, None
        case _:
            return parsed, port


def _display_url(url: str) -> str:
    """Return a safe origin suitable for a committed lock.

    URL paths are deliberately omitted as deployments frequently put access
    tokens in a path segment. The request digest remains the identity; this
    field is only safe diagnostic context.
    """
    parsed, port = _split_remote_url(url)
    hostname = parsed.hostname
    assert hostname is not None
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _request_sha256(
    url: str,
    headers: Sequence[tuple[str, str]] | None,
    query_parameters: Sequence[tuple[str, str]] | None,
) -> str:
    """Hash a request identity whose stable structure excludes every value."""
    parsed, port = _split_remote_url(url)
    canonical = {
        "headers": sorted(name.lower() for name, _ in headers or ()),
        "query_parameters": [name for name, _ in query_parameters or ()],
        "url": {
            "hostname": parsed.hostname,
            "path": parsed.path,
            "port": port,
            "scheme": parsed.scheme.lower(),
        },
        "url_query_parameters": [name for name, _ in parse_qsl(parsed.query, keep_blank_values=True)],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return _sha256(encoded)


def _is_sha256(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not value.startswith(_SHA256_PREFIX) or len(value) != len(_SHA256_PREFIX) + 64:
        return False
    try:
        bytes.fromhex(value.removeprefix(_SHA256_PREFIX))
    except ValueError:
        return False
    return True


def _validate_persisted_display_url(url: str) -> None:
    """Reject malformed display URLs before lock verification can proceed."""
    parsed, _ = _split_remote_url(url, persisted=True)
    has_sensitive_url_parts = any((parsed.path, parsed.query, parsed.fragment, parsed.username, parsed.password))
    if parsed.scheme not in {"http", "https"} or has_sensitive_url_parts:
        msg = "Invalid remote lock resource URL"
        raise RemoteLockError(msg)


def _entry_from_data(value: object) -> RemoteLockEntry:
    if not isinstance(value, dict):
        msg = "Invalid remote lock: each resource must be an object"
        raise RemoteLockError(msg)
    resource = cast("dict[str, object]", value)
    request_sha256 = resource.get("request_sha256")
    url = resource.get("url")
    body_sha256 = resource.get("body_sha256")
    if not _is_sha256(request_sha256) or not _is_sha256(body_sha256) or not isinstance(url, str):
        msg = "Invalid remote lock resource"
        raise RemoteLockError(msg)
    _validate_persisted_display_url(url)
    return RemoteLockEntry(request_sha256=request_sha256, url=url, body_sha256=body_sha256)


def _read_entries(path: Path) -> dict[str, RemoteLockEntry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"Unable to read remote lock {path}: {exc}"
        raise RemoteLockError(msg) from exc
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(data, dict) or type(version) is not int or version != _LOCK_VERSION:
        msg = f"Invalid remote lock {path}: expected version {_LOCK_VERSION}"
        raise RemoteLockError(msg)
    resources = data.get("resources")
    if not isinstance(resources, list):
        msg = f"Invalid remote lock {path}: resources must be a list"
        raise RemoteLockError(msg)
    entries: dict[str, RemoteLockEntry] = {}
    for resource in resources:
        entry = _entry_from_data(resource)
        if entry.request_sha256 in entries:
            msg = f"Invalid remote lock {path}: duplicate request identity"
            raise RemoteLockError(msg)
        entries[entry.request_sha256] = entry
    return entries


def _remove_temporary_file(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _nearest_existing_directory(path: Path) -> Path:
    """Return an existing ancestor without creating target lock directories."""
    while not path.is_dir():
        parent = path.parent
        if parent == path:
            msg = f"Unable to find an existing directory for remote lock: {path}"
            raise RemoteLockError(msg)
        path = parent
    return path


@dataclass(slots=True)
class RemoteReferenceLock:
    """Collect and verify all remote bytes for exactly one generation.

    ``commit()`` is intentionally explicit so a future batch executor can
    share a collector across targets and write only after every target succeeds.
    """

    path: Path
    update: bool
    _entries: dict[str, RemoteLockEntry]
    _seen: dict[str, RemoteLockEntry] = field(default_factory=dict)
    _committed: bool = False
    _staged_path: Path | None = None
    _staged_source: StagedFile | None = None
    _staging_directory: StagingDirectory | None = None

    @classmethod
    def open(cls, path: Path, *, update: bool, locked: bool) -> RemoteReferenceLock:
        """Open an existing lock or prepare a new one for an explicit update."""
        if path.is_file():
            return cls(path=path, update=update, _entries=_read_entries(path))
        if locked:
            msg = f"Remote lock file not found: {path}"
            raise RemoteLockError(msg)
        return cls(path=path, update=update, _entries={})

    def record_response(
        self,
        url: str,
        headers: Sequence[tuple[str, str]] | None,
        query_parameters: Sequence[tuple[str, str]] | None,
        body: bytes,
    ) -> None:
        """Validate or stage the raw bytes returned for one HTTP(S) request."""
        request_sha256 = _request_sha256(url, headers, query_parameters)
        entry = RemoteLockEntry(
            request_sha256=request_sha256,
            url=_display_url(url),
            body_sha256=_sha256(body),
        )
        if (seen_entry := self._seen.get(request_sha256)) is not None:
            if seen_entry.body_sha256 != entry.body_sha256:
                msg = f"Remote resource returned different content in one generation: {entry.url}"
                raise RemoteLockError(msg)
            return
        if (locked_entry := self._entries.get(request_sha256)) is None:
            if self.update:
                self._entries[request_sha256] = entry
                self._seen[request_sha256] = entry
                return
            msg = f"Remote resource is not recorded in lock: {entry.url}"
            raise RemoteLockError(msg)
        if locked_entry.body_sha256 == entry.body_sha256:
            self._seen[request_sha256] = entry
            return
        if self.update:
            self._entries[request_sha256] = entry
            self._seen[request_sha256] = entry
            return
        msg = f"Remote resource content does not match lock: {entry.url}"
        raise RemoteLockError(msg)

    def _write_staged_content(self, file: TextIO) -> None:
        """Stream one deterministic lock document without retaining a second full payload."""
        entries = sorted(self._seen.values(), key=lambda entry: entry.request_sha256)
        file.write('{\n  "resources": ')
        if not entries:
            file.write("[]")
        else:
            file.write("[\n")
            for index, entry in enumerate(entries):
                if index:
                    file.write(",\n")
                serialized_entry = json.dumps(asdict(entry), ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n".join(f"    {line}" for line in serialized_entry.splitlines()))
            file.write("\n  ]")
        file.write(f',\n  "version": {_LOCK_VERSION}\n}}\n')

    def stage(self, staging_directory: StagingDirectory | Path | None = None) -> StagedFile | Path | None:
        """Write the updated lock into a pre-existing private staging directory.

        The caller owns publication so batch output, metadata, and every lock
        can share one rollback journal.  A repeated call intentionally reuses
        the one staged file and does not serialize the lock twice.
        """
        if self._committed or not self.update:
            return None
        if self._staged_source is not None:
            return self._staged_source
        if self._staged_path is not None:
            return self._staged_path
        try:
            if staging_directory is not None and not isinstance(staging_directory, Path):
                from datamodel_code_generator._publication import StagedFile  # noqa: PLC0415

                file_fd, name = staging_directory.create_file(prefix=f".{self.path.name}.")
                try:
                    with os.fdopen(file_fd, "w", encoding="utf-8") as temporary_file:
                        self._write_staged_content(temporary_file)
                        temporary_file.flush()
                        os.fsync(temporary_file.fileno())
                except BaseException:
                    with contextlib.suppress(OSError):
                        staging_directory.discard_file(name)
                    raise
                staged_path = staging_directory.path / name if staging_directory.directory_fd is None else None
                self._staged_source = StagedFile(
                    staged_path,
                    self.path,
                    self.path,
                    source_directory_fd=staging_directory.directory_fd,
                    source_name=name if staging_directory.directory_fd is not None else None,
                )
                self._staging_directory = staging_directory
                return self._staged_source
            with contextlib.ExitStack() as cleanup:
                temporary_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
                    mode="w",
                    encoding="utf-8",
                    dir=staging_directory or _nearest_existing_directory(self.path.parent),
                    prefix=f".{self.path.name}.",
                    delete=False,
                )
                temporary_path = Path(temporary_file.name)
                # Register cleanup immediately after allocation: write(), flush(),
                # fsync(), and close() can all fail before replacement.
                cleanup.callback(_remove_temporary_file, temporary_path)
                with temporary_file:
                    self._write_staged_content(cast("TextIO", temporary_file.file))
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                self._staged_path = temporary_path
                cleanup.pop_all()
                return temporary_path
        except OSError as exc:
            msg = f"Unable to update remote lock {self.path}: {exc}"
            raise RemoteLockError(msg) from exc

    def discard_stage(self) -> None:
        """Remove a staged but unpublished update after a failed transaction."""
        if self._staged_source is not None and self._staging_directory is not None:
            source_name = self._staged_source.source_name or cast("Path", self._staged_source.staged_file).name
            self._staging_directory.discard_file(source_name)
            self._staged_source = None
            self._staging_directory = None
        if self._staged_path is not None:
            _remove_temporary_file(self._staged_path)
            self._staged_path = None

    def mark_committed(self) -> None:
        """Mark the collector committed only after its transaction publishes."""
        self._committed = True
        self._staged_path = None
        self._staged_source = None
        self._staging_directory = None

    def commit(self) -> None:
        """Atomically persist a successful explicit update at most once."""
        if self._committed or not self.update:
            return
        if self._staged_path is not None:
            self.discard_stage()
            msg = "Cannot commit remote lock after legacy Path staging; call discard_stage() before committing."
            raise RemoteLockError(msg)
        from datamodel_code_generator._publication import (  # noqa: PLC0415
            StagingDirectory,
            close_anchor,
            publication_anchor,
            publish_staged_files,
        )

        anchor = None
        staging_directory = None
        try:
            target = self.path if self.path.is_absolute() else Path.cwd() / self.path
            target = target.expanduser().parent.resolve(strict=False) / target.name
            anchor = publication_anchor(target.parent)
            staging_directory = StagingDirectory.create(anchor, prefix=".datamodel-codegen-lock-")
            staged_source = cast("StagedFile", self.stage(staging_directory))
            publish_staged_files((staged_source._replace(target=target, resolved_target=target, anchor=anchor),))
        except OSError as exc:
            with contextlib.suppress(OSError):
                self.discard_stage()
            msg = f"Unable to update remote lock {self.path}: {exc}"
            raise RemoteLockError(msg) from exc
        else:
            self.mark_committed()
        finally:
            if staging_directory is not None:
                with contextlib.suppress(OSError):
                    staging_directory.cleanup()
            with contextlib.suppress(OSError):
                close_anchor(anchor)
