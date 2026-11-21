"""Builtins of one platform or of newer versions, read under the guards that need them, resolve as builtins."""

import sys

if sys.platform == "win32":
    NATIVE_ERRORS = (WindowsError, OSError)
else:
    NATIVE_ERRORS = (OSError,)

if sys.version_info >= (3, 13):
    FINALIZING = (PythonFinalizationError, _IncompleteInputError)

if sys.version_info >= (3, 15):
    EMPTY = frozendict()
    MISSING = sentinel("MISSING")
    CYCLE = ImportCycleError
    LAZY = __lazy_import__
