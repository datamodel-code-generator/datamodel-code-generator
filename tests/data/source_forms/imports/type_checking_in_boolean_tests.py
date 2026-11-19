"""Review R15, must M1: TYPE_CHECKING inside and/or/not gives the guard of the branch that runs only for type checkers."""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a import X
elif sys.version_info >= (3, 12):
    from b import X
else:
    from c import X
if not TYPE_CHECKING and sys.version_info >= (3, 12):
    from d import Y
if TYPE_CHECKING or sys.version_info >= (3, 13):
    from e import Z
if TYPE_CHECKING and sys.version_info >= (3, 13):
    from f import W
else:
    from g import W
if not (TYPE_CHECKING and sys.platform == "win32"):
    from h import V
if sys.version_info >= (3, 12) and sys.version_info < (3, 14):
    from i import U
if sys.platform == "win32" or sys.platform == "cygwin":
    from j import S
