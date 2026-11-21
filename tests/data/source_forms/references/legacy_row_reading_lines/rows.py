"""The rows of a module, named the ways a package names them; ``legacy-module-doc`` names no reading line."""

from typing import Literal, TypeAlias

KEPT = "legacy-constant"
DOCUMENTED: TypeAlias = Literal["new", "old"]
"""Row ``legacy-constant-docstring``: the docstring of a module constant."""
UNREAD = "legacy-unread-constant"


class Policy:
    """Row ``legacy-class-doc``: a class docstring."""

    row = "legacy-class-attribute"

    def choose(self, row: str) -> str:
        return "legacy-in-method" if row else ""


def decide(row: str, flag: bool) -> str:
    if row == "legacy-in-test":
        return KEPT
    match row:
        case "legacy-in-case":
            return "old"
    return (
        "legacy-in-call"
        if flag
        else ""
    )


def documented(row: str = "legacy-default") -> str:
    """Row ``legacy-function-doc``: the function's first statement reads it."""
    choice: DOCUMENTED = "old"
    return choice + row


def empty() -> None:
    """Row ``legacy-empty-function``: a function with nothing but its docstring."""


TABLE = {"legacy-tabled": "a table of rows names no line"}
ATTRIBUTE_READ = "legacy-attribute-constant"


def collect(flag: bool) -> frozenset[str]:
    """Row ``legacy-collected-and-tested``: a line tests it, so neither this docstring nor its collection reads it."""
    rows = {"legacy-collected"}
    rows |= {"legacy-collected-and-tested"}
    rows.add("legacy-added")
    return frozenset(rows) | frozenset(TABLE) if flag else frozenset()


def branch(row: str, rows: frozenset[str]) -> str:
    if "legacy-collected-and-tested" in rows:
        return "old"
    if row in ("legacy-in-tuple", "new"):
        return row
    return hand(row="legacy-handed") + str(TABLE)


def hand(row: Literal["new", "legacy-annotated"] = "new") -> str:
    return row
