"""Relative imports resolve against the module's package, by level.

module: pkg.sub.mod
path: pkg/sub/mod.py
"""

from . import sibling
from .. import parent_level
from ..other import name
from ...top import beyond
from .sibling import thing as _thing
