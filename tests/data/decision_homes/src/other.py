"""Fixture: every signature kind outside the home."""

from __future__ import annotations

LIMIT = 2**63
SHIFTED = 1 << 63
BYTE = 255
SMALL_SHIFT = 1 << 62
KEYWORDS = {"a", "b", "c", "d", "e"}
TWO = ("a", "b")
TABLE = {"a": 1, "b": 2, "c": 3}


class Other:
    """Reads and writes the decision itself."""

    def method(self, cfg, other, types):
        """Read twice, write, call, test a type list."""
        first = cfg.flag
        second = cfg.flag
        other.flag = first + second
        size = 3
        self.size = size
        compute(cfg.x, mode=1)
        compute(cfg.y, mode=1)
        compute(cfg.x, mode=1, fast=True)
        compute(cfg.x, mode=1, fast=False)
        compute(cfg.x, mode=1, **types)
        compute(cfg.x)
        return "null" in types, "x" in types, "null" not in types


def _merge_values(value):
    async def _merge_inner():
        return value

    return _merge_inner


def other_helper(types):
    """Is not a merge function."""
    return {"a", "b", "c"} & set(types)


def read_keys(schema, config):
    """Read, write and delete keys of a raw object."""
    first = schema["type"]
    second = schema.get("items")
    schema.pop("type", None)
    schema.setdefault("items", [])
    config["type"] = schema["other"]
    del config["items"]
    return first, second, schema.get(first), schema.get("other")


def spell(direction):
    """Spell the segment in a string literal and in an f-string, and write other text."""
    return "seg-" + direction, f"seg-{direction}", f"{direction}-other", "other"


def fold(location, name, other):
    """Fold by a tested value in an expression and in a statement; other tests and branches do not count."""
    key = name.lower() if location == "upper" else name
    if location == "upper":
        key = (location, name.lower())
    kept = name.lower() if location == "lower" else name
    same = name if location == "upper" else other
    return key, kept, same


def displays(head, items):
    """Join items inside a call, a tuple and a list, and write a subscript and other joins."""
    return (
        f"{head}({', '.join(items)})",
        "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")",
        f"[{', '.join(items)}]",
        f"{head}[{', '.join(items)}]",
        f"Union[{', '.join(items)}]",
        f"({'; '.join(items)})",
        ", ".join(items),
    )


def keywords(name, value):
    """Spell keyword arguments whose keyword is data; a fixed keyword, a comparison and a bare keyword do not count."""
    return (
        f"{name}={value}",
        f"call({name}={value!r})",
        f"key={value}",
        f"{name}=={value}",
        f"{name}=",
        f"{name} = {value}",
    )


def search(name, taken, words, refresh):
    """Search for free names with an f-string, an annotated f-string and a padded string.

    A loop writing a message does not count, nor one whose f-string is only an argument of the call it assigns.
    """
    candidate, count = name, 0
    while candidate in taken:
        count += 1
        candidate = f"{name}_{count}"
    annotated, index = name, 0
    while annotated in taken:
        index += 1
        annotated: str = f"{name}-{index}"
    padded = name
    while padded in taken:
        padded += "_"
    message = ""
    while words:
        message = f"{words.pop()} left"
    remaining = words
    while remaining:
        remaining = refresh(f"{name}") or (lambda: f"{name}")
    return candidate, annotated, padded, message


LABELS = {"one": "Label", "two": "Other"}


def package_files(paths, name):
    """Test file names for a package's file on either side; a membership test and another name do not count."""
    return (
        [path for path in paths if path[-1] == "__init__.py"],
        "__init__.py" != name,
        name in {"__init__.py"},
        name == "module.py",
    )


def split_names(path, name):
    """Split the names of a path in a comprehension, a loop and a joined text; one name or another separator does not."""
    parts = [part for item in path for part in item.split(".")]
    flat = []
    for item in path:
        flat.extend(item.split("."))
    return (
        parts,
        flat,
        ".".join(path).split("."),
        name.split("."),
        [part for part in name.split(".")],
        [part for item in path for part in item.split("/")],
        "/".join(path).split("."),
    )


def marked(path, consts):
    """Test a path for the marker by its name or through a module; another name, a reversed test or == do not count."""
    return MARKER in path, consts.MARKER not in path, OTHER in path, path in MARKER, MARKER == path


def climb(path, other, roots):
    """Walk up to an existing path, directly and through a name; another name, no test or a nested walk do not count."""
    while not path.exists():
        path = path.parent
    current = other
    while not current.is_dir():
        parent = current.parent
        if parent == current:
            break
        current = parent
    while not path.is_file():
        path = path.parent
    while not other.exists():
        other = roots.parent
    while current != current.parent:
        current = current.parent
    while not path.is_dir():
        roots.append(lambda: path.parent)
    return path, current
