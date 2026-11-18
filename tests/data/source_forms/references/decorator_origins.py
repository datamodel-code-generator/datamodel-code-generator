"""#7129: decorators that change how callers use a function, qualified, bare and through an alias."""

import contextlib
from contextlib import asynccontextmanager
from functools import lru_cache as cached

manager = contextlib.contextmanager


@contextlib.contextmanager
def recorded():
    yield


@asynccontextmanager
async def arecorded():
    yield


@manager
def aliased():
    yield


@cached(maxsize=1)
def cached_value():
    return 1
