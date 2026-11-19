"""SD (step 1, globals-registry): how the value of any expression is used, the value of a call included.

The iterable of a ``for`` or a comprehension, ``*`` and ``**``, an argument by position or keyword, the receiver of a
method call, a subscript read and a subscript store.
consumers: calls
"""

REGISTRY = {}
for _name in globals():
    REGISTRY[_name] = 1
KEYS = [key for key in globals().keys()]
COPY = dict(globals())
SPREAD = [*globals()]
MERGED = {**globals()}
OPTIONS = dict(**vars())
SORTED = sorted(REGISTRY, key=str.lower)
LOOKUP = globals()["REGISTRY"]
globals()["LATE"] = 1
TOTAL = sum(1 for _ in globals())
FOUND = globals().get("COPY")


async def stream(source):
    async for item in source():
        yield item


class Built(dict, metaclass=type):
    entries = list(map(str, range(3)))
