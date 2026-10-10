"""Unit tests for persisted remote reference integrity locks."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from datamodel_code_generator import _publication as publication_module
from datamodel_code_generator import remote_lock
from datamodel_code_generator.remote_lock import RemoteLockError, RemoteReferenceLock


@pytest.mark.allow_direct_assert
def test_remote_lock_normalizes_request_headers_and_replaces_the_observed_closure(tmp_path: Path) -> None:
    """Header order is irrelevant and updates remove resources not reached this run."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    first_url = "https://schemas.example/first.json"
    second_url = "https://schemas.example/second.json"
    headers = [("Authorization", "Bearer lock-secret"), ("X-Trace", "trace")]

    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response(first_url, headers, [("revision", "one")], b"first")
    updater.record_response(second_url, headers, [("revision", "one")], b"second")
    updater.commit()

    verifier = RemoteReferenceLock.open(lockfile, update=False, locked=True)
    verifier.record_response(first_url, list(reversed(headers)), [("revision", "one")], b"first")
    verifier.commit()

    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response(first_url, headers, [("revision", "one")], b"first")
    updater.commit()
    lock_data = json.loads(lockfile.read_text(encoding="utf-8"))
    assert [resource["url"] for resource in lock_data["resources"]] == ["https://schemas.example"]


@pytest.mark.allow_direct_assert
def test_remote_lock_never_persists_url_or_request_secrets(tmp_path: Path) -> None:
    """The persisted identity excludes user info, query values, and header values."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response(
        "https://alice:password@schemas.example:8443/path-secret/schema.json?access_token=token#fragment",
        [("Authorization", "Bearer lock-secret")],
        [("access_token", "token")],
        b"schema",
    )
    updater.commit()

    content = lockfile.read_text(encoding="utf-8")
    lock_data = json.loads(content)
    assert lock_data["resources"][0]["url"] == "https://schemas.example:8443"
    assert "alice" not in content
    assert "password" not in content
    assert "lock-secret" not in content
    assert "access_token" not in content
    assert "path-secret" not in content


@pytest.mark.allow_direct_assert
def test_remote_lock_request_identity_uses_only_safe_request_structure(tmp_path: Path) -> None:
    """Credential values cannot split a lock identity or become offline hash inputs."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    url = "https://alice:first-password@schemas.example:8443/v1/schema.json?token=first-token&view=full#first"
    headers = [("Authorization", "Bearer first-secret"), ("X-Api-Key", "first-key")]
    query = [("access_token", "first-token"), ("region", "tokyo")]
    identity = remote_lock._request_sha256(url, headers, query)

    assert remote_lock._request_sha256(
        "https://schemas.example:443/v1/schema.json?token=first-token&view=full",
        headers,
        query,
    ) == remote_lock._request_sha256(
        "https://schemas.example/v1/schema.json?token=first-token&view=full",
        headers,
        query,
    )
    assert remote_lock._display_url("http://schemas.example:80/schema.json") == "http://schemas.example"

    assert identity == remote_lock._request_sha256(
        "https://bob:second-password@schemas.example:8443/v1/schema.json?token=first-token&view=full#second",
        headers,
        query,
    )
    assert identity == remote_lock._request_sha256(
        "https://alice:first-password@schemas.example:8443/v1/schema.json?token=second-token&view=compact#first",
        headers,
        query,
    )
    assert identity == remote_lock._request_sha256(
        url,
        [("authorization", "Bearer second-secret"), ("x-api-key", "second-key")],
        query,
    )
    assert identity == remote_lock._request_sha256(
        url,
        headers,
        [("access_token", "second-token"), ("region", "osaka")],
    )
    assert identity != remote_lock._request_sha256(url.replace("/v1/", "/v2/"), headers, query)
    assert identity != remote_lock._request_sha256(url, [("X-Other-Key", "first-key")], query)
    assert identity != remote_lock._request_sha256(
        url.replace("token=first-token&view=full", "view=full&token=first-token"), headers, query
    )
    assert identity != remote_lock._request_sha256(
        url.replace("token=first-token", "session=first-token"), headers, query
    )
    assert identity != remote_lock._request_sha256(url, headers, [("region", "tokyo"), ("access_token", "first-token")])
    assert identity != remote_lock._request_sha256(url, headers, [("other_token", "first-token"), ("region", "tokyo")])

    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response(url, headers, query, b"first")
    updater.record_response(
        "https://bob:second-password@schemas.example:8443/v1/schema.json?token=second-token&view=compact#second",
        [("authorization", "Bearer second-secret"), ("x-api-key", "second-key")],
        [("access_token", "second-token"), ("region", "osaka")],
        b"first",
    )
    with pytest.raises(RemoteLockError, match="different content in one generation"):
        updater.record_response(url, headers, query, b"second")


