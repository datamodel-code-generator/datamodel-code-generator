"""A6: X.__module__ = ... and for v in (A, B): v.__module__ = __name__ are attribute stores through the binding."""


class A: ...


class B: ...


A.__module__ = "pkg"
for value in (A, B):
    value.__module__ = __name__
B.PARSER_CONSTRUCTOR = staticmethod(print)
