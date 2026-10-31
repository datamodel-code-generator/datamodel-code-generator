"""Local filesystem paths: the deepest existing ancestor of a path, the one walk every caller makes.

The publication journal anchors, stages and creates parents from there, the remote lock writes its temporary file there
and the watcher watches there. The callers differ in what counts as existing and in what happens at a root that fails,
and each difference is a named row of ``existing_ancestor``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

AncestorTest = Literal["directory", "exists", "legacy-staging-parent-may-be-a-file"]
AtRoot = Literal["root", "legacy-unbounded-ancestor-walk"]


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
