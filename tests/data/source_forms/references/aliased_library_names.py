"""A3: library names read through an alias import, a module alias and an assignment alias; an alias cycle names none."""

import typing
import typing as tp
from importlib import import_module as load
from typing import cast as c

no_check = typing.no_type_check
TC = tp.TYPE_CHECKING
first = second
second = first


@no_check
def f(x):
    return c(int, x), load("json"), tp.Any, first
