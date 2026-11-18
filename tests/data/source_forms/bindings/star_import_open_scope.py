"""A star import leaves the module's names open: an unbound read resolves as star, not as a guess (review R12)."""

from os.path import *


def f():
    return join("a"), missing
