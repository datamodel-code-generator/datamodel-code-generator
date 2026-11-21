"""Review R09: a property setter rebinds the name; a decorated function and its rebinding are two bindings."""

import functools


class C:
    @property
    def x(self):
        return 1

    @x.setter
    def x(self, value):
        pass


@functools.cache
def g():
    pass


g = functools.wraps(g)(g)
