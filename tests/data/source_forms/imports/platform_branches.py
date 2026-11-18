"""A6 M13: imports and bindings under platform tests, by sys.platform, os.name and platform.system()."""

import os
import platform
import sys

if sys.platform == "win32":
    WATCH_BACKEND = "polling"
    import winreg
else:
    WATCH_BACKEND = "native"
if os.name == "nt":
    import msvcrt
if platform.system() == "Darwin":
    import fcntl
