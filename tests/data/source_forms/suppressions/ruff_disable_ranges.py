"""#7026: a disable range ends at an enable with the identical list in the same order, or at its block's end.

codes: F401 unused-import
"""

# ruff: disable[F401,E501]
from os import path as _path
# ruff: enable[F401]
from os import sep as _sep
# ruff: enable[F401,E501]
from os import name as _name


def block():
    # ruff: disable[F401]
    import json
    import re
    return json, re


import ast
x = 1  # ruff: disable[F401]
import abc
# ruff: disable[F401]
import zlib
