"""#7025 architecture boundaries: the bases a class lists and the annotation of a parameter, read by origin.

bases: yes
A subscript base gives its value, a keyword is no base, and any other expression stays as written.
"""
import typing as t
from typing import Generic, Protocol, TypeVar

from . import models

T = TypeVar("T")


class A(Protocol[T], metaclass=type):
    pass


class B(t.Protocol, Generic[T], models.Base, models.Sub[int]):
    pass


class C(make_base()):
    def method(self, other: "C", *rest: models.Host, key: Host | None = None, **extra: type[Host]) -> None:
        pass


def plain(a, b: int = 0):
    return lambda x: x
