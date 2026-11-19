"""#7129 DEFAULT_LINE_LENGTH, #7123 LiteralType.One: names followed through re-exports, aliases and submodules.

questions: imports
"""

from . import sub
from .format import DEFAULT_LINE_LENGTH as DEFAULT_LINE_LENGTH
from .format import LiteralType
from .loop import first

One = LiteralType.One
