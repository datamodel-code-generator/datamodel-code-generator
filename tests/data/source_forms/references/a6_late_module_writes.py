"""A6 identity writes, read through the model: __module__ stores on classes, directly and through a loop over them.

questions: bindings
"""

from ._home import Exported, Other

Exported.__module__ = __name__
for _cls in (Exported, Other):
    _cls.__module__ = __name__
del _cls
