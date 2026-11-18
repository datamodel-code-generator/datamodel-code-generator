"""Sweep find (__context): a private name in a class body and its methods is stored mangled; dunders are not."""

__context = "module"


class Parser:
    __slots__ = ("__context",)
    __limit = 3

    def __init__(self, __context):
        self.__context = __context
        self.__dict__["plain"] = 1

    def __read(self):
        return self.__context, Parser.__limit

    class __Inner:
        __depth = 1


class ___:
    __kept = 1
