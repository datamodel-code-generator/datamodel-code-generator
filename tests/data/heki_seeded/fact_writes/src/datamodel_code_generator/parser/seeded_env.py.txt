"""The forms of reading the environment, which the environment boundary forbids: 14 sites of ``<module>``.

Guarded imports (an import, a ``from`` import of two names, which are two probes, an ``import_module`` call under
``ModuleNotFoundError``, an import under a tuple of handlers, one more import whose fallback import in the handler is
none), ``os.environ``, ``os.getenv``, ``importlib.util.find_spec``, ``sys.platform``, ``os.name``, ``sys.hexversion``,
``platform.system()``, and ``sys.platform`` in a class body; ``old_python`` reads ``sys.version_info``. The lazy
import of ``lazy_import`` under ``except Exception`` reads nothing.
"""

import os
import platform
import sys
from importlib import import_module, util

try:
    import tomllib
except ImportError:
    tomllib = None
try:
    from yaml import CDumper, CSafeLoader
except ImportError:
    CDumper = CSafeLoader = None
try:
    import_module("orjson")
except ModuleNotFoundError:
    pass
try:
    import ujson
except (ImportError, AttributeError):
    ujson = None
try:
    import tomli
except ImportError:
    import tomllib as tomli

HOME = os.environ.get("HOME")
EDITOR = os.getenv("EDITOR")
FOUND = util.find_spec("yaml")
WINDOWS = sys.platform == "win32" or os.name == "nt"
HEX = sys.hexversion
SYSTEM = platform.system()


def old_python() -> bool:
    return sys.version_info < (3, 12)


class Platform:
    DARWIN = sys.platform == "darwin"


def lazy_import() -> None:
    try:
        import csv
    except Exception:
        return
    csv.reader([])