@pytest.mark.allow_direct_assert
def test_remote_lock_commit_resolves_a_relative_target_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct lock commits preserve relative-path behavior while using descriptor publication."""
    monkeypatch.chdir(tmp_path)
    lock = RemoteReferenceLock.open(Path("relative.lock"), update=True, locked=False)
    lock.record_response("https://schemas.example/schema.json", None, None, b"schema")

    lock.commit()

    assert (tmp_path / "relative.lock").is_file()


@pytest.mark.allow_direct_assert
def test_nearest_existing_directory_rejects_a_nonexistent_path_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real path root cannot make the ancestor walk loop forever when it is unavailable."""
    path_root = Path(tmp_path.anchor)
    real_is_dir = Path.is_dir

    def root_is_unavailable(path: Path) -> bool:
        return path != path_root and real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", root_is_unavailable)

    with pytest.raises(RemoteLockError, match="Unable to find an existing directory"):
        remote_lock._nearest_existing_directory(path_root)


@pytest.mark.allow_direct_assert
def test_windows_staging_fallback_fails_closed_after_its_private_path_is_replaced(
    tmp_path: Path,
) -> None:
    """A Windows path fallback never writes to or cleans a replacement staging directory."""
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_stat = parent.stat()
    anchor = publication_module.PublicationAnchor(parent, (parent_stat.st_dev, parent_stat.st_ino), None)
    staging = publication_module.StagingDirectory.create(anchor, prefix=".stage-")
    moved_staging = tmp_path / "moved-staging"
    staging.path.rename(moved_staging)
    staging.path.mkdir()

    with pytest.raises(OSError, match="private staging directory changed"):
        staging.create_file(prefix=".source-")
    with pytest.raises(OSError, match="private staging directory changed"):
        staging.cleanup()

    assert not list(staging.path.iterdir())
    moved_staging.rmdir()
    staging.path.rmdir()


@pytest.mark.allow_direct_assert
def test_windows_staging_fallback_rejects_a_replaced_anchor_before_creating_a_private_directory(
    tmp_path: Path,
) -> None:
    """A Windows lexical staging fallback never blesses a replacement anchor directory."""
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_stat = parent.stat()
    anchor = publication_module.PublicationAnchor(parent, (parent_stat.st_dev, parent_stat.st_ino), None)
    moved_parent = tmp_path / "moved-parent"
    parent.rename(moved_parent)
    parent.mkdir()
    with pytest.raises(OSError, match="publication destination changed"):
        publication_module.StagingDirectory.create(anchor, prefix=".stage-")

    assert not list(parent.iterdir())


@pytest.mark.allow_direct_assert
def test_windows_staging_fallback_rejects_a_removed_anchor_before_creating_a_private_directory(
    tmp_path: Path,
) -> None:
    """A deleted Windows anchor is rejected instead of being recreated as an attacker path."""
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_stat = parent.stat()
    anchor = publication_module.PublicationAnchor(parent, (parent_stat.st_dev, parent_stat.st_ino), None)
    parent.rename(tmp_path / "moved-parent")
    with pytest.raises(OSError, match="publication destination changed"):
        publication_module.StagingDirectory.create(anchor, prefix=".stage-")


