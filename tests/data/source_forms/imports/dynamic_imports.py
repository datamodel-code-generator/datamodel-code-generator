"""A3: import_module and __import__ with a literal (directly, through an alias, relative) are imports; a variable is
dynamic.
"""

import importlib
from importlib import import_module as load

json = importlib.import_module("json")
parser = load("pkg.parser")
sibling = importlib.import_module(".sibling", package="pkg")
own = importlib.import_module(".child", __name__)
parent = importlib.import_module("..up", "pkg.sub")
escape = importlib.import_module("..far", "pkg")
unknown = importlib.import_module(".x", package=None)
xml = __import__("xml.dom")


def by_name(name):
    return importlib.import_module(name), __import__(name)
