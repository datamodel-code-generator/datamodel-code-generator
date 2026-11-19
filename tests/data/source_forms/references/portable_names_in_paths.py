"""G13 portable-names: a string in a path position that Windows refuses is a site.

The positions are the right of ``/``, the arguments of ``Path``, ``os.path.join``, ``.joinpath``, ``.with_name`` and the
first argument of ``open``; the constant parts of an f-string count as fragments of a name. A colon, ``<>"|?*`` or a
control character is refused, a trailing dot or space is dropped and a device name (``con``, ``aux.txt``) is reserved.
A drive letter that opens the path, a glob pattern, a string that is no path and a name that holds only characters
Windows takes are no sites.
"""

import os.path
from pathlib import Path


def build(root, name):
    fine = root / "ok.txt"
    colon = root / "has:colon.txt"
    device = root / "con.txt"
    question = Path("a", "b?.json")
    trailing = os.path.join("x", "trail.")
    reserved = root.joinpath("sub", "AUX")
    fragment = root / f"{name}:x.toml"
    template = root / f"{name}.toml"
    drive = Path("C:/windows/x")
    pipe = open("name|pipe")
    pattern = root.glob("*.py")
    parent = root / "a/b/../c"
    beside = "has:colon.txt"
    return fine, colon, device, question, trailing, reserved, fragment, template, drive, pipe, pattern, parent, beside
