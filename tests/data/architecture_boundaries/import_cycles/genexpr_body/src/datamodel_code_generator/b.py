"""Import a back in the body of a generator expression, which runs only when it is iterated."""

from importlib import import_module

VALUE = 1
BACK = (import_module("datamodel_code_generator.a") for _ in range(1))
