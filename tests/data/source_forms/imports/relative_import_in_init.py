"""Review R29, N5: in a package __init__, one dot is the package itself; two dots leave a top-level package.

module: pkg
path: pkg/__init__.py
"""

from . import sibling as s
from .. import parent
from .sub import thing
