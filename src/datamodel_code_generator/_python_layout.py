"""Lay out generated Python expressions, breaking a bracketed group one item per line when it does not fit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class Group:
    """A bracketed sequence: its opening text, prefixed items, closing text, and a suffix for a lone item."""

    head: str
    items: tuple[tuple[str, Doc], ...]
    tail: str
    lone: str = ""


@dataclass(frozen=True, slots=True)
class Chain:
    """Operands joined by a binary operator, such as the members of a union."""

    operator: str
    operands: tuple[Doc, ...]


Doc: TypeAlias = str | Group | Chain


def layout(doc: Doc, indent: int, used: int, width: int) -> str:
    """Return a document on one line when it fits, otherwise broken one item or operand per line.

    A group puts a trailing comma after each item; a chain is parenthesized with each later operand led by its
    operator.
    """
    return _Layout(width).doc(doc, indent, used)


class _Layout:
    """Lay out one document, flattening each node once."""

    __slots__ = ("texts", "width")

    def __init__(self, width: int) -> None:
        self.width = width
        self.texts: dict[int, str] = {}

    def flat(self, doc: Doc) -> str:
        if isinstance(doc, str):
            return doc
        if (text := self.texts.get(id(doc))) is None:
            text = self.texts[id(doc)] = self.chain(doc) if isinstance(doc, Chain) else self.group(doc)
        return text

    def chain(self, doc: Chain) -> str:
        return f" {doc.operator} ".join(self.flat(operand) for operand in doc.operands)

    def group(self, doc: Group) -> str:
        inner = ", ".join(prefix + self.flat(item) for prefix, item in doc.items)
        return f"{doc.head}{inner}{doc.lone if len(doc.items) == 1 else ''}{doc.tail}"

    def doc(self, doc: Doc, indent: int, used: int) -> str:
        text = self.flat(doc)
        if isinstance(doc, str) or indent + used + len(text) < self.width:
            return text
        inner = " " * (indent + 4)
        if isinstance(doc, Chain):
            operator = f"{doc.operator} "
            first, *rest = doc.operands
            lines = f"{inner}{self.doc(first, indent + 4, 0)}\n" + "".join(
                f"{inner}{operator}{self.doc(operand, indent + 4, len(operator))}\n" for operand in rest
            )
            return f"(\n{lines}{' ' * indent})"
        lines = "".join(f"{inner}{prefix}{self.doc(item, indent + 4, len(prefix))},\n" for prefix, item in doc.items)
        return f"{doc.head}\n{lines}{' ' * indent}{doc.tail}"
