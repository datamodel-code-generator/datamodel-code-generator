"""#7026: a parameter of the same name reads the parameter, not the module binding."""

_CACHE = 1
_USED = 2


def f(_CACHE):
    return _CACHE


def g():
    return _USED
