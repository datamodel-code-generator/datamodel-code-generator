"""import a.b.c initialises every parent; from pkg import submodule loads the submodule.

questions: references
"""

from . import leaf
from .inner import deep
