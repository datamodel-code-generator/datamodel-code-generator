"""Where a run's remote lock lives and which policy its options give it, resolved without importing lock machinery.

The command line, ``generate()`` and the target runs read the lock options here only. The default lockfile lies in
the directory ``default_lock_parent`` names. Every run reads a lockfile spelling in the lexical form from the caller's
directory (the working directory on the command line), so ``..`` after a symlink names one file in every run, and
publishes to the resolved form of that, so a symlinked lockfile stays a symlink and its target is written.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from datamodel_code_generator._paths import lexical, resolved

if TYPE_CHECKING:
    from datamodel_code_generator.base_config import BaseGenerateConfig

LOCKFILE_NAME: Final = "datamodel-codegen.lock"

LockPolicy = Literal["inactive", "verify", "locked", "update"]


class RemoteLockPlan(NamedTuple):
    """The effective remote-lock policy for one resolved generation config."""

    canonical_path: Path
    literal_path: Path
    policy: LockPolicy

    @property
    def active(self) -> bool:
        return self.policy != "inactive"

    @property
    def read_only(self) -> bool:
        """Whether the policy never replaces the lock artifact."""
        return self.policy in {"verify", "locked"}


def default_lock_parent(pyproject_path: Path | None, cwd: Path | None = None) -> Path:
    """Return the directory of the default lockfile: the pyproject.toml directory, or ``cwd`` without one.

    ``cwd`` is the working directory when omitted. The command line passes the pyproject.toml it read, ``generate()``
    the one ``load_pyproject_config()`` read for its config, so the default lockfile of a project is one file however
    the project is run; ``generate()`` with any other config keeps it in the caller's directory.
    """
    if pyproject_path is not None:
        return pyproject_path.parent
    return Path.cwd() if cwd is None else cwd


def remote_lock_plan(
    config: BaseGenerateConfig, pyproject_path: Path | None, cwd: Path | None = None
) -> RemoteLockPlan:
    """Return the lock a config uses: its lockfile, or ``datamodel-codegen.lock`` in ``default_lock_parent``.

    The literal path is the lexical form from ``cwd`` (the working directory when omitted), the canonical path the
    resolved form of the literal one. The policy is an update, a locked run, a verification of the file that exists, or
    inactive.
    """
    default_parent = default_lock_parent(pyproject_path, cwd)
    literal = lexical(config.lockfile or default_parent / LOCKFILE_NAME, cwd)
    canonical = resolved(literal)
    policy: LockPolicy
    if config.update_lock:
        policy = "update"
    elif config.locked:
        policy = "locked"
    elif canonical.is_file():
        policy = "verify"
    else:
        policy = "inactive"
    return RemoteLockPlan(canonical, literal, policy)


def owns_lock_update(config: BaseGenerateConfig) -> bool:
    """Return whether a run updates a lock no caller resolved for it, so it publishes the update itself."""
    return config.update_lock and not config.remote_lock_resolved


def models_lockfile(config: BaseGenerateConfig, pyproject_path: Path | None) -> Path | None:
    """Return the lockfile a command-line target hands its models, or None while no lock applies.

    A given lockfile is handed as written, so the models plan it again; the default one only while a policy uses it:
    an update, a locked run, or an existing file to verify.
    """
    if config.lockfile is not None:
        return config.lockfile
    plan = remote_lock_plan(config, pyproject_path)
    return None if plan.policy == "inactive" else plan.literal_path