@pytest.mark.allow_direct_assert
def test_staging_directory_handles_deleted_sources_closed_handles_and_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Descriptor staging treats deleted and undeletable private files as transaction cleanup cases."""
    parent = tmp_path / "parent"
    parent.mkdir()
    anchor = publication_module.publication_anchor(parent)
    staging = publication_module.StagingDirectory.create(anchor, prefix=".stage-")
    file_fd, name = staging.create_file(prefix=".source-")
    os.close(file_fd)
    (staging.path / name).unlink()
    staging.discard_file(name)
    staging.cleanup()
    staging.cleanup()
    with pytest.raises(OSError, match="already closed"):
        staging.create_file(prefix=".source-")
    publication_module.close_anchor(anchor)

    class FailingStagingDirectory(publication_module.StagingDirectory):
        def __init__(
            self,
            directory_fds: tuple[int | None, int | None],
            name: str,
            path: Path,
            *,
            fallback: publication_module._StagingFallback | None = None,
        ) -> None:
            super().__init__(directory_fds, name, path, fallback=fallback)
            msg = "staging setup failed"
            raise OSError(msg)

    anchor = publication_module.publication_anchor(parent)
    with pytest.raises(OSError, match="staging setup failed"):
        FailingStagingDirectory.create(anchor, prefix=".stage-")
    assert not list(parent.iterdir())
    publication_module.close_anchor(anchor)

    anchor = publication_module.publication_anchor(parent)
    staging = publication_module.StagingDirectory.create(anchor, prefix=".stage-")
    file_fd, name = staging.create_file(prefix=".source-")
    os.close(file_fd)

    def fail_staged_unlink(*_args: object, **_kwargs: object) -> None:
        msg = "staged file is busy"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_unlink", fail_staged_unlink)
    with pytest.raises(OSError, match="staged file is busy"):
        staging.cleanup()
    monkeypatch.undo()
    (staging.path / name).unlink()
    staging.path.rmdir()
    publication_module.close_anchor(anchor)


@pytest.mark.allow_direct_assert
@pytest.mark.skipif(os.name == "nt", reason="private descriptor names require POSIX dir_fd support")
def test_staging_directory_fails_closed_for_unavailable_private_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name collisions and an open failure never leave an attacker-controlled staging entry behind."""
    parent = tmp_path / "parent"
    parent.mkdir()
    anchor = publication_module.publication_anchor(parent)

    monkeypatch.setattr(publication_module, "_private_name", lambda _prefix: ".stage")

    def fail_staging_open(*_args: object, **_kwargs: object) -> int:
        msg = "private staging open failed"
        raise OSError(msg)

    monkeypatch.setattr(publication_module.os, "open", fail_staging_open)
    with pytest.raises(OSError, match="private staging open failed"):
        publication_module.StagingDirectory.create(anchor, prefix=".stage-")
    assert not list(parent.iterdir())
    monkeypatch.undo()

    staging = publication_module.StagingDirectory.create(anchor, prefix=".stage-")
    monkeypatch.setattr(publication_module, "_private_name", lambda _prefix: ".source")

    def collide_with_reserved_name(*_args: object, **_kwargs: object) -> int:
        name = ".source"
        raise FileExistsError(name)

    monkeypatch.setattr(publication_module.os, "open", collide_with_reserved_name)
    with pytest.raises(FileExistsError, match="could not reserve private staged file"):
        staging.create_file(prefix=".source-")
    staging.path.rmdir()
    staging.cleanup()
    monkeypatch.undo()

    monkeypatch.setattr(
        publication_module.os,
        "mkdir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()),
    )
    with pytest.raises(FileExistsError, match="could not reserve private staging"):
        publication_module.StagingDirectory.create(anchor, prefix=".stage-")
    publication_module.close_anchor(anchor)


