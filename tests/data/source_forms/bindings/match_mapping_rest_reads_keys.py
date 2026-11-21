"""Review R05, must M2: a mapping pattern with **rest still reads its keys (Color.RED on both cases)."""

import enum


class Color(enum.Enum):
    RED = 1


def f(p):
    match p:
        case [first, *rest]:
            return first, rest
        case {Color.RED: value, **others}:
            return value, others
        case {Color.RED: _}:
            return None
        case str() as whole:
            return whole
