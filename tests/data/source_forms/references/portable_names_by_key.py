"""Rule portable-names: the keys of a mapping that a helper turns into file names are file names.

``write`` names files by the entries of the mapping it takes. A mapping passed to it, directly or through a module-level
name that an argument reads (``CASES[case]``), at any depth, is checked key by key. A key that is a name read from a constant (`KEY`) is the string it holds, and a mapping held by a local name passed to
the helper is checked like one written in the call. A mapping that nothing passes to it and a loop over a parameter that
names no file are not.
"""

from pathlib import Path

CASES = {"ok": {"fine": "x", "bad:name": "y"}, "other": {"quote\"": "z"}}
UNUSED = {"not:passed": 1}
KEY = "var:key"


def write(root, files):
    for name, text in files.items():
        (root / f"{name}.toml").write_text(text)


def count(files):
    for key in files:
        print(key)


def run(root: Path, case):
    write(root, CASES[case])
    write(root, files={"also:bad": "x", "fine": "y"})
    count({"not:a:file": 1})


def run_through_a_name(root: Path):
    files = {KEY: "x", "fine": "y"}
    write(root, files)
