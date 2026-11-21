"""C3 region from @, co_firstlineno: a definition starts at its first decorator, class and async included."""

import functools


@functools.cache
@staticmethod
def cached():
    return 1


@functools.total_ordering
class Ordered:
    @property
    def value(self):
        return 1


@functools.wraps(cached)
async def waits():
    return 2
