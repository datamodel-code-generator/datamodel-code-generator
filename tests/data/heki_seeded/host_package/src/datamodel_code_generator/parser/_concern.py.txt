"""A concern module (``parser/_*``) reaching the private members of ``Parser``, which ``parser/base.py`` defines.

A method of a subclass on its own receiver and a ``*Host`` Protocol that declares the member reach them; the class
itself, a ``__dict__`` key or ``getattr`` name, a Protocol of the module that is no ``*Host``, a subprotocol of a
Host and an untyped receiver do not. Dunders are none of the targets."""

from typing import Protocol

from datamodel_code_generator.parser.base import Parser


class CountHost(Protocol):
    _count: int

    def _reset(self) -> None: ...


class Counter(Protocol):
    _count: int


class NarrowCount(CountHost, Protocol):
    pass


class Child(Parser):
    def bump(self) -> None:
        self._count += 1
        self._reset()


def through_the_host(parser: CountHost) -> int:
    parser._reset()
    parser._count = 1
    return parser._count


def through_the_class(parser: Parser) -> int:
    parser._reset()
    parser._count = 1
    return parser._count


def through_a_key(parser: Parser) -> object:
    parser.__dict__["_count"] = 2
    return getattr(parser, "_count")


def through_other_protocols(counter: Counter, narrow: NarrowCount) -> int:
    return counter._count + narrow._count


def untyped(parser) -> int:
    return parser._count + len(parser.__class__.__name__)
