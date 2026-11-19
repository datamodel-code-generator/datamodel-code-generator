"""#7026 unused-private-global: what reads a module-level private name, and what only looks like a read.

A read in a function, a class body, a comprehension's condition or a type position counts; a parameter, a local or a
comprehension variable of the same name is another name, and so are an augmented assignment and an entry of ``__all__``.
"""
_UNUSED = 1
_READ = 2
_SHADOWED = 3
_COMPREHENSION = 4
_LISTED = 5
_COUNTER = 0
_FIRST, _SECOND = 6, 7
_ANNOTATED: int = 8
_TYPE: type = int
_CLASS_BODY = 9
_LATE = 10

__all__ = ["_LISTED"]
__all__ += ["_UNUSED"]


def reader():
    return _READ, _FIRST


def shadow(_SHADOWED):
    return _SHADOWED


def comprehension():
    return [_COMPREHENSION for _COMPREHENSION in range(3)], [x for x in range(3) if x > _ANNOTATED]


def bump():
    global _COUNTER
    _COUNTER += 1


def annotated(value: _TYPE) -> None:
    return None


class Holder:
    _CLASS_BODY = _CLASS_BODY


def late():
    return _LATE
