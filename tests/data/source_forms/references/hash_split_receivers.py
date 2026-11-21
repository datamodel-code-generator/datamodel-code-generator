"""#7026 hash-split: the unbound method is the builtin ``str`` by origin, not by its spelling.

A parameter, a local binding or an import named ``str`` is another object; the builtin under another name is not.
keywords: sep
"""
from builtins import str as text


def builtin(ref):
    return str.split(ref, "#"), text.rpartition(ref, "#"), text.split(ref, sep="#"), ref.split("#")


def parameter(str, ref):
    return str.split(ref, "#")


def local(ref):
    str = type(ref)
    return str.partition(ref, "#")


def others(ref, other):
    return other.split(ref, "#"), str.split(ref, "/"), ref.index("/")
