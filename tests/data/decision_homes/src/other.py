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


def search(name, taken, words):
    """Search for free names with an f-string and with a padded string; a loop writing a message does not count."""
    candidate, count = name, 0
    while candidate in taken:
        count += 1
        candidate = f"{name}_{count}"
    padded = name
    while padded in taken:
        padded += "_"
    message = ""
    while words:
        message = f"{words.pop()} left"
    return candidate, padded, message
