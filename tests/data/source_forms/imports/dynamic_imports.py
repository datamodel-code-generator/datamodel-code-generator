"""A3: import_module and __import__ with a literal (directly, through an alias, relative) are imports; a variable is
dynamic. __import__ counts its level argument from the package of its globals (globals(): this module's).
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
leaf = __import__("leaf", globals(), level=1)
upper = __import__("up", globals(), None, (), 2)
absolute = __import__("email", globals(), level=0)
nowhere = __import__("leaf", level=1)
dotted = __import__(".leaf", globals())


def by_name(name):
    return importlib.import_module(name), __import__(name)
