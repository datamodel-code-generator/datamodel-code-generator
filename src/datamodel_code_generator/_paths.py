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

A ``file`` URI names one local path on every Python version (``file_uri_path``): the generator splits the URI itself
and hands only its path to ``url2pathname``, whose reading of a host and a query differs between Python versions.

An output path names a directory, a package of modules, when it has no suffix. The files a generation stages or
copies are the files below that directory, listed in path order. The files of a directory input are listed by file
name, files of one name in the order the directory walk meets them.

What a generation wrote at an output path is its layout: a file is one module, any other existing path a package of
modules, a path that holds nothing has none. One module pairs with the destination itself, each file of a package with
its path below the destination. One module cannot replace an existing directory, and a package cannot replace an
existing file (``layout_refusal``): every run that writes, stages or checks an output refuses such a generation
before it copies, compares or publishes anything.
"""

from __future__ import annotations

import errno
import os
from operator import attrgetter
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

AncestorTest = Literal["directory", "exists"]
FileOrder = Literal["path", "name"]
OutputLayout = Literal["module", "package"]

SINGLE_MODULE_OUTPUT_DIRECTORY_ERROR: Final = "Single-module output requires a file path, not a directory"
MODULAR_OUTPUT_FILE_ERROR: Final = "Modular references require an output directory, not a file"


def lexical(path: Path, base: Path | None = None) -> Path:
    """Return the lexical form: ``~`` expanded, joined to ``base`` (or the working directory), dot components folded."""
    expanded = path.expanduser()
    return Path(os.path.abspath(expanded if base is None else base / expanded))  # noqa: PTH100


def resolved(path: Path, base: Path | None = None) -> Path:
    """Return the resolved form: ``~`` expanded, joined to ``base`` (or the working directory), symlinks resolved.

    A symlink loop stays as written from the link that starts it, on every Python version (``os.path.realpath``;
    ``Path.resolve`` raises ``RuntimeError`` there before Python 3.13). An existing path whose links name nothing that
    exists, such as ``/dev/stdin`` reading a pipe (``pipe:[...]``), keeps its lexical form, which opens it.
    """
    expanded = path.expanduser()
    joined = expanded if base is None else base / expanded
    if (real := Path(os.path.realpath(joined))).exists() or not joined.exists():
        return real
    return Path(os.path.abspath(joined))  # noqa: PTH100


def existing_ancestor(
    path: Path,
    *,
    test: AncestorTest = "directory",
    error: Callable[[Path], Exception] | None = None,
    missing: list[Path] | None = None,
) -> Path:
    """Return the deepest of ``path`` and its parents that exists by ``test``; ``missing`` receives those that do not.

    Rows of ``test``: ``directory`` holds for an existing directory (an anchor, a temporary file, a watch root);
    ``exists`` for any existing path, so the parents below it are the ones a publication creates (``creation_root``).
    A root that fails the test (a missing drive) is returned, or raises ``error(root)`` when given (``missing_root``
    for the publication walks).
    """
    while not (path.is_dir() if test == "directory" else path.exists()):
        if (parent := path.parent) == path:
            if error is not None:
                raise error(path)
            return path
        if missing is not None:
            missing.append(path)
        path = parent
    return path


def missing_root(root: Path) -> FileNotFoundError:
    """Return the error of a walk that reaches a root that does not exist, such as a missing drive, naming the root."""
    return FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(root))


def creation_root(path: Path) -> Path:
    """Return the deepest existing path of ``path`` and its parents, below which the missing ones are created.

    It must be a directory: a regular file there raises ``NotADirectoryError`` and a root that does not exist (a
    missing drive) ``FileNotFoundError``, each naming that path, so the error is the same on every run.
    """
    if not (root := existing_ancestor(path, test="exists", error=missing_root)).is_dir():
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(root))
    return root


def concrete_parent(path: Path) -> Path:
    """Return the directory ``path`` is published into: its parent, with ``~`` expanded and symlinks resolved."""
    return resolved(path.expanduser().parent)


def concrete(path: Path) -> Path:
    """Return where ``path`` is published: its concrete parent joined with its last component as written."""
    return concrete_parent(path) / path.name


def overlaps(first: Path, second: Path) -> bool:
    """Return whether one of two paths lies inside the other or is the other, compared as written."""
    return first.is_relative_to(second) or second.is_relative_to(first)


def same_file(first: Path, second: Path) -> bool:
    """Return whether two paths name one existing file, symlinks followed: a path that does not exist names none."""
    return first.exists() and second.exists() and first.samefile(second)


def file_uri_path(uri: str) -> Path:
    """Return the local path a ``file`` URI names (RFC 8089): its path percent-decoded, without the query or fragment.

    A host other than ``localhost``, in any letter case as RFC 3986 compares hosts, names a UNC share, ``//host/path``.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415
    from urllib.request import url2pathname  # noqa: PLC0415

    parts = urlsplit(uri)
    path = url2pathname(parts.path)
    return Path(path if parts.netloc.lower() in {"", "localhost"} else f"//{parts.netloc}{path}")


def names_directory(path: Path) -> bool:
    """Return whether an output path names a directory: it has no suffix."""
    return not path.suffix


def files_under(directory: Path, pattern: str = "*", *, order: FileOrder = "path") -> list[Path]:
    """Return the files below a directory whose names match ``pattern``, in path or file-name order; none below a file.

    The name order keeps files of one name in the order ``Path.rglob`` yields them.
    """
    files = (path for path in directory.rglob(pattern) if path.is_file())
    return sorted(files, key=attrgetter("name")) if order == "name" else sorted(files)


def output_layout(path: Path) -> OutputLayout | None:
    """Return what a generation wrote at a path: one module (a file), a package (any other existing path) or None."""
    if path.is_file():
        return "module"
    return "package" if path.exists() else None


def output_files(path: Path, pattern: str = "*") -> list[tuple[tuple[str, ...], Path]]:
    """Return the files a generation wrote at a path, each with its parts below the path, in path order.

    One module is the path itself without parts, a package its files below it whose names match ``pattern``, and
    nothing written has none.
    """
    if output_layout(path) == "module":
        return [((), path)]
    return [(file.relative_to(path).parts, file) for file in files_under(path, pattern)]


def layout_refusal(layout: OutputLayout | None, destination: Path, *, compares: bool = False) -> str | None:
    """Return why an output of ``layout`` cannot replace what ``destination`` holds, or None.

    One module cannot replace an existing directory, a package an existing file. A comparison against another input
    (``compares``) reads no destination, so it refuses nothing.
    """
    if compares:
        return None
    if layout == "module" and destination.is_dir():
        return SINGLE_MODULE_OUTPUT_DIRECTORY_ERROR
    if layout == "package" and destination.is_file():
        return MODULAR_OUTPUT_FILE_ERROR
    return None
