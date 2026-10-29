"""Where a run's remote lock lives and which policy its options give it, resolved without importing lock machinery.

The command line, ``generate()`` and the target runs read the lock options here only. They differ in how a lockfile
spelling becomes a path, and each difference is a named row of ``remote_lock_plan``:

* ``lexical`` (the command line): ``os.path.abspath`` against the working directory, then resolved;
* ``resolved`` (an update that ``generate()`` publishes with its artifacts): joined to the caller's directory, then
  resolved;
* ``legacy-api-lockfile-as-written`` (the other ``generate()`` runs): an absolute spelling is opened, checked and
  named as written, a relative one is joined to the caller's directory and resolved.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

if TYPE_CHECKING:
    from datamodel_code_generator.base_config import BaseGenerateConfig

LOCKFILE_NAME: Final = "datamodel-codegen.lock"

LockPolicy = Literal["inactive", "verify", "locked", "update"]
LockSpelling = Literal["lexical", "resolved", "legacy-api-lockfile-as-written"]


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


def remote_lock_plan(config: BaseGenerateConfig, default_parent: Path, spelling: LockSpelling) -> RemoteLockPlan:
    """Return the lock a config uses: its lockfile, or ``datamodel-codegen.lock`` in ``default_parent``.

    The policy is an update, a locked run, a verification of the file that exists, or inactive. ``resolved`` and the
    legacy row join a relative spelling to ``default_parent``; ``lexical`` reads it from the working directory.
    """
    path = (config.lockfile or default_parent / LOCKFILE_NAME).expanduser()
    literal = path if path.is_absolute() else default_parent / path
    if spelling == "lexical":
        literal = Path(os.path.abspath(path))  # noqa: PTH100
        canonical = literal.resolve(strict=False)
    elif spelling == "resolved":
        canonical = literal.resolve(strict=False)
    else:
        canonical = literal = path if path.is_absolute() else literal.resolve()
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


def models_lockfile(config: BaseGenerateConfig, default_parent: Path) -> Path | None:
    """Return the lockfile a command-line target hands its models, or None while no lock applies.

    A given lockfile is handed as written, so the models plan it again; the default one only while a policy uses it:
    an update, a locked run, or an existing file to verify.
    """
    if config.lockfile is not None:
        return config.lockfile
    plan = remote_lock_plan(config, default_parent, "lexical")
    return None if plan.policy == "inactive" else plan.literal_path
