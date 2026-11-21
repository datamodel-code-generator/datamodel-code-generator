"""#7026: a disable range ends at an enable with the identical list in the same order, or at its block's end.

Inside a multi-line string, a line neither ends the range nor holds an enable comment.

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
    text = """
# ruff: enable[F401]
"""
    import re
    return json, re, text


import ast
x = 1  # ruff: disable[F401]
import abc
# ruff: disable[F401]
import zlib
