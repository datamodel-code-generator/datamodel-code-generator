"""#7026 globals-registry: ``globals`` by origin, and how its value is used, wherever it stands.

Enumerated in a loop, a comprehension, a spread or an argument, or asked for its values, items, keys or copy; looked
up by key, written to, or enumerated inside a module ``__getattr__`` or ``__dir__`` is no registry.
"""
import builtins
from builtins import globals as namespace

for key in globals ():
    pass
ITEMS = [*builtins.globals().values()]
SPREAD = {**namespace()}
KEYS = sorted(namespace().keys())
LOOKUP = globals()["LOOKUP"]
globals()["LATE"] = 1
COUNT = len(globals())


def __getattr__(name):
    return [item for item in globals().items() if item[0] == name]


def __dir__():
    return list(globals())


def shadowed(globals):
    return list(globals())


def method_in_hook():
    def helper():
        return dict(globals())

    return helper
