"""Package imports: a dotted import initialises its parent; an import of a module's own package is no edge.

tool and sub are a cycle for every key: importing sub.leaf runs sub/__init__.py, which imports tool.
child reads a name from this package, which imports it: Python initialises a package before its
children, and the model's graph keeps no edge from a module to its own package, so it is no cycle.
Code scanning also sees no cycle there, nor in model/__init__.py, which this rule puts on a cycle.
"""

from datamodel_code_generator.child import CHILD

NAME = "package"
PAIR = CHILD
