"""An assignment re-export of another module's constant, a class, a conditional binding and an outside name."""

import os
import sys

from . import _format_types

DEFAULT_LINE_LENGTH = _format_types.DEFAULT_LINE_LENGTH
LiteralType = _format_types.LiteralType
SEP = os.sep
if sys.version_info >= (3, 12):
    from ._format_types import LiteralType as Versioned
else:
    Versioned = None
Choice = Versioned
