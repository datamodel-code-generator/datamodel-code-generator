"""The function-level imports of a module: which defer something and which load what the module already loads.

An import under a module-level ``if`` loads nothing for certain; one in a ``try`` loads unless the import fails.
"""
import sys
from typing import TYPE_CHECKING

from . import cycle, leaf
from .leaf import VALUE

if TYPE_CHECKING:
    from . import typing_only
if sys.version_info >= (3, 12):
    from . import newer
elif sys.platform == "win32":
    from . import windows
try:
    from . import optional
except ImportError:
    optional = None


def redundant():
    from . import leaf
    from .leaf import VALUE as value

    return leaf, value


def several():
    from . import cycle, later

    return cycle, later


def closes_the_cycle():
    from . import cycle

    return cycle


def under_guards():
    from . import typing_only, newer, windows

    return typing_only, newer, windows


def after_a_try():
    from . import optional

    return optional


def deferred():
    from . import later

    return later


class Holder:
    def method(self):
        from . import leaf

        return leaf


def nested():
    def inner():
        import datamodel_code_generator.redundant_local_imports.leaf

    class Local:
        import datamodel_code_generator.redundant_local_imports.nested as module

    return inner, Local, VALUE