@pytest.mark.allow_direct_assert
def test_staging_directory_path_fallback_cleanup_does_not_require_descriptors(tmp_path: Path) -> None:
    """The lexical staging fallback removes its private directory without attempting descriptor cleanup."""
    parent = tmp_path / "parent"
    path = parent / ".stage"
    path.mkdir(parents=True)
    path_stat = path.stat()
    parent_stat = parent.stat()
    staging = publication_module.StagingDirectory(
        (None, None),
        path.name,
        path,
        fallback=publication_module._StagingFallback(
            (path_stat.st_dev, path_stat.st_ino),
            (parent_stat.st_dev, parent_stat.st_ino),
        ),
    )

    staging.cleanup()

    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="descriptor publication requires POSIX dir_fd support")
@pytest.mark.allow_direct_assert
def test_descriptor_publication_error_and_rollback_primitives_preserve_private_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Descriptor-only error recovery keeps backups and failed cleanup inside the pinned parent."""
    parent = tmp_path / "parent"
    parent.mkdir()
    anchor = publication_module.publication_anchor(parent)
    moved_parent = tmp_path / "moved-parent"
    parent.rename(moved_parent)
    assert not publication_module._directory_fd_matches_path(anchor.directory_fd, parent)
    publication_module.close_anchor(anchor)
    moved_parent.rename(parent)

    target = parent / "target.py"
    target.write_text("generated\n", encoding="utf-8")
    publication_module._validate_publication_anchor(publication_module.StagedFile(None, target, target))
    directory_fd = os.open(parent, publication_module._directory_open_flags())
    target_stat = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
    collision = ".target.py.collision.bak"
    os.close(os.open(collision, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd))
    monkeypatch.setattr(publication_module, "_backup_names", lambda _name: iter((collision,)))
    with pytest.raises(FileExistsError, match="could not reserve backup"):
        publication_module._backup_existing_target_at(directory_fd, target.name, target_stat)
    monkeypatch.undo()

    def fail_hardlink(*_args: object, **_kwargs: object) -> None:
        msg = "hardlinks unavailable"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_backup_names", lambda _name: iter((collision,)))
    monkeypatch.setattr(publication_module.os, "link", fail_hardlink)
    with pytest.raises(FileExistsError, match="could not reserve backup"):
        publication_module._backup_existing_target_at(directory_fd, target.name, target_stat)
    monkeypatch.undo()

    backup_name = ".target.py.backup"
    os.link(target.name, backup_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
    publication_module._restore_backup_at(
        publication_module._BoundPublishedFile(target, directory_fd, target.name, backup_name)
    )
    assert not (parent / backup_name).exists()

    missing_target = parent / "missing.py"
    missing_backup = ".missing.py.backup"
    os.close(os.open(missing_backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd))
    publication_module._restore_backup_at(
        publication_module._BoundPublishedFile(missing_target, directory_fd, missing_target.name, missing_backup)
    )
    assert missing_target.is_file()

    symlink_target = parent / "symlink.py"
    os.symlink(target.name, ".symlink.py.backup", dir_fd=directory_fd)
    os.symlink(target.name, symlink_target.name, dir_fd=directory_fd)
    publication_module._restore_backup_at(
        publication_module._BoundPublishedFile(symlink_target, directory_fd, symlink_target.name, ".symlink.py.backup")
    )
    assert not (parent / ".symlink.py.backup").exists()

    def fail_target_unlink(*_args: object, **_kwargs: object) -> None:
        msg = "target is busy"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_unlink", fail_target_unlink)
    assert publication_module._rollback_bound_file(
        publication_module._BoundPublishedFile(target, directory_fd, target.name, None)
    ) == [target]
    monkeypatch.undo()

    staged = parent / "staged.py"
    staged.write_text("staged\n", encoding="utf-8")
    staged_file = publication_module.StagedFile(staged, target, target)
    publication_module._set_staged_mode(staged_file, 0o640)

    descriptor_staged_file = publication_module.StagedFile(
        None,
        target,
        target,
        source_directory_fd=directory_fd,
        source_name=staged.name,
    )
    publication_module._set_staged_mode(descriptor_staged_file, 0o640)
    assert stat.S_IMODE(os.stat(staged.name, dir_fd=directory_fd).st_mode) == 0o640

    def fail_staged_chmod(*_args: object, **_kwargs: object) -> None:
        msg = "staged file is busy"
        raise OSError(msg)

    monkeypatch.setattr(Path, "chmod", fail_staged_chmod)
    publication_module._set_staged_mode(staged_file, 0o640)
    monkeypatch.undo()
    os.close(directory_fd)


@pytest.mark.allow_direct_assert
def test_descriptor_publication_copy_and_journal_failure_paths_use_real_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy fallback and rollback failures leave the original source and report owned directories."""
    source = tmp_path / "source.py"
    source.write_text("source\n", encoding="utf-8")
    source_stat = source.stat()
    file_fd = publication_module._open_file_at(source, os.O_RDONLY, 0, None)
    os.close(file_fd)

    def fail_hardlink(*_args: object, **_kwargs: object) -> None:
        msg = "hardlinks unavailable"
        raise OSError(msg)

    monkeypatch.setattr(publication_module.os, "link", fail_hardlink)
    backup = publication_module._backup_existing_target(source)
    assert backup.read_text(encoding="utf-8") == "source\n"
    monkeypatch.undo()

    failed_backup = tmp_path / "failed-backup.py"

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        msg = "copy interrupted"
        raise OSError(msg)

    monkeypatch.setattr(publication_module.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="copy interrupted"):
        publication_module._copy_target(source, failed_backup, source_stat, directory_fd=None)
    assert not failed_backup.exists()
    monkeypatch.undo()

    planned = publication_module._planned_staged_file((source, tmp_path / "planned.py"))
    assert planned.staged_file == source
    with pytest.raises(OSError, match="requires a destination descriptor"):
        publication_module._replace_source(
            publication_module.StagedFile(
                None,
                source,
                source,
                source_directory_fd=0,
                source_name=source.name,
            ),
            source.name,
            None,
        )

    path_staged = tmp_path / "path-staged.py"
    path_target = tmp_path / "path-target.py"
    path_staged.write_text("path staged\n", encoding="utf-8")
    publication_module._replace_source(
        publication_module.StagedFile(path_staged, path_target, path_target),
        path_target,
        None,
    )
    assert path_target.read_text(encoding="utf-8") == "path staged\n"

    target_directory = tmp_path / "target-directory"
    target_directory.mkdir()
    with pytest.raises(IsADirectoryError):
        publication_module.publish_staged_files((
            publication_module.StagedFile(source, target_directory, target_directory),
        ))

    generated = tmp_path / "generated.py"
    generated.write_text("generated\n", encoding="utf-8")
    nested_target = tmp_path / "created" / "target.py"

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        msg = "replacement interrupted"
        raise OSError(msg)

    def fail_created_directory_removal(*_args: object, **_kwargs: object) -> None:
        msg = "directory is busy"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_replace", fail_replace)
    monkeypatch.setattr(publication_module, "_rmdir", fail_created_directory_removal)
    with pytest.raises(OSError, match="failed to roll back batch output"):
        publication_module.publish_staged_files((
            publication_module.StagedFile(generated, nested_target, nested_target),
        ))
    monkeypatch.undo()
    assert generated.is_file()
    assert nested_target.parent.is_dir()


