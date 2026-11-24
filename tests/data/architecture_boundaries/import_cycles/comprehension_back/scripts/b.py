"""Import a back in a comprehension that runs when this module is imported."""

from importlib import import_module

VALUE = 1
BACK = [import_module("scripts.a") for _ in range(1)]
LATER = lambda: import_module("scripts.c")  # noqa: E731
