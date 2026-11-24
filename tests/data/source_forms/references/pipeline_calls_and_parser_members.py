"""Deep warnings, and the origins the model reads in a ``Parser`` body, a decorator, a default and ``getattr``.

A call in a decorator or a default runs in the scope around the definition. A place reaches a ``Parser`` member by
attribute or through ``getattr`` and its aliases, by origin (a parameter named ``getattr`` is not the builtin). A
warning is deep when its ``stacklevel`` is a call or above 2; the pass pipeline check reads only that.
"""

from warnings import warn


def decorate(value):
    return lambda function: function


class Parser:
    __runner = None

    def __enter(self):
        return FinalFacts()

    async def __leave(self):
        pass

    for __index in ():
        pass


class Store:
    @decorate(FinalFacts())
    def seal(self, facts=FinalFacts()):
        return FinalFacts()


def outside(parser, getattr):
    lookup = __builtins__.getattr
    fetch = globals()["getattr"]
    return parser._Parser__runner, parser.__enter, getattr(parser, "__leave"), lookup, fetch


def reaches_by_name(parser):
    read = getattr
    return read(parser, "__index"), hasattr(parser, "__runner")


def warns(level):
    warn("shallow", stacklevel=2)
    warn("deep", stacklevel=3)
    warn("computed", stacklevel=level + 1)

    def nested():
        warn("inner", stacklevel=4)

    return nested


async def warns_later():
    warn("deep", stacklevel=5)
