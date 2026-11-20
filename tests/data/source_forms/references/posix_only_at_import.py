"""G13 posix-only-call: a name or module that exists on POSIX only breaks the import where it runs at import time.

The module (``fcntl``, ``resource``) or the name (``os.fork``, ``os.mkfifo``, ``signal.SIGKILL``) counts by the origin the
model gives it, through an alias or a ``from`` import. A use at module or class level is a site, in a script and in a
test; one inside a function is not (the function is POSIX-only, which the tests that reach it must say). A platform test
or an ``ImportError`` handler decides whether the import runs, a parameter named ``os`` is not the module, a string
is no use. A literal ``import_module`` of a module is its import, a ``from`` import of a name its use.
"""

import fcntl
import importlib
import os as operating_system
import sys
from os import mkfifo as make_pipe
from signal import SIGKILL as KILL

if sys.platform != "win32":
    import resource

try:
    import resource as limits
except ImportError:
    limits = None

PIPE = operating_system.mkfifo
OTHER = make_pipe
TEXT = "os.fork"
LATE = importlib.import_module("resource")


class Holder:
    signal = KILL

    def method(self):
        return KILL


def lazy():
    import fcntl as late

    return late


def shadowed(os):
    return os.fork()
