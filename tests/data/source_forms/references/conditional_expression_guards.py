"""Guards of conditional expressions and of ``and``/``or``: a platform test decides what its branch runs.

The branches of ``body if test else orelse`` run under the test like the branches of an ``if``; in ``a and b`` the
operand ``b`` runs under ``a`` and in ``a or b`` under the not-``a``, each operand after the first under the ones before
it. A platform test makes ``posix-only-call`` stop at the import it guards; another test decides nothing.

questions: imports
"""

import importlib
import sys
from typing import TYPE_CHECKING

CONFIG = object()

resource = None if sys.platform == "win32" else importlib.import_module("resource")
fcntl = sys.platform != "win32" and importlib.import_module("fcntl")
posix = sys.platform == "win32" or importlib.import_module("fcntl")
late = importlib.import_module("fcntl") if CONFIG else None
newer = importlib.import_module("resource") if sys.version_info >= (3, 12) else None
typed = int if TYPE_CHECKING else str
chained = CONFIG and sys.platform != "win32" and importlib.import_module("resource")