@pytest.mark.allow_direct_assert
def test_path_publication_helpers_preserve_modes_and_own_only_created_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lexical fallback helpers have the same real-filesystem rollback semantics as descriptor publication."""
    target = tmp_path / "target.py"
    staged = tmp_path / "staged.py"
    target.write_text("stale\n", encoding="utf-8")
    staged.write_text("generated\n", encoding="utf-8")
    target.chmod(0o640)

    publication_module._preserve_target_mode(staged, target)

    assert bool(staged.stat().st_mode & stat.S_IWUSR) is bool(target.stat().st_mode & stat.S_IWUSR)

    created_directories: list[Path] = []
    nested_target = tmp_path / "created" / "nested" / "model.py"
    publication_module._create_target_parent(nested_target, created_directories)

    assert created_directories == [tmp_path / "created", tmp_path / "created" / "nested"]
    assert publication_module._create_directory(created_directories[-1]) is False

    backup = tmp_path / "target.backup"
    backup.hardlink_to(target)
    publication_module._restore_backup(backup, target)

    assert not backup.exists()

    for directory in reversed(created_directories):
        assert publication_module._remove_created_directory(directory) == []

    collision = tmp_path / ".target.py.reserved.bak"
    collision.write_text("reserved\n", encoding="utf-8")
    monkeypatch.setattr(publication_module, "_backup_names", lambda _target_name: iter((collision.name,)))
    with pytest.raises(FileExistsError, match="could not reserve backup"):
        publication_module._backup_existing_target(target)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not available on Windows")
@pytest.mark.allow_direct_assert
def test_path_publication_helpers_preserve_posix_modes(tmp_path: Path) -> None:
    """Path publication copies every POSIX permission bit from an existing target."""
    target = tmp_path / "target.py"
    staged = tmp_path / "staged.py"
    target.write_text("stale\n", encoding="utf-8")
    staged.write_text("generated\n", encoding="utf-8")
    target.chmod(0o640)

    publication_module._preserve_target_mode(staged, target)

    assert staged.stat().st_mode & 0o777 == 0o640


@pytest.mark.allow_direct_assert
def test_remote_lock_legacy_staging_reuses_and_discards_one_private_file(tmp_path: Path) -> None:
    """The direct API retains its Path staging compatibility without leaking a temporary lock."""
    lockfile = tmp_path / "nested" / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")

    staged_path = updater.stage()

    assert isinstance(staged_path, Path)
    assert staged_path.is_file()
    assert updater.stage() == staged_path
    updater.discard_stage()
    assert not staged_path.exists()
    assert not lockfile.parent.exists()


@pytest.mark.allow_direct_assert
def test_remote_lock_commit_rejects_legacy_path_staging_without_leaking_a_temporary_file(tmp_path: Path) -> None:
    """The direct Path staging API cannot be mixed with descriptor-bound commit publication."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")
    staged_path = updater.stage()

    assert isinstance(staged_path, Path)
    with pytest.raises(RemoteLockError, match="legacy Path staging"):
        updater.commit()
    assert not staged_path.exists()
    assert not lockfile.exists()

    updater.commit()
    assert lockfile.is_file()


