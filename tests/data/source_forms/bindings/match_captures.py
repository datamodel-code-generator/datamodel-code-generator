"""A1 M1: capture patterns, star captures, mapping rest, class keyword captures and alternatives bind names."""

import enum


class Point:
    __match_args__ = ("x", "y")


class Color(enum.Enum):
    RED = 1


def f(subject):
    match subject:
        case [first, *others]:
            return first, others
        case {"key": found, **remaining}:
            return found, remaining
        case Point(x=0, y=height) | Point(x=height):
            return height
        case Color.RED as red:
            return red
        case str() | bytes() as text if text:
            return text
        case _:
            return None
