"""A6 fail-closed shapes: names a namespace call, exec, setattr on a module or a variable import may bind."""

import importlib
import sys

globals()["Late"] = 1
vars()["Other"] = 2
setattr(sys.modules[__name__], "Third", 3)
exec("Fourth = 4")


def f(name):
    return importlib.import_module(name), locals(), Late
