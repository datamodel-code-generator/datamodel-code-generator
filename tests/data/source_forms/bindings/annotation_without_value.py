"""An annotation without a value makes the name local without assigning it."""

count: int


def f():
    total: int
    return count


class Fields:
    name: str
    size: int = 0
