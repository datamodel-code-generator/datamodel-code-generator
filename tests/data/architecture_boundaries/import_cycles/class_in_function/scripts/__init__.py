"""A module-level import closed by an import back in a class defined in a function: lazy keys only.

The class body runs when the function runs, after both modules are imported, so no import fails.
"""
