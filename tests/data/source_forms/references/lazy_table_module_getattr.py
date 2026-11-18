"""A6 lazy tables, #7123 avro COMPLEX_TYPES: a module __getattr__ serves a literal table, read with constant().

constants: _LAZY_IMPORTS COMPLEX_TYPES _COMPUTED
"""

from importlib import import_module

_LAZY_IMPORTS = {"Generator": "pkg.generator", "Parser": ("pkg.parser", "Parser")}
COMPLEX_TYPES = frozenset({"record", "enum", "array"})
_COMPUTED = {name.upper(): name for name in ("a", "b")}


def __getattr__(name):
    entry = _LAZY_IMPORTS[name]
    module, attribute = entry if isinstance(entry, tuple) else (entry, name)
    return getattr(import_module(module), attribute)


def __dir__():
    return sorted({*globals(), *_LAZY_IMPORTS})
