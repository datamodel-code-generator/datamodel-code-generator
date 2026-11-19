"""#3866 architecture boundaries: an alias of a name bound twice may name the path of each of its bindings.

``loader`` is bound to two paths and has no single origin; ``load`` follows it to both, and ``registry`` names
``sys.modules`` beside a value with no path.
"""
import importlib
import sys

if sys.platform == "win32":
    loader = importlib.import_module
else:
    loader = sys.exit

load = loader
registry = sys.modules
registry = {}
read = registry
