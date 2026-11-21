"""#7026 globals-registry, hash-split: a star import may bind ``globals`` or ``str``, and the rules read them as builtins.

The model leaves such a name open (``star``) and gives it no origin; a rule that looks for a builtin counts it, as it
counts a gate that fails closed. An explicit binding of the name still shadows it.
"""
from os.path import *

for key in list(globals()):
    pass
TABLE = [value for value in globals().values()]


def cut(ref):
    return str.split(ref, "#")


def shadowed(str, ref):
    return str.split(ref, "#")
