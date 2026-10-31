"""Local filesystem paths: the forms of a spelled path, its deepest existing ancestor and the place it is published to.

A spelled path (an option value or a caller's argument) has two absolute forms: the lexical form expands ``~`` and
folds ``.`` and ``..`` as written, keeping symlinks; the resolved form expands ``~`` and resolves symlinks. Both join a
relative spelling to the caller's directory when one is given, else to the working directory. The checks that two
paths are one, overlap or hold one another compare these forms.

The publication journal anchors, stages and creates parents from the deepest existing ancestor, the remote lock writes
its temporary file there and the watcher watches there. The callers differ in what counts as existing and in what
happens at a root that fails, and each difference is a named row of ``existing_ancestor``.

The concrete form of a path is its parent with ``~`` expanded and symlinks resolved, joined with its last component as
written: the journal binds a staged file to it, the remote lock commits to it, and the watcher and the target checks
compare with it.

Two paths relate as written, without the filesystem: one lies inside the other when it is the other or below it
(``Path.is_relative_to``), and they overlap when either lies inside the other. Two existing paths name one file when
they reach one inode; the callers decide which forms they compare and what an error of the filesystem means.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

AncestorTest = Literal["directory", "exists", "legacy-staging-parent-may-be-a-file"]
AtRoot = Literal["root", "legacy-unbounded-ancestor-walk"]
Tilde = Literal["expand", "legacy-metadata-tilde-as-written"]
Missing = Literal["none", "legacy-missing-input-raises"]


def lexical(path: Path, base: Path | None = None) -> Path:
    """Return the lexical form: ``~`` expanded, joined to ``base`` (or the working directory), dot components folded."""
    expanded = path.expanduser()
    return Path(os.path.abspath(expanded if base is None else base / expanded))  # noqa: PTH100


def resolved(path: Path, base: Path | None = None) -> Path:
    """Return the resolved form: ``~`` expanded, joined to ``base`` (or the working directory), symlinks resolved."""
    expanded = path.expanduser()
    return (expanded if base is None else base / expanded).resolve(strict=False)


def existing_ancestor(
    path: Path,
    *,
    test: AncestorTest = "directory",
    at_root: AtRoot = "root",
    error: Callable[[Path], Exception] | None = None,
    missing: list[Path] | None = None,
) -> Path:
    """Return the deepest of ``path`` and its parents that exists by ``test``; ``missing`` receives those that do not.

    Rows of ``test``: ``directory`` holds for an existing directory (an anchor, a temporary file, a watch root);
    ``exists`` for any existing path, so the parents below it are the ones a publication creates;
    ``legacy-staging-parent-may-be-a-file`` (private staging, #5618) as ``exists``, so staging under a regular file
    fails naming its random private directory. Rows of ``at_root``, for a root that fails the test (a missing drive):
    ``root`` returns it, or raises ``error(root)`` when given; ``legacy-unbounded-ancestor-walk`` (the publication
    walks) keeps testing it.
    """
    while not (path.is_dir() if test == "directory" else path.exists()):
        if (parent := path.parent) == path and at_root == "root":
            if error is not None:
                raise error(path)
            return path
        if missing is not None:
            missing.append(path)
        path = parent
    return path


def concrete_parent(path: Path) -> Path:
    """Return the directory ``path`` is published into: its parent, with ``~`` expanded and symlinks resolved."""
    return path.expanduser().parent.resolve(strict=False)


def concrete(path: Path, *, tilde: Tilde = "expand") -> Path:
    """Return where ``path`` is published: its concrete parent joined with its last component as written.

    Rows of ``tilde``: ``expand`` reads a leading ``~`` as the home directory; ``legacy-metadata-tilde-as-written``
    (the files a directory input leaves out, fix C17-1) keeps it as a directory named ``~``, so the model metadata of a
    staged command spelled ``~/...`` is looked for where a direct run writes it, not where the staged run publishes it.
    """
    parent = concrete_parent(path) if tilde == "expand" else path.parent.resolve(strict=False)
    return parent / path.name


def overlaps(first: Path, second: Path) -> bool:
    """Return whether one of two paths lies inside the other or is the other, compared as written."""
    return first.is_relative_to(second) or second.is_relative_to(first)


def same_file(first: Path, second: Path, *, missing: Missing = "none") -> bool:
    """Return whether two paths name one existing file, symlinks followed.

    Rows of ``missing``: ``none`` names no file for a path that does not exist; ``legacy-missing-input-raises`` (the
    check that an output does not overwrite its input, fix C17-5) stats both paths as they are, so a missing input
    raises ``FileNotFoundError``; its caller tests that the output exists first.
    """
    if missing == "none" and not (first.exists() and second.exists()):
        return False
    return first.samefile(second)