@pytest.mark.allow_direct_assert
def test_remote_lock_stage_is_inactive_after_commit_or_without_update(tmp_path: Path) -> None:
    """Read-only and committed collectors never allocate legacy staging files."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    readonly = RemoteReferenceLock.open(lockfile, update=False, locked=False)
    assert readonly.stage() is None

    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")
    updater.commit()

    assert updater.stage() is None


@pytest.mark.allow_direct_assert
def test_remote_lock_can_commit_an_empty_observed_closure(tmp_path: Path) -> None:
    """An explicit update writes a deterministic empty lock when no remote resource was fetched."""
    lockfile = tmp_path / "datamodel-codegen.lock"

    RemoteReferenceLock.open(lockfile, update=True, locked=False).commit()

    assert json.loads(lockfile.read_text(encoding="utf-8")) == {"resources": [], "version": 1}


@pytest.mark.allow_direct_assert
def test_remote_lock_commit_releases_no_resources_when_anchor_preparation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An anchor failure is surfaced as a lock error before any staging resource exists."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")

    def fail_anchor(*_args: object, **_kwargs: object) -> object:
        msg = "destination unavailable"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "publication_anchor", fail_anchor)

    with pytest.raises(RemoteLockError, match="destination unavailable"):
        updater.commit()

    assert not lockfile.exists()


def test_remote_lock_reports_missing_malformed_unknown_and_changed_entries(tmp_path: Path) -> None:
    """Verification fails closed for each lock integrity error condition."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    with pytest.raises(RemoteLockError, match="not found"):
        RemoteReferenceLock.open(lockfile, update=False, locked=True)

    lockfile.write_text("{}", encoding="utf-8")
    with pytest.raises(RemoteLockError, match="expected version"):
        RemoteReferenceLock.open(lockfile, update=False, locked=False)

    lockfile.unlink()
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"before")
    updater.commit()
    verifier = RemoteReferenceLock.open(lockfile, update=False, locked=True)
    with pytest.raises(RemoteLockError, match="not recorded"):
        verifier.record_response("https://schemas.example/new.json", None, None, b"new")
    with pytest.raises(RemoteLockError, match="content does not match"):
        verifier.record_response("https://schemas.example/schema.json", None, None, b"after")


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_remote_lock_requires_exact_integer_version_one(tmp_path: Path, version: object) -> None:
    """Booleans and numerically equal non-integers are not lock format versions."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    lockfile.write_text(json.dumps({"version": version, "resources": []}), encoding="utf-8")

    with pytest.raises(RemoteLockError, match="expected version 1"):
        RemoteReferenceLock.open(lockfile, update=False, locked=False)


@pytest.mark.allow_direct_assert
def test_remote_lock_rejects_nondeterministic_repeated_responses_even_when_updating(tmp_path: Path) -> None:
    """An update must not silently choose one of two bodies for the same request."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)

    updater.record_response("https://schemas.example/schema.json", None, None, b"first")
    updater.record_response("https://schemas.example/schema.json", None, None, b"first")
    with pytest.raises(RemoteLockError, match="different content in one generation"):
        updater.record_response("https://schemas.example/schema.json", None, None, b"second")

    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "Unable to read remote lock"),
        ('{"version":1,"resources":{}}', "resources must be a list"),
        ('{"version":1,"resources":[null]}', "each resource must be an object"),
        ('{"version":1,"resources":[{}]}', "Invalid remote lock resource"),
        (
            '{"version":1,"resources":[{"request_sha256":"sha256:'
            + "0" * 64
            + '","body_sha256":"sha256:'
            + "1" * 64
            + '","url":"https://alice:password@schemas.example/schema.json?token=secret"}]}',
            "Invalid remote lock resource URL",
        ),
        (
            '{"version":1,"resources":[{"request_sha256":"sha256:'
            + "z" * 64
            + '","body_sha256":"sha256:'
            + "1" * 64
            + '","url":"https://schemas.example"}]}',
            "Invalid remote lock resource",
        ),
        (
            '{"version":1,"resources":[{"request_sha256":"sha256:'
            + "0" * 64
            + '","body_sha256":"sha256:'
            + "1" * 64
            + '","url":"https://[not-an-ipv6"}]}',
            "Invalid remote lock resource URL",
        ),
    ],
)
def test_remote_lock_rejects_malformed_resource_shapes(tmp_path: Path, content: str, message: str) -> None:
    """Malformed persisted data always fails before generation can continue."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    lockfile.write_text(content, encoding="utf-8")

    with pytest.raises(RemoteLockError, match=message):
        RemoteReferenceLock.open(lockfile, update=False, locked=False)


@pytest.mark.allow_direct_assert
def test_remote_lock_rejects_invalid_utf8(tmp_path: Path) -> None:
    """Persisted bytes are always decoded into a clean lock error."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    lockfile.write_bytes(b"\xff")

    with pytest.raises(RemoteLockError, match="Unable to read remote lock"):
        RemoteReferenceLock.open(lockfile, update=False, locked=False)


