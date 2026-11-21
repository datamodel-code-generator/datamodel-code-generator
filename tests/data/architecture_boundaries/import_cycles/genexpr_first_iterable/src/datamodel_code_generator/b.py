"""Import a back in the first iterable of a generator expression, which is evaluated where it stands."""

from importlib import import_module

VALUE = 1
BACK = (module for module in [import_module("datamodel_code_generator.a")])
