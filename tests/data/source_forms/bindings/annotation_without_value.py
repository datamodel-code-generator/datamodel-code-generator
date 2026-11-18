"""An annotation without a value makes the name local without assigning it; a parenthesized one binds nothing.

``(x): int`` without a value is not a simple target, so the compiler does not bind ``x`` (with a value it does).
"""

count: int


def f():
    total: int
    return count


def g():
    (count): int
    (other): int = 1
    return count, other


class Fields:
    name: str
    size: int = 0