@pytest.mark.allow_direct_assert
def test_remote_lock_rejects_unparseable_request_urls(tmp_path: Path) -> None:
    """Malformed request URLs cannot escape the lock API as URL parser errors."""
    lock = RemoteReferenceLock.open(tmp_path / "unused.lock", update=True, locked=False)

    with pytest.raises(RemoteLockError, match="Invalid remote lock URL"):
        lock.record_response("https://[not-an-ipv6", None, None, b"body")
    with pytest.raises(RemoteLockError, match="Invalid remote lock URL"):
        remote_lock._display_url("https://[not-an-ipv6")


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    "url",
    [
        "schema.example/file.json",
        "file:///schema.json",
        "https:///schema.json",
        "https://schemas.example:bad/schema.json",
        "https://schemas.example:65536/schema.json",
    ],
)
def test_remote_lock_rejects_invalid_request_scheme_host_and_port(tmp_path: Path, url: str) -> None:
    """Only HTTP(S) URLs with a usable host and port can become request identities."""
    lock = RemoteReferenceLock.open(tmp_path / "unused.lock", update=True, locked=False)

    with pytest.raises(RemoteLockError, match="Invalid remote lock URL"):
        lock.record_response(url, None, None, b"body")


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    "url",
    [
        "ftp://schemas.example",
        "https:///schema.json",
        "https://schemas.example/schema.json",
        "https://schemas.example:bad",
        "https://schemas.example:65536",
    ],
)
def test_remote_lock_rejects_invalid_persisted_scheme_host_and_port(tmp_path: Path, url: str) -> None:
    """Malformed persisted origins fail closed with a lock-specific diagnostic."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    lockfile.write_text(
        json.dumps({
            "version": 1,
            "resources": [
                {
                    "request_sha256": f"sha256:{'0' * 64}",
                    "body_sha256": f"sha256:{'1' * 64}",
                    "url": url,
                }
            ],
        }),
        encoding="utf-8",
    )

    with pytest.raises(RemoteLockError, match="Invalid remote lock resource URL"):
        RemoteReferenceLock.open(lockfile, update=False, locked=False)


@pytest.mark.allow_direct_assert
def test_remote_lock_rejects_duplicate_identities_and_normalizes_unusual_urls(tmp_path: Path) -> None:
    """Reject duplicate identities while keeping lock display URLs safe and canonical."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://[2001:db8::1]/schema.json", None, None, b"schema")
    updater.commit()
    resource = json.loads(lockfile.read_text(encoding="utf-8"))["resources"][0]
    assert resource["url"] == "https://[2001:db8::1]"
    with pytest.raises(RemoteLockError, match="Invalid remote lock URL"):
        remote_lock._display_url("https://schemas.example:bad/schema.json")

    lockfile.write_text(json.dumps({"version": 1, "resources": [resource, resource]}), encoding="utf-8")
    with pytest.raises(RemoteLockError, match="duplicate request identity"):
        RemoteReferenceLock.open(lockfile, update=False, locked=False)


