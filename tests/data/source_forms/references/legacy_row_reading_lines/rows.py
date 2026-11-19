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
