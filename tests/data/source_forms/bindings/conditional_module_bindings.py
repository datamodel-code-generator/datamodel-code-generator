"""#7129 libyaml, A6 table: bindings in try/except, if/else and a branch that raises each carry their guard.

questions: imports
"""

import sys

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

if sys.version_info >= (3, 12):
    LIMIT = 12
else:
    LIMIT = 11

try:
    import graphql
except ImportError:
    raise