@pytest.mark.allow_direct_assert
def test_remote_lock_cleans_up_after_atomic_write_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both pre-rename and post-rename failures leave no partial target lock."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")

    def raise_os_error(*_args: object, **_kwargs: object) -> None:
        message = "full"
        raise OSError(message)

    monkeypatch.setattr(publication_module.StagingDirectory, "create_file", raise_os_error)
    with pytest.raises(RemoteLockError, match="Unable to update remote lock"):
        updater.commit()
    assert not lockfile.exists()

    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")
    monkeypatch.undo()
    monkeypatch.setattr(publication_module, "_replace", raise_os_error)
    with pytest.raises(RemoteLockError, match="Unable to update remote lock"):
        updater.commit()
    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_remote_lock_atomic_failures_remove_temps_and_allow_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A transient pre-replace failure leaves no temp file and does not commit the collector."""
    lockfile = tmp_path / "datamodel-codegen.lock"
    updater = RemoteReferenceLock.open(lockfile, update=True, locked=False)
    updater.record_response("https://schemas.example/schema.json", None, None, b"schema")

    def raise_os_error(*_args: object, **_kwargs: object) -> None:
        message = "full"
        raise OSError(message)

    if failure == "write":
        monkeypatch.setattr(RemoteReferenceLock, "_write_staged_content", raise_os_error)
    else:
        monkeypatch.setattr(remote_lock.os, "fsync", raise_os_error)

    with pytest.raises(RemoteLockError, match="Unable to update remote lock"):
        updater.commit()
    assert not lockfile.exists()
    assert not list(tmp_path.glob(".datamodel-codegen.lock.*"))

    monkeypatch.undo()
    updater.commit()
    assert lockfile.is_file()
